"""数据清洗服务（纯函数，无 Streamlit 依赖，可单测）。

把旧版 `utils/data_cleaning.py` 的算法抽离为纯 Python：
- 去重 / 删列 / 缺失值填补（mean / median / mode / constant / 删除行）
- 提供声明式 `CleaningConfig` 与 `clean_dataframe()` 统一入口
- 所有函数返回 (新 DataFrame, 操作报告)，无副作用，便于 UI 渲染与单测
"""

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

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
