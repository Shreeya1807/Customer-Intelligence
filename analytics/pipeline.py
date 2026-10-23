import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.dummy import DummyClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .data import generate, snapshot, validate

FEATURES = [
    "recency",
    "frequency",
    "monetary",
    "average_order",
    "tenure",
    "orders_30d",
    "orders_previous_30d",
    "trend",
]


def partition(customer_id):
    return int(hashlib.sha256(str(customer_id).encode()).hexdigest()[:8], 16) % 10


def metrics(y, probabilities, threshold):
    prediction = (probabilities >= threshold).astype(int)
    return {
        "precision": float(precision_score(y, prediction, zero_division=0)),
        "recall": float(recall_score(y, prediction, zero_division=0)),
        "f1": float(f1_score(y, prediction, zero_division=0)),
        "roc_auc": float(roc_auc_score(y, probabilities)),
        "pr_auc": float(average_precision_score(y, probabilities)),
        "brier": float(brier_score_loss(y, probabilities)),
        "confusion_matrix": confusion_matrix(y, prediction, labels=[0, 1]).tolist(),
    }


def run(output="artifacts", source=None, seed=42, customers=1800):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    orders = validate(pd.read_csv(source) if source else generate(seed, customers))
    # Fixed chronological cutoffs keep the task reproducible and expose required coverage.
    early = snapshot(orders, "2024-09-01")
    late = snapshot(orders, "2024-12-01")
    train = early[early.customer_id.map(partition) < 6].copy()
    validation = early[early.customer_id.map(partition).between(6, 7)].copy()
    test = late[late.customer_id.map(partition) >= 8].copy()
    for name, data in [("train", train), ("validation", validation), ("test", test)]:
        if len(data) < 20 or data.churned.nunique() != 2:
            raise ValueError(f"{name} needs at least 20 customers and both outcomes")
    model = make_pipeline(
        StandardScaler(), LogisticRegression(max_iter=1500, random_state=seed)
    )
    model.fit(train[FEATURES], train.churned)
    val_prob = model.predict_proba(validation[FEATURES])[:, 1]
    thresholds = np.linspace(0.1, 0.9, 81)
    threshold = float(
        max(thresholds, key=lambda t: f1_score(validation.churned, val_prob >= t))
    )
    probabilities = model.predict_proba(test[FEATURES])[:, 1]
    baseline = DummyClassifier(strategy="prior").fit(train[FEATURES], train.churned)
    # Segmentation is fit on training customers only, independent of outcomes.
    rfm = ["recency", "frequency", "monetary"]
    segments = make_pipeline(
        StandardScaler(), KMeans(n_clusters=4, n_init=10, random_state=seed)
    )
    segments.fit(np.log1p(train[rfm]))
    scored = late.copy()
    scored["risk"] = model.predict_proba(scored[FEATURES])[:, 1]
    scored["cluster"] = segments.predict(np.log1p(scored[rfm]))
    scored["segment"] = np.select(
        [
            scored.recency > 60,
            scored.monetary >= train.monetary.quantile(0.75),
            scored.tenure <= 60,
        ],
        ["At risk", "High value", "New"],
        default="Developing",
    )
    scored["evaluation_split"] = np.where(
        scored.customer_id.map(partition) >= 8, "held-out test", "exploration only"
    )
    # Retention cohorts use observed order months, not model predictions.
    cohort_orders = orders.copy()
    cohort_orders["month"] = cohort_orders.ordered_at.dt.to_period("M").astype(str)
    cohort_orders["cohort"] = cohort_orders.groupby("customer_id").month.transform(
        "min"
    )
    cohorts = (
        cohort_orders.groupby(["cohort", "month"])
        .customer_id.nunique()
        .reset_index(name="active_customers")
    )
    sizes = cohort_orders.groupby("cohort").customer_id.nunique()
    cohorts["retention"] = cohorts.active_customers / cohorts.cohort.map(sizes)
    revenue = (
        cohort_orders.groupby("month")
        .agg(
            revenue=("amount", "sum"),
            orders=("order_id", "count"),
            customers=("customer_id", "nunique"),
        )
        .reset_index()
    )
    report = {
        "data_source": "user-supplied CSV"
        if source
        else "synthetic transactions; no real business outcomes",
        "seed": seed,
        "features": FEATURES,
        "threshold": threshold,
        "label": "No purchases in the following 60 days; an operational inactivity proxy",
        "split": {
            "train_cutoff": "2024-09-01",
            "test_cutoff": "2024-12-01",
            "train_customers": len(train),
            "validation_customers": len(validation),
            "test_customers": len(test),
            "customer_overlap": len(set(train.customer_id) & set(test.customer_id)),
        },
        "model": metrics(test.churned, probabilities, threshold),
        "baseline": metrics(
            test.churned, baseline.predict_proba(test[FEATURES])[:, 1], threshold
        ),
        "test_prevalence": float(test.churned.mean()),
        "coefficients": dict(zip(FEATURES, model[-1].coef_[0].tolist())),
    }
    (output / "metrics.json").write_text(json.dumps(report, indent=2) + "\n")
    for name, frame in [
        ("customers", scored),
        ("monthly_revenue", revenue),
        ("retention", cohorts),
        ("orders", orders),
    ]:
        frame.to_csv(output / f"{name}.csv", index=False)
    with sqlite3.connect(output / "warehouse.sqlite") as db:
        for name, frame in [
            ("orders", orders),
            ("customer_features", scored),
            ("monthly_revenue", revenue),
            ("retention", cohorts),
        ]:
            frame.to_sql(name, db, if_exists="replace", index=False)
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders(customer_id, ordered_at)"
        )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Build reproducible customer analytics artifacts"
    )
    parser.add_argument("--output", default="artifacts")
    parser.add_argument(
        "--source", help="CSV covering Jan 2024 through at least Jan 30 2025"
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--customers", type=int, default=1800)
    args = parser.parse_args()
    print(
        json.dumps(run(args.output, args.source, args.seed, args.customers), indent=2)
    )
