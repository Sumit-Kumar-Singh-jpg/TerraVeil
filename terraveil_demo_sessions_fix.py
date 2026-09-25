#!/usr/bin/env python3
"""Apply session-scoped TerraVeil Stage-3 demo and alert UI changes.
Run from the TerraVeil repository root: python3 terraveil_demo_sessions_fix.py
Creates timestamped backups, verifies expected source anchors, then writes changes.
Does not modify telemetry.py, stage3_risk.py or node firmware.
"""
from __future__ import annotations
import ast
from pathlib import Path
from datetime import datetime
import shutil
import sys

ROOT = Path.cwd()
STAMP = datetime.now().strftime('%Y%m%d-%H%M%S')

def replace_once(s, old, new, name):
    n = s.count(old)
    if n != 1:
        raise RuntimeError(f'{name}: expected one source anchor, found {n}. No files changed.')
    return s.replace(old, new, 1)

def splice(s, begin, end, replacement, name):
    if s.count(begin) != 1 or s.count(end) != 1:
        raise RuntimeError(f'{name}: source changed; cannot find unique block. No files changed.')
    a = s.index(begin); b = s.index(end, a)
    return s[:a] + replacement + '\n\n' + s[b:]

DEMO_SESSION_PY = r"""'''Durable per-demo node membership, baseline eligibility, event history and latches.'''
import json
import math
from datetime import datetime, timezone
from uuid import uuid4
from database import get_connection, get_nodes, get_latest_readings
from stage3_risk import BASELINE_SAMPLES, UG_TILT_TRIGGER_DEG, LD_DISPLACEMENT_TRIGGER_MM

FRESH_SECONDS = 8.0  # Normal three-node hub poll period is about one second.
MAX_QUEUE_AGE_MS = 3000
UG_BASELINE_SPREAD_DEG = 0.75
LD_BASELINE_SPREAD_MM = 0.45

def utcnow():
    return datetime.now(timezone.utc)

def _age(stamp):
    try:
        ts = datetime.fromisoformat(str(stamp).replace('Z', '+00:00'))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (utcnow() - ts).total_seconds()
    except (ValueError, TypeError):
        return float('inf')

def fresh(row):
    return (row is not None and row.get('online', True)
            and -2 <= _age(row.get('last_seen') or row.get('timestamp')) <= FRESH_SECONDS
            and -2 <= _age(row.get('server_timestamp')) <= FRESH_SECONDS
            and 0 <= int(row.get('queue_age_ms') or 0) <= MAX_QUEUE_AGE_MS)

def init_demo_db():
    with get_connection() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS demo_sessions (
            demo_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, ended_at TEXT,
            node_sessions TEXT NOT NULL, alarm_fired INTEGER NOT NULL DEFAULT 0,
            geophone_fired INTEGER NOT NULL DEFAULT 0, alarm_node TEXT)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS demo_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, demo_id TEXT NOT NULL,
            timestamp TEXT NOT NULL, kind TEXT NOT NULL, node_id TEXT,
            message TEXT NOT NULL, risk_level TEXT, risk_score REAL,
            FOREIGN KEY(demo_id) REFERENCES demo_sessions(demo_id))''')
        conn.execute('''CREATE UNIQUE INDEX IF NOT EXISTS demo_risk_dedupe
            ON demo_events(demo_id,node_id,kind) WHERE kind LIKE 'RISK_%' ''')
        interrupted = [r[0] for r in conn.execute('SELECT demo_id FROM demo_sessions WHERE ended_at IS NULL')]
        for demo_id in interrupted:
            conn.execute('UPDATE demo_sessions SET ended_at=? WHERE demo_id=?',
                         (utcnow().isoformat(), demo_id))
            _event(conn, demo_id, 'END', 'Flask restarted; previous demo safely disarmed.')

def _decode(row):
    if row is None:
        return None
    row = dict(row)
    row['node_sessions'] = json.loads(row['node_sessions'])
    row['node_ids'] = list(row['node_sessions'])
    return row

def latest_demo():
    with get_connection() as conn:
        return _decode(conn.execute('SELECT * FROM demo_sessions ORDER BY started_at DESC LIMIT 1').fetchone())

def active_demo():
    demo = latest_demo()
    return demo if demo and demo['ended_at'] is None else None

def _event(conn, demo_id, kind, message, node_id=None, risk_level=None, risk_score=None):
    conn.execute('''INSERT OR IGNORE INTO demo_events
        (demo_id,timestamp,kind,node_id,message,risk_level,risk_score)
        VALUES (?,?,?,?,?,?,?)''', (demo_id, utcnow().isoformat(), kind,
                                  node_id, message, risk_level, risk_score))

def start_demo():
    '''Snapshot currently streaming nodes. Rotate ONLY their monitoring sessions.'''
    registry = {n['node_id']: n for n in get_nodes('REAL')}
    latest = {r['node_id']: r for r in get_latest_readings('REAL')}
    selected = [node_id for node_id in registry if fresh(latest.get(node_id))]
    if not selected:
        raise ValueError('No fresh hardware node: verify HOST-01 and wait for an incoming packet.')
    session_map = {node_id: uuid4().hex for node_id in selected}
    demo_id = uuid4().hex
    now = utcnow().isoformat()
    with get_connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        previous = [r[0] for r in conn.execute('SELECT demo_id FROM demo_sessions WHERE ended_at IS NULL')]
        conn.execute('UPDATE demo_sessions SET ended_at=? WHERE ended_at IS NULL', (now,))
        for previous_id in previous:
            _event(conn, previous_id, 'END', 'Previous demo closed by a new SET ZERO.')
        for node_id, session_id in session_map.items():
            conn.execute('UPDATE hardware_nodes SET session_id=?, relative_baseline=1 WHERE node_id=?',
                         (session_id, node_id))
        conn.execute('''INSERT INTO demo_sessions
            (demo_id, started_at, node_sessions) VALUES (?,?,?)''',
            (demo_id, now, json.dumps(session_map, sort_keys=True)))
        _event(conn, demo_id, 'START',
               'SET ZERO: new baseline capture started for ' + ', '.join(selected))
    return active_demo()

def end_demo(demo_id, message='Demo ended; alarm disarmed.'):
    with get_connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        cursor = conn.execute('UPDATE demo_sessions SET ended_at=? WHERE demo_id=? AND ended_at IS NULL',
                              (utcnow().isoformat(), demo_id))
        if cursor.rowcount:
            _event(conn, demo_id, 'END', message)

def _stable_baseline(node_id, session_id, kind):
    with get_connection() as conn:
        rows = [dict(r) for r in conn.execute('''
            SELECT roll,pitch,displacement_mm,gx,gy,queue_age_ms
            FROM readings WHERE data_source='REAL' AND node_id=? AND session_id=?
            AND processed=1 ORDER BY id ASC LIMIT ?''',
            (node_id, session_id, BASELINE_SAMPLES))]
    if len(rows) < BASELINE_SAMPLES:
        return False
    if any(int(r['queue_age_ms'] or 0) > MAX_QUEUE_AGE_MS for r in rows):
        return False
    if kind == 'UnderGround':
        for axis in ('roll', 'pitch'):
            if any(r[axis] is None for r in rows):
                return False
            reference = float(rows[0][axis])
            deltas = [((float(r[axis]) - reference + 180) % 360) - 180 for r in rows]
            if max(deltas) - min(deltas) > UG_BASELINE_SPREAD_DEG:
                return False
        # Gyro values are optional in legacy packets. Where supplied, reject motion.
        for r in rows:
            if any(abs(float(r[axis])) > 4 for axis in ('gx','gy') if r[axis] is not None):
                return False
    else:
        vals = [r['displacement_mm'] for r in rows]
        if any(v is None for v in vals) or max(vals) - min(vals) > LD_BASELINE_SPREAD_MM:
            return False
    return True

def eligible_node(demo, node_id, reading=None):
    if not demo or demo['ended_at'] is not None or node_id not in demo['node_sessions']:
        return False
    if reading is None:
        reading = next((r for r in get_latest_readings('REAL') if r['node_id'] == node_id), None)
    return bool(reading and reading.get('session_id') == demo['node_sessions'][node_id]
                and reading.get('processed') == 1
                and reading.get('baseline_ready') == 1 and fresh(reading)
                and _stable_baseline(node_id, demo['node_sessions'][node_id],
                                     'UnderGround' if node_id.startswith('UG-') else 'Crack'))

def progress(demo=None):
    demo = demo if demo is not None else active_demo()
    if not demo:
        return {'ready': False, 'nodes': [], 'text': 'DISARMED · press SET ZERO with at least one live node'}
    latest = {r['node_id']: r for r in get_latest_readings('REAL')}
    nodes = []
    for node_id, session_id in demo['node_sessions'].items():
        r = latest.get(node_id)
        matching = bool(r and r.get('session_id') == session_id)
        count = int(r.get('baseline_samples') or 0) if matching else 0
        stable = _stable_baseline(node_id, session_id, 'UnderGround' if node_id.startswith('UG-') else 'Crack') if count >= BASELINE_SAMPLES else None
        ready = eligible_node(demo, node_id, r)
        nodes.append({'node_id': node_id, 'count': count, 'required': BASELINE_SAMPLES,
                      'online': bool(r and matching and fresh(r)),
                      'stable': stable, 'ready': ready})
    ready = any(n['ready'] for n in nodes)
    details = ' · '.join(f"{n['node_id']} {n['count']}/{n['required']}" +
                         (' READY' if n['ready'] else ' OFFLINE' if not n['online']
                          else ' UNSTABLE: SET ZERO WHILE STILL' if n['stable'] is False else '')
                         for n in nodes)
    return {'ready': ready, 'nodes': nodes,
            'text': ('ARMED · ' if ready else 'CALIBRATING · ') + details}

def note_risk(demo, row):
    if not demo or demo['ended_at'] is not None or row.get('risk_level') not in ('MEDIUM','HIGH','CRITICAL'):
        return
    node_id = row['node_id']
    with get_connection() as conn:
        _event(conn, demo['demo_id'], 'RISK_' + row['risk_level'],
               f"{node_id}: {row.get('evidence') or 'physical risk observed'}",
               node_id, row['risk_level'], row.get('risk_score'))

def claim_once(demo_id, column, kind, message, node_id=None):
    if column not in ('alarm_fired', 'geophone_fired'):
        raise ValueError('Unknown latch')
    with get_connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        cur = conn.execute(f'''UPDATE demo_sessions SET {column}=1
            {', alarm_node=?' if column == 'alarm_fired' else ''}
            WHERE demo_id=? AND ended_at IS NULL AND {column}=0''',
            ((node_id, demo_id) if column == 'alarm_fired' else (demo_id,)))
        if not cur.rowcount:
            return False
        _event(conn, demo_id, kind, message, node_id)
        return True

def physical_deformation(node_id, reading):
    if not reading or not reading.get('persistent'):
        return False
    if node_id.startswith('UG-'):
        return math.hypot(float(reading.get('tilt_x') or 0),
                          float(reading.get('tilt_y') or 0)) >= UG_TILT_TRIGGER_DEG
    return abs(float(reading.get('displacement_delta_mm') or 0)) >= LD_DISPLACEMENT_TRIGGER_MM

def session_history(demo_id=None):
    with get_connection() as conn:
        demos = [_decode(r) for r in conn.execute(
            'SELECT * FROM demo_sessions ORDER BY started_at DESC LIMIT 30')]
        chosen = next((r for r in demos if r['demo_id'] == demo_id), None) if demo_id else (demos[0] if demos else None)
        if demo_id and chosen is None:
            raise ValueError('Unknown demo session')
        events = ([dict(r) for r in conn.execute('''
            SELECT timestamp,kind,node_id,message,risk_level,risk_score FROM demo_events
            WHERE demo_id=? ORDER BY id ASC LIMIT 250''', (chosen['demo_id'],))]
            if chosen else [])
    return {'sessions': [{k: d[k] for k in ('demo_id','started_at','ended_at','node_ids','alarm_fired','geophone_fired','alarm_node')}
                         for d in demos],
            'selected_demo_id': chosen['demo_id'] if chosen else None, 'events': events}
"""

HISTORY_JS = r'''(() => {
  const host = document.getElementById('demo-session-conversations');
  if (!host) return;
  let selected = null, showPrevious = false, busy = false, lastHash = '';
  function safe(v) { return String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
  function date(v) { return v ? new Date(v).toLocaleString() : '—'; }
  function button(d, current) {
    return `<button type="button" class="demo-chat-item ${selected === d.demo_id ? 'chosen' : ''}"
      data-demo-id="${safe(d.demo_id)}"><b>${current ? 'Current / latest' : 'Earlier demo'} · ${safe(date(d.started_at))}</b>
      <small>${safe(d.node_ids.join(' + '))} · ${d.ended_at ? 'ENDED' : 'ACTIVE'} · ${d.alarm_fired ? 'Physical alarm fired' : 'No physical alarm'}</small></button>`;
  }
  function paint(payload) {
    const sessions = payload.sessions || [];
    if (!sessions.length) {
      host.innerHTML = '<div class="demo-chat-empty">No demo sessions yet. Press SET ZERO when a physical node is online.</div>';
      return;
    }
    const chosen = sessions.find(d => d.demo_id === selected) || sessions[0];
    selected = chosen.demo_id;
    const older = sessions.filter(d => d.demo_id !== sessions[0].demo_id);
    const bubbles = (payload.events || []).map(ev => {
      const cls = (ev.kind === 'PHYSICAL_ALARM' || ev.kind === 'MANUAL_ALARM') ? 'alarm' : ev.kind === 'SEISMIC' ? 'seismic' : ev.kind.startsWith('RISK_') ? 'risk' : 'system';
      const title = ev.kind.startsWith('RISK_') ? `Risk observed · ${ev.risk_level}` :
        ev.kind === 'PHYSICAL_ALARM' ? 'Physical deformation · ALARM FIRED' :
        ev.kind === 'MANUAL_ALARM' ? 'Operator command · manual alarm' :
        ev.kind === 'SEISMIC' ? 'Simulated geophone precursor' : ev.kind === 'END' ? 'Session finished' : 'Session started';
      return `<article class="demo-chat-bubble ${cls}"><strong>${safe(title)}</strong>
        <p>${safe(ev.message)}</p><small>${safe(ev.node_id || '')} · ${safe(date(ev.timestamp))}</small></article>`;
    }).join('') || '<div class="demo-chat-empty">No risk or alarm events in this session.</div>';
    host.innerHTML = `<div class="demo-chat-layout"><aside class="demo-chat-sidebar">
      <h4>Demo conversations</h4>${button(sessions[0], true)}
      <button type="button" id="demo-show-previous" class="demo-previous">${showPrevious ? 'Hide' : 'Show'} earlier demos (${older.length})</button>
      ${showPrevious ? older.map(d => button(d, false)).join('') : ''}
      </aside><section class="demo-chat-thread"><div class="demo-chat-header">
      <strong>${safe(date(chosen.started_at))}</strong><small>${safe(chosen.node_ids.join(' · '))} · ${chosen.ended_at ? 'ENDED' : 'IN PROGRESS'}</small></div>
      <div class="demo-chat-messages">${bubbles}</div></section></div>`;
    host.querySelectorAll('[data-demo-id]').forEach(btn => btn.addEventListener('click', () => {
      selected = btn.dataset.demoId;
      if (selected === sessions[0].demo_id) host.dataset.latestViewed = selected;
      lastHash = ''; refresh();
    }));
    host.querySelector('#demo-show-previous')?.addEventListener('click', () => {
      showPrevious = !showPrevious; lastHash = ''; refresh();
    });
  }
  async function refresh() {
    if (busy || document.getElementById('view-alerts')?.classList.contains('hidden')) return;
    busy = true;
    try {
      const url = '/api/demo/history' + (selected ? '?demo_id=' + encodeURIComponent(selected) : '');
      const response = await fetch(url, {cache:'no-store'});
      if (!response.ok) throw new Error('Demo history unavailable');
      const data = await response.json();
      // When a new demo starts, automatically switch away from the old chat only
      // if the operator was looking at the previous latest demo.
      if (selected && data.sessions?.length && selected !== data.sessions[0].demo_id &&
          host.dataset.latestViewed === selected) {
        selected = data.sessions[0].demo_id;
        const next = await fetch('/api/demo/history?demo_id=' + encodeURIComponent(selected), {cache:'no-store'});
        if (next.ok) { const newData = await next.json(); host.dataset.latestViewed = selected;
          lastHash = JSON.stringify(newData); paint(newData); return; }
      }
      const hash = JSON.stringify(data);
      if (hash !== lastHash) { lastHash = hash; paint(data); }
      if (!host.dataset.latestViewed && data.sessions?.length) host.dataset.latestViewed = data.sessions[0].demo_id;
    } catch (error) { host.textContent = error.message; }
    finally { busy = false; }
  }
  setInterval(refresh, 2000);
  document.querySelector('[data-target="view-alerts"]')?.addEventListener('click', () => setTimeout(refresh, 0));
})();
'''

HISTORY_CSS = r'''/* Session-scoped, conversation-style risk and alert history */
#view-alerts .alerts-table-wrapper { display: none !important; }
.demo-chat-layout { display:grid; grid-template-columns:minmax(185px,240px) minmax(0,1fr); min-height:400px; gap:16px; margin-top:14px; }
.demo-chat-sidebar { display:flex; flex-direction:column; gap:8px; border-right:1px solid var(--border-color,#334155); padding-right:12px; }
.demo-chat-sidebar h4 {margin:0 0 8px;}
.demo-chat-item,.demo-previous { display:block; width:100%; text-align:left; padding:10px; border-radius:10px; border:1px solid #475569; color:var(--text-primary,#e2e8f0); background:transparent; cursor:pointer; }
.demo-chat-item.chosen {border-color:#22c55e; background:rgba(34,197,94,.09);}
.demo-chat-item b,.demo-chat-item small {display:block; overflow-wrap:anywhere;}
.demo-chat-item small {opacity:.72; margin-top:6px;}
.demo-previous {font-size:12px;}
.demo-chat-thread { min-width:0; background:rgba(100,116,139,.045); border-radius:12px; padding:10px; }
.demo-chat-header {display:flex; flex-direction:column; gap:4px; border-bottom:1px solid #475569; padding:8px 10px 12px; }
.demo-chat-header small,.demo-chat-bubble small {opacity:.72;}
.demo-chat-messages {display:flex; flex-direction:column; align-items:flex-start; gap:11px; padding:14px 7px; }
.demo-chat-bubble {max-width:min(95%,620px); padding:12px 14px; border-radius:14px 14px 14px 3px; background:rgba(100,116,139,.16); border-left:3px solid #64748b; overflow-wrap:anywhere; }
.demo-chat-bubble p {margin:7px 0; line-height:1.5; }
.demo-chat-bubble small {font-size:11px;}
.demo-chat-bubble.risk {border-color:#f59e0b;}
.demo-chat-bubble.alarm {border-color:#ef4444; background:rgba(239,68,68,.10);}
.demo-chat-bubble.seismic {border-color:#818cf8;}
.demo-chat-empty {padding:30px 12px; opacity:.7;}
@media(max-width:700px){.demo-chat-layout {grid-template-columns:1fr;}.demo-chat-sidebar {border-right:0;border-bottom:1px solid #475569;padding-bottom:12px;}}
'''

def patch_app(s):
    s=replace_once(s,'from datetime import datetime\n','from datetime import datetime\nimport threading\nfrom functools import wraps\nimport demo_session as demo_store\n', 'app imports')
    s=replace_once(s, 'init_db()\n\nfrom telemetry import', '''init_db()\ndemo_store.init_demo_db()\nDEMO_LOCK = threading.RLock()\n\ndef synchronized_demo(fn):\n    @wraps(fn)\n    def wrapper(*args, **kwargs):\n        with DEMO_LOCK:\n            return fn(*args, **kwargs)\n    return wrapper\n\nfrom telemetry import''', 'demo db initialization')
    s=replace_once(s, '    "demo_armed": False,', '    "demo_armed": False,\n    "demo_id": None,\n    "demo_node_ids": [],\n    "baseline_progress": {"ready": False, "nodes": []},', 'state extras')
    reset=r'''@app.route("/api/demo/reset", methods=["POST"])
@app.route("/api/demo/baseline", methods=["POST"])
def demo_reset():
    """New independent demo with only the nodes currently streaming fresh data."""
    if mode() != "REAL":
        return jsonify(success=False, error="SET ZERO requires REAL HARDWARE mode"), 400
    with DEMO_LOCK:
        try:
            demo = demo_store.start_demo()
        except ValueError as error:
            return jsonify(success=False, error=str(error)), 409
        alert_state.update(status="NORMAL", sirens_active=False, alert_kind=None,
                           demo_armed=True, collapse_latched=False,
                           demo_id=demo['demo_id'], demo_node_ids=demo['node_ids'],
                           baseline_progress={'ready': False, 'nodes': []},
                           geophone_event=None, triggered_at=None, acknowledged_at=None,
                           message=f"New demo started for {', '.join(demo['node_ids'])}. Keep them still during baseline capture.")
        for siren in alert_state['siren_zones']:
            siren['status'] = 'OFF'
        return jsonify(success=True, message=alert_state['message'],
                       baseline_samples_required=BASELINE_SAMPLES,
                       sessions=demo['node_sessions'], alert_state=alert_state)


def _demo_baselines_ready():
    demo = demo_store.active_demo()
    return bool(demo and alert_state.get('demo_armed')
                and demo_store.progress(demo)['ready'])
'''
    s=splice(s,'@app.route("/api/demo/reset", methods=["POST"])', '@app.route("/api/demo/seismic", methods=["POST"])',reset,'demo reset')
    s=replace_once(s, '@app.route("/api/demo/seismic", methods=["POST"])\ndef simulate_geophone_precursor():', '@app.route("/api/demo/seismic", methods=["POST"])\n@synchronized_demo\ndef simulate_geophone_precursor():', 'lock geophone route')
    s=replace_once(s, '    if not _demo_baselines_ready():\n        return jsonify(\n            success=False,\n            error="Baseline capture is not complete for all physical nodes yet."\n        ), 409', '''    if not _demo_baselines_ready():
        return jsonify(success=False,
                       error="No participating node has a fresh, stable completed baseline."), 409''', 'geophone ready')
    s=replace_once(s, '    origin_time = datetime.utcnow().isoformat()\n    geophone_event = {', '''    with DEMO_LOCK:
        demo = demo_store.active_demo()
        if not demo or not alert_state.get('demo_armed') or not _demo_baselines_ready():
            return jsonify(success=False, error='Demo not ready or already ended'), 409
        if not demo_store.claim_once(demo['demo_id'], 'geophone_fired', 'SEISMIC',
                                    'SIMULATED geophone precursor activated (not physical seismic measurements).'):
            return jsonify(success=False, error='Geophone precursor already triggered in this demo.'), 409
    origin_time = datetime.utcnow().isoformat()
    geophone_event = {''', 'geophone once latch')
    # If reset races with the precursor, don't paint a new demo with the previous demo's siren.
    s=replace_once(s, '''    alert_state.update(
        status="EARLY_WARNING",''', '''    with DEMO_LOCK:
        if demo_store.active_demo() is None or alert_state.get('demo_id') != demo['demo_id']:
            return jsonify(success=False, error='Session changed while triggering precursor'), 409
        alert_state.update(
        status="EARLY_WARNING",''','geophone state lock')
    # Indent remaining geophone alert update + siren + return block inside lock
    a=s.index('        alert_state.update(\n        status="EARLY_WARNING",')
    b=s.index('\n\n\ndef accept_telemetry(payload):',a)
    snippet=s[a:b]
    # alert_state.update currently has +8 leading spaces, its args +8 too; Python permits it.
    lead='''        alert_state.update(
        status="EARLY_WARNING",'''
    # easiest: close lock after update? update to return under lock by indent remainder 4 spaces
    marker='''    for siren in alert_state["siren_zones"]:
        siren["status"] = "ON"

    return jsonify(
        success=True,
        message="Simulated geophone precursor issued.",
        alert_state=alert_state,
    )'''
    s=replace_once(s,marker, '\n'.join('    '+ln if ln else ln for ln in marker.split('\n')), 'geophone lock tail')
    telemetry=r'''def accept_telemetry(payload):
    """Risk observations are logged per demo; one physical alarm is allowed per demo."""
    with DEMO_LOCK:
        result = ingest(payload)
        if not result.get('success') or not result.get('applied') or mode() != 'REAL':
            return result
        demo = demo_store.active_demo()
        if not demo or not alert_state.get('demo_armed') or alert_state.get('demo_id') != demo['demo_id']:
            return result
        # Fetch the EXACT row just ingested, not a last-known high-risk sample.
        with demo_store.get_connection() as conn:
            got = conn.execute('SELECT * FROM readings WHERE id=?', (result['id'],)).fetchone()
        reading = dict(got) if got else None
        if not reading or reading['data_source'] != 'REAL' or reading['processed'] != 1:
            return result
        node_id = reading['node_id']
        if reading['session_id'] != demo['node_sessions'].get(node_id):
            return result  # An offline/unselected node or an old pre-zero session.
        if not demo_store.fresh(reading):
            return result
        # Avoid logging alarms from an unstable baseline or from the first N samples.
        if not demo_store.eligible_node(demo, node_id, reading):
            return result
        demo_store.note_risk(demo, reading)
        if (isinstance(payload, dict) and payload.get('_transport') in ('HUB_DATA', 'LEGACY')
                and reading.get('risk_level') in ('HIGH','CRITICAL')
                and demo_store.physical_deformation(node_id, reading)
                and not alert_state.get('collapse_latched')):
            msg = (f'PERSISTENT PHYSICAL DEFORMATION at {node_id}: threshold-confirmed '
                   'movement from this demo baseline. Inspect the affected area.')
            if demo_store.claim_once(demo['demo_id'], 'alarm_fired', 'PHYSICAL_ALARM', msg, node_id):
                alert_state.update(status='RED_ALERT', sirens_active=True,
                                   alert_kind='PHYSICAL_DEFORMATION', collapse_latched=True,
                                   triggered_at=datetime.utcnow().isoformat(), message=msg)
                for siren in alert_state['siren_zones']:
                    siren['status'] = 'ON'
        return result
'''
    s=splice(s,'def accept_telemetry(payload):', "@app.route('/api/telemetry', methods=['POST'])",telemetry,'alarm trigger')
    s=replace_once(s, """    response = jsonify(system=network_state(source), nodes=registered, readings=readings, events=get_recent_events(source),
        twin=build_twin_state(registered, readings, alert_state))""", """    # REAL events in the dashboard are restricted to the current active demo.
    demo = demo_store.active_demo() if source == 'REAL' else None
    if source == 'REAL':
        events = []
        if demo:
            with demo_store.get_connection() as conn:
                for node_id, session_id in demo['node_sessions'].items():
                    events.extend(dict(r) for r in conn.execute('''SELECT * FROM readings
                        WHERE node_id=? AND session_id=? AND data_source='REAL' AND processed=1
                        AND (anomaly=1 OR persistent=1) ORDER BY id DESC LIMIT 10''',
                        (node_id, session_id)))
            events.sort(key=lambda r: r['id'], reverse=True)
            # Show only the newest instance of each node/severity, not each one-second sample.
            seen = set()
            distinct = []
            for row in events:
                key = (row['node_id'], row['risk_level'])
                if key not in seen:
                    seen.add(key)
                    distinct.append(row)
            events = distinct[:12]
    else:
        events = get_recent_events(source)
    response = jsonify(system=network_state(source), nodes=registered, readings=readings, events=events,
        twin=build_twin_state(registered, readings, alert_state))""",'session-filtered live events')
    s=replace_once(s,'''@app.route("/api/alert/status", methods=["GET"])
def get_alert_status():
    return jsonify(alert_state)''','''@app.route("/api/alert/status", methods=["GET"])
def get_alert_status():
    with DEMO_LOCK:
        alert_state['baseline_progress'] = demo_store.progress(demo_store.active_demo())
        return jsonify(alert_state)


@app.route('/api/demo/history', methods=['GET'])
def demo_history():
    try:
        return jsonify(demo_store.session_history(request.args.get('demo_id')))
    except ValueError as error:
        return jsonify(success=False, error=str(error)), 404


@app.route('/api/demo/end', methods=['POST'])
def end_demo():
    with DEMO_LOCK:
        demo = demo_store.active_demo()
        if demo:
            demo_store.end_demo(demo['demo_id'])
        alert_state.update(status='NORMAL', sirens_active=False, demo_armed=False,
                           alert_kind=None, collapse_latched=True, geophone_event=None,
                           message='Demo ended. Alarm disarmed; old risk events kept in session history.')
        for siren in alert_state['siren_zones']:
            siren['status'] = 'OFF'
        return jsonify(success=True, alert_state=alert_state)
''','history+end endpoint')
    s=replace_once(s, '@app.route("/api/alert/trigger", methods=["POST"])\ndef trigger_alert():', '@app.route("/api/alert/trigger", methods=["POST"])\n@synchronized_demo\ndef trigger_alert():', 'lock manual alert route')
    s=replace_once(s, '@app.route("/api/alert/reset", methods=["POST"])\ndef reset_alert():', '@app.route("/api/alert/reset", methods=["POST"])\n@synchronized_demo\ndef reset_alert():', 'lock acknowledge route')
    # Manual alert while a demo is active must consume same latch; once ACKed, session is ended.
    s=replace_once(s, '''    req_data = request.get_json(silent=True) or {}
    custom_msg = req_data.get("message") or''', '''    req_data = request.get_json(silent=True) or {}
    with DEMO_LOCK:
        demo = demo_store.active_demo()
        if demo:
            if not alert_state.get('demo_armed') or not demo_store.claim_once(
                demo['demo_id'], 'alarm_fired', 'MANUAL_ALARM',
                'Manual emergency command invoked by operator (not a physical sensor detection).'):
                return jsonify(success=False, error='Physical alarm already used or demo disarmed.'), 409
    custom_msg = req_data.get("message") or''','manual alert shares latch')
    s=replace_once(s, '''    if previous_status == "RED_ALERT":
        alert_state["demo_armed"] = False''', '''    if previous_status == "RED_ALERT":
        alert_state["demo_armed"] = False
        alert_state["collapse_latched"] = True
        demo = demo_store.active_demo()
        if demo:
            demo_store.end_demo(demo['demo_id'], 'Physical alarm acknowledged; demo disarmed.')''','red ACK ends demo')
    s=replace_once(s, """        if mode() == 'SIMULATION':
            start_simulator()
    return jsonify(network_state())""", """        if mode() == 'SIMULATION':
            with DEMO_LOCK:
                active = demo_store.active_demo()
                if active:
                    demo_store.end_demo(active['demo_id'], 'Switched to simulation: demo disarmed.')
                alert_state.update(status='NORMAL', sirens_active=False, demo_armed=False,
                                   collapse_latched=True, alert_kind=None, geophone_event=None)
                for siren in alert_state['siren_zones']:
                    siren['status'] = 'OFF'
            start_simulator()
    return jsonify(network_state())""", 'simulation disarms demo')
    return s

def patch_controls(s):
    s=replace_once(s, '''        const nodes = telemetry.nodes || [];
        const readings = telemetry.readings || {};

        if (!nodes.length) {''', '''        const nodes = (telemetry.nodes || []).filter(n =>
            (alertState?.demo_node_ids || []).includes(n.node_id));
        const readings = telemetry.readings || {};

        if (!nodes.length) {''','participating nodes only')
    s=replace_once(s, '''        const parts = nodes.map(node => {''', '''        if (alertState?.baseline_progress) {
            return {
                ready: alertState.baseline_progress.ready === true,
                disarmed: false,
                text: alertState.baseline_progress.text || 'Waiting for fresh baseline readings'
            };
        }
        const parts = nodes.map(node => {''','server verified baseline')
    s=replace_once(s,'''            alertState?.status === 'RED_ALERT';''','''            alertState?.status === 'RED_ALERT' ||
            alertState?.status === 'EARLY_WARNING' ||
            alertState?.demo_armed !== true;''','geophone gating')
    s=replace_once(s,'''    zeroButton.addEventListener('click', async () => {
        if (telemetry?.mode !== 'REAL') return;''','''    zeroButton.addEventListener('click', async () => {
        if (telemetry?.mode !== 'REAL') return;
        window.unlockTerraVeilSirenAudio?.();''','unlock audio on user gesture')
    s=replace_once(s,'''    seismicButton.addEventListener('click', async () => {
        seismicButton.disabled = true;''','''    seismicButton.addEventListener('click', async () => {
        window.unlockTerraVeilSirenAudio?.();
        seismicButton.disabled = true;''','geophone unlock')
    s=replace_once(s,'''    window.addEventListener('terraveil:telemetry', event => {''','''    const endButton = document.getElementById('btn-stage3-end');
    endButton?.addEventListener('click', async () => {
        const resp = await fetch('/api/demo/end', {method:'POST'});
        if (!resp.ok) { statusElement.textContent = 'Failed to end demo'; return; }
        alertState = (await resp.json()).alert_state;
        hideEarlyWarning(true);
        window.exitRedAlertUI?.();
        updateControls();
    });

    window.addEventListener('terraveil:telemetry', event => {''','end button')
    return s

def patch_appjs(s):
    s=replace_once(s, '''    const liveAlarmCount = Object.values(readings).filter(r => r.online && ['MEDIUM','HIGH','CRITICAL'].includes(r.risk_level)).length;
    const activeAlarms = events;''','''    // REAL dashboard reports only current-session risk observations, not old demos.
    const activeAlarms = events;
    const liveAlarmCount = currentMode === 'REAL'
        ? new Set(activeAlarms.filter(r => ['MEDIUM','HIGH','CRITICAL'].includes(r.risk_level)).map(r => r.node_id)).size
        : Object.values(readings).filter(r => r.online && ['MEDIUM','HIGH','CRITICAL'].includes(r.risk_level)).length;''','current demo badge')
    # The hidden legacy table still updates, so no need to mutate app.js elsewhere.
    s=replace_once(s, '''let sirenAudioCtx = null;''','''let sirenAudioCtx = null;
// Unlock browser audio synchronously from the user's SET ZERO gesture.
window.unlockTerraVeilSirenAudio = function () {
    try {
        if (!sirenAudioCtx) sirenAudioCtx = new (window.AudioContext || window.webkitAudioContext)();
        if (sirenAudioCtx.state === 'suspended') sirenAudioCtx.resume();
    } catch (e) { console.warn('Audio unavailable:', e); }
};''','siren unlock')
    s=replace_once(s,'''async function triggerEmergencyAlert() {''','''window.exitRedAlertUI = exitRedAlertUI;

async function triggerEmergencyAlert() {
    window.unlockTerraVeilSirenAudio?.();''','external stop + manual unlock')
    return s

def patch_html(s):
    s=replace_once(s, '''            <button type="button" id="btn-stage3-geophone"''', '''            <button type="button" id="btn-stage3-end" class="stage3-control-btn zero">
                END DEMO / SILENCE
            </button>

            <button type="button" id="btn-stage3-geophone"''','add end button')
    s=replace_once(s, '''                    <div class="alerts-table-wrapper">''', '''                    <div id="demo-session-conversations" aria-live="polite"></div>
                    <div class="alerts-table-wrapper">''','session conversation host')
    s=replace_once(s, '''    <link rel="stylesheet" href="{{ url_for('static', filename='css/stage3-demo.css') }}">''', '''    <link rel="stylesheet" href="{{ url_for('static', filename='css/stage3-demo.css') }}">
    <link rel="stylesheet" href="{{ url_for('static', filename='css/demo-history.css') }}">''','css link')
    s=replace_once(s, '''<script src="{{ url_for('static', filename='js/demo-controls.js') }}"></script>''', '''<script src="{{ url_for('static', filename='js/demo-controls.js') }}"></script>
<script src="{{ url_for('static', filename='js/demo-history.js') }}"></script>''','history js link')
    return s

def patch_stage3_css(s):
    return replace_once(s,
        '    grid-template-columns: auto auto auto minmax(280px, 1fr);',
        '    grid-template-columns: auto auto auto auto minmax(240px, 1fr);',
        'demo control-bar layout')

def apply():
    modifications = {'app.py': patch_app, 'static/js/demo-controls.js': patch_controls,
                     'static/js/app.js': patch_appjs, 'templates/index.html': patch_html,
                     'static/css/stage3-demo.css': patch_stage3_css}
    prepared = {}
    for name, transform in modifications.items():
        path = ROOT / name
        if not path.is_file(): raise RuntimeError(f'Missing {name}; run from TerraVeil root.')
        old = path.read_text(encoding='utf-8')
        # Support safe re-runs, but never clobber a previously installed patch.
        if name == 'app.py' and 'import demo_session as demo_store' in old:
            raise RuntimeError('Demo-session patch appears already installed; do not run twice.')
        new = transform(old)
        if name.endswith('.py'): ast.parse(new, filename=name)
        prepared[path] = new
    fresh = {'demo_session.py': DEMO_SESSION_PY, 'static/js/demo-history.js': HISTORY_JS,
             'static/css/demo-history.css': HISTORY_CSS}
    for name, content in fresh.items():
        path = ROOT / name
        if path.exists(): raise RuntimeError(f'{name} already exists; inspect before overwriting.')
        if name.endswith('.py'): ast.parse(content, filename=name)
        prepared[path] = content
    # All anchor/syntax validation precedes any write.
    for name in modifications:
        path=ROOT/name
        shutil.copy2(path, path.with_name(path.name + '.bak-' + STAMP))
    for path, content in prepared.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
    print('Applied TerraVeil demo-session update; original files backed up with .bak-' + STAMP)
    print('Restart Flask, hard-refresh browser, then press SET ZERO when at least one node is streaming.')

if __name__ == '__main__':
    try: apply()
    except (OSError, RuntimeError, SyntaxError) as error:
        print('Patch NOT applied: ' + str(error), file=sys.stderr)
        sys.exit(1)
