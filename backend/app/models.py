"""批次溯源侧表（不改动业务表结构）。

- synth_batches : 每个生成批次一行，保存参数 / 状态 / 进度 / 错误 / 约束检查结果
- synth_rows    : 批次中插入的每一条业务行（表名 + 主键 JSONB），
                  使“仅按本批次清理”可以精确删除，不碰既有记录
"""
from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

from .config import BATCH_TABLE, ROWS_TABLE, get_engine

metadata = MetaData()

batches_t = Table(
    BATCH_TABLE,
    metadata,
    Column("id", String(80), primary_key=True),
    Column("seed", Integer, nullable=False),
    Column("params", JSONB, nullable=False),
    Column("status", String(20), nullable=False, index=True),  # running/done/failed
    Column("progress", JSONB, nullable=False, server_default="{}"),
    Column("inserted_rows", Integer, nullable=False, server_default="0"),
    Column("constraint_checks", JSONB, nullable=True),
    Column("error", JSONB, nullable=True),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    Column("finished_at", DateTime(timezone=True), nullable=True),
)

# row_pk 存 JSONB 化的业务主键（支持复合主键）。
# JSON 不能进 btree 主键，因此用代理 id + (批次,表,主键) 唯一约束。
rows_t = Table(
    ROWS_TABLE,
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("batch_id", String(80), nullable=False, index=True),
    Column("table_name", String(120), nullable=False, index=True),
    Column("row_pk", JSONB, nullable=False),
    UniqueConstraint("batch_id", "table_name", "row_pk", name="synth_rows_uk"),
)


def init_sidecar() -> None:
    metadata.create_all(get_engine())
