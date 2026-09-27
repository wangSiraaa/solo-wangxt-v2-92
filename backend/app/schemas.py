"""Pydantic 请求 / 响应模型。"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ColumnOut(BaseModel):
    name: str
    type: str
    nullable: bool
    is_pk: bool
    db_generated: bool
    references: str | None = None


class TableOut(BaseModel):
    name: str
    columns: list[ColumnOut]
    pk_columns: list[str]
    unique_columns: list[str]
    fk_targets: list[str]
    generation_order: int


class SchemaOut(BaseModel):
    tables: list[TableOut]
    generation_sequence: list[str]


class GenerateIn(BaseModel):
    seed: int = Field(default=42, ge=0, le=2**31 - 1)
    counts: dict[str, int] = Field(default_factory=dict)
    batch_id: str | None = Field(default=None, max_length=80)


class BatchOut(BaseModel):
    id: str
    seed: int
    params: dict[str, Any]
    status: str
    progress: dict[str, Any]
    inserted_rows: int
    constraint_checks: list[dict[str, Any]] | None = None
    error: dict[str, Any] | None = None
    created_at: str | None = None
    finished_at: str | None = None


class CleanupOut(BaseModel):
    batch_id: str
    deleted_rows: int
    deleted_by_table: dict[str, int]
    skipped_orphan_pk: int
