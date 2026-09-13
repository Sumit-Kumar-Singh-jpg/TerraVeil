import copy,gzip,json,sys,unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from digital_twin import build_twin_state
NOW=datetime(2026,9,13,tzinfo=timezone.utc)
NODES=[dict(node_id='G-001',node_type='UnderGround',latitude=23.657,longitude=86.452),dict(node_id='C-015',node_type='crack',latitude=23.6571,longitude=86.4521)]
def reading(key,risk='HIGH',**kwargs):return dict(node_id=key,timestamp=NOW.isoformat(),risk_level=risk,risk_score=80,battery=90,**kwargs)
ALERT=dict(status='NORMAL',sirens_active=False,siren_zones=[dict(id='SRN-01',name='Surface',location='Mine',status='OFF')])

class TwinAdapterTests(unittest.TestCase):
    def test_live_fields_and_cluster(self):
        state=build_twin_state(NODES,[reading('G-001',tilt_x=.42),reading('C-015',displacement_mm=3)],ALERT,NOW)
        self.assertEqual(state['nodes'][0]['tilt_x'],.42);self.assertEqual(state['nodes'][1]['displacement_mm'],3)
        self.assertEqual(state['system_state'],'HIGH_RISK');self.assertEqual(len(state['zones']),1)
    def test_single_node_not_confirmed_subsidence(self):
        state=build_twin_state(NODES,[reading('G-001',tilt_x=5)],ALERT,NOW)
        self.assertEqual(state['system_state'],'EARLY_ANOMALY');self.assertEqual(state['deformation']['subsidence_level'],0)
        self.assertFalse(state['alert']['sirens_active'])
    def test_no_alert_mutation(self):
        before=copy.deepcopy(ALERT)
        state=build_twin_state(NODES,[reading('G-001',tilt_x=80),reading('C-015',displacement_mm=100)],ALERT,NOW)
        self.assertEqual(ALERT,before);self.assertFalse(state['alert']['sirens_active'])
    def test_host_alert_is_authority(self):
        alert=copy.deepcopy(ALERT);alert.update(status='RED_ALERT',sirens_active=True);alert['siren_zones'][0]['status']='ON'
        state=build_twin_state(NODES,[],alert,NOW)
        self.assertEqual(state['system_state'],'EMERGENCY');self.assertEqual(state['sirens'][0]['state'],'ACTIVE')
    def test_stale_and_missing_data(self):
        rd=reading('G-001');rd['timestamp']=(NOW-timedelta(seconds=16)).isoformat()
        state=build_twin_state(NODES,[rd],ALERT,NOW)
        self.assertEqual([n['status'] for n in state['nodes']],['STALE','NO_DATA']);self.assertFalse(state['zones'])
    def test_nonfinite_normalized(self):
        state=build_twin_state(NODES,[reading('G-001',tilt_x=float('nan'),vibration=float('inf'))],ALERT,NOW)
        json.dumps(state,allow_nan=False);self.assertEqual(state['nodes'][0]['tilt_x'],0)
    def test_spatially_separated_nodes_not_correlated(self):
        nodes=copy.deepcopy(NODES);nodes[1]['latitude']+=.05
        state=build_twin_state(nodes,[reading('G-001'),reading('C-015')],ALERT,NOW)
        self.assertFalse(state['zones'])
    def test_duplicate_latest_reading(self):
        state=build_twin_state(NODES,[reading('G-001',id=1,tilt_x=.1),reading('G-001',id=2,tilt_x=.8)],ALERT,NOW)
        self.assertEqual(state['nodes'][0]['tilt_x'],.8)

class TwinRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app import app
        cls.client=app.test_client()
    def test_index_contains_view(self):
        response=self.client.get('/');self.assertIn(b'id="view-twin"',response.data);self.assertIn(b'type="importmap"',response.data)
    def test_state_readonly_endpoint(self):
        with patch('app.get_nodes',return_value=NODES),patch('app.get_latest_readings',return_value=[]):
            response=self.client.get('/api/twin/state');self.assertEqual(response.status_code,200);self.assertEqual(response.headers['Cache-Control'],'no-store');self.assertEqual(len(response.json['nodes']),2)
        self.assertEqual(self.client.post('/api/twin/state').status_code,405)
    def test_compressed_model_and_unknown_asset(self):
        response=self.client.get('/api/twin/assets/mine.glb',headers={'Accept-Encoding':'gzip'})
        self.assertEqual(response.headers['Content-Encoding'],'gzip');self.assertEqual(gzip.decompress(response.data)[:4],b'glTF')
        self.assertEqual(self.client.get('/api/twin/assets/secret.txt').status_code,404)
    def test_replay_contains_all_states(self):
        response=self.client.get('/static/models/terraveil/demo.json');data=json.loads(response.data)
        self.assertEqual(len(data),177);self.assertEqual({f['state']['system_state'] for f in data},{'NORMAL','EARLY_ANOMALY','CORRELATED_DEFORMATION','HIGH_RISK','EMERGENCY'})

if __name__=='__main__':unittest.main(verbosity=2)
