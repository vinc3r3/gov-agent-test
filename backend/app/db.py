from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import settings

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

_pool: ConnectionPool | None = None


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(settings.database_url, min_size=1, max_size=10,
                               kwargs={"row_factory": dict_row}, open=True)
    return _pool


@contextmanager
def conn():
    with pool().connection() as c:
        yield c


def connect() -> psycopg.Connection:
    return psycopg.connect(settings.database_url, row_factory=dict_row, autocommit=True)


def run_sql_file(name: str) -> None:
    sql = (SQL_DIR / name).read_text()
    with connect() as c:
        c.execute(sql)


def query(sql: str, params=None) -> list[dict]:
    with conn() as c:
        return c.execute(sql, params or None).fetchall()
