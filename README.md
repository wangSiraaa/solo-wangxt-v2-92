# 合成数据生成工作台（Synthetic Data Workbench）

为测试平台生成**可复现**合成数据的全栈工作台：

- **前端**：React 18 + TypeScript + Vite —— 展示允许的表结构（主键 / 外键 /
  非空 / 唯一 / CHECK）、每表记录数量、随机种子、实时生成进度、样例数据、
  约束冲突详情，以及批次的创建 / 查看 / 仅本批次清理。
- **后端**：FastAPI + SQLAlchemy —— 从 PostgreSQL 实时自省表结构，按外键
  拓扑排序（父表先、子表后），用 **Faker 生成非真实姓名等测试值**，
  每批数据登记独立批次标识与每行主键。
- **数据库**：本地 PostgreSQL 作为生成目标。

## 录制的关键行为

| 录制点 | 实现方式 |
| --- | --- |
| 相同种子得到相同结构的数据 | `Faker.seed(seed)` + 私有 `random.Random(seed)`；编号/邮箱/手机号等唯一字段按 `seed + 行序号` 确定性渲染 |
| 客户 → 订单关联 | `orders.customer_id` 外键引用 `customers(id)`；拓扑序保证先生成 customers，订单从已存在客户池中随机选父 |
| 唯一字段 | `customers.customer_no`、`customers.email`、`orders.order_no` 均为 UNIQUE |
| 约束冲突显示涉及的表和约束 | 冲突卡片展示 类型 / 表（含关联表）/ 约束名 / 列 / 冲突值 / 说明 |
| 相同种子重放 | 复现完全相同的编号/邮箱 → 自然触发唯一约束冲突，整批事务回滚 |
| 清理不删除既有记录 | 清理只按 `synth_batch_rows` 登记的主键删本批次行；预置行 `PREEXIST-0001` 从不登记、从不删除，删除顺序为子先父后 |

## 目录结构

```
backend/
  app/
    config.py        # 环境变量配置
    db.py            # 引擎 + synth.synth_batches / synth_batch_rows 记账表
    introspect.py    # 表结构自省（列/PK/FK/UNIQUE/CHECK/枚举）+ 拓扑排序
    generator.py     # Faker 确定性值生成器
    worker.py        # 后台生成任务（预检、生成、约束捕获、批次登记）
    batches.py       # 批次列表/详情/仅本批次清理
    main.py          # FastAPI 路由
  scripts/
    init_demo_db.sql # customers/orders 演示 schema + 1 条预置既有客户
    e2e_check.py     # 端到端自检（25 项断言）
frontend/
  src/
    App.tsx, api.ts, types.ts
    components/      # SchemaPanel / GenerationForm / JobProgress / Samples / Batches
```

## 快速开始（本地 PostgreSQL）

需要 Python 3.11+、Node 18+、PostgreSQL 14+。

```bash
# 0)（无 root 环境可用 conda 提供 PG 与 Python）
#    conda create -n synth python=3.11 postgresql=16 -c conda-forge && conda activate synth

# 1) 建库并导入演示 schema（customers→orders，含 1 条预置客户）
createdb synthdb
psql -d synthdb -f backend/scripts/init_demo_db.sql

# 2) 启动后端
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
DATABASE_URL=postgresql+psycopg2://postgres@localhost:5432/synthdb \
  uvicorn app.main:app --reload --port 8000

# 3) 启动前端
cd ../frontend
npm install
npm run dev        # http://127.0.0.1:5173 （/api 已代理到 8000）
```

或一键启动（已安装 PG 到 PATH 时）：`./dev.sh`。

## 使用流程（即验收录屏脚本）

1. 左侧确认表结构：`customers → orders` 生成顺序、类型与各种约束徽章；
   设置 customers=5、orders=10，种子填 **42**。
2. 可先勾选**试运行**：看到样例数据与“检查通过（已回滚）”，库里无新增。
3. 正式提交：得到批次号 `B<时间戳>-S42-<6位随机后缀>`（其中可读出种子 42），
   批次面板出现该批次，可查看在库样例。
4. **不清理**，再次用种子 42 相同数量提交：出现红色冲突卡片 ——
   表 `public.customers`、约束 `customers_customer_no_key`、列 `customer_no`、
   冲突值 `CUS-S42-000001`；整批回滚，不产生半成品。
5. 对上一批次点击**仅清理本批次**：删除 orders×10、customers×5，
   预置客户 `PREEXIST-0001` 依然存在；批次记录同步清除。
6. 再用种子 42 生成：数据与第 3 步**逐值相同**。

## 端到端自检

在后端运行时执行（脚本会自行清理批次、验证全部录制点）：

```bash
python backend/scripts/e2e_check.py http://127.0.0.1:8000
```

## HTTP API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/tables` | 允许的表结构 + 依赖图 + 拓扑生成顺序 |
| POST | `/api/generate` | `{seed, counts:{table:n}, dry_run, note}` → `job_id`（异步） |
| GET | `/api/jobs/{id}` | 进度、已生成计数、样例、结构化 `conflict` |
| GET | `/api/batches` | 已提交批次列表 |
| GET | `/api/batches/{batch_no}` | 批次详情：每表登记行数 + 在库样例 |
| POST | `/api/batches/{batch_no}/cleanup` | **仅**删除该批次登记的主键行 |

## 设计说明

- **记账 schema 隔离**：批次元数据放在私有 schema（默认 `synth`），自省只看
  `BUSINESS_SCHEMA`，因此记账表永远不会被当成业务表生成数据。
- **整批事务**：一次生成共用一个事务；任何约束失败都整体回滚，
  失败批次不会留下批次记录或半成品行。
- **预检**：子表要生成而父表无记录（NOT NULL FK）时，在写入前直接返回
  外键冲突说明，而不是依赖数据库报错。
- **非真实数据**：姓名/地址/电话全部来自 Faker；邮箱域名为保留域名
  `example.test`，确定性编号形如 `CUS-S42-000001`。
- **清理安全性**：DELETE 的主键集合来自 `synth_batch_rows`，并按拓扑逆序
  （先删 orders 再删 customers）执行，受 `ON DELETE RESTRICT` 保护。
