#!/usr/bin/env python3
"""
Automated Test Suite for HailWarn Server & Sensor Fusion Logic
Tests endpoints, response schemas, and multi-source hail threat algorithms.
"""

import unittest
import threading
import time
import json
import urllib.request
from server import run_server, HailWarnRequestHandler, ThreadedHTTPServer, STATIC_DIR
from hail_core import (
    describe_hail_size,
    haversine_distance,
    evaluate_hail_risk,
    perform_full_assessment,
    load_nexrad_stations,
    find_nearest_nexrad
)

TEST_PORT = 18088

class TestHailDetection(unittest.TestCase):

    def test_hail_descriptions(self):
        self.assertIn("Quarter", describe_hail_size(1.00))
        self.assertIn("Golf Ball", describe_hail_size(1.75))
        self.assertIn("Baseball", describe_hail_size(2.75))
        self.assertIn("Softball", describe_hail_size(4.50))
        self.assertEqual(describe_hail_size(0), "None")

    def test_haversine_distance(self):
        # Dallas (32.7767, -96.7970) to Fort Worth (32.7555, -97.3308) is ~30 miles
        dist = haversine_distance(32.7767, -96.7970, 32.7555, -97.3308)
        self.assertTrue(28 < dist < 35, f"Expected ~30 miles, got {dist}")

    def test_threat_evaluation_clear_sky(self):
        assessment = evaluate_hail_risk([], [], {'cape_j_kg': 200, 'peak_cape_today': 300, 'lifted_index': 2.0})
        self.assertEqual(assessment['level'], 'NONE')
        self.assertLess(assessment['score'], 20)
        self.assertEqual(assessment['max_hail_inches'], 0.0)

    def test_threat_evaluation_severe_warning(self):
        alerts = [{
            'event': 'Severe Thunderstorm Warning',
            'hail_size_in': 1.75,
            'hail_threat_type': 'RADAR INDICATED',
            'headline': 'Severe Thunderstorm Warning for Hail',
            'description': 'HAIL...1.75 INCHES'
        }]
        convective = {'cape_j_kg': 1800, 'peak_cape_today': 2400, 'lifted_index': -4.5}
        assessment = evaluate_hail_risk(alerts, [], convective)
        self.assertIn(assessment['level'], ['WARNING', 'EMERGENCY'])
        self.assertGreaterEqual(assessment['score'], 70)
        self.assertEqual(assessment['max_hail_inches'], 1.75)
        self.assertIn("Golf Ball", assessment['max_hail_label'])

    def test_threat_evaluation_ground_mping_report(self):
        # mPING report 3.5 miles away of quarter size hail
        reports = [{
            'source_type': 'mPING Citizen Report',
            'is_mping': True,
            'hail_size_in': 1.00,
            'distance_miles': 3.5,
            'age_hours': 0.8
        }]
        convective = {'cape_j_kg': 1400, 'peak_cape_today': 1900, 'lifted_index': -3.0}
        assessment = evaluate_hail_risk([], reports, convective)
        self.assertGreaterEqual(assessment['score'], 35)
        self.assertTrue(any("mPING" in r for r in assessment['reasons']))

    def test_nearest_nexrad_lookup(self):
        # Fort Worth coordinates: 32.7555, -97.3308 should return KFWS (Dallas/Fort Worth)
        nearest_fw = find_nearest_nexrad(32.7555, -97.3308)
        self.assertIsNotNone(nearest_fw)
        self.assertEqual(nearest_fw['icao'], 'KFWS')
        self.assertLess(nearest_fw['distance_miles'], 16.0)

        # Oklahoma City: 35.4676, -97.5164 should return KTLX
        nearest_okc = find_nearest_nexrad(35.4676, -97.5164)
        self.assertIsNotNone(nearest_okc)
        self.assertEqual(nearest_okc['icao'], 'KTLX')

class TestServerEndpoints(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadedHTTPServer(('127.0.0.1', TEST_PORT), HailWarnRequestHandler)
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        time.sleep(0.5)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def get_url(self, path):
        req = urllib.request.Request(f"http://127.0.0.1:{TEST_PORT}{path}")
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.status, resp.headers, resp.read()

    def test_static_files(self):
        status, headers, content = self.get_url("/index.html")
        self.assertEqual(status, 200)
        self.assertIn(b"HAILWARN", content)
        self.assertIn("text/html", headers.get('Content-Type'))

        status, headers, content = self.get_url("/styles.css")
        self.assertEqual(status, 200)
        self.assertIn(b"ops-header", content)

        status, headers, content = self.get_url("/app.js")
        self.assertEqual(status, 200)
        self.assertIn(b"refreshAssessment", content)

    def test_api_health(self):
        status, headers, content = self.get_url("/api/health")
        self.assertEqual(status, 200)
        data = json.loads(content.decode('utf-8'))
        self.assertEqual(data.get('status'), 'ok')

    def test_api_assess_validation(self):
        # Missing coordinates should return 400
        try:
            self.get_url("/api/assess")
            self.fail("Expected 400 for missing coords")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)

    def test_api_assess_endpoint(self):
        status, headers, content = self.get_url("/api/assess?lat=32.7767&lon=-96.7970")
        self.assertEqual(status, 200)
        data = json.loads(content.decode('utf-8'))
        self.assertIn('assessment', data)
        self.assertIn('nws_alerts', data)
        self.assertIn('lsr_reports', data)
        self.assertIn('convective_data', data)
        self.assertIn('radar_metadata', data)
        self.assertIn('score', data['assessment'])
        self.assertIn('level', data['assessment'])

    def test_api_hotspots_endpoint(self):
        status, headers, content = self.get_url("/api/hotspots")
        self.assertEqual(status, 200)
        data = json.loads(content.decode('utf-8'))
        self.assertIn('hotspots', data)
        self.assertIsInstance(data['hotspots'], list)

    def test_api_radar_endpoint(self):
        status, headers, content = self.get_url("/api/radar")
        self.assertEqual(status, 200)
        data = json.loads(content.decode('utf-8'))
        self.assertIn('host', data)
        self.assertIn('frames', data)

    def test_api_search_endpoint(self):
        status, headers, content = self.get_url("/api/search?q=Dallas")
        self.assertEqual(status, 200)
        data = json.loads(content.decode('utf-8'))
        self.assertIsInstance(data, list)

    def test_api_nws_warnings_endpoint(self):
        status, headers, content = self.get_url("/api/nws/warnings?lat=32.7767&lon=-96.7970")
        self.assertEqual(status, 200)
        data = json.loads(content.decode('utf-8'))
        self.assertIn('warnings', data)
        self.assertIn('geojson', data)
        self.assertIn('count', data)
        self.assertEqual(data['geojson'].get('type'), 'FeatureCollection')

    def test_api_spc_outlook_endpoint(self):
        status, headers, content = self.get_url("/api/spc/outlook")
        self.assertEqual(status, 200)
        data = json.loads(content.decode('utf-8'))
        self.assertIn('categorical', data)
        self.assertIn('hail', data)
        self.assertEqual(data['categorical'].get('type'), 'FeatureCollection')

    def test_api_reverse_geocode_endpoint(self):
        status, headers, content = self.get_url("/api/reverse-geocode?lat=32.7767&lon=-96.7970")
        self.assertEqual(status, 200)
        data = json.loads(content.decode('utf-8'))
        self.assertIn('display_name', data)
        self.assertIn('clean_name', data)

    def test_api_nexrad_stations_endpoint(self):
        status, headers, content = self.get_url("/api/nexrad/stations?lat=32.7555&lon=-97.3308")
        self.assertEqual(status, 200)
        data = json.loads(content.decode('utf-8'))
        self.assertIn('stations', data)
        self.assertIn('nearest', data)
        self.assertEqual(data['count'], 160)
        self.assertIsNotNone(data['nearest'])
        self.assertEqual(data['nearest']['icao'], 'KFWS')

if __name__ == '__main__':
    unittest.main()
