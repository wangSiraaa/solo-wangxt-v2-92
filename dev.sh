#!/usr/bin/env bash
# 一键启动本地开发环境：PostgreSQL + FastAPI + Vite
#
# 可重复执行：已运行的组件会被跳过；演示库不存在时自动初始化。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON:-python3}"
PGBIN_DIR="$(dirname "$(command -v initdb || true)")"
PGDATA="${PGDATA:-$HOME/pgdata}"
PGPORT="${PGPORT:-5432}"
DB_NAME="${DB_NAME:-synthdb}"
DB_USER="${DB_USER:-postgres}"
DB_URL="postgresql+psycopg2://${DB_USER}@localhost:${PGPORT}/${DB_NAME}"

echo "==> [1/4] PostgreSQL"
if ! command -v initdb >/dev/null 2>&1; then
  echo "    未找到 initdb。请先安装 PostgreSQL，例如："
  echo "      conda create -n synth python=3.11 postgresql=16 -c conda-forge && conda activate synth"
  exit 1
fi
if [ ! -d "$PGDATA" ]; then
  initdb -D "$PGDATA" -U "$DB_USER" --auth=trust -E UTF8 >/dev/null
  echo "unix_socket_directories = '/tmp'" >> "$PGDATA/postgresql.conf"
  echo "listen_addresses = 'localhost'" >> "$PGDATA/postgresql.conf"
fi
if ! pg_isready -h localhost -p "$PGPORT" >/dev/null 2>&1; then
  mkdir -p "$HOME/pglogs"
  pg_ctl -D "$PGDATA" -l "$HOME/pglogs/pg.log" start >/dev/null
  sleep 2
fi
if ! psql -h localhost -U "$DB_USER" -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1; then
  psql -h localhost -U "$DB_USER" -d postgres -c "CREATE DATABASE ${DB_NAME};"
fi
psql -h localhost -U "$DB_USER" -d "$DB_NAME" -f "$ROOT/backend/scripts/init_demo_db.sql" >/dev/null
echo "    PostgreSQL 就绪 (localhost:${PGPORT}/${DB_NAME})"

echo "==> [2/4] Python 依赖"
if [ ! -d "$ROOT/backend/.venv" ]; then
  "$PY" -m venv "$ROOT/backend/.venv"
fi
# shellcheck disable=SC1091
source "$ROOT/backend/.venv/bin/activate"
pip install -q -r "$ROOT/backend/requirements.txt"

echo "==> [3/4] FastAPI (后台)"
if ! curl -sf http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
  (
    cd "$ROOT/backend"
    DATABASE_URL="$DB_URL" nohup "$ROOT/backend/.venv/bin/python" -m uvicorn app.main:app \
      --host 127.0.0.1 --port 8000 > "$ROOT/backend/uvicorn.log" 2>&1 &
    echo $! > "$ROOT/backend/.uvicorn.pid"
  )
  sleep 3
fi
curl -sf http://127.0.0.1:8000/api/health >/dev/null && echo "    API: http://127.0.0.1:8000/docs"

echo "==> [4/4] 前端依赖"
if [ ! -d "$ROOT/frontend/node_modules" ]; then
  (cd "$ROOT/frontend" && npm install)
fi
echo "    启动 Vite: cd frontend && npm run dev  →  http://127.0.0.1:5173"
echo
echo "完成。端到端自检：python backend/scripts/e2e_check.py"
