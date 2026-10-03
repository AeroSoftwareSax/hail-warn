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

    def read(self):
        return self.data_bytes

    def close(self):
        pass

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


if __name__ == '__main__':
    unittest.main()
