"""Validated source adapters and one offline evidence/risk ingestion path.

REAL hardware supports:
- UG-01 / UG-02: roll, pitch, binary vibration, raw soil ADC, RSSI/SNR.
- LD-01: calibrated displacement, raw potentiometer ADC, RSSI/SNR.
"""
import math
import os
from statistics import median
from datetime import datetime, timezone, timedelta

from database import get_connection, get_nodes, get_latest_readings, mode
from ml_model import calculate_risk
from digital_twin import distance


def timeout_seconds():
    return max(1, float(os.environ.get("NODE_TIMEOUT_SECONDS", "15")))


def utcnow():
    return datetime.now(timezone.utc)


def decorate(row):
    stamp = row.get("last_seen") or row["timestamp"]
    seen = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    row["last_seen"] = stamp
    row["online"] = (utcnow() - seen).total_seconds() <= timeout_seconds()
    row["status"] = (
        (
            "CRITICAL"
            if row["risk_level"] == "CRITICAL"
            else "WARNING"
            if row["risk_level"] in ("MEDIUM", "HIGH")
            else "ONLINE"
        )
        if row["online"]
        else "OFFLINE"
    )
    return row


def number(payload, key, low, high, integer=False):
    value = payload.get(key)
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or (isinstance(value, float) and not math.isfinite(value))
    ):
        raise ValueError(f"{key} must be a finite number")
    if integer and not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    if not low <= value <= high:
        raise ValueError(f"{key} outside [{low}, {high}]")
    return value


def _real_common_fields(payload, node, row):
    if payload.get("host_id") != node["host_id"] or payload.get("zone_id") != node["zone_id"]:
        raise ValueError("host_id/zone_id do not match registered node")

    row.update(
        host_id=node["host_id"],
        zone_id=node["zone_id"],
        sequence=number(payload, "sequence", 0, 4294967295, True),
        rssi=number(payload, "rssi", -200, 20),
        snr=number(payload, "snr", -30, 30),
    )


def _real_underground_fields(payload, row):
    row["roll"] = number(payload, "roll", -180, 180)
    row["pitch"] = number(payload, "pitch", -180, 180)
    row["vibration"] = number(payload, "vibration", 0, 1)
    row["soil"] = number(payload, "soil", 0, 4095)

    if row["vibration"] not in (0, 1):
        raise ValueError("vibration must be 0 or 1")

    row["tilt_x"] = row["roll"]
    row["tilt_y"] = row["pitch"]


def _real_displacement_fields(payload, row):
    row["displacement_mm"] = number(payload, "displacement_mm", 0, 10000)
    row["potentiometer_raw"] = number(payload, "potentiometer_raw", 0, 4095)

    # Optional LD MPU orientation is useful for diagnostics and is stored if sent,
    # but COPOD/risk for the displacement node remains based on displacement_mm.
    if "roll" in payload:
        row["roll"] = number(payload, "roll", -180, 180)
    if "pitch" in payload:
        row["pitch"] = number(payload, "pitch", -180, 180)


def ingest(payload, source="REAL"):
    if not isinstance(payload, dict):
        raise ValueError("JSON object required")

    registry = {n["node_id"]: n for n in get_nodes(source)}
    node_id = payload.get("node_id")
    if not isinstance(node_id, str) or node_id not in registry:
        raise ValueError("Unknown node_id")

    node = registry[node_id]
    now = utcnow()
    row = dict(
        node_id=node_id,
        data_source=source,
        timestamp=now.isoformat(),
        server_timestamp=now.isoformat(),
        last_seen=now.isoformat(),
        processed=1,
    )

    if source == "REAL":
        _real_common_fields(payload, node, row)

        if node["node_type"] == "UnderGround":
            _real_underground_fields(payload, row)
        else:
            _real_displacement_fields(payload, row)

        value = payload.get("queue_age_ms", 0)
        if "queue_age_ms" in payload:
            row["queue_age_ms"] = number(
                payload, "queue_age_ms", 0, 31536000000, True
            )
        else:
            row["queue_age_ms"] = 0

        row["last_seen"] = (
            now - timedelta(milliseconds=row["queue_age_ms"])
        ).isoformat()
        row["timestamp"] = row["last_seen"]

    else:
        fields = [("battery", 0, 100)]
        if node["node_type"] == "UnderGround":
            fields += [
                ("tilt_x", -180, 180),
                ("tilt_y", -180, 180),
                ("vibration", 0, 100),
                ("temperature", -100, 200),
                ("humidity", 0, 100),
            ]
        else:
            fields += [
                ("displacement_mm", 0, 10000),
                ("potentiometer_raw", 0, 4095),
            ]
        for key, low, high in fields:
            row[key] = number(payload, key, low, high)

    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")

        if source == "SIMULATION" and conn.execute(
            "SELECT value FROM settings WHERE key='mode'"
        ).fetchone()[0] != "SIMULATION":
            return dict(success=True, skipped=True)

        row["session_id"] = (
            conn.execute(
                "SELECT session_id FROM hardware_nodes WHERE node_id=?",
                (node_id,),
            ).fetchone()[0]
            if source == "REAL"
            else "legacy"
        )

        if source == "REAL":
            duplicate = conn.execute(
                "SELECT id FROM readings WHERE data_source='REAL' "
                "AND node_id=? AND session_id=? AND sequence=?",
                (node_id, row["session_id"], row["sequence"]),
            ).fetchone()
            if duplicate:
                return dict(success=True, duplicate=True, id=duplicate["id"])

        previous = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM readings WHERE node_id=? AND data_source=? "
                "AND session_id=? AND processed=1 ORDER BY id DESC LIMIT 5",
                (node_id, source, row["session_id"]),
            )
        ]

        if source == "REAL" and previous and row["sequence"] < previous[0]["sequence"]:
            row["processed"] = 0

        filtered = dict(row)
        for field in ("tilt_x", "tilt_y", "displacement_mm"):
            if field in row:
                prior = [
                    r[field]
                    for r in previous[:2]
                    if r.get(field) is not None
                    and abs(
                        (
                            datetime.fromisoformat(row["timestamp"])
                            - datetime.fromisoformat(r["timestamp"]).replace(
                                tzinfo=timezone.utc
                            )
                        ).total_seconds()
                    )
                    <= timeout_seconds()
                ]
                filtered[field] = median([row[field], *prior])

        candidate, ml_level = calculate_risk(
            node["node_type"],
            filtered
        )

        # Preserve the raw COPOD result separately from the
        # network-validated TerraVeil risk state.
        row["ml_score"] = round(candidate, 2)
        amplitude = math.hypot(row.get("tilt_x", 0), row.get("tilt_y", 0))
        abnormal = (
            amplitude >= 0.8
            or row.get("vibration", 0) >= 0.8
            or row.get("displacement_mm", 0) >= 4
        )
        row["anomaly"] = int(abnormal and candidate >= 40)

        recent = [
            r
            for r in previous
            if abs(
                (
                    datetime.fromisoformat(row["timestamp"])
                    - datetime.fromisoformat(r["timestamp"]).replace(
                        tzinfo=timezone.utc
                    )
                ).total_seconds()
            )
            <= timeout_seconds()
        ]
        row["persistent"] = int(
            row["anomaly"]
            and len(recent) >= 2
            and all(r["anomaly"] for r in recent[:2])
        )

        trend = bool(
            recent
            and (
                amplitude
                > math.hypot(
                    recent[0].get("tilt_x") or 0,
                    recent[0].get("tilt_y") or 0,
                )
                + 0.05
                or row.get("displacement_mm", 0)
                > (recent[0].get("displacement_mm") or 0) + 0.05
            )
        )

        peers = []
        if (
            row["persistent"]
            and node.get("latitude") is not None
            and decorate(row | {"risk_level": "LOW"})["online"]
        ):
            for other in registry.values():
                if (
                    other["node_id"] == node_id
                    or other.get("latitude") is None
                    or distance(node, other) > 500
                ):
                    continue
                rd = conn.execute(
                    "SELECT * FROM readings WHERE data_source=? AND node_id=? "
                    "AND session_id=? AND processed=1 ORDER BY id DESC LIMIT 1",
                    (source, other["node_id"], other.get("session_id", "legacy")),
                ).fetchone()
                if rd and rd["persistent"] and decorate(dict(rd))["online"]:
                    peers.append(other["node_id"])

        level = "MEDIUM" if row["anomaly"] else "LOW"
        if row["persistent"] and peers:
            level = "HIGH"
            if (
                trend
                and row.get("vibration", 0) >= 1
                and amplitude >= 5
                and len(recent) >= 4
                and all(r["persistent"] for r in recent[:3])
            ):
                level = "CRITICAL"

        row["risk_level"] = level
        row["risk_score"] = round(
            min(candidate, {"LOW": 39, "MEDIUM": 69, "HIGH": 89, "CRITICAL": 100}[level]),
            2,
        )
        row["evidence"] = (
            "SUBSIDENCE RISK: persistent correlated evidence"
            if peers
            else "ANOMALY DETECTED: unconfirmed; inspect and track persistence"
            if row["anomaly"]
            else "No anomaly detected in available sensors"
        )

        if not row["processed"]:
            row.update(
                risk_level=None,
                risk_score=None,
                anomaly=None,
                persistent=None,
                evidence="Historical out-of-order packet; not applied to live state",
            )

        columns = ",".join(row)
        cursor = conn.execute(
            f"INSERT INTO readings ({columns}) VALUES ({','.join('?' for _ in row)})",
            tuple(row.values()),
        )
        return dict(
            success=True,
            duplicate=False,
            applied=bool(row["processed"]),
            id=cursor.lastrowid,
            server_timestamp=row["server_timestamp"],
            risk_level=row["risk_level"],
        )


def network_state(source=None):
    from serial_bridge import health

    source = source or mode()
    readings = get_latest_readings(source)
    live = any(r["online"] for r in readings)
    with get_connection() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM readings WHERE data_source=?", (source,)
        ).fetchone()[0]
    return dict(
        mode=source,
        usb=health(),
        lora_network=("ONLINE" if live else "OFFLINE") if source == "REAL" else "SIMULATION",
        host_01=("ONLINE" if live else "UNCONFIRMED") if source == "REAL" else "SIMULATION",
        mother_host="ONLINE",
        database="LOCAL",
        ml_engine="ACTIVE",
        readings_count=count,
        timeout_seconds=timeout_seconds(),
    )
