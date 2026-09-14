import numpy as np
from sklearn.ensemble import IsolationForest


# --------------------------------------------------
# UnderGround sensor model
# --------------------------------------------------

ground_model = IsolationForest(
    contamination=0.03,
    random_state=42
)

normal_ground_data = []

rng = np.random.default_rng(42)

for _ in range(2000):

    tilt_x = rng.normal(0, 0.08)
    tilt_y = rng.normal(0, 0.08)
    vibration = abs(rng.normal(0.15, 0.05))
    temperature = rng.normal(30, 3)
    humidity = np.clip(rng.normal(60, 8), 20, 95)

    normal_ground_data.append([
        tilt_x,
        tilt_y,
        vibration,
        temperature,
        humidity
    ])

ground_model.fit(normal_ground_data)
# Capability-specific model: never impute absent BME280 measurements.
imu_model = IsolationForest(contamination=0.03, random_state=42)
imu_model.fit(np.asarray(normal_ground_data)[:, :3])


# --------------------------------------------------
# Crack/displacement model
# --------------------------------------------------

crack_model = IsolationForest(
    contamination=0.03,
    random_state=42
)

normal_crack_data = []

for _ in range(2000):

    displacement = abs(rng.normal(0.5, 0.15))

    normal_crack_data.append([
        displacement
    ])

crack_model.fit(normal_crack_data)


def evaluate_ground(tilt_x, tilt_y, vibration, temperature, humidity):

    x = np.array([[
        tilt_x,
        tilt_y,
        vibration,
        temperature,
        humidity
    ]])

    prediction = ground_model.predict(x)[0]
    anomaly_strength = -ground_model.score_samples(x)[0]

    return prediction, anomaly_strength


def evaluate_crack(displacement):

    x = np.array([[displacement]])

    prediction = crack_model.predict(x)[0]
    anomaly_strength = -crack_model.score_samples(x)[0]

    return prediction, anomaly_strength


def calculate_risk(node_type, values):

    if node_type == "UnderGround":

        if values.get('temperature') is None or values.get('humidity') is None:
            features = [[values['tilt_x'], values['tilt_y'], values['vibration']]]
            prediction = imu_model.predict(features)[0]
            anomaly = -imu_model.score_samples(features)[0]
        else:
            prediction, anomaly = evaluate_ground(values['tilt_x'], values['tilt_y'],
                values['vibration'], values['temperature'], values['humidity'])

        # Domain-specific contribution
        tilt = np.sqrt(
            values["tilt_x"] ** 2 +
            values["tilt_y"] ** 2
        )

        risk = (
            anomaly * 100
            + tilt * 8
            + values["vibration"] * 3
        )

    else:

        prediction, anomaly = evaluate_crack(
            values["displacement_mm"]
        )

        risk = anomaly * 100 + values["displacement_mm"] * 2

    risk = max(0, min(100, risk))

    if risk >= 70:
        level = "HIGH"
    elif risk >= 40:
        level = "MEDIUM"
    else:
        level = "LOW"

    return round(risk, 2), level