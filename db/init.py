import sqlite3
import logging
import os
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def get_connection() -> sqlite3.Connection:
    """Return a SQLite connection with WAL mode and busy_timeout applied.

    Rule 1: Every database access must go through this function.
    Never call sqlite3.connect() directly anywhere else in the codebase.
    """
    db_path = os.getenv("DB_PATH", "./storage.sqlite")
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def execute_write(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> sqlite3.Cursor:
    """Execute a single write statement with error handling.

    Wraps every DB write in try/except. On OperationalError, logs the
    error with full context and re-raises. Never swallows DB errors silently.
    """
    try:
        cursor = conn.execute(sql, params)
        conn.commit()
        return cursor
    except sqlite3.OperationalError as e:
        logger.error("DB OperationalError: %s | SQL: %s | Params: %s", e, sql, params)
        conn.rollback()
        raise
    except sqlite3.IntegrityError as e:
        logger.error("DB IntegrityError: %s | SQL: %s | Params: %s", e, sql, params)
        conn.rollback()
        raise
