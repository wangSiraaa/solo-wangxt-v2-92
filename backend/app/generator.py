"""确定性合成数据生成器。

同一个 (seed, 表集合, 每表行数) 产出相同的数据：Faker 与 random 都由
seed 固定，按父表 -> 子表的拓扑顺序逐表、逐行调用。批内唯一性由每表
行号 + 已用集合保证；跨批次/既有数据的冲突交由数据库约束裁决，错误中
带出涉及的表与约束名。
"""
from __future__ import annotations

import random
import threading
import traceback
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from faker import Faker
from sqlalchemy import MetaData as SAMetaData, insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine

from .config import MAX_ROWS_PER_TABLE
from .models import batches_t, rows_t
from .reflection import ColumnMeta, TableMeta, reflect, topo_order

CHUNK = 200

SQLSTATE_LABELS = {
    "23505": "unique_violation",
    "23503": "foreign_key_violation",
    "23502": "not_null_violation",
    "23514": "check_violation",
    "23p01": "exclusion_violation",
}


class GenerationError(Exception):
    def __init__(self, payload: dict[str, Any]):
        super().__init__(payload.get("detail", "generation failed"))
        self.payload = payload


def _constraint_payload(exc: Exception, current_table: str | None) -> dict[str, Any]:
    orig = getattr(exc, "orig", exc)
    diag = getattr(orig, "diag", None)
    sqlstate = getattr(orig, "sqlstate", None)
    return {
        "type": SQLSTATE_LABELS.get(sqlstate or "", "constraint_violation"),
        "sqlstate": sqlstate,
        "table": getattr(diag, "table_name", None) or current_table,
        "constraint": getattr(diag, "constraint_name", None),
        "detail": getattr(diag, "message_primary", None) or str(orig),
    }


class DataGenerator:
    def __init__(self, engine: Engine, seed: int, counts: dict[str, int],
                 batch_tag: str = ""):
        self.engine = engine
        self.seed = seed
        self.counts = counts
        # 批次短标识：唯一列追加此后缀。同一批次内相同种子 => 相同数据；
        # 不同批次 => 后缀不同，避免与既有/上批次数据的唯一约束冲突
        self.tag = (batch_tag or "")[:12]
        self.fake = Faker("zh_CN")
        self.fake.seed_instance(seed)
        self.rnd = random.Random(seed)
        # table -> col -> 批内已用值
        self._used: dict[str, dict[str, set[Any]]] = {}
        # table -> 已插入父表行的实际主键，供外键列选用
        self.pk_pool: dict[str, list[dict[str, Any]]] = {}

        self.metas = reflect(engine)
        self.order = topo_order(self.metas)
        self.by_meta = {t.name: t for t in self.metas}

        # 反射真实 Table 对象用于插入（类型 / RETURNING / insertmanyvalues）
        md = SAMetaData()
        allowed = [t.name for t in self.metas]
        md.reflect(bind=engine, only=allowed, views=False)
        self.tables = md.tables
        self.samples: dict[str, list[dict[str, Any]]] = {}

    # ---------- 进度 ----------
    @staticmethod
    def _write_progress(conn, batch_id: str, progress: dict) -> None:
        conn.execute(
            batches_t.update().where(batches_t.c.id == batch_id).values(progress=progress)
        )

    # ---------- 值生成 ----------
    def _tagged(self, value: Any) -> Any:
        """唯一列固定挂批次后缀：同批同种子数据一致，跨批次不撞车。"""
        if not self.tag:
            return value
        s = str(value)
        base, at, domain = s.partition("@")
        return f"{base}-{self.tag}@{domain}" if at else f"{base}-{self.tag}"

    def _uniquify(self, table: str, col: ColumnMeta, value: Any) -> Any:
        value = self._tagged(value)
        bucket = self._used.setdefault(table, {}).setdefault(col.name, set())
        n = 0
        candidate = value
        base, at, domain = str(value).partition("@")
        while candidate in bucket:
            n += 1
            candidate = (
                f"{base}-{n:03d}@{domain}" if at else f"{base}-{n:03d}"
            )
        bucket.add(candidate)
        return candidate

    def _parent_pool(self, table: TableMeta, parent: str) -> list[dict[str, Any]]:
        """父表候选主键：本批次生成优先；未生成则从库内既有行加载（按主键排序）。"""
        if parent in self.pk_pool:
            return self.pk_pool[parent]
        pmeta = self.by_meta[parent]
        pt = self.tables[parent]
        pk_cols = [pt.c[c] for c in pmeta.pk_columns]
        with self.engine.connect() as conn:
            rows = [dict(r) for r in conn.execute(
                sa.select(*pk_cols).order_by(*pk_cols)
            ).mappings().all()]
        self.pk_pool[parent] = rows
        return rows

    def _fk_value(self, table: TableMeta, col: ColumnMeta) -> Any:
        parent, pcol = col.references.split(".")
        pool = self._parent_pool(table, parent)
        if not pool:
            raise GenerationError({
                "type": "foreign_key_violation",
                "table": table.name,
                "constraint": f"fk_{table.name}_{col.name}",
                "detail": (
                    f"表 {table.name}.{col.name} 外键指向 {parent}.{pcol}，"
                    f"但父表 {parent} 中没有任何可引用的行（本批次与库内均为空）"
                ),
            })
        return self.rnd.choice(pool)[pcol]

    def _value_for(self, table: TableMeta, col: ColumnMeta, row_idx: int) -> Any:
        if col.references:
            return self._fk_value(table, col)

        name = col.name.lower()
        f = self.fake
        unique = col.name in table.unique_columns

        if "email" in name:
            value = f"{f.user_name()}{row_idx}@example.test"
            return self._uniquify(table.name, col, value) if unique else value
        if name in ("name", "full_name", "customer_name", "username"):
            value = f.name()  # zh_CN 姓名（非真实姓名）
            return self._uniquify(table.name, col, value) if unique else value
        if "phone" in name or "mobile" in name or "tel" in name:
            value = f"1{f.numerify(text='##########')}"
            return self._uniquify(table.name, col, value) if unique else value
        if "company" in name:
            return f.company()
        if "city" in name:
            return f.city()
        if "address" in name or "addr" in name:
            return f.address().replace("\n", " ")
        if "status" in name:
            return f.random_element(["pending", "paid", "shipped", "cancelled"])
        if name.endswith("_no") or name in ("no", "code") or name.endswith("_code") \
                or name.endswith("number") or "order_no" in name:
            prefix = "".join(ch for ch in table.name.upper() if ch.isalpha())[:3] or "GEN"
            value = f"{prefix}-{row_idx + 1:06d}"
            return self._uniquify(table.name, col, value) if unique else value
        if any(k in name for k in ("amount", "price", "total", "cost")):
            return round(f.pyfloat(positive=True, min_value=10, max_value=999), 2)

        t = col.type
        if t in {"BIGINT", "INTEGER", "SMALLINT", "BIGSERIAL", "SERIAL"}:
            if unique:
                used = self._used.setdefault(table.name, {}).setdefault(col.name, set())
                # 低位放批内序号，高位混入种子与批次标识保证跨批次不冲突
                tag_num = sum(self.tag.encode()) % 1000 if self.tag else 0
                v = ((self.seed % 100000) * 1000 + tag_num) * 100000 + row_idx
                used.add(v)
                return v
            return f.random_int(min=1, max=10**6)
        if t in {"NUMERIC", "DECIMAL"}:
            return round(f.pyfloat(positive=True, min_value=1, max_value=1000), 2)
        if t in {"DOUBLE_PRECISION", "REAL", "FLOAT"}:
            return f.pyfloat(positive=True, min_value=1, max_value=1000)
        if t in {"BOOLEAN", "BOOL"}:
            return f.boolean()
        if t == "DATE":
            return f.date_between(start_date="-2y", end_date="today")
        if "TIMESTAMP" in t:
            return f.date_time_between(start_date="-2y", end_date="now")
        if t == "TIME":
            return f.time()
        if t in {"JSON", "JSONB"}:
            return {"synth": True, "seed": self.seed, "n": row_idx}
        if "UUID" in t:
            value = str(uuid.UUID(int=self.rnd.getrandbits(128), version=4))
            return self._uniquify(table.name, col, value) if unique else value

        # VARCHAR / TEXT / CHAR 及其他：简短备注式文本
        value = f.sentence(nb_words=8).rstrip("。.")
        if unique:
            value = self._uniquify(table.name, col, f"{value}-{row_idx}")
        return value

    def _row(self, table: TableMeta, row_idx: int) -> dict[str, Any]:
        row: dict[str, Any] = {}
        for col in table.columns:
            if col.db_generated:
                continue  # SERIAL / IDENTITY / server_default 交给数据库
            if not col.nullable:
                row[col.name] = self._value_for(table, col, row_idx)
            elif self.rnd.random() < 0.85:
                row[col.name] = self._value_for(table, col, row_idx)
            else:
                row[col.name] = None
        return row

    # ---------- 主流程 ----------
    def run(self, batch_id: str, stop: threading.Event | None = None) -> dict:
        totals = {
            t.name: min(int(self.counts.get(t.name, 0)), MAX_ROWS_PER_TABLE)
            for t in self.order
        }
        grand_total = sum(totals.values()) or 1
        done = 0
        progress: dict[str, Any] = {
            "current_table": None,
            "tables": {
                t.name: {"inserted": 0, "total": totals[t.name]} for t in self.order
            },
            "percent": 0,
            "samples": {},
        }
        try:
            for table in self.order:
                want = totals[table.name]
                if want == 0:
                    continue
                progress["current_table"] = table.name
                self.pk_pool.setdefault(table.name, [])
                self._insert_table(batch_id, table, want, progress, done, grand_total, stop)
                done += want
                progress["percent"] = round(done * 100 / grand_total, 1)

            checks = self._constraint_checks()
            with self.engine.begin() as conn:
                conn.execute(
                    batches_t.update()
                    .where(batches_t.c.id == batch_id)
                    .values(
                        status="done",
                        progress=progress,
                        inserted_rows=done,
                        constraint_checks=checks,
                        finished_at=datetime.utcnow(),
                    )
                )
            return {"status": "done", "checks": checks, "samples": self.samples}
        except Exception as exc:  # noqa: BLE001 - 统一记录到批次
            if isinstance(exc, GenerationError):
                payload = exc.payload
            elif getattr(getattr(exc, "orig", None), "sqlstate", None):
                payload = _constraint_payload(exc, progress.get("current_table"))
            else:
                payload = {
                    "type": "error",
                    "table": progress.get("current_table"),
                    "constraint": None,
                    "detail": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(limit=6),
                }
            with self.engine.begin() as conn:
                conn.execute(
                    batches_t.update()
                    .where(batches_t.c.id == batch_id)
                    .values(
                        status="failed",
                        progress=progress,
                        inserted_rows=done,
                        error=payload,
                        finished_at=datetime.utcnow(),
                    )
                )
            return {"status": "failed", "error": payload}

    def _insert_table(self, batch_id, table: TableMeta, want: int,
                      progress: dict, done: int, grand_total: int, stop) -> None:
        rows = [self._row(table, i) for i in range(want)]
        table_obj = self.tables[table.name]
        pk_cols = table.pk_columns
        inserted_pks: list[dict[str, Any]] = []

        for start in range(0, len(rows), CHUNK):
            chunk = rows[start:start + CHUNK]
            try:
                with self.engine.begin() as conn:
                    if pk_cols:
                        stmt = pg_insert(table_obj).returning(
                            *[table_obj.c[c] for c in pk_cols]
                        )
                        returned = [dict(r) for r in
                                    conn.execute(stmt, chunk).mappings().all()]
                    else:
                        conn.execute(insert(table_obj), chunk)
                        returned = []
                    if returned:
                        # 与业务数据在同一事务登记，保证清理时只删本批次行
                        conn.execute(
                            rows_t.insert(),
                            [
                                {"batch_id": batch_id, "table_name": table.name,
                                 "row_pk": _jsonable(pk)}
                                for pk in returned
                            ],
                        )
                        inserted_pks.extend(returned)

                    if table.name not in self.samples:
                        # 用 RETURNING 回来的真实主键补齐样例行，便于展示自增 id
                        if returned and len(returned) == len(chunk):
                            sample = []
                            for r, pk in zip(chunk, returned):
                                sample.append({**_jsonable(r), **_jsonable(pk)})
                        else:
                            sample = [_jsonable(r) for r in chunk[:5]]
                        self.samples[table.name] = sample[:5]

                    n_now = progress["tables"][table.name]["inserted"] + len(chunk)
                    progress["tables"][table.name]["inserted"] = n_now
                    progress["percent"] = round((done + n_now) * 100 / grand_total, 1)
                    progress["samples"] = self.samples
                    self._write_progress(conn, batch_id, progress)
            except Exception as exc:  # noqa: BLE001
                if getattr(getattr(exc, "orig", None), "sqlstate", None):
                    raise GenerationError(_constraint_payload(exc, table.name))
                raise
            if stop and stop.is_set():
                break

        # 数据库实际主键进入外键候选池（SERIAL/IDENTITY 必须用真实值）
        if inserted_pks:
            self.pk_pool[table.name] = inserted_pks
        elif pk_cols:
            self.pk_pool[table.name] = [
                {c: r.get(c) for c in pk_cols} for r in rows
            ]

    # ---------- 约束检查（插后复查，信息以数据库强制结果为准） ----------
    def _constraint_checks(self) -> list[dict]:
        checks: list[dict] = []
        for table in self.order:
            t = self.tables[table.name]
            # NOT NULL：插入成功即满足；列出数据库约束名
            for col in table.columns:
                if col.nullable or col.db_generated:
                    continue
                checks.append({
                    "table": table.name,
                    "constraint": f"{table.name}_{col.name}_not_null",
                    "type": "not_null",
                    "status": "ok",
                    "detail": f"非空列 {col.name} 全部有值（数据库约束强制）",
                })

            # 唯一：整表查重（插入已由数据库担保，此处为可见的复查结果）
            for col_name in table.unique_columns:
                col = t.c[col_name]
                dup_sub = (
                    sa.select(col.label("v"))
                    .group_by(col)
                    .having(sa.func.count() > 1)
                    .subquery()
                )
                with self.engine.connect() as conn:
                    dup = conn.execute(
                        sa.select(sa.func.count())
                        .select_from(t)
                        .where(col.in_(sa.select(dup_sub.c.v)))
                    ).scalar_one()
                checks.append({
                    "table": table.name,
                    "constraint": f"{table.name}_{col_name}_key",
                    "type": "unique",
                    "status": "violated" if dup else "ok",
                    "detail": (
                        f"唯一列 {col_name} 存在 {dup} 行重复取值"
                        if dup else f"唯一列 {col_name} 无重复取值"
                    ),
                    "violated_rows": dup,
                })

            # 外键
            for col in table.columns:
                if not col.references:
                    continue
                parent, pcol = col.references.split(".")
                checks.append({
                    "table": table.name,
                    "constraint": f"{table.name}_{col.name}_fkey",
                    "type": "foreign_key",
                    "status": "ok",
                    "detail": f"{col.name} -> {parent}.{pcol} 全部有效（数据库约束强制）",
                })
        return checks


def _jsonable(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        if isinstance(v, (datetime, date)):
            v = v.isoformat()
        elif isinstance(v, Decimal):
            v = float(v)
        elif isinstance(v, uuid.UUID):
            v = str(v)
        out[k] = v
    return out
