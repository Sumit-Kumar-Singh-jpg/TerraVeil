#!/usr/bin/env python3
"""
TerraVeil Stage 2 installer
===========================

Adds live STL orientation + complete live telemetry modal for:
  - UG-01 -> static/stl-files/UG-Node.stl
  - UG-02 -> static/stl-files/UG-Node.stl
  - LD-01 -> static/stl-files/LD-Node.stl

Run from the TerraVeil repository root:
    python3 apply_stage2_live_stl.py

Files modified:
    database.py
    serial_bridge.py
    telemetry.py
    static/js/app.js
    templates/index.html

Files created:
    static/js/node-orientation.js
    static/css/node-orientation.css

The implementation is offline-first and uses the repository's existing local
Three.js + OrbitControls. It does NOT require STLLoader or a CDN.
"""

from pathlib import Path

ROOT = Path.cwd()

required = {
    "database": ROOT / "database.py",
    "bridge": ROOT / "serial_bridge.py",
    "telemetry": ROOT / "telemetry.py",
    "appjs": ROOT / "static" / "js" / "app.js",
    "index": ROOT / "templates" / "index.html",
    "three": ROOT / "static" / "vendor" / "three" / "three.module.js",
    "orbit": ROOT / "static" / "vendor" / "three" / "addons" / "controls" / "OrbitControls.js",
    "ug_stl": ROOT / "static" / "stl-files" / "UG-Node.stl",
    "ld_stl": ROOT / "static" / "stl-files" / "LD-Node.stl",
}

for name, path in required.items():
    if not path.exists():
        raise SystemExit(
            f"Missing required file ({name}): {path}\n"
            "Run this script from the TerraVeil repository root and ensure the "
            "Stage 2 STL files have been pulled."
        )


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"{label}: expected exactly one match but found {count}. "
            "The repository may differ from the Stage 2 baseline."
        )
    return text.replace(old, new, 1)


def write_updated(path: Path, original: str, updated: str):
    if original == updated:
        print(f"[UNCHANGED] {path}")
        return
    path.write_text(updated)
    print(f"[UPDATED]   {path}")


# ===========================================================================
# 1. DATABASE: persist complete MPU telemetry
# ===========================================================================
path = required["database"]
original = path.read_text()
text = original

old = '''        "roll": "REAL",
        "pitch": "REAL",
        "soil": "REAL",
'''
new = '''        "roll": "REAL",
        "pitch": "REAL",
        "yaw": "REAL",
        "ax": "REAL",
        "ay": "REAL",
        "az": "REAL",
        "gx": "REAL",
        "gy": "REAL",
        "gz": "REAL",
        "soil": "REAL",
'''
text = replace_once(text, old, new, "database MPU columns")
write_updated(path, original, text)


# ===========================================================================
# 2. SERIAL BRIDGE: preserve every MPU field emitted by HUB_DATA
# ===========================================================================
path = required["bridge"]
original = path.read_text()
text = original

old = '''            if node_type == "UG":
                vibration = self._finite_float(fields["VIBRATION"])
                if vibration not in (0.0, 1.0):
                    return None
                payload.update(
                    roll=self._finite_float(fields["ROLL"]),
                    pitch=self._finite_float(fields["PITCH"]),
                    vibration=int(vibration),
                    soil=self._finite_float(fields["SOIL_ADC"]),
                )
            else:  # LD
                payload.update(
                    displacement_mm=self._finite_float(fields["DISP_MM"]),
                    potentiometer_raw=self._finite_float(fields["POT_ADC"]),
                )
                if "ROLL" in fields:
                    payload["roll"] = self._finite_float(fields["ROLL"])
                if "PITCH" in fields:
                    payload["pitch"] = self._finite_float(fields["PITCH"])
'''

new = '''            # Complete MPU6050 payload shared by UG and LD nodes.
            # The hub already emits these fields; preserve them all the way
            # through ingestion so the dashboard can render true live orientation
            # and expose the complete radio packet.
            for source_key, payload_key in (
                ("ROLL", "roll"),
                ("PITCH", "pitch"),
                ("YAW", "yaw"),
                ("AX", "ax"),
                ("AY", "ay"),
                ("AZ", "az"),
                ("GX", "gx"),
                ("GY", "gy"),
                ("GZ", "gz"),
            ):
                if source_key in fields:
                    payload[payload_key] = self._finite_float(fields[source_key])

            if node_type == "UG":
                vibration = self._finite_float(fields["VIBRATION"])
                if vibration not in (0.0, 1.0):
                    return None
                payload.update(
                    vibration=int(vibration),
                    soil=self._finite_float(fields["SOIL_ADC"]),
                )
            else:  # LD
                payload.update(
                    displacement_mm=self._finite_float(fields["DISP_MM"]),
                    potentiometer_raw=self._finite_float(fields["POT_ADC"]),
                )
'''
text = replace_once(text, old, new, "serial bridge HUB_DATA parser")
write_updated(path, original, text)


# ===========================================================================
# 3. TELEMETRY INGESTION: validate/store optional complete MPU fields
# ===========================================================================
path = required["telemetry"]
original = path.read_text()
text = original

old = '''def _real_underground_fields(payload, row):
    row["roll"] = number(payload, "roll", -180, 180)
    row["pitch"] = number(payload, "pitch", -180, 180)
    row["vibration"] = number(payload, "vibration", 0, 1)
    row["soil"] = number(payload, "soil", 0, 4095)

    if row["vibration"] not in (0, 1):
        raise ValueError("vibration must be 0 or 1")

    row["tilt_x"] = row["roll"]
    row["tilt_y"] = row["pitch"]


def _real_displacement_fields(payload, row):
    row["displacement_mm"] = number(payload, "displacement_mm", 0, 10000)
    row["potentiometer_raw"] = number(payload, "potentiometer_raw", 0, 4095)

    if "roll" in payload:
        row["roll"] = number(payload, "roll", -180, 180)
    if "pitch" in payload:
        row["pitch"] = number(payload, "pitch", -180, 180)
'''

new = '''def _real_mpu_fields(payload, row):
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
'''
text = replace_once(text, old, new, "telemetry MPU ingestion")
write_updated(path, original, text)


# ===========================================================================
# 4. APP.JS: publish telemetry snapshot for the modal + 1 s UI refresh
# ===========================================================================
path = required["appjs"]
original = path.read_text()
text = original

old = '''        nodesList = registered;
        latestReadings = Object.fromEntries(readingsData.map(r => [r.node_id,r]));
// ============================================================
'''

new = '''        nodesList = registered;
        latestReadings = Object.fromEntries(readingsData.map(r => [r.node_id,r]));

        // Publish the same authoritative /api/live snapshot for local modules.
        // node-orientation.js consumes this without creating another HTTP poll.
        const telemetrySnapshot = {
            mode: currentMode,
            nodes: nodesList,
            readings: latestReadings,
            system,
            receivedAt: Date.now()
        };
        window.TerraVeilTelemetry = telemetrySnapshot;
        window.dispatchEvent(new CustomEvent(
            'terraveil:telemetry',
            {detail: telemetrySnapshot}
        ));
// ============================================================
'''
text = replace_once(text, old, new, "app.js telemetry event")

text = replace_once(
    text,
    "        document.getElementById('kpi-sync-sec').textContent = '2s';",
    "        document.getElementById('kpi-sync-sec').textContent = '1s';",
    "app.js KPI sync interval",
)

text = replace_once(
    text,
    "setInterval(pollDataPipeline, 2000);",
    "setInterval(pollDataPipeline, 1000);",
    "app.js live polling interval",
)

write_updated(path, original, text)


# ===========================================================================
# 5. INDEX.HTML: add local orientation stylesheet + module
# ===========================================================================
path = required["index"]
original = path.read_text()
text = original

css_anchor = '''    <link rel="stylesheet" href="{{ url_for('static', filename='css/node-placement.css') }}">
'''
css_new = css_anchor + '''    <link rel="stylesheet" href="{{ url_for('static', filename='css/node-orientation.css') }}">
'''
text = replace_once(text, css_anchor, css_new, "index orientation stylesheet")

script_anchor = '''<script src="{{ url_for('static', filename='js/node-placement.js') }}"></script>
</body>
'''
script_new = '''<script src="{{ url_for('static', filename='js/node-placement.js') }}"></script>
<script type="module" src="{{ url_for('static', filename='js/node-orientation.js') }}"></script>
</body>
'''
text = replace_once(text, script_anchor, script_new, "index orientation module")
write_updated(path, original, text)


# ===========================================================================
# 6. NODE ORIENTATION MODULE
# ===========================================================================
orientation_js = r'''import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const NODE_CONFIG = {
    'UG-01': {
        label: 'Underground Monitoring Node',
        model: '/static/stl-files/UG-Node.stl',
        card: 'conn-ug01',
        kind: 'UG'
    },
    'UG-02': {
        label: 'Underground Monitoring Node',
        model: '/static/stl-files/UG-Node.stl',
        card: 'conn-ug02',
        kind: 'UG'
    },
    'LD-01': {
        label: 'Linear Displacement Node',
        model: '/static/stl-files/LD-Node.stl',
        card: 'conn-ld01',
        kind: 'LD'
    }
};

// If the CAD body's local axes differ from the physical MPU6050 mounting axes,
// adjust these ONCE after a bench test. Values are degrees.
const MODEL_AXIS_CORRECTION_DEG = {
    'UG-01': {x: 0, y: 0, z: 0},
    'UG-02': {x: 0, y: 0, z: 0},
    'LD-01': {x: 0, y: 0, z: 0}
};

const state = {
    activeNodeId: null,
    reading: null,
    modal: null,
    viewport: null,
    renderer: null,
    scene: null,
    camera: null,
    controls: null,
    modelRoot: null,
    animationFrame: null,
    resizeObserver: null,
    modelCache: new Map(),
    targetQuaternion: new THREE.Quaternion(),
    displayQuaternion: new THREE.Quaternion(),
    correctionQuaternion: new THREE.Quaternion(),
};

const deg = THREE.MathUtils.degToRad;

function finite(value) {
    return Number.isFinite(Number(value));
}

function number(value, digits = 2, suffix = '') {
    return finite(value) ? `${Number(value).toFixed(digits)}${suffix}` : '—';
}

function integer(value) {
    return finite(value) ? `${Math.round(Number(value))}` : '—';
}

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, char => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#039;'
    }[char]));
}

function ensureModal() {
    if (state.modal) return state.modal;

    const modal = document.createElement('div');
    modal.id = 'node-orientation-modal';
    modal.className = 'node-orientation-modal';
    modal.setAttribute('aria-hidden', 'true');
    modal.innerHTML = `
        <div class="node-orientation-backdrop" data-node-modal-close></div>

        <section class="node-orientation-dialog" role="dialog" aria-modal="true"
                 aria-labelledby="node-orientation-title">
            <header class="node-orientation-header">
                <div>
                    <div class="node-orientation-eyebrow">LIVE PHYSICAL NODE</div>
                    <h2 id="node-orientation-title">Node</h2>
                    <div id="node-orientation-subtitle" class="node-orientation-subtitle"></div>
                </div>

                <div class="node-orientation-header-right">
                    <span id="node-orientation-live" class="node-orientation-live-pill">NO DATA</span>
                    <button type="button" class="node-orientation-close"
                            aria-label="Close node viewer" data-node-modal-close>×</button>
                </div>
            </header>

            <div class="node-orientation-body">
                <div class="node-orientation-view-column">
                    <div id="node-orientation-viewport" class="node-orientation-viewport">
                        <div class="node-orientation-loading" id="node-orientation-loading">
                            Loading local STL…
                        </div>
                        <div class="node-orientation-axis-label axis-x">X · ROLL</div>
                        <div class="node-orientation-axis-label axis-y">Y · PITCH</div>
                        <div class="node-orientation-axis-label axis-z">Z · YAW</div>
                    </div>

                    <div class="node-orientation-angle-strip">
                        <div><span>ROLL</span><strong id="node-live-roll">—</strong></div>
                        <div><span>PITCH</span><strong id="node-live-pitch">—</strong></div>
                        <div><span>YAW</span><strong id="node-live-yaw">—</strong></div>
                    </div>

                    <div class="node-orientation-hint">
                        Drag to orbit · Scroll to zoom · STL orientation follows live MPU6050 telemetry
                    </div>
                </div>

                <aside class="node-orientation-telemetry">
                    <div class="node-orientation-section-title">LIVE SENSOR PACKET</div>
                    <div id="node-orientation-sensors" class="node-orientation-grid"></div>

                    <div class="node-orientation-section-title secondary">RADIO / TRANSPORT</div>
                    <div id="node-orientation-radio" class="node-orientation-grid compact"></div>
                </aside>
            </div>
        </section>
    `;

    document.body.appendChild(modal);
    state.modal = modal;
    state.viewport = modal.querySelector('#node-orientation-viewport');

    modal.querySelectorAll('[data-node-modal-close]').forEach(el => {
        el.addEventListener('click', closeModal);
    });

    return modal;
}

function parseBinarySTL(buffer) {
    if (!(buffer instanceof ArrayBuffer) || buffer.byteLength < 84) {
        throw new Error('STL is empty or invalid');
    }

    const view = new DataView(buffer);
    const triangleCount = view.getUint32(80, true);
    const expectedLength = 84 + triangleCount * 50;

    if (triangleCount <= 0 || expectedLength > buffer.byteLength) {
        throw new Error('Expected a binary STL file');
    }

    const positions = new Float32Array(triangleCount * 9);
    const normals = new Float32Array(triangleCount * 9);

    let offset = 84;
    let write = 0;

    for (let i = 0; i < triangleCount; i++) {
        const nx = view.getFloat32(offset, true);
        const ny = view.getFloat32(offset + 4, true);
        const nz = view.getFloat32(offset + 8, true);
        offset += 12;

        for (let vertex = 0; vertex < 3; vertex++) {
            positions[write] = view.getFloat32(offset, true);
            normals[write++] = nx;
            positions[write] = view.getFloat32(offset + 4, true);
            normals[write++] = ny;
            positions[write] = view.getFloat32(offset + 8, true);
            normals[write++] = nz;
            offset += 12;
        }

        offset += 2;
    }

    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute('normal', new THREE.BufferAttribute(normals, 3));
    geometry.computeBoundingBox();
    geometry.computeBoundingSphere();

    return geometry;
}

async function loadGeometry(url) {
    if (state.modelCache.has(url)) {
        return state.modelCache.get(url).clone();
    }

    const response = await fetch(url, {cache: 'force-cache'});
    if (!response.ok) {
        throw new Error(`STL load failed (${response.status})`);
    }

    const geometry = parseBinarySTL(await response.arrayBuffer());

    // Supplied CAD exports are millimetre based. Center the geometry so live
    // rotation occurs around the physical body rather than the CAD world origin.
    geometry.center();
    geometry.computeBoundingSphere();

    // Normalize for the viewer camera only; geometry proportions are preserved.
    const radius = geometry.boundingSphere?.radius || 1;
    const viewerRadius = 2.15;
    geometry.scale(viewerRadius / radius, viewerRadius / radius, viewerRadius / radius);
    geometry.computeBoundingBox();
    geometry.computeBoundingSphere();

    state.modelCache.set(url, geometry.clone());
    return geometry;
}

function setupScene() {
    if (state.renderer) return;

    const viewport = state.viewport;
    const width = Math.max(320, viewport.clientWidth);
    const height = Math.max(300, viewport.clientHeight);

    const renderer = new THREE.WebGLRenderer({antialias: true, alpha: true});
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(width, height, false);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    viewport.prepend(renderer.domElement);

    const scene = new THREE.Scene();

    const camera = new THREE.PerspectiveCamera(38, width / height, 0.05, 100);
    camera.position.set(5.2, 3.6, 5.8);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.target.set(0, 0, 0);
    controls.minDistance = 3.3;
    controls.maxDistance = 12;

    scene.add(new THREE.HemisphereLight(0xffffff, 0x1b2635, 2.1));

    const key = new THREE.DirectionalLight(0xffffff, 2.2);
    key.position.set(4, 6, 5);
    scene.add(key);

    const rim = new THREE.DirectionalLight(0x8ebcff, 1.1);
    rim.position.set(-5, 2, -4);
    scene.add(rim);

    const grid = new THREE.GridHelper(8, 16, 0x475569, 0x263140);
    grid.position.y = -2.45;
    scene.add(grid);

    const axes = new THREE.AxesHelper(2.7);
    axes.position.set(-2.8, -2.35, -2.8);
    scene.add(axes);

    state.renderer = renderer;
    state.scene = scene;
    state.camera = camera;
    state.controls = controls;

    state.resizeObserver = new ResizeObserver(() => resizeScene());
    state.resizeObserver.observe(viewport);

    animate();
}

function resizeScene() {
    if (!state.renderer || !state.viewport) return;
    const width = Math.max(1, state.viewport.clientWidth);
    const height = Math.max(1, state.viewport.clientHeight);

    state.renderer.setSize(width, height, false);
    state.camera.aspect = width / height;
    state.camera.updateProjectionMatrix();
}

function animate() {
    state.animationFrame = requestAnimationFrame(animate);

    if (state.modelRoot) {
        state.displayQuaternion.slerp(state.targetQuaternion, 0.12);
        state.modelRoot.quaternion.copy(state.displayQuaternion);
    }

    state.controls?.update();
    state.renderer?.render(state.scene, state.camera);
}

function setTargetOrientation(reading) {
    if (!reading) return;

    const roll = finite(reading.roll) ? Number(reading.roll) : 0;
    const pitch = finite(reading.pitch) ? Number(reading.pitch) : 0;
    const yaw = finite(reading.yaw) ? Number(reading.yaw) : 0;

    // Intrinsic aerospace-style roll(X), pitch(Y), yaw(Z).
    const liveEuler = new THREE.Euler(deg(roll), deg(pitch), deg(yaw), 'ZYX');
    const liveQuaternion = new THREE.Quaternion().setFromEuler(liveEuler);

    const correction = MODEL_AXIS_CORRECTION_DEG[state.activeNodeId] || {x: 0, y: 0, z: 0};
    const correctionEuler = new THREE.Euler(
        deg(correction.x || 0),
        deg(correction.y || 0),
        deg(correction.z || 0),
        'XYZ'
    );
    state.correctionQuaternion.setFromEuler(correctionEuler);

    state.targetQuaternion.copy(liveQuaternion).multiply(state.correctionQuaternion);
}

async function swapModel(nodeId) {
    const config = NODE_CONFIG[nodeId];
    if (!config) return;

    setupScene();

    const loading = document.getElementById('node-orientation-loading');
    loading.textContent = 'Loading local STL…';
    loading.classList.remove('hidden');

    try {
        const geometry = await loadGeometry(config.model);

        if (nodeId !== state.activeNodeId) return;

        if (state.modelRoot) {
            state.scene.remove(state.modelRoot);
            state.modelRoot.traverse?.(child => {
                if (child.material?.dispose) child.material.dispose();
            });
        }

        const material = new THREE.MeshStandardMaterial({
            color: 0xc3ccd8,
            metalness: 0.22,
            roughness: 0.48,
            flatShading: false
        });

        const mesh = new THREE.Mesh(geometry, material);
        const edges = new THREE.LineSegments(
            new THREE.EdgesGeometry(geometry, 28),
            new THREE.LineBasicMaterial({
                color: 0x334155,
                transparent: true,
                opacity: 0.42
            })
        );

        const root = new THREE.Group();
        root.add(mesh);
        root.add(edges);

        state.scene.add(root);
        state.modelRoot = root;
        state.displayQuaternion.copy(state.targetQuaternion);
        root.quaternion.copy(state.displayQuaternion);

        loading.classList.add('hidden');
    } catch (error) {
        console.error('[TerraVeil] STL viewer error:', error);
        loading.textContent = `Unable to load STL: ${error.message}`;
    }
}

function metric(label, value, unit = '', tone = '') {
    return `
        <div class="node-orientation-metric ${tone}">
            <span>${escapeHtml(label)}</span>
            <strong>${escapeHtml(value)}${unit ? `<small>${escapeHtml(unit)}</small>` : ''}</strong>
        </div>
    `;
}

function renderTelemetry() {
    if (!state.modal || !state.activeNodeId) return;

    const nodeId = state.activeNodeId;
    const config = NODE_CONFIG[nodeId];
    const reading = state.reading || {};

    document.getElementById('node-orientation-title').textContent = nodeId;
    document.getElementById('node-orientation-subtitle').textContent =
        `${config.label} · ${config.model.split('/').pop()}`;

    const livePill = document.getElementById('node-orientation-live');
    const online = reading?.online === true;
    livePill.textContent = online ? '● LIVE' : '○ OFFLINE / LAST SAMPLE';
    livePill.classList.toggle('online', online);

    document.getElementById('node-live-roll').textContent = number(reading.roll, 2, '°');
    document.getElementById('node-live-pitch').textContent = number(reading.pitch, 2, '°');
    document.getElementById('node-live-yaw').textContent = number(reading.yaw, 2, '°');

    let sensorHtml = '';

    if (config.kind === 'UG') {
        sensorHtml += metric('Soil ADC', integer(reading.soil));
        sensorHtml += metric(
            'Vibration',
            finite(reading.vibration)
                ? (Number(reading.vibration) >= 1 ? 'DETECTED' : 'NORMAL')
                : '—',
            '',
            Number(reading.vibration) >= 1 ? 'danger' : ''
        );
    } else {
        sensorHtml += metric('Displacement', number(reading.displacement_mm, 2), ' mm');
        sensorHtml += metric('Potentiometer ADC', integer(reading.potentiometer_raw));
    }

    sensorHtml += metric('Roll', number(reading.roll, 2), '°');
    sensorHtml += metric('Pitch', number(reading.pitch, 2), '°');
    sensorHtml += metric('Yaw (relative)', number(reading.yaw, 2), '°');

    sensorHtml += metric('Accel X', number(reading.ax, 3), ' g');
    sensorHtml += metric('Accel Y', number(reading.ay, 3), ' g');
    sensorHtml += metric('Accel Z', number(reading.az, 3), ' g');

    sensorHtml += metric('Gyro X', number(reading.gx, 3), ' °/s');
    sensorHtml += metric('Gyro Y', number(reading.gy, 3), ' °/s');
    sensorHtml += metric('Gyro Z', number(reading.gz, 3), ' °/s');

    document.getElementById('node-orientation-sensors').innerHTML = sensorHtml;

    const lastSeen = reading.last_seen
        ? new Date(reading.last_seen).toLocaleTimeString()
        : '—';

    document.getElementById('node-orientation-radio').innerHTML =
        metric('Sequence', integer(reading.sequence)) +
        metric('RSSI', number(reading.rssi, 0), ' dBm') +
        metric('SNR', number(reading.snr, 1), ' dB') +
        metric('Last seen', lastSeen) +
        metric('Risk', escapeHtml(reading.risk_level || '—')) +
        metric('ML score', number(reading.ml_score, 1));

    setTargetOrientation(reading);
}

async function openModal(nodeId) {
    const config = NODE_CONFIG[nodeId];
    if (!config) return;

    ensureModal();

    state.activeNodeId = nodeId;
    state.reading = window.TerraVeilTelemetry?.readings?.[nodeId] || null;

    // Never carry another node's last displayed attitude into this viewer.
    state.targetQuaternion.identity();
    state.displayQuaternion.identity();

    state.modal.classList.add('open');
    state.modal.setAttribute('aria-hidden', 'false');
    document.body.classList.add('node-orientation-modal-open');

    renderTelemetry();
    await swapModel(nodeId);
    requestAnimationFrame(resizeScene);
}

function closeModal() {
    if (!state.modal) return;
    state.modal.classList.remove('open');
    state.modal.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('node-orientation-modal-open');
    state.activeNodeId = null;
    state.reading = null;
}

function makeCardsInteractive() {
    Object.entries(NODE_CONFIG).forEach(([nodeId, config]) => {
        const card = document.getElementById(config.card);
        if (!card || card.dataset.orientationBound === '1') return;

        card.dataset.orientationBound = '1';
        card.classList.add('node-orientation-card');
        card.setAttribute('role', 'button');
        card.setAttribute('tabindex', '0');
        card.setAttribute('aria-label', `Open live 3D telemetry for ${nodeId}`);
        card.title = `Open ${nodeId} live 3D orientation`;

        card.addEventListener('click', () => openModal(nodeId));
        card.addEventListener('keydown', event => {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                openModal(nodeId);
            }
        });
    });
}

window.addEventListener('terraveil:telemetry', event => {
    const snapshot = event.detail;

    makeCardsInteractive();

    if (snapshot?.mode !== 'REAL' && state.activeNodeId) {
        closeModal();
        return;
    }

    if (!state.activeNodeId) return;

    state.reading = snapshot?.readings?.[state.activeNodeId] || null;
    renderTelemetry();
});

window.addEventListener('keydown', event => {
    if (event.key === 'Escape' && state.modal?.classList.contains('open')) {
        closeModal();
    }
});

ensureModal();
makeCardsInteractive();
'''

orientation_css = r'''/* ============================================================
   TerraVeil live node STL / telemetry viewer
   ============================================================ */

.node-orientation-card {
    position: relative;
    cursor: pointer;
    transition: transform 150ms ease, border-color 150ms ease, box-shadow 150ms ease;
}

.node-orientation-card::after {
    content: "OPEN 3D";
    position: absolute;
    top: 8px;
    right: 8px;
    font-family: "JetBrains Mono", monospace;
    font-size: 8px;
    font-weight: 700;
    letter-spacing: .08em;
    color: var(--text-muted);
    opacity: .72;
}

.node-orientation-card:hover,
.node-orientation-card:focus-visible {
    transform: translateY(-1px);
    border-color: rgba(96, 165, 250, .62) !important;
    box-shadow: 0 0 0 1px rgba(96, 165, 250, .13), 0 10px 28px rgba(0, 0, 0, .17);
    outline: none;
}

.node-orientation-card:hover::after,
.node-orientation-card:focus-visible::after {
    color: #60a5fa;
    opacity: 1;
}

body.node-orientation-modal-open { overflow: hidden; }

.node-orientation-modal {
    position: fixed;
    inset: 0;
    z-index: 12000;
    display: none;
}
.node-orientation-modal.open { display: block; }

.node-orientation-backdrop {
    position: absolute;
    inset: 0;
    background: rgba(3, 7, 18, .78);
    backdrop-filter: blur(7px);
}

.node-orientation-dialog {
    position: absolute;
    inset: 4.5vh 4vw;
    display: flex;
    flex-direction: column;
    min-width: 0;
    overflow: hidden;
    background: var(--bg-card, #111720);
    border: 1px solid var(--border-subtle, #2a303a);
    border-radius: 14px;
    box-shadow: 0 28px 90px rgba(0, 0, 0, .55);
}

.node-orientation-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 18px;
    padding: 18px 20px 16px;
    border-bottom: 1px solid var(--border-subtle, #2a303a);
}

.node-orientation-eyebrow,
.node-orientation-section-title {
    font: 700 9px/1.3 "JetBrains Mono", monospace;
    letter-spacing: .12em;
    color: #60a5fa;
}

.node-orientation-header h2 {
    margin: 4px 0 2px;
    font-size: 22px;
    color: var(--text-primary, #f2f4f7);
}

.node-orientation-subtitle {
    font-size: 11px;
    color: var(--text-muted, #9aa3af);
}

.node-orientation-header-right {
    display: flex;
    align-items: center;
    gap: 12px;
}

.node-orientation-live-pill {
    padding: 6px 9px;
    border-radius: 999px;
    background: rgba(100, 116, 139, .13);
    border: 1px solid rgba(100, 116, 139, .35);
    color: #94a3b8;
    font: 700 9px/1 "JetBrains Mono", monospace;
    letter-spacing: .07em;
}

.node-orientation-live-pill.online {
    color: #34d399;
    background: rgba(16, 185, 129, .10);
    border-color: rgba(16, 185, 129, .35);
}

.node-orientation-close {
    width: 36px;
    height: 36px;
    border-radius: 8px;
    border: 1px solid var(--border-subtle, #2a303a);
    background: var(--bg-input, #171d26);
    color: var(--text-primary, #f2f4f7);
    font-size: 25px;
    line-height: 1;
    cursor: pointer;
}

.node-orientation-body {
    flex: 1;
    min-height: 0;
    display: grid;
    grid-template-columns: minmax(0, 1.6fr) minmax(330px, .9fr);
}

.node-orientation-view-column {
    min-width: 0;
    min-height: 0;
    display: flex;
    flex-direction: column;
    padding: 14px;
    border-right: 1px solid var(--border-subtle, #2a303a);
}

.node-orientation-viewport {
    position: relative;
    flex: 1;
    min-height: 360px;
    overflow: hidden;
    border: 1px solid var(--border-subtle, #2a303a);
    border-radius: 10px;
    background: radial-gradient(circle at 50% 42%, rgba(69, 86, 111, .16), transparent 38%),
                linear-gradient(180deg, rgba(14, 21, 31, .92), rgba(8, 13, 20, .98));
}

.node-orientation-viewport canvas {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
}

.node-orientation-loading {
    position: absolute;
    z-index: 3;
    inset: 0;
    display: grid;
    place-items: center;
    padding: 20px;
    color: var(--text-muted, #9aa3af);
    font: 600 11px/1.4 "JetBrains Mono", monospace;
    text-align: center;
    pointer-events: none;
}
.node-orientation-loading.hidden { display: none; }

.node-orientation-axis-label {
    position: absolute;
    z-index: 2;
    bottom: 10px;
    padding: 3px 6px;
    border-radius: 4px;
    background: rgba(0, 0, 0, .46);
    font: 700 8px/1.2 "JetBrains Mono", monospace;
    letter-spacing: .05em;
    pointer-events: none;
}
.node-orientation-axis-label.axis-x { left: 10px; color: #fb7185; }
.node-orientation-axis-label.axis-y { left: 72px; color: #4ade80; }
.node-orientation-axis-label.axis-z { left: 142px; color: #60a5fa; }

.node-orientation-angle-strip {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 8px;
    margin-top: 10px;
}

.node-orientation-angle-strip > div {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 8px;
    padding: 9px 11px;
    border: 1px solid var(--border-subtle, #2a303a);
    border-radius: 7px;
    background: var(--bg-input, #171d26);
}

.node-orientation-angle-strip span {
    font: 700 8px/1 "JetBrains Mono", monospace;
    letter-spacing: .07em;
    color: var(--text-muted, #9aa3af);
}
.node-orientation-angle-strip strong {
    font: 700 14px/1 "JetBrains Mono", monospace;
    color: var(--text-primary, #f2f4f7);
}

.node-orientation-hint {
    padding-top: 9px;
    text-align: center;
    font-size: 9px;
    color: var(--text-muted, #9aa3af);
}

.node-orientation-telemetry {
    min-width: 0;
    overflow: auto;
    padding: 18px;
}
.node-orientation-section-title.secondary { margin-top: 18px; }

.node-orientation-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 8px;
    margin-top: 9px;
}
.node-orientation-grid.compact { grid-template-columns: repeat(2, minmax(0, 1fr)); }

.node-orientation-metric {
    min-width: 0;
    padding: 10px;
    border: 1px solid var(--border-subtle, #2a303a);
    border-radius: 7px;
    background: var(--bg-input, #171d26);
}

.node-orientation-metric > span {
    display: block;
    overflow: hidden;
    margin-bottom: 6px;
    font: 600 8px/1.25 "JetBrains Mono", monospace;
    letter-spacing: .05em;
    text-transform: uppercase;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--text-muted, #9aa3af);
}

.node-orientation-metric > strong {
    display: block;
    overflow-wrap: anywhere;
    font: 700 13px/1.2 "JetBrains Mono", monospace;
    color: var(--text-primary, #f2f4f7);
}

.node-orientation-metric strong small {
    margin-left: 3px;
    font-size: 9px;
    font-weight: 500;
    color: var(--text-muted, #9aa3af);
}

.node-orientation-metric.danger {
    border-color: rgba(239, 68, 68, .45);
    background: rgba(239, 68, 68, .08);
}
.node-orientation-metric.danger strong { color: #fb7185; }

@media (max-width: 980px) {
    .node-orientation-dialog { inset: 2vh 2vw; }
    .node-orientation-body { grid-template-columns: 1fr; overflow: auto; }
    .node-orientation-view-column {
        min-height: 550px;
        border-right: 0;
        border-bottom: 1px solid var(--border-subtle, #2a303a);
    }
    .node-orientation-telemetry { overflow: visible; }
}

@media (max-width: 620px) {
    .node-orientation-dialog { inset: 0; border-radius: 0; }
    .node-orientation-grid,
    .node-orientation-grid.compact { grid-template-columns: 1fr 1fr; }
    .node-orientation-header { padding: 14px; }
    .node-orientation-view-column { min-height: 480px; padding: 10px; }
    .node-orientation-viewport { min-height: 320px; }
}
'''

js_path = ROOT / "static" / "js" / "node-orientation.js"
css_path = ROOT / "static" / "css" / "node-orientation.css"
js_path.parent.mkdir(parents=True, exist_ok=True)
css_path.parent.mkdir(parents=True, exist_ok=True)

js_path.write_text(orientation_js)
css_path.write_text(orientation_css)
print(f"[CREATED]   {js_path}")
print(f"[CREATED]   {css_path}")

print()
print("Stage 2 installed.")
print()
print("Next:")
print("  1. python3 -m py_compile database.py serial_bridge.py telemetry.py")
print("  2. git diff -- database.py serial_bridge.py telemetry.py static/js/app.js templates/index.html")
print("  3. git status --short")
print("  4. Restart Flask so init_db() adds the new SQLite columns.")
print("  5. Open REAL HARDWARE mode and click UG-01, UG-02, or LD-01.")
print()
print("Expected:")
print("  - UG-01 + UG-02 load static/stl-files/UG-Node.stl")
print("  - LD-01 loads static/stl-files/LD-Node.stl")
print("  - Roll/pitch/yaw continuously drive the model")
print("  - Complete MPU + node-specific sensor data appear in the modal")
