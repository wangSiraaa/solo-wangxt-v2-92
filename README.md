# 合成数据生成工作台（全栈）

为测试平台的业务库生成**可复现**合成数据的全栈工作台：

- **React + TypeScript（Vite）**：展示允许的表结构、每表记录数量、随机种子、
  生成进度、样例数据与约束检查结果；可创建 / 查看批次，并**仅按本批次清理**。
- **FastAPI + SQLAlchemy**：反射读取表的主键、外键、非空、唯一约束（含唯一索引），
  按外键拓扑排序**先生成父表再生成子表**；Faker（zh_CN，非真实姓名）生成测试值。
- **PostgreSQL** 本地测试目标；所有合成数据以「批次」为单位生成与溯源。

## 演示模型：客户 → 订单 + 唯一字段

```sql
customers(id PK BIGSERIAL, name NOT NULL, email NOT NULL UNIQUE, phone, city, created_at)
orders   (id PK BIGSERIAL, customer_id NOT NULL REFERENCES customers(id),
          order_no NOT NULL UNIQUE, amount NUMERIC NOT NULL,
          status NOT NULL DEFAULT 'pending', remark, created_at)
```

库中预置 1 个既有客户 `preexisting@corp.example` 和 1 张既有订单
`PRE-EXISTING-0001`，用于验证「清理不会删除既有记录」。

## 一键启动

```bash
./start.sh
```

- 前端：http://localhost:5173
- API 文档：http://127.0.0.1:8000/docs
- PostgreSQL：`127.0.0.1:5439`，库名 `synthdb`，trust 认证、用户 `synth`

后端依赖装在 `/workspace/miniforge3/envs/synth`（Python 3.11），
数据目录 `/workspace/pgdata`。手动启动见文末。

## 核心设计

### 可复现（相同种子 → 相同数据）

- Faker 实例 `seed_instance(seed)`、Python `random.Random(seed)` 双固定，
  按固定的父→子拓扑顺序逐行生成。
- 唯一列在种子生成值后**固定追加批次短标识**（batch_id 的 uuid 段）：
  - 同一批次内相同参数与种子 ⇒ 数据完全一致；
  - 不同批次之间唯一值不撞车，可同时存在多批。
- 自增主键 / `server_default` / IDENTITY 列（`id`、`created_at`、`status`）
  交由数据库生成，用 `RETURNING` 取回真实主键，再作为子表外键候选。

### 批次溯源与「仅按本批次清理」

- `synth_batches`：批次参数、seed、状态、实时进度、插入行数、约束检查结果、错误。
- `synth_rows`：每一条插入的业务行的 `(batch_id, table_name, 主键 JSONB)`，
  与业务插入在**同一事务**提交。
- 清理按**子→父逆拓扑**逐行按主键精确删除；只删 `synth_rows` 中登记的行，
  再移除溯源记录。未登记的既有记录在物理上不可能被命中。

### 约束处理与展示

- 插入分块（200 行/事务），捕获 PG 错误码并解析 `diag`：
  - `23505` 唯一冲突、`23503` 外键、`23502` 非空、`23514` check 等，
  - 响应/批次错误中带**涉及的表**（`table_name`）与**约束名**（`constraint_name`）。
- 生成结束后对非空 / 唯一 / 外键逐项复查，结果随批次返回并在页面展示。
- 子表行数 > 0 而父表本批次为 0 时，自动引用库内既有父表行（按主键排序选取，
  保持确定性）；父表全空才报外键错误。

## API 摘要

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/schema` | 反射的表结构 + 生成顺序 |
| POST | `/api/batches` | 创建批次（`{seed, counts:{表:行数}}`），后台线程生成 |
| GET | `/api/batches` | 批次列表（页面每 0.7s 轮询运行中批次的进度） |
| GET | `/api/batches/{id}` | 批次详情：进度 / 检查结果 / 错误 |
| GET | `/api/batches/{id}/rows/{table}` | 回表读取本批次插入的样例行 |
| POST | `/api/batches/{id}/cancel` | 取消运行中批次（停在当前块之后） |
| DELETE | `/api/batches/{id}` | **仅清理该批次**插入的行 |

## 验证脚本（确定性 / 清理隔离 / 约束报错）

```bash
cd backend
../../miniforge3/envs/synth/bin/python scripts/verify.py
```

覆盖：
1. 相同种子跑两个批次，姓名 / 邮箱 / 电话 / 城市 / 订单业务字段逐行一致；
2. 清理第一批只删 13 行，既有客户与订单保留；
3. 预置外部占用唯一邮箱后再生成，批次失败并上报
   `table=customers, constraint=customers_email_key, sqlstate=23505`。

## 手动启动

```bash
# 数据库
export PATH=/workspace/miniforge3/bin:$PATH
pg_ctl -D /workspace/pgdata -l /workspace/pgdata/pg.log start
psql -h 127.0.0.1 -p 5439 -U synth -d synthdb -f backend/init_business_schema.sql

# 后端
cd backend && ../../miniforge3/envs/synth/bin/python -m uvicorn app.main:app --port 8000

# 前端
cd frontend && npm install && npm run dev
```

## 目录

```
backend/
  app/
    config.py        # 引擎 / 表白名单 / 侧表名
    models.py        # synth_batches / synth_rows
    reflection.py    # SQLAlchemy 反射 + 外键拓扑排序
    generator.py     # 确定性 Faker 生成、分块事务、约束错误解析、插后复查
    cleanup.py       # 批次精确清理（子→父）
    schemas.py       # Pydantic 模型
    main.py          # FastAPI 路由
  init_business_schema.sql
  scripts/verify.py
frontend/
  src/{api.ts,App.tsx,components/}
start.sh
```
