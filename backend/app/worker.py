import asyncio
import logging
import httpx
import aiosqlite
import sqlite3
from datetime import datetime, timezone, timedelta

from kubernetes.client.rest import ApiException

from .config import (
    DB_PATH,
    OPEN_GROK_REINDEX_URL,
    AUTO_SYNC_INTERVAL_SEC,
    POD_NAMESPACE,
    WATCH_INTERVAL_SEC,
)
from . import k8s
from .database import custom_connection_factory
from .k8s import batch_v1_api

logger = logging.getLogger(f"uvicorn.{__name__}")
STOP_WATCHER = asyncio.Event()

async def trigger_opengrok_reindex(job_name: str):
    """
    Sends a GET request to the OpenGrok reindex endpoint.
    """
    logger.info(f"Triggering OpenGrok reindex for Job: {job_name}. URL: {OPEN_GROK_REINDEX_URL}")
    
    # Set a timeout so that a failed request does not block the worker.
    timeout = httpx.Timeout(10.0, connect=5.0)
    
    try:
        # Send an asynchronous GET request using httpx
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(OPEN_GROK_REINDEX_URL)
            response.raise_for_status() # Detect HTTP error codes

        logger.info(f"OpenGrok reindex successfully triggered. Response status: {response.status_code}")

    except httpx.HTTPStatusError as e:
        logger.error(f"Failed to trigger OpenGrok reindex due to HTTP error: {e}")
    except httpx.RequestError as e:
        logger.error(f"Failed to trigger OpenGrok reindex due to connection error: {e}")
    except Exception as e:
        logger.error(f"An unexpected error occurred while triggering reindex: {e}")

async def job_watcher_worker():
    """
    Background task to monitor the status of K8s Jobs.
    Manages DB connection on each loop iteration.
    """
    logger.info("Starting Kubernetes Job Watcher.")
    
    while not STOP_WATCHER.is_set():
        await asyncio.sleep(WATCH_INTERVAL_SEC)
        
        db = None 
        try:
            # Create a dedicated connection for the worker each time
            db = await aiosqlite.connect(DB_PATH, factory=custom_connection_factory)
            
            async with db.execute("SELECT id, job_name, status, pvc_path FROM repositories WHERE status IN ('PENDING', 'DELETING')") as cursor:
                active_repos = await cursor.fetchall()

            if not active_repos:
                logger.debug("No active jobs (PENDING or DELETING) found. Continuing watch.")
                continue

            for repo in active_repos:
                if repo['status'] == 'PENDING':
                    await check_cloning_job_status(db, repo)
                elif repo['status'] == 'DELETING':
                    await check_cleanup_job_status(db, repo)

        except Exception as e:
            logger.error(f"Error during database operation in watcher: {e}")

        finally:
            # Always close the connection used in this loop at the end of the iteration
            if db:
                await db.close()
                
    logger.info("Kubernetes Job Watcher stopped.")


async def check_cloning_job_status(db: aiosqlite.Connection, repo: aiosqlite.Row):
    """Checks the status of a cloning job and updates the DB."""
    job_name = repo['job_name']
    new_status = None
    try:
        k8s_job = batch_v1_api.read_namespaced_job_status(name=job_name, namespace=POD_NAMESPACE)
        
        if k8s_job.status.succeeded is not None and k8s_job.status.succeeded >= 1:
            new_status = 'COMPLETED'
            logger.info(f"Job COMPLETED: {job_name}")
            await trigger_opengrok_reindex(job_name)
            
        elif k8s_job.status.failed is not None and k8s_job.status.failed >= 1:
            new_status = 'FAILED'
            logger.warning(f"Job FAILED: {job_name}")
        
        if new_status:
            await db.execute(
                "UPDATE repositories SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE job_name = ?",
                (new_status, job_name)
            )
            await db.commit()

    except ApiException as e:
        if e.status == 404:
            logger.warning(f"Job {job_name} not found in K8s (404). Setting DB status to UNKNOWN_CLEANUP.")
            await db.execute(
                "UPDATE repositories SET status = 'UNKNOWN_CLEANUP', updated_at = CURRENT_TIMESTAMP WHERE job_name = ?",
                (job_name,)
            )
            await db.commit()
        else:
            logger.error(f"Error reading K8s Job {job_name}: {e}")
    except Exception as e:
        logger.error(f"An unexpected error occurred during job check for {job_name}: {e}")


async def check_cleanup_job_status(db: aiosqlite.Connection, repo: aiosqlite.Row):
    """Checks the status of a cleanup job and updates the DB."""
    repo_id = repo['id']
    pvc_path = repo['pvc_path']
    
    try:
        # Find the cleanup job by label selector
        label_selector = f"app=crpaas-git-cleaner,pvc-path={pvc_path}"
        job_list = batch_v1_api.list_namespaced_job(namespace=POD_NAMESPACE, label_selector=label_selector)

        if not job_list.items:
            logger.warning(f"No cleanup job found for pvc_path: {pvc_path}. Waiting for creation.")
            return

        # Get the most recent job if multiple exist
        cleanup_job = sorted(job_list.items, key=lambda j: j.metadata.creation_timestamp, reverse=True)[0]
        cleanup_job_name = cleanup_job.metadata.name

        if cleanup_job.status.succeeded is not None and cleanup_job.status.succeeded >= 1:
            logger.info(f"Cleanup job {cleanup_job_name} SUCCEEDED for repo ID {repo_id}.")
            # On success, delete the repository record from the database
            await db.execute("DELETE FROM repositories WHERE id = ?", (repo_id,))
            await db.commit()
            logger.info(f"Repository record for ID {repo_id} deleted.")

        elif cleanup_job.status.failed is not None and cleanup_job.status.failed >= 1:
            logger.error(f"Cleanup job {cleanup_job_name} FAILED for repo ID {repo_id}.")
            # On failure, update the status to DELETION_FAILED
            await db.execute(
                "UPDATE repositories SET status = 'DELETION_FAILED', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (repo_id,)
            )
            await db.commit()

    except ApiException as e:
        logger.error(f"API error checking cleanup job for repo ID {repo_id}: {e}")
    except Exception as e:
        logger.error(f"Unexpected error in check_cleanup_job_status for repo ID {repo_id}: {e}")


async def auto_sync_worker():
    """
    Background task to check for and trigger scheduled repository syncs.
    """
    logger.info("Starting Auto-Sync Worker.")

    # Keep track of the last time the check was run
    last_check_time = datetime.now(timezone.utc)

    while not STOP_WATCHER.is_set():
        await asyncio.sleep(AUTO_SYNC_INTERVAL_SEC)

        db = None
        try:
            db = await aiosqlite.connect(DB_PATH, factory=custom_connection_factory)
            
            current_check_time = datetime.now(timezone.utc)

            # Find repositories that are enabled for auto-sync
            async with db.execute(
                "SELECT * FROM repositories WHERE auto_sync_enabled = TRUE AND auto_sync_schedule IS NOT NULL"
            ) as cursor:
                repos_to_sync = await cursor.fetchall()

            if not repos_to_sync:
                # Update last_check_time even if there are no repos to sync
                last_check_time = current_check_time
                continue

            for repo in repos_to_sync:
                # Check if the scheduled time falls between the last check and the current check
                schedule_time_str = repo['auto_sync_schedule'] # "HH:MM"
                schedule_hour, schedule_minute = map(int, schedule_time_str.split(':'))
                
                # Find the most recent past occurrence of the scheduled time
                potential_sync_time = current_check_time.replace(hour=schedule_hour, minute=schedule_minute, second=0, microsecond=0)
                if potential_sync_time > current_check_time:
                    # If the time is in the future for today, check yesterday's time
                    potential_sync_time -= timedelta(days=1)

                # If the scheduled time was between the last check and now, it's due for a sync.
                if not (last_check_time < potential_sync_time <= current_check_time):
                    continue

                # Check if the last sync was on a different day to prevent multiple syncs in one day
                last_synced_at = repo['last_synced_at']
                last_synced_date_str = ""
                if last_synced_at:
                    # The value might be a string or a datetime object depending on where it was written.
                    # Handle both cases to be safe.
                    if isinstance(last_synced_at, str):
                        last_synced_at = datetime.fromisoformat(last_synced_at)
                    last_synced_date_str = last_synced_at.strftime("%Y-%m-%d")

                if last_synced_date_str == potential_sync_time.strftime("%Y-%m-%d"):
                    logger.info(f"Repo ID {repo['id']} already synced today. Skipping.")
                    continue

                # Do not trigger a new sync if the previous one is still pending
                if repo['status'] == 'PENDING':
                    logger.warning(f"Skipping auto-sync for repo ID {repo['id']} as its status is PENDING.")
                    continue

                logger.info(f"Triggering auto-sync for repository ID: {repo['id']} ({repo['repo_url']})")

                try:
                    # Create a new, unique job name for the sync operation
                    new_job_name = k8s.create_job_name(repo['repo_url'], repo['commit_id'])

                    # Generate the K8s Job manifest
                    job_manifest = k8s.create_job_manifest(
                        new_job_name,
                        repo['repo_url'],
                        repo['commit_id'],
                        repo['pvc_path'],
                        repo['clone_single_branch'],
                        repo['clone_recursive']
                    )

                    # Create the new Job via the K8s API
                    k8s.batch_v1_api.create_namespaced_job(body=job_manifest, namespace=POD_NAMESPACE)
                    logger.info(f"Created auto-sync Job: {new_job_name} for repository ID: {repo['id']}")

                    # Update the database record
                    await db.execute(
                        "UPDATE repositories SET status = 'PENDING', job_name = ?, last_synced_at = ? WHERE id = ?",
                        (new_job_name, current_check_time, repo['id'])
                    )
                    await db.commit()

                except Exception as e:
                    logger.error(f"Failed to trigger auto-sync for repo ID {repo['id']}: {e}")

            # Update the last check time after processing all repos
            last_check_time = current_check_time

        except Exception as e:
            logger.error(f"Error during database operation in auto-sync worker: {e}")
        finally:
            if db:
                await db.close()

    logger.info("Auto-Sync Worker stopped.")
