import asyncio
import logging

from fastapi import FastAPI
from app import config, worker
from app.api import router as api_router

# --- Logging Setup ---
logger = logging.getLogger(f"uvicorn.{__name__}")

# --- FastAPI Application ---
app = FastAPI(
    title="CRPaaS Manager",
    description="API to dynamically fetch Git repositories."
)

# -------------------------------------------------------------
# FastAPI Lifecycle Events
# -------------------------------------------------------------
@app.on_event("startup")
async def startup_event():
    """Start the worker on application startup (DB initialization is handled by db_init.py)."""
    
    if not all([config.PVC_NAME, config.GIT_CLONER_IMAGE, config.POD_NAMESPACE]):
        logger.error("FATAL: Missing environment variables! (SOURCE_CODE_PVC_NAME, GIT_CLONER_IMAGE, POD_NAMESPACE)")
        # In a real scenario, the process should terminate here
    
    logger.info("Database initialization skipped (handled by pre-start script).")
    
    # Start the background worker
    watcher_task = asyncio.create_task(worker.job_watcher_worker())
    auto_sync_task = asyncio.create_task(worker.auto_sync_worker())
    app.state.worker_tasks = [watcher_task, auto_sync_task]
    
    logger.info("FastAPI startup complete, background workers initiated.")

@app.on_event("shutdown")
async def shutdown_event():
    """Stop the worker on application shutdown."""
    worker.STOP_WATCHER.set()
    tasks = app.state.worker_tasks
    if tasks:
        # Wait for all tasks to complete with a timeout
        done, pending = await asyncio.wait(tasks, timeout=5.0)

        for task in pending:
            logger.warning(f"Worker task {task.get_name()} did not stop gracefully, cancelling.")
            task.cancel()
        
        # Gather results to propagate any exceptions during shutdown
        await asyncio.gather(*done, return_exceptions=True)
    
    logger.info("FastAPI shutdown complete, background workers stopped.")

# Include the API router
app.include_router(api_router, prefix="/api/v1")
