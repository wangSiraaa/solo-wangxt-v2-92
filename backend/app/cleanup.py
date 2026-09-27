"""按批次精确清理：只删除 synth_rows 中登记的本批次业务行。

删除顺序按外键逆拓扑（子表先删），每条删除按主键精确匹配，
因此既有记录（未在本批次登记）绝不会被删除。
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from .models import batches_t, rows_t
from .reflection import reflect, topo_order


def cleanup_batch(engine: Engine, batch_id: str) -> dict:
    metas = {t.name: t for t in reflect(engine)}
    order = topo_order(list(metas.values()))  # 父 -> 子
    delete_order = list(reversed(order))      # 子 -> 父

    deleted: dict[str, int] = {}
    skipped_orphan_pk = 0

    with engine.begin() as conn:
        rows = conn.execute(
            sa.select(rows_t.c.table_name, rows_t.c.row_pk)
            .where(rows_t.c.batch_id == batch_id)
        ).all()
        by_table: dict[str, list[dict]] = {}
        for table_name, row_pk in rows:
            by_table.setdefault(table_name, []).append(row_pk)

        for table_meta in delete_order:
            name = table_meta.name
            pks = by_table.get(name)
            if not pks:
                continue
            t = sa.table(name, *[sa.column(c) for c in table_meta.pk_columns])
            pk_cols = table_meta.pk_columns
            count = 0
            for pk in pks:
                if not all(c in pk for c in pk_cols):
                    skipped_orphan_pk += 1
                    continue
                cond = sa.and_(*[sa.column(c) == pk[c] for c in pk_cols])
                result = conn.execute(sa.delete(t).where(cond))
                count += result.rowcount or 0
            deleted[name] = count

        # 全部业务行删除成功后才移除溯源登记与批次记录
        conn.execute(rows_t.delete().where(rows_t.c.batch_id == batch_id))
        conn.execute(batches_t.delete().where(batches_t.c.id == batch_id))

    total = sum(deleted.values())
    return {
        "batch_id": batch_id,
        "deleted_rows": total,
        "deleted_by_table": deleted,
        "skipped_orphan_pk": skipped_orphan_pk,
    }
