import os
import pytest
import asyncio
import aiosqlite
from httpx import AsyncClient, ASGITransport
from main import app
from app.database import get_db_session

# Use an in-memory database for testing
TEST_DB_PATH = ":memory:"

async def init_test_db(db: aiosqlite.Connection):
    await db.execute("""
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
            task_log TEXT,
            UNIQUE(repo_url, commit_id)
        )
    """)
    await db.commit()

@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()

@pytest.fixture(scope="function")
async def db_session():
    async with aiosqlite.connect(TEST_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await init_test_db(db)
        yield db

@pytest.fixture(scope="function")
async def client(db_session):
    async def override_get_db_session():
        yield db_session

    app.dependency_overrides[get_db_session] = override_get_db_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
