"""Application configuration.

All values come from environment variables so the workbench can point at any
local PostgreSQL test target.  Defaults match the demo instance created by
``scripts/init_demo_db.sql``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    # libpq connection string used by SQLAlchemy
    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg2://postgres@localhost:5432/synthdb",
    )
    # Business schema the workbench is allowed to read from / write to.
    business_schema: str = os.getenv("BUSINESS_SCHEMA", "public")
    # Private schema holding batch bookkeeping tables (never introspected).
    workbench_schema: str = os.getenv("WORKBENCH_SCHEMA", "synth")
    # Faker locale used for non-real names / addresses.
    faker_locale: str = os.getenv("FAKER_LOCALE", "zh_CN")
    # Max number of sample rows kept per table in job state.
    sample_size: int = int(os.getenv("SAMPLE_SIZE", "10"))
    # Max rows per INSERT executemany batch.
    insert_batch_size: int = int(os.getenv("INSERT_BATCH_SIZE", "200"))


settings = Settings()
