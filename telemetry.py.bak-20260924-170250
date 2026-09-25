"""Validated TerraVeil telemetry ingestion and freshness state.

Drop-in replacement fixes REAL HARDWARE nodes getting stuck OFFLINE after a
physical reboot while preserving stale/buffered packet protection.
"""

import math
import os
from datetime import datetime, timezone, timedelta
from statistics import median
from uuid import uuid4

from database import get_connection, get_nodes, get_latest_readings, mode
from ml_model import calculate_risk
from digital_twin import distance
from stage3_risk import apply_stage3_risk


def timeout_seconds():
    # 30 s is intentionally tolerant of a three-node polling round and a few
    # missed LoRa turns while still detecting genuinely stale telemetry quickly.
    return max(1, float(os.environ.get("NODE_TIMEOUT_SECONDS", "30")))


def utcnow():
    return datetime.now(timezone.utc)


def _as_aware(stamp):
    value = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def decorate(row):
    stamp = row.get("last_seen") or row["timestamp"]
    seen = _as_aware(stamp)
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


def _real_mpu_fields(payload, row):
    """Persist the complete MPU6050 payload when supplied by HOST-01.

    Roll/pitch remain the safety-engine tilt inputs. The additional orientation,
    acceleration and gyro values are stored for live visualization/diagnostics
    and do not change the existing risk decision path.
    """
    limits = {
        "roll": (-180, 180),
        "pitch": (-180, 180),
        "yaw": (-180, 180),
        # Wider than the configured +-2 g / +-250 dps ranges so brief numerical
        # overshoot or future MPU range changes do not discard a valid packet.
        "ax": (-16, 16),
        "ay": (-16, 16),
        "az": (-16, 16),
        "gx": (-2000, 2000),
        "gy": (-2000, 2000),
        "gz": (-2000, 2000),
    }
    for key, (low, high) in limits.items():
        if key in payload:
            row[key] = number(payload, key, low, high)


def _real_underground_fields(payload, row):
    row["roll"] = number(payload, "roll", -180, 180)
    row["pitch"] = number(payload, "pitch", -180, 180)
    _real_mpu_fields(payload, row)

    row["vibration"] = number(payload, "vibration", 0, 1)
    row["soil"] = number(payload, "soil", 0, 4095)

    if row["vibration"] not in (0, 1):
        raise ValueError("vibration must be 0 or 1")

    row["tilt_x"] = row["roll"]
    row["tilt_y"] = row["pitch"]


def _real_displacement_fields(payload, row):
    row["displacement_mm"] = number(payload, "displacement_mm", 0, 10000)
    row["potentiometer_raw"] = number(payload, "potentiometer_raw", 0, 4095)
    _real_mpu_fields(payload, row)


def _previous_is_stale(previous, now):
    if not previous:
        return True
    stamp = previous.get("last_seen") or previous.get("timestamp")
    if not stamp:
        return True
    try:
        return (now - _as_aware(stamp)).total_seconds() > timeout_seconds()
    except (TypeError, ValueError):
        return True


def _is_fresh_live_counter_reset(payload, previous, now):
    """Return True only for a likely physical node reboot.

    We automatically rotate the backend session only for non-buffered live serial
    formats. Buffered TELEMETRY replay never triggers this, so stale gateway data
    still cannot make a dead node appear live.
    """
    if not previous:
        return False

    new_sequence = payload.get("sequence")
    old_sequence = previous.get("sequence")
    if type(new_sequence) is not int or type(old_sequence) is not int:
        return False
    if new_sequence >= old_sequence:
        return False

    # Nodes start again near zero after an ESP32 reboot. Do not infer a reboot
    # from arbitrary out-of-order numbers.
    if new_sequence > 3:
        return False

    # Only immediate/live transports are eligible. Buffered gateway replays must
    # continue to use the explicit manual-session path if needed.
    if payload.get("_transport") not in ("HUB_DATA", "LEGACY"):
        return False

    queue_age_ms = payload.get("queue_age_ms", 0)
    if type(queue_age_ms) is not int or queue_age_ms < 0:
        return False
    if queue_age_ms > 5000:
        return False

    # Strong evidence of a reboot: either the former reading has already gone
    # stale, or the old counter had clearly progressed before suddenly returning
    # to the startup range.
    return _previous_is_stale(previous, now) or old_sequence >= 10


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

        if "queue_age_ms" in payload:
            row["queue_age_ms"] = number(payload, "queue_age_ms", 0, 31536000000, True)
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

        new_session = False

        if source == "REAL":
            session_row = conn.execute(
                "SELECT session_id FROM hardware_nodes WHERE node_id=?",
                (node_id,),
            ).fetchone()
            row["session_id"] = session_row[0]

            previous_row = conn.execute(
                "SELECT * FROM readings WHERE node_id=? AND data_source='REAL' "
                "AND session_id=? AND processed=1 ORDER BY id DESC LIMIT 1",
                (node_id, row["session_id"]),
            ).fetchone()
            previous_latest = dict(previous_row) if previous_row else None

            # IMPORTANT: detect a genuine fresh counter reset BEFORE duplicate
            # lookup. Otherwise rebooted sequence 1/2/3 is mistaken for an old
            # duplicate and last_seen can never recover.
            if _is_fresh_live_counter_reset(payload, previous_latest, now):
                row["session_id"] = uuid4().hex
                conn.execute(
                    "UPDATE hardware_nodes SET session_id=? WHERE node_id=?",
                    (row["session_id"], node_id),
                )
                previous_latest = None
                new_session = True

            duplicate = conn.execute(
                "SELECT id FROM readings WHERE data_source='REAL' "
                "AND node_id=? AND session_id=? AND sequence=?",
                (node_id, row["session_id"], row["sequence"]),
            ).fetchone()
            if duplicate:
                return dict(success=True, duplicate=True, id=duplicate["id"])

        else:
            row["session_id"] = "legacy"

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

        relative_mode = (
            source == "REAL"
            and bool(node.get("relative_baseline"))
        )

        if relative_mode:
            apply_stage3_risk(
                conn,
                node,
                row,
                previous,
                registry,
            )
        else:
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
                                - datetime.fromisoformat(r["timestamp"]).replace(tzinfo=timezone.utc)
                            ).total_seconds()
                        )
                        <= timeout_seconds()
                    ]
                    filtered[field] = median([row[field], *prior])

            # ------------------------------------------------------------
            # LD-01 displacement reference
            # ------------------------------------------------------------
            # A linear potentiometer reports an absolute shaft position. For
            # subsidence monitoring we care about MOVEMENT away from the position
            # observed at the beginning of the current node session. This also
            # means LD-01 can power on at any point along its mechanical travel
            # without being declared anomalous just because the absolute mm value
            # is large.
            ld_change_mm = 0.0
            ld_baseline_mm = None

            if node["node_type"] != "UnderGround":
                baseline_row = conn.execute(
                    "SELECT displacement_mm FROM readings "
                    "WHERE node_id=? AND data_source=? AND session_id=? "
                    "AND processed=1 AND displacement_mm IS NOT NULL "
                    "ORDER BY id ASC LIMIT 1",
                    (node_id, source, row["session_id"]),
                ).fetchone()

                ld_baseline_mm = (
                    float(baseline_row["displacement_mm"])
                    if baseline_row is not None
                    else float(row["displacement_mm"])
                )
                ld_change_mm = abs(float(row["displacement_mm"]) - ld_baseline_mm)

                # COPOD for LD-01 sees displacement CHANGE, not absolute position.
                filtered["displacement_delta_mm"] = ld_change_mm

            candidate, _ml_level = calculate_risk(node["node_type"], filtered)

            row["ml_score"] = round(candidate, 2)
            amplitude = math.hypot(row.get("tilt_x", 0), row.get("tilt_y", 0))

            if node["node_type"] == "UnderGround":
                # UG-01 / UG-02 behaviour remains unchanged.
                abnormal = (
                    amplitude >= 0.8
                    or row.get("vibration", 0) >= 0.8
                )
            else:
                # LD-01: >= 1 mm movement from the current-session reference is
                # physically meaningful enough for the prototype anomaly demo.
                # COPOD must ALSO judge the movement as unusual.
                abnormal = ld_change_mm >= 1.0

            row["anomaly"] = int(abnormal and candidate >= 40)

            recent = [
                r
                for r in previous
                if abs(
                    (
                        datetime.fromisoformat(row["timestamp"])
                        - datetime.fromisoformat(r["timestamp"]).replace(tzinfo=timezone.utc)
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
            if peers:
                row["evidence"] = "SUBSIDENCE RISK: persistent correlated evidence"
            elif row["anomaly"] and node["node_type"] != "UnderGround":
                row["evidence"] = (
                    f"ANOMALY DETECTED: LD-01 displacement changed "
                    f"{ld_change_mm:.2f} mm from session baseline "
                    f"({ld_baseline_mm:.2f} mm); inspect and track persistence"
                )
            elif row["anomaly"]:
                row["evidence"] = (
                    "ANOMALY DETECTED: unconfirmed; inspect and track persistence"
                )
            else:
                row["evidence"] = "No anomaly detected in available sensors"

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
            new_session=new_session,
            id=cursor.lastrowid,
            server_timestamp=row["server_timestamp"],
            risk_level=row["risk_level"],
        )


def network_state(source=None):
    from serial_bridge import health

    source = source or mode()
    readings = get_latest_readings(source)
    live = any(r["online"] for r in readings)
    usb = health()
    usb_open = usb.get("status") == "OPEN"

    with get_connection() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM readings WHERE data_source=?", (source,)
        ).fetchone()[0]

    return dict(
        mode=source,
        usb=usb,
        # LoRa/network freshness is based on actual fresh node packets.
        lora_network=("ONLINE" if live else "OFFLINE") if source == "REAL" else "SIMULATION",
        # HOST-01 itself is a USB-connected device, so report its own transport
        # state independently from whether a field node has spoken recently.
        host_01=("ONLINE" if usb_open else "OFFLINE") if source == "REAL" else "SIMULATION",
        mother_host="ONLINE",
        database="LOCAL",
        ml_engine="ACTIVE",
        readings_count=count,
        timeout_seconds=timeout_seconds(),
        online_nodes=sum(1 for r in readings if r["online"]),
    )
