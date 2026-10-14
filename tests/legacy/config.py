"""
LOC Recommendation — Configuration
====================================
Defines scoring dimensions, default weights, and constants for the
warehouse location recommendation engine.
"""

# ---------------------------------------------------------------------------
# Scoring Dimensions
# ---------------------------------------------------------------------------
# Each dimension is a factor that influences which LOC is best for a SKU.
CRITERIA_NAMES = [
    "velocity",         # 1. Pick velocity (outbound frequency)
    "zone_affinity",    # 2. Historical co-location with similar SKUs
    "weight_fit",       # 3. LOC weight capacity vs SKU weight
    "volume_fit",       # 4. LOC cubic capacity vs SKU dimensions
    "travel_distance",  # 5. Distance from receiving dock (lower = better)
    "temperature_fit",  # 6. Temperature zone compatibility
    "replenishment",    # 7. Proximity to bulk/reserve storage
]

# ---------------------------------------------------------------------------
# Default Weights  (must sum to 100 per profile)
# ---------------------------------------------------------------------------
DEFAULT_WEIGHTS = {
    "Standard": {
        "velocity": 30,
        "zone_affinity": 15,
        "weight_fit": 20,
        "volume_fit": 15,
        "travel_distance": 10,
        "temperature_fit": 5,
        "replenishment": 5,
    },
    "Perishable": {
        "velocity": 20,
        "zone_affinity": 10,
        "weight_fit": 10,
        "volume_fit": 10,
        "travel_distance": 5,
        "temperature_fit": 40,
        "replenishment": 5,
    },
    "Bulky": {
        "velocity": 15,
        "zone_affinity": 10,
        "weight_fit": 30,
        "volume_fit": 30,
        "travel_distance": 10,
        "temperature_fit": 0,
        "replenishment": 5,
    },
}

# ---------------------------------------------------------------------------
# Velocity Tier Thresholds  (units picked / 30 days)
# ---------------------------------------------------------------------------
# tier A: high-velocity → front zone
# tier B: medium
# tier C: slow-moving → back zone
VELOCITY_TIERS = {
    "A": 50,   # >= 50 picks/30d
    "B": 10,   # >= 10
    "C": 0,    # anything below B
}

# Preferred zone by velocity tier  (mapped to LOC zone codes in the DB)
VELOCITY_ZONE_MAP = {
    "A": ["AA", "AB", "BA", "BB"],   # front / golden zones
    "B": ["CA", "CB", "DA", "DB"],
    "C": ["EA", "EB", "FA", "FB"],   # back / overflow
}

# ---------------------------------------------------------------------------
# Tier Boundaries for the final recommendation score (0-100)
# ---------------------------------------------------------------------------
LOC_TIER_BOUNDARIES = {"Excellent": 85, "Good": 70, "Fair": 50}

# ---------------------------------------------------------------------------
# Required columns when importing a CSV instead of loading from DB
# ---------------------------------------------------------------------------
REQUIRED_CSV_COLUMNS = [
    "sku",
    "loc",
    "zone",
    "velocity_30d",
    "weight_kg",
    "volume_cbm",
    "distance_m",
    "temperature_zone",   # AMBIENT | CHILLED | FROZEN
    "reserve_distance_m",
]
