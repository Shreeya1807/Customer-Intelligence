import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd


def generate(seed=42, customers=1800):
    """Synthetic orders; never presented as real customer/business evidence."""
    rng = np.random.default_rng(seed)
    rows = []
    start = pd.Timestamp("2024-01-01")
    for customer in range(customers):
        rate = rng.uniform(0.4, 3.5)
        spend = rng.lognormal(3.7, 0.55)
        dropout = rng.integers(100, 425) if rng.random() < 0.5 else 500
        for day in range(425):
            probability = rate / 30 * (1 if day < dropout else 0.04)
            if rng.random() < probability:
                rows.append(
                    (
                        f"O{len(rows):07}",
                        f"C{customer:05}",
                        (start + pd.Timedelta(days=day)).isoformat(),
                        round(max(1, rng.lognormal(np.log(spend), 0.35)), 2),
                    )
                )
    return pd.DataFrame(
        rows, columns=["order_id", "customer_id", "ordered_at", "amount"]
    )


def validate(orders):
    required = {"order_id", "customer_id", "ordered_at", "amount"}
    if not required.issubset(orders.columns):
        raise ValueError(f"Missing columns: {sorted(required - set(orders.columns))}")
    frame = orders[list(sorted(required))].copy()
    if frame.isna().any().any() or frame.order_id.duplicated().any():
        raise ValueError("Null fields and duplicate order IDs are not allowed")
    frame["ordered_at"] = pd.to_datetime(frame.ordered_at, utc=True).dt.tz_localize(
        None
    )
    frame["amount"] = pd.to_numeric(frame.amount, errors="raise")
    if not np.isfinite(frame.amount).all() or (frame.amount <= 0).any():
        raise ValueError(
            "Amounts must be finite and positive; net refunds before import"
        )
    for col in ["order_id", "customer_id"]:
        frame[col] = frame[col].astype(str)
        if frame[col].str.strip().eq("").any():
            raise ValueError("Identifiers cannot be empty")
    return frame.sort_values("ordered_at").reset_index(drop=True)


def snapshot(orders, cutoff, horizon=60, observation_end=None):
    """Features use strictly earlier orders; labels use [cutoff, cutoff+horizon)."""
    frame = validate(orders)
    cutoff = pd.Timestamp(cutoff)
    end = pd.Timestamp(observation_end) if observation_end else frame.ordered_at.max()
    if end < cutoff + pd.Timedelta(days=horizon):
        raise ValueError("Observation window does not cover the entire label horizon")
    history = frame[frame.ordered_at < cutoff].copy()
    if history.empty:
        raise ValueError("No observed history before cutoff")
    history["ordered_at"] = history.ordered_at.dt.strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(":memory:") as db:
        history.to_sql("orders", db, index=False)
        features = pd.read_sql_query(
            """
          SELECT customer_id,
            julianday(?) - julianday(MAX(ordered_at)) AS recency,
            COUNT(*) AS frequency, SUM(amount) AS monetary,
            AVG(amount) AS average_order,
            julianday(?) - julianday(MIN(ordered_at)) AS tenure,
            SUM(CASE WHEN julianday(?) - julianday(ordered_at) <= 30 THEN 1 ELSE 0 END) AS orders_30d,
            SUM(CASE WHEN julianday(?) - julianday(ordered_at) > 30 AND julianday(?) - julianday(ordered_at) <= 60 THEN 1 ELSE 0 END) AS orders_previous_30d
          FROM orders GROUP BY customer_id
        """,
            db,
            params=[cutoff.isoformat()] * 5,
        )
    future = frame[
        (frame.ordered_at >= cutoff)
        & (frame.ordered_at < cutoff + pd.Timedelta(days=horizon))
    ]
    features["churned"] = (~features.customer_id.isin(future.customer_id)).astype(int)
    features["cutoff"] = cutoff.date().isoformat()
    features["trend"] = features.orders_30d - features.orders_previous_30d
    return features


def save_orders(frame, folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    validate(frame).to_csv(folder / "orders.csv", index=False)
