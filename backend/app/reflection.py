"""通过 SQLAlchemy 反射读取业务表结构：主键 / 外键 / 非空 / 唯一约束。"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import MetaData as SAMetaData
from sqlalchemy.engine import Engine
from sqlalchemy import UniqueConstraint

from .config import ALLOWED_TABLES, SIDE_PREFIX


@dataclass
class ColumnMeta:
    name: str
    type: str
    nullable: bool
    is_pk: bool
    # 自增 / 有数据库端默认值，由数据库生成，生成器不赋值
    db_generated: bool
    references: str | None  # "other_table.column"


@dataclass
class TableMeta:
    name: str
    columns: list[ColumnMeta]
    pk_columns: list[str]
    # 唯一约束涉及的列集合（含 UNIQUE INDEX）
    unique_columns: list[str]
    fk_targets: list[str] = field(default_factory=list)  # 仅保留在白名单内的依赖


def _is_db_generated(col) -> bool:
    if col.autoincrement and col.primary_key:
        return True
    if col.server_default is not None:
        return True
    if col.identity is not None:
        return True
    expr = col.server_default.arg.text if col.server_default is not None else ""
    # 显式 default 表达式（如 gen_random_uuid()）的列也交给数据库
    if expr.upper().startswith(("NEXTVAL(", "GEN_RANDOM_UUID()", "UUID_GENERATE_V4(")):
        return True
    return False


def reflect(engine: Engine) -> list[TableMeta]:
    md = SAMetaData()
    tables = _allowed_names(engine)
    # reflect 默认带出外键关系
    md.reflect(bind=engine, only=tables, views=False)

    result: list[TableMeta] = []
    for name in tables:
        table = md.tables[name]
        pk = [c.name for c in table.primary_key.columns]

        unique_cols: set[str] = set()
        for con in table.constraints:
            if isinstance(con, UniqueConstraint):
                unique_cols.update(c.name for c in con.columns)
        for idx in table.indexes:
            if idx.unique:
                unique_cols.update(c.name for c in idx.columns)

        cols: list[ColumnMeta] = []
        fk_targets: list[str] = []
        for col in table.columns:
            ref = None
            for fk in col.foreign_keys:
                ref = f"{fk.column.table.name}.{fk.column.name}"
                target = fk.column.table.name
                if target in tables and target != name and target not in fk_targets:
                    fk_targets.append(target)
            cols.append(
                ColumnMeta(
                    name=col.name,
                    type=type(col.type).__name__.upper(),
                    nullable=col.nullable,
                    is_pk=col.name in pk,
                    db_generated=_is_db_generated(col),
                    references=ref,
                )
            )
        result.append(
            TableMeta(
                name=name,
                columns=cols,
                pk_columns=pk,
                unique_columns=sorted(unique_cols),
                fk_targets=fk_targets,
            )
        )
    return result


def _allowed_names(engine: Engine) -> list[str]:
    insp = engine.dialect.get_table_names(engine.connect(), schema="public")
    business = [t for t in insp if not t.startswith(SIDE_PREFIX)]
    if ALLOWED_TABLES:
        return [t for t in ALLOWED_TABLES if t in business]
    return business


def topo_order(tables: list[TableMeta]) -> list[TableMeta]:
    """父表先于子表（被引用的表在前）。出现环则报错。"""
    by_name = {t.name: t for t in tables}
    ordered: list[TableMeta] = []
    visited: set[str] = set()
    visiting: set[str] = set()

    def visit(t: TableMeta, path: list[str]) -> None:
        if t.name in visited:
            return
        if t.name in visiting:
            chain = " -> ".join(path + [t.name])
            raise ValueError(f"外键依赖存在环，无法排序: {chain}")
        visiting.add(t.name)
        for dep in t.fk_targets:
            visit(by_name[dep], path + [t.name])
        visiting.discard(t.name)
        visited.add(t.name)
        ordered.append(t)

    for t in tables:
        visit(t, [])
    return ordered
