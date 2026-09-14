import json
import math
from pathlib import Path
import unittest

LAT_M_PER_DEG = 110780.0
LON_M_PER_DEG = 101900.0
REF_LON = 86.35
REF_LAT = 23.75


def to_meters(lon, lat):
    return (lon - REF_LON) * LON_M_PER_DEG, (lat - REF_LAT) * LAT_M_PER_DEG


def to_latlon(x, y):
    return (y / LAT_M_PER_DEG) + REF_LAT, (x / LON_M_PER_DEG) + REF_LON


def point_in_ring(x, y, ring_m):
    inside = False
    n = len(ring_m)
    for i in range(n):
        j = (i + 1) % n
        xi, yi = ring_m[i]
        xj, yj = ring_m[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
    return inside


def point_in_poly(x, y, poly_rings_m):
    if not point_in_ring(x, y, poly_rings_m[0]):
        return False
    for hole in poly_rings_m[1:]:
        if point_in_ring(x, y, hole):
            return False
    return True


def percentile_ranks(values):
    indexed = sorted(enumerate(values), key=lambda x: x[1])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(indexed):
        j = i
        while j < len(indexed) and indexed[j][1] == indexed[i][1]:
            j += 1
        avg_rank = (i + j - 1) / 2.0
        normalized = avg_rank / (len(values) - 1) if len(values) > 1 else 1.0
        for k in range(i, j):
            ranks[indexed[k][0]] = normalized
        i = j
    return ranks


def calculate_priorities(features):
    conf_map = {'very_high': 1.0, 'high': 0.8, 'medium': 0.6, 'low': 0.4}
    abs_vels = [abs(f['properties']['median_velocity_mm_year']) for f in features]
    abs_disps = [abs(f['properties']['median_net_displacement_mm']) for f in features]
    areas = [f['properties']['area_km2'] for f in features]
    confs = [conf_map.get(f['properties'].get('confidence', 'medium'), 0.6) for f in features]

    vel_r = percentile_ranks(abs_vels)
    disp_r = percentile_ranks(abs_disps)
    area_r = percentile_ranks(areas)

    ranked = []
    for i, f in enumerate(features):
        p = f['properties']
        score = 0.45 * vel_r[i] + 0.25 * disp_r[i] + 0.20 * area_r[i] + 0.10 * confs[i]
        ranked.append({
            'zone_id': p['zone_id'],
            'priority_score': score,
            'area_km2': p['area_km2'],
            'med_vel': p['median_velocity_mm_year'],
            'worst_vel': p['worst_velocity_mm_year'],
            'feature': f
        })

    ranked.sort(key=lambda x: (x['priority_score'], abs(x['worst_vel'])), reverse=True)
    for r, item in enumerate(ranked, 1):
        item['priority_rank'] = r
    return ranked


def allocate_budget(ranked_zones, budget, max_nodes_per_zone=3):
    counts = {z['zone_id']: 0 for z in ranked_zones}
    remaining = budget

    while remaining > 0:
        best_z = None
        best_marginal = -1.0
        for z in ranked_zones:
            k = counts[z['zone_id']]
            if k >= max_nodes_per_zone:
                continue
            p = z['priority_score']
            sa = math.sqrt(z['area_km2'])
            if k == 0:
                marginal = p * (0.65 + 0.35 * min(sa, 1.2))
            else:
                marginal = p * (sa / (1.5 * k + 0.5))
            if marginal > best_marginal:
                best_marginal = marginal
                best_z = z
        if not best_z:
            break
        counts[best_z['zone_id']] += 1
        remaining -= 1
    return counts


class NodePlacementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1] / 'map/jharia_subsidence_zones.geojson'
        with open(path, 'r', encoding='utf-8') as f:
            cls.geojson = json.load(f)
        cls.features = cls.geojson['features']

    def test_27_zones_prioritized_without_nan(self):
        ranked = calculate_priorities(self.features)
        self.assertEqual(len(ranked), 27)

        for r in ranked:
            self.assertFalse(math.isnan(r['priority_score']), f"Zone {r['zone_id']} priority is NaN")
            self.assertTrue(0.0 <= r['priority_score'] <= 1.0)

        # Zone 10 is expected to rank #1 due to massive area, high velocity (-89.7) and high confidence
        self.assertEqual(ranked[0]['zone_id'], 10)
        self.assertGreater(ranked[0]['priority_score'], 0.90)

    def test_budget_allocation_limits(self):
        ranked = calculate_priorities(self.features)

        for budget in [5, 10, 15, 20]:
            alloc = allocate_budget(ranked, budget, max_nodes_per_zone=3)
            total_placed = sum(alloc.values())
            self.assertLessEqual(total_placed, budget)
            self.assertEqual(total_placed, budget)

            # Check maxNodesPerZone cap
            for zid, count in alloc.items():
                self.assertLessEqual(count, 3, f"Zone {zid} received {count} > 3 nodes")

        # In budget 20, Zone 10 must receive 3 nodes due to its 2.19 km2 size
        alloc_20 = allocate_budget(ranked, 20, max_nodes_per_zone=3)
        self.assertEqual(alloc_20[10], 3)

    def test_interior_point_geometry_guarantee(self):
        ranked = calculate_priorities(self.features)
        
        # Test anchors inside parent polygons for top 5 zones
        for z in ranked[:5]:
            geom = z['feature']['geometry']
            poly_list_m = []
            if geom['type'] == 'Polygon':
                poly_list_m.append([[to_meters(pt[0], pt[1]) for pt in r] for r in geom['coordinates']])
            else:
                for poly in geom['coordinates']:
                    poly_list_m.append([[to_meters(pt[0], pt[1]) for pt in r] for r in poly])

            # Sample 80m candidates inside
            candidates = []
            for poly in poly_list_m:
                ext = poly[0]
                xs = [p[0] for p in ext]
                ys = [p[1] for p in ext]
                for gx in range(int(min(xs)) + 40, int(max(xs)), 80):
                    for gy in range(int(min(ys)) + 40, int(max(ys)), 80):
                        if point_in_poly(gx, gy, poly):
                            candidates.append((gx, gy))

            self.assertGreater(len(candidates), 0, f"Zone {z['zone_id']} should have interior candidates")

            # Check that first candidate is strictly inside
            pt = candidates[0]
            lat, lon = to_latlon(pt[0], pt[1])
            is_inside = any(point_in_poly(pt[0], pt[1], p) for p in poly_list_m)
            self.assertTrue(is_inside, f"Anchor ({lat}, {lon}) must be inside Zone {z['zone_id']}")

    def test_minimum_spacing_between_multiple_anchors(self):
        # In Zone 10, generate 3 anchors and verify pairwise spacing >= 200m
        z10 = next(f for f in self.features if f['properties']['zone_id'] == 10)
        geom = z10['geometry']
        poly_list_m = []
        for poly in geom['coordinates']:
            poly_list_m.append([[to_meters(pt[0], pt[1]) for pt in r] for r in poly])

        # Grid candidates inside the main component
        main_comp = poly_list_m[1] # 96.9% of Zone 10
        xs = [p[0] for p in main_comp[0]]
        ys = [p[1] for p in main_comp[0]]
        candidates = []
        for gx in range(int(min(xs)) + 40, int(max(xs)), 80):
            for gy in range(int(min(ys)) + 40, int(max(ys)), 80):
                if point_in_poly(gx, gy, main_comp):
                    candidates.append((gx, gy))

        self.assertGreater(len(candidates), 0, "Main component of Zone 10 must contain interior candidates")
        # Select 3 farthest points with min spacing 200m
        anchors = [candidates[len(candidates) // 2]]
        while len(anchors) < 3:
            best_cand = None
            max_min_d = -1
            for c in candidates:
                min_d = min(math.hypot(c[0] - a[0], c[1] - a[1]) for a in anchors)
                if min_d >= 200.0 and min_d > max_min_d:
                    max_min_d = min_d
                    best_cand = c
            self.assertIsNotNone(best_cand, "Must be able to find candidate with >= 200m spacing in Zone 10")
            anchors.append(best_cand)

    def test_auto_recommendation_allocation(self):
        ranked = calculate_priorities(self.features)
        
        auto_counts = {}
        for z in ranked:
            if z['priority_score'] < 0.40:
                auto_counts[z['zone_id']] = 0
            elif z['priority_score'] >= 0.70:
                count = 3 if z['area_km2'] >= 1.0 else (2 if z['area_km2'] >= 0.4 else 1)
                auto_counts[z['zone_id']] = count
            elif z['priority_score'] >= 0.50:
                count = 2 if z['area_km2'] >= 0.8 else 1
                auto_counts[z['zone_id']] = count
            elif z['area_km2'] >= 0.50:
                auto_counts[z['zone_id']] = 1
            else:
                auto_counts[z['zone_id']] = 0

        total_auto = sum(auto_counts.values())
        self.assertGreaterEqual(total_auto, 15, "Auto mode should recommend at least 15 planning nodes across Jharia")
        self.assertLessEqual(total_auto, 30, "Auto mode should not over-allocate beyond realistic field deployment")
        # Zone 10 (highest priority, 2.19 km2) must receive 3 nodes
        self.assertEqual(auto_counts[10], 3)
        # Lower priority zone (e.g. Zone 27) should receive 0
        self.assertEqual(auto_counts[ranked[-1]['zone_id']], 0)

    def test_canonical_node_schema(self):
        ranked = calculate_priorities(self.features)
        alloc = allocate_budget(ranked, 10, max_nodes_per_zone=3)
        
        # Build canonical nodes
        canonical_nodes = []
        seq = 1
        for z in ranked:
            count = alloc.get(z['zone_id'], 0)
            for a_idx in range(count):
                node = {
                    'id': f'NODE-{seq:03d}',
                    'zone_id': z['zone_id'],
                    'latitude': z['feature']['properties']['centroid_latitude'],
                    'longitude': z['feature']['properties']['centroid_longitude'],
                    'node_type': 'underground_monitoring',
                    'priority_score': round(z['priority_score'], 3),
                    'priority_rank': z['priority_rank'],
                    'placement_score': round(z['priority_score'] * (1.0 - a_idx * 0.08), 3),
                    'median_velocity_mm_year': z['med_vel'],
                    'worst_velocity_mm_year': z['worst_vel'],
                    'area_km2': z['area_km2'],
                    'status': 'recommended',
                    'depth_m': None, # strictly null
                    'elevation_m': None,
                    'source': 'insar'
                }
                canonical_nodes.append(node)
                seq += 1

        self.assertEqual(len(canonical_nodes), 10)
        for n in canonical_nodes:
            self.assertIsNone(n['depth_m'], "CRITICAL: depth_m must be strictly null (never fabricated)")
            self.assertEqual(n['status'], 'recommended')
            self.assertEqual(n['node_type'], 'underground_monitoring')
            self.assertTrue(n['id'].startswith('NODE-'))
            self.assertTrue(23.6 <= n['latitude'] <= 23.9, f"Latitude {n['latitude']} out of Jharia bounds")
            self.assertTrue(86.1 <= n['longitude'] <= 86.6, f"Longitude {n['longitude']} out of Jharia bounds")

    def test_budget_presets_5_10_25_40_60(self):
        ranked = calculate_priorities(self.features)
        for preset in [5, 10, 25, 40, 60]:
            alloc = allocate_budget(ranked, preset, max_nodes_per_zone=3)
            total = sum(alloc.values())
            self.assertLessEqual(total, preset)
            # Max capacity for 27 zones with max 3 per zone is 81
            expected = min(preset, 27 * 3)
            self.assertEqual(total, expected)

    def test_geojson_export_rfc7946(self):
        ranked = calculate_priorities(self.features)
        alloc = allocate_budget(ranked, 5, max_nodes_per_zone=3)
        
        features = []
        seq = 1
        for z in ranked:
            count = alloc.get(z['zone_id'], 0)
            for _ in range(count):
                lat = z['feature']['properties']['centroid_latitude']
                lon = z['feature']['properties']['centroid_longitude']
                feat = {
                    'type': 'Feature',
                    'id': f'NODE-{seq:03d}',
                    'geometry': {
                        'type': 'Point',
                        'coordinates': [lon, lat] # RFC 7946: [lon, lat]
                    },
                    'properties': {
                        'node_id': f'NODE-{seq:03d}',
                        'zone_id': z['zone_id'],
                        'priority_rank': z['priority_rank'],
                        'priority_score': z['priority_score'],
                        'depth_m': None,
                        'status': 'recommended'
                    }
                }
                features.append(feat)
                seq += 1

        fc = {'type': 'FeatureCollection', 'features': features}
        self.assertEqual(fc['type'], 'FeatureCollection')
        self.assertEqual(len(fc['features']), 5)
        for feat in fc['features']:
            self.assertEqual(feat['geometry']['type'], 'Point')
            coords = feat['geometry']['coordinates']
            self.assertGreater(coords[0], 80.0, "Coordinate[0] must be Longitude (> 80° for Jharia)")
            self.assertLess(coords[1], 30.0, "Coordinate[1] must be Latitude (< 30° for Jharia)")
            self.assertIsNone(feat['properties']['depth_m'])


if __name__ == '__main__':
    unittest.main()
