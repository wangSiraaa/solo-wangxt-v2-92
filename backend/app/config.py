"""应用配置与数据库引擎。"""
from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://synth:synth@127.0.0.1:5439/synthdb",
)
SIDE_PREFIX = os.getenv("SIDE_PREFIX", "synth_")
BATCH_TABLE = f"{SIDE_PREFIX}batches"
ROWS_TABLE = f"{SIDE_PREFIX}rows"

MAX_ROWS_PER_TABLE = int(os.getenv("MAX_ROWS_PER_TABLE", "100000"))


def _parse_allowed() -> list[str] | None:
    raw = os.getenv("ALLOWED_TABLES", "customers,orders").strip()
    if not raw:
        return None
    return [t.strip() for t in raw.split(",") if t.strip()]


ALLOWED_TABLES = _parse_allowed()


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        future=True,
    )
