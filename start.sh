#!/usr/bin/env bash
# 一键启动：PostgreSQL(5439) + FastAPI(8000) + Vite(5173)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
export PATH="/workspace/miniforge3/bin:$PATH"
PY=/workspace/miniforge3/envs/synth/bin/python
PGDATA=/workspace/pgdata

# 1. PostgreSQL
if ! pg_isready -h 127.0.0.1 -p 5439 >/dev/null 2>&1; then
  if [ ! -s "$PGDATA/PG_VERSION" ]; then
    initdb -D "$PGDATA" -U synth --auth=trust --auth-host=trust -E UTF8
    printf "port = 5439\nlisten_addresses = '127.0.0.1'\nunix_socket_directories = '/tmp'\n" \
      >> "$PGDATA/postgresql.conf"
  fi
  pg_ctl -D "$PGDATA" -l "$PGDATA/pg.log" -w start
  createdb -h 127.0.0.1 -p 5439 -U synth synthdb 2>/dev/null || true
fi
psql -h 127.0.0.1 -p 5439 -U synth -d synthdb \
  -f "$ROOT/backend/init_business_schema.sql" >/dev/null

# 2. 后端（会自动创建 synth_batches / synth_rows 侧表）
if ! curl -s http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
  ( cd "$ROOT/backend" && "$PY" -m uvicorn app.main:app \
      --host 127.0.0.1 --port 8000 > uvicorn.log 2>&1 & )
fi

# 3. 前端
if [ ! -d "$ROOT/frontend/node_modules" ]; then
  ( cd "$ROOT/frontend" && npm install --registry=https://registry.npmmirror.com )
fi
( cd "$ROOT/frontend" && npm run dev > vite.log 2>&1 & )

sleep 3
echo "✅ 已启动："
echo "   前端工作台  http://localhost:5173"
echo "   后端 API    http://127.0.0.1:8000/docs"
echo "   PostgreSQL  127.0.0.1:5439/synthdb (trust, 用户 synth)"
