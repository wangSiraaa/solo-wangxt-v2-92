"""Database engine and workbench bookkeeping tables.

The workbench keeps its own metadata in a private schema (``synth`` by
default) so that business-table introspection only sees user tables.

Bookkeeping model
-----------------
``synth_batches``  - one row per generation batch (which seed, counts, ...)
``synth_batch_rows`` - every generated row's primary key, per table per batch

Deleting a batch therefore means *only* deleting rows whose primary key is
registered here.  Pre-existing business records are never touched.
"""
from __future__ import annotations

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    func,
    text,
)

from .config import settings

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    future=True,
)

metadata = MetaData(schema=settings.workbench_schema)

synth_batches = Table(
    "synth_batches",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("batch_no", String(40), nullable=False, unique=True),
    Column("seed", Integer, nullable=False),
    Column("status", String(20), nullable=False),  # dry_run / committed / failed
    Column("table_counts", Text, nullable=False),  # JSON: {table: count}
    Column("note", Text),
    Column("created_at", DateTime, nullable=False, server_default=func.now()),
)

synth_batch_rows = Table(
    "synth_batch_rows",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("batch_id", Integer, nullable=False),
    Column("table_schema", String(128), nullable=False),
    Column("table_name", String(128), nullable=False),
    Column("pk_cols", Text, nullable=False),   # JSON array, e.g. ["id"]
    Column("pk_values", Text, nullable=False),  # JSON array, e.g. [123]
    ForeignKeyConstraint(
        ["batch_id"],
        [f"{settings.workbench_schema}.synth_batches.id"],
        ondelete="CASCADE",
    ),
)


def init_db() -> None:
    """Create private schema + bookkeeping tables if missing."""
    with engine.begin() as conn:
        conn.execute(
            text(
                f"CREATE SCHEMA IF NOT EXISTS {settings.workbench_schema}"
            )
        )
    metadata.create_all(engine)
