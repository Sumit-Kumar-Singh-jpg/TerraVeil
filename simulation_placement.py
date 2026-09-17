"""Persist simulation placement independently of the three hardware nodes."""
import json
import math
import re
from flask import Blueprint, request, jsonify
from database import get_connection
placement_api=Blueprint('simulation_placement',__name__)

@placement_api.before_request
def body_limit():
    if request.path=='/api/simulation/placement': request.max_content_length=512*1024

@placement_api.get('/api/simulation/placement')
def current_plan():
    with get_connection() as conn:
        row=conn.execute("SELECT value FROM settings WHERE key='simulation_placement'").fetchone()
    response=jsonify(plan=json.loads(row[0]) if row else None)
    response.headers['Cache-Control']='no-store'
    return response

@placement_api.post('/api/simulation/placement')
def save_plan():
    data=request.get_json(silent=True)
    try:
        if not isinstance(data,dict) or not isinstance(data.get('nodes'),list) or len(data['nodes'])>200:
            raise ValueError('Provide a placement plan with at most 200 nodes')
        ids=set();rows=[]
        for node in data['nodes']:
            if not isinstance(node,dict): raise ValueError('Invalid placement record')
            key=node.get('id');lat=node.get('latitude');lon=node.get('longitude')
            if not isinstance(key,str) or not re.fullmatch(r'NODE-[0-9]{3,}',key) or key in ids:
                raise ValueError('Use unique Node Placement IDs (NODE-001 etc.)')
            ids.add(key)
            for v,bound in ((lat,90),(lon,180)):
                if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or abs(v)>bound:
                    raise ValueError('Invalid placement coordinates')
            kind={'underground_monitoring':'UnderGround','UnderGround':'UnderGround','crack':'crack'}.get(node.get('node_type'))
            if not kind: raise ValueError('Invalid node type')
            rows.append((key,kind,lat,lon))
        for key in ('config','stats'):
            if key in data and not isinstance(data[key],dict): raise ValueError('Invalid plan metadata')
        encoded=json.dumps(data,allow_nan=False)
        with get_connection() as conn:
            conn.execute('UPDATE nodes SET active=0')
            conn.executemany("""INSERT INTO nodes(node_id,node_type,latitude,longitude,active) VALUES(?,?,?,?,1)
                ON CONFLICT(node_id) DO UPDATE SET node_type=excluded.node_type,latitude=excluded.latitude,
                longitude=excluded.longitude,active=1""",rows)
            conn.execute("INSERT INTO settings(key,value) VALUES('simulation_placement',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(encoded,))
        return jsonify(success=True,count=len(rows))
    except (ValueError,TypeError) as error:
        return jsonify(error=str(error)),400
