"""PostgreSQL connection helper using psycopg2."""

import contextlib
import psycopg2
import psycopg2.extras
from common.config import DB_DSN


def get_connection():
    """Return a new psycopg2 connection."""
    return psycopg2.connect(DB_DSN)


@contextlib.contextmanager
def cursor(commit: bool = True):
    """Context manager that yields a DictCursor and handles commit/rollback."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            yield cur
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def execute(sql: str, params=None, *, commit: bool = True):
    """Execute a single statement (INSERT / UPDATE / DELETE)."""
    with cursor(commit=commit) as cur:
        cur.execute(sql, params)


def fetchone(sql: str, params=None) -> dict | None:
    """Execute a SELECT and return the first row as a dict (or None)."""
    with cursor(commit=False) as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def fetchall(sql: str, params=None) -> list[dict]:
    """Execute a SELECT and return all rows as a list of dicts."""
    with cursor(commit=False) as cur:
        cur.execute(sql, params)
        return cur.fetchall()
