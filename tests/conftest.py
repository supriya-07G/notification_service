import os
import sys
import pytest

# Ensure project root is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Force in-memory DB before any module reads DB_PATH
os.environ["DB_PATH"] = ":memory:"

# Session auth env vars for testing
os.environ["SESSION_SECRET_KEY"] = "a" * 64  # 64-char test-only key
os.environ["SESSION_SECURE_COOKIE"] = "false"

from db.init import get_connection


@pytest.fixture
def test_db():
    """Provide a clean in-memory SQLite database with schema applied.

    Creates an in-memory connection, applies WAL pragmas (via get_connection),
    reads and executes db/schema.sql, yields the connection, and closes it.
    """
    conn = get_connection()

    schema_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "db",
        "schema.sql",
    )
    with open(schema_path, "r", encoding="utf-8") as f:
        schema_sql = f.read()

    conn.executescript(schema_sql)
    conn.commit()

    yield conn

    conn.close()


class _NonClosingConnection:
    """Wrapper that delegates everything to a real sqlite3.Connection but no-ops close().

    sqlite3.Connection.close is a read-only C attribute, so we can't monkey-patch it.
    This thin proxy lets production code call conn.close() without actually closing the
    test fixture's connection.
    """

    def __init__(self, real_conn):
        self._conn = real_conn

    def close(self):
        pass  # no-op — fixture manages lifecycle

    def __getattr__(self, name):
        return getattr(self._conn, name)


@pytest.fixture
def non_closing_db(test_db):
    """Yield a _NonClosingConnection wrapper around the test_db fixture.

    Use this when the code under test calls conn.close() and you need
    the underlying connection to survive for post-call assertions.
    """
    return _NonClosingConnection(test_db)
