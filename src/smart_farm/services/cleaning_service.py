"""数据清洗服务（纯函数，无 Streamlit 依赖，可单测）。

把旧版 `utils/data_cleaning.py` 的算法抽离为纯 Python：
- 去重 / 删列 / 缺失值填补（mean / median / mode / constant / 删除行）
- 提供声明式 `CleaningConfig` 与 `clean_dataframe()` 统一入口
- 规则引擎 `DataCleaningRule` + `DataCleaner` + 模板（农业标准 / ML / 自定义）
- 所有函数返回 (新 DataFrame, 操作报告)，无副作用，便于 UI 渲染与单测
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from smart_farm.services import anomaly_service as an

_MISSING_METHODS = {"mean", "median", "mode", "constant", "drop"}


@dataclass
class CleaningConfig:
    """声明式清洗配置：UI 层组装，服务层执行。

    - drop_duplicates: 是否删除完全重复的行。
    - drop_columns: 需要删除的列名列表。
    - missing: {列名: (方法, 填充值)}，方法 ∈ mean/median/mode/constant/drop。
    """

    drop_duplicates: bool = False
    drop_columns: list[str] = field(default_factory=list)
    missing: dict[str, tuple[str, Any]] = field(default_factory=dict)


def fill_missing(
    df: pd.DataFrame,
    column: str,
    method: str,
    fill_value: Any = None,
) -> tuple[pd.DataFrame, dict]:
    """对单列做缺失值处理。返回 (新 DataFrame, 操作报告)。

    Args:
        df: 数据集。
        column: 目标列名。
        method: mean | median | mode | constant | drop。
        fill_value: method='constant' 时的填充值。
    """
    report = {"column": column, "method": method, "filled": 0, "dropped_rows": 0}
    if column not in df.columns:
        report["error"] = f"列 {column} 不存在"
        return df.copy(), report

    missing = int(df[column].isnull().sum())
    if missing == 0:
        report["note"] = "无缺失值"
        return df.copy(), report

    if method == "drop":
        out = df.dropna(subset=[column]).reset_index(drop=True)
        report["dropped_rows"] = len(df) - len(out)
        return out, report

    if method == "constant":
        value: Any = fill_value
    elif method == "mean":
        value = float(df[column].mean())
    elif method == "median":
        value = float(df[column].median())
    elif method == "mode":
        modes = df[column].mode()
        value = modes.iloc[0] if not modes.empty else fill_value
    else:
        raise ValueError(f"未知缺失值处理方法：{method}")

    out = df.copy()
    out[column] = out[column].fillna(value)
    report["filled"] = missing
    report["fill_value"] = value
    return out, report


def clean_dataframe(
    df: pd.DataFrame, config: CleaningConfig
) -> tuple[pd.DataFrame, dict]:
    """按配置清洗数据，返回 (清洗后 DataFrame, 清洗报告)。"""
    report = {"original_shape": df.shape, "operations": [], "final_shape": None}
    out = df.copy()

    if config.drop_duplicates:
        before = len(out)
        out = out.drop_duplicates().reset_index(drop=True)
        removed = before - len(out)
        if removed:
            report["operations"].append({"type": "drop_duplicates", "removed": removed})

    if config.drop_columns:
        existing = [c for c in config.drop_columns if c in out.columns]
        if existing:
            out = out.drop(columns=existing)
            report["operations"].append({"type": "drop_columns", "columns": existing})

    for column, (method, value) in config.missing.items():
        if column not in out.columns:
            continue
        out, sub = fill_missing(out, column, method, value)
        if sub.get("dropped_rows"):
            report["operations"].append({"type": "drop_missing_rows", **sub})
        elif sub.get("filled"):
            report["operations"].append({"type": "fill_missing", **sub})

    report["final_shape"] = out.shape
    return out, report


def assess_quality(df: pd.DataFrame) -> dict:
    """评估数据质量：行数、列数、缺失单元格数、完整率、逐列缺失率。"""
    total_cells = int(df.size)
    missing_cells = int(df.isnull().sum().sum())
    completeness = 100.0 - (missing_cells / total_cells * 100) if total_cells else 100.0
    return {
        "total_rows": len(df),
        "total_columns": df.shape[1],
        "missing_cells": missing_cells,
        "missing_rate": round(missing_cells / total_cells * 100, 2) if total_cells else 0.0,
        "completeness": round(completeness, 2),
        "by_column": {
            c: {
                "count": int(df[c].isnull().sum()),
                "percentage": round(df[c].isnull().sum() / len(df) * 100, 2) if len(df) else 0.0,
            }
            for c in df.columns
        },
    }


# ----------------------------- 规则引擎（对齐旧版 utils/data_cleaning.py） -----------------------------


@dataclass
class DataCleaningRule:
    """声明式清洗规则（对齐旧版 DataCleaningRule）。

    - rules.duplicate_removal: bool
    - rules.column_dropping: list[str]
    - rules.missing_value_handling: {列: {"method": str, "fill_value": Any}}
    - rules.outlier_detection: {"method": str, "params": dict} | None
    """

    name: str
    description: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    rules: dict[str, Any] = field(
        default_factory=lambda: {
            "duplicate_removal": False,
            "column_dropping": [],
            "missing_value_handling": None,
            "outlier_detection": None,
        }
    )

    def configure_outlier_detection(self, method: str, params: dict[str, Any]) -> None:
        self.rules["outlier_detection"] = {"method": method, "params": params}

    def configure_missing_value(self, column: str, method: str, fill_value: Any = None) -> None:
        if self.rules["missing_value_handling"] is None:
            self.rules["missing_value_handling"] = {}
        self.rules["missing_value_handling"][column] = {"method": method, "fill_value": fill_value}

    def set_duplicate_removal(self, enabled: bool) -> None:
        self.rules["duplicate_removal"] = enabled

    def add_column_to_drop(self, column: str) -> None:
        if column not in self.rules["column_dropping"]:
            self.rules["column_dropping"].append(column)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "created_at": self.created_at.isoformat(),
            "rules": self.rules,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DataCleaningRule":
        rule = cls(data["name"], data.get("description", ""))
        try:
            rule.created_at = datetime.fromisoformat(data["created_at"])
        except (KeyError, ValueError):
            pass
        rule.rules = data["rules"]
        return rule

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


class DataCleaner:
    """清洗执行器（对齐旧版 DataCleaner.apply_rule + generate_report）。"""

    def __init__(self) -> None:
        self.statistics = {"rows_removed": 0, "values_filled": 0, "outliers_removed": 0}

    def apply_rule(self, df: pd.DataFrame, rule: DataCleaningRule) -> tuple[pd.DataFrame, dict]:
        """按规则清洗，返回 (清洗后 DataFrame, 报告)。"""
        report: dict[str, Any] = {
            "original_shape": df.shape,
            "operations": [],
            "quality_before": assess_quality(df),
            "quality_after": None,
        }
        out = df.copy()

        # 1. 删除重复行
        if rule.rules["duplicate_removal"]:
            before = len(out)
            out = out.drop_duplicates().reset_index(drop=True)
            removed = before - len(out)
            if removed > 0:
                report["operations"].append({"type": "duplicate_removal", "removed_rows": removed})
                self.statistics["rows_removed"] += removed

        # 2. 删除指定列
        if rule.rules["column_dropping"]:
            cols = [c for c in rule.rules["column_dropping"] if c in out.columns]
            if cols:
                out = out.drop(columns=cols)
                report["operations"].append({"type": "column_dropping", "columns": cols})

        # 3. 缺失值处理
        missing_cfg = rule.rules["missing_value_handling"] or {}
        for column, config in missing_cfg.items():
            if column not in out.columns:
                continue
            method = config["method"]
            if method == "delete":
                before = len(out)
                out = out.dropna(subset=[column]).reset_index(drop=True)
                removed = before - len(out)
                if removed > 0:
                    report["operations"].append(
                        {"type": "missing_value_deletion", "column": column, "removed_rows": removed}
                    )
                    self.statistics["rows_removed"] += removed
            elif method in ("mean", "median", "mode"):
                if method == "mean":
                    fill_value: Any = float(out[column].mean())
                elif method == "median":
                    fill_value = float(out[column].median())
                else:
                    modes = out[column].mode()
                    fill_value = modes.iloc[0] if not modes.empty else 0
                missing_count = int(out[column].isnull().sum())
                if missing_count > 0:
                    out[column] = out[column].fillna(fill_value)
                    report["operations"].append(
                        {
                            "type": "missing_value_imputation",
                            "column": column,
                            "method": method,
                            "filled_count": missing_count,
                            "fill_value": fill_value,
                        }
                    )
                    self.statistics["values_filled"] += missing_count

        # 4. 异常值检测与处理
        outlier_cfg = rule.rules["outlier_detection"]
        if outlier_cfg:
            method = outlier_cfg["method"]
            params = outlier_cfg.get("params", {})
            numeric_cols = params.get(
                "columns", out.select_dtypes(include=[np.number]).columns.tolist()
            )
            numeric_cols = [c for c in numeric_cols if c in out.columns]

            anomalies: dict[str, list] = {}
            if method == "iqr":
                for col in numeric_cols:
                    mask = an.detect_outliers_iqr(out, col)
                    anomalies[col] = out.index[mask].tolist()
            elif method == "zscore":
                threshold = params.get("threshold", 3.0)
                for col in numeric_cols:
                    mask = an.detect_outliers_zscore(out, col, threshold)
                    anomalies[col] = out.index[mask].tolist()
            elif method == "isolation_forest":
                if_params = {
                    k: v
                    for k, v in params.items()
                    if k in ("contamination", "n_estimators", "max_samples", "random_state")
                }
                mask = an.detect_outliers_isolation_forest(out, numeric_cols, **if_params)
                for col in numeric_cols:
                    anomalies[col] = out.index[mask].tolist()

            if anomalies:
                if params.get("remove", False):
                    before = len(out)
                    out = an.remove_anomalies(out, anomalies)
                    removed = before - len(out)
                    report["operations"].append(
                        {"type": "outlier_removal", "method": method, "removed_rows": removed}
                    )
                    self.statistics["outliers_removed"] += removed
                else:
                    report["outliers"] = anomalies
                    report["anomaly_summary"] = an.get_anomaly_summary(anomalies)

        report["quality_after"] = assess_quality(out)
        return out, report

    def generate_report(self, report: dict) -> str:
        """把清洗报告渲染为可读文本（对齐旧版 generate_report）。"""
        lines = [f"原数据形状: {report['original_shape']}"]
        qb = report["quality_before"]
        qa = report["quality_after"]
        if qa:
            lines.append(
                f"清洗后形状: {qa['total_rows']} 行 x {qa['total_columns']} 列 "
                f"(完整率 {qa['completeness']}% vs 原 {qb['completeness']}%)"
            )
        for op in report["operations"]:
            t = op["type"]
            if t == "duplicate_removal":
                lines.append(f"✓ 删除重复行: {op['removed_rows']} 行")
            elif t == "column_dropping":
                lines.append(f"✓ 删除列: {', '.join(op['columns'])}")
            elif t == "missing_value_deletion":
                lines.append(f"✓ 删除 {op['column']} 缺失行: {op['removed_rows']} 行")
            elif t == "missing_value_imputation":
                lines.append(
                    f"✓ 填充 {op['column']} 列缺失值 ({op['method']}): {op['filled_count']} 个值"
                )
            elif t == "outlier_removal":
                lines.append(f"✓ 移除异常值 ({op['method']}): {op['removed_rows']} 行")
        if report.get("outliers"):
            lines.append(f"⚠ 检出异常值但未删除: {sum(len(v) for v in report['outliers'].values())} 个")
        return "\n".join(lines)


def create_template_rule(template_name: str, df: pd.DataFrame) -> DataCleaningRule:
    """基于数据分析自动创建清洗模板（对齐旧版 create_template_rule）。

    规则：缺失率 >50% 删列；5%-50% 数值列 median / 分类列 mode；默认去重。
    """
    rule = DataCleaningRule(
        name=template_name,
        description=f"基于{len(df)}行数据自动生成的清洗模板",
    )
    rule.set_duplicate_removal(True)
    for col in df.columns:
        missing_rate = df[col].isnull().sum() / len(df)
        if missing_rate > 0.5:
            rule.add_column_to_drop(col)
        elif missing_rate > 0.05:
            if df[col].dtype in (np.float64, np.int64):
                rule.configure_missing_value(col, "median")
            else:
                rule.configure_missing_value(col, "mode")
    return rule


def create_agricultural_standard_template() -> DataCleaningRule:
    """农业数据标准清洗模板（对齐旧版）：IQR 异常移除 + 常见列 median 填充 + 去重。"""
    rule = DataCleaningRule(
        name="农业数据标准清洗流程",
        description="适用于智能农场传感器数据的标准清洗流程",
    )
    rule.set_duplicate_removal(True)
    rule.configure_outlier_detection("iqr", {"columns": [], "remove": True})
    for col in ("temperature", "humidity", "soil_moisture", "soil_nutrient", "light_intensity"):
        rule.configure_missing_value(col, "median")
    return rule


def create_machine_learning_template(df: pd.DataFrame) -> DataCleaningRule:
    """机器学习清洗模板（对齐旧版）：孤立森林异常移除 + 全数值列 median 填充 + 去重。"""
    rule = DataCleaningRule(
        name="机器学习数据清洗模板",
        description="为机器学习模型准备的高质量数据集",
    )
    rule.set_duplicate_removal(True)
    rule.configure_outlier_detection(
        "isolation_forest",
        {"contamination": 0.1, "n_estimators": 100, "max_samples": "auto", "remove": True},
    )
    for col in df.select_dtypes(include=[np.number]).columns:
        rule.configure_missing_value(col, "median")
    return rule


def fill_missing_with_flag(df: pd.DataFrame, column: str, method: str) -> tuple[pd.DataFrame, dict]:
    """填充缺失值并新增 `{列}_filled` 标识列（对齐旧版 data_cleaning_ui）。

    标识列记录 "行号:xxx | 类型:xxx | 填充值:xxx"；环境类型按列名关键词推断。
    """
    out = df.copy()
    if column not in out.columns:
        return out, {"error": f"列 {column} 不存在"}

    missing_index = out[column].isnull()
    if not missing_index.any():
        return out, {"column": column, "filled": 0, "note": "无缺失值"}

    if method == "mean":
        fill_value = float(out[column].mean())
    elif method == "median":
        fill_value = float(out[column].median())
    elif method == "mode":
        modes = out[column].mode()
        fill_value = modes.iloc[0] if not modes.empty else 0
    else:
        raise ValueError(f"未知填充方法：{method}")

    # 环境类型推断（对齐旧版）
    env_type = "其他"
    lowered = column.lower()
    if "temperature" in lowered:
        env_type = "空气温度"
    elif "humidity" in lowered:
        env_type = "空气湿度"
    elif "soil" in lowered:
        env_type = "土壤数据"
    elif "light" in lowered:
        env_type = "光照强度"

    flag_col = f"{column}_filled"
    out[flag_col] = ""  # object 列，非缺失行为空字符串
    flag_values = {
        idx: f"行号:{idx} | 类型:{env_type} | 填充值:{fill_value:.2f}"
        for idx in missing_index[missing_index].index
    }
    out.loc[missing_index, column] = fill_value
    out.loc[list(flag_values), flag_col] = list(flag_values.values())
    return out, {
        "column": column,
        "method": method,
        "filled": int(missing_index.sum()),
        "fill_value": fill_value,
        "flag_column": flag_col,
    }
