# 智慧大棚数据管理平台（从 0 重写）

基于 Streamlit 的智慧农业大棚数据可视化与分析系统。本仓库为按 `docs/rewrite_roadmap.md`
从零重写的版本，核心原则：**业务/UI 分离、安全内建、依赖分层、不包含任何 Ollama 本地模型**。

## 技术栈

- UI：Streamlit + Plotly
- 数据：SQLAlchemy 2.0（MySQL 8.0 / SQLite 开发）
- 预测：Prophet / SARIMA（可选 `.[ml]`）/ 朴素兜底
- 认证：bcrypt + JWT（密钥外置，令牌不进 URL）

## 快速开始

```bash
# 安装依赖（含开发依赖）
uv pip install -e '.[dev]'

# 初始化数据库结构（Alembic 迁移，单一事实来源）
python scripts/migrate.py upgrade
# 或：alembic upgrade head

# 生成演示数据
python -m smart_farm.data.seed

# 启动
streamlit run src/smart_farm/app/main.py
```

默认账号（seed 生成）：`admin` / `Admin@123456`

## 数据库迁移（Alembic）

建表/升级统一通过 Alembic，模型 `smart_farm.data.models.Base` 为单一事实来源：

```bash
python scripts/migrate.py upgrade            # 升级到最新
python scripts/migrate.py downgrade base     # 回滚到初始
python scripts/migrate.py revision -m "说明"  # 对比模型生成新迁移
```

> 开发环境也可用 `python -m smart_farm.data.seed`（内部调用迁移）；请勿再使用
> `Base.metadata.create_all`，以免与 Alembic 版本记录冲突。

## 结构

```
src/smart_farm/
  config.py          # 类型化配置
  data/              # ORM 模型 / 数据库 / 仓库
  services/          # 纯业务逻辑（auth/analysis/prediction/decision/llm）
  app/               # Streamlit UI（仅渲染）
tests/               # pytest
```
