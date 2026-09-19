"""Stage 3 baseline-relative risk engine for TerraVeil REAL hardware.

This module intentionally keeps raw sensor telemetry untouched. It derives
risk-only deltas from an operator-defined session baseline so a node may be
zeroed at any resting orientation/position.

COPOD remains an advisory score. Automatic collapse severity is gated by
deterministic physical movement + persistence/correlation.
"""

import math
import os
from datetime import datetime, timezone
from statistics import median

from ml_model import calculate_risk


BASELINE_SAMPLES = max(
    3,
    int(os.environ.get("TERRAVEIL_BASELINE_SAMPLES", "5")),
)

UG_TILT_TRIGGER_DEG = float(
    os.environ.get("TERRAVEIL_UG_TILT_TRIGGER_DEG", "2.0")
)
UG_TILT_CRITICAL_DEG = float(
    os.environ.get("TERRAVEIL_UG_TILT_CRITICAL_DEG", "5.0")
)

LD_DISPLACEMENT_TRIGGER_MM = float(
    os.environ.get("TERRAVEIL_LD_TRIGGER_MM", "1.0")
)
LD_DISPLACEMENT_CRITICAL_MM = float(
    os.environ.get("TERRAVEIL_LD_CRITICAL_MM", "3.0")
)


def _angle_delta(value, baseline):
    return ((float(value) - float(baseline) + 180.0) % 360.0) - 180.0


def _circular_mean(values):
    values = [float(value) for value in values]
    if not values:
        return 0.0
    sin_sum = sum(math.sin(math.radians(value)) for value in values)
    cos_sum = sum(math.cos(math.radians(value)) for value in values)
    return math.degrees(math.atan2(sin_sum, cos_sum))


def _aware(stamp):
    value = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _fresh(previous, current_stamp):
    try:
        timeout = max(
            1.0,
            float(os.environ.get("NODE_TIMEOUT_SECONDS", "30")),
        )
        return abs((_aware(current_stamp) - _aware(previous["timestamp"])).total_seconds()) <= timeout
    except (KeyError, TypeError, ValueError):
        return False


def _deformation_present(node_type, reading):
    if node_type == "UnderGround":
        return (
            math.hypot(
                float(reading.get("tilt_x") or 0.0),
                float(reading.get("tilt_y") or 0.0),
            )
            >= UG_TILT_TRIGGER_DEG
        )
    return (
        abs(float(reading.get("displacement_delta_mm") or 0.0))
        >= LD_DISPLACEMENT_TRIGGER_MM
    )


def _bounded_risk_score(level, candidate):
    candidate = max(0.0, min(100.0, float(candidate)))
    if level == "CRITICAL":
        return round(max(90.0, candidate), 2)
    if level == "HIGH":
        return round(max(70.0, min(89.0, candidate)), 2)
    if level == "MEDIUM":
        return round(max(40.0, min(69.0, candidate)), 2)
    return round(min(39.0, candidate), 2)


def apply_stage3_risk(conn, node, row, previous, registry):
    """Mutate one REAL reading with baseline-relative Stage 3 risk fields."""

    baseline_rows = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM readings "
            "WHERE node_id=? AND data_source='REAL' "
            "AND session_id=? AND processed=1 "
            "ORDER BY id ASC LIMIT ?",
            (
                row["node_id"],
                row["session_id"],
                BASELINE_SAMPLES,
            ),
        )
    ]

    captured = len(baseline_rows)
    if row.get("processed") and captured < BASELINE_SAMPLES:
        captured += 1

    row["baseline_samples"] = min(captured, BASELINE_SAMPLES)

    # The Nth packet completes reference capture. Risk starts with the next
    # packet so no reading is judged against a baseline that contains itself.
    if len(baseline_rows) < BASELINE_SAMPLES:
        row["baseline_ready"] = int(captured >= BASELINE_SAMPLES)
        row["roll_delta"] = 0.0
        row["pitch_delta"] = 0.0
        row["displacement_delta_mm"] = 0.0

        if node["node_type"] == "UnderGround":
            row["tilt_x"] = 0.0
            row["tilt_y"] = 0.0

        row["ml_score"] = 0.0
        row["anomaly"] = 0
        row["persistent"] = 0
        row["risk_level"] = "LOW"
        row["risk_score"] = 0.0
        row["evidence"] = (
            f"CALIBRATING: baseline sample "
            f"{row['baseline_samples']}/{BASELINE_SAMPLES}. "
            "Keep the node still at its current resting position."
        )
        return

    row["baseline_ready"] = 1
    row["baseline_samples"] = BASELINE_SAMPLES

    recent = [
        item
        for item in previous
        if item.get("baseline_ready") == 1
        and _fresh(item, row["timestamp"])
    ]

    peers = []
    severe = False
    vibration_only = False
    deformation = False

    if node["node_type"] == "UnderGround":
        baseline_roll = _circular_mean(
            [r["roll"] for r in baseline_rows if r.get("roll") is not None]
        )
        baseline_pitch = _circular_mean(
            [r["pitch"] for r in baseline_rows if r.get("pitch") is not None]
        )

        raw_roll_delta = _angle_delta(row["roll"], baseline_roll)
        raw_pitch_delta = _angle_delta(row["pitch"], baseline_pitch)

        prior_roll = [
            float(r["roll_delta"])
            for r in recent[:1]
            if r.get("roll_delta") is not None
        ]
        prior_pitch = [
            float(r["pitch_delta"])
            for r in recent[:1]
            if r.get("pitch_delta") is not None
        ]

        risk_roll = median([raw_roll_delta, *prior_roll])
        risk_pitch = median([raw_pitch_delta, *prior_pitch])

        row["roll_delta"] = round(raw_roll_delta, 4)
        row["pitch_delta"] = round(raw_pitch_delta, 4)
        row["tilt_x"] = round(risk_roll, 4)
        row["tilt_y"] = round(risk_pitch, 4)
        row["displacement_delta_mm"] = None

        amplitude = math.hypot(risk_roll, risk_pitch)
        deformation = amplitude >= UG_TILT_TRIGGER_DEG
        severe = amplitude >= UG_TILT_CRITICAL_DEG

        vibration_event = float(row.get("vibration") or 0.0) >= 1.0
        vibration_only = vibration_event and not deformation

        # Soil stays visible as telemetry/context, but does not inflate the
        # movement score until a mine/site-specific soil model is calibrated.
        candidate, _ = calculate_risk(
            node["node_type"],
            {
                "tilt_x": risk_roll,
                "tilt_y": risk_pitch,
                "vibration": float(row.get("vibration") or 0.0),
            },
        )

        row["anomaly"] = int(deformation or vibration_event)

    else:
        baseline_mm = median(
            [
                float(r["displacement_mm"])
                for r in baseline_rows
                if r.get("displacement_mm") is not None
            ]
        )

        raw_change = float(row["displacement_mm"]) - baseline_mm

        prior_changes = [
            float(r["displacement_delta_mm"])
            for r in recent[:1]
            if r.get("displacement_delta_mm") is not None
        ]
        risk_change = median([raw_change, *prior_changes])

        row["roll_delta"] = None
        row["pitch_delta"] = None
        row["displacement_delta_mm"] = round(risk_change, 4)

        magnitude = abs(risk_change)
        deformation = magnitude >= LD_DISPLACEMENT_TRIGGER_MM
        severe = magnitude >= LD_DISPLACEMENT_CRITICAL_MM

        candidate, _ = calculate_risk(
            node["node_type"],
            {"displacement_delta_mm": magnitude},
        )
        row["anomaly"] = int(deformation)

    # Persistence is based on actual physical deformation only, not COPOD alone
    # and not the cheap binary vibration switch by itself.
    row["persistent"] = int(
        deformation
        and len(recent) >= 1
        and _deformation_present(node["node_type"], recent[0])
    )

    # Correlate within the same logical mine zone. This matches TerraVeil's
    # zone architecture and avoids using the demo map's illustrative 1 km
    # coordinates as though they were measured physical separation.
    if row["persistent"]:
        for other in registry.values():
            if other["node_id"] == row["node_id"]:
                continue
            if not node.get("zone_id") or other.get("zone_id") != node.get("zone_id"):
                continue

            peer = conn.execute(
                "SELECT * FROM readings "
                "WHERE data_source='REAL' "
                "AND node_id=? AND session_id=? "
                "AND processed=1 ORDER BY id DESC LIMIT 1",
                (
                    other["node_id"],
                    other.get("session_id", "legacy"),
                ),
            ).fetchone()

            if not peer:
                continue

            peer = dict(peer)
            if (
                peer.get("baseline_ready") == 1
                and peer.get("persistent")
                and _fresh(peer, row["timestamp"])
            ):
                peers.append(other["node_id"])

    row["ml_score"] = round(float(candidate), 2)

    if row["persistent"] and (severe or peers):
        level = "CRITICAL"
    elif row["persistent"]:
        level = "HIGH"
    elif row["anomaly"]:
        level = "MEDIUM"
    else:
        level = "LOW"

    row["risk_level"] = level
    row["risk_score"] = _bounded_risk_score(level, candidate)

    if level == "CRITICAL" and peers:
        row["evidence"] = (
            "CRITICAL DEFORMATION: persistent baseline-relative movement "
            f"corroborated in {node.get('zone_id') or 'the local zone'} "
            f"by {', '.join(peers)}."
        )
    elif level == "CRITICAL":
        row["evidence"] = (
            "CRITICAL DEFORMATION: severe baseline-relative movement "
            "persisted across consecutive live packets."
        )
    elif level == "HIGH":
        row["evidence"] = (
            "PERSISTENT DEFORMATION: physical movement remains above the "
            "deterministic threshold; continue local confirmation."
        )
    elif vibration_only:
        row["evidence"] = (
            "VIBRATION EVENT: local binary vibration detected, but no "
            "baseline-relative deformation threshold was crossed; "
            "collapse siren withheld."
        )
    elif row["anomaly"]:
        row["evidence"] = (
            "DEFORMATION DETECTED: baseline-relative movement crossed the "
            "physical threshold; waiting for persistence/correlation."
        )
    elif float(candidate) >= 95.0:
        row["evidence"] = (
            f"COPOD advisory outlier ({candidate:.1f}th percentile), but no "
            "deterministic movement threshold was crossed."
        )
    else:
        row["evidence"] = "Normal relative to the current operator-set baseline."
