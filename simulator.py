import random
import math
import time
import threading
from datetime import datetime

from database import add_reading, mode, get_nodes


# Chasnalla Colliery demo area — centre used for spatial deformation gradient.
CENTER_LAT = 23.657
CENTER_LON = 86.452

nodes = []

simulation_step = 0


def create_nodes():
    """Populate from placement-predicted nodes only (no fixed generator)."""
    nodes.clear()
    nodes.extend(get_nodes('SIMULATION'))


def generate_ground_reading(node):

    global simulation_step

    # Normal behaviour
    tilt_x = random.gauss(0, 0.08)
    tilt_y = random.gauss(0, 0.08)

    vibration = abs(random.gauss(0.15, 0.05))

    temperature = random.gauss(30, 2)
    humidity = random.gauss(60, 7)

    # Gradually introduce subsidence
    if simulation_step > 30:

        progress = min(
            1.0,
            (simulation_step - 30) / 100
        )

        # Nodes near the centre experience more deformation
        node_lat = node.get("latitude") if node.get("latitude") is not None else CENTER_LAT
        node_lon = node.get("longitude") if node.get("longitude") is not None else CENTER_LON
        distance = math.sqrt(
            (node_lat - CENTER_LAT) ** 2 +
            (node_lon - CENTER_LON) ** 2
        )

        spatial_factor = max(
            0,
            1 - distance / 0.02
        )

        deformation = progress * spatial_factor

        tilt_x += deformation * 2.5
        tilt_y += deformation * 1.7
        vibration += deformation * 2.5

    battery = max(
        10,
        100 - simulation_step * 0.03 + random.uniform(-0.2, 0.2)
    )

    return {
        "tilt_x": tilt_x,
        "tilt_y": tilt_y,
        "vibration": vibration,
        "temperature": temperature,
        "humidity": humidity,
        "battery": min(100, battery)
    }


def generate_crack_reading(node):

    global simulation_step

    displacement = abs(
        random.gauss(0.5, 0.15)
    )

    if simulation_step > 30:

        progress = min(
            1,
            (simulation_step - 30) / 100
        )

        node_lat = node.get("latitude") if node.get("latitude") is not None else CENTER_LAT
        node_lon = node.get("longitude") if node.get("longitude") is not None else CENTER_LON
        distance = math.sqrt(
            (node_lat - CENTER_LAT) ** 2 +
            (node_lon - CENTER_LON) ** 2
        )

        spatial_factor = max(
            0,
            1 - distance / 0.02
        )

        displacement += (
            progress *
            spatial_factor *
            12
        )

    potentiometer_raw = (
        displacement / 20
    ) * 4095

    battery = max(
        10,
        100 - simulation_step * 0.03 + random.uniform(-0.2, 0.2)
    )

    return {
        "displacement_mm": displacement,
        "potentiometer_raw": potentiometer_raw,
        "battery": min(100, battery)
    }


def run_simulation():

    global simulation_step

    while True:

        if mode() != "SIMULATION":
            time.sleep(2)
            continue

        simulation_step += 1

        for node in get_nodes('SIMULATION'):

            if node["node_type"] == "UnderGround":

                values = generate_ground_reading(node)

            else:

                values = generate_crack_reading(node)

            reading = {"node_id": node["node_id"], **values}

            try:
                add_reading(reading)
            except ValueError:
                if node['node_id'] in {n['node_id'] for n in get_nodes('SIMULATION')}:
                    raise

        time.sleep(2)


_started = False

def start_simulator():
    global _started
    if _started:
        return
    _started = True

    create_nodes()

    thread = threading.Thread(
        target=run_simulation,
        daemon=True
    )

    thread.start()


_hw_sim_started = False
_hw_sim_stop = threading.Event()


def run_hardware_simulation(ingest_fn):
    seq = {"UG-01": 1, "UG-02": 1, "LD-01": 1}
    try:
        from database import get_connection
        with get_connection() as conn:
            for nid in seq.keys():
                row = conn.execute(
                    "SELECT MAX(sequence) FROM readings WHERE node_id=? AND data_source='REAL'",
                    (nid,),
                ).fetchone()
                if row and row[0] is not None:
                    seq[nid] = int(row[0]) + 1
    except Exception:
        pass

    while not _hw_sim_stop.is_set():
        try:
            # If physical USB serial is actively connected and feeding, yield to real hardware
            try:
                import serial_bridge
                if serial_bridge.health().get("status") == "CONNECTED":
                    _hw_sim_stop.wait(2.0)
                    continue
            except Exception:
                pass

            # UG-01 packet
            roll1 = round(random.gauss(0.18, 0.04), 2)
            pitch1 = round(random.gauss(-0.09, 0.04), 2)
            soil1 = random.randint(2190, 2225)
            p1 = {
                "node_id": "UG-01",
                "host_id": "HOST-01",
                "zone_id": "ZONE-A",
                "sequence": seq["UG-01"],
                "roll": roll1,
                "pitch": pitch1,
                "vibration": 0,
                "soil": soil1,
                "rssi": -62,
                "snr": 9.25,
            }
            seq["UG-01"] += 1
            ingest_fn(p1)

            # UG-02 packet
            roll2 = round(random.gauss(-0.32, 0.04), 2)
            pitch2 = round(random.gauss(0.24, 0.04), 2)
            soil2 = random.randint(2160, 2195)
            p2 = {
                "node_id": "UG-02",
                "host_id": "HOST-01",
                "zone_id": "ZONE-A",
                "sequence": seq["UG-02"],
                "roll": roll2,
                "pitch": pitch2,
                "vibration": 0,
                "soil": soil2,
                "rssi": -65,
                "snr": 8.80,
            }
            seq["UG-02"] += 1
            ingest_fn(p2)

            # LD-01 packet
            disp = round(abs(random.gauss(1.15, 0.03)), 2)
            pot = random.randint(1840, 1875)
            p3 = {
                "node_id": "LD-01",
                "host_id": "HOST-01",
                "zone_id": "ZONE-A",
                "sequence": seq["LD-01"],
                "displacement_mm": disp,
                "potentiometer_raw": pot,
                "rssi": -68,
                "snr": 8.10,
            }
            seq["LD-01"] += 1
            ingest_fn(p3)

        except Exception:
            pass

        _hw_sim_stop.wait(2.0)


def start_hardware_simulator(ingest_fn):
    global _hw_sim_started
    if _hw_sim_started:
        return
    _hw_sim_started = True
    thread = threading.Thread(
        target=run_hardware_simulation,
        args=(ingest_fn,),
        daemon=True,
    )
    thread.start()