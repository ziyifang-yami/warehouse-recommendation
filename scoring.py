"""
LOC Recommendation — Scoring Engine
======================================
Computes a 0-100 recommendation score for each (SKU, LOC) candidate pair.

Each dimension is scored 0-100, then weighted-summed using the config weights.
"""

import numpy as np
import pandas as pd

from config import VELOCITY_TIERS, VELOCITY_ZONE_MAP


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _velocity_tier(velocity: float) -> str:
    if velocity >= VELOCITY_TIERS["A"]:
        return "A"
    if velocity >= VELOCITY_TIERS["B"]:
        return "B"
    return "C"


def _normalize_inverse(series: pd.Series) -> pd.Series:
    """Lower raw value → higher score (0-100). Used for distance, weight diff."""
    mn, mx = series.min(), series.max()
    if mx == mn:
        return pd.Series(100.0, index=series.index)
    return 100.0 * (1.0 - (series - mn) / (mx - mn))


def _normalize(series: pd.Series) -> pd.Series:
    """Higher raw value → higher score (0-100). Used for velocity."""
    mn, mx = series.min(), series.max()
    if mx == mn:
        return pd.Series(100.0, index=series.index)
    return 100.0 * (series - mn) / (mx - mn)


# ---------------------------------------------------------------------------
# Per-dimension scorers
# ---------------------------------------------------------------------------

def score_velocity(sku_df: pd.DataFrame, loc_df: pd.DataFrame) -> pd.Series:
    """
    LOCs in the preferred velocity zone get 100; wrong zone gets 0-50.
    Applied per SKU row.
    """
    tier = sku_df["velocity_30d"].apply(_velocity_tier)
    preferred_zones = tier.apply(lambda t: VELOCITY_ZONE_MAP[t])

    def _zone_score(row):
        zone = row["zone"]
        t = _velocity_tier(row["velocity_30d"])
        preferred = VELOCITY_ZONE_MAP[t]
        # tier B zones also partially acceptable for A SKUs
        if zone in preferred:
            return 100.0
        # one tier off
        all_zones = list(VELOCITY_ZONE_MAP.values())
        for i, zones in enumerate(all_zones):
            if zone in zones:
                tier_idx = list(VELOCITY_ZONE_MAP.keys()).index(t)
                distance = abs(i - tier_idx)
                return max(0.0, 100.0 - distance * 40.0)
        return 0.0

    return sku_df.apply(_zone_score, axis=1)


def score_weight_fit(sku_df: pd.DataFrame) -> pd.Series:
    """Score = 100 if SKU weight <= LOC capacity, else penalised."""
    ratio = sku_df["weight_kg"] / sku_df["max_weight_kg"].replace(0, np.nan).fillna(999)
    # ratio < 1 → fits; score based on how much headroom
    scores = np.where(ratio <= 1.0, 100.0 * (1.0 - ratio * 0.5), np.maximum(0.0, 100.0 - (ratio - 1.0) * 200.0))
    return pd.Series(scores, index=sku_df.index)


def score_volume_fit(sku_df: pd.DataFrame) -> pd.Series:
    """Same logic as weight_fit but for volume."""
    ratio = sku_df["volume_cbm"] / sku_df["max_volume_cbm"].replace(0, np.nan).fillna(999)
    scores = np.where(ratio <= 1.0, 100.0 * (1.0 - ratio * 0.5), np.maximum(0.0, 100.0 - (ratio - 1.0) * 200.0))
    return pd.Series(scores, index=sku_df.index)


def score_travel_distance(sku_df: pd.DataFrame) -> pd.Series:
    """Shorter distance → higher score."""
    return _normalize_inverse(sku_df["distance_m"])


def score_temperature_fit(sku_df: pd.DataFrame) -> pd.Series:
    """Exact match = 100, mismatch = 0."""
    match = sku_df["temperature_zone_sku"] == sku_df["temperature_zone_loc"]
    return match.astype(float) * 100.0


def score_replenishment(sku_df: pd.DataFrame) -> pd.Series:
    """Shorter reserve distance → higher score."""
    return _normalize_inverse(sku_df["reserve_distance_m"])


def score_zone_affinity(sku_df: pd.DataFrame) -> pd.Series:
    """
    Placeholder: in a future version this would look at co-location history.
    For now returns a uniform 50 so it doesn't distort results.
    """
    return pd.Series(50.0, index=sku_df.index)


# ---------------------------------------------------------------------------
# Main scoring function
# ---------------------------------------------------------------------------

def compute_scores(merged_df: pd.DataFrame, weights: dict) -> pd.DataFrame:
    """
    Compute weighted recommendation score for each (SKU, LOC) row.

    merged_df must have columns from both sku_df and loc_df joined.
    Returns the same DataFrame with added dimension score columns and
    a final 'rec_score' column (0-100).
    """
    df = merged_df.copy()

    # Dimension scores
    df["s_velocity"]        = score_velocity(df, None)
    df["s_weight_fit"]      = score_weight_fit(df)
    df["s_volume_fit"]      = score_volume_fit(df)
    df["s_travel_distance"] = score_travel_distance(df)
    df["s_temperature_fit"] = score_temperature_fit(df)
    df["s_replenishment"]   = score_replenishment(df)
    df["s_zone_affinity"]   = score_zone_affinity(df)

    dim_map = {
        "velocity":         "s_velocity",
        "zone_affinity":    "s_zone_affinity",
        "weight_fit":       "s_weight_fit",
        "volume_fit":       "s_volume_fit",
        "travel_distance":  "s_travel_distance",
        "temperature_fit":  "s_temperature_fit",
        "replenishment":    "s_replenishment",
    }

    total_weight = sum(weights.values())
    if total_weight == 0:
        df["rec_score"] = 0.0
        return df

    df["rec_score"] = sum(
        df[col] * (weights.get(dim, 0) / total_weight)
        for dim, col in dim_map.items()
    ).round(1)

    return df


def classify_tier(score: float) -> str:
    """Map a 0-100 score to Excellent / Good / Fair / Poor."""
    if score >= 85:
        return "Excellent"
    if score >= 70:
        return "Good"
    if score >= 50:
        return "Fair"
    return "Poor"
