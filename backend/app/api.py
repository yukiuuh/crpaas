import logging
import re
import sqlite3
import time
import asyncio
from datetime import datetime, timedelta, timezone

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from kubernetes import client
from kubernetes.client.rest import ApiException

from . import k8s, worker
from .worker import K8S_EXEC_LOCK

from .config import POD_NAMESPACE, OPEN_GROK_BASE_URL, DB_PATH
from .database import get_db_session, custom_connection_factory
from .schemas import (
    RepositoryInfo, RepositoryRequest, RepositoryExpirationUpdateRequest, JobLogs, 
    AppConfig, RepositoryAutoSyncUpdateRequest, OpenGrokPodStatus, 
    OpenGrokDeploymentStatus, OpenGrokStatusResponse, RepositoryStatus,
    RepositoryExport, RepositoriesExportResponse, RepositoriesImportRequest,
    RepositoryImportResult, RepositoriesImportResponse
)

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
    background_tasks: BackgroundTasks,
    db: aiosqlite.Connection = Depends(get_db_session) 
):
    """
    Receives a repository URL and commit ID, then creates a K8s Job.
    An optional `project_name` can be provided to customize the directory name.
    """
    try:
        # Validate Git repository URL format before proceeding
        git_url_pattern = re.compile(r"^((https?|git):\/\/.+?|git@.+?:.+?)\.git$")
        if not git_url_pattern.match(req.repo_url):
            raise HTTPException(
                status_code=422,
                detail=(
                    "Invalid 'repo_url'. It must be a valid Git URL ending in .git "
                    "(e.g., 'https://...', 'git://...', or 'git@...')."
                ),
            )
        # 0. Determine expiration date
        expired_at = None
        # If retention_days is a positive number, calculate the expiration date.
        if req.retention_days is not None and req.retention_days > 0:
            expired_at = datetime.now(timezone.utc) + timedelta(days=req.retention_days)
        elif req.retention_days == 0:  # 0 means indefinite retention
            expired_at = None

        # 1. Determine the PVC path (project name)
        if req.project_name:
            # Sanitize the provided project name to ensure it's safe for use as a directory name.
            sanitized_name = k8s.sanitize_for_dns(req.project_name)
            # Even if the validator in schemas.py passes, it might allow uppercase letters if not configured.
            # This check ensures that the name doesn't change after sanitization (e.g., 'MyProject' -> 'myproject').
            if sanitized_name != req.project_name:
                raise HTTPException(
                    status_code=422,
                    detail="Invalid 'project_name'. It must consist of lowercase alphanumeric characters or '-', and start and end with an alphanumeric character."
                )
            pvc_path = sanitized_name
            # Check if a project with this custom name already exists
            async with db.execute("SELECT repo_url, commit_id FROM repositories WHERE pvc_path = ?", (pvc_path,)) as cursor:
                conflicting_repo = await cursor.fetchone()
                if conflicting_repo:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Project name '{pvc_path}' is already in use by repository '{conflicting_repo['repo_url']}'. Please choose a different name."
                    )
        else:
            # Generate the path if not provided
            repo_name_sanitized = k8s.sanitize_for_dns(req.repo_url.split('/')[-1])
            commit_hash_short = req.commit_id[:12]
            pvc_path = f"{repo_name_sanitized}-{commit_hash_short}"

        # Check for duplicate request (same repo and commit)
        async with db.execute("SELECT pvc_path FROM repositories WHERE repo_url = ? AND commit_id = ?", 
                              (req.repo_url, req.commit_id)) as cursor:
            existing = await cursor.fetchone()
            
        if existing:
            logger.info(f"Duplicate request: {req.repo_url} @ {req.commit_id}")
            raise HTTPException(
                status_code=409,
                detail=f"This repository and commit ID combination already exists under the project name '{existing['pvc_path']}'."
            )


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
                req.repo_url, req.commit_id, RepositoryStatus.PENDING.value, "EXEC", pvc_path, 
                expired_at, req.clone_single_branch, req.clone_recursive, 
                datetime.now(timezone.utc),
                req.auto_sync_enabled,
                req.auto_sync_schedule if req.auto_sync_enabled else None
            )
        ) as cursor:
            await db.commit()
            record_id = cursor.lastrowid

        # 6. Add background task to clone repository
        background_tasks.add_task(
            worker.perform_clone_task,
            record_id=record_id,
            repo_url=req.repo_url,
            pvc_path=pvc_path,
            commit_id=req.commit_id,
            single_branch=req.clone_single_branch,
            recursive=req.clone_recursive
        )
        
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

    # 2. Update the database record with PENDING status
    await db.execute(
        "UPDATE repositories SET status = ?, job_name = 'SYNC', last_synced_at = ? WHERE id = ?",
        (RepositoryStatus.PENDING.value, datetime.now(timezone.utc), record_id)
    )
    await db.commit()

    # 3. Add background task to clone/sync repository
    background_tasks.add_task(
        worker.perform_clone_task,
        record_id=record_id,
        repo_url=repo_info.repo_url,
        pvc_path=repo_info.pvc_path,
        commit_id=repo_info.commit_id,
        single_branch=repo_info.clone_single_branch,
        recursive=repo_info.clone_recursive
    )


    # 6. Fetch and return the updated record
    async with db.execute("SELECT * FROM repositories WHERE id = ?", (record_id,)) as cursor:
        updated_record = await cursor.fetchone()
    return RepositoryInfo(**updated_record)


# Removed _trigger_repository_deletion as we use worker.perform_cleanup_task


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
    background_tasks: BackgroundTasks,
    db: aiosqlite.Connection = Depends(get_db_session)
):
    """
    Initiates the deletion of a repository's resources in the background.
    """
    # 1. Check for the record existence to provide immediate feedback to the user.
    async with db.execute("SELECT id FROM repositories WHERE id = ?", (record_id,)) as cursor:
        if not await cursor.fetchone():
            raise HTTPException(status_code=404, detail=f"Repository with ID {record_id} not found.")

    # 2. Immediately update the status to 'DELETING'
    await db.execute(
        "UPDATE repositories SET status = ? WHERE id = ?", 
        (RepositoryStatus.DELETING.value, record_id)
    )
    await db.commit()

    # 3. Add the heavy deletion logic to a background task
    background_tasks.add_task(worker.perform_cleanup_task, record_id)

    return {"message": f"Deletion initiated for repository ID {record_id}."}


@router.get("/repositories", response_model=list[RepositoryInfo])
async def list_repositories(
    db: aiosqlite.Connection = Depends(get_db_session)
):
    """
    Returns a list of all managed (requested) code repositories with dynamically updated statuses.
    """
    async with db.execute("SELECT * FROM repositories ORDER BY created_at DESC") as cursor:
        rows = await cursor.fetchall()
    
    repos = [RepositoryInfo(**row) for row in rows]

    # Create a list of tasks to run concurrently
    tasks = []
    for repo in repos:
        # For pending jobs, dynamically query the real-time status from Kubernetes
        if repo.status == RepositoryStatus.PENDING:
            tasks.append(update_repo_status(repo))

    # Run status update tasks in parallel
    if tasks:
        await asyncio.gather(*tasks)

    return repos


async def update_repo_status(repo: RepositoryInfo):
    """
    Helper function to update a single repository's status in-place.
    Since we don't rely on Jobs anymore, this mainly handles logic if we wanted to
    query active execs, but for now we rely on DB status being accurate.
    """
    pass


@router.get("/repositories/export", response_model=RepositoriesExportResponse)
async def export_repositories(
    db: aiosqlite.Connection = Depends(get_db_session)
):
    """
    Exports all repositories as JSON for backup or migration purposes.
    Only includes configuration data, not runtime status or logs.
    """
    async with db.execute("SELECT * FROM repositories ORDER BY created_at DESC") as cursor:
        rows = await cursor.fetchall()
    
    now = datetime.now(timezone.utc)
    exports = []
    
    for row in rows:
        # Calculate retention_days from expired_at
        retention_days = None
        if row['expired_at']:
            expired_at = datetime.fromisoformat(row['expired_at'].replace('Z', '+00:00')) if isinstance(row['expired_at'], str) else row['expired_at']
            # Calculate remaining days from now (rounded up)
            remaining = (expired_at - now).days
            retention_days = max(0, remaining)  # Don't export negative days
        
        exports.append(RepositoryExport(
            repo_url=row['repo_url'],
            commit_id=row['commit_id'],
            pvc_path=row['pvc_path'],
            clone_single_branch=row['clone_single_branch'],
            clone_recursive=row['clone_recursive'],
            retention_days=retention_days,
            auto_sync_enabled=row['auto_sync_enabled'],
            auto_sync_schedule=row['auto_sync_schedule']
        ))
    
    return RepositoriesExportResponse(
        exported_at=now,
        repositories=exports
    )


@router.post("/repositories/import", response_model=RepositoriesImportResponse)
async def import_repositories(
    req: RepositoriesImportRequest,
    background_tasks: BackgroundTasks,
    db: aiosqlite.Connection = Depends(get_db_session)
):
    """
    Imports repositories from a JSON export.
    Skips duplicates (existing pvc_path) and reports results for each repository.
    """
    results: list[RepositoryImportResult] = []
    created_count = 0
    skipped_count = 0
    error_count = 0
    
    for repo_export in req.repositories:
        try:
            # Check if pvc_path already exists
            async with db.execute(
                "SELECT id, repo_url FROM repositories WHERE pvc_path = ?", 
                (repo_export.pvc_path,)
            ) as cursor:
                existing = await cursor.fetchone()
            
            if existing:
                results.append(RepositoryImportResult(
                    pvc_path=repo_export.pvc_path,
                    status="skipped",
                    message=f"Already exists (ID: {existing['id']}, URL: {existing['repo_url']})"
                ))
                skipped_count += 1
                continue
            
            # Calculate expired_at from retention_days
            expired_at = None
            if repo_export.retention_days is not None and repo_export.retention_days > 0:
                expired_at = datetime.now(timezone.utc) + timedelta(days=repo_export.retention_days)
            
            # Insert new repository
            async with db.execute(
                """
                INSERT INTO repositories (
                    repo_url, commit_id, status, job_name, pvc_path, expired_at, 
                    clone_single_branch, clone_recursive, last_synced_at,
                    auto_sync_enabled, auto_sync_schedule
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    repo_export.repo_url, repo_export.commit_id, RepositoryStatus.PENDING.value, 
                    "IMPORT", repo_export.pvc_path, expired_at,
                    repo_export.clone_single_branch, repo_export.clone_recursive,
                    datetime.now(timezone.utc),
                    repo_export.auto_sync_enabled,
                    repo_export.auto_sync_schedule if repo_export.auto_sync_enabled else None
                )
            ) as cursor:
                await db.commit()
                record_id = cursor.lastrowid
            
            # Add background task to clone repository
            background_tasks.add_task(
                worker.perform_clone_task,
                record_id=record_id,
                repo_url=repo_export.repo_url,
                pvc_path=repo_export.pvc_path,
                commit_id=repo_export.commit_id,
                single_branch=repo_export.clone_single_branch,
                recursive=repo_export.clone_recursive
            )
            
            results.append(RepositoryImportResult(
                pvc_path=repo_export.pvc_path,
                status="created",
                message=f"Import initiated (ID: {record_id})"
            ))
            created_count += 1
            
        except Exception as e:
            logger.error(f"Error importing repository {repo_export.pvc_path}: {e}")
            results.append(RepositoryImportResult(
                pvc_path=repo_export.pvc_path,
                status="error",
                message=str(e)
            ))
            error_count += 1
    
    logger.info(f"Import completed: {created_count} created, {skipped_count} skipped, {error_count} errors")
    
    return RepositoriesImportResponse(
        total=len(req.repositories),
        created=created_count,
        skipped=skipped_count,
        errors=error_count,
        results=results
    )

@router.get("/repository/{record_id}/logs", response_model=JobLogs)
async def get_repository_logs(
    record_id: int,
    db: aiosqlite.Connection = Depends(get_db_session)
):
    """
    Retrieves logs for the pod associated with a repository's job.
    - For standard statuses, it fetches logs from the clone/sync job.
    - For 'DELETING' or 'DELETION_FAILED' statuses, it fetches logs from the cleanup job.
    """
    # 1. Get repository info from DB
    async with db.execute("SELECT job_name, pvc_path, status, task_log FROM repositories WHERE id = ?", (record_id,)) as cursor:
        record = await cursor.fetchone()
    if not record:
        raise HTTPException(status_code=404, detail=f"Repository with ID {record_id} not found.")

    repo_status = record['status']
    task_log = record['task_log']

    pvc_path = record['pvc_path']

    # 2. Return stored logs if available
    if task_log:
        return {"logs": task_log}
    
    # 3. If in progress, try to read the real-time log from the pod
    if repo_status in [RepositoryStatus.PENDING.value, RepositoryStatus.CLONING.value, RepositoryStatus.POD_CREATING.value, RepositoryStatus.DELETING.value]:
        log_file = f"/tmp/task-log-{pvc_path}.txt"
        async with K8S_EXEC_LOCK:
            live_logs = await asyncio.to_thread(k8s.exec_read_file, log_file)
        if live_logs:
            return {"logs": live_logs}
        
        return {"logs": f"Task is currently in progress (Status: {repo_status}). Logs are being generated..."}

    return {"logs": "No logs available for this repository."}

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
            # Trigger cleanup directly (fire and forget in background loop)
            asyncio.create_task(worker.perform_cleanup_task(repo['id']))
    finally:
        if db:
            await db.close()


@router.get("/opengrok/status", response_model=OpenGrokStatusResponse)
async def get_opengrok_status():
    """
    Retrieves the status, resource usage, and deployment info of OpenGrok.
    """
    resources = k8s.get_opengrok_resources()
    deployment = resources.get("deployment")
    pod_list = resources.get("pods", [])

    deployment_status = None
    if deployment:
        # Create Deployment Status object once
        status = deployment.status
        deployment_status = OpenGrokDeploymentStatus(
            name=deployment.metadata.name,
            replicas=status.replicas or 0,
            ready_replicas=status.ready_replicas or 0,
            available_replicas=status.available_replicas or 0,
            unavailable_replicas=status.unavailable_replicas or 0,
            updated_replicas=status.updated_replicas or 0,
        )

    pod_statuses = []
    for pod in pod_list:
        # Create a status object for each pod
        pod_name = pod.metadata.name
        metrics = k8s.get_pod_metrics(pod_name)
        async with K8S_EXEC_LOCK:
            storage_list_of_dicts = await asyncio.to_thread(k8s.get_storage_usage, pod_name)
        
        pod_status = OpenGrokPodStatus(
            pod_name=pod_name,
            pod_status=pod.status.phase,
            pod_ip=pod.status.pod_ip,
            node_name=pod.spec.node_name,
            cpu_usage=metrics.get("cpu"),
            memory_usage=metrics.get("memory"),
            storage_usage=storage_list_of_dicts,
        )
        pod_statuses.append(pod_status)
    
    return OpenGrokStatusResponse(
        deployment_status=deployment_status,
        pod_statuses=pod_statuses
    )


@router.get("/opengrok/logs", response_model=JobLogs)
async def get_opengrok_logs(pod_name: str, tail_lines: int = 500):
    """
    Retrieves the logs for a specific OpenGrok pod.
    """
    # The pod_name is now a required query parameter, so we don't need to find the pod first.
    # We directly request the logs for the given pod name.
    # tail_lines can be adjusted via query parameter.
    logs = k8s.get_pod_logs(pod_name, tail_lines=tail_lines)
    
    return JobLogs(logs=logs)
