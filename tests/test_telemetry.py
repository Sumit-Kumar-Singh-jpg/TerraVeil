import concurrent.futures
import os
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import timedelta
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import database
from app import app
from telemetry import utcnow, ingest

PACKET = dict(host_id='HOST-01',zone_id='ZONE-A',node_id='UG-01',sequence=126,
              roll=54.37,pitch=-6.19,vibration=0,soil=0.0,rssi=-33,snr=9.5)

class HardwareTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database,'DB_NAME',str(Path(self.directory.name)/'test.db'))
        self.db_patch.start()
        database.init_db()
        database.set_mode('REAL')
        self.client = app.test_client()

    def tearDown(self):
        self.db_patch.stop()
        self.directory.cleanup()

    def post(self, **changes):
        return self.client.post('/api/telemetry',json=PACKET | changes)

    def test_packet_storage_and_twin(self):
        result = self.post()
        self.assertEqual(result.status_code,200)
        self.assertTrue(result.json['success'])
        rd = self.client.get('/api/readings/latest').json[0]
        self.assertEqual(rd['roll'],54.37)
        self.assertEqual(rd['tilt_x'],54.37)
        self.assertEqual(rd['snr'],9.5)
        self.assertIsNone(rd['temperature'])
        self.assertIsNone(rd['battery'])
        self.assertEqual(rd['data_source'],'REAL')
        self.assertIn('server_timestamp',rd)
        twin = self.client.get('/api/twin/state').json
        self.assertEqual(twin['input_mode'],'REAL')
        self.assertEqual(twin['nodes'][0]['tilt_x'],rd['roll'])
        self.assertIsNone(twin['nodes'][0]['battery'])
        self.assertIsNone(twin['nodes'][0]['position'])
        self.assertFalse(twin['zones'])
        self.assertEqual(rd['risk_level'],'MEDIUM')
        self.assertIn('ANOMALY DETECTED',rd['evidence'])

    def test_invalid_json_and_fields(self):
        for payload in [None, [], True, 'DATA', {}, PACKET | {'node_id':[]},
                        PACKET | {'sequence':True}, PACKET | {'sequence':1.5},
                        PACKET | {'sequence':10**500}, PACKET | {'sequence':-1}, PACKET | {'roll':float('nan')},
                        PACKET | {'roll':'54.37'}, PACKET | {'soil':4096},
                        PACKET | {'vibration':.5}, PACKET | {'host_id':'OTHER'},
                        PACKET | {'queue_age_ms':2**53-1}, PACKET | {'queue_age_ms':-1}]:
            with self.subTest(payload=payload):
                self.assertEqual(self.client.post('/api/telemetry',json=payload).status_code,400)
        self.assertEqual(self.client.post('/api/telemetry',data='{bad',content_type='application/json').status_code,400)
        self.assertEqual(database.get_history('UG-01'),[])

    def test_duplicate_and_out_of_order(self):
        self.post()
        first = database.get_latest_readings()[0]
        self.assertTrue(self.post().json['duplicate'])
        old = self.post(sequence=125,roll=2)
        self.assertFalse(old.json['applied'])
        self.assertEqual(len(database.get_history('UG-01')),2)
        latest = database.get_latest_readings()[0]
        self.assertEqual(latest['sequence'],126)
        self.assertEqual(latest['last_seen'],first['last_seen'])

    def test_concurrent_duplicates(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda _: ingest(PACKET),range(8)))
        self.assertEqual(sum(not r['duplicate'] for r in results),1)
        self.assertEqual(len(database.get_history('UG-01')),1)

    def test_timeout_and_duplicate_does_not_refresh(self):
        self.post()
        with patch('telemetry.utcnow',return_value=utcnow()+timedelta(seconds=20)):
            self.post()
            rd = database.get_latest_readings()[0]
            self.assertFalse(rd['online'])
            self.assertEqual(rd['status'],'OFFLINE')
        self.assertEqual(len(database.get_history('UG-01')),1)

    def test_buffered_packet_stays_offline(self):
        self.post(queue_age_ms=60000)
        rd=database.get_latest_readings()[0]
        self.assertFalse(rd['online'])
        self.assertEqual(rd['queue_age_ms'],60000)
        self.assertEqual(self.client.get('/api/twin/state').json['nodes'][0]['status'],'OFFLINE')

    def test_no_data_not_online(self):
        live=self.client.get('/api/live').json
        self.assertEqual(len(live['nodes']),1)
        self.assertEqual(live['readings'],[])
        self.assertEqual(live['twin']['nodes'][0]['status'],'OFFLINE')
        self.assertIsNone(live['twin']['nodes'][0]['vibration'])
        self.assertEqual(live['system']['lora_network'],'OFFLINE')

    def test_mode_isolation_and_simulator_shared_pipeline(self):
        self.post()
        node=dict(node_id='G-001',node_type='UnderGround',latitude=23.65,longitude=86.45)
        database.add_node(node)
        sim=dict(node_id='G-001',tilt_x=.02,tilt_y=.01,vibration=.1,temperature=30,humidity=60,battery=90)
        self.assertTrue(database.add_reading(sim)['skipped'])
        with patch('app.start_simulator'):
            self.assertEqual(self.client.post('/api/system',json={'mode':'SIMULATION'}).status_code,200)
        database.add_reading(sim)
        self.assertEqual(database.get_latest_readings()[0]['data_source'],'SIMULATION')
        self.assertEqual(database.get_history('UG-01'),[])
        self.assertEqual(self.client.get('/api/twin/state').json['input_mode'],'SIMULATION')
        self.client.post('/api/system',json={'mode':'REAL'})
        self.assertEqual(database.get_latest_readings()[0]['sequence'],126)
        self.assertEqual(database.get_history('G-001'),[])

    def test_invalid_mode_and_extra_sensors_not_persisted(self):
        self.assertEqual(self.client.post('/api/system',json={'mode':'fake'}).status_code,400)
        self.post(temperature=30,battery=99)
        self.assertIsNone(database.get_latest_readings()[0]['battery'])

    def test_single_node_persistence_never_confirms_subsidence(self):
        for i in range(8): self.post(sequence=i,roll=50+i,vibration=1)
        rd=database.get_latest_readings()[0]
        self.assertTrue(rd['persistent'])
        self.assertEqual(rd['risk_level'],'MEDIUM')
        self.assertFalse(self.client.get('/api/twin/state').json['zones'])

    def test_persistence_correlation_and_critical_emergency(self):
        import app as application
        with database.get_connection() as conn:
            conn.execute("UPDATE hardware_nodes SET latitude=23.65,longitude=86.45")
            conn.execute("INSERT INTO hardware_nodes(node_id,node_type,latitude,longitude,host_id,zone_id) VALUES ('UG-02','UnderGround',23.6501,86.4501,'HOST-01','ZONE-A')")
        with patch.dict(application.alert_state, {'status':'NORMAL','sirens_active':False,'siren_zones':[]}):
            for i in range(7):
                for node_id in ('UG-01','UG-02'):
                    self.post(node_id=node_id,sequence=i,roll=5+i,vibration=1)
            snapshot=self.client.get('/api/live').json
            self.assertEqual(len(snapshot['twin']['zones']),1)
            self.assertEqual(snapshot['twin']['system_state'],'EMERGENCY')
            self.assertTrue(application.alert_state['sirens_active'])

    def test_generator_cycle_passes_shared_validation(self):
        import simulator
        database.set_mode('SIMULATION')
        simulator.create_nodes()
        for step in (0, 1, 50, 200):
            simulator.simulation_step = step
            for node in simulator.nodes:
                values = (simulator.generate_ground_reading(node) if node['node_type']=='UnderGround' else simulator.generate_crack_reading(node))
                self.assertTrue(database.add_reading(dict(node_id=node['node_id'], **values))['success'])
        self.assertEqual(len(database.get_latest_readings()),20)

    def test_events_preserve_past_vibration(self):
        self.post(vibration=1)
        self.post(sequence=127,roll=0,pitch=0,vibration=0)
        events=self.client.get('/api/live').json['events']
        self.assertTrue(any(r['sequence']==126 and r['vibration']==1 for r in events))

    def test_registered_location_used(self):
        with database.get_connection() as conn:
            conn.execute("UPDATE hardware_nodes SET latitude=23.65,longitude=86.45")
        self.post()
        node=self.client.get('/api/twin/state').json['nodes'][0]
        self.assertEqual(node['latitude'],23.65)
        self.assertIsNotNone(node['position'])

    def test_replay_history_preserved_across_init(self):
        self.post()
        database.init_db()
        self.assertEqual(len(database.get_history('UG-01')),1)
        self.assertTrue(self.post().json['duplicate'])

if __name__=='__main__': unittest.main()
