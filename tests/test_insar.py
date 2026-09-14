import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import h5py
import numpy as np
from flask import Flask

from insar import insar
from scripts.prepare_insar import prepare


class InSARTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.output = self.root/'source', self.root/'output'
        (self.source/'inputs').mkdir(parents=True)
        specs = [('velocity_corrected.h5', 'velocity', [[-.02, 0, .01], [.03, -.01, .02]]),
                 ('inputs/geometryGeo.h5', 'height', [[100, 110, 120], [120, 130, 140]]),
                 ('temporalCoherence.h5', 'temporalCoherence', [[1, 1, 0], [1, 1, 1]]),
                 ('waterMask.h5', 'waterMask', [[1, 1, 1], [0, 1, 1]])]
        for name,key,data in specs:
            with h5py.File(self.source/name,'w') as f:
                f.create_dataset(key,data=np.array(data,dtype='f4'))
                f.attrs.update(X_FIRST=408160,Y_FIRST=2643520,X_STEP=80,Y_STEP=-80,EPSG=32645,
                               UNIT='m/year',NO_DATA_VALUE=0,START_DATE='20220102',END_DATE='20260909')
                if key=='velocity':f.create_dataset('velocityStd',data=np.full((2,3),.001,dtype='f4'))

    def test_actual_units_coordinates_and_masks_preserved(self):
        m=prepare(self.source,self.output)
        raw=(self.output/m['asset']['filename']).read_bytes()
        a=np.frombuffer(raw,dtype='<f4').reshape(2,3,7)
        self.assertAlmostEqual(a[0,0,1],-20,places=4)
        self.assertAlmostEqual(a[0,0,5],1,places=4)
        self.assertEqual(a[0,0,0],100)
        self.assertTrue(86<a[0,0,3]<87)
        self.assertTrue(23<a[0,0,4]<24)
        self.assertGreater(a[0,1,3],a[0,0,3])
        self.assertLess(a[1,0,4],a[0,0,4])
        self.assertEqual(m['stats']['velocity_samples'],3)
        for r,c in [(0,1),(0,2),(1,0)]:self.assertFalse(int(a[r,c,6])&2)
        self.assertTrue(int(a[0,1,6])&1)  # Missing LOS must not remove valid terrain.
        self.assertEqual(hashlib.sha256(raw).hexdigest(),m['asset']['sha256'])
        self.assertEqual(gzip.decompress((self.output/(m['asset']['filename']+'.gz')).read_bytes()),raw)

    def test_zero_is_valid_when_nodata_not_declared(self):
        with h5py.File(self.source/'velocity_corrected.h5','a') as f:del f.attrs['NO_DATA_VALUE']
        m=prepare(self.source,self.output)
        self.assertEqual(m['stats']['velocity_samples'],4)

    def test_rejects_misaligned_grid(self):
        with h5py.File(self.source/'inputs/geometryGeo.h5','a') as f:f.attrs['X_FIRST']=408200
        with self.assertRaisesRegex(ValueError,'not aligned'):prepare(self.source,self.output)
        self.assertFalse((self.output/'manifest.json').exists())

    def test_rejects_unknown_units(self):
        with h5py.File(self.source/'velocity_corrected.h5','a') as f:f.attrs['UNIT']='unknown'
        with self.assertRaisesRegex(ValueError,'unit'):prepare(self.source,self.output)

    def test_rejects_missing_crs(self):
        with h5py.File(self.source/'velocity_corrected.h5','a') as f:del f.attrs['EPSG']
        with self.assertRaisesRegex(ValueError,'EPSG'):prepare(self.source,self.output)

    def test_rejects_geographic_grid(self):
        for path in self.source.rglob('*.h5'):
            with h5py.File(path,'a') as f:f.attrs['EPSG']=4326
        with self.assertRaisesRegex(ValueError,'projected'):prepare(self.source,self.output)

    def test_uniform_false_water_excludes_all(self):
        with h5py.File(self.source/'waterMask.h5','a') as f:f['waterMask'][:]=0
        with self.assertRaisesRegex(ValueError,'No valid'):prepare(self.source,self.output)

    def test_invalid_terrain_not_fabricated(self):
        with h5py.File(self.source/'inputs/geometryGeo.h5','a') as f:f['height'][0,0]=np.nan
        m=prepare(self.source,self.output)
        a=np.fromfile(self.output/m['asset']['filename'],dtype='<f4').reshape(-1,7)
        self.assertFalse(int(a[0,6])&3)
        self.assertTrue(np.isfinite(a).all())

    def test_read_only_routes_and_compression(self):
        m=prepare(self.source,self.output)
        app=Flask(__name__);app.config['INSAR_DATA_DIR']=self.output;app.register_blueprint(insar)
        client=app.test_client()
        with client.get('/api/insar/manifest.json') as response:
            self.assertEqual(response.json['version'],1)
            self.assertEqual(response.headers['Cache-Control'],'no-store')
        url='/api/insar/'+m['asset']['filename']
        with client.get(url,headers={'Accept-Encoding':'gzip'}) as response:
            self.assertEqual(response.headers['Content-Encoding'],'gzip')
            self.assertEqual(len(gzip.decompress(response.data)),m['asset']['bytes'])
        with client.get(url,headers={'Accept-Encoding':'gzip;q=0'}) as response:
            self.assertNotIn('Content-Encoding',response.headers)
        self.assertEqual(client.post(url).status_code,405)
        self.assertEqual(client.get('/api/insar/velocity_corrected.h5').status_code,404)
        self.assertEqual(client.get('/api/insar/..%2Fapp.py').status_code,404)

    def test_prepared_real_asset_matches_manifest(self):
        folder=Path(__file__).resolve().parents[1]/'static/insar/jharia'
        m=json.loads((folder/'manifest.json').read_text())
        data=(folder/m['asset']['filename']).read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(),m['asset']['sha256'])
        self.assertEqual(len(data),m['grid']['rows']*m['grid']['columns']*28)
        self.assertEqual(m['units']['velocity'],'mm/year')


if __name__=='__main__':unittest.main()
