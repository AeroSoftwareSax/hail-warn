#!/usr/bin/env python3
"""
Automated Test Suite for HailWarn Server & Sensor Fusion Logic
Tests endpoints, response schemas, and multi-source hail threat algorithms.
Includes deterministic offline fixtures and regression tests for SEC-01..07 & DEF-01..11.
"""

import os
import sys
import unittest
import unittest.mock as mock
import threading
import time
import json
import urllib.request
import urllib.error
import warnings
import socket
import csv
from server import run_server, HailWarnRequestHandler, ThreadedHTTPServer, STATIC_DIR
from hail_core import (
    describe_hail_size,
    haversine_distance,
    evaluate_hail_risk,
    perform_full_assessment,
    load_nexrad_stations,
    find_nearest_nexrad,
    fetch_nws_point_alerts,
    fetch_iem_lsr_reports,
    fetch_open_meteo_convective,
    fetch_rainviewer_radar,
    fetch_active_nws_warnings,
    fetch_active_national_hotspots,
    fetch_spc_day1_outlook,
    evaluate_threat_threshold,
    is_point_in_bbox,
    is_geometry_intersecting_bbox,
    scan_hail_bbox,
    generate_threat_dossier,
    SSL_CTX,
    _CACHE,
    _cache_set,
    _cache_get,
    MAX_CACHE_ENTRIES
)

TEST_PORT = 18088

# ---------------------------------------------------------------------------
# Offline Mock HTTP Fixtures
# ---------------------------------------------------------------------------
class MockResponse:
    """Mock urllib response object returning predetermined JSON payloads."""
    def __init__(self, data_bytes, status=200, headers=None):
        self.data_bytes = data_bytes
        self.status = status
        self.headers = headers or {'Content-Type': 'application/json'}
        self.closed = False

    def read(self):
        return self.data_bytes

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

_ORIG_URLOPEN = urllib.request.urlopen

def mock_urlopen(req, *args, **kwargs):
    """Deterministic offline interceptor for external meteorological and geocoding APIs."""
    url = req.full_url if hasattr(req, 'full_url') else str(req)

    # Let localhost requests through to the local test server
    if f"127.0.0.1:{TEST_PORT}" in url or f"localhost:{TEST_PORT}" in url:
        return _ORIG_URLOPEN(req, *args, **kwargs)

    # NWS active point or national alerts
    if "api.weather.gov/alerts/active" in url:
        mock_data = {
            "type": "FeatureCollection",
            "features": [{
                "id": "urn:oid:2.49.0.1.840.0.mock.alert.1",
                "properties": {
                    "event": "Severe Thunderstorm Warning",
                    "severity": "Severe",
                    "urgency": "Immediate",
                    "headline": "Severe Thunderstorm Warning issued for Dallas County",
                    "description": "HAIL...1.75 INCHES AND 60 MPH WIND GUSTS",
                    "instruction": "Take shelter in a sturdy building.",
                    "areaDesc": "Dallas, TX",
                    "parameters": {
                        "maxHailSize": ["1.75"],
                        "maxWindGust": ["60 MPH"],
                        "hailThreat": ["RADAR INDICATED"],
                        "eventMotionDescription": ["240DEG...35KT...32.70,-96.90"]
                    }
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [-96.90, 32.70],
                        [-96.70, 32.70],
                        [-96.70, 32.85],
                        [-96.90, 32.85],
                        [-96.90, 32.70]
                    ]]
                }
            }]
        }
        return MockResponse(json.dumps(mock_data).encode('utf-8'))

    # IEM Local Storm Reports (LSR)
    if "mesonet.agron.iastate.edu/geojson/lsr.py" in url:
        mock_data = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-96.80, 32.78]},
                "properties": {
                    "typetext": "HAIL",
                    "type": "H",
                    "magnitude": "1.75",
                    "city": "Dallas",
                    "county": "Dallas",
                    "state": "TX",
                    "remark": "Golf ball hail reported by trained spotter",
                    "source": "Spotter",
                    "valid": "2026-10-03T18:00:00Z"
                }
            }]
        }
        return MockResponse(json.dumps(mock_data).encode('utf-8'))

    # IEM Storm-Based Warnings (SBW)
    if "mesonet.agron.iastate.edu/geojson/sbw.geojson" in url:
        mock_data = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [-96.95, 32.65],
                        [-96.65, 32.65],
                        [-96.65, 32.90],
                        [-96.95, 32.90],
                        [-96.95, 32.65]
                    ]]
                },
                "properties": {
                    "wfo": "FWD",
                    "phenomena": "SV",
                    "eventid": 101,
                    "year": 2026,
                    "ps": "Severe Thunderstorm Warning",
                    "hailtag": 1.75,
                    "windtag": 60
                }
            }]
        }
        return MockResponse(json.dumps(mock_data).encode('utf-8'))

    # Open-Meteo convective physics
    if "api.open-meteo.com" in url:
        mock_data = {
            "current": {
                "temperature_2m": 24.5,
                "relative_humidity_2m": 72,
                "precipitation": 0.0,
                "wind_speed_10m": 15.0,
                "wind_gusts_10m": 28.0,
                "wind_direction_10m": 180,
                "surface_pressure": 1010.5,
                "weather_code": 3
            },
            "hourly": {
                "cape": [2200, 2400, 1800],
                "lifted_index": [-4.5, -5.2, -3.8],
                "convective_inhibition": [-15, -20, -10],
                "freezing_level_height": [3600, 3550, 3500]
            }
        }
        return MockResponse(json.dumps(mock_data).encode('utf-8'))

    # RainViewer radar metadata
    if "api.rainviewer.com" in url:
        mock_data = {
            "host": "https://tilecache.rainviewer.com",
            "radar": {
                "past": [
                    {"time": 1727980000, "path": "/v2/radar/1727980000"},
                    {"time": 1727980600, "path": "/v2/radar/1727980600"}
                ],
                "nowcast": [
                    {"time": 1727981200, "path": "/v2/radar/1727981200"}
                ]
            }
        }
        return MockResponse(json.dumps(mock_data).encode('utf-8'))

    # SPC Convective Outlooks
    if "spc.noaa.gov" in url:
        mock_data = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [-100.0, 30.0],
                        [-95.0, 30.0],
                        [-95.0, 36.0],
                        [-100.0, 36.0],
                        [-100.0, 30.0]
                    ]]
                },
                "properties": {"LABEL": "SLGT", "LABEL2": "Slight"}
            }]
        }
        return MockResponse(json.dumps(mock_data).encode('utf-8'))

    # OpenStreetMap Nominatim search
    if "nominatim.openstreetmap.org/search" in url:
        mock_data = [{
            "display_name": "Dallas, Dallas County, Texas, United States",
            "lat": "32.7767",
            "lon": "-96.7970",
            "type": "city",
            "importance": 0.85
        }]
        return MockResponse(json.dumps(mock_data).encode('utf-8'))

    # OpenStreetMap Nominatim reverse geocode
    if "nominatim.openstreetmap.org/reverse" in url:
        mock_data = {
            "display_name": "Dallas, Dallas County, Texas, United States",
            "address": {
                "city": "Dallas",
                "state": "Texas",
                "country": "United States"
            }
        }
        return MockResponse(json.dumps(mock_data).encode('utf-8'))

    # Fallback empty payload
    return MockResponse(b"{}")


class TestHailDetection(unittest.TestCase):
    """Unit tests for hail detection domain algorithms."""

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
    """End-to-end integration tests for HTTP server endpoints with offline fixtures."""

    @classmethod
    def setUpClass(cls):
        # Install deterministic offline interceptor for external requests
        urllib.request.urlopen = mock_urlopen

        cls.server = ThreadedHTTPServer(('127.0.0.1', TEST_PORT), HailWarnRequestHandler)
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        # Restore original urlopen handler
        urllib.request.urlopen = _ORIG_URLOPEN

    def get_url(self, path):
        req = urllib.request.Request(f"http://127.0.0.1:{TEST_PORT}{path}")
        with urllib.request.urlopen(req, timeout=8) as resp:  # nosec B310
            return resp.status, resp.headers, resp.read()

    # --- Baseline 17 Tests ---

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
            e.close()  # DEF-10: Close HTTPError to prevent ResourceWarning

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

    # =======================================================================
    # Regression Test Suite: Security (SEC-01..07) & Defects (DEF-01..11)
    # =======================================================================

    def test_sec01_tls_verification_enabled(self):
        """SEC-01: Verify TLS certificate & hostname validation is strictly enabled."""
        import ssl
        self.assertEqual(SSL_CTX.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(SSL_CTX.check_hostname)

    def test_sec02_xss_sanitization_in_feed_and_popups(self):
        """SEC-02: Verify escapeHtml helper exists and sanitizes dynamic values in static/app.js."""
        app_js_path = os.path.join(STATIC_DIR, 'app.js')
        with open(app_js_path, 'r', encoding='utf-8') as f:
            content = f.read()
        self.assertIn("function escapeHtml(str)", content)
        self.assertIn("escapeHtml(item.remark)", content)
        self.assertIn("escapeHtml(r.remark", content)
        self.assertIn("escapeHtml(item.display_name)", content)
        self.assertIn("escapeHtml(h.area)", content)

    def test_sec03_security_headers_and_options_preflight(self):
        """SEC-03: Verify security headers and CORS OPTIONS preflight response."""
        status, headers, content = self.get_url("/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get('X-Content-Type-Options'), 'nosniff')
        self.assertEqual(headers.get('X-Frame-Options'), 'DENY')
        self.assertEqual(headers.get('Referrer-Policy'), 'strict-origin-when-cross-origin')
        self.assertIn("default-src 'self'", headers.get('Content-Security-Policy', ''))

        # OPTIONS preflight request
        req = urllib.request.Request(f"http://127.0.0.1:{TEST_PORT}/api/assess", method='OPTIONS')
        with urllib.request.urlopen(req, timeout=5) as resp:  # nosec B310
            self.assertEqual(resp.status, 204)
            allow_methods = resp.headers.get('Access-Control-Allow-Methods', '')
            self.assertIn('GET', allow_methods)
            self.assertIn('OPTIONS', allow_methods)

    def test_sec04_default_host_binding(self):
        """SEC-04: Verify server default host is 127.0.0.1 instead of 0.0.0.0."""
        from server import run_server
        defaults = run_server.__defaults__
        default_host = defaults[1] if len(defaults) > 1 else None
        self.assertEqual(default_host, '127.0.0.1')

    def test_sec05_coordinate_and_parameter_bounds(self):
        """SEC-05: Strict coordinate and parameter validation (reject NaN, Inf, out-of-bounds)."""
        invalid_urls = [
            "/api/assess?lat=nan&lon=nan",
            "/api/assess?lat=inf&lon=0",
            "/api/assess?lat=95.0&lon=0",
            "/api/assess?lat=0&lon=190.0",
            "/api/assess?lat=32.7&lon=-96.8&radius=-10",
            "/api/assess?lat=32.7&lon=-96.8&hours=-5",
            "/api/assess?lat=32.7&lon=-96.8&hours=10000",
        ]
        for url in invalid_urls:
            with self.subTest(url=url):
                try:
                    self.get_url(url)
                    self.fail(f"Expected HTTP 400 for invalid query {url}")
                except urllib.error.HTTPError as e:
                    self.assertEqual(e.code, 400)
                    e.close()

    def test_sec06_cdn_subresource_integrity(self):
        """SEC-06: Verify SRI integrity and crossorigin attributes on external CDN assets."""
        index_html_path = os.path.join(STATIC_DIR, 'index.html')
        with open(index_html_path, 'r', encoding='utf-8') as f:
            content = f.read()
        self.assertIn('integrity="sha512-DTOQO9RWCH3ppGqcWaEA1BIZOC6xxalwEsw9c2QQeAIftl+Vegovlnee1c9QX4TctnWMn13TZye+giMm8e2LwA=="', content)
        self.assertIn('crossorigin="anonymous"', content)

    def test_sec07_no_internal_stack_leak_on_500(self):
        """SEC-07: Verify 500 error responses do not leak internal exception tracebacks or file paths."""
        with mock.patch('server.perform_full_assessment', side_effect=RuntimeError("SecretInternalDbError: /var/secrets/key")):
            try:
                self.get_url("/api/assess?lat=32.7767&lon=-96.7970")
                self.fail("Expected 500 status")
            except urllib.error.HTTPError as e:
                self.assertEqual(e.code, 500)
                body = e.read().decode('utf-8')
                e.close()
                data = json.loads(body)
                self.assertNotIn("SecretInternalDbError", body)
                self.assertNotIn("/var/secrets", body)
                self.assertNotIn("details", data)
                self.assertEqual(data.get('error'), 'Assessment failure. Please check server logs.')

    def test_def01_iem_lsr_null_geometry_handling(self):
        """DEF-01: IEM Local Storm Report GeoJSON null geometry & empty coordinates handling."""
        null_geom_data = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": None, "properties": {"typetext": "HAIL", "magnitude": "1.00", "valid": "2026-10-03T18:00:00Z"}},
                {"type": "Feature", "geometry": {"coordinates": []}, "properties": {"typetext": "HAIL", "magnitude": "1.00", "valid": "2026-10-03T18:00:00Z"}},
                {"type": "Feature", "geometry": {"coordinates": [-96.80, 32.78]}, "properties": {"typetext": "HAIL", "magnitude": "1.25", "valid": "2026-10-03T18:00:00Z"}}
            ]
        }
        with mock.patch('hail_core.http_get_json', return_value=null_geom_data):
            reports = fetch_iem_lsr_reports(32.78, -96.80)
            self.assertEqual(len(reports), 1)
            self.assertEqual(reports[0]['hail_size_in'], 1.25)

    def test_def02_open_meteo_null_hourly_iterables(self):
        """DEF-02: Open-Meteo null hourly dictionary or null iterables handling."""
        null_hourly = {
            "current": {"temperature_2m": 22.0},
            "hourly": {
                "cape": None,
                "lifted_index": None,
                "convective_inhibition": None,
                "freezing_level_height": None
            }
        }
        with mock.patch('hail_core.http_get_json', return_value=null_hourly):
            res = fetch_open_meteo_convective(32.78, -96.80)
            self.assertIsInstance(res, dict)
            self.assertEqual(res.get('cape_j_kg'), 0)

    def test_def03_risk_evaluation_none_comparisons(self):
        """DEF-03: Guard against NoneType comparisons in evaluate_hail_risk."""
        alerts = [{'event': 'Severe Thunderstorm Warning', 'hail_size_in': None}]
        reports = [{'age_hours': None, 'distance_miles': None, 'hail_size_in': None}]
        convective = {'cape_j_kg': None, 'peak_cape_today': None, 'lifted_index': None, 'freezing_level_ft': None}
        reg_warnings = [{'event': 'Severe Thunderstorm Warning', 'distance_miles': None, 'hail_size_in': None, 'motion': None}]
        res = evaluate_hail_risk(alerts, reports, convective, regional_warnings=reg_warnings)
        self.assertIsInstance(res, dict)
        self.assertIn('score', res)
        self.assertIn('level', res)

    def test_def04_cache_max_capacity_and_thread_safety(self):
        """DEF-04: Cache capacity limit (500 items), TTL eviction, and thread safety."""
        for i in range(550):
            _cache_set(f"http://test.url/{i}", {"index": i})
        self.assertLessEqual(len(_CACHE), MAX_CACHE_ENTRIES)
        self.assertIsNotNone(_cache_get("http://test.url/549"))
        self.assertIsNone(_cache_get("http://test.url/0"))  # Evicted LRU

    def test_def05_haversine_and_storm_motion_boundary_cases(self):
        """DEF-05: Haversine domain clamping (a > 1.0) and storm motion at poles (slat = 90)."""
        # Test antipodal points
        dist = haversine_distance(0.0, 0.0, 0.0, 180.0)
        self.assertGreater(dist, 12000.0)

        # Test polar storm motion vector projection
        polar_nws = {
            "type": "FeatureCollection",
            "features": [{
                "id": "polar.warn.1",
                "properties": {
                    "event": "Severe Thunderstorm Warning",
                    "parameters": {
                        "eventMotionDescription": ["045DEG...30KT...90.0,0.0"],
                        "maxHailSize": ["1.00"]
                    }
                },
                "geometry": {"type": "Polygon", "coordinates": [[[0, 89], [1, 89], [1, 90], [0, 89]]]}
            }]
        }
        with mock.patch('hail_core.http_get_json', return_value=polar_nws):
            warn_res = fetch_active_nws_warnings(lat=89.0, lon=0.0)
            self.assertIsInstance(warn_res, dict)
            self.assertGreaterEqual(warn_res['count'], 1)

    def test_def06_radar_playback_with_zero_frames(self):
        """DEF-06: Radar animation playback loop safely handles empty radarFrames."""
        app_js_path = os.path.join(STATIC_DIR, 'app.js')
        with open(app_js_path, 'r', encoding='utf-8') as f:
            content = f.read()
        self.assertIn("if (!state.radarFrames || state.radarFrames.length === 0)", content)

    def test_def07_freezing_level_missing_data_default(self):
        """DEF-07: Freezing level missing data defaults to 3500m / moderate, not 0 / high."""
        empty_data = {"current": {}, "hourly": {}}
        with mock.patch('hail_core.http_get_json', return_value=empty_data):
            res = fetch_open_meteo_convective(32.78, -96.80)
            self.assertGreater(res['freezing_level_ft'], 10000)
            self.assertEqual(res['hail_survival_rating'], 'MODERATE')

    def test_def08_nws_and_rainviewer_null_properties(self):
        """DEF-08: NWS alerts with null parameters/description and RainViewer null radar."""
        null_alert = {
            "type": "FeatureCollection",
            "features": [{
                "id": "null.alert.1",
                "properties": {
                    "event": "Severe Thunderstorm Warning",
                    "parameters": None,
                    "description": None,
                    "headline": None
                },
                "geometry": None
            }]
        }
        with mock.patch('hail_core.http_get_json', return_value=null_alert):
            alerts = fetch_nws_point_alerts(32.78, -96.80)
            self.assertEqual(len(alerts), 1)
            self.assertIsNone(alerts[0]['hail_size_in'])

        null_radar = {"host": None, "radar": None}
        with mock.patch('hail_core.http_get_json', return_value=null_radar):
            radar = fetch_rainviewer_radar()
            self.assertEqual(radar['frames'], [])

    def test_def09_offline_deterministic_test_suite(self):
        """DEF-09: Tests execute 100% offline with zero live network calls."""
        # Confirm that mock_urlopen interceptor is active for public domains
        resp = urllib.request.urlopen("https://api.weather.gov/alerts/active")  # nosec B310
        data = json.loads(resp.read().decode('utf-8'))
        self.assertEqual(data.get('type'), 'FeatureCollection')

    def test_def10_http_error_closed_without_resource_warning(self):
        """DEF-10: HTTPError tempfile response closed properly without ResourceWarning."""
        with warnings.catch_warnings(record=True) as recorded:
            warnings.simplefilter("always", ResourceWarning)
            try:
                self.get_url("/api/assess")
            except urllib.error.HTTPError as e:
                e.close()
            resource_warnings = [w for w in recorded if issubclass(w.category, ResourceWarning)]
            self.assertEqual(len(resource_warnings), 0)

    def test_def11_assessment_graceful_sensor_degradation(self):
        """DEF-11: perform_full_assessment survives single sensor failure gracefully."""
        with mock.patch('hail_core.fetch_open_meteo_convective', side_effect=RuntimeError("Simulated Sensor Failure")):
            res = perform_full_assessment(32.7767, -96.7970, radius_miles=45)
            self.assertIn('assessment', res)
            self.assertEqual(res['convective_data'], {})
            self.assertIn('score', res['assessment'])

    def test_def12_fuzz_null_features_handling(self):
        """DEF-12: Verify GeoJSON feeds with {'features': null} degrade cleanly to empty collections."""
        null_payload = {"type": "FeatureCollection", "features": None}

        # 1. Test fetch_active_nws_warnings
        with mock.patch('hail_core.http_get_json', return_value=null_payload):
            warn_res = fetch_active_nws_warnings(lat=32.7767, lon=-96.7970)
            self.assertIsInstance(warn_res, dict)
            self.assertEqual(warn_res['count'], 0)
            self.assertEqual(warn_res['warnings'], [])
            self.assertEqual(warn_res['geojson']['type'], 'FeatureCollection')
            self.assertEqual(warn_res['geojson']['features'], [])

        # 2. Test fetch_nws_point_alerts
        with mock.patch('hail_core.http_get_json', return_value=null_payload):
            alerts = fetch_nws_point_alerts(32.7767, -96.7970)
            self.assertIsInstance(alerts, list)
            self.assertEqual(len(alerts), 0)

        # 3. Test fetch_iem_lsr_reports
        with mock.patch('hail_core.http_get_json', return_value=null_payload):
            reports = fetch_iem_lsr_reports(32.7767, -96.7970)
            self.assertIsInstance(reports, list)
            self.assertEqual(len(reports), 0)

    def test_def13_fuzz_null_feature_items_and_properties(self):
        """DEF-13: Verify features array containing [None], non-dicts, or null properties are filtered safely."""
        corrupt_features = {
            "type": "FeatureCollection",
            "features": [
                None,
                42,
                "corrupt_element",
                {"properties": None, "geometry": None},
                {"properties": {}, "geometry": None},
                {
                    "id": "valid.alert.1",
                    "properties": {
                        "id": "valid.alert.1",
                        "event": "Severe Thunderstorm Warning",
                        "severity": "Severe",
                        "description": "HAIL...1.50 INCHES",
                        "parameters": {"maxHailSize": ["1.50"]}
                    },
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[-96.8, 32.7], [-96.7, 32.7], [-96.7, 32.8], [-96.8, 32.7]]]
                    }
                }
            ]
        }

        with mock.patch('hail_core.http_get_json', return_value=corrupt_features):
            # Test in active warnings
            warn_res = fetch_active_nws_warnings(lat=32.7767, lon=-96.7970)
            self.assertEqual(warn_res['count'], 1)
            self.assertEqual(warn_res['warnings'][0]['id'], 'valid.alert.1')
            self.assertEqual(warn_res['warnings'][0]['hail_size_in'], 1.50)

            # Test in point alerts
            alerts = fetch_nws_point_alerts(32.7767, -96.7970)
            self.assertEqual(len(alerts), 1)
            self.assertEqual(alerts[0]['id'], 'valid.alert.1')
            self.assertEqual(alerts[0]['hail_size_in'], 1.50)

    def test_def14_fuzz_non_string_motion_and_wind_tags(self):
        """DEF-14: Verify non-string motion/wind tags ([None], integers, booleans) do not crash regex or containment."""
        test_cases = [
            ([None], [None], "NoneType tags"),
            ([123], [80], "Integer tags"),
            ([{"deg": 240}], [{"gust": 80}], "Dict tags"),
            ([True], [False], "Boolean tags"),
            (["invalid string without pattern"], ["60 MPH"], "Malformed motion string")
        ]

        for motion_val, wind_val, case_name in test_cases:
            with self.subTest(case=case_name):
                payload = {
                    "type": "FeatureCollection",
                    "features": [{
                        "id": f"test.motion.{case_name}",
                        "properties": {
                            "event": "Severe Thunderstorm Warning",
                            "severity": "Severe",
                            "parameters": {
                                "eventMotionDescription": motion_val,
                                "maxWindGust": wind_val,
                                "maxHailSize": ["1.25"]
                            }
                        },
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[[-96.8, 32.7], [-96.7, 32.7], [-96.7, 32.8], [-96.8, 32.7]]]
                        }
                    }]
                }
                with mock.patch('hail_core.http_get_json', return_value=payload):
                    warn_res = fetch_active_nws_warnings(lat=32.7767, lon=-96.7970)
                    self.assertEqual(warn_res['count'], 1)
                    w = warn_res['warnings'][0]
                    self.assertEqual(w['hail_size_in'], 1.25)
                    self.assertIsNone(w['motion'])  # Malformed motion tag defaults safely to None

    def test_def15_fuzz_string_hail_size_coercion(self):
        """DEF-15: Verify describe_hail_size coerces string numbers and handles garbage inputs safely."""
        # 1. Valid string floats matching standard hail table
        self.assertIn("Quarter", describe_hail_size("1.0"))
        self.assertIn("Golf Ball", describe_hail_size("1.75"))
        self.assertIn("Baseball", describe_hail_size("2.75"))
        self.assertIn("Softball+", describe_hail_size("5.0"))

        # 2. Zero and negative string numbers
        self.assertEqual(describe_hail_size("0"), "None")
        self.assertEqual(describe_hail_size("0.0"), "None")
        self.assertEqual(describe_hail_size("-1.5"), "None")

        # 3. Garbage strings, NaNs, and infinities
        self.assertEqual(describe_hail_size("unknown"), "None")
        self.assertEqual(describe_hail_size("N/A"), "None")
        self.assertEqual(describe_hail_size(""), "None")
        self.assertEqual(describe_hail_size("nan"), "None")
        self.assertEqual(describe_hail_size("inf"), "None")

        # 4. Non-string, non-numeric types
        self.assertEqual(describe_hail_size(None), "None")
        self.assertEqual(describe_hail_size([]), "None")
        self.assertEqual(describe_hail_size({}), "None")

    def test_def16_fuzz_open_meteo_string_and_malformed_physics(self):
        """DEF-16: Verify Open-Meteo payload with string floats and corrupted arrays parses cleanly."""
        payload = {
            "current": {
                "temperature_2m": "24.5",
                "relative_humidity_2m": "72",
                "precipitation": "0.0",
                "wind_speed_10m": "15.0",
                "wind_gusts_10m": "28.0",
                "wind_direction_10m": "180",
                "surface_pressure": "1010.5",
                "weather_code": "3"
            },
            "hourly": {
                "cape": ["2200", "2400", "invalid_cape"],
                "lifted_index": ["-4.5", "-5.2", "bad_li"],
                "convective_inhibition": ["-15", "-20"],
                "freezing_level_height": ["3600", "3550", "corrupt_fz"]
            }
        }
        with mock.patch('hail_core.http_get_json', return_value=payload):
            res = fetch_open_meteo_convective(32.7767, -96.7970)
            self.assertIsInstance(res, dict)
            self.assertEqual(res['temperature_c'], 24.5)
            self.assertEqual(res['temperature_f'], 76.1)
            self.assertEqual(res['wind_speed_mph'], 9.3)
            self.assertEqual(res['freezing_level_ft'], 11811)
            self.assertEqual(res['hail_survival_rating'], 'MODERATE')
            self.assertGreaterEqual(res['hail_potential_index'], 40)
            self.assertLessEqual(res['hail_potential_index'], 100)

    def test_def17_fuzz_lsr_and_multi_sensor_non_float_attributes(self):
        """DEF-17: Verify evaluate_hail_risk coerces string numbers and ignores garbage across all sensor types."""
        alerts = [{'event': 'Severe Thunderstorm Warning', 'hail_size_in': '1.75'}]
        reports = [
            {'distance_miles': '3.5', 'hail_size_in': '1.75', 'age_hours': '0.8', 'is_mping': True},
            {'distance_miles': 'invalid', 'hail_size_in': 'bad', 'age_hours': 'old'}  # Non-numeric garbage
        ]
        convective = {
            'cape_j_kg': '2200',
            'peak_cape_today': '2600',
            'lifted_index': '-4.5',
            'freezing_level_ft': '9200'
        }
        reg_warnings = [{
            'event': 'Severe Thunderstorm Warning',
            'distance_miles': '10.5',
            'hail_size_in': '1.50',
            'motion': {'eta_mins': '25'}
        }]

        assessment = evaluate_hail_risk(alerts, reports, convective, regional_warnings=reg_warnings)
        self.assertIsInstance(assessment, dict)
        self.assertGreaterEqual(assessment['score'], 70)
        self.assertIn(assessment['level'], ['WARNING', 'EMERGENCY'])
        self.assertEqual(assessment['max_hail_inches'], 1.75)
        self.assertTrue(any("mPING" in r for r in assessment['reasons']))
        self.assertTrue(any("3.5 mi" in r for r in assessment['reasons']))  # Formatting {dist:.1f} succeeded

    def test_def18_national_hotspots_upstream_outage_null_response(self):
        """DEF-18: Verify fetch_active_national_hotspots safely returns 4 fallback hotspots during feed outage."""
        # 1. Direct core function test with None return value (outage)
        with mock.patch('hail_core.http_get_json', return_value=None):
            hotspots = fetch_active_national_hotspots()
            self.assertIsInstance(hotspots, list)
            self.assertEqual(len(hotspots), 4)
            areas = [h['area'] for h in hotspots]
            self.assertIn("Dallas / Fort Worth, TX", areas)
            self.assertIn("Oklahoma City, OK", areas)
            self.assertIn("Denver, CO", areas)
            self.assertIn("Wichita, KS", areas)
            for h in hotspots:
                self.assertIn('latitude', h)
                self.assertIn('longitude', h)
                self.assertIn('event', h)
                self.assertIn('hail_label', h)

        # 2. Direct core function test with empty dict return value
        with mock.patch('hail_core.http_get_json', return_value={}):
            hotspots_empty = fetch_active_national_hotspots()
            self.assertEqual(len(hotspots_empty), 4)

    def test_def18_api_hotspots_outage_fallback_endpoint(self):
        """DEF-18: Verify /api/hotspots HTTP endpoint returns HTTP 200 and fallback hotspots when NWS is down."""
        with mock.patch('hail_core.http_get_json', return_value=None):
            status, headers, content = self.get_url("/api/hotspots")
            self.assertEqual(status, 200)
            data = json.loads(content.decode('utf-8'))
            self.assertIn('hotspots', data)
            self.assertEqual(len(data['hotspots']), 4)
            self.assertEqual(data['hotspots'][0]['area'], 'Dallas / Fort Worth, TX')

    def test_def19_offline_determinism_socket_firewall(self):
        """DEF-19: Strict transport-layer firewall ensuring zero live external network connections occur."""
        orig_connect = socket.socket.connect
        blocked_calls = []

        def firewall_connect(s_self, address):
            host = address[0] if isinstance(address, tuple) and len(address) > 0 else str(address)
            # Allow loopback connections to the local integration test server
            if host in ('127.0.0.1', 'localhost', '::1'):
                return orig_connect(s_self, address)
            blocked_calls.append(address)
            raise AssertionError(f"CRITICAL: Unmocked live external network call attempted to {address}!")

        with mock.patch.object(socket.socket, 'connect', firewall_connect):
            # 1. Full multi-sensor assessment
            assessment = perform_full_assessment(32.7767, -96.7970, radius_miles=45)
            self.assertIn('assessment', assessment)
            self.assertIn('score', assessment['assessment'])

            # 2. Active regional warnings
            warn_res = fetch_active_nws_warnings(lat=32.7767, lon=-96.7970)
            self.assertIn('warnings', warn_res)

            # 3. National hotspots
            hotspots = fetch_active_national_hotspots()
            self.assertIsInstance(hotspots, list)

            # 4. SPC convective outlook
            outlook = fetch_spc_day1_outlook()
            self.assertIn('categorical', outlook)

            # 5. RainViewer radar metadata
            radar = fetch_rainviewer_radar()
            self.assertIn('frames', radar)

            # 6. Verify zero external socket calls were attempted
            self.assertEqual(len(blocked_calls), 0, f"External network calls detected: {blocked_calls}")

    def test_def19_offline_mock_coverage_completeness(self):
        """DEF-19: Verify every external weather API endpoint has an explicit offline mock fixture."""
        expected_endpoints = [
            "https://api.weather.gov/alerts/active",
            "https://mesonet.agron.iastate.edu/geojson/lsr.py?hours=168",
            "https://mesonet.agron.iastate.edu/geojson/sbw.geojson",
            "https://api.open-meteo.com/v1/forecast?latitude=32.7767&longitude=-96.7970",
            "https://api.rainviewer.com/public/weather-maps.json",
            "https://www.spc.noaa.gov/products/outlook/day1otlk_cat.lyr.geojson",
            "https://www.spc.noaa.gov/products/outlook/day1otlk_hail.lyr.geojson",
            "https://nominatim.openstreetmap.org/search?q=Dallas&format=json&limit=1",
            "https://nominatim.openstreetmap.org/reverse?lat=32.7767&lon=-96.7970&format=json"
        ]
        for url in expected_endpoints:
            with self.subTest(url=url):
                req = urllib.request.Request(url)
                resp = mock_urlopen(req)
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode('utf-8'))
                self.assertIsNotNone(data)

    def test_expanded_ui_xss_and_csp_compliance(self):
        """Verify static/app.js escapes radar tower icon, range rings, SPC tooltips, and has no inline onclick."""
        with open(os.path.join(STATIC_DIR, 'app.js'), 'r', encoding='utf-8') as f:
            js = f.read()

        # 1. escapeHtml handles backticks
        self.assertIn('.replace(/`/g, "&#96;")', js)

        # 2. Radar tower beacon title escapes st.icao and st.name
        self.assertIn('escapeHtml(st.icao)', js)
        self.assertIn('escapeHtml(st.name)', js)

        # 3. Radar range ring label escapes st.icao
        self.assertIn('escapeHtml(st.icao)} ${escapeHtml(r.label)}', js)

        # 4. SPC Outlook tooltip escapes p.LABEL and p.LABEL2
        self.assertIn('escapeHtml(p.LABEL', js)
        self.assertIn('escapeHtml(p.LABEL2', js)

        # 5. Zero inline onclick handlers (CSP compliance)
        self.assertNotIn('onclick=', js)

    def test_server_route_empty_fallbacks(self):
        """Verify route handlers handle core functions returning None, empty lists, or malformed query dicts."""
        # 1. /api/hotspots when core returns None
        with mock.patch('server.fetch_active_national_hotspots', return_value=None):
            status, headers, content = self.get_url("/api/hotspots")
            self.assertEqual(status, 200)
            data = json.loads(content.decode())
            self.assertIsInstance(data['hotspots'], list)
            self.assertEqual(data['count'], 0)

        # 2. /api/nws/warnings when core returns None
        with mock.patch('server.fetch_active_nws_warnings', return_value=None):
            status, headers, content = self.get_url("/api/nws/warnings")
            self.assertEqual(status, 200)
            data = json.loads(content.decode())
            self.assertIsInstance(data['warnings'], list)
            self.assertEqual(data['count'], 0)

        # 3. /api/assess when core returns empty dict
        with mock.patch('server.perform_full_assessment', return_value={}):
            status, headers, content = self.get_url("/api/assess?lat=32.7&lon=-96.8")
            self.assertEqual(status, 200)
            data = json.loads(content.decode())
            self.assertIn('assessment', data)
            self.assertEqual(data['assessment']['level'], 'NONE')
            self.assertIsInstance(data['nws_alerts'], list)

        # 4. /api/search with malformed coordinates in upstream Nominatim item
        bad_nominatim = [
            {'display_name': 'Valid City', 'lat': '32.7767', 'lon': '-96.7970'},
            {'display_name': 'Corrupt City', 'lat': None, 'lon': 'abc'},
            {'display_name': 'Another Valid City', 'lat': '30.2672', 'lon': '-97.7431'}
        ]
        def custom_urlopen(req, *args, **kwargs):
            url = req.full_url if hasattr(req, 'full_url') else str(req)
            if f"127.0.0.1:{TEST_PORT}" in url or f"localhost:{TEST_PORT}" in url:
                return _ORIG_URLOPEN(req, *args, **kwargs)
            return MockResponse(json.dumps(bad_nominatim).encode())

        with mock.patch('urllib.request.urlopen', side_effect=custom_urlopen):
            status, headers, content = self.get_url("/api/search?q=Texas")
            self.assertEqual(status, 200)
            data = json.loads(content.decode())
            self.assertEqual(len(data), 2)  # Corrupt city skipped, valid cities preserved

    # =========================================================================
    # FEAT-01: Custom Hail Threat & Severe Threshold Alert Engine Tests
    # =========================================================================

    def test_feat01_threshold_parameter_validation(self):
        """FEAT-01: Parameter validation for /api/threat/threshold-check (lat, lon, min_hail, min_score, max_eta, radius)."""
        invalid_queries = [
            "/api/threat/threshold-check",                                # Missing lat and lon
            "/api/threat/threshold-check?lat=32.7",                       # Missing lon
            "/api/threat/threshold-check?lat=nan&lon=-96.8",             # NaN lat
            "/api/threat/threshold-check?lat=95.0&lon=-96.8",            # Out of bounds lat
            "/api/threat/threshold-check?lat=32.7&lon=190.0",            # Out of bounds lon
            "/api/threat/threshold-check?lat=32.7&lon=-96.8&min_hail=-1.0", # Negative min_hail
            "/api/threat/threshold-check?lat=32.7&lon=-96.8&min_hail=15.0", # min_hail > 10.0
            "/api/threat/threshold-check?lat=32.7&lon=-96.8&min_score=-5",  # Negative min_score
            "/api/threat/threshold-check?lat=32.7&lon=-96.8&min_score=150", # min_score > 100
            "/api/threat/threshold-check?lat=32.7&lon=-96.8&max_eta=0",     # max_eta < 1
            "/api/threat/threshold-check?lat=32.7&lon=-96.8&max_eta=500",   # max_eta > 360
            "/api/threat/threshold-check?lat=32.7&lon=-96.8&radius=-10",    # radius < 1
            "/api/threat/threshold-check?lat=32.7&lon=-96.8&radius=1000",   # radius > 500
        ]
        for q in invalid_queries:
            with self.subTest(query=q):
                try:
                    self.get_url(q)
                    self.fail(f"Expected HTTP 400 for invalid query {q}")
                except urllib.error.HTTPError as e:
                    self.assertEqual(e.code, 400)
                    body = e.read().decode('utf-8')
                    e.close()
                    data = json.loads(body)
                    self.assertIn('error', data)

    def test_feat01_threshold_evaluation_business_logic(self):
        """FEAT-01: Direct evaluation of evaluate_threat_threshold business logic under varying storm scenarios."""
        # Scenario 1: Triggered threat (large hail, severe score, storm within ETA window)
        mock_assess_severe = {
            'assessment': {
                'score': 85,
                'level': 'WARNING',
                'max_hail_inches': 1.75,
                'max_hail_label': 'Golf Ball Size (1.75 in)',
            },
            'regional_warnings': {
                'warnings': [{
                    'distance_miles': 15.0,
                    'motion': {'eta_mins': 22}
                }]
            },
            'nws_alerts': []
        }
        with mock.patch('hail_core.perform_full_assessment', return_value=mock_assess_severe):
            res = evaluate_threat_threshold(32.7767, -96.7970, min_hail=1.00, min_score=70, max_eta=45)
            self.assertEqual(res['status'], 'ok')
            self.assertTrue(res['evaluation']['triggered'])
            self.assertTrue(res['evaluation']['hail_threshold_met'])
            self.assertTrue(res['evaluation']['score_threshold_met'])
            self.assertTrue(res['evaluation']['eta_threshold_met'])
            self.assertIn("CRITICAL ACTION REQUIRED", res['directive'])
            self.assertEqual(res['current_metrics']['nearest_storm_eta_mins'], 22)
            self.assertEqual(len(res['evaluation']['matched_reasons']), 3)

        # Scenario 2: Suppressed by ETA (storm is 75 mins away, user threshold is max_eta=30)
        mock_assess_far = {
            'assessment': {
                'score': 85,
                'level': 'WARNING',
                'max_hail_inches': 2.00,
                'max_hail_label': 'Hen Egg Size (2.00 in)'
            },
            'regional_warnings': {
                'warnings': [{
                    'distance_miles': 45.0,
                    'motion': {'eta_mins': 75}
                }]
            },
            'nws_alerts': []
        }
        with mock.patch('hail_core.perform_full_assessment', return_value=mock_assess_far):
            res_far = evaluate_threat_threshold(32.7767, -96.7970, min_hail=1.00, min_score=70, max_eta=30)
            self.assertFalse(res_far['evaluation']['triggered'])
            self.assertFalse(res_far['evaluation']['eta_threshold_met'])
            self.assertIn("MONITORING ACTIVE", res_far['directive'])

        # Scenario 3: Suppressed by hail size (0.50" hail vs user min_hail=1.50" and min_score=70)
        mock_assess_mild = {
            'assessment': {
                'score': 45,
                'level': 'WATCH',
                'max_hail_inches': 0.50,
                'max_hail_label': 'Marble Size (0.50 in)'
            },
            'regional_warnings': {'warnings': []},
            'nws_alerts': []
        }
        with mock.patch('hail_core.perform_full_assessment', return_value=mock_assess_mild):
            res_mild = evaluate_threat_threshold(32.7767, -96.7970, min_hail=1.50, min_score=70, max_eta=45)
            self.assertFalse(res_mild['evaluation']['triggered'])
            self.assertFalse(res_mild['evaluation']['hail_threshold_met'])
            self.assertFalse(res_mild['evaluation']['score_threshold_met'])

        # Scenario 4: Direct warning zone overhead (point alerts has active warning)
        mock_assess_overhead = {
            'assessment': {
                'score': 75,
                'level': 'WARNING',
                'max_hail_inches': 1.00,
                'max_hail_label': 'Quarter Size (1.00 in)'
            },
            'regional_warnings': {'warnings': []},
            'nws_alerts': [{'event': 'Severe Thunderstorm Warning', 'hail_size_in': 1.00}]
        }
        with mock.patch('hail_core.perform_full_assessment', return_value=mock_assess_overhead):
            res_overhead = evaluate_threat_threshold(32.7767, -96.7970, min_hail=1.00, min_score=70, max_eta=45)
            self.assertTrue(res_overhead['evaluation']['triggered'])
            self.assertTrue(res_overhead['evaluation']['eta_threshold_met'])

    def test_feat01_threshold_endpoint_integration(self):
        """FEAT-01: Integration test for GET /api/threat/threshold-check HTTP response schema."""
        status, headers, content = self.get_url("/api/threat/threshold-check?lat=32.7767&lon=-96.7970&min_hail=1.0&min_score=70&max_eta=45")
        self.assertEqual(status, 200)
        self.assertIn("application/json", headers.get('Content-Type'))
        data = json.loads(content.decode('utf-8'))
        self.assertEqual(data.get('status'), 'ok')
        self.assertIn('timestamp', data)
        self.assertIn('rule_criteria', data)
        self.assertIn('current_metrics', data)
        self.assertIn('evaluation', data)
        self.assertIn('directive', data)
        eval_obj = data['evaluation']
        for k in ['triggered', 'hail_threshold_met', 'score_threshold_met', 'eta_threshold_met', 'matched_reasons']:
            self.assertIn(k, eval_obj)

    def test_feat01_threshold_offline_outage_fallback(self):
        """FEAT-01: Verify threshold check returns HTTP 200 and triggered=False when weather sensors fail."""
        with mock.patch('hail_core.perform_full_assessment', return_value={}):
            status, headers, content = self.get_url("/api/threat/threshold-check?lat=32.7767&lon=-96.7970")
            self.assertEqual(status, 200)
            data = json.loads(content.decode('utf-8'))
            self.assertEqual(data['status'], 'ok')
            self.assertFalse(data['evaluation']['triggered'])
            self.assertIn("MONITORING ACTIVE", data['directive'])

    def test_feat01_threshold_ui_and_audio_gating(self):
        """FEAT-01: Verify static frontend contains threshold settings UI modal, badge, and audio gating logic."""
        with open(os.path.join(STATIC_DIR, 'index.html'), 'r', encoding='utf-8') as f:
            html = f.read()
        self.assertIn('id="threshold-cfg-btn"', html)
        self.assertIn('id="threshold-modal"', html)
        self.assertIn('id="active-rule-badge"', html)
        self.assertIn('id="cfg-min-hail"', html)
        self.assertIn('id="cfg-min-score"', html)
        self.assertIn('id="cfg-max-eta"', html)

        with open(os.path.join(STATIC_DIR, 'app.js'), 'r', encoding='utf-8') as f:
            js = f.read()
        self.assertIn('/api/threat/threshold-check', js)
        self.assertIn('thresholdConfig', js)
        self.assertIn('updateActiveRuleBadge', js)

    # =========================================================================
    # FEAT-02: Bounding Box & Transit Corridor Hail Threat Scanner Tests
    # =========================================================================

    def test_feat02_bbox_parameter_validation(self):
        """FEAT-02: Parameter validation for /api/hail/bbox (min_lat, min_lon, max_lat, max_lon, hours)."""
        invalid_bbox_queries = [
            "/api/hail/bbox",                                                     # Missing all params
            "/api/hail/bbox?min_lat=32.5&min_lon=-97.5",                         # Incomplete params
            "/api/hail/bbox?min_lat=35.0&min_lon=-97.5&max_lat=32.0&max_lon=-96.5", # Inverted latitude (min > max)
            "/api/hail/bbox?min_lat=32.0&min_lon=-96.5&max_lat=35.0&max_lon=-97.5", # Inverted longitude (min > max)
            "/api/hail/bbox?min_lat=-95.0&min_lon=-97.5&max_lat=35.0&max_lon=-96.5",# Out of bounds min_lat
            "/api/hail/bbox?min_lat=32.0&min_lon=-190.0&max_lat=35.0&max_lon=-96.5",# Out of bounds min_lon
            "/api/hail/bbox?min_lat=nan&min_lon=-97.5&max_lat=35.0&max_lon=-96.5",   # NaN coordinate
            "/api/hail/bbox?min_lat=32.0&min_lon=-97.5&max_lat=35.0&max_lon=-96.5&hours=-1", # Negative hours
            "/api/hail/bbox?min_lat=32.0&min_lon=-97.5&max_lat=35.0&max_lon=-96.5&hours=1000",# Hours > 720
        ]
        for q in invalid_bbox_queries:
            with self.subTest(query=q):
                try:
                    self.get_url(q)
                    self.fail(f"Expected HTTP 400 for invalid query {q}")
                except urllib.error.HTTPError as e:
                    self.assertEqual(e.code, 400)
                    body = e.read().decode('utf-8')
                    e.close()
                    data = json.loads(body)
                    self.assertIn('error', data)

    def test_feat02_bbox_spatial_intersection_and_metrics(self):
        """FEAT-02: Direct unit tests of geospatial containment and scan_hail_bbox aggregation."""
        # 1. Point containment test
        self.assertTrue(is_point_in_bbox(33.0, -97.0, 32.0, -98.0, 34.0, -96.0))
        self.assertFalse(is_point_in_bbox(35.0, -97.0, 32.0, -98.0, 34.0, -96.0))
        self.assertFalse(is_point_in_bbox(None, -97.0, 32.0, -98.0, 34.0, -96.0))

        # 2. Polygon intersection test
        poly_geom = {
            'type': 'Polygon',
            'coordinates': [[
                [-97.2, 32.8],
                [-96.8, 32.8],
                [-96.8, 33.2],
                [-97.2, 33.2],
                [-97.2, 32.8]
            ]]
        }
        self.assertTrue(is_geometry_intersecting_bbox(poly_geom, 32.5, -97.5, 33.5, -96.5))
        self.assertFalse(is_geometry_intersecting_bbox(poly_geom, 35.0, -97.5, 36.0, -96.5))

        # 3. Direct scan_hail_bbox aggregation test with mock feeds
        mock_warnings = {
            'warnings': [
                {
                    'id': 'warn-in-corridor',
                    'event': 'Severe Thunderstorm Warning',
                    'category': 'SEVERE_TSTORM',
                    'severity': 'Severe',
                    'hail_size_in': 1.75,
                    'center_lat': 33.0,
                    'center_lon': -97.0,
                    'geometry': poly_geom
                },
                {
                    'id': 'warn-out-corridor',
                    'event': 'Flash Flood Warning',
                    'category': 'FLASH_FLOOD',
                    'severity': 'Severe',
                    'hail_size_in': 0.0,
                    'center_lat': 38.0,
                    'center_lon': -97.0,
                    'geometry': {
                        'type': 'Polygon',
                        'coordinates': [[[-97.2, 37.8], [-96.8, 37.8], [-96.8, 38.2], [-97.2, 38.2], [-97.2, 37.8]]]
                    }
                }
            ]
        }
        mock_reports = [
            {'latitude': 33.1, 'longitude': -97.1, 'hail_size_in': 2.0, 'source_type': 'Spotter', 'valid': '2026-10-03T18:00:00Z'},
            {'latitude': 32.9, 'longitude': -96.9, 'hail_size_in': 1.0, 'source_type': 'mPING', 'valid': '2026-10-03T18:00:00Z'},
            {'latitude': 39.0, 'longitude': -97.0, 'hail_size_in': 2.5, 'source_type': 'Spotter', 'valid': '2026-10-03T18:00:00Z'} # Outside
        ]

        with mock.patch('hail_core.fetch_active_nws_warnings', return_value=mock_warnings), \
             mock.patch('hail_core.fetch_iem_lsr_reports', return_value=mock_reports):
            res = scan_hail_bbox(32.5, -97.5, 33.5, -96.5, min_hail=0.0, hours=24)
            self.assertEqual(res['status'], 'ok')
            self.assertEqual(res['corridor_metrics']['total_warnings'], 1)
            self.assertEqual(res['corridor_metrics']['total_hail_reports'], 2)
            self.assertEqual(res['corridor_metrics']['max_hail_inches'], 2.0)
            self.assertIn("Hen Egg", res['corridor_metrics']['max_hail_label'])
            self.assertEqual(res['corridor_metrics']['highest_severity'], 'DESTRUCTIVE_HAIL')
            self.assertGreaterEqual(res['corridor_metrics']['composite_corridor_score'], 75)
            self.assertEqual(len(res['geojson']['features']), 3) # 1 warning + 2 reports

    def test_feat02_bbox_endpoint_integration(self):
        """FEAT-02: Integration test for GET /api/hail/bbox HTTP endpoint."""
        status, headers, content = self.get_url("/api/hail/bbox?min_lat=32.5&min_lon=-97.6&max_lat=35.6&max_lon=-96.6")
        self.assertEqual(status, 200)
        self.assertIn("application/json", headers.get('Content-Type'))
        data = json.loads(content.decode('utf-8'))
        self.assertEqual(data.get('status'), 'ok')
        self.assertIn('bbox', data)
        self.assertIn('corridor_metrics', data)
        self.assertIn('contained_warnings', data)
        self.assertIn('contained_reports', data)
        self.assertIn('geojson', data)
        metrics = data['corridor_metrics']
        for k in ['total_warnings', 'total_hail_reports', 'max_hail_inches', 'max_hail_label', 'highest_severity', 'composite_corridor_score']:
            self.assertIn(k, metrics)

    def test_feat02_bbox_empty_corridor_offline_fallback(self):
        """FEAT-02: Verify /api/hail/bbox returns HTTP 200 with 0 metrics during empty/quiet conditions."""
        with mock.patch('hail_core.fetch_active_nws_warnings', return_value={'warnings': []}), \
             mock.patch('hail_core.fetch_iem_lsr_reports', return_value=[]):
            status, headers, content = self.get_url("/api/hail/bbox?min_lat=30.0&min_lon=-100.0&max_lat=31.0&max_lon=-99.0")
            self.assertEqual(status, 200)
            data = json.loads(content.decode('utf-8'))
            self.assertEqual(data['corridor_metrics']['total_warnings'], 0)
            self.assertEqual(data['corridor_metrics']['total_hail_reports'], 0)
            self.assertEqual(data['corridor_metrics']['composite_corridor_score'], 0)
            self.assertEqual(data['corridor_metrics']['highest_severity'], 'NONE')

    def test_feat02_bbox_ui_drawer_and_presets(self):
        """FEAT-02: Verify static frontend contains corridor drawer, preset highway corridor buttons, and JS scanner."""
        with open(os.path.join(STATIC_DIR, 'index.html'), 'r', encoding='utf-8') as f:
            html = f.read()
        self.assertIn('id="btn-bbox-mode"', html)
        self.assertIn('id="corridor-drawer"', html)
        self.assertIn('I-35 Texas-Oklahoma Corridor', html)
        self.assertIn('I-70 Colorado-Kansas', html)
        self.assertIn('I-80 Nebraska-Iowa', html)

        with open(os.path.join(STATIC_DIR, 'app.js'), 'r', encoding='utf-8') as f:
            js = f.read()
        self.assertIn('/api/hail/bbox', js)
        self.assertIn('loadCorridorBbox', js)
        self.assertIn('corridorDrawer', js)

    # =========================================================================
    # FEAT-03: Operations Threat Dossier Export Engine Tests
    # =========================================================================

    def test_feat03_dossier_parameter_validation(self):
        """FEAT-03: Parameter validation for /api/export/threat-dossier (lat, lon, radius, format)."""
        invalid_dossier_queries = [
            "/api/export/threat-dossier",                          # Missing coords
            "/api/export/threat-dossier?lat=32.7",                 # Missing lon
            "/api/export/threat-dossier?lat=nan&lon=-96.8",        # NaN
            "/api/export/threat-dossier?lat=95.0&lon=-96.8",       # Out of bounds lat
            "/api/export/threat-dossier?lat=32.7&lon=-96.8&radius=-5", # Negative radius
            "/api/export/threat-dossier?lat=32.7&lon=-96.8&format=xml", # Unsupported format
            "/api/export/threat-dossier?lat=32.7&lon=-96.8&format=pdf", # Unsupported format
        ]
        for q in invalid_dossier_queries:
            with self.subTest(query=q):
                try:
                    self.get_url(q)
                    self.fail(f"Expected HTTP 400 for invalid query {q}")
                except urllib.error.HTTPError as e:
                    self.assertEqual(e.code, 400)
                    body = e.read().decode('utf-8')
                    e.close()
                    data = json.loads(body)
                    self.assertIn('error', data)

    def test_feat03_dossier_text_format(self):
        """FEAT-03: Verify formatted ASCII Plaintext Operations Threat Briefing export."""
        status, headers, content = self.get_url("/api/export/threat-dossier?lat=32.7767&lon=-96.7970&format=text")
        self.assertEqual(status, 200)
        self.assertIn("text/plain", headers.get('Content-Type'))
        text = content.decode('utf-8')
        self.assertIn("HAILWARN SEVERE WEATHER OPERATIONS THREAT DOSSIER", text)
        self.assertIn("DOSSIER ID:      HW-", text)
        self.assertIn("COORDINATES:     32.7767° N, 96.7970° W", text)
        self.assertIn("HAIL RISK SCORE:", text)
        self.assertIn("THREAT SEVERITY LEVEL:", text)
        self.assertIn("ACTIVE NWS WARNING BULLETINS", text)
        self.assertIn("VERIFIED GROUND TRUTH OBSERVATIONS", text)
        self.assertIn("ATMOSPHERIC CONVECTIVE SOUNDING PROFILE", text)
        self.assertIn("PROTECTIVE ACTION CHECKLIST", text)

    def test_feat03_dossier_csv_format(self):
        """FEAT-03: Verify RFC 4180 CSV Spreadsheet Operations Threat Dossier export."""
        status, headers, content = self.get_url("/api/export/threat-dossier?lat=32.7767&lon=-96.7970&format=csv")
        self.assertEqual(status, 200)
        self.assertIn("text/csv", headers.get('Content-Type'))
        content_disp = headers.get('Content-Disposition', '')
        self.assertIn('attachment', content_disp)
        self.assertIn('filename="hailwarn_dossier_', content_disp)

        csv_text = content.decode('utf-8')
        self.assertIn("# HAILWARN OPERATIONS THREAT DOSSIER - ASSESSMENT KEY METRICS", csv_text)
        self.assertIn("# DETAILED WARNINGS AND GROUND OBSERVATIONS LOG", csv_text)

        # Parse with standard library csv.reader to ensure RFC 4180 validity
        reader = csv.reader(csv_text.splitlines())
        rows = list(reader)
        self.assertGreaterEqual(len(rows), 5)
        # Check assessment metrics row
        metrics_header = rows[1]
        self.assertIn("Dossier ID", metrics_header)
        self.assertIn("Threat Score", metrics_header)
        self.assertIn("Max Hail Inches", metrics_header)

    def test_feat03_dossier_json_format(self):
        """FEAT-03: Verify structured JSON Operations Threat Dossier export."""
        status, headers, content = self.get_url("/api/export/threat-dossier?lat=32.7767&lon=-96.7970&format=json")
        self.assertEqual(status, 200)
        self.assertIn("application/json", headers.get('Content-Type'))
        data = json.loads(content.decode('utf-8'))
        self.assertEqual(data.get('status'), 'ok')
        self.assertIn('dossier_id', data)
        self.assertIn('timestamp', data)
        self.assertIn('location', data)
        self.assertIn('assessment', data)
        self.assertIn('contained_warnings', data)
        self.assertIn('contained_reports', data)
        self.assertIn('convective_data', data)
        self.assertIn('geojson', data)

    def test_feat03_dossier_offline_resilience(self):
        """FEAT-03: Verify dossier export safely completes without crashing when external APIs fail."""
        with mock.patch('hail_core.perform_full_assessment', return_value={}):
            for fmt in ['text', 'csv', 'json']:
                with self.subTest(format=fmt):
                    status, headers, content = self.get_url(f"/api/export/threat-dossier?lat=32.7767&lon=-96.7970&format={fmt}")
                    self.assertEqual(status, 200)
                    self.assertGreater(len(content), 0)

    def test_feat03_dossier_ui_buttons(self):
        """FEAT-03: Verify static frontend briefing modal contains CSV, plaintext, and JSON export buttons."""
        with open(os.path.join(STATIC_DIR, 'index.html'), 'r', encoding='utf-8') as f:
            html = f.read()
        self.assertIn('id="copy-briefing-btn"', html)
        self.assertIn('id="download-briefing-btn"', html)
        self.assertIn('id="download-csv-btn"', html)
        self.assertIn('id="json-briefing-btn"', html)

        with open(os.path.join(STATIC_DIR, 'app.js'), 'r', encoding='utf-8') as f:
            js = f.read()
        self.assertIn('/api/export/threat-dossier', js)
        self.assertIn('download-csv-btn', js)


if __name__ == '__main__':
    unittest.main()


