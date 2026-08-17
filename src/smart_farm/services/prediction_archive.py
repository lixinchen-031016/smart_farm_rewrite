"""预测结果自动保存与归档服务（纯 Python，无 Streamlit 依赖）。

对齐旧版 `utils/prediction_auto_save.py`：
- 每次预测自动保存：CSV（utf-8-sig）+ Markdown 报告 + SQLite 历史记录
- ID 格式：`{预测类型}_{YYYYMMDD_HHMMSS}_{8位随机hex}`
- 历史上限 1000 条（超出删除最旧）
- 目录默认项目根 `predictions_exports/`，可通过 `base_dir` 注入（便于测试）
"""

import json
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import pandas as pd

MAX_HISTORY_RECORDS = 1000


def _default_exports_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "predictions_exports"


class PredictionArchive:
    """预测归档管理器（CSV + Markdown + SQLite）。"""

    def __init__(self, base_dir: Optional[Path] = None, max_records: int = MAX_HISTORY_RECORDS):
        self.base_dir = Path(base_dir) if base_dir else _default_exports_dir()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.max_records = max_records
        self._lock = threading.Lock()

    @property
    def history_db_path(self) -> Path:
        return self.base_dir / "prediction_history.db"

    # ----------------------------- 数据库 -----------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.history_db_path)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS prediction_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prediction_id TEXT UNIQUE NOT NULL,
                prediction_type TEXT NOT NULL,
                model_type TEXT NOT NULL,
                prediction_days INTEGER,
                created_at TEXT NOT NULL,
                rmse REAL DEFAULT 0,
                r_squared REAL DEFAULT 0,
                data_points INTEGER DEFAULT 0,
                file_path TEXT,
                metadata TEXT,
                status TEXT DEFAULT 'ok'
            )
            """
        )
        conn.commit()
        return conn

    @staticmethod
    def _generate_prediction_id(prediction_type: str) -> str:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        suffix = os.urandom(4).hex()
        return f"{prediction_type}_{ts}_{suffix}"

    # ----------------------------- 保存 -----------------------------

    def save_prediction_result(
        self,
        historical_data: pd.DataFrame,
        forecast_data: pd.DataFrame,
        prediction_type: str,
        model_type: str,
        prediction_days: int,
        model_explanation: str = "",
        rmse: float = 0.0,
        r_squared: float = 0.0,
        additional_metrics: Optional[dict[str, Any]] = None,
        username: str = "system",
    ) -> dict[str, str]:
        """保存一次预测结果：CSV + MD + SQLite。返回 {status, prediction_id, csv_path, markdown_path, timestamp}。"""
        with self._lock:
            prediction_id = self._generate_prediction_id(prediction_type)
            timestamp = datetime.now()

            csv_path = self._save_to_csv(historical_data, forecast_data, prediction_id)
            md_path = self._save_to_markdown(
                historical_data, forecast_data, prediction_id, prediction_type,
                model_type, prediction_days, model_explanation, rmse, r_squared,
                additional_metrics, username,
            )

            self._insert_history(
                prediction_id=prediction_id,
                prediction_type=prediction_type,
                model_type=model_type,
                prediction_days=prediction_days,
                created_at=timestamp.isoformat(),
                rmse=rmse,
                r_squared=r_squared,
                data_points=len(historical_data),
                file_path=str(csv_path),
                metadata=json.dumps(additional_metrics or {}, ensure_ascii=False),
            )
            self._cleanup_old_records()

            return {
                "status": "ok",
                "prediction_id": prediction_id,
                "csv_path": str(csv_path),
                "markdown_path": str(md_path),
                "timestamp": timestamp.isoformat(),
            }

    def _save_to_csv(
        self, historical_data: pd.DataFrame, forecast_data: pd.DataFrame, prediction_id: str
    ) -> Path:
        path = self.base_dir / f"prediction_{prediction_id}.csv"
        hist = historical_data.copy()
        fc = forecast_data.copy()
        hist["data_type"] = "historical"
        fc["data_type"] = "forecast"
        hist["prediction_id"] = prediction_id
        fc["prediction_id"] = prediction_id
        created_at = datetime.now().isoformat()
        hist["created_at"] = created_at
        fc["created_at"] = created_at
        combined = pd.concat([hist, fc], ignore_index=True)
        combined.to_csv(path, index=False, encoding="utf-8-sig")
        return path

    def _save_to_markdown(
        self,
        historical_data: pd.DataFrame,
        forecast_data: pd.DataFrame,
        prediction_id: str,
        prediction_type: str,
        model_type: str,
        prediction_days: int,
        model_explanation: str,
        rmse: float,
        r_squared: float,
        additional_metrics: Optional[dict[str, Any]],
        username: str,
    ) -> Path:
        path = self.base_dir / f"prediction_{prediction_id}.md"
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            f"# 预测报告 {prediction_id}",
            "",
            "| 项目 | 值 |",
            "|---|---|",
            f"| 预测类型 | {prediction_type} |",
            f"| 模型 | {model_type} |",
            f"| 预测天数 | {prediction_days} |",
            f"| 数据点数 | {len(historical_data)} |",
            f"| RMSE | {rmse:.4f} |",
            f"| R² | {r_squared:.4f} |",
            f"| 用户 | {username} |",
            f"| 时间 | {now} |",
            "",
            "## 模型说明",
            "",
            model_explanation or "（无）",
            "",
            "## 历史数据统计",
            "",
        ]
        hist_stats = historical_data.select_dtypes(include="number").describe().round(3)
        try:
            lines.append(hist_stats.to_markdown())
        except ImportError:
            lines.append(str(hist_stats))
        lines.append("")
        lines.append("## 预测结果预览（前 5 条）")
        lines.append("")
        try:
            lines.append(forecast_data.head(5).to_markdown())
        except ImportError:
            lines.append(str(forecast_data.head(5)))
        lines.append("")
        lines.append(f"CSV 文件：`prediction_{prediction_id}.csv`")
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    def _insert_history(self, **kwargs) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO prediction_history
                (prediction_id, prediction_type, model_type, prediction_days,
                 created_at, rmse, r_squared, data_points, file_path, metadata, status)
                VALUES (:prediction_id, :prediction_type, :model_type, :prediction_days,
                        :created_at, :rmse, :r_squared, :data_points, :file_path, :metadata, :status)
                """,
                {**kwargs, "status": "ok"},
            )
            conn.commit()
        finally:
            conn.close()

    def _cleanup_old_records(self) -> None:
        conn = self._connect()
        try:
            count = conn.execute("SELECT COUNT(*) FROM prediction_history").fetchone()[0]
            if count > self.max_records:
                excess = count - self.max_records
                conn.execute(
                    """
                    DELETE FROM prediction_history WHERE id IN (
                        SELECT id FROM prediction_history ORDER BY created_at ASC LIMIT ?
                    )
                    """,
                    (excess,),
                )
                conn.commit()
        finally:
            conn.close()

    # ----------------------------- 查询 -----------------------------

    def get_prediction_history(
        self,
        prediction_type: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            sql = "SELECT * FROM prediction_history WHERE 1=1"
            params: list[Any] = []
            if prediction_type:
                sql += " AND prediction_type = ?"
                params.append(prediction_type)
            if start_date:
                sql += " AND created_at >= ?"
                params.append(start_date)
            if end_date:
                sql += " AND created_at <= ?"
                params.append(end_date)
            sql += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            cols = [d[0] for d in conn.execute("SELECT * FROM prediction_history LIMIT 0").description]
            return [dict(zip(cols, r)) for r in rows]
        finally:
            conn.close()

    def get_statistics(self) -> dict[str, Any]:
        conn = self._connect()
        try:
            total = conn.execute("SELECT COUNT(*) FROM prediction_history").fetchone()[0]
            from datetime import datetime, timedelta

            since7 = (datetime.now() - timedelta(days=7)).isoformat()
            recent7 = conn.execute(
                "SELECT COUNT(*) FROM prediction_history WHERE created_at >= ?", (since7,)
            ).fetchone()[0]
            by_type_rows = conn.execute(
                "SELECT prediction_type, COUNT(*) FROM prediction_history GROUP BY prediction_type"
            ).fetchall()
            avg_rmse = conn.execute(
                "SELECT AVG(rmse) FROM prediction_history WHERE rmse > 0"
            ).fetchone()[0]
            return {
                "total_predictions": total,
                "recent_predictions_7d": recent7,
                "avg_rmse": round(avg_rmse, 4) if avg_rmse else 0.0,
                "by_type": {t: c for t, c in by_type_rows},
            }
        finally:
            conn.close()

    def delete_prediction(self, prediction_id: str) -> bool:
        """删除一条历史记录（含关联文件）。"""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT file_path FROM prediction_history WHERE prediction_id = ?", (prediction_id,)
            ).fetchone()
            if row and row[0]:
                p = Path(row[0])
                for candidate in [p, p.with_suffix(".md")]:
                    if candidate.exists():
                        candidate.unlink()
            conn.execute("DELETE FROM prediction_history WHERE prediction_id = ?", (prediction_id,))
            conn.commit()
            return True
        finally:
            conn.close()


archive = PredictionArchive()
