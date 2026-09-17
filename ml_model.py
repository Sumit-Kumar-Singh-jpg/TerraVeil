import numpy as np
from pyod.models.copod import COPOD

# ============================================================
# TERRAVEIL COPOD ANOMALY MODEL
# ============================================================
# Public API preserved:
#     calculate_risk(node_type, values)
#
# UG nodes are scored from tilt/vibration/soil (or legacy BME inputs).
# LD nodes are scored from displacement CHANGE relative to the node's
# current-session baseline. telemetry.py supplies displacement_delta_mm.
# ============================================================

RNG_SEED = 42
BASELINE_SAMPLES = 3000
rng = np.random.default_rng(RNG_SEED)


def _clip(value, low, high):
    return np.clip(value, low, high)


def _fit_copod(data):
    model = COPOD()
    data = np.asarray(data, dtype=float)
    model.fit(data)
    reference_scores = np.sort(np.asarray(model.decision_scores_, dtype=float))
    return model, reference_scores


def _score_percentile(model, reference_scores, sample):
    """Return empirical anomaly percentile (0-100) and raw COPOD score."""
    x = np.asarray(sample, dtype=float).reshape(1, -1)
    raw_score = float(model.decision_function(x)[0])
    rank = np.searchsorted(reference_scores, raw_score, side="right")
    percentile = (rank / len(reference_scores)) * 100.0
    return float(percentile), raw_score


def _level_from_score(score):
    # Compatibility only. telemetry.py applies the physical anomaly gate.
    if score >= 90:
        return "HIGH"
    if score >= 70:
        return "MEDIUM"
    return "LOW"


# ============================================================
# UNDERGROUND NODE BASELINES
# ============================================================

normal_ground_data = []
for _ in range(BASELINE_SAMPLES):
    tilt_x = rng.normal(0.0, 0.08)
    tilt_y = rng.normal(0.0, 0.08)
    vibration = rng.choice([0.0, 1.0], p=[0.985, 0.015])
    soil = _clip(rng.normal(1800.0, 450.0), 500.0, 3500.0)
    normal_ground_data.append([tilt_x, tilt_y, vibration, soil])

ground_model, ground_reference_scores = _fit_copod(normal_ground_data)


normal_ground_bme_data = []
for _ in range(BASELINE_SAMPLES):
    tilt_x = rng.normal(0.0, 0.08)
    tilt_y = rng.normal(0.0, 0.08)
    vibration = abs(rng.normal(0.15, 0.05))
    temperature = rng.normal(30.0, 3.0)
    humidity = _clip(rng.normal(60.0, 8.0), 20.0, 95.0)
    normal_ground_bme_data.append([
        tilt_x,
        tilt_y,
        vibration,
        temperature,
        humidity,
    ])

ground_bme_model, ground_bme_reference_scores = _fit_copod(normal_ground_bme_data)


# ============================================================
# LINEAR DISPLACEMENT NODE BASELINE
# ============================================================
# LD-01 must detect MOVEMENT, not the shaft's arbitrary absolute position.
# Normal movement between the session reference and a healthy reading is
# expected to be close to zero. The physical gate in telemetry.py requires
# >= 1.0 mm change before an LD reading becomes an anomaly.
# ============================================================

normal_displacement_change_data = []
for _ in range(BASELINE_SAMPLES):
    displacement_change = abs(rng.normal(0.0, 0.12))
    normal_displacement_change_data.append([displacement_change])

displacement_model, displacement_reference_scores = _fit_copod(
    normal_displacement_change_data
)


# 3-feature fallback model for older callers without soil/BME.
normal_ground_core_data = np.asarray(normal_ground_data, dtype=float)[:, :3]
ground_core_model, ground_core_reference_scores = _fit_copod(
    normal_ground_core_data
)


# ============================================================
# EVALUATION FUNCTIONS
# ============================================================

def evaluate_ground(
    tilt_x,
    tilt_y,
    vibration,
    soil=None,
    temperature=None,
    humidity=None,
):
    """Return (anomaly_percentile, raw_copod_score)."""

    if soil is not None:
        return _score_percentile(
            ground_model,
            ground_reference_scores,
            [tilt_x, tilt_y, vibration, soil],
        )

    if temperature is not None and humidity is not None:
        return _score_percentile(
            ground_bme_model,
            ground_bme_reference_scores,
            [tilt_x, tilt_y, vibration, temperature, humidity],
        )

    return _score_percentile(
        ground_core_model,
        ground_core_reference_scores,
        [tilt_x, tilt_y, vibration],
    )


def evaluate_crack(displacement_change_mm):
    """Score LD/crack movement relative to its current-session reference."""
    return _score_percentile(
        displacement_model,
        displacement_reference_scores,
        [displacement_change_mm],
    )


# ============================================================
# PUBLIC API USED BY telemetry.py
# ============================================================

def calculate_risk(node_type, values):
    """Return (COPOD percentile 0-100, compatibility risk level)."""

    if node_type == "UnderGround":
        score, _ = evaluate_ground(
            tilt_x=float(values["tilt_x"]),
            tilt_y=float(values["tilt_y"]),
            vibration=float(values["vibration"]),
            soil=(
                float(values["soil"])
                if values.get("soil") is not None
                else None
            ),
            temperature=(
                float(values["temperature"])
                if values.get("temperature") is not None
                else None
            ),
            humidity=(
                float(values["humidity"])
                if values.get("humidity") is not None
                else None
            ),
        )
    else:
        # New telemetry.py supplies displacement_delta_mm. Keep a fallback to
        # displacement_mm so older simulation/tests/callers do not break.
        movement = values.get("displacement_delta_mm")
        if movement is None:
            movement = values.get("displacement_mm", 0.0)
        score, _ = evaluate_crack(abs(float(movement)))

    score = max(0.0, min(100.0, float(score)))
    level = _level_from_score(score)

    return round(score, 2), level
