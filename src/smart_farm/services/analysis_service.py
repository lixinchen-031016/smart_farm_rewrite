"""数据分析服务（纯函数，无 Streamlit 依赖，可单测）。

把旧版 `utils/data_analysis.py` / `analysis.py` / `advanced_analysis.py` 中的
算法抽离为纯 Python，UI 层只负责调用与渲染。

`explain_*` / `provide_smart_insights` 返回**解读文本列表**（而非直接 st 输出），
便于单测与 UI 灵活渲染。
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
    """按分组列对数值列做聚合，返回带中文列名的结果。

    修复：非法聚合函数抛出 ValueError（旧版静默回退 mean，展示名与实际不符）。
    """
    if group_column not in df.columns or agg_column not in df.columns:
        raise ValueError("分组列或聚合列不存在")
    if agg_column not in df.select_dtypes(include=[np.number]).columns:
        raise ValueError("聚合列必须是数值列")
    if agg_function not in _AGG_FUNCS:
        raise ValueError(f"不支持的聚合方式：{agg_function}（可选 {list(_AGG_FUNCS.keys())}）")

    func = _AGG_FUNCS[agg_function]
    grouped = df.groupby(group_column, dropna=False)[agg_column].agg(func).reset_index()
    grouped.columns = [group_column, f"{agg_column}_{agg_function}"]
    return grouped


# ----------------------------- 智能解读（对齐旧版 enhanced_analysis） -----------------------------

# 农业指标解读阈值（对齐旧版）
_METRIC_RULES = {
    "temperature": {"low": 15, "high": 30, "unit": "°C"},
    "humidity": {"low": 40, "high": 70, "unit": "%"},
    "moisture": {"low": 30, "high": 60, "unit": ""},
    "soil_nutrient": {"low": 10, "high": 20, "unit": "ppm"},
    "light": {"low": 1000, "high": None, "unit": "lux"},
}


def _match_metric(col: str) -> Optional[str]:
    """按列名关键词匹配农业指标类型（对齐旧版 if/elif 链）。"""
    lowered = col.lower()
    if "temperature" in lowered or "温" in col:
        return "temperature"
    if "humidity" in lowered or "湿" in col:
        return "humidity"
    if "moisture" in lowered or "土壤" in col:
        return "moisture"
    if "soil_nutrient" in lowered or "养分" in col or "nutrient" in lowered:
        return "soil_nutrient"
    if "light" in lowered or "光照" in col:
        return "light"
    return None


def explain_descriptive_statistics(df: pd.DataFrame, desc_stats: pd.DataFrame) -> list[str]:
    """描述统计中文解读（对齐旧版 explain_descriptive_statistics）。"""
    lines: list[str] = []
    lines.append(f"共有 {df.shape[0]} 个数据记录，涵盖 {df.shape[1]} 个不同指标")
    numeric = df.select_dtypes(include=[np.number]).columns
    if not len(numeric) or desc_stats.empty:
        return lines

    for col in numeric:
        if col not in desc_stats.columns:
            continue
        try:
            mean_val = float(desc_stats.loc["mean", col])
            std_val = float(desc_stats.loc["std", col])
            min_val = float(desc_stats.loc["min", col])
            max_val = float(desc_stats.loc["max", col])
        except KeyError:
            continue
        lines.append(f"{col}：均值 {mean_val:.2f}，标准差 {std_val:.2f}，范围 {min_val:.2f} ~ {max_val:.2f}")

        metric = _match_metric(col)
        if metric == "temperature":
            if mean_val < 15:
                lines.append("  温度偏低，可能需要注意保温措施")
            elif mean_val > 30:
                lines.append("  温度偏高，可能需要注意通风降温")
            else:
                lines.append("  温度适中，适合大多数作物生长")
        elif metric == "humidity":
            if mean_val < 40:
                lines.append("  湿度偏低，可能需要增加喷雾或浇水")
            elif mean_val > 70:
                lines.append("  湿度偏高，可能需要注意通风除湿")
            else:
                lines.append("  湿度适中，有利于作物健康生长")
        elif metric == "moisture":
            if mean_val < 30:
                lines.append("  土壤偏干，建议及时灌溉")
            elif mean_val > 60:
                lines.append("  土壤偏湿，可能需要适当晾晒")
            else:
                lines.append("  土壤湿度适宜，作物生长良好")
        elif metric == "soil_nutrient":
            if mean_val < 10:
                lines.append("  土壤养分偏低，建议补充肥料")
            elif mean_val > 20:
                lines.append("  土壤养分偏高，建议控制施肥")
            else:
                lines.append("  土壤养分适宜")
        elif metric == "light":
            if mean_val < 1000:
                lines.append("  光照强度不足，可能需要补光")
            else:
                lines.append("  光照强度充足")

        # 波动性三档（对齐旧版 std/mean 比例 0.1/0.2/0.3）
        if abs(mean_val) > 1e-9:
            ratio = abs(std_val / mean_val)
            if ratio < 0.1:
                lines.append(f"  波动较小（变异系数 {ratio:.2f}），数据稳定")
            elif ratio < 0.2:
                lines.append(f"  波动适中（变异系数 {ratio:.2f}）")
            elif ratio < 0.3:
                lines.append(f"  波动较大（变异系数 {ratio:.2f}），注意异常波动")
            else:
                lines.append(f"  波动剧烈（变异系数 {ratio:.2f}），需排查异常原因")
    return lines


def explain_correlation_analysis(corr_matrix: Optional[pd.DataFrame]) -> list[str]:
    """相关性解读（对齐旧版 explain_correlation_analysis）：分级 + 强相关 Top3。"""
    lines: list[str] = []
    if corr_matrix is None or corr_matrix.empty or corr_matrix.shape[1] < 2:
        lines.append("数据中数值列不足，无法进行相关性分析")
        return lines

    very_strong, strong, moderate, weak = [], [], [], []
    for i in range(len(corr_matrix.columns)):
        for j in range(i + 1, len(corr_matrix.columns)):
            corr_val = corr_matrix.iloc[i, j]
            if np.isnan(corr_val):
                continue
            pair = (corr_matrix.columns[i], corr_matrix.columns[j], corr_val)
            if abs(corr_val) >= 0.9:
                very_strong.append(pair)
            elif abs(corr_val) >= 0.7:
                strong.append(pair)
            elif abs(corr_val) >= 0.3:
                moderate.append(pair)
            elif abs(corr_val) >= 0.1:
                weak.append(pair)

    if very_strong:
        lines.append("极强关联指标（|r|≥0.9）：")
        lines += [f"  {c1} 与 {c2}：r={r:.3f}" for c1, c2, r in very_strong]
    if strong:
        lines.append("强关联指标（0.7≤|r|<0.9）：")
        lines += [f"  {c1} 与 {c2}：r={r:.3f}" for c1, c2, r in strong]
    if moderate:
        lines.append("中等关联指标（0.3≤|r|<0.7）：")
        lines += [f"  {c1} 与 {c2}：r={r:.3f}" for c1, c2, r in moderate[:5]]
    if weak:
        lines.append("弱关联指标（0.1≤|r|<0.3）：")
        lines += [f"  {c1} 与 {c2}：r={r:.3f}" for c1, c2, r in weak[:3]]
    if not (very_strong or strong or moderate or weak):
        lines.append("未发现明显相关性。")

    # 强相关 Top3（|r|>0.7，按绝对值排序）
    top = sorted(very_strong + strong, key=lambda x: -abs(x[2]))[:3]
    if top:
        lines.append("强相关 Top3：")
        lines += [f"  {c1} 与 {c2}：r={r:.3f}" for c1, c2, r in top]
    return lines


def provide_smart_insights(df: pd.DataFrame) -> list[str]:
    """智能洞察（对齐旧版 provide_smart_insights，纯文本返回）。"""
    insights: list[str] = []

    if "timestamp" in df.columns:
        try:
            ts = pd.to_datetime(df["timestamp"])
            span = ts.max() - ts.min()
            insights.append(f"数据时间跨度：{span.days} 天")
        except (ValueError, TypeError):
            pass

    numeric = df.select_dtypes(include=[np.number]).columns
    for col in numeric:
        series = df[col].dropna()
        if series.empty:
            continue
        q1, q3 = series.quantile(0.25), series.quantile(0.75)
        iqr = q3 - q1
        outliers = series[(series < q1 - 1.5 * iqr) | (series > q3 + 1.5 * iqr)]
        if len(outliers) > 0:
            insights.append(f"在 '{col}' 列中发现 {len(outliers)} 个潜在异常值，可能需要进一步检查")

    # 正态性（Shapiro，样本 ≤5000）
    if len(numeric) > 0:
        first_col = numeric[0]
        series = df[first_col].dropna()
        if len(series) > 8 and len(series) <= 5000:
            try:
                from scipy import stats  # 可选依赖，懒加载

                stat, p_value = stats.shapiro(series.sample(min(5000, len(series))))
                if p_value < 0.05:
                    insights.append(f"'{first_col}' 列的数据分布不符合正态分布")
            except ImportError:
                pass

    # 采集频率（时间序列）
    if "timestamp" in df.columns:
        try:
            ts = pd.to_datetime(df["timestamp"]).sort_values()
            diffs = ts.diff().dropna()
            if len(diffs):
                avg = diffs.mean().total_seconds()
                if avg <= 3600:
                    insights.append(f"数据采集频率较高（平均间隔 {avg / 60:.1f} 分钟），适合高频分析")
                elif avg <= 86400:
                    insights.append(f"数据采集频率适中（平均间隔 {avg / 3600:.1f} 小时）")
                else:
                    insights.append(f"数据采集频率较低（平均间隔 {avg / 86400:.1f} 天）")
        except (ValueError, TypeError):
            pass

    # 农业指标范围检查
    for col in numeric:
        metric = _match_metric(col)
        if not metric:
            continue
        series = df[col].dropna()
        if series.empty:
            continue
        lo, hi = float(series.min()), float(series.max())
        if metric == "temperature":
            if lo < 0 or hi > 50:
                insights.append(f"温度数据范围异常（{lo:.1f}°C ~ {hi:.1f}°C），请确认单位和范围")
            elif hi - lo > 30:
                insights.append(f"温度变化范围较大（{hi - lo:.1f}°C），可能存在明显日温差")
        elif metric in ("humidity", "moisture"):
            if lo < 0 or hi > 100:
                name = "湿度" if metric == "humidity" else "土壤湿度"
                insights.append(f"{name}数据超出正常范围（{lo:.1f}% ~ {hi:.1f}%），请确认数据有效性")

    return insights


def enhanced_data_analysis(df: pd.DataFrame) -> tuple[pd.DataFrame, Optional[pd.DataFrame], list[str], list[str]]:
    """增强分析统一入口（对齐旧版 enhanced_data_analysis）：返回
    (desc_stats, corr_matrix, 描述解读列表, 相关性解读列表)。"""
    desc = describe_data(df)
    corr = calculate_correlation(df)
    return desc, corr, explain_descriptive_statistics(df, desc), explain_correlation_analysis(corr)
