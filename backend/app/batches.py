"""Batch listing, detail and batch-scoped cleanup.

Cleanup safety
--------------
DELETE statements are driven by the PKs registered in
``synth_batch_rows`` for *that specific batch only*.  Rows are deleted in
reverse topological order (children before parents).  Any pre-existing
business record (created by the demo seed script, another batch, or manual
SQL) has no registration entry and can therefore never be deleted.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from .config import settings
from .db import engine, synth_batch_rows, synth_batches
from .generator import GenerationError
from .introspect import introspect, topo_order


def list_batches() -> list[dict[str, Any]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT b.id, b.batch_no, b.seed, b.status,
                       b.table_counts, b.note,
                       b.created_at,
                       (SELECT count(*) FROM {settings.workbench_schema}.synth_batch_rows r
                        WHERE r.batch_id = b.id) AS row_registrations
                FROM {settings.workbench_schema}.synth_batches b
                ORDER BY b.id DESC
                """
            )
        ).mappings().all()

    result = []
    for r in rows:
        counts = json.loads(r["table_counts"])
        result.append(
            {
                "id": r["id"],
                "batch_no": r["batch_no"],
                "seed": r["seed"],
                "status": r["status"],
                "table_counts": counts,
                "note": r["note"],
                "created_at": r["created_at"].isoformat(sep=" ", timespec="seconds"),
                "row_registrations": r["row_registrations"],
                "total_planned": sum(counts.values()),
            }
        )
    return result


def batch_detail(batch_no: str) -> dict[str, Any] | None:
    with engine.connect() as conn:
        batch = conn.execute(
            synth_batches.select().where(synth_batches.c.batch_no == batch_no)
        ).mappings().first()
        if batch is None:
            return None

        regs = conn.execute(
            synth_batch_rows.select()
            .where(synth_batch_rows.c.batch_id == batch["id"])
            .order_by(synth_batch_rows.c.id)
        ).mappings().all()

        # live counts per table (proves rows still exist)
        live_counts: dict[str, int] = {}
        sample_rows: dict[str, list[dict[str, Any]]] = {}
        by_table: dict[str, list[dict[str, Any]]] = {}
        for r in regs:
            by_table.setdefault(r["table_name"], []).append(dict(r))

        for table_name, items in by_table.items():
            pk_cols = json.loads(items[0]["pk_cols"])
            pk_values = [json.loads(i["pk_values"]) for i in items]
            live_counts[table_name] = len(pk_values)
            # fetch up to 10 rows that still exist with their full content
            existing = _fetch_registered_rows(
                conn, table_name, pk_cols, pk_values[: settings.sample_size]
            )
            if existing:
                sample_rows[table_name] = existing

        return {
            "id": batch["id"],
            "batch_no": batch["batch_no"],
            "seed": batch["seed"],
            "status": batch["status"],
            "table_counts": json.loads(batch["table_counts"]),
            "note": batch["note"],
            "created_at": batch["created_at"].isoformat(sep=" ", timespec="seconds"),
            "registered_per_table": live_counts,
            "samples": sample_rows,
        }


def _fetch_registered_rows(
    conn, table_name: str, pk_cols: list[str], pk_values: list[list[Any]]
) -> list[dict[str, Any]]:
    if not pk_values:
        return []
    # single-column PK fast path
    if len(pk_cols) == 1:
        col = pk_cols[0]
        stmt = text(
            f'SELECT * FROM "{settings.business_schema}"."{table_name}" '
            f'WHERE "{col}" = ANY(:vals)'
        )
        rows = conn.execute(stmt, {"vals": [v[0] for v in pk_values]}).mappings()
        return [dict(r) for r in rows]
    # composite PK fallback: OR clauses
    clauses = " OR ".join(
        " AND ".join(f'"{c}" = :v{i}_{j}' for j, c in enumerate(pk_cols))
        for i in range(len(pk_values))
    )
    params: dict[str, Any] = {}
    for i, vals in enumerate(pk_values):
        for j, v in enumerate(vals):
            params[f"v{i}_{j}"] = v
    stmt = text(
        f'SELECT * FROM "{settings.business_schema}"."{table_name}" WHERE {clauses}'
    )
    return [dict(r) for r in conn.execute(stmt, params).mappings()]


def cleanup_batch(batch_no: str) -> dict[str, Any]:
    """Delete exactly the rows of one batch, children first."""
    tables = introspect(engine)
    tables_by_name = {t["name"]: t for t in tables}
    try:
        order = topo_order(tables)
    except ValueError as exc:
        raise GenerationError(str(exc))
    delete_order = list(reversed(order))

    with engine.begin() as conn:
        batch = conn.execute(
            synth_batches.select().where(synth_batches.c.batch_no == batch_no)
        ).mappings().first()
        if batch is None:
            raise GenerationError(f"批次 {batch_no} 不存在")
        if batch["status"] != "committed":
            raise GenerationError(
                f"批次状态为 {batch['status']}，仅已提交批次可以清理"
            )

        regs = conn.execute(
            synth_batch_rows.select().where(
                synth_batch_rows.c.batch_id == batch["id"]
            )
        ).mappings().all()

        by_table: dict[str, list[dict[str, Any]]] = {}
        for r in regs:
            by_table.setdefault(r["table_name"], []).append(dict(r))

        deleted: dict[str, int] = {}
        skipped_missing: dict[str, int] = {}

        for table_name in delete_order:
            items = by_table.get(table_name)
            if not items:
                continue
            table = tables_by_name[table_name]
            pk_cols = json.loads(items[0]["pk_cols"])
            pk_values = [tuple(json.loads(i["pk_values"])) for i in items]

            if len(pk_cols) == 1:
                col = pk_cols[0]
                # check which still exist
                existing = {
                    row[0]
                    for row in conn.execute(
                        text(
                            f'SELECT "{col}" FROM '
                            f'"{settings.business_schema}"."{table_name}" '
                            f'WHERE "{col}" = ANY(:vals)'
                        ),
                        {"vals": [v[0] for v in pk_values]},
                    )
                }
                to_delete = [v[0] for v in pk_values if v[0] in existing]
                skipped_missing[table_name] = len(pk_values) - len(to_delete)
                if not to_delete:
                    deleted[table_name] = 0
                    continue
                try:
                    result = conn.execute(
                        text(
                            f'DELETE FROM "{settings.business_schema}"."{table_name}" '
                            f'WHERE "{col}" = ANY(:vals)'
                        ),
                        {"vals": to_delete},
                    )
                    deleted[table_name] = result.rowcount
                except IntegrityError as exc:
                    _raise_cleanup_conflict(exc, table)
            else:
                deleted[table_name] = 0
                for vals in pk_values:
                    clause = " AND ".join(
                        f'"{c}" = :v{i}' for i, c in enumerate(pk_cols)
                    )
                    params = {f"v{i}": v for i, v in enumerate(vals)}
                    try:
                        result = conn.execute(
                            text(
                                f'DELETE FROM "{settings.business_schema}"."{table_name}" '
                                f"WHERE {clause}"
                            ),
                            params,
                        )
                        deleted[table_name] += result.rowcount
                    except IntegrityError as exc:
                        _raise_cleanup_conflict(exc, table)

        # only after business rows are gone, purge registrations + batch row
        conn.execute(
            synth_batch_rows.delete().where(
                synth_batch_rows.c.batch_id == batch["id"]
            )
        )
        conn.execute(
            synth_batches.delete().where(synth_batches.c.id == batch["id"])
        )

    return {
        "batch_no": batch_no,
        "deleted": deleted,
        "registrations_removed": len(regs),
        "skipped_already_missing": skipped_missing,
    }


def _raise_cleanup_conflict(exc: IntegrityError, table: dict[str, Any]) -> None:
    orig = getattr(exc, "orig", exc)
    diag = getattr(orig, "diag", None)
    con_name = getattr(diag, "constraint_name", "") or ""
    other = None
    for fk in table["foreign_keys"]:
        if fk["name"] == con_name:
            other = fk["foreign_table"]
    raise GenerationError(
        f"清理被约束 {con_name or '未知'} 阻止：表 {table['name']} 的批次记录仍被"
        f" {other or '其他表'} 引用，请先清理引用方批次",
        {
            "conflict": {
                "kind": "fk",
                "table": table["name"],
                "constraint": con_name,
                "other_table": other,
                "detail": str(orig).split(chr(10))[0],
            }
        },
    )
