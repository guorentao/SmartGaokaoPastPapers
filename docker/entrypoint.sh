#!/bin/sh
# 容器启动入口：数据库不存在时先重建，再拉起服务
set -e

DB="${GAOKAO_DB:-/app/database/gaokao.db}"

if [ ! -f "$DB" ]; then
    echo "[entrypoint] 未找到数据库 $DB，开始从 data/ 重建 ..."
    python scripts/bootstrap.py
    if [ "$DB" != "/app/database/gaokao.db" ] && [ -f /app/database/gaokao.db ]; then
        mkdir -p "$(dirname "$DB")"
        cp /app/database/gaokao.db "$DB"
    fi
fi

exec python -m uvicorn src.web.app:app --host 0.0.0.0 --port "${PORT:-8000}"
