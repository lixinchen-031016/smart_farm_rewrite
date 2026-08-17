import numpy as np
import pandas as pd

from smart_farm.services import analysis_service as az


def _df():
    return pd.DataFrame(
        {
            "group": ["a", "a", "b", "b"],
            "value": [1.0, 2.0, 3.0, 4.0],
            "other": [10.0, 20.0, 30.0, 40.0],
        }
    )


def test_describe():
    d = az.describe_data(_df())
    assert "value" in d.columns


def test_correlation():
    c = az.calculate_correlation(_df())
    assert c is not None
    assert c.shape == (2, 2)


def test_group_and_aggregate():
    g = az.group_and_aggregate(_df(), "group", "value", "平均值")
    assert g[g["group"] == "a"]["value_平均值"].iloc[0] == 1.5
    assert g[g["group"] == "b"]["value_平均值"].iloc[0] == 3.5


def test_group_aggregate_invalid_column():
    try:
        az.group_and_aggregate(_df(), "group", "nope", "平均值")
        assert False, "应抛出 ValueError"
    except ValueError:
        pass


# ----------------------------- 智能解读 -----------------------------


def _sensor_df():
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=100, freq="h"),
            "temperature": np.linspace(20.0, 25.0, 100) + np.sin(np.linspace(0, 6, 100)),
            "humidity": np.linspace(50.0, 60.0, 100),
            "soil_moisture": np.linspace(30.0, 45.0, 100),
            "light_intensity": np.linspace(2000.0, 5000.0, 100),
        }
    )


def test_explain_descriptive_mentions_columns():
    df = _sensor_df()
    desc = az.describe_data(df)
    lines = az.explain_descriptive_statistics(df, desc)
    assert any("temperature" in line for line in lines)
    assert any("温度适中" in line for line in lines)
    assert any("波动" in line for line in lines)


def test_explain_descriptive_temperature_low_high():
    low_df = pd.DataFrame({"temperature": [5.0, 6.0, 7.0]})
    lines = az.explain_descriptive_statistics(low_df, az.describe_data(low_df))
    assert any("温度偏低" in line for line in lines)

    high_df = pd.DataFrame({"temperature": [40.0, 41.0, 42.0]})
    lines = az.explain_descriptive_statistics(high_df, az.describe_data(high_df))
    assert any("温度偏高" in line for line in lines)


def test_explain_correlation_grades():
    df = pd.DataFrame({
        "a": np.linspace(0, 1, 50),
        "b": np.linspace(0, 1, 50),  # r≈1 极强
        "c": -np.linspace(0, 1, 50),  # r≈-1
        "d": np.random.default_rng(0).normal(size=50),  # 弱相关
    })
    corr = az.calculate_correlation(df)
    lines = az.explain_correlation_analysis(corr)
    joined = "\n".join(lines)
    assert "极强关联" in joined
    assert "强相关 Top3" in joined
    assert any("a 与 b" in line for line in lines)


def test_explain_correlation_none():
    lines = az.explain_correlation_analysis(None)
    assert "无法进行相关性分析" in lines[0]


def test_smart_insights_anomaly_and_frequency():
    df = _sensor_df()
    df.loc[5, "temperature"] = 200.0  # 异常值
    insights = az.provide_smart_insights(df)
    joined = "\n".join(insights)
    assert "时间跨度" in joined
    assert "潜在异常值" in joined
    assert "采集频率较高" in joined  # 1 小时间隔


def test_smart_insights_range_check():
    bad = pd.DataFrame({
        "temperature": [-5.0, 10.0, 20.0],
        "humidity": [150.0, 60.0, 70.0],
    })
    insights = az.provide_smart_insights(bad)
    joined = "\n".join(insights)
    assert "温度数据范围异常" in joined
    assert "湿度数据超出正常范围" in joined


def test_enhanced_data_analysis_entry():
    df = _sensor_df()
    desc, corr, exp_lines, corr_lines = az.enhanced_data_analysis(df)
    assert desc is not None and not desc.empty
    assert corr is not None
    assert exp_lines and corr_lines
