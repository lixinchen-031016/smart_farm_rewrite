# 智慧大棚数据平台 — 从 0 重写路线图

> 分析对象：`smart_farm_with_streamlit`（Streamlit 单体农业数据平台）
> 范围：现状结论 → 重写目标 → 目标架构 → 模块处置 → 数据模型 → 分阶段路线
> 约束：**不包含任何 Ollama / 本地大模型相关内容**，AI 洞察改为可插拔 Provider 抽象

---

## 1. 现状结论（一句话）

这是一个**功能面很广、但工程化与安全性偏弱**的 Streamlit 单体应用：认证、数据接入、清洗、分析、可视化、预测（Prophet/SARIMA/随机森林）、自动化决策、系统监控、备份恢复、日志、模块启停一应俱全；但存在**硬编码弱密钥、JWT 放 URL、全局共享 DB 会话、业务/UI 强耦合、无测试、依赖臃肿（含 snowflake/torch/boto3 等无关包）**等系统性短板。代码量约 `app.py`(911 行) + `utils/`(42 个模块, ~1.5 万行) + `auth.py`(548 行)。

**重写的核心目的**：保留已验证的业务能力（尤其预测与决策规则），用干净的"服务层 + UI 层"分层替掉耦合，把安全与测试作为地基而非补丁。

---

## 2. 重写目标与原则

| 原则 | 说明 |
|---|---|
| 业务/UI 分离 | 所有算法（预测、清洗、决策、异常、分析）写成**纯函数/服务类**，不依赖 `st.*`；UI 只负责渲染与取数。可单测、可复用、可 API 化。 |
| 安全内建 | 密钥外置、登录态不进 URL、参数化 SQL、最小权限——在脚手架阶段就落地，而非事后止血。 |
| 依赖瘦身 | 改用 `pyproject.toml` + 分层依赖（`base`/`ml`/`dev`），移除与场景无关的大包。 |
| Ollama 不复活 | 删除 `ollama_chat.py` 及本地模型拉取逻辑；如需 AI 解读，统一走**可插拔 LLM Provider**（云 API / 兼容 OpenAI 协议），默认可关闭。 |
| 渐进可演示 | 每个阶段都有可运行/可上线产出，不追求一次性大爆炸重写。 |
| 数据可迁移 | 新库表结构与现有 MySQL 数据尽量兼容，提供 Alembic 迁移与一次性导入脚本。 |

---

## 3. 目标技术栈（已剔除 Ollama 相关）

- **UI**：`streamlit` 1.4x（保留，适合数据后台）；可选叠加 `FastAPI` 暴露只读/写入 API。
- **可视化**：`plotly`（保留）、`altair`/`matplotlib`（按需）。
- **数据**：`pandas`、`numpy`、`pyarrow`、`openpyxl`/`xlsxwriter`。
- **预测/ML**：`prophet`（需 `cmdstanpy`，保留）、`statsmodels`（SARIMA）、`scikit-learn`（随机森林/孤立森林）。**移除 `torch`/`torchvision`/`torchaudio` 与 `gpu_accelerator.py`**（现有代码无真实训练链路，仅 GPU 加速脚手架）。
- **DB**：`sqlalchemy` 2.0 + `alembic` + `PyMySQL`（MySQL 8.0 保留；开发/测试用 SQLite）。
- **安全**：`bcrypt`、`PyJWT`、`cryptography`（备份 AES-256 保留）。
- **配置**：`pydantic-settings`（类型化配置，替代散落的 `os.getenv`）。
- **工程化**：`pytest`、`ruff`、`mypy`、GitHub Actions。
- **移除**：`snowflake-*`、`boto3/botocore/s3transfer`、`GitPython`、`ipython/debugpy`、`Flask-SQLAlchemy`、`streamlit-faker` 等无关/调试包。

---

## 4. 目标架构（分层）

```
┌──────────────────────────────────────────────────────────────┐
│  UI 层 (app/pages, app/components)  —— 仅 st.* + 调用 services │
│  Streamlit 多页应用；菜单/路由/登录态；不写任何算法           │
└───────────────────────────┬──────────────────────────────────┘
                            │ 调用
┌───────────────────────────▼──────────────────────────────────┐
│  服务层 (src/services/)  —— 纯 Python，无 st.*，可单测          │
│  analysis / cleaning / anomaly / prediction / decision /       │
│  report / llm(Provider 抽象) / auth(service) / export          │
└───────────────────────────┬──────────────────────────────────┘
                            │ 调用
┌───────────────────────────▼──────────────────────────────────┐
│  数据层 (src/data/)  —— models(ORM) / repositories / migrations │
│  统一 get_session() 上下文管理器；连接池只在此处创建           │
└───────────────────────────┬──────────────────────────────────┘
                            │
                     MySQL 8.0 (生产) / SQLite (测试)
        横切：config(pydantic) / logging / cache(st.cache_resource) / errors
```

> 关键变化：模块级全局 `session = Session()`（现 `auth.py:37`）彻底消失；预测不再在 UI 线程同步阻塞，改后台任务 + 进度；所有 SQL 走参数化。

---

## 5. 目标目录结构

```
smart_farm/
├── pyproject.toml            # 分层依赖 + ruff/mypy/pytest 配置
├── .env.example              # 密钥/DB/LLM_PROVIDER 占位（不提交真实值）
├── alembic.ini / migrations/ # 数据库迁移
├── src/
│   ├── config.py             # pydantic-settings 读取配置
│   ├── data/
│   │   ├── models.py         # ORM（修复 OperationLog 主键）
│   │   ├── database.py       # 唯一 engine + get_session()
│   │   └── repositories.py   # 传感器/用户/日志的取数（分页+时间窗）
│   ├── services/
│   │   ├── auth_service.py   # 登录/注册/限流（Redis 或 DB 表）
│   │   ├── analysis_service.py
│   │   ├── cleaning_service.py
│   │   ├── anomaly_service.py
│   │   ├── prediction_service.py   # Prophet/SARIMA/RF + 异步封装
│   │   ├── decision_service.py     # 规则配置化
│   │   ├── report_service.py       # 报告归档(md/json)
│   │   ├── llm/                    # ★ 可插拔 Provider（无 ollama）
│   │   │   ├── base.py             # LLMProvider 接口
│   │   │   └── openai_compatible.py# 默认实现，可关
│   │   └── export_service.py
│   └── app/                  # Streamlit UI
│       ├── main.py           # 入口/路由/菜单
│       ├── auth_ui.py
│       ├── pages/            # 各页面（仅渲染）
│       └── components/        # 卡片/图表封装
├── tests/                    # pytest 覆盖 services
└── deployment/               # Dockerfile / compose（强密钥占位）
```

---

## 6. 模块处置清单（保留 / 重写 / 丢弃）

| 现文件/模块 | 处置 | 重写要点 |
|---|---|---|
| `auth.py` | **重写** | 密钥外置；限流改 DB/Redis；JWT 不进 URL，登录态走 `st.session_state` + 服务端 Cookie/会话存储；bcrypt 保留 |
| `models.py` | **重写** | 修复 `OperationLog` 双主键（`log_time` 仅索引）；为未来多租户预留 `greenhouse_id` |
| `utils/database.py` | **重写** | 仅此处创建 `engine`/`Session`；统一 `get_session()`；接入 Alembic |
| `utils/data_operations.py` `sensor_data.py` | **重写** | 改为 `repositories.py`，增加分页 + 默认时间窗，避免全量拉取 |
| `data_cleaning*` `anomaly_detection.py` | **保留逻辑/重写** | 算法抽进 `cleaning_service`/`anomaly_service`，UI 仅渲染 |
| `data_analysis.py` `analysis.py` `advanced_analysis.py` | **保留逻辑/重写** | 迁入 `analysis_service`（纯函数 + 测试） |
| `visualization.py` `enhanced_visualization.py` | **保留逻辑/重写** | 图表函数迁入 `services` 或 `components`，UI 调用 |
| `predictions.py` `hybrid_prediction.py` | **保留核心/重写** | 保留 Prophet/SARIMA/RF；**删除 torch/GPU 分支**；异步执行 + 超时/进度；抽 `prediction_service` |
| `decision_engine.py` | **保留/重写** | 阈值抽成配置表；修复趋势计算（用真实时间差而非索引位）；去掉 `get_latest_sensor_data` 复制函数 |
| `backup.py` `restore.py` | **重写** | 参数化 SQL（`sqlalchemy.text` + 绑定）；`restore` 显式 `commit`；AES 加密保留 |
| `user_management.py` `system_monitoring.py` `log_viewer.py` `log_analyzer.py` | **保留/清理** | 迁入对应 UI 页；监控去 `psutil` 依赖可选 |
| `sync_manager.py` | **重写或暂缓** | 本地↔云端同步依赖具体云；重写时抽象为接口，默认不启用 |
| `module_config_ui.py` `module_manager.py` `module_config.json` | **重写** | 模块注册表改为代码内声明式 + DB 开关；消除"默认禁用却可 URL 直访"矛盾 |
| `dashboard.py` `integrated_dashboard.py` | **保留/重写** | 迁入 `pages/`；实时预览改用定时轮询/WebSocket 替代被禁用的 `data_preview` |
| `history_report_viewer.py` | **保留** | 仅读取报告归档，与 LLM 实现解耦即可 |
| `instruction_manual.py` `generate_docx_manual.py` | **保留** | 使用说明与文档生成保留 |
| `data_gen.py` | **保留** | 演示数据生成器，仅用于 dev/seed |
| `utils/logger.py` `error_handling.py` `captcha_utils.py` `ui_styles.py` | **保留/精简** | 横切能力保留；`logger` 改结构化 |
| `utils/ollama_chat.py` | **🗑 删除** | 本地 Ollama 客户端，全部移除 |
| `utils/ai_insights.py` | **🗑 重写** | 移除 Ollama 依赖，改为调用 `services/llm` 可插拔 Provider；默认可关闭 |
| `utils/gpu_accelerator.py` | **🗑 删除** | torch 依赖与 GPU 加速脚手架，无真实训练链路 |
| `utils/lazy_importer.py` | **🗑 删除** | 过度优化；直接用 `st.cache_resource` 缓存重对象 |
| `utils/debug_utils.py` (1230 行) | **🗑 重写** | 精简为可关闭的轻量调试页，不再巨文件 |
| `predictions_exports/` `ai_insights_exports/` | **保留目录** | 报告归档目录 |

**孤儿 `.pyc` 一律清理**：`enhanced_lstm_stable`/`machine_learning`/`navigation`/`performance_monitor`/`state_manager`（仅有 `.pyc` 无源）。

---

## 7. 数据模型重写建议

现状为 4 张独立传感器表 + `user` + `operation_logs`。重写时二选一：

- **方案 A（最小改动，推荐起步）**：保留 4 张传感器表结构，仅修复 `OperationLog` 主键（`id` 单字段自增，`log_time` 加索引），新增 `greenhouse` 表与 `greenhouse_id` 外键预留。迁移成本低、与现有数据兼容。
- **方案 B（更优但改动大）**：合并为统一时序表
  ```sql
  greenhouse(id, name, ...)
  sensor(id, greenhouse_id, type, unit, ...)
  reading(id, sensor_id, ts, value)   -- 按 ts 分区
  ```
  利于多棚/多指标扩展与查询聚合，但需一次性迁移历史数据。

> 重写第一阶段先落 **方案 A**，把"多租户/统一时序"列为第二阶段可选增强，避免首版过度设计。

---

## 8. 安全与质量基线（写进脚手架，不靠补丁）

1. 密钥：`.env`（已 gitignore）+ 生成 ≥32 位随机 `SECRET_KEY`；移除 `docker-compose` 中的 `031016`/`0000`/`DEBUG_MODE=true`。
2. 登录态：`st.session_state` + 服务端 HttpOnly Cookie 或短期内存态；URL 只保留 `?page=`。
3. DB：单例 `engine` + `get_session()` 上下文管理器；所有写操作参数化。
4. 限流：登录失败计数迁到 DB 表（或 Redis），跨进程生效。
5. 测试：`tests/` 覆盖 auth（哈希/限流）、prediction（确定性输出）、decision（规则）、cleaning（纯函数）；CI 跑 lint+test。
6. `.gitignore`：加入 `.venv/`、`__pycache__/`、`*.pyc`、`*.log`、模型权重、`.env`。

---

## 9. 分阶段重写路线图

> 节奏：每阶段可演示/可上线；总周期约 **10–14 周（1 名主力 + 评审）**。括号内为大致人周。

### 阶段 0 — 脚手架与地基（第 1–2 周）✅ 已完成
- [x] `pyproject.toml` 分层依赖 + `ruff`/`mypy`/`pytest` 配置
- [x] `config.py`（pydantic-settings）、`.env.example`、`.gitignore` 补全
- [x] `data/database.py`（单 engine + `get_session`）、`data/models.py`（修复主键）
- [x] Alembic 初始化 + 首版迁移（`migrations/`，初始迁移 `a00cd7251333`）；种子脚本 `data/seed.py`（经 `alembic upgrade head` 建表）
- [x] `auth_service` 骨架（bcrypt + JWT，密钥外置，限流占位）
- [x] `app/main.py` 最小可运行：登录页 + 路由 + 菜单
- [x] `.github/workflows/ci.yml`：ruff + pytest 自动跑（CI 绿）
- **交付**：能登录、能跑起来的空壳 + CI 绿 ✅

### 阶段 1 — 数据层与服务层（第 3–5 周）✅ 已完成
- [x] `repositories.py`：传感器/用户/日志取数（分页 + 时间窗）— 阶段 0/1 早期落地
- [x] `analysis_service`（已落地）；本次新增 `cleaning_service` / `anomaly_service`（从旧代码迁纯函数，修旧版 z-score 索引错位 bug）
- [x] `decision_service`（规则配置化、修趋势计算）— 早期落地
- [x] `prediction_service`（迁 Prophet/SARIMA，**去 torch/GPU**，naive 兜底，懒加载重依赖）— 早期落地
- [x] `st.cache_data` / `cache_resource` 接入热点查询与模型（`app/cache.py`：传感器时序查询 ttl=300、预测结果 ttl=600、`cached_llm_provider` 共享客户端；仪表板/预测页已接入，并移除废弃的 `use_container_width` → `width="stretch"`）
- [x] 上述 services 的 pytest 用例（`test_auth`/`test_analysis`/`test_decision`/`test_cleaning`/`test_anomaly`，共 24 例，CI 绿）
- **交付**：核心算法可单测、与 UI 解耦 ✅

### 阶段 2 — UI 移植（第 6–8 周）
- [ ] `pages/`：综合仪表板、数据概览（DB/上传/导出）、数据清洗、数据分析、可视化、高级分析
- [ ] 预测页（调用 `prediction_service`，异步 + 进度 + 置信区间）
- [ ] 决策引擎页（接通 `decision_service`）
- [ ] 实时预览改为定时轮询/可选 WebSocket（替代被禁用的旧模块）
- **交付**：功能对齐旧版（除 AI 洞察外）的可运行前端

### 阶段 3 — 系统管理与运维（第 9–10 周）
- [ ] 用户管理、系统监控、日志查看、模块配置（声明式注册 + DB 开关，消除矛盾入口）
- [ ] 备份/恢复（参数化 SQL + 显式 commit + AES 保留）
- [ ] `sync_manager` 抽象为接口（默认关闭）、`history_report_viewer`
- [ ] 使用说明 + 文档生成
- **交付**：管理闭环完整

### 阶段 4 — AI 洞察（无 Ollama，可插拔）（第 11 周，可选）
- [ ] `services/llm/`：`LLMProvider` 接口 + `openai_compatible` 实现（API Key 经配置注入）
- [ ] `report_service`：AI 解读结果归档为 md/json（与 `history_report_viewer` 对接）
- [ ] UI 页：可选开启；模型不可用时不报错、给出降级提示
- **交付**：用云 LLM（或自建兼容服务）替代本地 Ollama，无本地模型拉取

### 阶段 5 — 收尾与增强（第 12–14 周）
- [ ] 测试补全 + 覆盖率门禁；文档自动化（README 由代码生成，消除旧文档脱节）
- [ ] 部署：`Dockerfile` + `compose`（强密钥占位）、`deployment.yaml` 精简
- [ ] 可选：方案 B 统一时序表、多棚/多租户、开放 API（FastAPI 包装 services）

---

## 10. 关键决策与风险

- **Ollama 替代方案**：不引入任何本地模型。AI 洞察走 `LLMProvider` 抽象，默认实现为 OpenAI 兼容云 API；若后续要离线，可自行实现 `LocalProvider`（非 Ollama），与 UI/服务解耦。
- **torch 去留**：当前 `gpu_accelerator.py` 无真实训练链路，移除可显著缩减镜像；如确要 GPU 推理，单独列入 `ml` 依赖层并用 GPU 基础镜像。
- **JWT 改造**：Streamlit 下 Cookie 需注意反向代理后的 `st.context.headers`；多实例部署建议短期会话态 + 服务端 session store（Redis）。
- **预测阻塞**：长任务（Prophet/SARIMA）必须异步（后台线程/任务队列）+ 进度条 + 超时，否则卡死 UI。
- **数据库迁移**：旧 `intelligent_farm.sql` 等三份 dump 收敛为 Alembic 单一来源；历史数据用一次性导入脚本迁移。

---

## 附录：与旧代码对应关系（便于核对）

| 重写目标 | 旧代码位置 |
|---|---|
| `data/database.py` | `utils/database.py` + `auth.py:36-37`（去重） |
| `services/prediction_service.py` | `utils/predictions.py` + `utils/hybrid_prediction.py` |
| `services/decision_service.py` | `utils/decision_engine.py` |
| `services/analysis_service.py` | `utils/data_analysis.py` + `utils/analysis.py` + `utils/advanced_analysis.py` |
| `services/cleaning_service.py` | `utils/data_cleaning.py` + `utils/data_cleaning_ui.py` + `utils/anomaly_detection.py` |
| `services/llm/*` | 替代 `utils/ollama_chat.py` + `utils/ai_insights.py` |
| `app/auth_ui.py` | `auth.py`(UI 部分) |
| `app/pages/*` | `app.py` route_mapping 各分支 + 各 `utils/*_ui.py` |
