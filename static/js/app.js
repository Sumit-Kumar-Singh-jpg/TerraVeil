let currentMode = null;
let pollBusy = false;
function fmt(value, digits=1) { return Number.isFinite(value) ? value.toFixed(digits) : 'UNAVAILABLE'; }

// ==========================================================================
// TerraVeil Industrial Control Room Logic (Warm Industrial Spec)
// ==========================================================================

// Global state variables
let activeTab = "view-live";
let nodesList = [];
let latestReadings = {};
let selectedNodeId = null;
let liveChartInstance = null;
let historicalChartInstance = null;
let zonePolygons = []; // Map overlays for clusters
let spatialClusters = []; // Active deformation zones

// Leaflet map initialization
const map = L.map("map", {
    zoomControl: true,
    minZoom: 10,
    maxZoom: 17
}).setView([23.657, 86.452], 14);

window.map = map;
window.dispatchEvent(new CustomEvent('terraveil:map-ready', { detail: { map } }));

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap contributors"
}).addTo(map);

// Stored map markers and connections
const markers = {};
const crackLines = {};

// Connection status updates
let lastSyncTime = new Date();

// ==========================================================================
// SPA TAB NAVIGATION SWITCHER
// ==========================================================================
document.querySelectorAll(".nav-link").forEach(link => {
    link.addEventListener("click", (e) => {
        e.preventDefault();
        const target = link.getAttribute("data-target");
        if (!target) return;

        // Reset active links
        document.querySelectorAll(".nav-link").forEach(l => l.classList.remove("active"));
        link.classList.add("active");

        // Hide all views, show active one
        document.querySelectorAll(".tab-view").forEach(view => {
            view.classList.add("hidden");
        });
        document.getElementById(target).classList.remove("hidden");
        activeTab = target;

        // If switching to live tab, recalculate map layout
        if (target === "view-live") {
            setTimeout(() => {
                map.invalidateSize();
            }, 100);
        }

        // Trigger historical dropdown load if switching to history tab
        if (target === "view-history") {
            populateHistoricalDropdowns();
        }

        // If switching to placement tab, render placement section
        if (target === "view-placement") {
            window.NodePlacementEngine?.recompute();
        }
    });
});

// ==========================================================================
// HELPERS & THEME CONFIGURATION
// ==========================================================================
function getChartThemeColors() {
    const isLight = document.documentElement.getAttribute("data-theme") === "light";
    return {
        gridColor: isLight ? "#DCD5C9" : "#2A303A",
        tickColor: isLight ? "#556170" : "#9AA3AF",
        legendColor: isLight ? "#1C222B" : "#F2F4F7",
        tooltipBg: isLight ? "#FFFFFF" : "#1B2029",
        tooltipBorder: isLight ? "#DCD5C9" : "#2A303A",
        tooltipTitle: isLight ? "#1C222B" : "#F2F4F7",
        tooltipBody: isLight ? "#556170" : "#9AA3AF"
    };
}

function initTheme() {
    const savedTheme = localStorage.getItem("subsentry_theme") || "dark";
    applyTheme(savedTheme);

    const toggleBtn = document.getElementById("theme-toggle-btn");
    if (toggleBtn) {
        toggleBtn.addEventListener("click", (e) => {
            e.preventDefault();
            const currentTheme = document.documentElement.getAttribute("data-theme") || "dark";
            const newTheme = currentTheme === "dark" ? "light" : "dark";
            applyTheme(newTheme);
        });
    }
}

function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("subsentry_theme", theme);

    const label = document.getElementById("theme-toggle-label");
    const toggleBtn = document.getElementById("theme-toggle-btn");

    if (theme === "light") {
        if (label) label.innerText = "Light Mode";
        if (toggleBtn) {
            toggleBtn.setAttribute("title", "Switch to Dark Mode");
            toggleBtn.setAttribute("aria-label", "Switch to Dark Mode");
        }
    } else {
        if (label) label.innerText = "Dark Mode";
        if (toggleBtn) {
            toggleBtn.setAttribute("title", "Switch to Light Mode");
            toggleBtn.setAttribute("aria-label", "Switch to Light Mode");
        }
    }

    // Refresh live trend chart if it exists to adapt grid lines and text
    if (selectedNodeId && liveChartInstance) {
        updateLiveTrendChart(selectedNodeId);
    }
}

function getRiskColor(level) {
    if (level === "HIGH" || level === "CRITICAL") return "#EF4444"; // Strong Red
    if (level === "MEDIUM") return "#F59E0B"; // Amber / Orange
    return "#10B981"; // Muted Green
}

function getNodeLabel(type) {
    return type === "UnderGround" ? "Underground Monitoring Node" : "Crack / Displacement Node";
}

function getNodeIcon(type) {
    return type === "UnderGround" 
        ? "/static/images/UnderGround-node.png" 
        : "/static/images/crack-node.png";
}

function isCrackNode(type) {
    return String(type || "").toLowerCase() === "crack";
}


// ==========================================================================
// REAL HARDWARE CONNECTIVITY STRIP
// ==========================================================================

function updateHardwareConnectivity(system) {

    const strip =
        document.getElementById("real-hardware-strip");

    if (!strip) return;

    // Don't show the physical 3-node strip in simulation mode.
    if (currentMode !== "REAL") {
        strip.style.display = "none";
        return;
    }

    strip.style.display = "grid";


    // ----------------------------------------------------------
    // HOST-01 USB connection
    // ----------------------------------------------------------

    const host =
        document.getElementById("conn-host");

    const hostOnline =
        system?.usb?.status === "OPEN";

    if (host) {
        host.innerHTML = `
            <div style="font-weight:700;">
                HOST-01
            </div>

            <div style="
                margin-top:4px;
                color:${hostOnline ? "#10B981" : "#EF4444"};
                font-weight:700;
            ">
                ● ${hostOnline ? "USB CONNECTED" : "USB OFFLINE"}
            </div>

            <div style="
                margin-top:3px;
                font-size:10px;
                color:var(--text-muted);
            ">
                ${system?.usb?.port || "No serial port"}
            </div>
        `;
    }


    // ----------------------------------------------------------
    // FIELD NODES
    // ----------------------------------------------------------

    const nodes = [
        ["UG-01", "conn-ug01"],
        ["UG-02", "conn-ug02"],
        ["LD-01", "conn-ld01"]
    ];

    nodes.forEach(([nodeId, elementId]) => {

        const element =
            document.getElementById(elementId);

        if (!element) return;

        const reading =
            latestReadings[nodeId];

        const online =
            reading?.online === true;

        const rssi =
            Number.isFinite(reading?.rssi)
                ? `${reading.rssi.toFixed(0)} dBm`
                : "—";

        const snr =
            Number.isFinite(reading?.snr)
                ? `${reading.snr.toFixed(1)} dB`
                : "—";

        const sequence =
            reading?.sequence ?? "—";

        element.innerHTML = `
            <div style="font-weight:700;">
                ${nodeId}
            </div>

            <div style="
                margin-top:4px;
                color:${online ? "#10B981" : "#64748B"};
                font-weight:700;
            ">
                ● ${online ? "LIVE" : "OFFLINE"}
            </div>

            <div style="
                margin-top:3px;
                font-size:10px;
                color:var(--text-muted);
            ">
                RSSI ${rssi}
                &nbsp;|&nbsp;
                SNR ${snr}
            </div>

            <div style="
                margin-top:2px;
                font-size:10px;
                color:var(--text-muted);
            ">
                Packet #${sequence}
            </div>
        `;
    });
}
// Calculate Tilt Vector Magnitude
function getTiltMagnitude(tx, ty) {
    if (tx == null || ty == null) return null;
    return Math.sqrt(tx * tx + ty * ty);
}

// Calculate distance between two coordinate points
function getDistance(lat1, lon1, lat2, lon2) {
    const p = 0.017453292519943295; // Math.PI / 180
    const c = Math.cos;
    const a = 0.5 - c((lat2 - lat1) * p)/2 + 
            c(lat1 * p) * c(lat2 * p) * 
            (1 - c((lon2 - lon1) * p))/2;
    return 12742 * Math.asin(Math.sqrt(a)) * 1000; // Radius of earth in km * 1000 for meters
}

// ==========================================================================
// SPATIAL DEFORMATION CLUSTERING ALGORITHM
// ==========================================================================
function calculateSpatialClusters(nodes, readings) {
    spatialClusters = (window.backendZones || []).map(zone => zone.node_ids.map(id => nodes.find(n => n.node_id === id)).filter(Boolean));
    updateDeformationOverlays();
    updateZonesUI();
}

// Draw translucent risk contour regions around clustered nodes
function updateDeformationOverlays() {
    zonePolygons.forEach(p => map.removeLayer(p));
    zonePolygons = [];

    spatialClusters.forEach((cluster, idx) => {
        const lats = cluster.map(n => n.latitude);
        const lons = cluster.map(n => n.longitude);
        
        const avgLat = lats.reduce((a, b) => a + b, 0) / lats.length;
        const avgLon = lons.reduce((a, b) => a + b, 0) / lons.length;

        const hasHigh = cluster.some(n => {
            const rd = latestReadings[n.node_id];
            return rd && rd.risk_level === "HIGH";
        });

        const color = hasHigh ? "#EF4444" : "#F59E0B";
        const zoneName = `Risk Zone ${String.fromCharCode(65 + idx)}`;

        const points = cluster.map(n => [n.latitude, n.longitude]);
        // Observed node connections, not a measured deformation extent.
        const circle = (points.length >= 3 ? L.polygon(points, {color, fillOpacity: 0.12, weight: 1.5})
            : L.polyline(points, {color, weight: 3, dashArray: '4, 4'})).addTo(map);

        circle.bindTooltip(`<strong>${zoneName} (${hasHigh ? 'Critical' : 'Warning'})</strong><br>${cluster.length} nodes correlated`, {
            permanent: false,
            direction: 'center',
            className: 'boundary-tooltip'
        });

        zonePolygons.push(circle);
    });
}

// Update the bottom-right zones summary and dedicated Zones Tab
function updateZonesUI() {
    const listContainer = document.getElementById("active-zones-list");
    const zonesGridTab = document.getElementById("zones-details-grid");
    
    if (!listContainer) return;

    if (spatialClusters.length === 0) {
        listContainer.innerHTML = '<div class="empty-list-state">No correlated risk zones. Check node status and available evidence.</div>';
        zonesGridTab.innerHTML = '<div class="empty-list-state">No correlated risk zones. Missing telemetry does not establish stable ground.</div>';
        document.getElementById("kpi-risk-zones").innerText = "00";
        document.getElementById("kpi-risk-zones-status").innerText = "No correlated zones";
        document.getElementById("kpi-risk-zones-status").className = "kpi-status";
        return;
    }

    document.getElementById("kpi-risk-zones").innerText = String(spatialClusters.length).padStart(2, '0');
    document.getElementById("kpi-risk-zones-status").innerText = "Immediate attention";
    document.getElementById("kpi-risk-zones-status").className = "kpi-status status-red";

    let listHtml = "";
    let gridTabHtml = "";

    spatialClusters.forEach((cluster, idx) => {
        const zoneName = `Zone ${String.fromCharCode(65 + idx)}`;
        const hasHigh = cluster.some(n => {
            const rd = latestReadings[n.node_id];
            return rd && rd.risk_level === "HIGH";
        });

        const severityClass = hasHigh ? "zone-severity-high" : "zone-severity-medium";
        const badgeClass = hasHigh ? "badge-red" : "badge-amber";
        const statusText = hasHigh ? "SUBSIDENCE RISK" : "WARNING ALERT";

        listHtml += `
            <div class="zone-list-item ${severityClass}">
                <div class="zone-left">
                    <span class="zone-name">${zoneName}</span>
                    <span class="zone-nodes-count">${cluster.length} Nodes Correlated</span>
                </div>
                <span class="zone-status-pill ${badgeClass}">${hasHigh ? 'Critical' : 'Warning'}</span>
            </div>
        `;

        const nodeTags = cluster.map(n => `<span class="node-tag">${n.node_id}</span>`).join(" ");
        
        gridTabHtml += `
            <div class="zone-detail-card ${hasHigh ? 'severity-high' : 'severity-medium'}">
                <div class="zone-header-row">
                    <span class="zone-title">${zoneName} — ${statusText}</span>
                    <span class="status-badge ${badgeClass}">${hasHigh ? 'Critical' : 'Warning'}</span>
                </div>
                <ul class="zone-stats-list">
                    <li>
                        <span>Geological Hazard:</span>
                        <strong>Subsidence Cluster</strong>
                    </li>
                    <li>
                        <span>Affected Nodes:</span>
                        <strong>${cluster.length} Nodes</strong>
                    </li>
                    <li>
                        <span>Average Risk Score:</span>
                        <strong>${(cluster.reduce((acc, node) => acc + (latestReadings[node.node_id]?.risk_score || 0), 0) / cluster.length).toFixed(1)} / 100</strong>
                    </li>
                </ul>
                <div class="affected-nodes-tags">
                    ${nodeTags}
                </div>
            </div>
        `;
    });

    listContainer.innerHTML = listHtml;
    zonesGridTab.innerHTML = gridTabHtml;
}

function isNodeClustered(nodeId) {
    return spatialClusters.some(cluster => cluster.some(n => n.node_id === nodeId));
}

// ==========================================================================
// SENSOR NODE GIS MARKERS RENDERER
// ==========================================================================
function renderNodeMarker(node, reading) {
    if (node.latitude == null || node.longitude == null) return;
    const color = reading?.online === false || !reading ? "#64748b" : getRiskColor(reading?.risk_level || "LOW");
    const iconPath = getNodeIcon(node.node_type);
    const nodeLabel = getNodeLabel(node.node_type);

    const isSelected = selectedNodeId === node.node_id ? "risk-marker-selected" : "";
    const isCritical = reading?.risk_level === "HIGH" ? "critical-marker-div" : "";

    const markerHTML = `
        <div class="sensor-marker ${isSelected} ${isCritical}" id="marker-div-${node.node_id}">
            <div class="risk-ring" style="border-color: ${color};">
                <img src="${iconPath}" class="sensor-icon" alt="${nodeLabel}" />
            </div>
            <div class="risk-light" style="background: ${color}; border-color: #0F1115;"></div>
        </div>
    `;

    const customDivIcon = L.divIcon({
        className: "custom-sensor-marker",
        html: markerHTML,
        iconSize: [40, 40],
        iconAnchor: [20, 20],
        popupAnchor: [0, -20]
    });

    if (!markers[node.node_id]) {
        const marker = L.marker([node.latitude, node.longitude], { icon: customDivIcon });
        marker.addTo(map);
        
        marker.on("click", () => {
            selectNode(node.node_id);
        });

        markers[node.node_id] = marker;
    } else {
        markers[node.node_id].setIcon(customDivIcon);
    }

    const tooltipHtml = `
        <strong>${node.node_id}</strong>
        <br>
        ${reading?.online ? "● LIVE" : "○ OFFLINE"}
        ·
        ${reading?.risk_level || "NO DATA"}

        <br>

        RSSI ${fmt(reading?.rssi, 0)} dBm
        ·
        SNR ${fmt(reading?.snr, 1)} dB
    `;

    markers[node.node_id].bindTooltip(
        tooltipHtml,
        {
            permanent: currentMode === "REAL",
            direction: "top",
            offset: [0, -18],
            opacity: 0.95
        }
    );
    if (
    isCrackNode(node.node_type)
    &&
    node.pole_a_lat
    &&
    node.pole_b_lat
) {
        if (!crackLines[node.node_id]) {
            const line = L.polyline([
                [node.pole_a_lat, node.pole_a_lon],
                [node.pole_b_lat, node.pole_b_lon]
            ], {
                color: color,
                weight: 3.5,
                opacity: 0.85
            }).addTo(map);
            
            crackLines[node.node_id] = line;
        } else {
            crackLines[node.node_id].setStyle({ color: color });
        }
    }
}

// ==========================================================================
// EVENT LOGS / ALERTS COMPONENT
// ==========================================================================
function updateAlertsLog(readings, events=[]) {
    const logContainer = document.getElementById("recent-alerts-container");
    const safetyLogsTbody = document.getElementById("safety-logs-tbody");
    
    if (!logContainer) return;

    const liveAlarmCount = Object.values(readings).filter(r => r.online && ['MEDIUM','HIGH','CRITICAL'].includes(r.risk_level)).length;
    const activeAlarms = events;

    document.getElementById("sidebar-alert-badge").innerText = liveAlarmCount;
    const bellBadge = document.getElementById("bell-badge");
    if (liveAlarmCount > 0) {
        bellBadge.innerText = liveAlarmCount;
        bellBadge.style.display = "inline-block";
    } else {
        bellBadge.style.display = "none";
    }

    // Always override Anomalies Today count to "07" if simulation is running, or count dynamically
    document.getElementById("kpi-anomalies").innerText = String(liveAlarmCount).padStart(2, '0');
    document.getElementById("kpi-anomalies-status").innerText = "ML triggers active";

    if (activeAlarms.length === 0) {
        logContainer.innerHTML = '<div class="empty-list-state">No active safety warnings.</div>';
        safetyLogsTbody.innerHTML = '<tr><td colspan="7" style="text-align:center; padding: 20px;">No active triggers. Use Analysis for historical telemetry.</td></tr>';
        return;
    }

    let stripHtml = "";
    activeAlarms.slice(0, 5).forEach(alert => {
        const color = getRiskColor(alert.risk_level);
        const node = nodesList.find(n => n.node_id === alert.node_id);
        const typeLabel = node ? (node.node_type === "UnderGround" ? "Ground" : "Crack") : "Unknown";
        
        let reason = "Anomalous reading";
        if (alert.risk_level === "HIGH") {
            reason = node?.node_type === "UnderGround" ? "Critical tilt rate shift" : "Critical gap extension";
        } else {
            reason = node?.node_type === "UnderGround" ? "Tilt variation trigger" : "Displacement drift";
        }

        stripHtml += `
            <div class="alert-list-item severity-${alert.risk_level.toLowerCase()}" style="cursor:pointer;" onclick="selectNode('${alert.node_id}')">
                <span class="alert-dot" style="color: ${color}">●</span>
                <div class="alert-content">
                    <div class="alert-top">
                        <span class="alert-node">${alert.node_id}</span>
                        <span class="alert-time">${new Date(alert.timestamp).toLocaleTimeString()}</span>
                    </div>
                    <div class="alert-reason">
                        ${alert.evidence || reason}
                        — COPOD: ${
                            Number.isFinite(alert.ml_score)
                                ? alert.ml_score.toFixed(1)
                                : "—"
                        }
                        · Validated Risk: ${
                            Number.isFinite(alert.risk_score)
                                ? alert.risk_score.toFixed(1)
                                : "—"
                        }
                    </div>
                </div>
            </div>
        `;
    });
    logContainer.innerHTML = stripHtml;

    let tableHtml = "";
    activeAlarms.forEach(alert => {
        const node = nodesList.find(n => n.node_id === alert.node_id);
        const typeLabel = node ? (node.node_type === "UnderGround" ? "Underground Node" : "Crack Node") : "Sensor";
        const badgeClass = alert.risk_level === "HIGH" ? "badge-red" : "badge-amber";
        
        let factors = [];
        if (node?.node_type === "UnderGround") {
            if (getTiltMagnitude(alert.tilt_x, alert.tilt_y) > 0.5) factors.push("Tilt");
            if (alert.vibration > 0.25) factors.push("Vibration");
        } else {
            if (alert.displacement_mm > 2.0) factors.push("Displacement");
        }
        if (isNodeClustered(alert.node_id)) factors.push("Spatial Correlation");

        tableHtml += `
            <tr onclick="selectNodeAndSwitchTab('${alert.node_id}')">
                <td><span class="status-badge ${badgeClass}">${alert.risk_level}</span></td>
                <td style="font-family: 'JetBrains Mono', monospace; font-weight: 700;">${alert.node_id}</td>
                <td>${typeLabel}</td>
                <td>${new Date(alert.timestamp).toLocaleString()}</td>
            <td>
                <div style="font-weight:700;">
                    ${
                        Number.isFinite(alert.ml_score)
                            ? alert.ml_score.toFixed(1)
                            : "—"
                    } / 100
                </div>

                <div style="
                    font-size:9px;
                    color:var(--text-muted);
                    margin-top:2px;
                ">
                    COPOD OUTLIER
                </div>
            </td>
                <td>${factors.join(" + ") || "ML Feature Shift"}</td>
                <td>${currentMode === "REAL" ? `RSSI ${fmt(latestReadings[node.node_id]?.rssi)} / SNR ${fmt(latestReadings[node.node_id]?.snr)}` : "SIMULATION"}</td>
            </tr>
        `;
    });
    safetyLogsTbody.innerHTML = tableHtml;
}

function selectNodeAndSwitchTab(nodeId) {
    selectNode(nodeId);
    document.querySelector('[data-target="view-live"]').click();
}

// ==========================================================================
// SELECTED NODE TELEMETRY CARD, DIR INDICATORS & EXPLANER
// ==========================================================================
async function selectNode(nodeId) {
    const sourceAtStart = currentMode;
    selectedNodeId = nodeId;
    const node = nodesList.find(n => n.node_id === nodeId);
    const reading = latestReadings[nodeId];

    if (!node) return;
    if (currentMode === 'REAL') {
        document.getElementById('sidebar-node-title').textContent = nodeId;
        const fields = [['Roll',reading?.roll,'°'],['Pitch',reading?.pitch,'°'],['Vibration',reading?.vibration,''],['Soil ADC (raw)',reading?.soil,''],['RSSI',reading?.rssi,'dBm'],['SNR',reading?.snr,'dB']];
        document.getElementById('node-info-content').innerHTML = `<div class="node-meta-grid">REAL HARDWARE · ${reading?.status || 'OFFLINE'}<br>Risk: ${reading?.risk_level || 'UNAVAILABLE'}<br>${node.latitude == null ? 'Location not registered' : `${node.latitude}, ${node.longitude}`}</div><div class="telemetry-grid">${fields.map(([label,value,unit]) => `<div class="tel-cell"><span class="tel-label">${label}</span><span class="tel-value">${fmt(value,2)} ${Number.isFinite(value) ? unit : ''}</span></div>`).join('')}</div><p>${reading?.evidence || 'Waiting for physical telemetry'}</p><p>Battery / displacement / BME280: NOT INSTALLED</p><p>Last seen: ${reading?.last_seen || 'UNAVAILABLE'}<br>Sequence: ${reading?.sequence ?? 'UNAVAILABLE'}</p>`;
        const restartButton = document.createElement('button');
        restartButton.type = 'button';
        restartButton.className = 'btn btn-secondary';
        restartButton.textContent = 'Node restarted? Start new session';
        restartButton.onclick = () => startNodeSession(nodeId);
        document.getElementById('node-info-content').appendChild(restartButton);
        document.getElementById('ai-explainability-details').textContent = reading?.evidence || 'No telemetry. Missing sensors are unavailable.';
        updateLiveTrendChart(nodeId);
        return;
    }
    if (!reading) return;

    // Highlight marker visually
    Object.keys(markers).forEach(id => {
        const el = document.getElementById(`marker-div-${id}`);
        if (el) {
            if (id === nodeId) el.classList.add("risk-marker-selected");
            else el.classList.remove("risk-marker-selected");
        }
    });

    // Query historical trends to calculate direction indicators (↑, ↓, →)
    let trendIndicators = {
        tilt: "→",
        vibration: "→",
        displacement: "→"
    };

    try {
        const response = await fetch(`/api/history/${nodeId}`);
        const historyData = await response.json();
        if (currentMode !== sourceAtStart || selectedNodeId !== nodeId) return;
        
        if (historyData.length >= 2) {
            const currentRec = historyData[historyData.length - 1];
            const prevRec = historyData[historyData.length - 2];
            
            // 1. Tilt magnitude indicator
            const curTilt = getTiltMagnitude(currentRec.tilt_x, currentRec.tilt_y);
            const prevTilt = getTiltMagnitude(prevRec.tilt_x, prevRec.tilt_y);
            if (curTilt > prevTilt + 0.005) trendIndicators.tilt = "↑";
            else if (curTilt < prevTilt - 0.005) trendIndicators.tilt = "↓";

            // 2. Vibration indicator
            if (currentRec.vibration > prevRec.vibration + 0.005) trendIndicators.vibration = "↑";
            else if (currentRec.vibration < prevRec.vibration - 0.005) trendIndicators.vibration = "↓";

            // 3. Displacement indicator
            if (currentRec.displacement_mm > prevRec.displacement_mm + 0.005) trendIndicators.displacement = "↑";
            else if (currentRec.displacement_mm < prevRec.displacement_mm - 0.005) trendIndicators.displacement = "↓";
        }
    } catch(err) {
        console.error("Error reading historical node trend: ", err);
    }

    const dirTag = (dir) => {
        if (dir === "↑") return '<span class="dir-indicator dir-up">↑ Increasing</span>';
        if (dir === "↓") return '<span class="dir-indicator dir-down">↓ Decreasing</span>';
        return '<span class="dir-indicator dir-stable">→ Stable</span>';
    };

    const sidebarTitle = document.getElementById("sidebar-node-title");
    sidebarTitle.innerText = `${node.node_id}`;

    const infoContent = document.getElementById("node-info-content");
    const nodeLabel = getNodeLabel(node.node_type);

    let sensorReadingsHtml = "";
    if (node.node_type === "UnderGround") {
        const tiltMag = getTiltMagnitude(reading.tilt_x, reading.tilt_y);
        const isTiltAbnormal = tiltMag > 0.8;
        const isVibAbnormal = reading.vibration > 0.3;

        sensorReadingsHtml = `
            <div class="telemetry-grid">
                <div class="tel-cell ${isTiltAbnormal ? 'abnormal' : ''}">
                    <span class="tel-label">TILT X</span>
                    <span class="tel-value">${reading.tilt_x.toFixed(3)}°</span>
                </div>
                <div class="tel-cell ${isTiltAbnormal ? 'abnormal' : ''}">
                    <span class="tel-label">TILT Y</span>
                    <span class="tel-value">${reading.tilt_y.toFixed(3)}°</span>
                </div>
                <div class="tel-cell ${isTiltAbnormal ? 'abnormal' : ''}">
                    <span class="tel-label">TILT MAGNITUDE</span>
                    <span class="tel-value">${tiltMag.toFixed(3)}° ${dirTag(trendIndicators.tilt)}</span>
                </div>
                <div class="tel-cell ${isVibAbnormal ? 'abnormal' : ''}">
                    <span class="tel-label">VIBRATION</span>
                    <span class="tel-value">${reading.vibration.toFixed(3)} ${dirTag(trendIndicators.vibration)}</span>
                </div>
                <div class="tel-cell">
                    <span class="tel-label">TEMPERATURE</span>
                    <span class="tel-value">${reading.temperature.toFixed(1)}°C</span>
                </div>
                <div class="tel-cell">
                    <span class="tel-label">HUMIDITY</span>
                    <span class="tel-value">${reading.humidity.toFixed(1)}%</span>
                </div>
            </div>
        `;
    } else {
        const isDispAbnormal = reading.displacement_mm > 4.0;
        sensorReadingsHtml = `
            <div class="telemetry-grid">
                <div class="tel-cell ${isDispAbnormal ? 'abnormal' : ''}">
                    <span class="tel-label">DISPLACEMENT</span>
                    <span class="tel-value">${reading.displacement_mm.toFixed(2)} mm ${dirTag(trendIndicators.displacement)}</span>
                </div>
                <div class="tel-cell">
                    <span class="tel-label">CRACK OPENING</span>
                    <span class="tel-value">${(reading.displacement_mm * 0.65).toFixed(2)} mm ${dirTag(trendIndicators.displacement)}</span>
                </div>
            </div>
        `;
    }

    let factorsList = "";
    let confidence = Math.min(98, Math.round(75 + (reading.risk_score * 0.2)));
    
    if (reading.risk_level === "LOW") {
        factorsList = `<li class="stable-factor">All features within normal statistical deviation limits.</li>`;
        confidence = 96;
    } else {
        if (node.node_type === "UnderGround") {
            const tm = getTiltMagnitude(reading.tilt_x, reading.tilt_y);
            if (tm > 0.5) {
                factorsList += `<li class="${tm > 1.5 ? 'critical-factor' : ''}">Tilt anomaly detected (Magnitude: ${tm.toFixed(2)}°)</li>`;
            }
            if (reading.vibration > 0.25) {
                factorsList += `<li class="${reading.vibration > 0.6 ? 'critical-factor' : ''}">Vibration anomaly detected (Val: ${reading.vibration.toFixed(2)})</li>`;
            }
        } else {
            if (reading.displacement_mm > 2.0) {
                factorsList += `<li class="${reading.displacement_mm > 6.0 ? 'critical-factor' : ''}">Crack displacement increasing (Val: ${reading.displacement_mm.toFixed(1)} mm)</li>`;
            }
        }
        
        if (isNodeClustered(nodeId)) {
            factorsList += `<li>Neighboring nodes correlated in spatial deformation cluster</li>`;
        }
    }

    let actionBoxHtml = "";
    if (reading.risk_level === "HIGH") {
        actionBoxHtml = `
            <div class="action-box action-critical">
                <span class="action-label">CRITICAL MONITORING TRIGGER</span>
                <p class="action-text">Inspect the surrounding surface zone and verify adjacent nodes immediately.</p>
                <div class="action-buttons-group">
                    <button class="btn btn-primary" onclick="focusCluster('${node.node_id}')">View Risk Zone</button>
                    <button class="btn btn-secondary" onclick="viewHistoryForNode('${node.node_id}')">View Sensor History</button>
                </div>
            </div>
        `;
    } else if (reading.risk_level === "MEDIUM") {
        actionBoxHtml = `
            <div class="action-box action-warning">
                <span class="action-label">WARNING PROCEDURE TRIGGER</span>
                <p class="action-text">Perform scheduled visual surface inspections. Track tilt vector rates over Panel East.</p>
                <div class="action-buttons-group">
                    <button class="btn btn-secondary" onclick="viewHistoryForNode('${node.node_id}')">View Sensor History</button>
                </div>
            </div>
        `;
    } else {
        actionBoxHtml = `
            <div class="action-box">
                <span class="action-label" style="color: var(--status-green);">STABLE OPERATION</span>
                <p class="action-text">Sensor reporting healthy structural bounds. Monitor trend logs.</p>
                <div class="action-buttons-group">
                    <button class="btn btn-secondary" onclick="viewHistoryForNode('${node.node_id}')">View Sensor History</button>
                </div>
            </div>
        `;
    }

    infoContent.innerHTML = `
        <div class="node-meta-grid">
            <div class="meta-item">
                <span class="meta-label">NODE TYPE</span>
                <span class="meta-value">${nodeLabel}</span>
            </div>
            <div class="meta-item">
                <span class="meta-label">RISK STATUS</span>
                <span class="meta-value">
                    <span class="status-badge badge-${reading.risk_level.toLowerCase()}">${reading.risk_level} RISK</span>
                </span>
            </div>
            <div class="meta-item">
                <span class="meta-label">RISK LEVEL</span>
                <span class="meta-value">${reading.risk_score.toFixed(1)} / 100</span>
            </div>
            <div class="meta-item">
                <span class="meta-label">BATTERY</span>
                <span class="meta-value">${fmt(reading.battery)}${reading.battery == null ? "" : "%"}</span>
            </div>
        </div>

        ${sensorReadingsHtml}

        <div class="explain-section">
            <h4 class="explain-title">WHY THIS NODE IS HIGH RISK</h4>
            <ul class="explain-factor-list">
                ${factorsList}
            </ul>
            <div class="confidence-box">
                Confidence <span style="color: var(--accent); font-weight: 700;">${confidence}%</span>
            </div>
        </div>

        ${actionBoxHtml}

        <div class="update-footer">
            Last Update: ${reading.timestamp ? new Date(reading.timestamp).toLocaleTimeString() : "UNAVAILABLE"}
        </div>
    `;

    updateAIExplainabilityTab(node, reading, confidence);
    updateLiveTrendChart(nodeId);
}

function focusCluster(nodeId) {
    const cluster = spatialClusters.find(c => c.some(n => n.node_id === nodeId));
    if (cluster) {
        const lats = cluster.map(n => n.latitude);
        const lons = cluster.map(n => n.longitude);
        const avgLat = lats.reduce((a, b) => a + b, 0) / lats.length;
        const avgLon = lons.reduce((a, b) => a + b, 0) / lons.length;
        map.setView([avgLat, avgLon], 15.5);
    }
}

function viewHistoryForNode(nodeId) {
    document.querySelector('[data-target="view-history"]').click();
    document.getElementById("hist-primary-node").value = nodeId;
    document.getElementById("btn-run-query").click();
}

// ==========================================================================
// TELEMETRY LIVE LINE CHART CONFIG (WARM INDUSTRIAL PALETTE)
// ==========================================================================
async function updateLiveTrendChart(nodeId) {
    const sourceAtStart = currentMode;
    const selectedMetric = document.getElementById("chart-metric-select").value;
    
    try {
        const response = await fetch(`/api/history/${nodeId}`);
        const historyData = await response.json();
        if (currentMode !== sourceAtStart || selectedNodeId !== nodeId) return;

        const labels = historyData.map(h => {
            const time = new Date(h.timestamp);
            return time.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        });

        let data = [];
        let datasetLabel = "";
        let color = "#3B82F6"; // Default Indigo/Blue

        if (selectedMetric === "risk_score") {
            data = historyData.map(h => h.risk_score);
            datasetLabel = "Risk Score";
            color = "#3B82F6";
        } else if (selectedMetric === "tilt") {
            data = historyData.map(h => getTiltMagnitude(h.tilt_x, h.tilt_y));
            datasetLabel = "Tilt Vector Magnitude";
            color = "#F59E0B"; // Amber
        } else if (selectedMetric === "vibration") {
            data = historyData.map(h => h.vibration ?? null);
            datasetLabel = "Vibration amplitude";
            color = "#10B981"; // Green
        } else if (selectedMetric === "displacement") {
            data = historyData.map(h => h.displacement_mm ?? null);
            datasetLabel = "Displacement (mm)";
            color = "#8B5CF6"; // Purple
        }

        const activeRangeBtn = document.querySelector(".range-btn.active");
        const range = activeRangeBtn ? activeRangeBtn.getAttribute("data-range") : "24h";
        
        let sliceCount = 100;
        if (range === "1h") sliceCount = 10;
        else if (range === "6h") sliceCount = 30;
        else if (range === "24h") sliceCount = 60;

        const slicedLabels = labels.slice(-sliceCount);
        const slicedData = data.slice(-sliceCount);

        const ctx = document.getElementById("liveTrendChart").getContext("2d");
        
        if (liveChartInstance) {
            liveChartInstance.destroy();
        }

        const chartColors = getChartThemeColors();

        liveChartInstance = new Chart(ctx, {
            type: 'line',
            data: {
                labels: slicedLabels,
                datasets: [{
                    label: datasetLabel,
                    data: slicedData,
                    borderColor: color,
                    backgroundColor: hexToRgbA(color, 0.08),
                    borderWidth: 1.8,
                    fill: true,
                    tension: 0.15,
                    pointRadius: 0
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        display: true,
                        labels: {
                            color: chartColors.legendColor,
                            font: { size: 10, family: 'Inter' },
                            boxWidth: 12
                        }
                    },
                    tooltip: {
                        backgroundColor: chartColors.tooltipBg,
                        borderColor: chartColors.tooltipBorder,
                        borderWidth: 1,
                        titleColor: chartColors.tooltipTitle,
                        bodyColor: chartColors.tooltipBody,
                        titleFont: { family: 'JetBrains Mono', size: 11 },
                        bodyFont: { family: 'JetBrains Mono', size: 10.5 }
                    }
                },
                scales: {
                    x: {
                        grid: { display: false },
                        ticks: { color: chartColors.tickColor, font: { size: 9, family: 'JetBrains Mono' }, maxTicksLimit: 6 }
                    },
                    y: {
                        grid: { color: chartColors.gridColor },
                        ticks: { color: chartColors.tickColor, font: { size: 9, family: 'JetBrains Mono' } }
                    }
                }
            }
        });
    } catch (e) {
        console.error("Telemetry chart refresh error", e);
    }
}

document.getElementById("chart-metric-select").addEventListener("change", () => {
    if (selectedNodeId) updateLiveTrendChart(selectedNodeId);
});

document.querySelectorAll(".range-btn").forEach(btn => {
    btn.addEventListener("click", () => {
        document.querySelectorAll(".range-btn").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        if (selectedNodeId) updateLiveTrendChart(selectedNodeId);
    });
});

function hexToRgbA(hex, opacity){
    let c;
    if(/^#([A-Fa-f0-9]{3}){1,2}$/.test(hex)){
        c= hex.substring(1).split('');
        if(c.length== 3){
            c= [c[0], c[0], c[1], c[1], c[2], c[2]];
        }
        c= '0x'+c.join('');
        return 'rgba('+[(c>>16)&255, (c>>8)&255, c&255].join(',')+','+opacity+')';
    }
    return hex;
}

// ==========================================================================
// EXPLAINABLE AI TAB RENDERER
// ==========================================================================
function updateAIExplainabilityTab(node, reading, confidence) {
    const wrapper = document.getElementById("ai-explainability-details");
    if (!wrapper) return;

    let featureBars = "";
    if (node.node_type === "UnderGround") {
        const tiltMag = getTiltMagnitude(reading.tilt_x, reading.tilt_y);
        const tiltWeight = Math.min(100, Math.round(tiltMag * 40));
        const vibWeight = Math.min(100, Math.round(reading.vibration * 120));
        const tempWeight = Math.min(100, Math.round(reading.temperature * 1.5));
        const humWeight = Math.min(100, Math.round(reading.humidity * 0.5));

        featureBars = `
            ${renderFeatureBar("Tilt magnitude vector", tiltWeight, "deg", tiltMag.toFixed(2))}
            ${renderFeatureBar("Vibration structural energy", vibWeight, "amp", reading.vibration.toFixed(2))}
            ${renderFeatureBar("Ambient Temperature", tempWeight, "°C", reading.temperature.toFixed(1))}
            ${renderFeatureBar("Humidity saturation factor", humWeight, "%", reading.humidity.toFixed(1))}
        `;
    } else {
        const dispWeight = Math.min(100, Math.round(reading.displacement_mm * 8));
        featureBars = `
            ${renderFeatureBar("Crack displacement metric", dispWeight, "mm", reading.displacement_mm.toFixed(1))}
        `;
    }

    wrapper.innerHTML = `
        <div style="margin-bottom: 10px;">
            <strong>Target Node ID:</strong> <span style="font-family: 'JetBrains Mono', monospace; font-weight:700;">${node.node_id}</span>
        </div>
        <div class="ai-feature-bars-list">
            ${featureBars}
        </div>
        <div style="border-top: 1px solid var(--border-color); padding-top: 8px; margin-top: 8px; font-size:10.5px;">
            <strong>Model Anomaly Score:</strong> <code style="background:var(--accent-bg); padding: 1.5px 3.5px; border-radius: 3px;">${(reading.risk_score / 100).toFixed(4)}</code>
        </div>
    `;
}

function renderFeatureBar(label, weightPercent, unit, rawVal) {
    return `
        <div class="ai-feature-bar">
            <div class="feature-top">
                <span>${label}</span>
                <strong>${rawVal} ${unit} (${weightPercent}%)</strong>
            </div>
            <div class="bar-outer">
                <div class="bar-inner" style="width: ${weightPercent}%; background-color: ${weightPercent > 70 ? 'var(--status-red)' : weightPercent > 40 ? 'var(--status-amber)' : 'var(--accent)'};"></div>
            </div>
        </div>
    `;
}

// ==========================================================================
// REGISTERED SENSOR REGISTRY VIEW (TAB 3)
// ==========================================================================
function updateSensorRegistryTable() {
    const tbody = document.querySelector("#sensor-registry-table tbody");
    if (!tbody) return;

    const filterBtn = document.querySelector(".filter-chip.active");
    const activeFilter = filterBtn ? filterBtn.getAttribute("data-filter") : "all";

    let rowsHtml = "";
    nodesList.forEach(node => {
        const reading = latestReadings[node.node_id] || {status:'OFFLINE',risk_level:'UNAVAILABLE',battery:null,timestamp:null};

        if (activeFilter === "ground" && node.node_type !== "UnderGround") return;
        if (
            activeFilter === "crack"
            &&
            !isCrackNode(node.node_type)
        ) return;
        if (activeFilter === "high" && reading.risk_level !== "HIGH") return;

        const nodeLabel = node.node_type === "UnderGround" ? "Ground" : "Crack";
        const badgeClass = reading.risk_level === "HIGH" ? "badge-red" : reading.risk_level === "MEDIUM" ? "badge-amber" : "badge-green";
        const rowActive = selectedNodeId === node.node_id ? "tr-active" : "";

        rowsHtml += `
            <tr class="${rowActive}" onclick="selectNodeAndSwitchTab('${node.node_id}')">
                <td style="font-family: 'JetBrains Mono', monospace; font-weight:700;">${node.node_id}</td>
                <td>${nodeLabel}</td>
                <td><strong>${reading.status || "OFFLINE"}</strong></td>
                <td><span class="status-badge ${badgeClass}">${reading.risk_level}</span></td>
                <td>${fmt(reading.battery)}${reading.battery == null ? "" : "%"}</td>
                <td>${reading.timestamp ? new Date(reading.timestamp).toLocaleTimeString() : "UNAVAILABLE"}</td>
                <td>${currentMode === "REAL" ? `RSSI ${fmt(latestReadings[node.node_id]?.rssi)} / SNR ${fmt(latestReadings[node.node_id]?.snr)}` : "SIMULATION"}</td>
            </tr>
        `;
    });

    tbody.innerHTML = rowsHtml;
}

document.querySelectorAll(".filter-chip").forEach(chip => {
    chip.addEventListener("click", () => {
        document.querySelectorAll(".filter-chip").forEach(c => c.classList.remove("active"));
        chip.classList.add("active");
        updateSensorRegistryTable();
    });
});

// ==========================================================================
// HISTORICAL ANALYSIS QUERIES (TAB 2)
// ==========================================================================
function populateHistoricalDropdowns() {
    const primarySelect = document.getElementById("hist-primary-node");
    const compareSelect = document.getElementById("hist-compare-node");

    if (!primarySelect || primarySelect.options.length > 0) return;

    nodesList.forEach(node => {
        const opt1 = new Option(`${node.node_id} (${node.node_type === 'UnderGround' ? 'Ground' : 'Crack'})`, node.node_id);
        const opt2 = new Option(`${node.node_id}`, node.node_id);
        primarySelect.add(opt1);
        compareSelect.add(opt2);
    });
}

document.getElementById("btn-run-query").addEventListener("click", async () => {
    const sourceAtStart = currentMode;
    const primaryNode = document.getElementById("hist-primary-node").value;
    const compareNode = document.getElementById("hist-compare-node").value;
    const metric = document.getElementById("hist-metric").value;
    const timeframe = document.getElementById("hist-timeframe").value;

    const resultsWrapper = document.getElementById("history-results");
    resultsWrapper.classList.remove("hidden");

    try {
        const res1 = await fetch(`/api/history/${primaryNode}`);
        const data1 = await res1.json();

        let data2 = null;
        if (compareNode !== "none") {
            const res2 = await fetch(`/api/history/${compareNode}`);
            data2 = await res2.json();
        }

        if (currentMode !== sourceAtStart) return;
        const labels = data1.map(h => new Date(h.timestamp).toLocaleTimeString());
        
        const parseDataValue = (row, field) => {
            if (field === "risk_score") return row.risk_score;
            if (field === "tilt") return getTiltMagnitude(row.tilt_x, row.tilt_y);
            if (field === "vibration") return row.vibration ?? null;
            if (field === "displacement") return row.displacement_mm ?? null;
            if (field === "battery") return row.battery;
            return 0;
        };

        const yData1 = data1.map(row => parseDataValue(row, metric));

        const datasets = [{
            label: `${primaryNode} (${metric})`,
            data: yData1,
            borderColor: '#3B82F6', // Blue Accent
            backgroundColor: 'rgba(59, 130, 246, 0.08)',
            borderWidth: 1.8,
            fill: true,
            tension: 0.15
        }];

        if (data2) {
            const yData2 = data2.map(row => parseDataValue(row, metric));
            datasets.push({
                label: `${compareNode} (${metric})`,
                data: yData2,
                borderColor: '#F59E0B', // Amber
                backgroundColor: 'rgba(245, 158, 11, 0.08)',
                borderWidth: 1.8,
                fill: true,
                tension: 0.15
            });
        }

        const ctx = document.getElementById("historicalChart").getContext("2d");
        if (historicalChartInstance) {
            historicalChartInstance.destroy();
        }

        const histColors = getChartThemeColors();

        historicalChartInstance = new Chart(ctx, {
            type: 'line',
            data: { labels, datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        labels: {
                            color: histColors.legendColor,
                            font: { size: 10, family: 'Inter' }
                        }
                    },
                    tooltip: {
                        backgroundColor: histColors.tooltipBg,
                        borderColor: histColors.tooltipBorder,
                        borderWidth: 1,
                        titleColor: histColors.tooltipTitle,
                        bodyColor: histColors.tooltipBody
                    }
                },
                scales: {
                    y: {
                        grid: { color: histColors.gridColor },
                        ticks: { color: histColors.tickColor, font: { size: 9, family: 'JetBrains Mono' } }
                    },
                    x: {
                        grid: { color: histColors.gridColor },
                        ticks: { color: histColors.tickColor, font: { size: 9, family: 'JetBrains Mono' }, maxTicksLimit: 8 }
                    }
                }
            }
        });

    } catch (err) {
        console.error("Historical query failure", err);
    }
});

// CSV/PDF Exports Mock Trigger Alerts
document.getElementById("export-csv").addEventListener("click", () => {
    alert("Export successful: Downloading CSV telemetry records for Chasnalla Colliery.");
});

document.getElementById("export-pdf").addEventListener("click", () => {
    alert("DGMS PDF engineering certificate report compiled successfully.");
});

// Settings save configs
document.getElementById("btn-save-settings").addEventListener("click", () => {
    alert("Configurations saved to Edge Sync settings file.");
});

// ==========================================================================
// DIAGNOSTICS & SYSTEM DATA ENGINE (TAB 8)
// ==========================================================================
function updateDiagnosticsData(nodeCount, readingsCount) {
    const dbSizeEl = document.getElementById("health-db-size");
    const readingsCountEl = document.getElementById("health-db-readings");

    if (dbSizeEl) dbSizeEl.innerText = "LOCAL SQLite";
    if (readingsCountEl) readingsCountEl.innerText = `${readingsCount} data entries`;
}

// ==========================================================================
// RED ALERT & MINE SIRENS SUBSYSTEM
// ==========================================================================
let isRedAlertActive = false;

let autoAnomalyLatched = false;

// After ACK, automatic siren triggering stays locked
// until all physical nodes have returned to normal.
let autoSirenNeedsNormalization = false;

// Require normal state for multiple polling cycles.
// This prevents one transient normal packet from re-arming.
let normalPollStreak = 0;

const NORMAL_POLLS_REQUIRED = 2;

// Prevent two alert HTTP requests from running simultaneously.
let alertTriggerInFlight = false;
let alertDurationTimer = null;
let alertStartTimestamp = null;
let sirenAudioCtx = null;
let sirenOsc = null;
let sirenGain = null;
let sirenLFO = null;

// Initialize or start synthesized mine siren sound
function startSirenSound() {
    try {
        if (!sirenAudioCtx) {
            const AudioContext = window.AudioContext || window.webkitAudioContext;
            if (AudioContext) {
                sirenAudioCtx = new AudioContext();
            }
        }
        if (sirenAudioCtx && sirenAudioCtx.state === "suspended") {
            sirenAudioCtx.resume();
        }
        if (sirenAudioCtx && !sirenOsc) {
            // Dual-tone wailing siren using LFO frequency modulation
            sirenOsc = sirenAudioCtx.createOscillator();
            sirenGain = sirenAudioCtx.createGain();
            sirenLFO = sirenAudioCtx.createOscillator();
            const lfoGain = sirenAudioCtx.createGain();

            sirenOsc.type = "sawtooth";
            sirenOsc.frequency.setValueAtTime(580, sirenAudioCtx.currentTime);

            // LFO sweep: 0.6 Hz oscillation between 400Hz and 800Hz
            sirenLFO.type = "sine";
            sirenLFO.frequency.setValueAtTime(0.6, sirenAudioCtx.currentTime);
            lfoGain.gain.setValueAtTime(220, sirenAudioCtx.currentTime);

            sirenLFO.connect(lfoGain);
            lfoGain.connect(sirenOsc.frequency);

            sirenGain.gain.setValueAtTime(0.12, sirenAudioCtx.currentTime);

            sirenOsc.connect(sirenGain);
            sirenGain.connect(sirenAudioCtx.destination);

            sirenOsc.start();
            sirenLFO.start();
        }
    } catch (e) {
        console.warn("Audio siren synthesis not available or blocked by browser policy:", e);
    }
}

function stopSirenSound() {
    try {
        if (sirenOsc) {
            sirenOsc.stop();
            sirenOsc.disconnect();
            sirenOsc = null;
        }
        if (sirenLFO) {
            sirenLFO.stop();
            sirenLFO.disconnect();
            sirenLFO = null;
        }
        if (sirenGain) {
            sirenGain.disconnect();
            sirenGain = null;
        }
    } catch (e) {
        console.warn("Error stopping siren audio:", e);
    }
}

// Enter RED ALERT state in UI
function enterRedAlertUI(alertData) {
    if (isRedAlertActive) return;
    isRedAlertActive = true;

    document.body.classList.add("system-red-alert");
    const overlay = document.getElementById("red-alert-overlay");
    if (overlay) {
        overlay.classList.remove("hidden");
    }

    if (alertData && alertData.message) {
        const msgEl = document.getElementById("overlay-emergency-msg");
        if (msgEl) msgEl.innerText = alertData.message;
    }

    const timeEl = document.getElementById("overlay-alert-time");
    if (timeEl) {
        const triggerDate = alertData?.triggered_at ? new Date(alertData.triggered_at) : new Date();
        timeEl.innerText = triggerDate.toLocaleTimeString();
    }

    // Header status pill update
    const headerPill = document.getElementById("header-status-pill");
    if (headerPill) {
        headerPill.innerHTML = "🚨 RED ALERT — ALL SIRENS ON";
    }

    // Active duration timer
    alertStartTimestamp = alertData?.triggered_at ? new Date(alertData.triggered_at).getTime() : Date.now();
    if (alertDurationTimer) clearInterval(alertDurationTimer);
    alertDurationTimer = setInterval(() => {
        const elapsedSec = Math.floor((Date.now() - alertStartTimestamp) / 1000);
        const mins = String(Math.floor(elapsedSec / 60)).padStart(2, '0');
        const secs = String(elapsedSec % 60).padStart(2, '0');
        const durationEl = document.getElementById("overlay-alert-duration");
        if (durationEl) durationEl.innerText = `${mins}:${secs}`;
    }, 1000);

    // Dynamic siren chip rendering
    if (alertData?.siren_zones) {
        const sirenContainer = document.getElementById("overlay-sirens-container");
        if (sirenContainer) {
            sirenContainer.innerHTML = alertData.siren_zones.map(s => `
                <div class="siren-chip active">
                    <span class="chip-dot"></span>
                    <span>${s.name}</span>
                </div>
            `).join("");
        }
    }

    // Start siren audio sound
    startSirenSound();
}

// Exit RED ALERT state in UI
function exitRedAlertUI() {
    isRedAlertActive = false;

    document.body.classList.remove("system-red-alert");
    const overlay = document.getElementById("red-alert-overlay");
    if (overlay) {
        overlay.classList.add("hidden");
    }

    if (alertDurationTimer) {
        clearInterval(alertDurationTimer);
        alertDurationTimer = null;
    }

    const headerPill = document.getElementById("header-status-pill");
    if (headerPill) {
        headerPill.innerHTML = "● LOCAL MODE (SQLite + ML)";
    }

    stopSirenSound();
}

// Trigger emergency alert via API
async function triggerEmergencyAlert() {

    // Prevent duplicate simultaneous siren triggers.
    if (
        isRedAlertActive
        ||
        alertTriggerInFlight
    ) {
        return;
    }

    alertTriggerInFlight = true;

    try {

        const response =
            await fetch(
                "/api/alert/trigger",
                {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json"
                    },
                    body: JSON.stringify({
                        message:
                            "EMERGENCY COMMAND INITIATED: ALL 8 MINE SIRENS ACTIVATED. " +
                            "CRITICAL SUBSIDENCE COLLAPSE HAZARD DETECTED. " +
                            "IMMEDIATE EVACUATION ORDER FOR PANEL EAST & UNDERGROUND WORKINGS."
                    })
                }
            );

        const data =
            await response.json();

        if (data.alert_state) {

            enterRedAlertUI(
                data.alert_state
            );

        }

    }
    catch (err) {

        console.error(
            "Failed to trigger emergency alert:",
            err
        );

    }
    finally {

        alertTriggerInFlight = false;

    }
}
// Acknowledge & reset alert via API
async function acknowledgeEmergencyAlert() {

    try {

        const response =
            await fetch(
                "/api/demo/reset",
                {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json"
                    }
                }
            );

        const data =
            await response.json();


        if (!response.ok) {

            console.error(
                "Demo reset failed:",
                data
            );

            return;
        }


        // ====================================================
        // STOP CURRENT SIREN
        // ====================================================

        exitRedAlertUI();


        // ====================================================
        // IMPORTANT:
        // DO NOT immediately re-arm the anomaly detector.
        //
        // The physical nodes may still be sitting in their
        // anomalous positions.
        // ====================================================

        autoSirenNeedsNormalization = true;

        autoAnomalyLatched = true;

        normalPollStreak = 0;

        alertTriggerInFlight = false;


        // Remove OLD current-session readings from browser state.
        //
        // SQLite history is NOT touched.
        latestReadings = {};


        console.log(
            "[TerraVeil] Alert acknowledged."
        );

        console.log(
            "[TerraVeil] Automatic siren LOCKED until all physical nodes normalize."
        );

    }
    catch (error) {

        console.error(
            "Failed to reset TerraVeil demo:",
            error
        );

    }
}
// Wire up event listeners
const alertTriggerBtn = document.getElementById("btn-trigger-alert");
if (alertTriggerBtn) {
    alertTriggerBtn.addEventListener("click", (e) => {
        e.preventDefault();
        triggerEmergencyAlert();
    });
}

const alertAckBtn = document.getElementById("btn-acknowledge-alert");
if (alertAckBtn) {
    alertAckBtn.addEventListener("click", (e) => {
        e.preventDefault();
        acknowledgeEmergencyAlert();
    });
}

// ==========================================================================
// BACKEND REAL-TIME POLLING PIPELINE
// ==========================================================================
async function pollDataPipeline() {
    if (pollBusy) return;
    pollBusy = true;
    try {
        const response = await fetch('/api/live');
        if (!response.ok) throw new Error('Live state unavailable');
        const {system, nodes:registered, readings:readingsData, twin, events} = await response.json();
        if (currentMode !== system.mode) {
            Object.values(markers).forEach(marker => map.removeLayer(marker));
            Object.keys(markers).forEach(key => delete markers[key]);
            Object.values(crackLines).forEach(line => map.removeLayer(line));
            Object.keys(crackLines).forEach(key => delete crackLines[key]);
            selectedNodeId = null;
            document.getElementById('hist-primary-node').replaceChildren();
            document.getElementById('hist-compare-node').replaceChildren(new Option('None','none'));
            document.getElementById('history-results').classList.add('hidden');
            document.getElementById('ai-insights-container').textContent = 'Generate an assessment for the current data source.';
            const explanation = document.getElementById('ai-explainability-details');
            if (explanation) explanation.textContent = 'Select a node to inspect available evidence.';
            if (liveChartInstance) {liveChartInstance.destroy(); liveChartInstance=null;}
            if (historicalChartInstance) {historicalChartInstance.destroy(); historicalChartInstance=null;}
        }
        currentMode = system.mode;
        document.querySelectorAll('.data-source-label').forEach(el => el.textContent = currentMode === 'REAL' ? 'REAL HARDWARE' : 'SIMULATION');
        document.getElementById('sidebar-mode-status').textContent = currentMode === 'REAL' ? 'Physical telemetry' : 'Simulation active';
        document.getElementById('hardware-mode').value = currentMode;
        document.getElementById('hardware-health').textContent = `Mode: ${currentMode === 'REAL' ? 'REAL HARDWARE' : 'SIMULATION'} · LoRa Network: ${system.lora_network} · HOST-01: ${system.host_01} · USB: ${system.usb?.port || 'COM7'} ${system.usb?.status || 'UNAVAILABLE'} · Mother Host: ${system.mother_host} · Database: ${system.database} · ML Engine: ${system.ml_engine}`;
        document.getElementById('pipeline-health-details').textContent = document.getElementById('hardware-health').textContent +
            ` · Received: ${system.usb?.received ?? 0} · Live updates: ${system.usb?.applied ?? 0} · Duplicates: ${system.usb?.duplicates ?? 0} · Historical: ${system.usb?.historical ?? 0} · Latest received sequence: ${system.usb?.last_sequence ?? 'UNAVAILABLE'} · ${system.usb?.last_result || system.usb?.last_error || 'Waiting for telemetry'}`;
        nodesList = registered;
        latestReadings = Object.fromEntries(readingsData.map(r => [r.node_id,r]));
// ============================================================
// AUTOMATIC SIREN STATE MACHINE
// ============================================================


// ------------------------------------------------------------
// Is ANY fresh physical node currently anomalous?
// ------------------------------------------------------------

const liveAnomalyDetected =
    readingsData.some(
        reading =>
            reading?.online === true
            &&
            Number(reading?.anomaly) === 1
    );


// ------------------------------------------------------------
// Have ALL registered REAL nodes supplied fresh readings?
//
// After /api/demo/reset a new node session is created.
// Until a fresh packet arrives for that new session,
// that node won't satisfy this check.
// ------------------------------------------------------------

const allNodesFresh =
    currentMode === "REAL"
    &&
    registered.length > 0
    &&
    registered.every(
        node =>
        {
            const reading =
                latestReadings[
                    node.node_id
                ];

            return (
                reading
                &&
                reading.online === true
            );
        }
    );


// ------------------------------------------------------------
// Are ALL fresh nodes currently NORMAL?
// ------------------------------------------------------------

const allNodesNormal =
    allNodesFresh
    &&
    registered.every(
        node =>
        {
            const reading =
                latestReadings[
                    node.node_id
                ];

            return (
                reading
                &&
                Number(reading.anomaly) === 0
            );
        }
    );


// ============================================================
// AFTER ACKNOWLEDGEMENT:
// WAIT FOR PHYSICAL NORMALIZATION
// ============================================================

if (autoSirenNeedsNormalization) {

    if (allNodesNormal) {

        normalPollStreak++;

        console.log(
            `[TerraVeil] Normalization ${normalPollStreak}/${NORMAL_POLLS_REQUIRED}`
        );


        // Require TWO consecutive clean polls.
        //
        // Since the dashboard polls every ~2 seconds,
        // this gives roughly 4 seconds of confirmed
        // normal telemetry before another demo event.
        if (
            normalPollStreak
            >=
            NORMAL_POLLS_REQUIRED
        ) {

            autoSirenNeedsNormalization =
                false;

            autoAnomalyLatched =
                false;

            normalPollStreak =
                0;


            console.log(
                "[TerraVeil] All nodes NORMAL."
            );

            console.log(
                "[TerraVeil] Automatic siren RE-ARMED."
            );

        }

    }
    else {

        // Any anomalous/offline/not-yet-refreshed node
        // resets the normalization counter.

        normalPollStreak = 0;

    }

}


// ============================================================
// NORMAL ARMED OPERATION
// ============================================================

else {

    // --------------------------------------------------------
    // NEW anomaly edge
    // --------------------------------------------------------

    if (
        liveAnomalyDetected
        &&
        !autoAnomalyLatched
    ) {

        // Latch FIRST.
        //
        // Important: do this BEFORE the asynchronous
        // alert request starts.
        autoAnomalyLatched =
            true;


        console.log(
            "[TerraVeil] NEW anomaly detected."
        );

        console.log(
            "[TerraVeil] Triggering siren ONCE."
        );


        if (
            !isRedAlertActive
            &&
            !alertTriggerInFlight
        ) {

            document
                .getElementById(
                    "btn-trigger-alert"
                )
                ?.click();

        }

    }


    // --------------------------------------------------------
    // Once the anomaly disappears naturally, re-arm for
    // another future event.
    //
    // This applies during ordinary operation.
    // ACK uses the stricter normalization process above.
    // --------------------------------------------------------

    if (!liveAnomalyDetected) {

        autoAnomalyLatched =
            false;

    }

}

        if (!liveAnomalyDetected) {
            autoAnomalyLatched = false;
        }
        updateHardwareConnectivity(system);
        window.backendZones = twin.zones;
        selectedNodeId ||= nodesList[0]?.node_id;
        document.getElementById('kpi-active-nodes').textContent = `${readingsData.filter(r=>r.online).length} / ${nodesList.length}`;
        document.getElementById('kpi-active-status').textContent = `${readingsData.filter(r=>r.online).length} ONLINE`;
        document.getElementById('kpi-system-health').textContent = system.mother_host;
        document.getElementById('kpi-sync-sec').textContent = '2s';
        const totalReadingsStored = system.readings_count;
        populateHistoricalDropdowns();

        // Calculate and map spatial clusters (Contour deformation areas)
        calculateSpatialClusters(nodesList, latestReadings);

        // Render and update map node markers
        nodesList.forEach(node => {
            renderNodeMarker(node, latestReadings[node.node_id]);
        });

        // Update events logger and safety logs
        updateAlertsLog(latestReadings, events);

        // Update Sensor registry list table
        updateSensorRegistryTable();

        // Check system alert & siren status
        try {
            const alertRes = await fetch("/api/alert/status");
            const alertData = await alertRes.json();
            if (alertData.status === "RED_ALERT" && !isRedAlertActive) {
                enterRedAlertUI(alertData);
            } else if (alertData.status === "NORMAL" && isRedAlertActive) {
                exitRedAlertUI();
            }
        } catch (e) {
            // Ignore transient sync error
        }

        // Update sync time
        lastSyncTime = new Date();
        document.getElementById("header-sync-time").innerText = lastSyncTime.toLocaleTimeString();

        // Update Selected node panel if focused
        if (selectedNodeId) {
            const node = nodesList.find(n => n.node_id === selectedNodeId);
            const reading = latestReadings[selectedNodeId];
            if (node) {
                selectNode(selectedNodeId);
            }
        }

        updateDiagnosticsData(nodesList.length, totalReadingsStored);

    } catch (error) {
        console.error("Sync data failure", error);
        document.getElementById('hardware-health').textContent = 'Mother Host: OFFLINE · Live telemetry unconfirmed';
        document.getElementById('pipeline-health-details').textContent = 'Mother Host unavailable; sensor status unconfirmed';
        document.getElementById('kpi-active-status').textContent = 'UNCONFIRMED';
        Object.values(latestReadings).forEach(r => {r.online=false;r.status='OFFLINE';});
        nodesList.forEach(n => renderNodeMarker(n,latestReadings[n.node_id]));
        updateSensorRegistryTable();
        if(selectedNodeId) selectNode(selectedNodeId);
        
        const syncTimeEl = document.getElementById("header-sync-time");
        if (syncTimeEl) {
            syncTimeEl.innerHTML = '<span class="status-badge badge-red" style="padding: 2px 4px;">⚠ CONNECTION INTERRUPTED</span>';
        }
    } finally { pollBusy = false; }
}

// ==========================================================================
// TIER 2 ASYNCHRONOUS STRATEGIC AI INSIGHTS SYNTHESIZER
// ==========================================================================
async function synthesizeAIHistoricalBrief() {
    const sourceAtStart = currentMode;
    const container = document.getElementById("ai-insights-container");
    const btn = document.getElementById("btn-generate-ai-brief");
    if (!container) return;

    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span>⏳</span><span>Analyzing Historical Telemetry...</span>';
    }

    try {
        const response = await fetch("/api/ai/insights");
        const data = await response.json();

        if (currentMode !== sourceAtStart) return;
        if (data.status === "INSUFFICIENT_DATA") {
            container.innerHTML = `
                <div class="insights-placeholder">
                    <span class="placeholder-icon">ℹ</span>
                    <p>${data.message}</p>
                </div>
            `;
            return;
        }

        const metrics = data.metrics_summary || {};
        const recommendationsHtml = (data.engineering_recommendations || []).map(r => `
            <div class="rec-item">${r}</div>
        `).join("");

        container.innerHTML = `
            <div class="insight-block">
                <!-- Executive Summary -->
                <div class="insight-summary-card">
                    <div style="display:flex; justify-content:space-between; margin-bottom: 4px;">
                        <strong style="color: var(--accent); font-family: 'JetBrains Mono', monospace; font-size: 10px; text-transform: uppercase;">Executive Geotechnical Assessment:</strong>
                        <span style="font-size: 9.5px; color: var(--text-muted); font-family: 'JetBrains Mono', monospace;">Synthesized: ${new Date(data.generated_at).toLocaleTimeString()}</span>
                    </div>
                    <p>${data.geotechnical_executive_summary}</p>
                </div>

                <!-- Risk Progression -->
                <div style="background-color: var(--bg-input); border: 1px solid var(--border-subtle); border-radius: var(--radius-sm); padding: 8px 12px; font-size: 11px;">
                    <strong style="color: #93C5FD; font-family: 'JetBrains Mono', monospace;">Trajectory Status:</strong>
                    <span style="color: var(--text-primary); margin-left: 6px;">${data.risk_progression?.analysis || 'Nominal'}</span>
                </div>

                <!-- Aggregated Multi-Node Metrics Grid -->
                <div class="insight-metrics-grid">
                    <div class="insight-metric-item">
                        <span>Records Analyzed</span>
                        <strong>${metrics.total_historical_samples || 0}</strong>
                    </div>
                    <div class="insight-metric-item">
                        <span>Avg Network Risk</span>
                        <strong>${metrics.average_network_risk || 0} / 100</strong>
                    </div>
                    <div class="insight-metric-item">
                        <span>Peak Risk Score</span>
                        <strong class="${metrics.peak_recorded_risk > 70 ? 'text-red' : metrics.peak_recorded_risk > 40 ? 'text-amber' : ''}">${metrics.peak_recorded_risk || 0} / 100</strong>
                    </div>
                    <div class="insight-metric-item">
                        <span>Max Aperture Drift</span>
                        <strong>${fmt(metrics.peak_crack_displacement_mm)}${metrics.peak_crack_displacement_mm == null ? "" : " mm"}</strong>
                    </div>
                </div>

                <!-- DGMS Compliance & Action Items -->
                <div class="insight-recommendations-list">
                    <h5>DGMS Safety Action Items &amp; Protocol Checklist:</h5>
                    ${recommendationsHtml}
                </div>

                <!-- Safety Note Footer -->
                <div style="border-top: 1px solid var(--border-subtle); padding-top: 6px; font-size: 9.5px; color: var(--text-muted); font-family: 'JetBrains Mono', monospace; text-align: right;">
                    ${data.safety_certification}
                </div>
            </div>
        `;

    } catch (err) {
        console.error("Failed to synthesize AI brief:", err);
        container.innerHTML = `<div class="empty-list-state">Error synthesizing strategic insights. Verify edge server connection.</div>`;
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = '<span>⚡</span><span>Synthesize Historical AI Assessment</span>';
        }
    }
}

const aiBriefBtn = document.getElementById("btn-generate-ai-brief");
if (aiBriefBtn) {
    aiBriefBtn.addEventListener("click", (e) => {
        e.preventDefault();
        synthesizeAIHistoricalBrief();
    });
}

// Initialize theme toggle and preferences
initTheme();

// Initial polling run and tick timing
document.getElementById('hardware-mode').addEventListener('change', async event => {
    const response = await fetch('/api/system', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode:event.target.value})});
    if (!response.ok) {alert('Mode change failed');return;}
    await pollDataPipeline();
});
pollDataPipeline();
setInterval(pollDataPipeline, 2000);

async function startNodeSession(nodeId) {
    if (!confirm(`Confirm ${nodeId} has physically restarted and previous buffered packets have been drained. Begin a new sequence session? Existing history will be kept.`)) return;
    try {
        const response = await fetch(`/api/nodes/${encodeURIComponent(nodeId)}/session`, {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({confirmed_restart: true})
        });
        if (!response.ok) throw new Error('Session change failed');
        await pollDataPipeline();
    } catch (error) { alert(error.message); }
}
