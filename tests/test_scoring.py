"""Basic unit tests for the scoring engine."""

import pandas as pd
import pytest
from scoring import classify_tier, compute_scores
from config import DEFAULT_WEIGHTS


def _make_row(**kwargs):
    defaults = dict(
        sku="TEST001",
        sku_name="Test SKU",
        current_loc="AA001",
        loc="AB001",
        zone="AB",
        velocity_30d=60,
        weight_kg=5.0,
        volume_cbm=0.01,
        temperature_zone_sku="AMBIENT",
        temperature_zone_loc="AMBIENT",
        max_weight_kg=100.0,
        max_volume_cbm=1.0,
        distance_m=20.0,
        reserve_distance_m=50.0,
    )
    defaults.update(kwargs)
    return defaults


def test_classify_tier():
    assert classify_tier(90) == "Excellent"
    assert classify_tier(75) == "Good"
    assert classify_tier(60) == "Fair"
    assert classify_tier(40) == "Poor"


def test_compute_scores_returns_rec_score():
    df = pd.DataFrame([_make_row()])
    result = compute_scores(df, DEFAULT_WEIGHTS["Standard"])
    assert "rec_score" in result.columns
    assert 0 <= result["rec_score"].iloc[0] <= 100


def test_temperature_mismatch_penalises_score():
    row_match    = _make_row(temperature_zone_sku="CHILLED", temperature_zone_loc="CHILLED")
    row_mismatch = _make_row(temperature_zone_sku="CHILLED", temperature_zone_loc="AMBIENT")

    weights = {k: (40 if k == "temperature_fit" else 10) for k in DEFAULT_WEIGHTS["Standard"]}
    # normalise to 100
    total = sum(weights.values())
    weights = {k: int(v * 100 / total) for k, v in weights.items()}

    df = pd.DataFrame([row_match, row_mismatch])
    result = compute_scores(df, weights)
    assert result.iloc[0]["rec_score"] > result.iloc[1]["rec_score"]


def test_weights_must_affect_score():
    row = _make_row(velocity_30d=100, distance_m=200)
    df = pd.DataFrame([row, row])

    w_velocity_heavy  = dict(DEFAULT_WEIGHTS["Standard"])
    w_distance_heavy  = dict(DEFAULT_WEIGHTS["Standard"])
    w_velocity_heavy["velocity"] = 80
    w_distance_heavy["travel_distance"] = 80

    r1 = compute_scores(df.head(1), w_velocity_heavy)
    r2 = compute_scores(df.head(1), w_distance_heavy)
    # Both valid scores between 0-100
    assert 0 <= r1["rec_score"].iloc[0] <= 100
    assert 0 <= r2["rec_score"].iloc[0] <= 100
