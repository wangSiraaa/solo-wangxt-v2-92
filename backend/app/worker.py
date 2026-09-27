"""Batch generation worker.

Flow
----
1. introspect business tables and topologically sort them (parents first);
2. preflight: counts allowed, parent rows available for every NOT NULL FK;
3. for each table, in order:
     - preload existing values of unique column groups;
     - generate rows with the seeded Faker/RNG;
     - deterministic unique columns (email/code/phone) collide immediately
       when the same seed is re-run -> structured constraint conflict error;
     - other unique columns retry with fresh Faker values;
     - insert via executemany inside one transaction;
     - register every inserted PK in ``synth_batch_rows``;
4. dry-run rolls the transaction back (check + sample only, nothing stored).
"""
from __future__ import annotations

import json
import secrets
import threading
import time
import traceback
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from .config import settings
from .db import engine, synth_batch_rows, synth_batches
from .generator import GenerationError, ValueGenerator
from .introspect import introspect, topo_order

# In-memory job registry (single-process workbench).  Key: job_id.
_JOBS: dict[str, dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_job(job_id: str) -> dict[str, Any] | None:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        return dict(job) if job else None


def list_jobs(limit: int = 20) -> list[dict[str, Any]]:
    with _JOBS_LOCK:
        jobs = [dict(j) for j in _JOBS.values()]
    jobs.sort(key=lambda j: j["started_at"], reverse=True)
    return jobs[:limit]


def _serialize(v: Any) -> Any:
    if isinstance(v, (datetime,)):
        return v.isoformat(sep=" ", timespec="seconds")
    if hasattr(v, "isoformat"):  # date / Decimal fallback
        return str(v)
    return v


def _load_parent_pools(
    conn, tables_by_name: dict[str, dict[str, Any]], order: list[str]
) -> dict[str, list[tuple[Any, ...]]]:
    """Existing PK tuples per table (pre-existing rows + earlier tables)."""
    pools: dict[str, list[tuple[Any, ...]]] = {}
    for name in order:
        table = tables_by_name[name]
        pk = table["primary_key"]
        if not pk:
            pools[name] = []
            continue
        cols = ", ".join(f'"{c}"' for c in pk)
        rows = conn.execute(
            text(
                f'SELECT {cols} FROM "{settings.business_schema}"."{name}"'
            )
        ).fetchall()
        pools[name] = [tuple(r) for r in rows]
    return pools


def _load_unique_values(
    conn, table_name: str, group_cols: list[str]
) -> set[tuple[Any, ...]]:
    cols = ", ".join(f'"{c}"' for c in group_cols)
    rows = conn.execute(
        text(
            f"SELECT DISTINCT {cols} FROM "
            f'"{settings.business_schema}"."{table_name}"'
        )
    ).fetchall()
    return {tuple(r) for r in rows}


def _column_meta(table: dict[str, Any], col_name: str) -> dict[str, Any]:
    return next(c for c in table["columns"] if c["name"] == col_name)


# Columns whose unique value is rendered deterministically from (seed, seq),
# so re-running the same seed reproduces the exact value and surfaces a
# natural unique-conflict demonstration.
_DETERMINISTIC_HINTS = ("email", "code", "_no", "number", "phone", "mobile")


def _is_deterministic_col(col_name: str) -> bool:
    n = col_name.lower()
    return any(h in n for h in _DETERMINISTIC_HINTS)


def _generate_row(
    *,
    gen: ValueGenerator,
    table: dict[str, Any],
    seq: int,
    pools: dict[str, list[tuple[Any, ...]]],
    unique_used: dict[int, set[tuple[Any, ...]]],
) -> dict[str, Any] | None:
    """Build one row.

    Returns None when a *deterministic* unique column (email/code/phone...)
    collides with an existing value: such values cannot be varied, so the
    worker aborts with a structured unique-conflict report.
    """
    fk_by_col: dict[str, dict[str, Any]] = {}
    for fk in table["foreign_keys"]:
        for col in fk["columns"]:
            fk_by_col[col] = fk

    # pick one parent tuple per distinct FK constraint (composite safe)
    chosen_fk_tuples: dict[str, tuple[Any, ...]] = {}
    for fk in table["foreign_keys"]:
        chosen_fk_tuples[fk["name"]] = gen.pick_parent(
            pools.get(fk["foreign_table"], [])
        )

    # unique groups other than the plain primary-key group
    uniq_groups = [
        (idx, g)
        for idx, g in enumerate(table["unique_groups"])
        if not (
            g["is_primary"]
            and set(g["columns"]) == set(table["primary_key"])
        )
    ]
    single_groups: dict[str, list[int]] = {}
    for idx, g in uniq_groups:
        if len(g["columns"]) == 1:
            single_groups.setdefault(g["columns"][0], []).append(idx)

    row: dict[str, Any] = {}
    for col_meta in table["columns"]:
        col = col_meta["name"]
        if col_meta["is_auto"]:
            continue

        if col in fk_by_col:
            fk = fk_by_col[col]
            parent_tuple = chosen_fk_tuples[fk["name"]]
            row[col] = parent_tuple[fk["columns"].index(col)]
            continue

        rules = table["check_rules"].get(col)
        deterministic = col in single_groups and _is_deterministic_col(col)

        value = gen.value_for(
            table=table["name"],
            column=col_meta,
            check_rules=rules,
            unique_deterministic=deterministic,
            unique_seq=seq,
        )

        if col in single_groups:
            for gidx in single_groups[col]:
                if (value,) in unique_used.setdefault(gidx, set()):
                    if deterministic:
                        return None
                    # non-deterministic unique: the Faker unique provider
                    # already avoids in-process repeats; against pre-existing
                    # rows, try fresh values a bounded number of times.
                    value = _retry_unique_scalar(
                        gen, table, col_meta, rules, unique_used[gidx]
                    )

        row[col] = value

    # register every unique key produced by this row
    for idx, g in uniq_groups:
        key = tuple(row[c] for c in g["columns"] if c in row)
        if len(key) == len(g["columns"]):
            unique_used.setdefault(idx, set()).add(key)

    return row


def _retry_unique_scalar(
    gen: ValueGenerator,
    table: dict[str, Any],
    col_meta: dict[str, Any],
    rules: list[dict[str, Any]] | None,
    used: set[tuple[Any, ...]],
) -> Any:
    for _ in range(50):
        candidate = gen.value_for(
            table=table["name"],
            column=col_meta,
            check_rules=rules,
            unique_deterministic=False,
        )
        if (candidate,) not in used:
            return candidate
    raise GenerationError(
        f"唯一列 {col_meta['name']} 取值空间不足，重试 50 次仍冲突"
    )


def run_generation(
    *,
    seed: int,
    counts: dict[str, int],
    dry_run: bool,
    note: str = "",
) -> str:
    """Create a job, start its background thread and return job_id."""
    job_id = f"job-{int(time.time() * 1000)}-{threading.get_ident() % 100000:05d}"
    job = {
        "job_id": job_id,
        "seed": seed,
        "requested_counts": counts,
        "dry_run": dry_run,
        "status": "running",
        "phase": "introspect",
        "percent": 0,
        "order": [],
        "generated": {},
        "samples": {},
        "conflict": None,
        "error": None,
        "traceback": None,
        "batch_no": None,
        "started_at": _now(),
        "finished_at": None,
        "note": note,
    }
    with _JOBS_LOCK:
        _JOBS[job_id] = job

    thread = threading.Thread(
        target=_run_safe, args=(job_id, seed, counts, dry_run, note), daemon=True
    )
    thread.start()
    return job_id


def _run_safe(
    job_id: str,
    seed: int,
    counts: dict[str, int],
    dry_run: bool,
    note: str,
) -> None:
    try:
        _run(job_id, seed, counts, dry_run, note)
    except Exception as exc:  # noqa: BLE001 - surfaced to UI as structured info
        with _JOBS_LOCK:
            job = _JOBS[job_id]
            if isinstance(exc, GenerationError):
                job["status"] = "failed"
                job["error"] = exc.message
                if exc.details.get("conflict"):
                    job["conflict"] = exc.details["conflict"]
            else:
                job["status"] = "failed"
                job["error"] = f"{type(exc).__name__}: {exc}"
            job["traceback"] = traceback.format_exc(limit=6)
            job["finished_at"] = _now()


def _set_progress(job_id: str, **kw: Any) -> None:
    with _JOBS_LOCK:
        _JOBS[job_id].update(kw)


def _conflict_payload(
    *,
    table: dict[str, Any] | None,
    constraint_name: str,
    kind: str,
    columns: list[str],
    values: list[Any],
    detail: str,
    other_table: str | None = None,
) -> dict[str, Any]:
    return {
        "kind": kind,  # unique | fk | not_null | check
        "table": table["name"] if table else None,
        "table_schema": settings.business_schema,
        "constraint": constraint_name,
        "columns": columns,
        "values": [_serialize(v) for v in values],
        "other_table": other_table,
        "detail": detail,
    }


def _run(
    job_id: str,
    seed: int,
    counts: dict[str, int],
    dry_run: bool,
    note: str,
) -> None:
    tables = introspect(engine)
    tables_by_name = {t["name"]: t for t in tables}

    unknown = [t for t in counts if t not in tables_by_name]
    if unknown:
        raise GenerationError(
            "请求中包含数据库里不存在的表",
            {"unknown_tables": unknown},
        )

    try:
        order = topo_order(tables)
    except ValueError as exc:
        raise GenerationError(str(exc))

    # counts default to zero; restrict to tables actually in the schema
    eff_counts = {name: int(counts.get(name, 0)) for name in order}
    total = sum(eff_counts.values())
    if total <= 0:
        raise GenerationError("请至少为一张表指定大于 0 的记录数量")

    _set_progress(job_id, phase="preflight", order=order, percent=2)

    gen = ValueGenerator(seed=seed, locale=settings.faker_locale)

    batch_no = (
        f"B{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        f"-S{seed}-{secrets.randbelow(1_000_000):06d}"
    )
    if dry_run:
        batch_no = f"DRY-{batch_no}"

    connection = engine.connect()
    tx = connection.begin()
    batch_pk: int | None = None
    done = 0
    generated: dict[str, int] = {}
    samples: dict[str, list[dict[str, Any]]] = {}

    try:
        # register batch row up-front (rolled back for dry-run)
        if not dry_run:
            res = connection.execute(
                synth_batches.insert().values(
                    batch_no=batch_no,
                    seed=seed,
                    status="running",
                    table_counts=json.dumps(eff_counts, ensure_ascii=False),
                    note=note,
                )
            )
            batch_pk = int(res.inserted_primary_key[0])

        pools = _load_parent_pools(connection, tables_by_name, order)

        # ---- preflight: NOT NULL FK needs a non-empty parent pool -------
        for name in order:
            if eff_counts[name] <= 0:
                continue
            table = tables_by_name[name]
            for fk in table["foreign_keys"]:
                if not pools.get(fk["foreign_table"]):
                    child_not_null = all(
                        _column_meta(table, c)["not_null"] for c in fk["columns"]
                    )
                    if child_not_null:
                        raise GenerationError(
                            f"无法生成子表 {name} 的记录：父表 "
                            f"{fk['foreign_table']} 中没有可引用的记录",
                            {
                                "conflict": _conflict_payload(
                                    table=table,
                                    constraint_name=fk["name"],
                                    kind="fk",
                                    columns=fk["columns"],
                                    values=[],
                                    other_table=fk["foreign_table"],
                                    detail=(
                                        f"外键 {fk['name']} 要求 "
                                        f"{name}.{','.join(fk['columns'])} "
                                        f"引用 {fk['foreign_table']}，"
                                        f"但父表无数据（请先生成父表）"
                                    ),
                                )
                            },
                        )

        # ---- generation, parents first ----------------------------------
        for name in order:
            want = eff_counts[name]
            table = tables_by_name[name]
            if want <= 0:
                continue

            _set_progress(job_id, phase=f"generate:{name}")

            # unique groups excluding pure-PK
            uniq_groups = [
                (idx, g)
                for idx, g in enumerate(table["unique_groups"])
                if not (
                    g["is_primary"]
                    and set(g["columns"]) == set(table["primary_key"])
                )
            ]
            unique_used: dict[int, set[tuple[Any, ...]]] = {}
            for idx, g in uniq_groups:
                unique_used[idx] = _load_unique_values(
                    connection, name, g["columns"]
                )

            insert_cols = [
                c for c in table["columns"] if not c["is_auto"]
            ]
            col_names = [c["name"] for c in insert_cols]
            quoted = ", ".join(f'"{c}"' for c in col_names)
            params = ", ".join(f":{c}" for c in col_names)
            insert_sql = text(
                f'INSERT INTO "{settings.business_schema}"."{name}" '
                f"({quoted}) VALUES ({params}) "
                f"RETURNING "
                + ", ".join(f'"{c}"' for c in table["primary_key"])
            )

            table_samples: list[dict[str, Any]] = []
            new_pk_tuples: list[tuple[Any, ...]] = []

            # generate rows in groups; deterministic unique collisions abort
            rows_to_insert: list[dict[str, Any]] = []
            pct = 0
            for i in range(1, want + 1):
                row = _generate_row(
                    gen=gen,
                    table=table,
                    seq=i,
                    pools=pools,
                    unique_used=unique_used,
                )
                if row is None:
                    # deterministic collision -> identify offending column
                    conflict = _find_deterministic_conflict(
                        table=table,
                        seq=i,
                        seed=seed,
                        unique_used=unique_used,
                    )
                    raise GenerationError(
                        conflict["detail"], {"conflict": conflict}
                    )
                rows_to_insert.append(row)
                done += 1
                if total:
                    pct = 2 + int(done / total * 90)
                if i % 25 == 0 or i == want:
                    _set_progress(
                        job_id,
                        percent=max(2, min(92, pct)),
                        generated={**generated, name: i},
                    )

            # insert + collect PKs in executemany-sized chunks
            inserted_so_far = 0
            for start in range(0, len(rows_to_insert), settings.insert_batch_size):
                chunk = rows_to_insert[start : start + settings.insert_batch_size]
                for row in chunk:
                    try:
                        r = connection.execute(insert_sql, row)
                    except Exception as exc:  # DB-level constraint violation
                        conflict = _conflict_from_db_error(
                            exc, table, row, tables_by_name
                        )
                        raise GenerationError(
                            conflict["detail"], {"conflict": conflict}
                        ) from exc
                    pk_tuple = tuple(r.fetchone())
                    new_pk_tuples.append(pk_tuple)
                    inserted_so_far += 1
                    if len(table_samples) < settings.sample_size:
                        sample_row = dict(row)
                        # expose real (possibly serial) PK in samples
                        for pk_col, pk_val in zip(
                            table["primary_key"], pk_tuple
                        ):
                            sample_row.setdefault(pk_col, pk_val)
                        table_samples.append(
                            {
                                k: _serialize(v)
                                for k, v in sample_row.items()
                            }
                        )
                    if batch_pk is not None:
                        connection.execute(
                            synth_batch_rows.insert().values(
                                batch_id=batch_pk,
                                table_schema=settings.business_schema,
                                table_name=name,
                                pk_cols=json.dumps(
                                    table["primary_key"], ensure_ascii=False
                                ),
                                pk_values=json.dumps(
                                    list(pk_tuple), ensure_ascii=False,
                                    default=str
                                ),
                            )
                        )

            generated[name] = want
            samples[name] = table_samples
            pools[name] = pools.get(name, []) + new_pk_tuples
            _set_progress(
                job_id,
                generated=dict(generated),
                samples={k: v for k, v in samples.items()},
                percent=min(92, 2 + int(done / total * 92)) if total else 2,
            )

        # ---- commit / rollback ------------------------------------------
        _set_progress(job_id, phase="commit", percent=96)
        if dry_run:
            tx.rollback()
            final_status = "dry_run"
            batch_no_final: str | None = None
        else:
            connection.execute(
                synth_batches.update()
                .where(synth_batches.c.id == batch_pk)
                .values(status="committed")
            )
            # commit BEFORE exposing the terminal status: otherwise clients
            # could observe "committed" while the transaction is still
            # in-flight and race a follow-up generation.
            tx.commit()
            final_status = "committed"
            batch_no_final = batch_no

        _set_progress(
            job_id,
            status=final_status,
            phase="done",
            percent=100,
            generated=generated,
            samples=samples,
            batch_no=batch_no_final,
            finished_at=_now(),
        )
    except Exception:
        try:
            tx.rollback()
        except Exception:
            pass
        # mark batch failed if it had been registered
        if batch_pk is not None:
            with engine.begin() as conn2:
                conn2.execute(
                    synth_batches.update()
                    .where(synth_batches.c.id == batch_pk)
                    .values(status="failed")
                )
        raise
    finally:
        connection.close()


def _find_deterministic_conflict(
    *,
    table: dict[str, Any],
    seq: int,
    seed: int,
    unique_used: dict[int, set[tuple[Any, ...]]],
) -> dict[str, Any]:
    """Reproduce the deterministic candidate value and locate the colliding
    unique constraint, so the UI can show table + constraint + values."""
    tmp_gen = ValueGenerator(seed=seed, locale=settings.faker_locale)
    # advance the global sequence roughly; deterministic templates do not use
    # _seq, so we can render the candidate directly with seq.
    for idx, g in enumerate(table["unique_groups"]):
        if g["is_primary"] and set(g["columns"]) == set(table["primary_key"]):
            continue
        used = unique_used.get(idx, set())
        candidate = tuple(
            tmp_gen.value_for(
                table=table["name"],
                column=_column_meta(table, c),
                check_rules=table["check_rules"].get(c),
                unique_deterministic=_is_deterministic_col(c),
                unique_seq=seq,
            ) if _is_deterministic_col(c) else f"<{c}>"
            for c in g["columns"]
        )
        if candidate in used:
            return _conflict_payload(
                table=table,
                constraint_name=g["name"],
                kind="unique",
                columns=g["columns"],
                values=list(candidate),
                detail=(
                    f"表 {table['name']} 违反唯一约束 {g['name']} "
                    f"(列: {', '.join(g['columns'])})；"
                    f"种子 {seed} 生成的值 {list(candidate)} 在数据库中已存在。"
                    f"相同种子会产生相同数据，请更换种子或仅保留一个批次。"
                ),
            )
    return _conflict_payload(
        table=table,
        constraint_name="(unknown unique)",
        kind="unique",
        columns=[],
        values=[],
        detail=f"表 {table['name']} 唯一约束冲突（未能定位具体列）",
    )


def _conflict_from_db_error(
    exc: Exception,
    table: dict[str, Any],
    row: dict[str, Any],
    tables_by_name: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Translate a psycopg2 IntegrityError into a structured conflict."""
    orig = getattr(exc, "orig", exc)
    msg = str(orig)
    constraint = getattr(orig, "diag", None)
    con_name = getattr(constraint, "constraint_name", None) or ""
    detail = msg.split("\n")[0]

    kind = "check"
    columns: list[str] = []
    other_table = None
    if "unique constraint" in msg.lower() or "duplicate key" in msg.lower():
        kind = "unique"
    elif "foreign key constraint" in msg.lower():
        kind = "fk"
    elif "not-null" in msg.lower() or "violates not-null" in msg.lower():
        kind = "not_null"

    # try enrich columns / other table from introspected metadata
    for g in table["unique_groups"]:
        if g["name"] == con_name:
            columns = g["columns"]
    for fk in table["foreign_keys"]:
        if fk["name"] == con_name:
            columns = fk["columns"]
            other_table = fk["foreign_table"]
    if not columns and getattr(constraint, "column_name", None):
        columns = [constraint.column_name]

    values = [row.get(c) for c in columns] if columns else []
    return _conflict_payload(
        table=table,
        constraint_name=con_name or detail[:60],
        kind=kind,
        columns=columns,
        values=values,
        other_table=other_table,
        detail=(
            f"表 {table['name']} 违反约束 {con_name or detail[:60]}：{detail}"
        ),
    )
