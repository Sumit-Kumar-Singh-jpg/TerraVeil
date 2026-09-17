if __name__ == '__main__':
    from pathlib import Path
    from runtime_lock import InstanceLock
    try:
        _instance_lock = InstanceLock(Path(__file__).parent / '.runtime' / 'mother-host.lock')
    except RuntimeError as error:
        raise SystemExit(str(error))

from pathlib import Path
import os
from flask import Flask, jsonify, render_template, request, send_from_directory
from datetime import datetime

from database import (
    init_db,
    get_nodes,
    get_latest_readings,
    get_history, get_recent_events, mode, set_mode, start_node_session
)

from simulator import start_simulator
from digital_twin import build_twin_state


app = Flask(__name__)
from insar import insar
app.register_blueprint(insar)
from simulation_placement import placement_api
app.register_blueprint(placement_api)
app.config["MAX_CONTENT_LENGTH"] = 8192
init_db()

from telemetry import ingest, network_state

# Global Alert & Siren State
alert_state = {
    "status": "NORMAL",             # "NORMAL" or "RED_ALERT"
    "sirens_active": False,
    "triggered_at": None,
    "acknowledged_at": None,
    "message": "Normal operations in progress. All sirens in standby mode.",
    "siren_zones": [
        {"id": "SRN-01", "name": "Surface Main Klaxon 1", "location": "Surface Admin Building", "status": "OFF"},
        {"id": "SRN-02", "name": "Surface Main Klaxon 2", "location": "Pithead / Loading Bay", "status": "OFF"},
        {"id": "SRN-03", "name": "Shaft 1 In-Mine Siren", "location": "Shaft 1 Incline (Depth: 120m)", "status": "OFF"},
        {"id": "SRN-04", "name": "Shaft 2 In-Mine Siren", "location": "Shaft 2 Level 2 (Depth: 250m)", "status": "OFF"},
        {"id": "SRN-05", "name": "Shaft 3 In-Mine Siren", "location": "Shaft 3 Haulage Drift", "status": "OFF"},
        {"id": "SRN-06", "name": "Shaft 4 In-Mine Siren", "location": "Shaft 4 Return Airway", "status": "OFF"},
        {"id": "SRN-07", "name": "Panel East Warning Array", "location": "Panel East Longwall Face", "status": "OFF"},
        {"id": "SRN-08", "name": "Central Conveyor Siren", "location": "Main Subsurface Beltline", "status": "OFF"}
    ]
}


@app.route("/")
def index():
    return render_template("index.html")


def accept_telemetry(payload):
    result = ingest(payload)
    if result.get('applied') and result.get('risk_level') == 'CRITICAL' and mode() == 'REAL':
        alert_state.update(status='RED_ALERT', sirens_active=True,
            triggered_at=datetime.utcnow().isoformat(), message='Validated persistent correlated critical sensor event')
        for siren in alert_state['siren_zones']:
            siren['status'] = 'ON'
    return result


@app.route('/api/telemetry', methods=['POST'])
def telemetry():
    try:
        return jsonify(accept_telemetry(request.get_json(silent=True)))
    except ValueError as error:
        return jsonify(success=False, error=str(error)), 400


@app.route('/api/system', methods=['GET', 'POST'])
def system():
    if request.method == 'POST':
        payload = request.get_json(silent=True)
        try:
            set_mode(payload.get('mode') if isinstance(payload, dict) else None)
        except ValueError as error:
            return jsonify(success=False, error=str(error)), 400
        if mode() == 'SIMULATION':
            start_simulator()
    return jsonify(network_state())


@app.route('/api/live')
def live_state():
    source = mode()
    registered = get_nodes(source)
    readings = get_latest_readings(source)
    response = jsonify(system=network_state(source), nodes=registered, readings=readings, events=get_recent_events(source),
        twin=build_twin_state(registered, readings, alert_state))
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.route('/api/nodes/<node_id>/session', methods=['POST'])
def new_node_session(node_id):
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or payload.get('confirmed_restart') is not True:
        return jsonify(success=False, error='Confirm the physical node restarted and previous buffered packets are drained'), 400
    try:
        return jsonify(success=True, session_id=start_node_session(node_id))
    except ValueError as error:
        return jsonify(success=False, error=str(error)), 400


@app.route("/api/nodes")
def nodes():
    return jsonify(get_nodes())


@app.route("/api/readings/latest")
def latest_readings():
    return jsonify(get_latest_readings())


@app.route("/api/twin/state")
def twin_state():
    """Read-only visualization state; existing alert endpoints remain authoritative."""
    response = jsonify(build_twin_state(get_nodes(), get_latest_readings(), alert_state))
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.route('/api/twin/assets/<filename>')
def twin_asset(filename):
    """Serve the exported model with precompressed transfer when supported."""
    if filename not in ('mine.glb', 'scene.json'):
        return jsonify(error='Unknown twin asset'), 404
    compressed = request.accept_encodings['gzip'] > 0
    response = send_from_directory(
        app.static_folder + '/models/terraveil', filename + ('.gz' if compressed else ''),
        mimetype='model/gltf-binary' if filename.endswith('.glb') else 'application/json',
        max_age=3600,
    )
    if compressed:
        response.headers['Content-Encoding'] = 'gzip'
    response.headers['Vary'] = 'Accept-Encoding'
    return response


@app.route('/map/<filename>')
def map_asset(filename):
    if filename not in ('jharia_subsidence_zones.geojson', 'jharia_subsidence_zones.csv'):
        return jsonify(error='Unknown map asset'), 404
    folder = Path(__file__).parent / 'map'
    mimetype = 'application/geo+json' if filename.endswith('.geojson') else 'text/csv'
    return send_from_directory(folder, filename, mimetype=mimetype)


@app.route("/api/history/<node_id>")
def history(node_id):
    return jsonify(get_history(node_id))


@app.route("/api/alert/status", methods=["GET"])
def get_alert_status():
    return jsonify(alert_state)


@app.route("/api/alert/trigger", methods=["POST"])
def trigger_alert():
    global alert_state
    req_data = request.get_json(silent=True) or {}
    custom_msg = req_data.get("message") or "CRITICAL SUBSIDENCE COLLAPSE HAZARD DETECTED — ALL MINE SIRENS ACTIVATED — EVACUATE PANEL EAST & UNDERGROUND WORKINGS IMMEDIATELY!"
    
    alert_state["status"] = "RED_ALERT"
    alert_state["sirens_active"] = True
    alert_state["triggered_at"] = datetime.utcnow().isoformat()
    alert_state["message"] = custom_msg
    for s in alert_state["siren_zones"]:
        s["status"] = "ON"
        
    return jsonify({
        "success": True,
        "message": "Emergency command executed. All mine sirens activated. System in RED ALERT state.",
        "alert_state": alert_state
    })


@app.route("/api/alert/reset", methods=["POST"])
def reset_alert():
    global alert_state
    alert_state["status"] = "NORMAL"
    alert_state["sirens_active"] = False
    alert_state["acknowledged_at"] = datetime.utcnow().isoformat()
    alert_state["message"] = "Alert acknowledged and reset. All sirens silenced. Resuming normal monitoring."
    for s in alert_state["siren_zones"]:
        s["status"] = "OFF"
        
    return jsonify({
        "success": True,
        "message": "Alert acknowledged. Sirens deactivated. System returned to NORMAL state.",
        "alert_state": alert_state
    })


from ai_layer import analyze_historical_trends


@app.route("/api/ai/insights", methods=["GET", "POST"])
def ai_insights():
    """
    Tier 2 Strategic AI Layer:
    Asynchronously analyzes accumulated historical sensor data for multi-hour/multi-day
    trend identification, historical event correlation, risk progression analysis, and
    natural language operator insights.

    SAFETY RULE:
    Operates strictly outside the critical real-time safety path.
    """
    return jsonify(analyze_historical_trends())


@app.route("/api/ai/architecture", methods=["GET"])
def ai_architecture():
    """
    Returns the formal two-tier AI architecture specification.
    """
    return jsonify({
        "system": "TerraVeil AI Safety Architecture",
        "tier_1_realtime_safety": {
            "name": "Local Edge ML Anomaly Engine",
            "locality": "Edge Gateway / Microserver (100% Offline)",
            "critical_path": True,
            "latency": "< 2 ms",
            "responsibilities": [
                "Real-time incoming telemetry stream processing",
                "Isolation Forest statistical anomaly scoring",
                "Domain-specific physical vector weighting (tilt, vibration, displacement)",
                "500m proximity spatial clustering",
                "Deterministic immediate LOW/MEDIUM/HIGH risk alerts",
                "Mine-wide emergency siren trigger execution"
            ],
            "internet_required": False
        },
        "tier_2_strategic_intelligence": {
            "name": "Asynchronous AI / LLM Historical Intelligence Layer",
            "locality": "Asynchronous Background / Cloud or Edge Co-processor",
            "critical_path": False,
            "latency": "Advisory / Scheduled (Asynchronous)",
            "responsibilities": [
                "Multi-hour and multi-day long-term sensor trend identification",
                "Historical event and spatial strain correlation across panels",
                "Geotechnical risk progression and trajectory forecasting",
                "Synthesis of operator-friendly natural language engineering briefs",
                "Automated DGMS compliance audit summaries"
            ],
            "emergency_decision_maker": False,
            "safety_invariant": "The AI/LLM layer is NEVER used as the mandatory decision-maker for immediate emergency alerts or siren activations."
        }
    })


if mode() == "SIMULATION":
    start_simulator()


if __name__ == "__main__":

    init_db()

    if mode() == "SIMULATION":
        start_simulator()

    import logging
    import serial_bridge
    logging.basicConfig(level=logging.INFO)
    serial_bridge.start(accept_telemetry)

    # Start live hardware simulator for UG-01, UG-02, and LD-01 only when explicitly enabled
    if os.environ.get("TERRAVEIL_SIMULATE_HARDWARE", "0") == "1":
        from simulator import start_hardware_simulator
        start_hardware_simulator(accept_telemetry)

    app.run(
        debug=False,
        host="0.0.0.0",
        port=5000,
        use_reloader=False
    )
