import random
import math
import time
import threading
from datetime import datetime

from database import add_node, add_reading
from ml_model import calculate_risk


NUM_NODES = 20



# Chasnalla Colliery demo area
# Approximate center based on documented Chasnalla location
CENTER_LAT = 23.657
CENTER_LON = 86.452

MINE_LAT_MIN = 23.648
MINE_LAT_MAX = 23.672

MINE_LON_MIN = 86.435
MINE_LON_MAX = 86.468
nodes = []

simulation_step = 0


def random_mine_location():

    return (
        random.uniform(
            MINE_LAT_MIN,
            MINE_LAT_MAX
        ),
        random.uniform(
            MINE_LON_MIN,
            MINE_LON_MAX
        )
    )

def create_nodes():

    global nodes

    random.seed(42)

    for i in range(NUM_NODES):

        lat, lon = random_mine_location()
        # 70% normal UnderGround nodes
        if i < int(NUM_NODES * 0.7):

            node = {
                "node_id": f"G-{i+1:03}",
                "node_type": "UnderGround",
                "latitude": lat,
                "longitude": lon
            }

        else:

            # Crack sensor represented by two poles
            pole_a_lat = lat - 0.0003
            pole_a_lon = lon - 0.0003

            pole_b_lat = lat + 0.0003
            pole_b_lon = lon + 0.0003

            node = {
                "node_id": f"C-{i+1:03}",
                "node_type": "crack",
                "latitude": lat,
                "longitude": lon,
                "pole_a_lat": pole_a_lat,
                "pole_a_lon": pole_a_lon,
                "pole_b_lat": pole_b_lat,
                "pole_b_lon": pole_b_lon
            }

        add_node(node)
        nodes.append(node)


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
        distance = math.sqrt(
            (node["latitude"] - CENTER_LAT) ** 2 +
            (node["longitude"] - CENTER_LON) ** 2
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
        "battery": battery
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

        distance = math.sqrt(
            (node["latitude"] - CENTER_LAT) ** 2 +
            (node["longitude"] - CENTER_LON) ** 2
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
        "battery": battery
    }


def run_simulation():

    global simulation_step

    while True:

        simulation_step += 1

        for node in nodes:

            if node["node_type"] == "UnderGround":

                values = generate_ground_reading(node)

            else:

                values = generate_crack_reading(node)

            risk_score, risk_level = calculate_risk(
                node["node_type"],
                values
            )

            reading = {
                "node_id": node["node_id"],
                "timestamp": datetime.utcnow().isoformat(),

                **values,

                "risk_score": risk_score,
                "risk_level": risk_level
            }

            add_reading(reading)

        time.sleep(2)


def start_simulator():

    create_nodes()

    thread = threading.Thread(
        target=run_simulation,
        daemon=True
    )

    thread.start()