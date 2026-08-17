"""异常检测服务（纯函数，无 Streamlit 依赖，可单测）。

把旧版 `utils/anomaly_detection.py` 的算法抽离为纯 Python：
- IQR / Z-Score / 孤立森林(IsolationForest, 懒加载 sklearn)
- 均返回布尔掩码或 {列名: 异常索引列表}，无副作用

注意：旧版 `detect_outliers_zscore` 存在索引错位 bug（对 dropna 后的子序列算
z 分数，却用原 df 的 `isin` 去匹配），本版改为基于位置对齐再回填掩码，已修正。
"""

from typing import Any, Optional

import numpy as np
import pandas as pd


def detect_outliers_iqr(
    df: pd.DataFrame, column: str, factor: float = 1.5
) -> pd.Series:
    """IQR 四分位距法：返回布尔掩码，True 表示异常值。

    Args:
        df: 数据集。
        column: 目标列名。
        factor: IQR 倍数，默认 1.5（常用 1.5 / 3.0）。
    """
    col = df[column]
    q1 = col.quantile(0.25)
    q3 = col.quantile(0.75)
    iqr = q3 - q1
    lower = q1 - factor * iqr
    upper = q3 + factor * iqr
    return (col < lower) | (col > upper)


def detect_outliers_zscore(
    df: pd.DataFrame, column: str, threshold: float = 3.0
) -> pd.Series:
    """Z-Score 法：基于列均值/标准差，返回布尔掩码，True 表示异常值。

    对非有限值（NaN/inf）不做标记，避免污染结果。
    """
    col = df[column]
    vals = col.to_numpy(dtype=float)
    finite = np.isfinite(vals)
    mask = np.zeros(len(col), dtype=bool)
    if finite.sum() > 1:
        mean = vals[finite].mean()
        std = vals[finite].std()
        if std and std > 0:
            z = np.abs((vals - mean) / std)
            mask = (z > threshold) & finite
    return pd.Series(mask, index=df.index)


def detect_outliers_isolation_forest(
    df: pd.DataFrame,
    columns: Optional[list[str]] = None,
    contamination: Any = "auto",
    n_estimators: int = 100,
    max_samples: Any = "auto",
    random_state: int = 42,
) -> pd.Series:
    """孤立森林（懒加载 sklearn）。返回布尔掩码，True 表示异常值。

    适用于多维联合异常检测。未安装 scikit-learn 时抛出明确异常。
    """
    try:
        from sklearn.ensemble import IsolationForest
        from sklearn.preprocessing import StandardScaler
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "未安装 scikit-learn。请运行 `uv pip install -e '.[ml]'` 后重试。"
        ) from exc

    if columns is None:
        columns = df.select_dtypes(include=[np.number]).columns.tolist()
    if not columns:
        return pd.Series(False, index=df.index)

    subset = df[columns].select_dtypes(include=[np.number])
    clean = subset.dropna()
    if clean.empty:
        return pd.Series(False, index=df.index)

    if isinstance(max_samples, float) and 0 < max_samples <= 1.0:
        max_samples = int(max_samples * len(clean))

    scaled = StandardScaler().fit_transform(clean)
    model = IsolationForest(
        contamination=contamination,
        n_estimators=n_estimators,
        max_samples=max_samples,
        random_state=random_state,
    )
    preds = model.fit_predict(scaled)
    # 修复：按位置回填掩码（isin 在重复索引时会把同标签所有行误标为异常）
    outlier_positions = [clean.index.get_loc(idx) for idx in clean.index[preds == -1]]
    mask = pd.Series(False, index=df.index)
    if outlier_positions:
        mask.iloc[outlier_positions] = True
    return mask


def detect_anomalies(
    df: pd.DataFrame, method: str = "iqr", **kwargs: Any
) -> dict[str, list]:
    """对全部数值列做异常检测，返回 {列名: 异常行索引列表}。

    Args:
        df: 数据集。
        method: 'iqr' | 'zscore' | 'isolation_forest'。
        **kwargs: 方法专用参数（如 zscore 的 threshold、iqr 的 factor）。
    """
    method = (method or "iqr").lower()
    numeric_cols = list(df.select_dtypes(include=[np.number]).columns)

    if method == "isolation_forest":
        if not numeric_cols:
            return {}
        mask = detect_outliers_isolation_forest(df, numeric_cols, **kwargs)
        idx = df.index[mask].tolist()
        return {col: idx for col in numeric_cols}

    result: dict[str, list] = {}
    for col in numeric_cols:
        if method == "zscore":
            mask = detect_outliers_zscore(df, col, kwargs.get("threshold", 3.0))
        else:  # iqr
            mask = detect_outliers_iqr(df, col, kwargs.get("factor", 1.5))
        result[col] = df.index[mask].tolist()
    return result


def remove_anomalies(df: pd.DataFrame, anomalies: dict[str, list]) -> pd.DataFrame:
    """按异常索引字典删除行，返回重置索引后的新 DataFrame。"""
    idx: set[int] = set()
    for indices in anomalies.values():
        idx.update(indices)
    return df.drop(index=list(idx)).reset_index(drop=True)


def get_anomaly_summary(
    anomalies: dict[str, list], total_rows: Optional[int] = None
) -> dict[str, dict[str, float]]:
    """汇总每列异常数量与占比。

    若未提供 total_rows，则以所有异常索引的并集大小作为分母。
    """
    if total_rows is None:
        union: set[int] = set()
        for indices in anomalies.values():
            union.update(indices)
        total_rows = len(union)
    summary: dict[str, dict[str, float]] = {}
    for col, indices in anomalies.items():
        count = len(indices)
        summary[col] = {
            "count": count,
            "percentage": round(count / total_rows * 100, 2) if total_rows else 0.0,
        }
    return summary
