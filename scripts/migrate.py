"""数据库迁移命令行工具。

用法：
    python scripts/migrate.py upgrade        # 升级到最新
    python scripts/migrate.py downgrade base # 回滚到初始
    python scripts/migrate.py revision -m "说明"  # 生成新迁移（对比模型）
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402


def _config() -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    return cfg


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)

    action = sys.argv[1]
    cfg = _config()

    if action == "upgrade":
        command.upgrade(cfg, sys.argv[2] if len(sys.argv) > 2 else "head")
    elif action == "downgrade":
        command.downgrade(cfg, sys.argv[2] if len(sys.argv) > 2 else "base")
    elif action == "revision":
        # 透传剩余参数（如 -m "说明"），autogenerate 由脚本固定开启
        message = None
        extra: list[str] = []
        args = sys.argv[2:]
        i = 0
        while i < len(args):
            if args[i] == "-m" and i + 1 < len(args):
                message = args[i + 1]
                i += 2
            elif args[i].startswith("--message") and "=" in args[i]:
                message = args[i].split("=", 1)[1]
                i += 1
            else:
                extra.append(args[i])
                i += 1
        command.revision(cfg, message=message, autogenerate=True, *extra)
    else:
        print(f"未知命令: {action}\n{__doc__}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
