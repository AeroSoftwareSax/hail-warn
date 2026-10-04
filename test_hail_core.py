#!/usr/bin/env python3
"""
Unit Test Suite for HailCore Algorithmic Logic
Deterministic, offline unit tests for core geospatial and threat assessment logic.
"""

import unittest
import unittest.mock as mock
from hail_core import (
    describe_hail_size,
    haversine_distance,
    find_nearest_nexrad,
    load_nexrad_stations,
    evaluate_hail_risk,
    perform_full_assessment
)

class TestHailCoreUnit(unittest.TestCase):
    """Unit tests for hail_core algorithms."""

    def test_describe_hail_size_table(self):
        self.assertEqual(describe_hail_size(0.0), "None")
        self.assertIn("Pea", describe_hail_size(0.25))
        self.assertIn("Marble", describe_hail_size(0.50))
        self.assertIn("Penny", describe_hail_size(0.75))
        self.assertIn("Nickel", describe_hail_size(0.88))
        self.assertIn("Quarter", describe_hail_size(1.00))
        self.assertIn("Half Dollar", describe_hail_size(1.25))
        self.assertIn("Walnut", describe_hail_size(1.50))
        self.assertIn("Golf Ball", describe_hail_size(1.75))
        self.assertIn("Hen Egg", describe_hail_size(2.00))
        self.assertIn("Tennis Ball", describe_hail_size(2.50))
        self.assertIn("Baseball", describe_hail_size(2.75))
        self.assertIn("Tea Cup", describe_hail_size(3.00))
        self.assertIn("Grapefruit", describe_hail_size(4.00))
        self.assertIn("Softball", describe_hail_size(4.50))
        self.assertIn("Softball+", describe_hail_size(5.00))

    def test_haversine_calculation(self):
        # Austin (30.2672, -97.7431) to San Antonio (29.4241, -98.4936) is ~74 miles
        d = haversine_distance(30.2672, -97.7431, 29.4241, -98.4936)
        self.assertTrue(70 < d < 80, f"Distance expected ~74 miles, got {d}")

    def test_find_nearest_nexrad(self):
        # Austin coordinates should return KGRK (Central Texas radar, 38 miles)
        station = find_nearest_nexrad(30.2672, -97.7431)
        self.assertIsNotNone(station)
        self.assertEqual(station['icao'], 'KGRK')
        self.assertLess(station['distance_miles'], 40.0)

    def test_load_nexrad_stations_structure(self):
        stations = load_nexrad_stations()
        self.assertIsInstance(stations, list)
        self.assertEqual(len(stations), 160)
        first = stations[0]
        self.assertIn('icao', first)
        self.assertIn('lat', first)
        self.assertIn('lon', first)

    def test_evaluate_hail_risk_offline(self):
        alerts = [{
            'event': 'Severe Thunderstorm Warning',
            'hail_size_in': 2.0,
            'description': 'HAIL...2.00 INCHES'
        }]
        convective = {'cape_j_kg': 2500, 'peak_cape_today': 3200, 'lifted_index': -6.0, 'freezing_level_ft': 9000}
        res = evaluate_hail_risk(alerts, [], convective)
        self.assertGreaterEqual(res['score'], 85)
        self.assertIn(res['level'], ['WARNING', 'EMERGENCY'])
        self.assertEqual(res['max_hail_inches'], 2.0)

    def test_perform_full_assessment_offline_mocked(self):
        mock_alerts = [{'event': 'Severe Thunderstorm Warning', 'hail_size_in': 1.5, 'description': 'HAIL...1.50 INCHES'}]
        mock_lsr = [{'distance_miles': 2.0, 'hail_size_in': 1.5, 'age_hours': 0.5, 'is_mping': True}]
        mock_convective = {'cape_j_kg': 1500, 'peak_cape_today': 2000, 'lifted_index': -3.0, 'freezing_level_ft': 11000}
        mock_radar = {'host': 'https://tilecache.rainviewer.com', 'frames': []}
        mock_regional = {'warnings': [], 'geojson': {'type': 'FeatureCollection', 'features': []}}
        mock_spc = {'categorical': {'type': 'FeatureCollection', 'features': []}, 'hail': {'type': 'FeatureCollection', 'features': []}}

        with mock.patch('hail_core.fetch_nws_point_alerts', return_value=mock_alerts), \
             mock.patch('hail_core.fetch_iem_lsr_reports', return_value=mock_lsr), \
             mock.patch('hail_core.fetch_open_meteo_convective', return_value=mock_convective), \
             mock.patch('hail_core.fetch_rainviewer_radar', return_value=mock_radar), \
             mock.patch('hail_core.fetch_active_nws_warnings', return_value=mock_regional), \
             mock.patch('hail_core.fetch_spc_day1_outlook', return_value=mock_spc):

            res = perform_full_assessment(30.2672, -97.7431, radius_miles=40)
            self.assertIn('assessment', res)
            self.assertIn('location', res)
            self.assertEqual(res['location']['latitude'], 30.2672)
            self.assertGreaterEqual(res['assessment']['score'], 60)


if __name__ == '__main__':
    unittest.main()
