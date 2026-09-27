-- Demo business schema: 客户(customers) -> 订单(orders)
--
-- Both tables carry a UNIQUE field so that deterministic same-seed
-- re-generation can demonstrate unique-constraint conflicts.
-- Run once against the target database:
--   psql -h localhost -U postgres -d synthdb -f init_demo_db.sql

BEGIN;

CREATE TABLE IF NOT EXISTS customers (
    id          BIGSERIAL PRIMARY KEY,
    customer_no VARCHAR(32)  NOT NULL UNIQUE,           -- 唯一字段：客户编号
    full_name   VARCHAR(64)  NOT NULL,                  -- Faker 非真实姓名
    email       VARCHAR(128) NOT NULL UNIQUE,           -- 唯一字段：邮箱
    phone       VARCHAR(20),
    city        VARCHAR(64),
    address     VARCHAR(255),
    status      VARCHAR(16) NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active', 'pending', 'closed')),
    created_at  TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS orders (
    id           BIGSERIAL PRIMARY KEY,
    order_no     VARCHAR(40)   NOT NULL UNIQUE,         -- 唯一字段：订单编号
    customer_id  BIGINT        NOT NULL
                   REFERENCES customers(id) ON DELETE RESTRICT, -- 外键：父表先
    amount       NUMERIC(12,2) NOT NULL CHECK (amount > 0),
    currency     VARCHAR(8)    NOT NULL DEFAULT 'CNY'
                   CHECK (currency IN ('CNY', 'USD', 'EUR')),
    order_status VARCHAR(16)   NOT NULL DEFAULT 'pending'
                   CHECK (order_status IN ('pending', 'paid', 'shipped', 'cancelled')),
    remark       VARCHAR(255),
    created_at   TIMESTAMP     NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders(customer_id);

-- Pre-existing records inserted BEFORE any generated batch.
-- Batch cleanup must never delete these rows (no batch registration).
INSERT INTO customers (customer_no, full_name, email, phone, city, address, status)
SELECT 'PREEXIST-0001',
       '预置客户（既有记录）',
       'preexisting@example.test',
       '13900000000',
       'Beijing',
       '1 Existing Road',
       'active'
WHERE NOT EXISTS (
    SELECT 1 FROM customers WHERE customer_no = 'PREEXIST-0001'
);

COMMIT;
