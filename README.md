# 智慧大棚数据管理平台（从 0 重写）

基于 Streamlit 的智慧农业大棚数据可视化、清洗、分析、预测与运维系统。本仓库是按
`docs/rewrite_roadmap.md` 从零重写的版本，核心原则：

- **业务 / UI 分离**：所有算法写成纯 Python 服务（无 `st.*`），可单测、可复用、可 API 化。
- **安全内建**：密钥外置、登录态不进 URL、参数化 SQL、最小权限——在脚手架阶段即落地。
- **依赖分层**：`base` / `ml`（可选）/ `dev`，不引入无关大包。
- **无本地模型**：不引入任何 Ollama / 本地大模型，AI 洞察模块已取消（见路线图阶段 4）。

## 功能模块（17 个页面）

| 模块 | 路由键 | 说明 |
|---|---|---|
| 综合监控仪表板 | `dashboard` | 5 项实时指标卡、作物阶段阈值配置、快捷按钮、异常点 + 预测线叠加 |
| 数据概览 | `data_overview` | 数据库浏览（按指标 / 全量多表 JOIN）+ 上传 / 导出 |
| 数据清洗与异常 | `data_cleaning` | 5-tab：规则模板（农业/ML/自定义）、基础清洗、缺失值（含 `_filled` 标识列）、异常检测（IQR/Z/孤立森林）、导出 |
| 数据分析 | `data_analysis` | 智能解读 + 描述统计 + 相关性热力图 + 分组聚合 |
| 高级分析 | `advanced_analysis` | 分组聚合 + 最高/最低分组洞察 + 阈值建议 |
| 可视化 | `visualization` | 智能推荐 + 7 类基础图 + 双轴图/多子图 + 每图解读 |
| 本地数据预测 | `prediction` | 单变量（Prophet+SARIMA 混合/纯 Prophet）+ 多变量随机森林、置信区间、综合评分、自动归档 + 历史 tab |
| 自动化决策 | `decision` | 规则引擎（土壤/温度/湿度/光照） |
| 历史报告 * | `history_reports` | 预测归档浏览（搜索/预览/下载/删除） |
| 用户管理 * | `user_management` | 管理员申请审批、改角色、重置密码、创建/编辑/删除用户 |
| 操作日志 * | `log_viewer` | 时间快捷筛选、关键词/正则搜索、统计/操作链/告警 + 下载 |
| 系统监控 * | `system_monitoring` | 数据量与质量 + psutil 实时 CPU/内存/磁盘 |
| 数据库同步 * | `sync_databases` | 云端 ↔ 本地 4 表增量双向同步（手动） |
| 备份与恢复 * | `backup_restore` | JSON 快照 / Fernet 加密导出（密钥单独下载）+ 二次确认恢复 |
| 使用说明 | `use_instruction` | 功能说明 + FAQ + docx 说明书生成 |
| 模块配置 * | `module_config` | 模块启停 toggle + 依赖检查 + admin_only + JSON 持久化（导航真过滤） |
| 调试信息 * | `debug_info` | DEBUG_MODE 门控：环境/DB 状态/缓存/异常模拟 |

> `*` 为管理员专属（module_manager 控制）：普通用户菜单不可见，直访显示无权限。

认证能力：验证码（PIL PNG）、注册密码强度条、管理员申请审批、登录限流（10 次/30s）、bcrypt + JWT（令牌不进 URL）。

## 技术栈

- UI：Streamlit ≥ 1.40 + Plotly
- 数据：SQLAlchemy 2.0（MySQL 8.0 生产 / SQLite 开发）+ Alembic 迁移
- 预测：Prophet / SARIMA（必选依赖，随包安装）/ 朴素兜底
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
│   ├── services/             # 纯 Python，无 st.*，可单测（14 个模块）
│   │   ├── auth_service.py   # 登录/注册/哈希/限流/密码强度
│   │   ├── captcha_service.py    # 验证码 PNG（PIL）
│   │   ├── dashboard_service.py  # 作物阶段推荐/阈值
│   │   ├── analysis_service.py   # 描述统计/相关性/智能解读
│   │   ├── cleaning_service.py   # 清洗 + 规则引擎/模板
│   │   ├── anomaly_service.py    # IQR/Z-Score/孤立森林
│   │   ├── prediction_service.py # 预测（naive/Prophet/SARIMA/混合/多变量 RF）
│   │   ├── prediction_archive.py # 预测归档（CSV+MD+SQLite）
│   │   ├── decision_service.py   # 决策规则引擎
│   │   ├── visualization_service.py # 智能推荐/双轴/多子图
│   │   ├── system_service.py     # psutil 系统监控（可选）
│   │   ├── log_analysis_service.py # 日志统计分析
│   │   ├── module_manager.py     # 模块注册表 + 启停/依赖
│   │   ├── backup_service.py     # Fernet 加密备份
│   │   ├── sync_service.py       # 数据库双向同步
│   │   ├── errors.py             # 错误处理体系
│   │   ├── docx_manual.py        # 使用说明书 docx（可选）
│   │   └── instruction_data.py   # 使用说明数据
│   └── app/                  # Streamlit UI（仅渲染）
│       ├── main.py           # 入口：st.navigation + 模块过滤 + 角色守卫
│       ├── auth_ui.py        # 登录/注册（验证码 + 强度条）
│       ├── captcha_ui.py     # 验证码渲染
│       ├── cache.py          # @st.cache_data 热点查询与预测
│       ├── guards.py         # require_admin()
│       └── app_pages/        # 17 个页面（直接脚本，仅渲染）
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
# ML 依赖（scikit-learn/statsmodels/prophet/psutil）为必选，随 uv pip install -e . 一并安装

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

- 所有算法（预测、清洗、异常、分析、决策、同步、备份加密）位于 `services/`，UI 仅负责渲染与取数。
- 导航使用 `st.navigation` + `st.Page` + `app_pages/` 目录（遵循 developing-with-streamlit 技能，
  替代 legacy `pages/` 自动发现）；页面为直接脚本，不包裹 `show()` 函数；**导航经 module_manager
  按模块启停状态真过滤**（非仅菜单隐藏）。
- 热点查询与预测结果经 `app/cache.py` 的 `@st.cache_data(ttl=, max_entries=)` 缓存，防止无限增长。
- 预测：3H 采样（每天 8 点）、Prophet+SARIMA 权重融合、多变量随机森林、置信区间、综合评分，
  结果自动归档（CSV+MD+SQLite，上限 1000 条）。
- 角色守卫 `require_admin()` 基于 `st.session_state["role"]`；管理页仅在管理员角色时注入导航，
  且页面内部仍二次校验（普通用户直访显示无权限）。
- 备份 / 恢复：JSON 快照或 Fernet 加密（**密钥单独下载**，修复旧库密钥与密文同包缺陷），
  ORM 参数化重建，显式 `commit`，恢复需二次确认。
- 可选依赖懒加载：Prophet / psutil / python-docx 未安装时自动降级或给出安装提示（不崩溃）。
- 覆盖率门禁：`pytest` 通过 `addopts` 对 `smart_farm.services` 设 ≥70% 覆盖率下限（CI 强制执行）。

## 复刻自旧库的缺陷修复清单

| 旧库缺陷 | 修复方式 |
|---|---|
| JWT 写入 URL query_params（泄露风险） | 登录态仅存 session_state，令牌不入 URL |
| 备份密钥与密文同 zip 存放（等于没加密） | 密钥单独下载，与密文分开保存 |
| log_viewer 搜索模式 `if "AND" in ...` 恒真（OR/EXACT 死代码） | AND/OR/EXACT 三模式全可用 |
| 用户管理可删除当前登录用户 | 禁止删除自身 |
| 模块配置"菜单隐藏但 URL 可直访" | module_manager 真过滤导航 + 页面内守卫 |
| z-score 异常检测索引错位（isin 匹配 dropna 后子序列） | 位置对齐回填掩码 + 回归测试 |
| 使用说明数据双份重复定义（instruction_manual / generate_docx） | 统一读 instruction_data 单一来源 |
