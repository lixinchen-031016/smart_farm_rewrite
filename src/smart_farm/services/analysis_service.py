"""数据分析服务（纯函数，无 Streamlit 依赖，可单测）。

把旧版 `utils/data_analysis.py` / `analysis.py` / `advanced_analysis.py` 中的
算法抽离为纯 Python，UI 层只负责调用与渲染。
"""

from typing import Optional

import numpy as np
import pandas as pd


def describe_data(df: pd.DataFrame) -> pd.DataFrame:
    """对数值列做描述性统计。"""
    numeric = df.select_dtypes(include=[np.number])
    if numeric.empty:
        return pd.DataFrame()
    return numeric.describe(include="all").round(3)


def calculate_correlation(df: pd.DataFrame) -> Optional[pd.DataFrame]:
    """数值列相关性矩阵；无数值列返回 None。"""
    numeric = df.select_dtypes(include=[np.number])
    if numeric.shape[1] < 2:
        return None
    return numeric.corr().round(3)


_AGG_FUNCS = {
    "平均值": "mean",
    "总和": "sum",
    "最大值": "max",
    "最小值": "min",
}


def group_and_aggregate(
    df: pd.DataFrame, group_column: str, agg_column: str, agg_function: str
) -> pd.DataFrame:
    """按分组列对数值列做聚合，返回带中文列名的结果。"""
    if group_column not in df.columns or agg_column not in df.columns:
        raise ValueError("分组列或聚合列不存在")
    if agg_column not in df.select_dtypes(include=[np.number]).columns:
        raise ValueError("聚合列必须是数值列")

    func = _AGG_FUNCS.get(agg_function, "mean")
    grouped = df.groupby(group_column, dropna=False)[agg_column].agg(func).reset_index()
    grouped.columns = [group_column, f"{agg_column}_{agg_function}"]
    return grouped
