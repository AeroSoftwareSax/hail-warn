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
    USER_AGENT,
    SSL_CTX
)

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')

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

    def handle_assess(self, query):
        lat_param = query.get('lat', [None])[0]
        lon_param = query.get('lon', [None])[0]
        radius_param = query.get('radius', [None])[0]
        hours_param = query.get('hours', [None])[0]

        try:
            lat, lon = validate_coordinates(lat_param, lon_param)
            radius = validate_radius(radius_param, default=45.0)
            hours = validate_hours(hours_param, default=168)
        except ValueError as e:
            self.send_json_response({'error': str(e)}, 400)
            return

        try:
            result = perform_full_assessment(lat, lon, radius_miles=radius, hours=hours)
            self.send_json_response(result)
        except Exception as e:
            print(f"[Server Error] Assessment failed for {lat}, {lon}: {e}", file=sys.stderr)
            self.send_json_response({'error': 'Assessment failure. Please check server logs.'}, 500)

    def handle_nws_warnings(self, query):
        lat = None
        lon = None
        radius = 250.0
        try:
            if 'lat' in query and 'lon' in query:
                lat, lon = validate_coordinates(query['lat'][0], query['lon'][0])
            if 'radius' in query:
                radius = validate_radius(query['radius'][0], default=250.0)
        except ValueError as e:
            self.send_json_response({'error': str(e)}, 400)
            return

        try:
            data = fetch_active_nws_warnings(lat=lat, lon=lon, radius_miles=radius)
            self.send_json_response(data)
        except Exception as e:
            print(f"[Server Error] Active NWS warnings failed: {e}", file=sys.stderr)
            self.send_json_response({'warnings': [], 'geojson': {'type': 'FeatureCollection', 'features': []}, 'count': 0})

    def handle_spc_outlook(self):
        try:
            data = fetch_spc_day1_outlook()
            self.send_json_response(data)
        except Exception as e:
            print(f"[Server Error] SPC outlook failed: {e}", file=sys.stderr)
            self.send_json_response({'categorical': {'type': 'FeatureCollection', 'features': []}, 'hail': {'type': 'FeatureCollection', 'features': []}})

    def handle_hotspots(self):
        try:
            hotspots = fetch_active_national_hotspots()
            self.send_json_response({'hotspots': hotspots})
        except Exception as e:
            print(f"[Server Error] Hotspots query failed: {e}", file=sys.stderr)
            self.send_json_response({'hotspots': []})

    def handle_radar(self):
        try:
            radar = fetch_rainviewer_radar()
            self.send_json_response(radar)
        except Exception as e:
            print(f"[Server Error] Radar query failed: {e}", file=sys.stderr)
            self.send_json_response({'host': 'https://tilecache.rainviewer.com', 'frames': []})

    def handle_nexrad_stations(self, query):
        lat = None
        lon = None
        try:
            if 'lat' in query and 'lon' in query:
                lat, lon = validate_coordinates(query['lat'][0], query['lon'][0])
        except ValueError as e:
            self.send_json_response({'error': str(e)}, 400)
            return

        stations = load_nexrad_stations()
        nearest = find_nearest_nexrad(lat, lon) if lat is not None and lon is not None else None
        self.send_json_response({
            'stations': stations,
            'nearest': nearest,
            'count': len(stations)
        })

    def handle_search(self, query):
        q = query.get('q', [''])[0].strip()
        if not q:
            self.send_json_response([])
            return

        enc_q = urllib.parse.quote(q)
        url = f"https://nominatim.openstreetmap.org/search?q={enc_q}&format=json&limit=5&addressdetails=1"
        req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=6, context=SSL_CTX) as resp:  # nosec B310
                data = json.loads(resp.read().decode('utf-8'))
                results = []
                for item in data:
                    results.append({
                        'display_name': item.get('display_name'),
                        'lat': float(item.get('lat')),
                        'lon': float(item.get('lon')),
                        'type': item.get('type'),
                        'importance': item.get('importance')
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
        lat_param = query.get('lat', [None])[0]
        lon_param = query.get('lon', [None])[0]
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
