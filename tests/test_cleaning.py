"""cleaning_service 测试。"""

import numpy as np
import pandas as pd

from smart_farm.services import cleaning_service as cs
from smart_farm.services.cleaning_service import CleaningConfig


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "temp": [1.0, 2.0, np.nan, 4.0, 2.0],
            "hum": [10, 10, 10, 10, 10],  # 全重复，用于去重测试
            "note": ["a", "b", "c", "d", "e"],
        }
    )


def test_fill_missing_mean():
    df = _frame()
    out, rep = cs.fill_missing(df, "temp", "mean")
    assert rep["filled"] == 1
    assert out["temp"].isnull().sum() == 0
    assert abs(out["temp"].iloc[2] - 2.25) < 1e-6


def test_fill_missing_median_mode_constant():
    df = _frame()
    out_m, _ = cs.fill_missing(df, "temp", "median")
    assert out_m["temp"].iloc[2] == 2.0
    out_c, rep_c = cs.fill_missing(df, "temp", "constant", fill_value=99.0)
    assert out_c["temp"].iloc[2] == 99.0
    assert rep_c["fill_value"] == 99.0


def test_fill_missing_drop_rows():
    df = _frame()
    out, rep = cs.fill_missing(df, "temp", "drop")
    assert rep["dropped_rows"] == 1
    assert len(out) == 4
    assert out["temp"].isnull().sum() == 0


def test_fill_missing_no_missing_is_noop():
    df = _frame().assign(temp=[1, 2, 3, 4, 5])
    out, rep = cs.fill_missing(df, "temp", "mean")
    assert rep.get("note") == "无缺失值"
    assert out.equals(df)


def test_clean_dataframe_config():
    # hum 全为 10，temp 含一个缺失值，并人为追加一行完全重复行用于去重测试
    df = pd.DataFrame(
        {
            "temp": [1.0, 2.0, np.nan, 4.0, 5.0],
            "hum": [10, 10, 10, 10, 10],
        }
    )
    df = pd.concat([df, df.iloc[[0]]], ignore_index=True)  # 6 行，末行与首行完全重复
    cfg = CleaningConfig(
        drop_duplicates=True,
        drop_columns=[],
        missing={"temp": ("mean", None)},
    )
    out, report = cs.clean_dataframe(df, cfg)
    assert report["original_shape"][0] == 6
    assert report["final_shape"][0] == 5  # 去掉 1 个完全重复行
    assert out["temp"].isnull().sum() == 0  # 缺失值已填补
    types = {op["type"] for op in report["operations"]}
    assert "drop_duplicates" in types
    assert "fill_missing" in types


def test_clean_dataframe_drop_columns_only():
    df = _frame()
    cfg = CleaningConfig(drop_columns=["note"])
    out, report = cs.clean_dataframe(df, cfg)
    assert "note" not in out.columns
    assert report["final_shape"][0] == 5  # 未删行
    assert {op["type"] for op in report["operations"]} == {"drop_columns"}


def test_assess_quality():
    df = _frame()
    q = cs.assess_quality(df)
    assert q["total_rows"] == 5
    assert q["missing_cells"] == 1
    assert 0 < q["completeness"] < 100
    assert q["by_column"]["temp"]["count"] == 1


# ----------------------------- 规则引擎（DataCleaningRule / DataCleaner / 模板） -----------------------------


def _rule_frame():
    return pd.DataFrame(
        {
            "temperature": [20.0, 22.0, 21.0, 100.0, 21.5, None],  # 100 是异常，最后缺失
            "humidity": [50.0, None, 52.0, 51.0, 50.5, 53.0],
            "note": ["a", "b", "c", "d", "e", "f"],
        }
    )


def test_rule_to_dict_from_dict_roundtrip():
    rule = cs.DataCleaningRule("test")
    rule.set_duplicate_removal(True)
    rule.configure_missing_value("temperature", "median")
    rule.configure_outlier_detection("iqr", {"columns": [], "remove": True})
    rule.add_column_to_drop("note")
    data = rule.to_dict()
    restored = cs.DataCleaningRule.from_dict(data)
    assert restored.name == "test"
    assert restored.rules["duplicate_removal"] is True
    assert restored.rules["column_dropping"] == ["note"]
    assert restored.rules["missing_value_handling"]["temperature"]["method"] == "median"
    assert restored.rules["outlier_detection"]["method"] == "iqr"


def test_rule_to_json_valid():
    rule = cs.DataCleaningRule("t")
    rule.set_duplicate_removal(True)
    payload = rule.to_json()
    assert '"name": "t"' in payload
    assert "duplicate_removal" in payload


def test_apply_rule_full_pipeline():
    df = _rule_frame()
    rule = cs.DataCleaningRule("full")
    rule.set_duplicate_removal(True)
    rule.add_column_to_drop("note")
    rule.configure_missing_value("temperature", "median")
    rule.configure_missing_value("humidity", "median")
    rule.configure_outlier_detection("iqr", {"columns": ["temperature"], "remove": True})
    out, report = cs.DataCleaner().apply_rule(df, rule)
    assert "note" not in out.columns
    assert out["temperature"].isnull().sum() == 0
    assert out["humidity"].isnull().sum() == 0
    # 异常值 100 被移除
    assert 100.0 not in out["temperature"].values
    types = {op["type"] for op in report["operations"]}
    assert {"column_dropping", "missing_value_imputation", "outlier_removal"} <= types
    assert report["quality_after"]["completeness"] == 100.0


def test_apply_rule_outlier_mark_without_remove():
    df = _rule_frame()
    rule = cs.DataCleaningRule("mark")
    rule.configure_outlier_detection("iqr", {"columns": ["temperature"], "remove": False})
    out, report = cs.DataCleaner().apply_rule(df, rule)
    assert len(out) == len(df)  # 不删行
    assert "outliers" in report
    assert report["anomaly_summary"]["temperature"]["count"] >= 1


def test_agricultural_template():
    rule = cs.create_agricultural_standard_template()
    assert rule.rules["duplicate_removal"] is True
    assert rule.rules["outlier_detection"]["method"] == "iqr"
    assert rule.rules["outlier_detection"]["params"]["remove"] is True
    assert set(rule.rules["missing_value_handling"].keys()) >= {
        "temperature", "humidity", "soil_moisture", "soil_nutrient", "light_intensity"
    }


def test_ml_template():
    df = _rule_frame()
    rule = cs.create_machine_learning_template(df)
    assert rule.rules["duplicate_removal"] is True
    assert rule.rules["outlier_detection"]["method"] == "isolation_forest"
    assert rule.rules["outlier_detection"]["params"]["contamination"] == 0.1
    # 全部数值列配置 median 填充
    assert {"temperature", "humidity"} <= set(rule.rules["missing_value_handling"].keys())


def test_create_template_rule_auto():
    df = pd.DataFrame(
        {
            "temperature": [1.0, 2.0, 3.0],
            "bad_col": [1.0, None, None],  # 缺失率 66% > 50% → 删列
            "half_col": [1.0, None, 3.0],  # 缺失率 33% → median 填充
            "category": ["a", "b", None],  # 分类列 → mode 填充
        }
    )
    rule = cs.create_template_rule("auto", df)
    assert "bad_col" in rule.rules["column_dropping"]
    assert rule.rules["missing_value_handling"]["half_col"]["method"] == "median"
    assert rule.rules["missing_value_handling"]["category"]["method"] == "mode"
    assert rule.rules["duplicate_removal"] is True


def test_fill_missing_with_flag():
    df = pd.DataFrame({"temperature": [20.0, None, 22.0]})
    out, report = cs.fill_missing_with_flag(df, "temperature", "median")
    assert out["temperature"].isnull().sum() == 0
    assert "temperature_filled" in out.columns
    assert report["filled"] == 1
    flag = out.loc[1, "temperature_filled"]
    assert isinstance(flag, str) and "空气温度" in flag and "填充值" in flag


def test_generate_report_text():
    df = _rule_frame()
    rule = cs.DataCleaningRule("r")
    rule.set_duplicate_removal(True)
    rule.configure_missing_value("temperature", "median")
    _, report = cs.DataCleaner().apply_rule(df, rule)
    text = cs.DataCleaner().generate_report(report)
    assert "原数据形状" in text
    assert "填充" in text
