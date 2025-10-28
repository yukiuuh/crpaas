import aiosqlite
import sqlite3

from .config import DB_PATH

def custom_connection_factory(database, **kwargs):
    """
    Custom connection factory called by aiosqlite/sqlite3.

    It needs to accept the 'database' argument passed from sqlite3.connect
    and '**kwargs' (which includes 'factory' itself).
    """
    # Ignoring kwargs, it uses the passed 'database' path
    # to call the standard sqlite3.connect.
    conn = sqlite3.connect(database)
    
    # Set connection properties here
    conn.row_factory = sqlite3.Row
    return conn

# -------------------------------------------------------------
# DB Session for API Requests (Dependency Injection)
# -------------------------------------------------------------
async def get_db_session():
    """
    DB session for FastAPI's Dependency Injection.
    Connects at the start of an API request and automatically closes at the end.
    """
    db = None
    try:
        db = await aiosqlite.connect(DB_PATH, factory=custom_connection_factory)
        yield db 
    finally:
        if db:
            await db.close()
