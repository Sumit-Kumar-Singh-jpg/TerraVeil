#!/usr/bin/env python3
"""
TerraVeil live hardware fix.

Run from the TerraVeil repository root:
    python3 terraveil_live_fix.py

This patches:
  - telemetry.py: robust live sequence-reset/session recovery + MPU angle normalization.
  - static/js/node-orientation.js: faster frame-rate-independent 3D orientation rendering.

Backups are created next to the edited files.
"""

from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path.cwd()
TELEMETRY = ROOT / "telemetry.py"
VIEWER = ROOT / "static" / "js" / "node-orientation.js"

STAMP = datetime.now().strftime("%Y%m%d-%H%M%S")


def backup(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Missing file: {path}")
    shutil.copy2(path, path.with_suffix(path.suffix + f".bak-{STAMP}"))


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected exactly one match for {label}, found {count}")
    return text.replace(old, new, 1)


def patch_telemetry() -> None:
    backup(TELEMETRY)
    text = TELEMETRY.read_text(encoding="utf-8")

    helper = '''\n\ndef _wrap_degrees(value):\n    """Normalize any valid calibrated MPU angle into [-180, 180].\n\n    The ESP32 firmware subtracts calibration offsets. Near the +/-180 boundary\n    that can temporarily produce values outside the conventional range even\n    though the physical attitude is valid. Store a normalized angle instead of\n    rejecting the packet and accidentally making a live node appear OFFLINE.\n    """\n    return ((float(value) + 180.0) % 360.0) - 180.0\n'''
    if "def _wrap_degrees" not in text:
        text = replace_once(
            text,
            '''def _as_aware(stamp):\n    value = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))\n    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)\n''',
            '''def _as_aware(stamp):\n    value = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))\n    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)\n''' + helper,
            "insert _wrap_degrees",
        )

    new_reset_function = '''def _is_fresh_live_counter_reset(payload, previous, now):\n    """Return True for a likely physical ESP32 node reboot.\n\n    A common failure mode during demos is:\n      1. UG-02 was previously stored at a high sequence number.\n      2. The node reboots and starts again from sequence 1.\n      3. Flask misses the first few startup packets.\n      4. Sequence 4/5/6 is then treated as historical, so the dashboard keeps\n         rendering the node OFFLINE even though PlatformIO shows it is alive.\n\n    Buffered TELEMETRY replay is deliberately excluded. Only fresh live serial\n    transports may rotate the node session automatically.\n    """\n    if not previous:\n        return False\n\n    new_sequence = payload.get("sequence")\n    old_sequence = previous.get("sequence")\n\n    if type(new_sequence) is not int or type(old_sequence) is not int:\n        return False\n\n    if new_sequence >= old_sequence:\n        return False\n\n    if payload.get("_transport") not in ("HUB_DATA", "LEGACY"):\n        return False\n\n    queue_age_ms = payload.get("queue_age_ms", 0)\n    if type(queue_age_ms) is not int or queue_age_ms < 0:\n        return False\n    if queue_age_ms > 5000:\n        return False\n\n    # Strong evidence: the last applied live reading has already gone stale.\n    # Do not require sequence <= 3 here, because Flask may miss the first few\n    # startup packets while the browser/server is restarting.\n    if _previous_is_stale(previous, now):\n        return True\n\n    # Also accept an immediate small startup-range reset while the previous\n    # session had clearly progressed. The window is configurable for testing.\n    startup_window = max(3, int(os.environ.get("TERRAVEIL_REBOOT_SEQUENCE_WINDOW", "25")))\n    return new_sequence <= startup_window and old_sequence >= 10\n'''

    text, count = re.subn(
        r"def _is_fresh_live_counter_reset\(payload, previous, now\):.*?\n\ndef ingest\(",
        new_reset_function + "\n\ndef ingest(",
        text,
        count=1,
        flags=re.S,
    )
    if count != 1:
        raise RuntimeError("Could not replace _is_fresh_live_counter_reset")

    text = replace_once(
        text,
        '''        "roll": (-180, 180),\n        "pitch": (-180, 180),\n        "yaw": (-180, 180),''',
        '''        "roll": (-720, 720),\n        "pitch": (-720, 720),\n        "yaw": (-720, 720),''',
        "angle limit expansion",
    )

    old_assignment = '''        if key in payload:\n            row[key] = number(payload, key, low, high)'''
    new_assignment = '''        if key in payload:\n            value = number(payload, key, low, high)\n            row[key] = _wrap_degrees(value) if key in ("roll", "pitch", "yaw") else value'''
    text = replace_once(text, old_assignment, new_assignment, "normalize MPU fields")

    text = replace_once(
        text,
        '''    row["roll"] = number(payload, "roll", -180, 180)\n    row["pitch"] = number(payload, "pitch", -180, 180)''',
        '''    row["roll"] = _wrap_degrees(number(payload, "roll", -720, 720))\n    row["pitch"] = _wrap_degrees(number(payload, "pitch", -720, 720))''',
        "normalize UG roll/pitch",
    )

    compile(text, str(TELEMETRY), "exec")
    TELEMETRY.write_text(text, encoding="utf-8")


VIEWER_JS = r'''import * as THREE from 'three';
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

// Most mechanical CAD/STL exports are Z-up. Three.js scenes are Y-up.
// Rotating -90 deg around X maps CAD +Z to Three.js +Y.
const CAD_Z_UP_TO_THREE_Y_UP = new THREE.Quaternion().setFromEuler(
    new THREE.Euler(-Math.PI / 2, 0, 0, 'XYZ')
);

// Higher value = faster visual convergence to the latest live packet.
const ORIENTATION_RESPONSE_HZ = 16;
const MAX_FRAME_DT_SECONDS = 0.08;

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
    viewerOpen: false,
    lastFrameTime: 0,
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
    geometry.computeVertexNormals();
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

    const renderer = new THREE.WebGLRenderer({antialias: true, alpha: true, powerPreference: 'high-performance'});
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
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
}

function resizeScene() {
    if (!state.renderer || !state.viewport) return;
    const width = Math.max(1, state.viewport.clientWidth);
    const height = Math.max(1, state.viewport.clientHeight);

    state.renderer.setSize(width, height, false);
    state.camera.aspect = width / height;
    state.camera.updateProjectionMatrix();
}

function startAnimationLoop() {
    if (state.animationFrame) return;
    state.lastFrameTime = performance.now();
    state.animationFrame = requestAnimationFrame(animate);
}

function animate(timestamp) {
    state.animationFrame = null;
    if (!state.viewerOpen) return;

    const dt = Math.min(MAX_FRAME_DT_SECONDS, Math.max(0.001, (timestamp - state.lastFrameTime) / 1000));
    state.lastFrameTime = timestamp;
    const alpha = 1 - Math.exp(-ORIENTATION_RESPONSE_HZ * dt);

    if (state.modelRoot) {
        state.displayQuaternion.slerp(state.targetQuaternion, alpha);
        state.modelRoot.quaternion.copy(state.displayQuaternion);
    }

    state.controls?.update();
    state.renderer?.render(state.scene, state.camera);
    state.animationFrame = requestAnimationFrame(animate);
}

function setTargetOrientation(reading) {
    if (!reading) return;

    const roll = finite(reading.roll) ? Number(reading.roll) : 0;
    const pitch = finite(reading.pitch) ? Number(reading.pitch) : 0;
    const yaw = finite(reading.yaw) ? Number(reading.yaw) : 0;

    // Firmware packet uses roll=X, pitch=Y, yaw=Z in degrees.
    // Yaw is relative because MPU6050 has no magnetometer.
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

    state.targetQuaternion
        .copy(liveQuaternion)
        .multiply(state.correctionQuaternion)
        .multiply(CAD_Z_UP_TO_THREE_Y_UP);
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
                if (child.geometry?.dispose) child.geometry.dispose();
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
        const edgesGeometry = new THREE.EdgesGeometry(geometry, 28);
        const edges = new THREE.LineSegments(
            edgesGeometry,
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
        state.renderer?.render(state.scene, state.camera);
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

    state.viewerOpen = true;
    state.modal.classList.add('open');
    state.modal.setAttribute('aria-hidden', 'false');
    document.body.classList.add('node-orientation-modal-open');

    renderTelemetry();
    await swapModel(nodeId);
    requestAnimationFrame(resizeScene);
    startAnimationLoop();
}

function closeModal() {
    if (!state.modal) return;
    state.modal.classList.remove('open');
    state.modal.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('node-orientation-modal-open');
    state.viewerOpen = false;
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


def patch_viewer() -> None:
    backup(VIEWER)
    VIEWER.write_text(VIEWER_JS, encoding="utf-8")


def main() -> None:
    if not TELEMETRY.exists() or not VIEWER.exists():
        raise SystemExit(
            "Run this script from the TerraVeil repository root. "
            "Expected telemetry.py and static/js/node-orientation.js."
        )

    patch_telemetry()
    patch_viewer()

    print("✅ TerraVeil live hardware patch applied.")
    print(f"   Backups created with suffix: .bak-{STAMP}")
    print("   Restart Flask: python3 app.py")
    print("   Hard-refresh browser: Ctrl+Shift+R")


if __name__ == "__main__":
    main()
