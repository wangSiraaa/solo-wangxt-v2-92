"""Read table structure from the live PostgreSQL database.

Captured metadata per table:
  * columns        - name, type, nullable, default, length/precision, enum values
  * primary key    - ordered PK column list
  * foreign keys   - constrained columns -> parent table / parent columns
  * unique groups  - UNIQUE constraints and unique indexes (column lists)
  * check rules    - parsed NOT NULL / allowed-value / numeric range CHECKs
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .config import settings

_COLUMNS_SQL = text(
    """
    SELECT
        a.attname                              AS name,
        a.attnum                               AS ordinal,
        pg_catalog.format_type(a.atttypid, a.atttypmod) AS full_type,
        t.typname                              AS udt_name,
        a.attnotnull                           AS not_null,
        pg_get_expr(d.adbin, d.adrelid)        AS default_expr,
        CASE WHEN a.attidentity IN ('a','d') THEN true ELSE false END AS is_identity,
        information_schema._pg_char_max_length(a.atttypid, a.atttypmod) AS char_len,
        information_schema._pg_numeric_precision(a.atttypid, a.atttypmod) AS num_precision,
        information_schema._pg_numeric_scale(a.atttypid, a.atttypmod) AS num_scale
    FROM pg_attribute a
    JOIN pg_class c      ON c.oid = a.attrelid
    JOIN pg_namespace n  ON n.oid = c.relnamespace
    JOIN pg_type t       ON t.oid = a.atttypid
    LEFT JOIN pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
    WHERE n.nspname = :schema
      AND c.relname = :table
      AND a.attnum > 0
      AND NOT a.attisdropped
    ORDER BY a.attnum
    """
)

_PK_SQL = text(
    """
    SELECT a.attname AS name
    FROM pg_index i
    JOIN pg_class c     ON c.oid = i.indrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY(i.indkey)
    WHERE n.nspname = :schema AND c.relname = :table AND i.indisprimary
    ORDER BY array_position(i.indkey, a.attnum)
    """
)

_FK_SQL = text(
    """
    SELECT
        con.conname AS name,
        (
            SELECT array_agg(att.attname ORDER BY u.ord)
            FROM unnest(con.conkey) WITH ORDINALITY AS u(attnum, ord)
            JOIN pg_attribute att
              ON att.attrelid = con.conrelid AND att.attnum = u.attnum
        ) AS columns,
        fn.nspname AS foreign_schema,
        fc.relname AS foreign_table,
        (
            SELECT array_agg(att.attname ORDER BY u.ord)
            FROM unnest(con.confkey) WITH ORDINALITY AS u(attnum, ord)
            JOIN pg_attribute att
              ON att.attrelid = con.confrelid AND att.attnum = u.attnum
        ) AS foreign_columns
    FROM pg_constraint con
    JOIN pg_class c     ON c.oid = con.conrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_class fc    ON fc.oid = con.confrelid
    JOIN pg_namespace fn ON fn.oid = fc.relnamespace
    WHERE n.nspname = :schema
      AND c.relname = :table
      AND con.contype = 'f'
    """
)

_UNIQUE_SQL = text(
    """
    SELECT
        COALESCE(con.conname, idx.relname) AS name,
        i.indisunique AS is_unique,
        COALESCE(con.contype = 'p', i.indisprimary) AS is_primary,
        (
            SELECT array_agg(att.attname ORDER BY u.ord)
            FROM unnest(i.indkey) WITH ORDINALITY AS u(attnum, ord)
            JOIN pg_attribute att
              ON att.attrelid = i.indrelid AND att.attnum = u.attnum
            WHERE NOT (i.indisunique AND array_length(i.indkey,1) IS NULL)
        ) AS columns
    FROM pg_index i
    JOIN pg_class idx     ON idx.oid = i.indexrelid
    JOIN pg_class c       ON c.oid = i.indrelid
    JOIN pg_namespace n   ON n.oid = c.relnamespace
    LEFT JOIN pg_constraint con
      ON con.conindid = i.indexrelid AND con.contype IN ('u','p')
    WHERE n.nspname = :schema
      AND c.relname = :table
      AND (i.indisunique OR i.indisprimary)
    """
)

_CHECK_SQL = text(
    """
    SELECT con.conname AS name, pg_get_constraintdef(con.oid) AS definition
    FROM pg_constraint con
    JOIN pg_class c     ON c.oid = con.conrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = :schema
      AND c.relname = :table
      AND con.contype = 'c'
      AND NOT con.convalidated = false
    """
)

_ENUM_SQL = text(
    """
    SELECT e.enumlabel AS label
    FROM pg_enum e
    JOIN pg_type t ON t.oid = e.enumtypid
    WHERE t.typname = :udt_name
    ORDER BY e.enumsortorder
    """
)


def _list_tables(conn) -> list[str]:
    rows = conn.execute(
        text(
            """
            SELECT c.relname
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = :schema
              AND c.relkind IN ('r', 'p')
              AND NOT c.relispartition
            ORDER BY c.relname
            """
        ),
        {"schema": settings.business_schema},
    )
    return [r[0] for r in rows]


def _strip_balanced_parens(p: str) -> str:
    """Remove only *one* outer layer of matching parentheses, repeatedly
    while the whole expression is wrapped (handles '((x))')."""
    s = p.strip()
    while s.startswith("(") and s.endswith(")"):
        depth = 0
        wraps_all = True
        for i, ch in enumerate(s):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0 and i != len(s) - 1:
                    wraps_all = False
                    break
        if not wraps_all:
            break
        s = s[1:-1].strip()
    return s


def _split_top_level_and(body: str) -> list[str]:
    """Split on AND at parenthesis depth 0."""
    parts: list[str] = []
    depth = 0
    buf: list[str] = []
    tokens = re.split(r"(\s+AND\s+)", body, flags=re.IGNORECASE)
    for tok in tokens:
        if re.fullmatch(r"\s+AND\s+", tok, flags=re.IGNORECASE):
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(tok)
    parts.append("".join(buf))
    return parts


def _parse_check(definition: str) -> list[dict[str, Any]] | None:
    """Best-effort parse of simple CHECK expressions into generation hints.

    Recognised shapes (PostgreSQL stores variants with casts/parens):
        col IN ('a', 'b')
        (col)::text = ANY (ARRAY['a'::text, ...]::text[])
        (amount > (0)::numeric), col >= 0 ...
    Anything we cannot parse is dropped (raw SQL text is never executed).
    """
    body = definition.strip()
    if body.upper().startswith("CHECK"):
        body = body[5:].strip()
    body = _strip_balanced_parens(body)

    rules: list[dict[str, Any]] = []
    for part in _split_top_level_and(body):
        p = _strip_balanced_parens(part.strip())

        # = ANY (ARRAY[...]::... )
        m = re.match(
            r"\(?([\w]+)\)?(?:::[\w ]+)?\s*=\s*ANY\s*\(.*?ARRAY\[(.*?)\].*\)\s*$",
            p,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if m:
            col, body2 = m.group(1), m.group(2)
            vals = re.findall(r"'((?:[^']|'')*)'", body2)
            rules.append(
                {"column": col, "kind": "allowed_values", "values": vals}
            )
            continue

        m = re.match(
            r"\(?([\w]+)\)?(?:::[\w ]+)?\s+IN\s*\((.*?)\)\s*$",
            p,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if m:
            col, body2 = m.group(1), m.group(2)
            vals = re.findall(r"'((?:[^']|'')*)'", body2)
            if vals:
                rules.append(
                    {"column": col, "kind": "allowed_values", "values": vals}
                )
            continue

        m = re.match(
            r"\(?([\w]+)\)?(?:::[\w ]+)?\s*(>=|>|<=|<)\s*\(?(-?[\d.]+)\)?(?:::[\w ]+)?\s*$",
            p,
        )
        if m:
            col, op, val = m.group(1), m.group(2), float(m.group(3))
            rules.append(
                {"column": col, "kind": "range", "op": op, "value": val}
            )
            continue

    return rules or None


def introspect(engine: Engine) -> list[dict[str, Any]]:
    """Return structured metadata for every business table."""
    tables: list[dict[str, Any]] = []
    with engine.connect() as conn:
        names = _list_tables(conn)
        for name in names:
            params = {"schema": settings.business_schema, "table": name}

            columns: list[dict[str, Any]] = []
            for r in conn.execute(_COLUMNS_SQL, params):
                enum_values = None
                if r.udt_name.startswith("_") is False and r.full_type.startswith(
                    "USER-DEFINED"
                ):
                    enum_rows = conn.execute(
                        _ENUM_SQL, {"udt_name": r.udt_name}
                    ).fetchall()
                    if enum_rows:
                        enum_values = [er[0] for er in enum_rows]
                default_expr = r.default_expr
                auto = bool(r.is_identity) or (
                    default_expr is not None
                    and default_expr.lower().startswith(
                        ("nextval(", "gen_random_uuid(", "uuid_generate_v")
                    )
                )
                columns.append(
                    {
                        "name": r.name,
                        "ordinal": r.ordinal,
                        "full_type": r.full_type,
                        "udt_name": r.udt_name,
                        "not_null": bool(r.not_null),
                        "default_expr": default_expr,
                        "is_auto": auto,
                        "char_len": r.char_len,
                        "num_precision": r.num_precision,
                        "num_scale": r.num_scale,
                        "enum_values": enum_values,
                    }
                )

            pk = [r[0] for r in conn.execute(_PK_SQL, params)]

            foreign_keys = []
            for r in conn.execute(_FK_SQL, params):
                foreign_keys.append(
                    {
                        "name": r.name,
                        "columns": list(r.columns or []),
                        "foreign_schema": r.foreign_schema,
                        "foreign_table": r.foreign_table,
                        "foreign_columns": list(r.foreign_columns or []),
                    }
                )

            unique_groups = []
            for r in conn.execute(_UNIQUE_SQL, params):
                cols = list(r.columns or [])
                if not cols:
                    continue
                unique_groups.append(
                    {
                        "name": r.name,
                        "columns": cols,
                        "is_primary": bool(r.is_primary),
                    }
                )

            check_rules: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for r in conn.execute(_CHECK_SQL, params):
                parsed = _parse_check(r.definition)
                if not parsed:
                    continue
                for rule in parsed:
                    if rule.get("kind") == "raw":
                        continue
                    rule["constraint"] = r.name
                    check_rules[rule["column"]].append(rule)

            tables.append(
                {
                    "schema": settings.business_schema,
                    "name": name,
                    "columns": columns,
                    "primary_key": pk,
                    "foreign_keys": foreign_keys,
                    "unique_groups": unique_groups,
                    "check_rules": dict(check_rules),
                }
            )
    return tables


def build_dependency_graph(
    tables: list[dict[str, Any]],
) -> dict[str, set[str]]:
    """table -> set of parent tables it depends on (FK targets)."""
    names = {t["name"] for t in tables}
    graph: dict[str, set[str]] = {t["name"]: set() for t in tables}
    for t in tables:
        for fk in t["foreign_keys"]:
            parent = fk["foreign_table"]
            if parent in names and parent != t["name"]:
                graph[t["name"]].add(parent)
    return graph


def topo_order(tables: list[dict[str, Any]]) -> list[str]:
    """Kahn's algorithm: parents before children. Raises ValueError on cycle."""
    graph = build_dependency_graph(tables)
    indegree = {k: 0 for k in graph}
    children: dict[str, list[str]] = {k: [] for k in graph}
    for child, parents in graph.items():
        indegree[child] = len(parents)
        for p in parents:
            children[p].append(child)

    ready = sorted([t for t, d in indegree.items() if d == 0])
    order: list[str] = []
    while ready:
        node = ready.pop(0)
        order.append(node)
        for ch in sorted(children[node]):
            indegree[ch] -= 1
            if indegree[ch] == 0:
                ready.append(ch)
        ready.sort()

    if len(order) != len(graph):
        cyclic = [t for t, d in indegree.items() if d > 0]
        raise ValueError(f"检测到外键依赖循环，涉及表: {', '.join(sorted(cyclic))}")
    return order
