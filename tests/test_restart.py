import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import database
from app import app
from telemetry import ingest
from runtime_lock import InstanceLock

PACKET = dict(host_id='HOST-01', zone_id='ZONE-A', node_id='UG-01', sequence=46,
              roll=50, pitch=-5, vibration=0, soil=0, rssi=-20, snr=9.5)


class RestartTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.db = patch.object(database, 'DB_NAME', str(Path(self.folder.name)/'test.db'))
        self.db.start()
        database.init_db()
        database.set_mode('REAL')
        self.client=app.test_client()

    def tearDown(self):
        self.db.stop()
        self.folder.cleanup()

    def test_restart_allows_reused_sequence_and_preserves_history(self):
        ingest(PACKET | {'sequence':1})
        ingest(PACKET)
        self.assertFalse(ingest(PACKET | {'sequence':12})['applied'])
        self.assertEqual(database.get_latest_readings()[0]['sequence'],46)
        response=self.client.post('/api/nodes/UG-01/session', json={'confirmed_restart':True})
        self.assertEqual(response.status_code,200)
        self.assertEqual(database.get_latest_readings(),[])
        result=ingest(PACKET | {'sequence':1})
        self.assertTrue(result['applied'])
        rows=database.get_history('UG-01')
        self.assertEqual(len(rows),4)
        self.assertNotEqual(rows[0]['session_id'],rows[-1]['session_id'])
        latest=database.get_latest_readings()[0]
        self.assertTrue(latest['online'])
        self.assertEqual(latest['sequence'],1)
        self.assertEqual(latest['persistent'],0)
        self.assertTrue(ingest(PACKET | {'sequence':1})['duplicate'])
        database.init_db()
        self.assertEqual(len(database.get_history('UG-01')),4)
        self.assertEqual(database.get_latest_readings()[0]['sequence'],1)

    def test_no_automatic_reset_or_unconfirmed_action(self):
        ingest(PACKET)
        self.assertEqual(self.client.post('/api/nodes/UG-01/session',json={}).status_code,400)
        self.assertEqual(self.client.post('/api/nodes/UG-01/session',json={'confirmed_restart':'true'}).status_code,400)
        self.assertEqual(self.client.post('/api/nodes/UNKNOWN/session',json={'confirmed_restart':True}).status_code,400)
        self.assertFalse(ingest(PACKET | {'sequence':1})['applied'])
        self.assertEqual(database.get_latest_readings()[0]['sequence'],46)

    def test_process_lock_excludes_second_instance_and_releases(self):
        path=Path(self.folder.name)/'host.lock'
        first=InstanceLock(path)
        try:
            with self.assertRaises(RuntimeError): InstanceLock(path)
        finally:
            first.close()
        second=InstanceLock(path)
        second.close()

if __name__=='__main__': unittest.main()
