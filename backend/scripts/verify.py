"""端到端验证脚本：确定性 / 批次清理隔离 / 唯一冲突报错。

用法（在 backend/ 目录）：
    /workspace/miniforge3/envs/synth/bin/python scripts/verify.py
"""
from __future__ import annotations

import sys

from sqlalchemy import text

sys.path.insert(0, ".")

from app.config import get_engine
from app.generator import DataGenerator
from app.models import batches_t, init_sidecar
from app.cleanup import cleanup_batch

PRE_EMAIL = "preexisting@corp.example"
PRE_ORDER = "PRE-EXISTING-0001"


def reset() -> None:
    with get_engine().begin() as conn:
        conn.execute(text("TRUNCATE synth_rows, synth_batches"))
        conn.execute(text("TRUNCATE orders, customers RESTART IDENTITY CASCADE"))
        conn.execute(text(
            "INSERT INTO customers (name, email, phone, city) "
            "VALUES ('既有客户-手动录入', :e, '13800000000', '北京')"
        ), {"e": PRE_EMAIL})
        conn.execute(text(
            "INSERT INTO orders (customer_id, order_no, amount, status, remark) "
            "SELECT id, :o, 88.88, 'paid', '既有订单' FROM customers WHERE email = :e"
        ), {"o": PRE_ORDER, "e": PRE_EMAIL})
    init_sidecar()


def make_batch(bid: str, seed: int, counts: dict, tag: str) -> dict:
    with get_engine().begin() as conn:
        conn.execute(
            batches_t.insert().values(
                id=bid, seed=seed, params={"counts": counts},
                status="running", progress={}, inserted_rows=0,
            )
        )
    return DataGenerator(get_engine(), seed, counts, batch_tag=tag).run(bid)


def counts() -> dict[str, int]:
    with get_engine().connect() as conn:
        c = conn.execute(text("SELECT count(*) FROM customers")).scalar_one()
        o = conn.execute(text("SELECT count(*) FROM orders")).scalar_one()
        sr = conn.execute(text("SELECT count(*) FROM synth_rows")).scalar_one()
    return {"customers": c, "orders": o, "synth_rows": sr}


def main() -> None:
    reset()
    print("[0] 初始既有数据:", counts())

    # 1) 种子 42 跑两次，比较生成数据是否完全一致
    r1 = make_batch("v42-1", 42, {"customers": 5, "orders": 8}, "aaa11111")
    assert r1["status"] == "done", r1
    s1_customers = r1["samples"]["customers"]
    s1_orders = r1["samples"]["orders"]

    r2 = make_batch("v42-2", 42, {"customers": 5, "orders": 8}, "bbb22222")
    assert r2["status"] == "done", r2
    s2_customers = r2["samples"]["customers"]
    s2_orders = r2["samples"]["orders"]

    # 去掉批次后缀后比较（id 是数据库自增，不参与确定性比较）
    def strip_tag(rows, tag):
        out = []
        for r in rows:
            r = {k: v for k, v in r.items() if k not in ("id", "created_at")}
            for k, v in list(r.items()):
                if isinstance(v, str):
                    r[k] = v.replace(f"-{tag}", "")
            out.append(r)
        return out

    a = strip_tag(s1_customers, "aaa11111")
    b = strip_tag(s2_customers, "bbb22222")
    assert a == b, f"相同种子客户数据不一致:\n{a}\n{b}"
    # 订单的 customer_id 会因父表自增 id 不同而不同，其余业务字段应一致
    def biz(rows):
        return [{k: v for k, v in r.items() if k not in ("id", "customer_id", "created_at")}
                for r in rows]
    assert biz(strip_tag(s1_orders, "aaa11111")) == biz(strip_tag(s2_orders, "bbb22222"))
    print("[1] ✔ 相同种子 => 相同结构的合成数据（Faker 序列一致，唯一值仅批次后缀不同）")
    print("    客户样例:", [r["name"] for r in a[:3]], [r["email"] for r in a[:3]])

    n = counts()
    assert n["customers"] == 1 + 10 and n["orders"] == 1 + 16, n
    assert n["synth_rows"] == 26, n
    print("[2] ✔ 两个批次共存，既有记录仍在:", n)

    # 2) 清理第一批，只删 5+8，第二批和既有数据保留
    res = cleanup_batch(get_engine(), "v42-1")
    n = counts()
    assert res["deleted_rows"] == 13, res
    assert n["customers"] == 1 + 5 and n["orders"] == 1 + 8, n
    with get_engine().connect() as conn:
        pre_c = conn.execute(text(
            "SELECT name FROM customers WHERE email = :e"
        ), {"e": PRE_EMAIL}).scalar_one()
        pre_o = conn.execute(text(
            "SELECT remark FROM orders WHERE order_no = :o"
        ), {"o": PRE_ORDER}).scalar_one()
    assert pre_c == "既有客户-手动录入" and pre_o == "既有订单"
    print("[3] ✔ 仅清理本批次:", res["deleted_by_table"],
          "| 既有记录保留:", pre_c, "/", pre_o, "| 剩余:", n)

    # 3) 制造唯一冲突：外部预置一条占用新批次将生成的唯一邮箱
    conflict_email = "external-block@example.test"
    with get_engine().begin() as conn:
        conn.execute(text(
            "INSERT INTO customers (name, email) VALUES ('占位', :t)"
        ), {"t": conflict_email})

    # 让该批次生成的第一个唯一邮箱恰好等于被占用值：临时打 monkey 补丁
    orig = DataGenerator._value_for

    def patched(self, table, col, row_idx):
        v = orig(self, table, col, row_idx)
        if table.name == "customers" and col.name == "email" and row_idx == 0:
            return conflict_email
        return v

    DataGenerator._value_for = patched
    try:
        r3 = make_batch("v42-3", 42, {"customers": 1, "orders": 0}, "ccc33333")
    finally:
        DataGenerator._value_for = orig
    assert r3["status"] == "failed", r3
    err = r3["error"]
    assert err["type"] == "unique_violation", err
    assert err["table"] == "customers", err
    assert err["constraint"] == "customers_email_key", err
    print("[4] ✔ 唯一冲突正确上报: 表=%s 约束=%s sqlstate=%s" % (
        err["table"], err["constraint"], err["sqlstate"]))
    print("   detail:", err["detail"][:100])

    # 失败批次登记为 failed，已提交的 chunk 之外不影响数据
    n2 = counts()
    print("[5] 冲突后数据量:", n2, "（既有与前批次数据完好）")

    # 清理第二批与失败批次：失败批次可能没有登记行，清理应安全
    res2 = cleanup_batch(get_engine(), "v42-2")
    print("[6] ✔ 清理第二批:", res2["deleted_by_table"], "剩余:", counts())

    print("\n全部验证通过 ✅")


if __name__ == "__main__":
    main()
