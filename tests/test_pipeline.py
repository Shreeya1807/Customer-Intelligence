import json

import pandas as pd
import pytest
from analytics.data import generate, snapshot, validate
from analytics.pipeline import partition, run


def fixture_orders():
    return pd.DataFrame(
        [
            ["1", "A", "2024-01-01", 10],
            ["2", "A", "2024-02-01", 20],
            ["3", "B", "2024-01-20", 30],
            ["4", "C", "2024-04-15", 40],
        ],
        columns=["order_id", "customer_id", "ordered_at", "amount"],
    )


def test_future_orders_do_not_leak_into_features():
    orders = fixture_orders()
    first = snapshot(orders, "2024-02-01")
    orders.loc[1, "amount"] = 999999
    second = snapshot(orders, "2024-02-01")
    pd.testing.assert_frame_equal(first, second)
    assert first.set_index("customer_id").loc["A", "monetary"] == 10
    assert first.set_index("customer_id").loc["A", "churned"] == 0
    assert first.set_index("customer_id").loc["B", "churned"] == 1
    assert "C" not in set(first.customer_id)


def test_incomplete_label_window_rejected():
    with pytest.raises(ValueError, match="entire label horizon"):
        snapshot(fixture_orders(), "2024-04-01")


@pytest.mark.parametrize("kind", ["duplicate", "negative", "missing", "nonfinite"])
def test_invalid_data_rejected(kind):
    data = fixture_orders()
    if kind == "duplicate":
        data.loc[1, "order_id"] = "1"
    if kind == "negative":
        data.loc[0, "amount"] = -1
    if kind == "missing":
        data.loc[0, "customer_id"] = None
    if kind == "nonfinite":
        data["amount"] = float("inf")
    with pytest.raises(ValueError):
        validate(data)


def test_reproducible_generation():
    pd.testing.assert_frame_equal(generate(42, 10), generate(42, 10))


def test_pipeline_exports_and_split(tmp_path):
    result = run(tmp_path, customers=350)
    assert result["split"]["customer_overlap"] == 0
    assert 0 <= result["model"]["roc_auc"] <= 1
    assert result["model"]["confusion_matrix"]
    assert json.loads((tmp_path / "metrics.json").read_text()) == result
    customers = pd.read_csv(tmp_path / "customers.csv")
    assert customers.customer_id.is_unique
    assert customers.risk.between(0, 1).all()
    assert all(
        partition(c) >= 8
        for c in customers.loc[
            customers.evaluation_split == "held-out test", "customer_id"
        ]
    )


def test_dashboard_has_no_exceptions():
    from pathlib import Path

    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(
        str(Path(__file__).resolve().parents[1] / "dashboard.py")
    ).run(timeout=30)
    assert not app.exception


def test_native_report_embeds_matching_snapshot(tmp_path):
    import base64
    import re
    import zlib
    from pathlib import Path

    from analytics.powerbi import build

    artifacts = Path(__file__).resolve().parents[1] / "artifacts"
    output = build(artifacts, tmp_path / "report")
    model = json.loads(
        (output / "CustomerIntelligence.SemanticModel/model.bim").read_text()
    )
    customers = next(t for t in model["model"]["tables"] if t["name"] == "customers")
    expression = "\n".join(customers["partitions"][0]["source"]["expression"])
    payload = re.search(r'FromText\("([A-Za-z0-9+/=]+)"', expression).group(1)
    rows = json.loads(zlib.decompress(base64.b64decode(payload), -15))
    original = pd.read_csv(artifacts / "customers.csv")
    assert len(rows) == len(original)
    assert len(rows[0]) == len(original.columns)
    assert (output / "CustomerIntelligence.pbip").is_file()
    assert len(list(output.rglob("visual.json"))) == 13
