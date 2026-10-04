#!/usr/bin/env python3
"""
HailWarn Server - High-performance Python Standard Library Backend
Serves the Hail Warning System web application and REST API endpoints.
Zero external dependencies required.
"""

import sys
import os
import json
import math
import time
import urllib.parse
import urllib.request
import ssl
from http.server import HTTPServer, SimpleHTTPRequestHandler
from socketserver import ThreadingMixIn

from hail_core import (
    perform_full_assessment,
    fetch_active_national_hotspots,
    fetch_rainviewer_radar,
    fetch_active_nws_warnings,
    fetch_spc_day1_outlook,
    load_nexrad_stations,
    find_nearest_nexrad,
    evaluate_threat_threshold,
    scan_hail_bbox,
    generate_threat_dossier,
    USER_AGENT,
    SSL_CTX
)

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')

def get_query_param(query, key, default=None):
    """Safely extract the first value from a parse_qs dictionary without IndexError."""
    vals = query.get(key)
    if isinstance(vals, list) and len(vals) > 0:
        return vals[0]
    return default

def validate_coordinates(lat_raw, lon_raw):
    """Validate and return float (lat, lon) within bounds [-90, 90] and [-180, 180].
    Rejects None, NaN, Inf, and non-numeric values.
    """
    if lat_raw is None or lon_raw is None:
        raise ValueError("Latitude and longitude are required.")
    try:
        lat = float(lat_raw)
        lon = float(lon_raw)
    except (TypeError, ValueError):
        raise ValueError("Coordinates must be valid decimal numbers.")
    if math.isnan(lat) or math.isinf(lat) or math.isnan(lon) or math.isinf(lon):
        raise ValueError("Coordinates cannot be NaN or infinite.")
    if not (-90.0 <= lat <= 90.0):
        raise ValueError(f"Latitude out of bounds [-90, 90]: {lat}")
    if not (-180.0 <= lon <= 180.0):
        raise ValueError(f"Longitude out of bounds [-180, 180]: {lon}")
    return lat, lon

def validate_search_query(q_raw, max_len=200):
    """Validate free-text search input before using it in outbound requests."""
    q = str(q_raw if q_raw is not None else '').strip()
    if not q:
        return ''
    if len(q) > max_len:
        q = q[:max_len]
    allowed_punct = set(".,-' ")
    for ch in q:
        if not (ch.isalnum() or ch in allowed_punct):
            raise ValueError("Search query contains invalid characters.")
    return q

def validate_radius(radius_raw, default=45.0):
    if radius_raw is None:
        return default
    try:
        r = float(radius_raw)
    except (TypeError, ValueError):
        raise ValueError("Radius must be a valid decimal number.")
    if math.isnan(r) or math.isinf(r) or r < 1.0 or r > 500.0:
        raise ValueError(f"Radius out of bounds [1.0, 500.0]: {r}")
    return r

def validate_hours(hours_raw, default=168):
    if hours_raw is None:
        return default
    try:
        h = int(hours_raw)
    except (TypeError, ValueError):
        raise ValueError("Hours must be a valid integer.")
    if h < 1 or h > 720:
        raise ValueError(f"Hours out of bounds [1, 720]: {h}")
    return h

def validate_bbox(min_lat_raw, min_lon_raw, max_lat_raw, max_lon_raw):
    """Validate bounding box coordinate boundaries.
    Ensures -90 <= min_lat < max_lat <= 90 and -180 <= min_lon < max_lon <= 180.
    Rejects NaN, Inf, and non-numeric values.
    """
    if min_lat_raw is None or min_lon_raw is None or max_lat_raw is None or max_lon_raw is None:
        raise ValueError("All bounding box parameters (min_lat, min_lon, max_lat, max_lon) are required.")
    try:
        min_lat = float(min_lat_raw)
        min_lon = float(min_lon_raw)
        max_lat = float(max_lat_raw)
        max_lon = float(max_lon_raw)
    except (TypeError, ValueError):
        raise ValueError("Bounding box coordinates must be valid decimal numbers.")
    if math.isnan(min_lat) or math.isinf(min_lat) or math.isnan(min_lon) or math.isinf(min_lon) or \
       math.isnan(max_lat) or math.isinf(max_lat) or math.isnan(max_lon) or math.isinf(max_lon):
        raise ValueError("Bounding box coordinates cannot be NaN or infinite.")
    if not (-90.0 <= min_lat <= 90.0):
        raise ValueError(f"min_lat out of bounds [-90, 90]: {min_lat}")
    if not (-90.0 <= max_lat <= 90.0):
        raise ValueError(f"max_lat out of bounds [-90, 90]: {max_lat}")
    if min_lat >= max_lat:
        raise ValueError(f"min_lat ({min_lat}) must be strictly less than max_lat ({max_lat}).")
    if not (-180.0 <= min_lon <= 180.0):
        raise ValueError(f"min_lon out of bounds [-180, 180]: {min_lon}")
    if not (-180.0 <= max_lon <= 180.0):
        raise ValueError(f"max_lon out of bounds [-180, 180]: {max_lon}")
    if min_lon >= max_lon:
        raise ValueError(f"min_lon ({min_lon}) must be strictly less than max_lon ({max_lon}).")
    return min_lat, min_lon, max_lat, max_lon

def validate_min_hail(min_hail_raw, default=0.0):
    if min_hail_raw is None:
        return default
    try:
        val = float(min_hail_raw)
    except (TypeError, ValueError):
        raise ValueError("min_hail must be a valid decimal number.")
    if math.isnan(val) or math.isinf(val) or val < 0.0 or val > 10.0:
        raise ValueError(f"min_hail out of bounds [0.0, 10.0]: {val}")
    return val

def validate_min_score(min_score_raw, default=70):
    if min_score_raw is None:
        return default
    try:
        val = int(min_score_raw)
    except (TypeError, ValueError):
        raise ValueError("min_score must be a valid integer.")
    if val < 0 or val > 100:
        raise ValueError(f"min_score out of bounds [0, 100]: {val}")
    return val

def validate_max_eta(max_eta_raw, default=45):
    if max_eta_raw is None:
        return default
    try:
        val = int(max_eta_raw)
    except (TypeError, ValueError):
        raise ValueError("max_eta must be a valid integer.")
    if val < 1 or val > 360:
        raise ValueError(f"max_eta out of bounds [1, 360]: {val}")
    return val

def validate_export_format(format_raw, default='text'):
    if format_raw is None:
        return default
    fmt = str(format_raw).strip().lower()
    if fmt not in ('text', 'csv', 'json'):
        raise ValueError(f"Invalid format '{fmt}'. Supported formats are 'text', 'csv', and 'json'.")
    return fmt

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Handle requests in separate threads for maximum throughput."""
    daemon_threads = True

class HailWarnRequestHandler(SimpleHTTPRequestHandler):
    """Custom request handler serving REST APIs and static dashboard assets."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        # API Endpoints
        if path == '/api/assess':
            self.handle_assess(query)
        elif path == '/api/nws/warnings':
            self.handle_nws_warnings(query)
        elif path == '/api/spc/outlook':
            self.handle_spc_outlook()
        elif path == '/api/hotspots':
            self.handle_hotspots()
        elif path == '/api/radar':
            self.handle_radar()
        elif path == '/api/nexrad/stations':
            self.handle_nexrad_stations(query)
        elif path == '/api/search':
            self.handle_search(query)
        elif path == '/api/reverse-geocode':
            self.handle_reverse_geocode(query)
        elif path == '/api/threat/threshold-check':
            self.handle_threat_threshold(query)
        elif path == '/api/hail/bbox':
            self.handle_hail_bbox(query)
        elif path == '/api/export/threat-dossier':
            self.handle_export_threat_dossier(query)
        elif path == '/api/health':
            self.send_json_response({'status': 'ok', 'app': 'HailWarn', 'version': '1.0'})
        else:
            # Fallback to static directory
            if path == '/' or path == '':
                self.path = '/index.html'
            super().do_GET()

    def do_OPTIONS(self):
        """Handle CORS preflight requests."""
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
        self.send_header('Access-Control-Max-Age', '86400')
        self.end_headers()

    def end_headers(self):
        """Inject strict security headers for all HTTP responses."""
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'strict-origin-when-cross-origin')
        self.send_header(
            'Content-Security-Policy',
            "default-src 'self'; "
            "script-src 'self' https://unpkg.com; "
            "style-src 'self' 'unsafe-inline' https://unpkg.com https://cdnjs.cloudflare.com https://fonts.googleapis.com; "
            "font-src https://cdnjs.cloudflare.com https://fonts.gstatic.com; "
            "img-src 'self' data: https:; "
            "connect-src 'self' https:;"
        )
        super().end_headers()

    def send_json_response(self, data, status_code=200):
        body = json.dumps(data, indent=2).encode('utf-8')
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.end_headers()
        self.wfile.write(body)

    def send_text_response(self, text, status_code=200, content_type='text/plain; charset=utf-8', headers=None):
        body = text.encode('utf-8')
        self.send_response(status_code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        if headers:
            for k, v in headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def handle_assess(self, query):
        lat_param = get_query_param(query, 'lat')
        lon_param = get_query_param(query, 'lon')
        radius_param = get_query_param(query, 'radius')
        hours_param = get_query_param(query, 'hours')

        try:
            lat, lon = validate_coordinates(lat_param, lon_param)
            radius = validate_radius(radius_param, default=45.0)
            hours = validate_hours(hours_param, default=168)
        except ValueError as e:
            self.send_json_response({'error': str(e)}, 400)
            return

        try:
            result = perform_full_assessment(lat, lon, radius_miles=radius, hours=hours)
            if not isinstance(result, dict) or not result:
                result = {}

            # Guarantee mandatory schema fields
            result.setdefault('timestamp', time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
            result.setdefault('location', {'latitude': lat, 'longitude': lon, 'radius_miles': radius, 'hours': hours})
            if not isinstance(result.get('assessment'), dict):
                result['assessment'] = {
                    'score': 0,
                    'level': 'NONE',
                    'badge_class': 'danger-none',
                    'max_hail_inches': 0.0,
                    'max_hail_label': 'None',
                    'estimated_dbz': '< 30 dBZ (Light Rain / Clear)',
                    'action_recommendation': 'NO ACTIVE HAIL THREAT: No severe hail signatures detected.',
                    'reasons': ['Zero active radar hail signatures.']
                }
            if not isinstance(result.get('nws_alerts'), list):
                result['nws_alerts'] = []
            if not isinstance(result.get('lsr_reports'), list):
                result['lsr_reports'] = []
            if not isinstance(result.get('convective_data'), dict):
                result['convective_data'] = {}
            if not isinstance(result.get('radar_metadata'), dict):
                result['radar_metadata'] = {'host': 'https://tilecache.rainviewer.com', 'frames': []}
            if not isinstance(result.get('regional_warnings'), dict):
                result['regional_warnings'] = {'warnings': [], 'geojson': {'type': 'FeatureCollection', 'features': []}, 'count': 0}
            if not isinstance(result.get('spc_outlook'), dict):
                result['spc_outlook'] = {'categorical': {'type': 'FeatureCollection', 'features': []}, 'hail': {'type': 'FeatureCollection', 'features': []}}

            self.send_json_response(result)
        except Exception as e:
            print(f"[Server Error] Assessment failed for {lat}, {lon}: {e}", file=sys.stderr)
            self.send_json_response({'error': 'Assessment failure. Please check server logs.'}, 500)

    def handle_nws_warnings(self, query):
        lat = None
        lon = None
        radius = 250.0
        try:
            lat_raw = get_query_param(query, 'lat')
            lon_raw = get_query_param(query, 'lon')
            if lat_raw is not None and lon_raw is not None:
                lat, lon = validate_coordinates(lat_raw, lon_raw)
            radius_raw = get_query_param(query, 'radius')
            if radius_raw is not None:
                radius = validate_radius(radius_raw, default=250.0)
        except ValueError as e:
            self.send_json_response({'error': str(e)}, 400)
            return

        try:
            data = fetch_active_nws_warnings(lat=lat, lon=lon, radius_miles=radius)
            if not isinstance(data, dict):
                data = {'warnings': [], 'geojson': {'type': 'FeatureCollection', 'features': []}, 'count': 0}
            else:
                if not isinstance(data.get('warnings'), list):
                    data['warnings'] = []
                if not isinstance(data.get('geojson'), dict):
                    data['geojson'] = {'type': 'FeatureCollection', 'features': []}
                data['count'] = len(data['warnings'])
            self.send_json_response(data)
        except Exception as e:
            print(f"[Server Error] Active NWS warnings failed: {e}", file=sys.stderr)
            self.send_json_response({'warnings': [], 'geojson': {'type': 'FeatureCollection', 'features': []}, 'count': 0})

    def handle_spc_outlook(self):
        try:
            data = fetch_spc_day1_outlook()
            if not isinstance(data, dict):
                data = {'categorical': {'type': 'FeatureCollection', 'features': []}, 'hail': {'type': 'FeatureCollection', 'features': []}}
            self.send_json_response(data)
        except Exception as e:
            print(f"[Server Error] SPC outlook failed: {e}", file=sys.stderr)
            self.send_json_response({'categorical': {'type': 'FeatureCollection', 'features': []}, 'hail': {'type': 'FeatureCollection', 'features': []}})

    def handle_hotspots(self):
        try:
            hotspots = fetch_active_national_hotspots()
            if not isinstance(hotspots, list):
                hotspots = []
            safe_hotspots = []
            for h in hotspots:
                if isinstance(h, dict) and 'latitude' in h and 'longitude' in h:
                    safe_hotspots.append(h)
            self.send_json_response({'hotspots': safe_hotspots, 'count': len(safe_hotspots)})
        except Exception as e:
            print(f"[Server Error] Hotspots query failed: {e}", file=sys.stderr)
            self.send_json_response({'hotspots': [], 'count': 0})

    def handle_radar(self):
        try:
            radar = fetch_rainviewer_radar()
            if not isinstance(radar, dict):
                radar = {'host': 'https://tilecache.rainviewer.com', 'frames': []}
            self.send_json_response(radar)
        except Exception as e:
            print(f"[Server Error] Radar query failed: {e}", file=sys.stderr)
            self.send_json_response({'host': 'https://tilecache.rainviewer.com', 'frames': []})

    def handle_nexrad_stations(self, query):
        lat = None
        lon = None
        try:
            lat_raw = get_query_param(query, 'lat')
            lon_raw = get_query_param(query, 'lon')
            if lat_raw is not None and lon_raw is not None:
                lat, lon = validate_coordinates(lat_raw, lon_raw)
        except ValueError as e:
            self.send_json_response({'error': str(e)}, 400)
            return

        try:
            stations = load_nexrad_stations()
            if not isinstance(stations, list):
                stations = []
            nearest = find_nearest_nexrad(lat, lon) if lat is not None and lon is not None else None
            self.send_json_response({
                'stations': stations,
                'nearest': nearest,
                'count': len(stations)
            })
        except Exception as e:
            print(f"[Server Error] NEXRAD stations lookup failed: {e}", file=sys.stderr)
            self.send_json_response({'stations': [], 'nearest': None, 'count': 0})

    def handle_search(self, query):
        q_raw = get_query_param(query, 'q', '')
        try:
            q = validate_search_query(q_raw, max_len=200)
        except ValueError as e:
            self.send_json_response({'error': str(e)}, 400)
            return
        if not q:
            self.send_json_response([])
            return

        base_url = "https://nominatim.openstreetmap.org/search"
        params = {
            'q': q,
            'format': 'json',
            'limit': '5',
            'addressdetails': '1'
        }
        url = f"{base_url}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=6, context=SSL_CTX) as resp:  # nosec B310
                raw_data = resp.read().decode('utf-8')
                data = json.loads(raw_data)
                if not isinstance(data, list):
                    self.send_json_response([])
                    return

                results = []
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    try:
                        lat_val = float(item.get('lat'))
                        lon_val = float(item.get('lon'))
                    except (TypeError, ValueError):
                        continue
                    if math.isnan(lat_val) or math.isinf(lat_val) or not (-90.0 <= lat_val <= 90.0):
                        continue
                    if math.isnan(lon_val) or math.isinf(lon_val) or not (-180.0 <= lon_val <= 180.0):
                        continue

                    results.append({
                        'display_name': str(item.get('display_name') or f"{lat_val:.3f}, {lon_val:.3f}"),
                        'lat': lat_val,
                        'lon': lon_val,
                        'type': str(item.get('type') or ''),
                        'importance': float(item.get('importance') or 0.0) if isinstance(item.get('importance'), (int, float)) else 0.0
                    })
                self.send_json_response(results)
        except urllib.error.HTTPError as e:
            e.close()
            print(f"[Server Error] Nominatim search failed: {e}", file=sys.stderr)
            self.send_json_response([])
        except Exception as e:
            print(f"[Server Error] Nominatim search failed: {e}", file=sys.stderr)
            self.send_json_response([])

    def handle_reverse_geocode(self, query):
        lat_param = get_query_param(query, 'lat')
        lon_param = get_query_param(query, 'lon')
        try:
            lat, lon = validate_coordinates(lat_param, lon_param)
        except ValueError:
            self.send_json_response({'display_name': 'Selected Coordinate', 'clean_name': 'Selected Coordinate'})
            return

        url = f"https://nominatim.openstreetmap.org/reverse?lat={lat:.4f}&lon={lon:.4f}&format=json&addressdetails=1"
        req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=6, context=SSL_CTX) as resp:  # nosec B310
                data = json.loads(resp.read().decode('utf-8'))
                addr = data.get('address', {})
                city = (
                    addr.get('city') or
                    addr.get('town') or
                    addr.get('village') or
                    addr.get('municipality') or
                    addr.get('hamlet') or
                    addr.get('county') or
                    data.get('name')
                )
                state = addr.get('state') or addr.get('country')
                if city and state:
                    clean_name = f"{city}, {state}"
                elif city:
                    clean_name = city
                elif data.get('display_name'):
                    clean_name = ", ".join(data.get('display_name').split(",")[:2])
                else:
                    clean_name = f"{lat:.3f}, {lon:.3f}"

                self.send_json_response({
                    'display_name': data.get('display_name', f"{lat:.3f}, {lon:.3f}"),
                    'clean_name': clean_name,
                    'city': city or clean_name,
                    'state': state or ''
                })
        except urllib.error.HTTPError as e:
            e.close()
            self.send_json_response({
                'display_name': f"{lat:.3f}, {lon:.3f}",
                'clean_name': f"{lat:.3f}, {lon:.3f}",
                'city': f"{lat:.3f}, {lon:.3f}",
                'state': ''
            })
        except Exception:
            self.send_json_response({
                'display_name': f"{lat:.3f}, {lon:.3f}",
                'clean_name': f"{lat:.3f}, {lon:.3f}",
                'city': f"{lat:.3f}, {lon:.3f}",
                'state': ''
            })

    def handle_threat_threshold(self, query):
        lat_param = get_query_param(query, 'lat')
        lon_param = get_query_param(query, 'lon')
        min_hail_param = get_query_param(query, 'min_hail')
        min_score_param = get_query_param(query, 'min_score')
        max_eta_param = get_query_param(query, 'max_eta')
        radius_param = get_query_param(query, 'radius')

        try:
            lat, lon = validate_coordinates(lat_param, lon_param)
            min_hail = validate_min_hail(min_hail_param, default=1.0)
            min_score = validate_min_score(min_score_param, default=70)
            max_eta = validate_max_eta(max_eta_param, default=45)
            radius = validate_radius(radius_param, default=45.0)
        except ValueError as e:
            self.send_json_response({'error': str(e)}, 400)
            return

        try:
            result = evaluate_threat_threshold(
                lat=lat,
                lon=lon,
                min_hail=min_hail,
                min_score=min_score,
                max_eta=max_eta,
                radius_miles=radius
            )
            if not isinstance(result, dict):
                result = {'status': 'ok', 'evaluation': {'triggered': False}}
            self.send_json_response(result)
        except Exception as e:
            print(f"[Server Error] Threat threshold evaluation failed for {lat}, {lon}: {e}", file=sys.stderr)
            self.send_json_response({'error': 'Threshold evaluation failure. Please check server logs.'}, 500)

    def handle_hail_bbox(self, query):
        min_lat_param = get_query_param(query, 'min_lat')
        min_lon_param = get_query_param(query, 'min_lon')
        max_lat_param = get_query_param(query, 'max_lat')
        max_lon_param = get_query_param(query, 'max_lon')
        min_hail_param = get_query_param(query, 'min_hail')
        hours_param = get_query_param(query, 'hours')

        try:
            min_lat, min_lon, max_lat, max_lon = validate_bbox(
                min_lat_param, min_lon_param, max_lat_param, max_lon_param
            )
            min_hail = validate_min_hail(min_hail_param, default=0.0)
            hours = validate_hours(hours_param, default=24)
        except ValueError as e:
            self.send_json_response({'error': str(e)}, 400)
            return

        try:
            result = scan_hail_bbox(
                min_lat=min_lat,
                min_lon=min_lon,
                max_lat=max_lat,
                max_lon=max_lon,
                min_hail=min_hail,
                hours=hours
            )
            if not isinstance(result, dict):
                result = {
                    'status': 'ok',
                    'bbox': {'min_lat': min_lat, 'min_lon': min_lon, 'max_lat': max_lat, 'max_lon': max_lon},
                    'corridor_metrics': {'total_warnings': 0, 'total_hail_reports': 0, 'composite_corridor_score': 0},
                    'contained_warnings': [],
                    'contained_reports': [],
                    'geojson': {'type': 'FeatureCollection', 'features': []}
                }
            self.send_json_response(result)
        except Exception as e:
            print(f"[Server Error] Hail bbox scan failed for [{min_lat}, {min_lon}, {max_lat}, {max_lon}]: {e}", file=sys.stderr)
            self.send_json_response({'error': 'Bounding box scan failure. Please check server logs.'}, 500)

    def handle_export_threat_dossier(self, query):
        lat_param = get_query_param(query, 'lat')
        lon_param = get_query_param(query, 'lon')
        radius_param = get_query_param(query, 'radius')
        format_param = get_query_param(query, 'format')

        try:
            lat, lon = validate_coordinates(lat_param, lon_param)
            radius = validate_radius(radius_param, default=45.0)
            export_fmt = validate_export_format(format_param, default='text')
        except ValueError as e:
            self.send_json_response({'error': str(e)}, 400)
            return

        try:
            result = generate_threat_dossier(
                lat=lat,
                lon=lon,
                radius_miles=radius,
                format_type=export_fmt
            )
            if export_fmt == 'json':
                if not isinstance(result, dict):
                    result = {'status': 'ok', 'dossier_id': 'HW-UNKNOWN'}
                self.send_json_response(result)
            elif export_fmt == 'csv':
                ts_str = time.strftime('%Y%m%d_%H%M%S', time.gmtime())
                filename = f"hailwarn_dossier_{ts_str}.csv"
                self.send_text_response(
                    str(result),
                    status_code=200,
                    content_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition': f'attachment; filename="{filename}"'}
                )
            else:
                self.send_text_response(
                    str(result),
                    status_code=200,
                    content_type='text/plain; charset=utf-8'
                )
        except Exception as e:
            print(f"[Server Error] Threat dossier export failed for {lat}, {lon}: {e}", file=sys.stderr)
            self.send_json_response({'error': 'Threat dossier export failure. Please check server logs.'}, 500)

    def log_message(self, format, *args):
        # Clean terminal logging
        sys.stderr.write(f"[{self.log_date_time_string()}] {format % args}\n")

def run_server(port=8080, host=os.environ.get('HOST', '127.0.0.1')):
    os.makedirs(STATIC_DIR, exist_ok=True)
    server_address = (host, port)
    httpd = ThreadedHTTPServer(server_address, HailWarnRequestHandler)
    print(f"==================================================")
    print(f"⚡ HailWarn Detection & Warning Operations Server")
    print(f"🌐 Running on: http://localhost:{port}")
    print(f"🛰️  Data Sources: NWS Alerts, IEM LSR / mPING, RainViewer dBZ, Open-Meteo")
    print(f"🔒 100% Free Open-Source APIs (No API keys required)")
    print(f"==================================================")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...")
        httpd.server_close()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        port = int(sys.argv[1])
    run_server(port=port)
