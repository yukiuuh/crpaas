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
from . import k8s
from .database import custom_connection_factory

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

async def perform_clone_task(record_id: int, repo_url: str, pvc_path: str, commit_id: str, single_branch: bool, recursive: bool):
    """
    Background task to execute git clone/pull in OpenGrok pod and update DB status.
    """
    logger.info(f"Starting clone task for repo ID {record_id}")
    
    # 1. Update status to CLONING
    try:
        async with aiosqlite.connect(DB_PATH, factory=custom_connection_factory) as db:
            await db.execute(
                "UPDATE repositories SET status = 'CLONING', updated_at = ? WHERE id = ?",
                (datetime.now(timezone.utc), record_id)
            )
            await db.commit()
    except Exception as e:
        logger.error(f"Failed to update status to CLONING for repo {record_id}: {e}")

    # 2. Exec (blocking call, run in thread)
    success, output = await asyncio.to_thread(
        k8s.exec_clone_repository, 
        repo_url, pvc_path, commit_id, single_branch, recursive
    )
    
    # 3. Update DB with final status
    new_status = 'COMPLETED' if success else 'FAILED'
    current_timestamp = datetime.now(timezone.utc)
    
    try:
        async with aiosqlite.connect(DB_PATH, factory=custom_connection_factory) as db:
            await db.execute(
                "UPDATE repositories SET status = ?, updated_at = ?, task_log = ? WHERE id = ?",
                (new_status, current_timestamp, output, record_id)
            )
            await db.commit()
    except Exception as e:
        logger.error(f"Failed to update DB for repo {record_id}: {e}")
        return

    if success:
        logger.info(f"Clone success for {record_id}.")
        await trigger_opengrok_reindex(f"repo-{record_id}")
    else:
        logger.error(f"Clone failed for {record_id}. Output: {output}")

async def perform_cleanup_task(record_id: int):
    """
    Background task to execute cleanup and update status/delete record.
    """
    db = None
    try:
        db = await aiosqlite.connect(DB_PATH, factory=custom_connection_factory)
        
        async with db.execute("SELECT pvc_path FROM repositories WHERE id = ?", (record_id,)) as cursor:
            record = await cursor.fetchone()
        
        if not record:
            return

        pvc_path = record['pvc_path']
        
        logger.info(f"Starting cleanup for repo ID {record_id} ({pvc_path})")
        
        success = await asyncio.to_thread(k8s.exec_cleanup_repository, pvc_path)
        
        if success:
            await db.execute("DELETE FROM repositories WHERE id = ?", (record_id,))
            logger.info(f"Cleanup SUCCEEDED for repo ID {record_id}.")
            await trigger_opengrok_reindex(f"cleanup-repo-{record_id}")
        else:
            logger.error(f"Cleanup FAILED for repo ID {record_id}.")
            await db.execute(
                "UPDATE repositories SET status = 'DELETION_FAILED', updated_at = ?, task_log = ? WHERE id = ?",
                (datetime.now(timezone.utc), "Cleanup failed via exec.", record_id)
            )
        await db.commit()

    except Exception as e:
        logger.error(f"Error in cleanup task for {record_id}: {e}")
    finally:
        if db:
            await db.close()



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
                    # Trigger the clone task
                    asyncio.create_task(perform_clone_task(
                        repo['id'],
                        repo['repo_url'],
                        repo['pvc_path'],
                        repo['commit_id'],
                        repo['clone_single_branch'],
                        repo['clone_recursive']
                    ))

                    # Update the database record
                    await db.execute(
                        "UPDATE repositories SET status = 'PENDING', job_name = 'SYNC', last_synced_at = ? WHERE id = ?",
                        (current_check_time, repo['id'])
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
