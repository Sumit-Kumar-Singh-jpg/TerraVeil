> Hardware integration: see [HARDWARE.md](HARDWARE.md) for the selected LoRa → HOST-01 → USB/COM7 path, setup, optional buffered receiver firmware and acceptance checks. Earlier demo architecture descriptions below predate this integration.

# TerraVeil

<p align="center">
  <img src="static/images/UnderGround-node.png" alt="TerraVeil Logo" width="80" height="80" />
</p>

<h3 align="center">AI-Enabled Smart Mine Subsidence Monitoring & Early Warning Platform</h3>

<p align="center">
  <strong>An edge-first geospatial IoT and explainable machine learning platform designed for real-time detection of ground deformation and proactive mine safety hazard mitigation.</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-blue?style=flat-square&logo=python" alt="Python" />
  <img src="https://img.shields.io/badge/Flask-3.1.1-lightgrey?style=flat-square&logo=flask" alt="Flask" />
  <img src="https://img.shields.io/badge/Scikit--Learn-1.7.1-orange?style=flat-square&logo=scikit-learn" alt="Scikit-Learn" />
  <img src="https://img.shields.io/badge/Leaflet-1.9.4-green?style=flat-square&logo=leaflet" alt="Leaflet" />
  <img src="https://img.shields.io/badge/Chart.js-4.x-FF6384?style=flat-square&logo=chartdotjs" alt="Chart.js" />
  <img src="https://img.shields.io/badge/SQLite-3-003B57?style=flat-square&logo=sqlite" alt="SQLite" />
  <img src="https://img.shields.io/badge/Status-SIH_Prototype-success?style=flat-square" alt="Status" />
</p>

---

## 📌 Executive Overview

Underground coal mining operations frequently induce surface subsidence, ground fractures, and structural destabilization. Undetected subsidence poses acute risks to surface infrastructure, local habitats, and underground mine workings.

**TerraVeil** is an industrial IoT and geospatial early-warning control platform tailored for coalfields (e.g., *Chasnalla Colliery, Panel East, Jharia Coalfield*). The system integrates:
1. **Dual-topology sensor nodes** (borehole underground tilt/vibration nodes & surface crack extensometers).
2. **Local Edge ML Anomaly Detection** using pre-trained **Isolation Forest** algorithms operating at sub-2ms latency without cloud dependence.
3. **Spatial Clustering Algorithms** that aggregate multi-node telemetry anomalies into contour risk zones.
4. **Geospatial Control Room Dashboard** featuring interactive OpenStreetMap GIS, real-time Chart.js telemetry telemetry, explainable AI factor breakdowns, and DGMS compliance reporting.

---

## 🚀 Key Features

### 1. Geospatial Live Intelligence & GIS Map
- Interactive **OpenStreetMap + Leaflet GIS** visualization centered on active mine panels.
- Live pulsing risk indicators:
  - 🟢 **LOW Risk (Green)**: Baseline geological stability.
  - 🟡 **MEDIUM Risk (Amber)**: Sensor drift or minor strain detected.
  - 🔴 **HIGH Risk (Red)**: Critical structural displacement or seismic acceleration.
- Visual representation of **Crack Sensor Dual-Pole Geometries** with dynamic status-colored anchor lines.
- Bounding polygon overlay depicting mine concession limits (`PANEL EAST — Boundary limits`).

### 2. Dual Sensor Node Topologies
- **Underground Monitoring Nodes (`G-xxx`)**:
  - Inclinometer Tri-axial Tilt ($X, Y$ angles in degrees).
  - High-frequency seismic vibration ($g$ / acceleration amplitude).
  - Ambient environmental factors (Temperature & Relative Humidity).
  - Battery capacity (%) and millisecond-precision timestamps.
- **Crack / Displacement Monitoring Nodes (`C-xxx`)**:
  - Linear potentiometer crack displacement measurements ($0 - 20\text{ mm}$).
  - Raw ADC potentiometer voltage telemetry ($0 - 4095$).
  - Dual anchor pole geographic coordinates (`Pole A` & `Pole B`).

### 3. Edge ML Anomaly Detection & Risk Scoring
- Powered by Scikit-Learn **Isolation Forest** ($contamination = 0.03$) trained on baseline geological profiles.
- Sub-2ms local inference directly on the gateway layer, maintaining offline-first survivability.
- Multi-variate composite risk scoring ($0 - 100$) integrating statistical anomaly strength with domain physical weights:
  - Vector tilt magnitude $\|\vec{\theta}\| = \sqrt{\theta_x^2 + \theta_y^2}$
  - Peak structural vibration energy
  - Crack aperture expansion velocity
- Risk Tier Stratification:
  - **$0 \le \text{Risk} < 40$**: `LOW` (Normal operations)
  - **$40 \le \text{Risk} < 70$**: `MEDIUM` (Warning / scheduled visual inspection)
  - **$70 \le \text{Risk} \le 100$**: `HIGH` (Critical alarm / immediate safety trigger)

### 4. Autonomous Spatial Deformation Clustering
- Real-time **500-meter proximity clustering algorithm** continuously scans the network.
- Groups correlated abnormal nodes into named **Risk Zones** (e.g., `Zone A`, `Zone B`).
- Renders dynamic translucent risk contour overlays on GIS to delineate expanding subsidence bowls.

### 5. Explainable AI (XAI) & Rate Indicators
- Real-time feature attribution bar charts displaying the percentage contribution of each telemetry parameter to the anomaly score.
- Dynamic directional indicators ($\uparrow$ Increasing, $\downarrow$ Decreasing, $\rightarrow$ Stable) computed across sliding time-series windows.
- Contextual safety procedure triggers and operator action cards.

### 6. Industrial Multi-Tab Control Workspace
| Tab | Functionality |
| :--- | :--- |
| **Live Monitoring** | Split GIS hero map, selected node telemetry card, live Chart.js trend curves, active risk zones, and recent event alerts. |
| **Historical Analysis** | Time-series query engine supporting multi-node comparative analytics across 1H, 6H, 24H, and 7D time horizons. |
| **Sensor Network** | Full hardware inventory table with live filtering (All, Underground, Crack, Critical). |
| **Risk & Alerts** | Audit log detailing historical trigger levels, contributing geological flags, and acknowledgment status. |
| **Risk Zones** | Dedicated spatial cluster dashboard displaying affected node sets, average risk scores, and hazard profiles. |
| **AI Analysis** | Explainable ML dashboard inspecting Isolation Forest signatures, edge latency, and node-level feature weights. |
| **Reports** | Automated export triggers for Daily Safety Summaries, DGMS Regulatory CSV templates, and JSON event logs. |
| **System Health** | Real-time diagnostic checklist covering SQLite DB size, total stored readings, thread status, and engine latencies. |
| **Settings** | Local operator configurations, alert threshold sliders ($30-90$), and safety response dispatch emails. |

### 7. Realistic Subsidence Simulation Model
- Physics-based synthetic generator emulating gradual geotechnical subsidence.
- Initiates with Gaussian sensor background noise ($\mu=0, \sigma=0.08$).
- Introduces progressive subsidence after step 30 centered around `(23.657° N, 86.452° E)`.
- Spatial distance decay function $f(d) = \max\left(0, 1 - \frac{d}{0.02}\right)$ ensuring realistic localized strain propagation.

---

## 🏗️ System Architecture

```text
                                 GEOTECHNICAL FIELD SENSOR LAYER
                     ┌──────────────────────────────────────────────────────┐
                     │                                                      │
                     ▼                                                      ▼
           [ Underground Node: G-xxx ]                            [ Crack Node: C-xxx ]
           • MPU-6050 (Tilt X/Y)                                  • Linear Potentiometer
           • Piezo / Seismic Vibration                            • Crack Anchor Poles (A/B)
           • DHT22 (Temp / Humidity)                              • ADC Voltage Divider
           • Power Management (Battery)                           • Power Management (Battery)
                     │                                                      │
                     └──────────────────────────┬───────────────────────────┘
                                                │
                                                ▼
                                   [ LoRa / LoRaWAN Gateway ]
                                   (Local Edge Microserver)
                                                │
                                                ▼
                                    [ Flask 3.1.1 Backend ]
                                                │
                     ┌──────────────────────────┴───────────────────────────┐
                     │                                                      │
                     ▼                                                      ▼
            [ SQLite Database ]                                    [ Local ML Engine ]
            • nodes table (lat/lon, poles)                         • Scikit-Learn Isolation Forest
            • readings table (time-series)                         • Risk Scoring & Classification
                     │                                                      │
                     └──────────────────────────┬───────────────────────────┘
                                                │
                                                ▼
                                 [ Spatial Clustering Engine ]
                                 • 500m Proximity Clustering
                                 • Subsidence Contour Generation
                                                │
                                                ▼
                             [ TerraVeil Industrial Web Console ]
                             • Leaflet GIS Map & Pulse Markers
                             • Real-Time Chart.js Visualizer
                             • Explainable AI Telemetry Attribution
                             • DGMS Compliance & PDF/CSV Reporting
```

---

## 🔬 Mathematical & Machine Learning Formulation

### 1. Feature Representation
For an Underground node $i$:
$$\mathbf{x}_{\text{ground}} = \begin{bmatrix} \theta_x & \theta_y & v & T & H \end{bmatrix}^T$$
Where:
- $\theta_x, \theta_y$: Inclinometer tilt angles ($^\circ$)
- $v$: Vibration amplitude ($g$)
- $T, H$: Temperature ($^\circ\text{C}$) and Relative Humidity ($\%$)

For a Crack node $j$:
$$\mathbf{x}_{\text{crack}} = \begin{bmatrix} d \end{bmatrix}$$
Where $d$ represents surface crack displacement in millimeters ($\text{mm}$).

### 2. Isolation Forest Anomaly Scoring
The decision tree ensemble measures path length $h(\mathbf{x})$ to isolate sample $\mathbf{x}$. The raw anomaly score $s(\mathbf{x}, n)$ is computed via:
$$s(\mathbf{x}, n) = 2^{-\frac{\mathbb{E}(h(\mathbf{x}))}{c(n)}}$$
The anomaly strength used in TerraVeil is defined as:
$$\alpha(\mathbf{x}) = - \text{score\_samples}(\mathbf{x})$$

### 3. Composite Risk Calculation
**Underground Nodes:**
$$\text{Tilt Vector Magnitude } \|\vec{\theta}\| = \sqrt{\theta_x^2 + \theta_y^2}$$
$$\text{Risk}_{\text{ground}} = \text{clamp}_{[0, 100]}\left(100 \cdot \alpha(\mathbf{x}) + 8 \cdot \|\vec{\theta}\| + 3 \cdot v\right)$$

**Crack Nodes:**
$$\text{Risk}_{\text{crack}} = \text{clamp}_{[0, 100]}\left(100 \cdot \alpha(\mathbf{x}) + 2 \cdot d\right)$$

### 4. Spatial Deformation Propagation (Simulator)
For a node located at coordinate $(lat, lon)$, the Euclidean distance $D$ to the subsidence epicenter $(lat_0, lon_0)$ is:
$$D = \sqrt{(lat - lat_0)^2 + (lon - lon_0)^2}$$
The spatial decay factor $S$ and total deformation $\Delta$ at simulation step $t > 30$ are:
$$S = \max\left(0, 1 - \frac{D}{0.02}\right), \quad P = \min\left(1.0, \frac{t - 30}{100}\right), \quad \Delta = P \cdot S$$

---

## 📂 Project Structure

```text
terraveil/
│
├── app.py                     # Flask HTTP application server & REST endpoints
├── database.py                # SQLite schema setup, connection pooling & queries
├── ml_model.py                # Isolation Forest training, evaluation & risk scoring
├── simulator.py               # Physics-based sensor node simulation & background thread
├── requirements.txt           # Python package dependencies
├── terraveil.db               # SQLite time-series & node metadata database
│
├── templates/
│   └── index.html             # Multi-tab industrial control room single-page layout
│
└── static/
    ├── css/
    │   └── style.css          # Industrial warm palette styling & responsive layouts
    ├── js/
    │   └── app.js             # Leaflet GIS, Chart.js telemetry, spatial clustering & UI logic
    └── images/
        ├── UnderGround-node.png   # Custom underground node GIS map icon
        └── crack-node.png         # Custom crack extensometer GIS map icon
```

---

## 🔌 REST API Specification

### 1. `GET /`
- **Description:** Renders the main industrial monitoring SPA dashboard.
- **Response:** `200 OK` (HTML)

---

### 2. `GET /api/nodes`
- **Description:** Retrieves the complete registry of deployed sensor nodes with spatial coordinates.
- **Response:** `200 OK` (JSON)
```json
[
  {
    "node_id": "G-001",
    "node_type": "UnderGround",
    "latitude": 23.65421,
    "longitude": 86.44983,
    "pole_a_lat": null,
    "pole_a_lon": null,
    "pole_b_lat": null,
    "pole_b_lon": null
  },
  {
    "node_id": "C-015",
    "node_type": "crack",
    "latitude": 23.66120,
    "longitude": 86.45890,
    "pole_a_lat": 23.66090,
    "pole_a_lon": 86.45860,
    "pole_b_lat": 23.66150,
    "pole_b_lon": 86.45920
  }
]
```

---

### 3. `GET /api/readings/latest`
- **Description:** Returns the most recent telemetry readings across all registered sensor nodes.
- **Response:** `200 OK` (JSON)
```json
[
  {
    "id": 1420,
    "node_id": "G-001",
    "timestamp": "2026-09-01T06:05:22.108412",
    "tilt_x": 0.041,
    "tilt_y": -0.023,
    "vibration": 0.148,
    "temperature": 29.8,
    "humidity": 61.2,
    "displacement_mm": null,
    "potentiometer_raw": null,
    "battery": 98.4,
    "risk_score": 18.42,
    "risk_level": "LOW"
  }
]
```

---

### 4. `GET /api/history/<node_id>`
- **Description:** Retrieves the chronological historical records for a specific sensor node (latest 100 entries).
- **Parameters:**
  - `node_id` *(path, string)*: e.g. `G-001` or `C-015`
- **Response:** `200 OK` (JSON Array ordered chronologically)
```json
[
  {
    "id": 1320,
    "node_id": "G-001",
    "timestamp": "2026-09-01T06:01:20.000000",
    "tilt_x": 0.021,
    "tilt_y": 0.015,
    "vibration": 0.142,
    "temperature": 29.5,
    "humidity": 60.8,
    "displacement_mm": null,
    "potentiometer_raw": null,
    "battery": 98.7,
    "risk_score": 12.10,
    "risk_level": "LOW"
  }
]
```

---

### 5. `GET /api/alert/status`
- **Description:** Returns the current system-wide emergency status, siren broadcast states, and active broadcast message.
- **Response:** `200 OK` (JSON)
```json
{
  "status": "NORMAL",
  "sirens_active": false,
  "triggered_at": null,
  "acknowledged_at": null,
  "message": "Normal operations in progress. All sirens in standby mode.",
  "siren_zones": [
    { "id": "SRN-01", "name": "Surface Main Klaxon 1", "location": "Surface Admin Building", "status": "OFF" },
    { "id": "SRN-02", "name": "Surface Main Klaxon 2", "location": "Pithead / Loading Bay", "status": "OFF" },
    { "id": "SRN-03", "name": "Shaft 1 In-Mine Siren", "location": "Shaft 1 Incline (Depth: 120m)", "status": "OFF" }
  ]
}
```

---

### 6. `POST /api/alert/trigger`
- **Description:** Sends an emergency command to trigger **RED ALERT** state and activate ALL mine sirens simultaneously.
- **Request Body (Optional):**
```json
{
  "message": "CRITICAL SUBSIDENCE COLLAPSE HAZARD DETECTED — ALL MINE SIRENS ACTIVATED — EVACUATE IMMEDIATELY!"
}
```
- **Response:** `200 OK` (JSON)
```json
{
  "success": true,
  "message": "Emergency command executed. All mine sirens activated. System in RED ALERT state.",
  "alert_state": {
    "status": "RED_ALERT",
    "sirens_active": true,
    "triggered_at": "2026-09-01T06:25:00.000000",
    "message": "CRITICAL SUBSIDENCE COLLAPSE HAZARD DETECTED...",
    "siren_zones": [...]
  }
}
```

---

### 7. `POST /api/alert/reset`
- **Description:** Acknowledges the emergency alert, silences and deactivates all sirens, and returns the system to `NORMAL` monitoring state.
- **Response:** `200 OK` (JSON)
```json
{
  "success": true,
  "message": "Alert acknowledged. Sirens deactivated. System returned to NORMAL state.",
  "alert_state": {
    "status": "NORMAL",
    "sirens_active": false,
    "acknowledged_at": "2026-09-01T06:27:30.000000"
  }
}
```

## 🛠️ Installation & Quick Start

### Prerequisites
- **Python 3.10+**
- **pip** package manager
- Modern web browser (Chrome, Edge, Firefox)

### 1. Clone the Repository
```bash
git clone https://github.com/your-username/terraveil.git
cd terraveil
```

### 2. Create and Activate a Virtual Environment
#### On Windows (PowerShell / Command Prompt):
```powershell
python -m venv venv
venv\Scripts\activate
```

#### On Linux / macOS:
```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Launch the Application Server
```bash
python app.py
```

### 5. Access the Dashboard
Open your browser and navigate to:
```text
http://localhost:5000
```
*(The background simulation engine starts automatically upon launch and begins populating SQLite telemetry data.)*

---

## 📡 Hardware & IoT Deployment Roadmap

While the current release uses high-fidelity simulation for hackathon demonstration, the platform's modular API and database layer are designed for drop-in hardware compatibility:

```text
               FIELD HARDWARE LAYER (PLANNED)
 ┌─────────────────────────────────────────────────────────┐
 │ ESP32 Microcontroller (Ultra-low power deep sleep)     │
 │  ├── MPU-6050 / ADXL345 (Digital I2C Accelerometer)     │
 │  ├── 10k Linear Potentiometer (ADC Extensometer)        │
 │  ├── SHT31 / DHT22 (Temp & Humidity Sensor)             │
 │  └── SX1276 / SX1262 LoRa 868/915 MHz Transceiver       │
 └────────────────────────────┬────────────────────────────┘
                              │ LoRa Packet (AES-128 Encrypted)
                              ▼
 ┌─────────────────────────────────────────────────────────┐
 │ LoRaWAN Gateway (e.g. RAKwireless / Dragino)            │
 │  └── Embedded MQTT Forwarder                            │
 └────────────────────────────┬────────────────────────────┘
                              │ JSON Telemetry Payload
                              ▼
 ┌─────────────────────────────────────────────────────────┐
 │ TerraVeil Ingestion Engine                              │
 │  └── POST /api/readings/telemetry                       │
 └─────────────────────────────────────────────────────────┘
```

### Standard 12-Byte Binary LoRa Payload Format
```text
[ 0-1 ] Node ID (uint16)
[ 2-3 ] Tilt X (int16, scaled x100)
[ 4-5 ] Tilt Y (int16, scaled x100)
[ 6-7 ] Vibration / Disp (uint16, scaled x100)
[  8  ] Temperature (int8)
[  9  ] Humidity (uint8)
[ 10  ] Battery % (uint8)
[ 11  ] Checksum (CRC8)
```

---

## 🤖 AI Architecture: Two-Tier Intelligence Model

TerraVeil operates on a strict **Two-Tier Intelligence Architecture** separating real-time deterministic safety control from asynchronous strategic analytics.

```text
  ┌────────────────────────────────────────────────────────────────────────────────────────┐
  │                       TIER 1: LOCAL OFFLINE ML ENGINE (SAFETY CRITICAL)                │
  ├────────────────────────────────────────────────────────────────────────────────────────┤
  │  • Locality: Edge Gateway / Local SQLite Core (100% Offline Survivable)               │
  │  • Latency: < 1.5 ms                                                                   │
  │  • Critical Path: YES — Sole Authority for Real-Time Safety & Sirens                   │
  │  • Core Functions:                                                                     │
  │     1. Live telemetry multi-variate feature processing (Tilt, Vibration, Crack Disp)   │
  │     2. Scikit-Learn Isolation Forest statistical anomaly scoring                       │
  │     3. 500m proximity spatial clustering into hazard contour zones                     │
  │     4. Deterministic LOW / MEDIUM / HIGH emergency alerts and siren trigger execution  │
  └────────────────────────────────────────────────────────────────────────────────────────┘
                                              │ (Historical Telemetry Logging)
                                              ▼
  ┌────────────────────────────────────────────────────────────────────────────────────────┐
  │                   TIER 2: ASYNCHRONOUS AI / LLM LAYER (STRATEGIC ANALYTICS)            │
  ├────────────────────────────────────────────────────────────────────────────────────────┤
  │  • Locality: Asynchronous Background / Cloud / Edge Co-processor                       │
  │  • Latency: Advisory / On-Demand / Scheduled                                           │
  │  • Critical Path: NO — Non-Blocking Strategic Intelligence                             │
  │  • Core Functions:                                                                     │
  │     1. Multi-hour & multi-day time-series trend identification                         │
  │     2. Cross-panel strain shift & spatial deformation trajectory modeling               │
  │     3. Historical incident correlation and hazard progression forecasting              │
  │     4. Synthesis of natural language operator-friendly engineering briefs               │
  │     5. Automated DGMS regulatory safety compliance audit generation                    │
  └────────────────────────────────────────────────────────────────────────────────────────┘
```

> [!CAUTION]
> **SAFETY ARCHITECTURE INVARIANT:**
> The AI/LLM layer is **NEVER used as the mandatory decision-maker for an immediate emergency alert or siren activation**.
> 
> Real-time emergency detection and evacuation commands are executed **100% deterministically by the local offline ML engine (Tier 1)** on the edge gateway without requiring Internet connectivity, cloud APIs, or non-deterministic prompt reasoning.

---

### Additional REST Endpoints

#### `GET /api/ai/insights`
- **Description:** Runs Tier 2 asynchronous historical trend analysis across accumulated sensor records and returns an operator-friendly geotechnical brief, risk trajectory, and DGMS action recommendations.
- **Response:** `200 OK` (JSON)
```json
{
  "architecture_layer": "Tier 2: Asynchronous AI/LLM Historical Intelligence Layer",
  "safety_path_isolation": "NON_CRITICAL_PATH_ADVISORY",
  "safety_certification": "Real-time emergency safety triggers and sirens remain 100% governed by the deterministic local edge ML engine.",
  "metrics_summary": {
    "total_historical_samples": 400,
    "average_network_risk": 22.4,
    "peak_recorded_risk": 78.5,
    "average_tilt_magnitude_deg": 0.412,
    "peak_crack_displacement_mm": 6.82
  },
  "risk_progression": {
    "status": "ACCELERATING_DEFORMATION",
    "analysis": "Upward progression detected: average risk scores have increased by 4.2% over the monitored window."
  },
  "geotechnical_executive_summary": "CRITICAL STRUCTURAL ATTENTION: Edge ML engines have identified elevated localized strain in Panel East...",
  "engineering_recommendations": [
    "Dispatch geotechnical visual inspection crew to Panel East Longwall Face within 2 hours.",
    "Verify secondary anchor bolt tension on Shaft 1 & Shaft 2 incline drifts."
  ]
}
```

#### `GET /api/ai/architecture`
- **Description:** Returns the formal Two-Tier AI Architecture specification, latency budgets, and safety isolation guarantees.
- **Response:** `200 OK` (JSON)

---

## 👥 Hackathon & Project Context

- **Event:** Smart India Hackathon (SIH) Prototype
- **Domain:** Mining Safety, Disaster Prevention & IoT Geospatial Intelligence
- **Target Location:** Chasnalla Colliery, Panel East, Jharia Coalfield, Dhanbad, Jharkhand, India

---

## 📄 License

This project is developed as an academic and technological prototype for the **Smart India Hackathon**. All rights reserved by the development team.
