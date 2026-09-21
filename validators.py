"""
LOC Recommendation — Validators
==================================
Input validation for weights and required CSV columns.
"""

from config import CRITERIA_NAMES, REQUIRED_CSV_COLUMNS


def validate_weights(weights: dict) -> list[str]:
    """Return a list of error messages (empty = valid)."""
    errors = []
    total = sum(weights.values())
    if abs(total - 100) > 0.01:
        errors.append(f"Weights must sum to 100 (current: {total:.1f})")
    for name in CRITERIA_NAMES:
        v = weights.get(name, 0)
        if v < 0:
            errors.append(f"Weight for '{name}' cannot be negative (got {v})")
    return errors


def get_remaining_budget(weights: dict) -> float:
    """How much weight budget is left (100 - sum)."""
    return 100.0 - sum(weights.values())


def validate_csv_columns(df_columns: list[str]) -> list[str]:
    """Return missing required columns."""
    cols = [c.strip().lower() for c in df_columns]
    return [req for req in REQUIRED_CSV_COLUMNS if req not in cols]
