-- 业务库示例：客户与订单关联，email 为唯一字段
-- 由 SQLAlchemy 反射读取主键 / 外键 / 非空 / 唯一约束

CREATE TABLE IF NOT EXISTS customers (
    id          BIGSERIAL PRIMARY KEY,
    name        VARCHAR(100) NOT NULL,
    email       VARCHAR(200) NOT NULL,
    phone       VARCHAR(40),
    city        VARCHAR(80),
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT customers_email_key UNIQUE (email)
);

CREATE TABLE IF NOT EXISTS orders (
    id          BIGSERIAL PRIMARY KEY,
    customer_id BIGINT NOT NULL
                REFERENCES customers(id) ON DELETE RESTRICT,
    order_no    VARCHAR(40) NOT NULL,
    amount      NUMERIC(12, 2) NOT NULL,
    status      VARCHAR(20) NOT NULL DEFAULT 'pending',
    remark      TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT orders_order_no_key UNIQUE (order_no)
);

-- 演示用的“既有记录”：批次清理绝不能删掉这些行
INSERT INTO customers (name, email, phone, city)
VALUES ('既有客户-手动录入', 'preexisting@corp.example', '13800000000', '北京')
ON CONFLICT (email) DO NOTHING;

INSERT INTO orders (customer_id, order_no, amount, status, remark)
SELECT id, 'PRE-EXISTING-0001', 88.88, 'paid', '生成器之外的既有订单'
FROM customers
WHERE email = 'preexisting@corp.example'
ON CONFLICT (order_no) DO NOTHING;
