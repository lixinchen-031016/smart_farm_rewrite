"""智慧大棚数据管理平台（从 0 重写）。

分层结构：
- config: 类型化配置（pydantic-settings）
- data:   ORM 模型 / 数据库连接 / 仓库（取数）
- services: 纯业务逻辑（无 st.*，可单测、可复用、可 API 化）
- app:    Streamlit UI（仅渲染 + 调用 services）
"""

__version__ = "0.1.0"
