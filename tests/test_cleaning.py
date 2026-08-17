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
