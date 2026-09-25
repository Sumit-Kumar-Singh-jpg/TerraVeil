'''Durable per-demo node membership, baseline eligibility, event history and latches.'''
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
