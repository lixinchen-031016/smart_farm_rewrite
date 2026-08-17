import pandas as pd

from smart_farm.services import analysis_service as an


def _df():
    return pd.DataFrame(
        {
            "group": ["a", "a", "b", "b"],
            "value": [1.0, 2.0, 3.0, 4.0],
            "other": [10.0, 20.0, 30.0, 40.0],
        }
    )


def test_describe():
    d = an.describe_data(_df())
    assert "value" in d.columns


def test_correlation():
    c = an.calculate_correlation(_df())
    assert c is not None
    assert c.shape == (2, 2)


def test_group_and_aggregate():
    g = an.group_and_aggregate(_df(), "group", "value", "平均值")
    assert g[g["group"] == "a"]["value_平均值"].iloc[0] == 1.5
    assert g[g["group"] == "b"]["value_平均值"].iloc[0] == 3.5


def test_group_aggregate_invalid_column():
    try:
        an.group_and_aggregate(_df(), "group", "nope", "平均值")
        assert False, "应抛出 ValueError"
    except ValueError:
        pass
