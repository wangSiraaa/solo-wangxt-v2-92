"""FastAPI 入口：表结构 / 生成 / 进度 / 批次 / 清理 / 约束结果。"""
from __future__ import annotations

import threading
import uuid
from datetime import datetime
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from . import cleanup as cleanup_mod
from .config import BATCH_TABLE, get_engine
from .generator import DataGenerator
from .models import batches_t, rows_t, init_sidecar
from .reflection import reflect, topo_order
from .schemas import BatchOut, GenerateIn, SchemaOut, CleanupOut

app = FastAPI(title="合成数据生成工作台", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 内存中运行的批次：batch_id -> Event
_running: dict[str, threading.Event] = {}
_running_lock = threading.Lock()


@app.on_event("startup")
def _startup() -> None:
    init_sidecar()


@app.get("/api/health")
def health() -> dict:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("select 1"))
        return {"status": "ok", "batch_table": BATCH_TABLE}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            status_code=503, content={"status": "error", "detail": str(exc)}
        )


@app.get("/api/schema", response_model=SchemaOut)
def get_schema() -> SchemaOut:
    try:
        tables = reflect(get_engine())
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"无法读取表结构: {exc}")
    ordered = topo_order(tables)
    order_index = {t.name: i for i, t in enumerate(ordered)}
    return SchemaOut(
        tables=[
            _table_out(t, order_index[t.name])
            for t in sorted(tables, key=lambda t: order_index[t.name])
        ],
        generation_sequence=[t.name for t in ordered],
    )


@app.post("/api/batches", response_model=BatchOut, status_code=201)
def create_batch(body: GenerateIn) -> BatchOut:
    # 校验表名 & 行数
    tables = {t.name: t for t in reflect(get_engine())}
    for name in body.counts:
        if name not in tables:
            raise HTTPException(400, f"未知表名: {name}")
    for name, n in body.counts.items():
        if n < 0:
            raise HTTPException(400, f"表 {name} 行数不能为负")

    batch_id = body.batch_id or f"batch-{datetime.utcnow():%Y%m%d%H%M%S}-{uuid.uuid4().hex[:8]}"
    params: dict[str, Any] = {"counts": body.counts}

    # 父表行数为 0 时不再拒绝：子表可引用库内既有父表行；
    # 若库内也没有可引用的行，生成器会以 foreign_key_violation 明确报错。

    with get_engine().begin() as conn:
        conn.execute(
            batches_t.insert().values(
                id=batch_id,
                seed=body.seed,
                params=params,
                status="running",
                progress={"current_table": None, "tables": {}, "percent": 0},
                inserted_rows=0,
            )
        )

    stop = threading.Event()
    with _running_lock:
        _running[batch_id] = stop
    thread = threading.Thread(
        target=_run_generation, args=(batch_id, body.seed, dict(body.counts)),
        daemon=True,
    )
    thread.start()
    return _fetch_batch(batch_id)


def _run_generation(batch_id: str, seed: int, counts: dict[str, int]) -> None:
    try:
        # 用批次 id 末 8 位（uuid 段）作唯一列后缀，同种子跨批次不冲突
        tag = batch_id.rsplit("-", 1)[-1][-8:]
        gen = DataGenerator(get_engine(), seed, counts, batch_tag=tag)
        with _running_lock:
            stop = _running.get(batch_id)
        gen.run(batch_id, stop)
    finally:
        with _running_lock:
            _running.pop(batch_id, None)


@app.get("/api/batches", response_model=list[BatchOut])
def list_batches(limit: int = 50) -> list[BatchOut]:
    with get_engine().connect() as conn:
        rows = conn.execute(
            batches_t.select().order_by(batches_t.c.created_at.desc()).limit(limit)
        ).mappings().all()
    return [_batch_out(r) for r in rows]


@app.get("/api/batches/{batch_id}", response_model=BatchOut)
def get_batch(batch_id: str) -> BatchOut:
    return _fetch_batch(batch_id)


@app.post("/api/batches/{batch_id}/cancel")
def cancel_batch(batch_id: str) -> dict:
    with _running_lock:
        stop = _running.get(batch_id)
    if not stop:
        raise HTTPException(404, "批次不在运行中（已完成或不存在）")
    stop.set()
    return {"cancelled": batch_id}


@app.delete("/api/batches/{batch_id}", response_model=CleanupOut)
def delete_batch(batch_id: str) -> CleanupOut:
    with _running_lock:
        running = batch_id in _running
    if running:
        raise HTTPException(409, "批次仍在生成中，请先取消或等待完成后再清理")
    result = cleanup_mod.cleanup_batch(get_engine(), batch_id)
    return CleanupOut(**result)


@app.get("/api/batches/{batch_id}/rows/{table_name}")
def batch_rows(batch_id: str, table_name: str, limit: int = 20) -> dict:
    """查看批次在某表插入的样例数据（按登记主键回表）。"""
    import sqlalchemy as sa

    tables = {t.name: t for t in reflect(get_engine())}
    if table_name not in tables:
        raise HTTPException(404, f"未知表名: {table_name}")
    meta = tables[table_name]
    t = sa.table(
        table_name, *[sa.column(c.name) for c in meta.columns]
    )
    with get_engine().connect() as conn:
        registered = conn.execute(
            sa.select(rows_t.c.row_pk)
            .where(
                rows_t.c.batch_id == batch_id,
                rows_t.c.table_name == table_name,
            )
            .limit(limit)
        ).all()
        if not registered:
            raise HTTPException(404, "该批次在此表中没有登记数据")
        conds = [
            sa.and_(*[sa.column(c) == pk[c] for c in meta.pk_columns])
            for (pk,) in registered
        ]
        where = conds[0] if len(conds) == 1 else sa.or_(*conds)
        data = [
            {k: _ser(v) for k, v in r._mapping.items()}
            for r in conn.execute(t.select().where(where).limit(limit))
        ]
        total = conn.execute(
            sa.select(sa.func.count())
            .select_from(rows_t)
            .where(
                rows_t.c.batch_id == batch_id,
                rows_t.c.table_name == table_name,
            )
        ).scalar_one()
    return {"table": table_name, "rows": data, "count_registered": total}


# ---------- 序列化辅助 ----------
def _table_out(t, order: int):
    from .schemas import TableOut, ColumnOut

    return TableOut(
        name=t.name,
        columns=[ColumnOut(**c.__dict__) for c in t.columns],
        pk_columns=t.pk_columns,
        unique_columns=t.unique_columns,
        fk_targets=t.fk_targets,
        generation_order=order,
    )


def _fetch_batch(batch_id: str) -> BatchOut:
    with get_engine().connect() as conn:
        row = conn.execute(
            batches_t.select().where(batches_t.c.id == batch_id)
        ).mappings().first()
    if not row:
        raise HTTPException(404, f"批次不存在: {batch_id}")
    return _batch_out(row)


def _batch_out(row) -> BatchOut:
    d = dict(row)
    return BatchOut(
        id=d["id"],
        seed=d["seed"],
        params=d["params"],
        status=d["status"],
        progress=d["progress"],
        inserted_rows=d["inserted_rows"],
        constraint_checks=d.get("constraint_checks"),
        error=d.get("error"),
        created_at=d["created_at"].isoformat() if d.get("created_at") else None,
        finished_at=d["finished_at"].isoformat() if d.get("finished_at") else None,
    )


def _ser(v: Any) -> Any:
    import uuid
    from datetime import date
    from decimal import Decimal

    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, uuid.UUID):
        return str(v)
    return v
