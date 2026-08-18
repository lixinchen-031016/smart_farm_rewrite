#!/bin/sh
# 容器启动入口：先建表（Alembic 迁移单一事实来源），可选生成演示数据，再启动 Streamlit。
set -e

# 等待数据库（若使用 MySQL 等外部库，由 compose 的 depends_on healthcheck 保证已就绪）
python scripts/migrate.py upgrade

# 演示环境可选：SEED_DATA=1 时生成演示数据（生产请勿开启，会创建默认 admin 账号）
if [ "${SEED_DATA:-0}" = "1" ]; then
  python -m smart_farm.data.seed
fi

exec streamlit run src/smart_farm/app/main.py \
  --server.address=0.0.0.0 \
  --server.port=8501 \
  --server.headless=true
