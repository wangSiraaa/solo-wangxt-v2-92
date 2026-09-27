"""FastAPI application: synthetic-data generation workbench."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import batches
from .config import settings
from .db import engine, init_db
from .generator import GenerationError
from .introspect import build_dependency_graph, introspect, topo_order
from .worker import get_job, list_jobs, run_generation

app = FastAPI(
    title="合成数据生成工作台",
    version="1.0.0",
    description="读取 PostgreSQL 表结构并按外键顺序生成可复现的合成数据。",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class GenerationRequest(BaseModel):
    seed: int = Field(..., ge=0, le=2_147_483_647, description="随机种子")
    counts: dict[str, int] = Field(
        default_factory=dict, description="每张表要生成的记录数"
    )
    dry_run: bool = Field(
        False, description="试运行：执行约束检查但回滚，不写入任何数据"
    )
    note: str = Field("", description="批次备注")


class CleanupRequest(BaseModel):
    batch_no: str


@app.on_event("startup")
def _startup() -> None:
    init_db()


@app.get("/api/health")
def health() -> dict[str, Any]:
    with engine.connect() as conn:
        conn.commit()
    return {"status": "ok", "schema": settings.business_schema}


@app.get("/api/tables")
def get_tables() -> dict[str, Any]:
    """Allowed table structures: columns, PK, FK, NOT NULL, unique, checks."""
    tables = introspect(engine)
    graph = build_dependency_graph(tables)
    try:
        order = topo_order(tables)
        cycle_error: str | None = None
    except ValueError as exc:
        order = []
        cycle_error = str(exc)
    return {
        "schema": settings.business_schema,
        "tables": tables,
        "generation_order": order,
        "dependencies": {k: sorted(v) for k, v in graph.items()},
        "cycle_error": cycle_error,
    }


@app.post("/api/generate")
def generate(req: GenerationRequest) -> dict[str, str]:
    if req.seed is None:
        raise HTTPException(status_code=422, detail="必须提供随机种子")
    for table_name, n in req.counts.items():
        if n < 0 or n > 100_000:
            raise HTTPException(
                status_code=422,
                detail=f"表 {table_name} 的记录数必须在 0..100000 之间",
            )
    job_id = run_generation(
        seed=req.seed,
        counts=req.counts,
        dry_run=req.dry_run,
        note=req.note,
    )
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict[str, Any]:
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return job


@app.get("/api/jobs")
def recent_jobs() -> list[dict[str, Any]]:
    return list_jobs()


@app.get("/api/batches")
def get_batches() -> list[dict[str, Any]]:
    return batches.list_batches()


@app.get("/api/batches/{batch_no}")
def get_batch(batch_no: str) -> dict[str, Any]:
    detail = batches.batch_detail(batch_no)
    if detail is None:
        raise HTTPException(status_code=404, detail="批次不存在")
    return detail


@app.post("/api/batches/{batch_no}/cleanup")
def cleanup_batch(batch_no: str) -> dict[str, Any]:
    try:
        return batches.cleanup_batch(batch_no)
    except GenerationError as exc:
        raise HTTPException(
            status_code=409,
            detail={"message": exc.message, "details": exc.details},
        )
