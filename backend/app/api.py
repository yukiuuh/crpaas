import logging
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from kubernetes import client
from kubernetes.client.rest import ApiException

from . import k8s
from .config import POD_NAMESPACE, OPEN_GROK_BASE_URL, DB_PATH
from .database import get_db_session, custom_connection_factory
from .schemas import RepositoryInfo, RepositoryRequest, RepositoryExpirationUpdateRequest, JobLogs, AppConfig, RepositoryAutoSyncUpdateRequest

logger = logging.getLogger(f"uvicorn.{__name__}")
router = APIRouter()
scheduler = AsyncIOScheduler()


@router.get("/config", response_model=AppConfig)
async def get_app_config():
    """Returns application configuration to the frontend."""
    return AppConfig(opengrok_base_url=OPEN_GROK_BASE_URL)


@router.post("/repository", status_code=202, response_model=RepositoryInfo)
async def request_repository(
    req: RepositoryRequest, 
    db: aiosqlite.Connection = Depends(get_db_session) 
):
    """
    Receives a repository URL and commit ID, then creates a K8s Job.
    An optional `project_name` can be provided to customize the directory name.
    """
    try:
        # 0. Determine expiration date
        expired_at = None
        # If retention_days is a positive number, calculate the expiration date.
        if req.retention_days is not None and req.retention_days > 0:
            expired_at = datetime.now(timezone.utc) + timedelta(days=req.retention_days)
        elif req.retention_days == 0:  # 0 means indefinite retention
            expired_at = None

        # 1. Determine the PVC path (project name)
        if req.project_name:
            pvc_path = req.project_name
            # Check if a project with this custom name already exists
            async with db.execute("SELECT id FROM repositories WHERE pvc_path = ?", (pvc_path,)) as cursor:
                if await cursor.fetchone():
                    raise HTTPException(
                        status_code=409,
                        detail=f"A project with the name '{pvc_path}' already exists. Please choose a different name."
                    )
        else:
            # Generate the path if not provided
            repo_name_sanitized = k8s.sanitize_for_dns(req.repo_url.split('/')[-1])
            commit_hash_short = req.commit_id[:12]
            pvc_path = f"{repo_name_sanitized}-{commit_hash_short}"

        # Check for duplicate request (same repo and commit)
        async with db.execute("SELECT * FROM repositories WHERE repo_url = ? AND commit_id = ?", 
                              (req.repo_url, req.commit_id)) as cursor:
            existing = await cursor.fetchone()
            
        if existing:
            logger.info(f"Duplicate request: {req.repo_url} @ {req.commit_id}")
            return RepositoryInfo(**existing)

        # 2. Determine the Job name
        job_name = k8s.create_job_name(req.repo_url, req.commit_id)

        # 3. Generate the K8s Job manifest
        job_manifest = k8s.create_job_manifest(job_name, req.repo_url, req.commit_id, pvc_path, req.clone_single_branch, req.clone_recursive)
        
        try:
            # 4. Create the Job via the K8s API
            k8s.batch_v1_api.create_namespaced_job(
                body=job_manifest,
                namespace=POD_NAMESPACE
            )
            logger.info(f"Created Job: {job_name}")
        
        except ApiException as e:
            if e.status == 409: # If the Job already exists (idempotency)
                 logger.warning(f"Job {job_name} already exists. Assuming pending.")
            else:
                logger.error(f"Exception when creating K8s Job: {e}\n")
                raise HTTPException(status_code=500, detail=f"Failed to create Kubernetes Job: {e.body}")

        # 5. Save the request to the database
        async with db.execute(
            """
            INSERT INTO repositories (
                repo_url, commit_id, status, job_name, pvc_path, expired_at, 
                clone_single_branch, clone_recursive, last_synced_at,
                auto_sync_enabled, auto_sync_schedule
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                req.repo_url, req.commit_id, 'PENDING', job_name, pvc_path, 
                expired_at, req.clone_single_branch, req.clone_recursive, 
                datetime.now(timezone.utc),
                req.auto_sync_enabled,
                req.auto_sync_schedule if req.auto_sync_enabled else None
            )
        ) as cursor:
            await db.commit()
            record_id = cursor.lastrowid
        
        # Re-fetch the newly created record to get all fields, including timestamps
        async with db.execute("SELECT * FROM repositories WHERE id = ?", (record_id,)) as cursor:
            new_record = await cursor.fetchone()
            if not new_record:
                raise HTTPException(status_code=500, detail="Failed to retrieve newly created repository record.")
        return RepositoryInfo(**new_record)
    
    except sqlite3.IntegrityError as e:
        # Handle race conditions if two identical requests arrive at the same time
        logger.warning(f"Race condition likely avoided for: {req.repo_url} @ {req.commit_id}. Error: {e}")
        async with db.execute("SELECT * FROM repositories WHERE repo_url = ? AND commit_id = ?", 
                          (req.repo_url, req.commit_id)) as cursor:
            existing = await cursor.fetchone()
            if existing:
                return RepositoryInfo(**existing)
        # If the integrity error was for the pvc_path, the user will get a 500, which is acceptable for a race condition.
        raise HTTPException(status_code=500, detail="Internal server error due to data conflict.")

    except HTTPException: # Re-raise HTTPException to avoid being caught by the generic Exception handler
        raise

    except Exception as e:
        logger.error(f"Unexpected error in request_repository: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/repository/{record_id}/sync", status_code=202, response_model=RepositoryInfo)
async def sync_repository(
    record_id: int,
    db: aiosqlite.Connection = Depends(get_db_session)
):
    """
    Triggers a re-sync of an existing repository by creating a new git-clone Job.
    The underlying script will perform a 'git pull' if the directory exists.
    """
    # 1. Fetch the existing repository record
    async with db.execute("SELECT * FROM repositories WHERE id = ?", (record_id,)) as cursor:
        record = await cursor.fetchone()
    if not record:
        raise HTTPException(status_code=404, detail=f"Repository with ID {record_id} not found.")

    repo_info = RepositoryInfo(**record)

    # 2. Create a new, unique job name for the sync operation
    new_job_name = k8s.create_job_name(repo_info.repo_url, repo_info.commit_id)

    # 3. Generate the K8s Job manifest (this will use the clone-or-pull script)
    job_manifest = k8s.create_job_manifest(new_job_name, repo_info.repo_url, repo_info.commit_id, repo_info.pvc_path, repo_info.clone_single_branch, repo_info.clone_recursive)

    try:
        # 4. Create the new Job via the K8s API
        k8s.batch_v1_api.create_namespaced_job(
            body=job_manifest,
            namespace=POD_NAMESPACE
        )
        logger.info(f"Created sync Job: {new_job_name} for repository ID: {record_id}")
    except ApiException as e:
        logger.error(f"Exception when creating K8s sync Job: {e}\n")
        raise HTTPException(status_code=500, detail=f"Failed to create Kubernetes Job for sync: {e.body}")

    # 5. Update the database record with the new job name and PENDING status
    await db.execute(
        "UPDATE repositories SET status = 'PENDING', job_name = ?, last_synced_at = ? WHERE id = ?",
        # Set last_synced_at to the current time when sync is triggered
        (new_job_name, datetime.now(timezone.utc), record_id)
    )
    await db.commit()

    # 6. Fetch and return the updated record
    async with db.execute("SELECT * FROM repositories WHERE id = ?", (record_id,)) as cursor:
        updated_record = await cursor.fetchone()
    return RepositoryInfo(**updated_record)


async def _trigger_repository_deletion(record_id: int, db: aiosqlite.Connection):
    """
    Core logic to delete a repository's resources. Can be called from an API endpoint or a background task.
    """
    logger.info(f"Initiating deletion for repository ID: {record_id}")
    
    # Include a timestamp in the cleanup job name to ensure uniqueness
    cleanup_job_name = f"cleanup-{record_id}-{int(time.time())}"

    try:
        # 1. Fetch the record to be deleted (job_name and pvc_path are needed)
        async with db.execute("SELECT job_name, pvc_path FROM repositories WHERE id = ?", (record_id,)) as cursor:
            record = await cursor.fetchone()

        if not record:
            logger.warning(f"Deletion skipped: Repository with ID {record_id} not found in DB (already deleted?).")
            return

        # 2. Delete the DB record immediately
        await db.execute("DELETE FROM repositories WHERE id = ?", (record_id,))
        await db.commit()
        logger.info(f"DB record deleted for ID: {record_id}")

        # 3. Delete the Git Cloner Job from K8s
        try:
            k8s.batch_v1_api.delete_namespaced_job(
                name=record['job_name'],
                namespace=POD_NAMESPACE,
                # Setting to delete the Job's Pods as well
                body=client.V1DeleteOptions(propagation_policy='Foreground')
            )
            logger.info(f"Original Job deleted: {record['job_name']}")
        except ApiException as e:
            if e.status != 404: # Ignore 404 (Not Found) errors
                logger.warning(f"Failed to delete original Job {record['job_name']}: {e}")

        # 4. Create a Cleanup Job to delete the source code directory
        cleanup_manifest = k8s.create_cleanup_job_manifest(cleanup_job_name, record['pvc_path'])
        
        k8s.batch_v1_api.create_namespaced_job(
            body=cleanup_manifest,
            namespace=POD_NAMESPACE
        )
        logger.info(f"Cleanup Job created: {cleanup_job_name} for pvc_path: {record['pvc_path']}")

    except ApiException as e:
        logger.error(f"K8s API error during deletion for ID {record_id}: {e}")
        # In a background task, we log the error and continue.
        # Re-raising might stop the cleanup scheduler depending on error handling.
    except Exception as e:
        logger.error(f"Unexpected error during deletion for ID {record_id}: {e}")


@router.put("/repository/{record_id}/expiration", response_model=RepositoryInfo)
async def update_repository_expiration(
    record_id: int,
    req: RepositoryExpirationUpdateRequest,
    db: aiosqlite.Connection = Depends(get_db_session),
):
    """
    Updates the expiration date of a repository.
    - A positive `retention_days` sets the expiration that many days from now.
    - `retention_days: 0` makes the repository indefinite (removes expiration).
    """
    # 1. Calculate the new expiration date
    new_expired_at = None
    if req.retention_days > 0:
        new_expired_at = datetime.now(timezone.utc) + timedelta(days=req.retention_days)

    # 2. Update the database record
    current_timestamp = datetime.now(timezone.utc)
    async with db.execute(
        "UPDATE repositories SET expired_at = ?, updated_at = ? WHERE id = ?",
        (new_expired_at, current_timestamp, record_id),
    ) as cursor:
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail=f"Repository with ID {record_id} not found.")
        await db.commit()

    # 3. Fetch and return the updated record
    async with db.execute("SELECT * FROM repositories WHERE id = ?", (record_id,)) as cursor:
        updated_record = await cursor.fetchone()
        if not updated_record:
            # This case is unlikely if the update succeeded, but good for safety
            raise HTTPException(status_code=500, detail="Failed to retrieve updated repository record.")

    logger.info(f"Updated expiration for repository ID {record_id}. New expiration: {new_expired_at}")
    return RepositoryInfo(**updated_record)


@router.put("/repository/{record_id}/autosync", response_model=RepositoryInfo)
async def update_repository_auto_sync(
    record_id: int,
    req: RepositoryAutoSyncUpdateRequest,
    db: aiosqlite.Connection = Depends(get_db_session),
):
    """
    Updates the auto-sync settings for a repository.
    """
    # 1. Update the database record
    current_timestamp = datetime.now(timezone.utc)
    
    # If auto-sync is disabled, ensure the schedule is stored as NULL.
    schedule_to_save = req.auto_sync_schedule if req.auto_sync_enabled else None

    async with db.execute(
        "UPDATE repositories SET auto_sync_enabled = ?, auto_sync_schedule = ?, updated_at = ? WHERE id = ?",
        (req.auto_sync_enabled, schedule_to_save, current_timestamp, record_id),
    ) as cursor:
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail=f"Repository with ID {record_id} not found.")
        await db.commit()

    # 2. Fetch and return the updated record
    async with db.execute("SELECT * FROM repositories WHERE id = ?", (record_id,)) as cursor:
        updated_record = await cursor.fetchone()
        if not updated_record:
            raise HTTPException(status_code=500, detail="Failed to retrieve updated repository record.")

    logger.info(
        f"Updated auto-sync for repository ID {record_id}. "
        f"Enabled: {req.auto_sync_enabled}, Schedule: {schedule_to_save}"
    )
    # TODO: Need to notify the scheduler to update its job list

    return RepositoryInfo(**updated_record)


@router.delete("/repository/{record_id}", status_code=202)
async def delete_repository(
    record_id: int, 
    db: aiosqlite.Connection = Depends(get_db_session)
):
    """
    Initiates the deletion of a repository's resources via an API call.
    """
    # We still check for the record existence to provide immediate feedback to the user.
    async with db.execute("SELECT id FROM repositories WHERE id = ?", (record_id,)) as cursor:
        if not await cursor.fetchone():
            raise HTTPException(status_code=404, detail=f"Repository with ID {record_id} not found.")

    # The actual deletion is heavy, so we can run it in the background.
    # For simplicity here, we run it directly.
    # For a more robust system, you could add this to a proper background task queue.
    await _trigger_repository_deletion(record_id, db)

    return {"message": f"Deletion initiated for repository ID {record_id}."}


@router.get("/repositories", response_model=list[RepositoryInfo])
async def list_repositories(
    db: aiosqlite.Connection = Depends(get_db_session)
):
    """
    Returns a list of all managed (requested) code repositories.
    """
    async with db.execute("SELECT * FROM repositories ORDER BY created_at DESC") as cursor:
        rows = await cursor.fetchall()
    return [RepositoryInfo(**row) for row in rows]

@router.get("/repository/{record_id}/logs", response_model=JobLogs)
async def get_repository_logs(
    record_id: int,
    db: aiosqlite.Connection = Depends(get_db_session)
):
    """
    Retrieves the logs for the pod associated with a repository's job.
    """
    # 1. Get job_name from DB
    async with db.execute("SELECT job_name FROM repositories WHERE id = ?", (record_id,)) as cursor:
        record = await cursor.fetchone()
    if not record:
        raise HTTPException(status_code=404, detail=f"Repository with ID {record_id} not found.")

    job_name = record['job_name']

    try:
        # 2. Find the pod for the job using a label selector
        pod_list = k8s.core_v1_api.list_namespaced_pod(
            namespace=POD_NAMESPACE,
            label_selector=f"job-name={job_name}"
        )

        if not pod_list.items:
            # This can happen if the pod is already cleaned up or hasn't been created yet.
            return {"logs": f"No pod found for job '{job_name}'. The pod may have been cleaned up or is still pending creation."}

        pod_name = pod_list.items[0].metadata.name

        # 3. Get logs from the pod
        logs = k8s.core_v1_api.read_namespaced_pod_log(
            name=pod_name,
            namespace=POD_NAMESPACE
        )
        return {"logs": logs}

    except ApiException as e:
        logger.error(f"K8s API error when fetching logs for job {job_name}: {e}")
        return {"logs": f"Error fetching logs from Kubernetes: {e.reason}"}


async def cleanup_expired_repositories():
    """
    Scheduled job to find and delete expired repositories.
    """
    logger.info("Running scheduled job: cleanup_expired_repositories")
    db = None
    try:
        # We need a separate DB connection for the background task
        db = await aiosqlite.connect(DB_PATH, factory=custom_connection_factory)
        
        # Find repositories where expired_at is in the past
        now_utc = datetime.now(timezone.utc)
        async with db.execute("SELECT id FROM repositories WHERE expired_at IS NOT NULL AND expired_at < ?", (now_utc,)) as cursor:
            expired_repos = await cursor.fetchall()

        if not expired_repos:
            logger.info("No expired repositories found.")
            return

        logger.info(f"Found {len(expired_repos)} expired repositories to delete.")
        for repo in expired_repos:
            await _trigger_repository_deletion(repo['id'], db)
    finally:
        if db:
            await db.close()
