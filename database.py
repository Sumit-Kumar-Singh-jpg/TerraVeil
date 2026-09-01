import sqlite3
from datetime import datetime

DB_NAME = "terraveil.db"


def get_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


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
        node.get("pole_b_lon")
    ))

    conn.commit()
    conn.close()


def add_reading(reading):
    conn = get_connection()

    conn.execute("""
        INSERT INTO readings (
            node_id, timestamp,
            tilt_x, tilt_y,
            vibration, temperature, humidity,
            displacement_mm, potentiometer_raw,
            battery, risk_score, risk_level
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        reading["node_id"],
        reading["timestamp"],
        reading.get("tilt_x"),
        reading.get("tilt_y"),
        reading.get("vibration"),
        reading.get("temperature"),
        reading.get("humidity"),
        reading.get("displacement_mm"),
        reading.get("potentiometer_raw"),
        reading["battery"],
        reading["risk_score"],
        reading["risk_level"]
    ))

    conn.commit()
    conn.close()


def get_nodes():
    conn = get_connection()

    rows = conn.execute("SELECT * FROM nodes").fetchall()

    conn.close()

    return [dict(row) for row in rows]


def get_latest_readings():
    conn = get_connection()

    rows = conn.execute("""
        SELECT r.*
        FROM readings r
        INNER JOIN (
            SELECT node_id, MAX(timestamp) AS latest
            FROM readings
            GROUP BY node_id
        ) latest_reading
        ON r.node_id = latest_reading.node_id
        AND r.timestamp = latest_reading.latest
    """).fetchall()

    conn.close()

    return [dict(row) for row in rows]


def get_history(node_id, limit=100):
    conn = get_connection()

    rows = conn.execute("""
        SELECT *
        FROM readings
        WHERE node_id = ?
        ORDER BY timestamp DESC
        LIMIT ?
    """, (node_id, limit)).fetchall()

    conn.close()

    return [dict(row) for row in reversed(rows)]