"""Prepare a portable web replay from the existing external Blender twin state log."""
import json,sys
from pathlib import Path
source=Path(sys.argv[1]);root=Path(__file__).resolve().parents[1]
registry=json.loads((source/'Object_Registry.json').read_text())
replay=json.loads((source/'Simulation_Replay.json').read_text());frames=[]
for record in replay:
    state=record['packet']['state'];nodes=[]
    for key in ('G001','G002','C001'):
        n=dict(state['nodes'][key]);position=registry['nodes'][key]['position'];n['position']=[position[0],0,-position[1]]
        n.update(node_type='crack' if key=='C001' else 'UnderGround',timestamp=None,age_seconds=0)
        nodes.append(n)
    zone=state['zone']
    frames.append(dict(seconds=record['seconds'],state=dict(version=1,source='RECORDED_DEMO',input_mode='SIMULATION',system_state=state['system_state'],nodes=nodes,
        zones=[dict(zone_id='ZONE_A',node_ids=zone['affected_nodes'],risk_score=zone['risk_score'],risk_level=zone['risk_level'],correlation=zone['correlation'])] if zone['affected_nodes'] else [],
        deformation=state['deformation'],alert=dict(sirens_active=state['alert']['active'],status='RED_ALERT' if state['alert']['active'] else 'NORMAL'),
        sirens=[dict(siren_id=key,name=key,state=value['state']) for key,value in state['sirens'].items()])))
(root/'static/models/terraveil/demo.json').write_text(json.dumps(frames,separators=(',',':')))
print('Prepared',len(frames),'shared-pipeline demo states')
