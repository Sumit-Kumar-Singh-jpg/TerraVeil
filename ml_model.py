import numpy as np
from pyod.models.copod import COPOD

# ============================================================
# TERRAVEIL COPOD ANOMALY MODEL
# ============================================================
#
# Public API intentionally preserved for telemetry.py:
#     calculate_risk(node_type, values)
#
# COPOD is used through decision_function() only. We do not use
# model.predict() / threshold_ for anomaly scoring, so the PyOD
# contamination threshold does not control the anomaly score.
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
    # Compatibility only. telemetry.py uses the numeric candidate score.
    if score >= 90:
        return "HIGH"
    if score >= 70:
        return "MEDIUM"
    return "LOW"


# ============================================================
# UNDERGROUND NODE BASELINES
# ============================================================
# Current hardware baseline:
#   tilt_x, tilt_y, vibration, soil
#
# Legacy/simulation BME baseline:
#   tilt_x, tilt_y, vibration, temperature, humidity
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
# Use calibrated physical displacement, not raw potentiometer ADC,
# because raw ADC depends on mounting and calibration.
# ============================================================

normal_displacement_data = []
for _ in range(BASELINE_SAMPLES):
    displacement = abs(rng.normal(0.5, 0.15))
    normal_displacement_data.append([displacement])

displacement_model, displacement_reference_scores = _fit_copod(
    normal_displacement_data
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


def evaluate_crack(displacement):
    """Return (anomaly_percentile, raw_copod_score)."""
    return _score_percentile(
        displacement_model,
        displacement_reference_scores,
        [displacement],
    )


# ============================================================
# PUBLIC API USED BY telemetry.py
# ============================================================

def calculate_risk(node_type, values):
    """
    Return:
        (risk_score_0_to_100, risk_level)

    The numeric score is the empirical percentile of the COPOD
    outlier score against the fitted normal baseline.

    telemetry.py remains responsible for combining this candidate
    score with physical checks, temporal persistence and nearby-node
    correlation before escalating the live network risk state.
    """

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
        score, _ = evaluate_crack(float(values["displacement_mm"]))

    score = max(0.0, min(100.0, float(score)))
    level = _level_from_score(score)

    return round(score, 2), level
