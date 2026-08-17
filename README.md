# 智慧大棚数据管理平台（从 0 重写）

基于 Streamlit 的智慧农业大棚数据可视化、清洗、分析、预测与运维系统。本仓库是按
`docs/rewrite_roadmap.md` 从零重写的版本，核心原则：

- **业务 / UI 分离**：所有算法写成纯 Python 服务（无 `st.*`），可单测、可复用、可 API 化。
- **安全内建**：密钥外置、登录态不进 URL、参数化 SQL、最小权限——在脚手架阶段即落地。
- **依赖分层**：`base` / `ml`（可选）/ `dev`，不引入无关大包。
- **无本地模型**：不引入任何 Ollama / 本地大模型，AI 洞察模块已取消（见路线图阶段 4）。

## 功能模块

| 模块 | 路由键 | 说明 |
|---|---|---|
| 综合监控仪表板 | `dashboard` | 实时指标卡 + 趋势 |
| 数据概览 | `data_overview` | 数据库浏览 + 上传 / 导出 |
| 数据清洗与异常 | `data_cleaning` | 异常检测（IQR / Z-Score / 孤立森林）+ 清洗 |
| 数据分析 | `data_analysis` | 描述统计 / 相关性 / 时间派生聚合 |
| 可视化 | `visualization` | 折线 / 直方图 / 箱线 / 散点 |
| 本地数据预测 | `prediction` | Prophet / SARIMA / 朴素兜底（+ 置信区间） |
| 自动化决策 | `decision` | 规则引擎 |
| 用户管理 * | `user_management` | 用户与角色（管理员） |
| 操作日志 * | `log_viewer` | 审计日志（管理员） |
| 系统监控 * | `system_monitoring` | 数据量与质量（管理员） |
| 模块配置 * | `module_config` | 声明式模块注册表（管理员） |
| 备份与恢复 * | `backup_restore` | 数据导出 / 导入（管理员） |

> `*` 为管理员专属，普通用户菜单中不可见，直接访问会提示无权限。

## 技术栈

- UI：Streamlit ≥ 1.40 + Plotly
- 数据：SQLAlchemy 2.0（MySQL 8.0 生产 / SQLite 开发）+ Alembic 迁移
- 预测：Prophet / SARIMA（可选 `.[ml]` 依赖层）/ 朴素兜底（重依赖懒加载）
- 认证：bcrypt + PyJWT（密钥外置，令牌不进 URL）
- 配置：pydantic-settings（类型化，替代散落的 `os.getenv`）
- 工程化：pytest + ruff + mypy + GitHub Actions

## 目录结构

```
smart_farm/
├── pyproject.toml            # 分层依赖 + ruff/mypy/pytest 配置
├── .env.example              # 配置占位（不提交真实值）
├── alembic.ini / migrations/ # 数据库迁移（建表单一事实来源）
├── src/smart_farm/
│   ├── config.py             # pydantic-settings 读取配置
│   ├── data/
│   │   ├── models.py         # ORM（user / operation_logs / greenhouse / 4 张传感器表）
│   │   ├── database.py       # 单 engine + get_session() 上下文管理器
│   │   ├── repositories.py   # 传感器/用户/日志取数（分页 + 时间窗）
│   │   └── seed.py           # 演示数据生成（经 Alembic 路径）
│   ├── services/             # 纯 Python，无 st.*，可单测
│   │   ├── auth_service.py   # 登录/注册/哈希/限流
│   │   ├── analysis_service.py
│   │   ├── cleaning_service.py
│   │   ├── anomaly_service.py
│   │   ├── prediction_service.py
│   │   └── decision_service.py
│   └── app/                  # Streamlit UI（仅渲染）
│       ├── main.py           # 入口 / 路由 / 菜单 / 角色守卫
│       ├── auth_ui.py
│       ├── cache.py          # @st.cache_data 热点查询与预测
│       ├── guards.py         # require_admin()
│       └── pages/            # 各页面（仅渲染）
├── tests/                    # pytest 覆盖 services
├── scripts/migrate.py        # 迁移便捷命令
└── docs/rewrite_roadmap.md   # 重写路线图
```

数据模型（传感器表）：`soil_moisture`、`air_temperature_humidity`、`soil_nutrient`、
`light_intensity`；外加 `user`、`greenhouse`、`operation_logs`。

## 快速开始

```bash
# 1. 安装依赖（含开发依赖）。推荐用 uv 管理的 venv（Python 3.13）
uv pip install -e '.[dev]'

# 2. 初始化数据库结构（Alembic 迁移，单一事实来源）
python scripts/migrate.py upgrade
# 或：alembic upgrade head

# 3. 生成演示数据（每表约 1441 行 + admin 用户）
python -m smart_farm.data.seed

# 4. 启动
streamlit run src/smart_farm/app/main.py
```

默认管理员账号（由 seed 生成）：`admin` / `Admin@123456`

> 开发环境也可直接 `python -m smart_farm.data.seed` 内部调用迁移建表；请勿再使用
> `Base.metadata.create_all`，以免与 Alembic 版本记录冲突。

## 配置（密钥外置）

复制 `.env.example` 为 `.env` 并按需修改。生产环境 **必须** 用强随机 `SECRET_KEY`：

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

关键项：`DATABASE_URL`（开发用 SQLite，生产换 MySQL）、`SECRET_KEY`、
`JWT_ALGORITHM`、`ACCESS_TOKEN_EXPIRE_MINUTES`、`BCRYPT_ROUNDS`、DB 连接池参数。

## 数据库迁移（Alembic）

```bash
python scripts/migrate.py upgrade            # 升级到最新
python scripts/migrate.py downgrade base     # 回滚到初始
python scripts/migrate.py revision -m "说明"  # 对比模型生成新迁移
```

## 测试与质量

```bash
# Lint
ruff check

# 单元测试（services 层纯函数）
pytest
# 含预测/ML 用例需先安装可选依赖：uv pip install -e '.[ml]'
# 未装 scikit-learn 时相关用例自动跳过

# 类型检查（可选）
mypy src
```

CI（`.github/workflows/ci.yml`）自动跑 `ruff check` + `pytest`（含覆盖率门禁）。

## 部署（Docker）

```bash
# 构建并启动（强密钥通过环境变量注入，绝不写进镜像）
export SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")
export DATABASE_URL="mysql+pymysql://user:password@db:3306/intelligent_farm"
docker compose up -d --build
```

详见 `Dockerfile` 与 `docker-compose.yml`。容器以非 root 运行，仅暴露 Streamlit 端口。

## 实现说明

- 所有算法（预测、清洗、异常、分析、决策）位于 `services/`，UI 仅负责渲染与取数。
- 热点查询与预测结果经 `app/cache.py` 的 `@st.cache_data(ttl=, max_entries=)` 缓存，防止无限增长。
- 长预测任务（Prophet / SARIMA）当前为同步执行并带超时兜底；后续可改为后台任务 + 进度。
- 角色守卫 `require_admin()` 基于 `st.session_state["role"]`，管理页对普通用户隐藏且不可直访。
- 备份 / 恢复以 JSON 快照形式导出 / 导入，ORM 参数化重建，显式 `commit`。
