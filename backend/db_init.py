# db_init.py (located in the same directory as main.py)

import os
import sqlite3
import logging

logger = logging.getLogger(f"uvicorn.{__name__}")

DB_PATH = "/data/manager.db"

def initialize_db_sync():
    """
    Synchronously initializes the SQLite DB.
    This should be run once before Uvicorn starts.
    """
    logger.info(f"Attempting to initialize DB file at: {DB_PATH}")
    
    try:
        # Create the directory where the DB file will be stored
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        
        with sqlite3.connect(DB_PATH) as conn:
            # Match the settings with custom_connection_factory in main.py
            conn.row_factory = sqlite3.Row 
            cursor = conn.cursor()
            # Create table if it doesn't exist
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS repositories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    repo_url TEXT NOT NULL,
                    commit_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    job_name TEXT NOT NULL,
                    pvc_path TEXT NOT NULL UNIQUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expired_at TIMESTAMP,
                    last_synced_at TIMESTAMP,
                    clone_single_branch BOOLEAN DEFAULT FALSE,
                    clone_recursive BOOLEAN DEFAULT FALSE,
                    auto_sync_enabled BOOLEAN NOT NULL DEFAULT FALSE,
                    auto_sync_schedule TEXT,
                    UNIQUE(repo_url, commit_id)
                )
            """)

            # Add new columns to existing table for migration
            cursor.execute("PRAGMA table_info(repositories)")
            columns = [row[1] for row in cursor.fetchall()]
            if 'auto_sync_enabled' not in columns:
                cursor.execute("ALTER TABLE repositories ADD COLUMN auto_sync_enabled BOOLEAN NOT NULL DEFAULT FALSE")
                logger.info("Column 'auto_sync_enabled' added to repositories table.")
            if 'auto_sync_schedule' not in columns:
                cursor.execute("ALTER TABLE repositories ADD COLUMN auto_sync_schedule TEXT")
                logger.info("Column 'auto_sync_schedule' added to repositories table.")
            if 'task_log' not in columns:
                cursor.execute("ALTER TABLE repositories ADD COLUMN task_log TEXT")
                logger.info("Column 'task_log' added to repositories table.")

            conn.commit()
        logger.info("Database and table initialization/migration complete (Sync).")
        
    except Exception as e:
        logger.error(f"FATAL: Failed to initialize SQLite database: {e}")
        exit(1)

if __name__ == "__main__":
    initialize_db_sync()