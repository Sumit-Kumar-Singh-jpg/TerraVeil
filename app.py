from flask import Flask, jsonify, render_template, request
from datetime import datetime

from database import (
    init_db,
    get_nodes,
    get_latest_readings,
    get_history
)

from simulator import start_simulator


app = Flask(__name__)

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


@app.route("/api/nodes")
def nodes():
    return jsonify(get_nodes())


@app.route("/api/readings/latest")
def latest_readings():
    return jsonify(get_latest_readings())


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
        "system": "SubSentry AI Safety Architecture",
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


if __name__ == "__main__":

    init_db()

    start_simulator()

    app.run(
        debug=True,
        host="0.0.0.0",
        port=5000,
        use_reloader=False
    )