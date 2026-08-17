"""可视化服务（纯函数，无 Streamlit 依赖）。

对齐旧库 `utils/enhanced_visualization.py` 的核心能力：
- `create_smart_chart_recommendation`：按数据特征推荐图表
- `create_dual_axis_chart`：双 Y 轴图
- `create_multi_subplot_chart`：多子图
- `interpret_chart`：图表解读文案（相关性/趋势/分布）
返回 plotly figure 或文本，UI 层只渲染。
"""

from typing import Any, Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


def create_smart_chart_recommendation(df: pd.DataFrame, context: str = "") -> tuple[str, dict, str]:
    """按数据特征推荐图表（对齐旧版）。

    Returns:
        (chart_type, params, reason)
    """
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    datetime_cols = df.select_dtypes(include=["datetime64", "datetime64[ns]"]).columns.tolist()
    categorical_cols = df.select_dtypes(include=["object", "category"]).columns.tolist()

    if datetime_cols and numeric_cols:
        return (
            "线图",
            {"x_column": datetime_cols[0], "y_column": numeric_cols[0], "title": "时间趋势分析"},
            "检测到时间序列数据，适合用折线图展示趋势",
        )
    if len(numeric_cols) >= 2:
        return (
            "热力图",
            {"columns": numeric_cols[:10], "title": "变量相关性分析"},
            "多个数值变量，适合用热力图分析相关性",
        )
    if len(numeric_cols) == 1:
        return (
            "直方图",
            {"column": numeric_cols[0], "title": "数据分布分析"},
            "单数值变量，适合用直方图查看分布",
        )
    if categorical_cols:
        return (
            "柱状图",
            {"x_column": categorical_cols[0], "y_column": None, "title": "分类统计"},
            "存在分类变量，适合用柱状图比较类别",
        )
    return ("散点图", {"x_column": None, "y_column": None, "title": "数据概览"}, "兜底推荐散点图")


def create_dual_axis_chart(
    df: pd.DataFrame,
    x_column: str,
    y1_column: str,
    y2_column: str,
    y1_title: str = "Y1 轴",
    y2_title: str = "Y2 轴",
    title: Optional[str] = None,
    y1_color: str = "#185FA5",
    y2_color: str = "#D85A30",
) -> go.Figure:
    """双 Y 轴图：左轴 y1（实线）、右轴 y2（虚线）。"""
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df[x_column], y=df[y1_column], name=y1_title, mode="lines",
        line={"color": y1_color},
    ))
    fig.add_trace(go.Scatter(
        x=df[x_column], y=df[y2_column], name=y2_title, mode="lines",
        line={"color": y2_color, "dash": "dash"}, yaxis="y2",
    ))
    fig.update_layout(
        title=title,
        height=420,
        xaxis_title=x_column,
        yaxis={"title": y1_title, "color": y1_color},
        yaxis2={"title": y2_title, "overlaying": "y", "side": "right", "color": y2_color},
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig


def create_multi_subplot_chart(
    df: pd.DataFrame,
    x_column: str,
    y_columns: list[str],
    subplot_titles: Optional[list[str]] = None,
    shared_xaxes: bool = True,
) -> go.Figure:
    """多子图：每行 2 个子图，共享 X 轴。"""
    n = len(y_columns)
    if n == 0:
        raise ValueError("至少需要一个变量")
    rows = (n + 1) // 2
    titles = subplot_titles or y_columns
    fig = make_subplots(
        rows=rows, cols=2, subplot_titles=titles, shared_xaxes=shared_xaxes,
        vertical_spacing=0.08,
    )
    for i, col in enumerate(y_columns):
        row, col_idx = i // 2 + 1, i % 2 + 1
        fig.add_trace(
            go.Scatter(x=df[x_column], y=df[col], name=col, mode="lines"),
            row=row, col=col_idx,
        )
    fig.update_layout(height=400 * rows, showlegend=False)
    return fig


def interpret_chart(chart_type: str, df: pd.DataFrame, **params) -> str:
    """图表解读文案（对齐旧版 data_visualization 的每图解读）。"""
    try:
        if chart_type == "散点图":
            x, y = params.get("x_column"), params.get("y_column")
            if x and y and x in df.columns and y in df.columns:
                r = df[[x, y]].dropna().corr().iloc[0, 1]
                level = "强" if abs(r) >= 0.7 else "中等" if abs(r) >= 0.3 else "弱"
                return f"{x} 与 {y} 相关系数 r={r:.3f}（{level}相关）。"
        elif chart_type == "线图":
            y = params.get("y_column")
            if y and y in df.columns:
                s = df[y].dropna()
                if len(s) > 1:
                    pct = (s.iloc[-1] - s.iloc[0]) / abs(s.iloc[0]) * 100 if s.iloc[0] else 0
                    direction = "上升" if pct > 10 else "下降" if pct < -10 else "基本平稳"
                    return f"{y} 整体趋势{direction}（首末变化 {pct:.1f}%）。"
        elif chart_type == "箱线图":
            col = params.get("column")
            if col and col in df.columns:
                s = df[col].dropna()
                q1, med, q3 = s.quantile(0.25), s.median(), s.quantile(0.75)
                iqr = q3 - q1
                outliers = int(((s < q1 - 1.5 * iqr) | (s > q3 + 1.5 * iqr)).sum())
                return f"{col} 中位数 {med:.2f}，IQR {iqr:.2f}，检出 {outliers} 个离群点。"
        elif chart_type == "直方图":
            col = params.get("column")
            if col and col in df.columns:
                s = df[col].dropna()
                skew = float(s.skew())
                return f"{col} 均值 {s.mean():.2f}、中位数 {s.median():.2f}，偏度 {skew:.2f}。"
        elif chart_type == "热力图":
            corr = df.select_dtypes(include=[np.number]).corr()
            if corr.shape[0] >= 2:
                upper = corr.where(np.triu(np.ones(corr.shape), k=1).astype(bool))
                stack = upper.stack()
                if not stack.empty:
                    best = stack.abs().idxmax()
                    return f"最强相关：{best[0]} 与 {best[1]}（r={stack.loc[best]:.3f}）。"
        return "该图表已生成，请结合上下文解读。"
    except Exception:  # noqa: BLE001 解读失败不影响展示
        return "该图表已生成，请结合上下文解读。"


def build_chart(
    chart_type: str,
    df: pd.DataFrame,
    x_column: Optional[str] = None,
    y_column: Optional[str] = None,
    color_column: Optional[str] = None,
    column: Optional[str] = None,
    **kwargs: Any,
) -> go.Figure:
    """按类型构建图表（对齐旧版 visualize_data 基础分支）。"""
    import plotly.express as px

    if chart_type in ("散点图", "散点"):
        fig = px.scatter(df, x=x_column, y=y_column, color=color_column,
                         trendline="ols" if len(df) > 10 else None)
    elif chart_type in ("线图", "折线"):
        fig = px.line(df, x=x_column, y=y_column, color=color_column, markers=True)
    elif chart_type in ("柱状图", "柱状"):
        fig = px.bar(df, x=x_column, y=y_column, color=color_column)
    elif chart_type in ("箱线图", "箱线"):
        fig = px.box(df, y=column or y_column)
    elif chart_type in ("直方图",):
        fig = px.histogram(df, x=column or y_column, nbins=30, marginal="box")
    elif chart_type in ("饼图",):
        fig = px.pie(df, names=column or x_column, values=y_column)
    elif chart_type in ("热力图",):
        corr = df.select_dtypes(include=[np.number]).corr()
        fig = px.imshow(corr, text_auto=True, aspect="equal",
                        color_continuous_scale="RdBu_r", zmin=-1, zmax=1)
    elif chart_type in ("双轴图",):
        fig = create_dual_axis_chart(df, x_column, y_column, kwargs.get("y2_column"))
    elif chart_type in ("多子图",):
        fig = create_multi_subplot_chart(df, x_column, kwargs.get("y_columns", []))
    else:
        raise ValueError(f"不支持的图表类型：{chart_type}")
    fig.update_layout(height=kwargs.get("height", 420), template="plotly_white")
    return fig
