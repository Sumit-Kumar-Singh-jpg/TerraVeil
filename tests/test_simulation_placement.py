import tempfile
import unittest
import math
from pathlib import Path
from unittest.mock import patch
import database
from app import app

def plan(count):
    return dict(nodes=[dict(id=f'NODE-{i+1:03}',node_type='underground_monitoring',latitude=23.75+i*.001,longitude=86.4,status='recommended') for i in range(count)],config={'budget':count},stats={})

class PlacementInventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.p=patch.object(database,'DB_NAME',str(Path(self.tmp.name)/'test.db'));self.p.start();database.init_db();self.client=app.test_client()
    def tearDown(self):self.p.stop();self.tmp.cleanup()
    def save(self,count):
        r=self.client.post('/api/simulation/placement',json=plan(count));self.assertEqual(r.status_code,200,r.json)
    def test_dynamic_count_restore_and_hardware_separation(self):
        real=database.get_nodes('REAL')
        for count in (5,12,2,0,60):
            self.save(count);database.init_db()
            self.assertEqual(len(database.get_nodes('SIMULATION')),count)
            self.assertEqual(database.get_nodes('REAL'),real)
            self.assertEqual(self.client.get('/api/simulation/placement').json['plan'],plan(count))
        self.assertEqual(database.get_nodes('SIMULATION')[3]['latitude'],23.753)
    def test_bad_plan_atomic(self):
        self.save(2);bad=plan(4);bad['nodes'][3]['latitude']=float('nan')
        self.assertEqual(self.client.post('/api/simulation/placement',json=bad).status_code,400)
        self.assertEqual(len(database.get_nodes('SIMULATION')),2)
    def test_removal_preserves_history_but_not_live_readings(self):
        self.save(2)
        database.set_mode('SIMULATION')
        database.add_reading(dict(node_id='NODE-002',tilt_x=0,tilt_y=0,vibration=0,temperature=25,humidity=50,battery=90))
        self.save(1)
        self.assertEqual(len(database.get_history('NODE-002',source='SIMULATION')),1)
        self.assertEqual(database.get_latest_readings('SIMULATION'),[])
    def test_hardware_triangle_spacing(self):
        nodes=database.get_nodes('REAL');self.assertEqual({n['node_id'] for n in nodes},{'UG-01','UG-02','LD-01'})
        for i,a in enumerate(nodes):
            for b in nodes[i+1:]:
                lat1,lat2=map(math.radians,[a['latitude'],b['latitude']]);dl=math.radians(b['longitude']-a['longitude'])
                hav=math.sin((lat2-lat1)/2)**2+math.cos(lat1)*math.cos(lat2)*math.sin(dl/2)**2
                self.assertAlmostEqual(6371000*2*math.asin(math.sqrt(hav)),1000,places=3)
    def test_no_fixed_generator_inventory(self):
        import simulator
        simulator.create_nodes();self.assertEqual(simulator.nodes,[])
        self.save(7);simulator.create_nodes();self.assertEqual(len(simulator.nodes),7)
