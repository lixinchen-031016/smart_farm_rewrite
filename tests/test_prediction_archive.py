"""prediction_archive 测试（临时目录隔离）。"""


import pandas as pd
import pytest

from smart_farm.services.prediction_archive import PredictionArchive


@pytest.fixture()
def archive(tmp_path):
    return PredictionArchive(base_dir=tmp_path)


def _hist():
    return pd.DataFrame(
        {"ds": pd.date_range("2026-01-01", periods=10, freq="3h"),
         "y": [float(i) for i in range(10)]}
    )


def _fc():
    return pd.DataFrame(
        {"ds": pd.date_range("2026-01-01 00:00", periods=8, freq="3h"),
         "yhat": [1.0] * 8, "yhat_lower": [0.0] * 8, "yhat_upper": [2.0] * 8}
    )


def test_save_creates_csv_md_and_db(archive):
    res = archive.save_prediction_result(
        _hist(), _fc(), "空气温度", "Prophet+SARIMA(推荐)", 1,
        model_explanation="测试", rmse=0.5, r_squared=0.8,
    )
    assert res["status"] == "ok"
    assert res["prediction_id"].startswith("空气温度_")
    assert archive.base_dir.joinpath(f"prediction_{res['prediction_id']}.csv").exists()
    assert archive.base_dir.joinpath(f"prediction_{res['prediction_id']}.md").exists()
    assert archive.history_db_path.exists()


def test_csv_has_expected_columns(archive):
    res = archive.save_prediction_result(_hist(), _fc(), "土壤湿度", "naive", 1)
    csv_df = pd.read_csv(archive.base_dir / f"prediction_{res['prediction_id']}.csv")
    assert "data_type" in csv_df.columns
    assert "prediction_id" in csv_df.columns
    assert "created_at" in csv_df.columns
    assert (csv_df["data_type"] == "historical").sum() == 10
    assert (csv_df["data_type"] == "forecast").sum() == 8


def test_history_and_statistics(archive):
    archive.save_prediction_result(_hist(), _fc(), "空气温度", "m1", 1, rmse=0.5)
    archive.save_prediction_result(_hist(), _fc(), "空气湿度", "m2", 1, rmse=1.0)
    history = archive.get_prediction_history(limit=10)
    assert len(history) == 2
    stats = archive.get_statistics()
    assert stats["total_predictions"] == 2
    assert stats["recent_predictions_7d"] == 2
    assert stats["avg_rmse"] == pytest.approx(0.75)
    assert set(stats["by_type"].keys()) == {"空气温度", "空气湿度"}


def test_history_filter_by_type(archive):
    archive.save_prediction_result(_hist(), _fc(), "空气温度", "m1", 1)
    archive.save_prediction_result(_hist(), _fc(), "空气湿度", "m2", 1)
    history = archive.get_prediction_history(prediction_type="空气温度")
    assert len(history) == 1
    assert history[0]["prediction_type"] == "空气温度"


def test_cleanup_old_records(archive):
    for i in range(5):
        archive.save_prediction_result(_hist(), _fc(), f"T{i}", "m", 1)
    archive.max_records = 3
    archive._cleanup_old_records()
    assert archive.get_statistics()["total_predictions"] == 3


def test_delete_prediction(archive):
    res = archive.save_prediction_result(_hist(), _fc(), "空气温度", "m", 1)
    pid = res["prediction_id"]
    assert archive.delete_prediction(pid) is True
    assert archive.get_statistics()["total_predictions"] == 0
    assert not archive.base_dir.joinpath(f"prediction_{pid}.csv").exists()
