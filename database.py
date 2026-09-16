import sqlite3
import os
from pathlib import Path
from datetime import datetime

DB_NAME = os.environ.get("TERRAVEIL_DB", str(Path(__file__).with_name("terraveil.db")))


HARDWARE_NODES = (
    ("UG-01", "UnderGround", "HOST-01", "ZONE-A"),
    ("UG-02", "UnderGround", "HOST-01", "ZONE-A"),
    ("LD-01", "Crack",       "HOST-01", "ZONE-A"),
)

# Demo GIS positions for the three REAL physical nodes.
# These are visualization/demo coordinates, NOT measured GPS positions.
HARDWARE_DEMO_COORDS = {
    "UG-01": (23.6570, 86.4515),
    "UG-02": (23.6580, 86.4530),
    "LD-01": (23.6564, 86.4540),
}

def get_connection():
    conn = sqlite3.connect(DB_NAME, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def _coordinate_env_prefix(node_id):
    return node_id.replace("-", "")


def _apply_hardware_coordinates(conn, node_id):
    prefix = _coordinate_env_prefix(node_id)
    lat_name = f"{prefix}_LATITUDE"
    lon_name = f"{prefix}_LONGITUDE"
    lat_value = os.environ.get(lat_name)
    lon_value = os.environ.get(lon_name)

    if lat_value is None and lon_value is None:
        return
    if lat_value is None or lon_value is None:
        raise ValueError(f"Set both {lat_name} and {lon_name}")

    lat, lon = float(lat_value), float(lon_value)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError(f"Invalid coordinates for {node_id}")

    conn.execute(
        "UPDATE hardware_nodes SET latitude=?,longitude=? WHERE node_id=?",
        (lat, lon, node_id),
    )


def init_db():
    conn = get_connection()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS nodes (
            node_id TEXT PRIMARY KEY,
            node_type TEXT NOT NULL,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            pole_a_lat REAL,
            pole_a_lon REAL,
            pole_b_lat REAL,
            pole_b_lon REAL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            node_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,

            tilt_x REAL,
            tilt_y REAL,
            vibration REAL,
            temperature REAL,
            humidity REAL,

            displacement_mm REAL,
            potentiometer_raw REAL,

            battery REAL,
            risk_score REAL,
            risk_level TEXT,

            FOREIGN KEY(node_id) REFERENCES nodes(node_id)
        )
    """)

    columns = {row[1] for row in conn.execute("PRAGMA table_info(readings)")}
    additions = {
        "data_source": "TEXT NOT NULL DEFAULT 'SIMULATION'",
        "host_id": "TEXT",
        "zone_id": "TEXT",
        "sequence": "INTEGER",
        "session_id": "TEXT NOT NULL DEFAULT 'legacy'",
        "roll": "REAL",
        "pitch": "REAL",
        "soil": "REAL",
        "rssi": "REAL",
        "snr": "REAL",
        "server_timestamp": "TEXT",
        "last_seen": "TEXT",
        "queue_age_ms": "INTEGER",
        "anomaly": "INTEGER",
        "persistent": "INTEGER",
        "evidence": "TEXT",
        "processed": "INTEGER NOT NULL DEFAULT 1",
    }
    for name, definition in additions.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE readings ADD COLUMN {name} {definition}")

    conn.execute("DROP INDEX IF EXISTS real_packet")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS real_packet_session "
        "ON readings(node_id, session_id, sequence) WHERE data_source='REAL'"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS reading_source_node "
        "ON readings(data_source,node_id,id)"
    )

    conn.execute(
        "CREATE TABLE IF NOT EXISTS settings "
        "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT OR IGNORE INTO settings VALUES ('mode', ?)",
        (os.environ.get("TERRAVEIL_MODE", "REAL"),),
    )

    conn.execute(
        "CREATE TABLE IF NOT EXISTS hardware_nodes "
        "(node_id TEXT PRIMARY KEY, node_type TEXT, latitude REAL, longitude REAL, "
        "host_id TEXT, zone_id TEXT)"
    )
    hardware_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(hardware_nodes)")
    }
    if "session_id" not in hardware_columns:
        conn.execute(
            "ALTER TABLE hardware_nodes "
            "ADD COLUMN session_id TEXT NOT NULL DEFAULT 'legacy'"
        )

    for node_id, node_type, host_id, zone_id in HARDWARE_NODES:

        demo_lat, demo_lon = HARDWARE_DEMO_COORDS[node_id]

        conn.execute(
            "INSERT OR IGNORE INTO hardware_nodes "
            "(node_id,node_type,latitude,longitude,host_id,zone_id) "
            "VALUES (?,?,?,?,?,?)",
            (
                node_id,
                node_type,
                demo_lat,
                demo_lon,
                host_id,
                zone_id,
            ),
        )

        # Keep the REAL hardware registry fixed to exactly these 3 nodes
        # and ensure older databases also receive the demo coordinates.
        conn.execute(
            "UPDATE hardware_nodes "
            "SET node_type=?, host_id=?, zone_id=?, latitude=?, longitude=? "
            "WHERE node_id=?",
            (
                node_type,
                host_id,
                zone_id,
                demo_lat,
                demo_lon,
                node_id,
            ),
        )

        # Environment coordinates still override demo coordinates if supplied.
        _apply_hardware_coordinates(conn, node_id)

    conn.commit()
    conn.close()


def add_node(node):
    conn = get_connection()
    conn.execute("""
        INSERT OR IGNORE INTO nodes
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        node["node_id"],
        node["node_type"],
        node["latitude"],
        node["longitude"],
        node.get("pole_a_lat"),
        node.get("pole_a_lon"),
        node.get("pole_b_lat"),
        node.get("pole_b_lon"),
    ))
    conn.commit()
    conn.close()


def mode():
    with get_connection() as conn:
        return conn.execute(
            "SELECT value FROM settings WHERE key='mode'"
        ).fetchone()[0]


def set_mode(value):
    if value not in ("REAL", "SIMULATION"):
        raise ValueError("mode must be REAL or SIMULATION")
    with get_connection() as conn:
        conn.execute("UPDATE settings SET value=? WHERE key='mode'", (value,))


def add_reading(reading):
    from telemetry import ingest
    return ingest(reading, "SIMULATION")


def get_nodes(source=None):
    source = source or mode()
    with get_connection() as conn:
        table = "hardware_nodes" if source == "REAL" else "nodes"
        return [dict(r) for r in conn.execute(f"SELECT * FROM {table}")]


def get_latest_readings(source=None):
    source = source or mode()
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT * FROM readings WHERE id IN
            (SELECT MAX(r.id) FROM readings r
             WHERE r.data_source=? AND r.processed=1
             AND (r.data_source='SIMULATION' OR r.session_id=(
                 SELECT h.session_id FROM hardware_nodes h WHERE h.node_id=r.node_id
             ))
             GROUP BY r.node_id)
        """, (source,)).fetchall()
    from telemetry import decorate
    return [decorate(dict(r)) for r in rows]


def get_history(node_id, limit=100, source=None):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM readings WHERE node_id=? AND data_source=? "
            "ORDER BY id DESC LIMIT ?",
            (node_id, source or mode(), limit),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def get_recent_events(source=None, limit=30):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM readings WHERE data_source=? AND processed=1 "
            "AND (anomaly=1 OR vibration=1) ORDER BY id DESC LIMIT ?",
            (source or mode(), limit),
        ).fetchall()
    from telemetry import decorate
    return [decorate(dict(r)) for r in rows]


def start_node_session(node_id):
    """Explicit operator-confirmed node restart; never inferred from packet order."""
    from uuid import uuid4

    session_id = uuid4().hex
    with get_connection() as conn:
        result = conn.execute(
            "UPDATE hardware_nodes SET session_id=? WHERE node_id=?",
            (session_id, node_id),
        )
        if result.rowcount != 1:
            raise ValueError("Unknown hardware node")
    return session_id
