import csv
import io
import json
from pathlib import Path
import unittest

from flask import Flask
from app import app


class InSARZonesTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_geojson_endpoint_and_structure(self):
        res = self.client.get('/map/jharia_subsidence_zones.geojson')
        self.assertEqual(res.status_code, 200)
        self.assertIn('application/geo+json', res.headers.get('Content-Type', ''))
        
        data = json.loads(res.data.decode('utf-8'))
        self.assertEqual(data.get('type'), 'FeatureCollection')
        features = data.get('features', [])
        self.assertEqual(len(features), 27)

        required_keys = {
            'zone_id', 'centroid_latitude', 'centroid_longitude', 'area_km2',
            'pixel_count', 'median_velocity_mm_year', 'mean_velocity_mm_year',
            'worst_velocity_mm_year', 'median_net_displacement_mm',
            'worst_net_displacement_mm', 'median_velocity_std_mm_year',
            'median_temporal_coherence', 'median_num_interferograms', 'confidence'
        }

        zone_ids = set()
        geom_types = set()
        for f in features:
            self.assertIn(f['geometry']['type'], ('Polygon', 'MultiPolygon'))
            geom_types.add(f['geometry']['type'])
            props = f['properties']
            for key in required_keys:
                self.assertIn(key, props, f"Missing key {key} in zone {props.get('zone_id')}")
            zone_ids.add(props['zone_id'])

        self.assertEqual(len(zone_ids), 27)
        self.assertIn('Polygon', geom_types)
        self.assertIn('MultiPolygon', geom_types)

    def test_csv_endpoint_and_structure(self):
        res = self.client.get('/map/jharia_subsidence_zones.csv')
        self.assertEqual(res.status_code, 200)
        self.assertIn('text/csv', res.headers.get('Content-Type', ''))

        lines = res.data.decode('utf-8').strip().splitlines()
        reader = csv.DictReader(lines)
        rows = list(reader)
        self.assertEqual(len(rows), 27)

        csv_zone_ids = {int(r['zone_id']) for r in rows}
        self.assertEqual(len(csv_zone_ids), 27)

    def test_zone_10_exact_statistics(self):
        res = self.client.get('/map/jharia_subsidence_zones.geojson')
        data = json.loads(res.data.decode('utf-8'))
        zone_10 = next((f for f in data['features'] if f['properties']['zone_id'] == 10), None)
        self.assertIsNotNone(zone_10, "Zone 10 must exist in GeoJSON")

        p = zone_10['properties']
        self.assertAlmostEqual(p['centroid_latitude'], 23.770139, places=5)
        self.assertAlmostEqual(p['centroid_longitude'], 86.393539, places=5)
        self.assertAlmostEqual(p['area_km2'], 2.1888, places=4)
        self.assertEqual(p['pixel_count'], 342)
        self.assertAlmostEqual(p['median_velocity_mm_year'], -89.69, places=1)
        self.assertAlmostEqual(p['mean_velocity_mm_year'], -100.71, places=1)
        self.assertAlmostEqual(p['worst_velocity_mm_year'], -238.91, places=1)
        self.assertAlmostEqual(p['median_net_displacement_mm'], -408.91, places=1)
        self.assertAlmostEqual(p['worst_net_displacement_mm'], -1060.97, places=1)
        self.assertAlmostEqual(p['median_temporal_coherence'], 1.0, places=2)
        self.assertAlmostEqual(p['median_num_interferograms'], 138, places=0)
        self.assertEqual(p['confidence'], 'very_high')

    def test_security_and_unknown_assets(self):
        res = self.client.get('/map/unknown_file.json')
        self.assertEqual(res.status_code, 404)
        res_traversal = self.client.get('/map/..%2Fapp.py')
        self.assertEqual(res_traversal.status_code, 404)

    def test_severity_distribution_no_safe_zones(self):
        res = self.client.get('/map/jharia_subsidence_zones.geojson')
        data = json.loads(res.data.decode('utf-8'))
        
        def classify(vel):
            if vel <= -85.0: return 'Critical'
            if vel <= -80.0: return 'Severe'
            if vel <= -75.0: return 'High'
            return 'Elevated'

        labels = [classify(f['properties']['median_velocity_mm_year']) for f in data['features']]
        self.assertNotIn('Safe', labels)
        self.assertNotIn('Normal', labels)
        self.assertEqual(labels.count('Critical'), 8)
        self.assertEqual(labels.count('Severe'), 5)
        self.assertEqual(labels.count('High'), 7)
        self.assertEqual(labels.count('Elevated'), 7)


if __name__ == '__main__':
    unittest.main()
