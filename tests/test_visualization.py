"""visualization_service 测试（纯函数）。"""

import numpy as np
import pandas as pd

from smart_farm.services import visualization_service as vs


def _df():
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=50, freq="h"),
            "temperature": np.linspace(20.0, 30.0, 50) + np.random.default_rng(1).normal(0, 0.5, 50),
            "humidity": np.linspace(50.0, 60.0, 50),
            "region": ["a", "b"] * 25,
        }
    )


def test_smart_recommendation_trend():
    chart_type, params, reason = vs.create_smart_chart_recommendation(_df())
    assert chart_type == "线图"
    assert params["x_column"] == "timestamp"
    assert "时间序列" in reason


def test_smart_recommendation_correlation():
    df = pd.DataFrame({"a": [1.0, 2.0], "b": [3.0, 4.0]})
    chart_type, params, _ = vs.create_smart_chart_recommendation(df)
    assert chart_type == "热力图"
    assert "a" in params["columns"]


def test_smart_recommendation_histogram():
    df = pd.DataFrame({"x": [1.0, 2.0, 3.0]})
    chart_type, params, _ = vs.create_smart_chart_recommendation(df)
    assert chart_type == "直方图"
    assert params["column"] == "x"


def test_smart_recommendation_categorical_bar():
    df = pd.DataFrame({"c": ["x", "y"]})
    chart_type, _, _ = vs.create_smart_chart_recommendation(df)
    assert chart_type == "柱状图"  # 分类列 → 柱状图（对齐旧版分支顺序）


def test_dual_axis_chart():
    df = _df()
    fig = vs.create_dual_axis_chart(df, "timestamp", "temperature", "humidity")
    assert len(fig.data) == 2
    assert fig.data[1].yaxis == "y2"


def test_multi_subplot_chart():
    df = _df()
    fig = vs.create_multi_subplot_chart(df, "timestamp", ["temperature", "humidity"])
    assert len(fig.data) == 2
    # 两个子图 x 轴引用应不同（row1col1 / row1col2）
    assert fig.data[0].xaxis == "x"
    assert fig.data[1].xaxis == "x2"


def test_interpret_scatter_correlation():
    df = _df()
    text = vs.interpret_chart("散点图", df, x_column="temperature", y_column="humidity")
    assert "相关系数" in text


def test_interpret_line_trend():
    df = _df()
    text = vs.interpret_chart("线图", df, y_column="temperature")
    assert "趋势" in text


def test_interpret_box_outliers():
    df = _df()
    text = vs.interpret_chart("箱线图", df, column="temperature")
    assert "离群点" in text


def test_interpret_heatmap():
    df = _df()
    text = vs.interpret_chart("热力图", df)
    assert "最强相关" in text


def test_build_chart_types():
    df = _df()
    for ct in ["散点图", "线图", "柱状图", "箱线图", "直方图", "饼图", "热力图"]:
        fig = vs.build_chart(ct, df, x_column="temperature", y_column="humidity", column="region")
        assert fig is not None


def test_build_dual_axis_via_build_chart():
    df = _df()
    fig = vs.build_chart("双轴图", df, x_column="timestamp", y_column="temperature",
                         y2_column="humidity")
    assert len(fig.data) == 2


def test_build_unknown_raises():
    try:
        vs.build_chart("不存在", _df())
        assert False, "应抛出 ValueError"
    except ValueError:
        pass
