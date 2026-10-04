"""
HailCore - Hail Detection & Multi-Sensor Fusion Engine
Combines NWS active alerts, IEM Local Storm Reports & mPING crowdsourced reports,
convective atmospheric parameters from Open-Meteo, and RainViewer radar metadata.
Uses entirely free, open-source APIs without requiring any API keys.
"""

import os
import math
import time
import datetime
import json
import re
import csv
import io
import urllib.request
import urllib.parse
import ssl
from collections import OrderedDict
import threading
from concurrent.futures import ThreadPoolExecutor

USER_AGENT = "HailWarnApp/1.0 (contact@hailwarn.org; severe-weather-monitoring)"

def create_ssl_context():
    """Creates a secure SSLContext with certificate and hostname validation enabled.
    Supports optional custom CA bundle via SSL_CERT_FILE or HAILWARN_CA_BUNDLE environment variables.
    """
    ca_bundle = os.environ.get('SSL_CERT_FILE') or os.environ.get('HAILWARN_CA_BUNDLE')
    if ca_bundle and os.path.exists(ca_bundle):
        ctx = ssl.create_default_context(cafile=ca_bundle)
    else:
        ctx = ssl.create_default_context()
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx

SSL_CTX = create_ssl_context()

# Thread-safe LRU & TTL Cache to respect public rate limits (key -> (timestamp, data))
_CACHE_LOCK = threading.Lock()
_CACHE = OrderedDict()
CACHE_TTL_SECONDS = 60
MAX_CACHE_ENTRIES = 500

def _cache_get(url):
    now = time.time()
    with _CACHE_LOCK:
        if url in _CACHE:
            ts, data = _CACHE[url]
            if now - ts < CACHE_TTL_SECONDS:
                _CACHE.move_to_end(url)
                return data
            else:
                del _CACHE[url]
    return None

def _cache_set(url, data):
    now = time.time()
    with _CACHE_LOCK:
        expired_keys = [k for k, (ts, _) in _CACHE.items() if now - ts >= CACHE_TTL_SECONDS]
        for k in expired_keys:
            del _CACHE[k]
        while len(_CACHE) >= MAX_CACHE_ENTRIES:
            _CACHE.popitem(last=False)
        _CACHE[url] = (now, data)

# NEXRAD Station Database
NEXRAD_STATIONS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'nexrad_stations.json')
_NEXRAD_STATIONS = None

def load_nexrad_stations():
    """Loads all 160 US WSR-88D NEXRAD Doppler radar stations."""
    global _NEXRAD_STATIONS
    if _NEXRAD_STATIONS is not None:
        return _NEXRAD_STATIONS
    if os.path.exists(NEXRAD_STATIONS_FILE):
        try:
            with open(NEXRAD_STATIONS_FILE, 'r', encoding='utf-8') as f:
                _NEXRAD_STATIONS = json.load(f)
                return _NEXRAD_STATIONS
        except Exception as e:
            print(f"[HailCore Error] Failed loading nexrad_stations.json: {e}")
    _NEXRAD_STATIONS = []
    return _NEXRAD_STATIONS

def find_nearest_nexrad(lat, lon):
    """Finds the nearest WSR-88D NEXRAD station to given coordinates."""
    if lat is None or lon is None:
        return None
    try:
        lat = float(lat)
        lon = float(lon)
        if math.isnan(lat) or math.isinf(lat) or math.isnan(lon) or math.isinf(lon):
            return None
    except (TypeError, ValueError):
        return None
    stations = load_nexrad_stations()
    if not stations:
        return None
    best = None
    min_dist = float('inf')
    for s in stations:
        dist = haversine_distance(lat, lon, s['lat'], s['lon'])
        if dist < min_dist:
            min_dist = dist
            best = dict(s)
            best['distance_miles'] = round(dist, 1)
    return best

# Hail size reference table (inches to name)
HAIL_SIZE_DESCRIPTIONS = [
    (0.25, "Pea Size (0.25 in)"),
    (0.50, "Marble Size (0.50 in)"),
    (0.75, "Penny Size (0.75 in)"),
    (0.88, "Nickel Size (0.88 in)"),
    (1.00, "Quarter Size (1.00 in) - Severe Threshold"),
    (1.25, "Half Dollar (1.25 in)"),
    (1.50, "Walnut / Ping Pong (1.50 in)"),
    (1.75, "Golf Ball Size (1.75 in) - Vehicle Damage Risk"),
    (2.00, "Hen Egg Size (2.00 in)"),
    (2.50, "Tennis Ball Size (2.50 in) - High Roof Damage"),
    (2.75, "Baseball Size (2.75 in) - Destructive"),
    (3.00, "Tea Cup Size (3.00 in) - High Injury Risk"),
    (4.00, "Grapefruit Size (4.00 in) - Catastrophic"),
    (4.50, "Softball Size (4.50 in) - Catastrophic")
]

def _safe_float(val, default=0.0):
    """Safely convert any value to float with fallback, rejecting NaN/Inf."""
    if val is None:
        return default
    try:
        f = float(val)
        return default if (math.isnan(f) or math.isinf(f)) else f
    except (ValueError, TypeError):
        return default

def _safe_float_list(raw_list, default_val=0.0):
    """Extract list of finite floats from raw iterable, skipping malformed elements."""
    if not isinstance(raw_list, (list, tuple)):
        return [default_val]
    parsed = []
    for item in raw_list:
        try:
            f = float(item)
            if not (math.isnan(f) or math.isinf(f)):
                parsed.append(f)
        except (ValueError, TypeError):
            continue
    return parsed or [default_val]

def describe_hail_size(inches):
    """Returns human-readable descriptive name for a hail diameter in inches."""
    try:
        val = float(inches)
    except (ValueError, TypeError):
        return "None"

    if math.isnan(val) or math.isinf(val) or val <= 0:
        return "None"

    closest = None
    min_diff = 999.0
    for size, label in HAIL_SIZE_DESCRIPTIONS:
        diff = abs(size - val)
        if diff < min_diff:
            min_diff = diff
            closest = label
    if val >= 4.5:
        return f"Softball+ ({val:.2f} in) - Catastrophic"
    return closest or f"{val:.2f} in"


def haversine_distance(lat1, lon1, lat2, lon2):
    """Calculate the great circle distance in miles between two coordinates."""
    r = 3958.8  # Earth radius in miles
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2)**2
    # Clamp 'a' to [-1.0, 1.0] to prevent math domain error from float inaccuracies
    a = max(0.0, min(1.0, a))
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))
    return r * c

def http_get_json(url, headers=None, timeout=8):
    """Cached HTTP GET returning parsed JSON."""
    cached = _cache_get(url)
    if cached is not None:
        return cached

    req_headers = {'User-Agent': USER_AGENT, 'Accept': 'application/json, application/geo+json'}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as resp:  # nosec B310
            data = json.loads(resp.read().decode('utf-8'))
            _cache_set(url, data)
            return data
    except urllib.error.HTTPError as e:
        e.close()
        print(f"[HailCore HTTP Error] {url}: {e}")
        return None
    except Exception as e:
        print(f"[HailCore HTTP Error] {url}: {e}")
        return None

def fetch_nws_point_alerts(lat, lon):
    """
    Fetch active NWS alerts for the given lat/lon point.
    Free, no API key required.
    """
    url = f"https://api.weather.gov/alerts/active?point={lat:.4f},{lon:.4f}"
    data = http_get_json(url) or {}
    raw_features = data.get('features')
    if not isinstance(raw_features, list):
        return []

    alerts = []
    for f in raw_features:
        if not isinstance(f, dict):
            continue
        props = f.get('properties') if isinstance(f.get('properties'), dict) else {}
        geom = f.get('geometry') if isinstance(f.get('geometry'), dict) else None
        event = props.get('event') or ''
        if not event:
            continue
        severity = props.get('severity') or ''
        headline = props.get('headline') or ''
        description = props.get('description') or ''
        instruction = props.get('instruction') or ''
        parameters = props.get('parameters') if isinstance(props.get('parameters'), dict) else {}
        hail_tags = parameters.get('maxHailSize') or []
        hail_threat = parameters.get('hailThreat') or []

        # Extract hail size from parameters or regex in description
        hail_size = None
        if isinstance(hail_tags, list) and hail_tags:
            try:
                hail_size = float(hail_tags[0])
            except (ValueError, TypeError):
                hail_size = None
        elif isinstance(hail_tags, (int, float, str)):
            try:
                hail_size = float(hail_tags)
            except (ValueError, TypeError):
                hail_size = None

        if hail_size is None and description and isinstance(description, str):
            # Look for patterns like "HAIL...1.75 INCHES" or "hail up to quarter size"
            match = re.search(r'HAIL(?:\s+THREAT)?\.\.\.([0-9\.]+)\s*(?:IN|INCHES)?', description, re.IGNORECASE)
            if match:
                try:
                    hail_size = float(match.group(1))
                except (ValueError, TypeError):
                    hail_size = None

        threat_type = 'RADAR INDICATED'
        if isinstance(hail_threat, list) and hail_threat and hail_threat[0]:
            threat_type = str(hail_threat[0])
        elif isinstance(hail_threat, str) and hail_threat:
            threat_type = hail_threat

        alerts.append({
            'id': props.get('id'),
            'event': event,
            'severity': severity,
            'urgency': props.get('urgency'),
            'certainty': props.get('certainty'),
            'headline': headline,
            'description': description,
            'instruction': instruction,
            'hail_size_in': hail_size,
            'hail_description': describe_hail_size(hail_size) if hail_size else None,
            'hail_threat_type': threat_type,
            'effective': props.get('effective'),
            'expires': props.get('expires'),
            'geometry': geom,
            'is_severe_hail': (hail_size is not None and hail_size >= 1.0) or ('Severe Thunderstorm' in event and isinstance(description, str) and 'hail' in description.lower())
        })
    return alerts

def parse_iso_time(iso_str):
    """Safely parse ISO timestamp into UTC timestamp seconds."""
    if not iso_str:
        return 0
    try:
        clean = iso_str.replace('Z', '+00:00')
        dt = datetime.datetime.fromisoformat(clean)
        return dt.timestamp()
    except Exception:
        return 0

def fetch_iem_lsr_reports(lat, lon, radius_miles=45, hours=168):
    """
    Fetch NWS Local Storm Reports (LSR) including mPING ground hail reports
    from Iowa Environmental Mesonet (IEM).
    Filters strictly for hail reports within the specified radius and time window.
    """
    try:
        hours = max(1, min(720, int(hours)))
    except (TypeError, ValueError):
        hours = 168
    url = f"https://mesonet.agron.iastate.edu/geojson/lsr.py?hours={hours}"
    data = http_get_json(url)
    if not data or 'features' not in data:
        return []

    now_ts = time.time()
    cutoff_ts = now_ts - (hours * 3600)

    reports = []
    for f in (data.get('features') or []):
        if not isinstance(f, dict):
            continue
        props = f.get('properties') or {}
        geom = f.get('geometry') or {}
        if not isinstance(geom, dict):
            continue
        coords = geom.get('coordinates')
        if not coords or not isinstance(coords, (list, tuple)) or len(coords) < 2:
            continue
        try:
            report_lon = float(coords[0])
            report_lat = float(coords[1])
        except (ValueError, TypeError):
            continue
        
        typetext = (props.get('typetext') or '').upper()
        type_code = (props.get('type') or '').upper()
        remark = props.get('remark') or ''
        source = props.get('source') or ''

        # Strictly filter for hail
        is_hail = (typetext == 'HAIL') or (type_code == 'H') or ('hail' in remark.lower())
        if not is_hail:
            continue

        # Extract magnitude (hail diameter in inches)
        mag = props.get('magnitude')
        mag_float = None
        if mag is not None and str(mag).strip() != '':
            try:
                mag_float = float(mag)
            except (ValueError, TypeError):
                mag_float = None

        # If magnitude missing or not float, try to extract from remark
        if mag_float is None and remark:
            m = re.search(r'([0-9\.]+)\s*(?:in|inch|\"|\binches\b)', remark, re.IGNORECASE)
            if m:
                try:
                    mag_float = float(m.group(1))
                except ValueError:
                    mag_float = None

        # Validate magnitude range for hail (0.1 to 6.0 inches)
        if mag_float is None or mag_float <= 0.0 or mag_float > 8.0:
            continue

        # Distance check
        dist_mi = haversine_distance(lat, lon, report_lat, report_lon)
        if dist_mi > radius_miles:
            continue

        # Check timestamp
        valid_str = props.get('valid')
        valid_ts = parse_iso_time(valid_str)
        if valid_ts > 0 and valid_ts < cutoff_ts:
            continue

        age_hours = round((now_ts - valid_ts) / 3600, 1) if valid_ts > 0 else 999.0
        is_mping = 'mping' in remark.lower() or 'mping' in source.lower()

        reports.append({
            'source_type': 'mPING Citizen Report' if is_mping else f"NWS Spotter ({source or 'Observer'})",
            'is_mping': is_mping,
            'type': 'HAIL',
            'hail_size_in': mag_float,
            'hail_description': describe_hail_size(mag_float),
            'city': props.get('city'),
            'county': props.get('county'),
            'state': props.get('state'),
            'remark': remark,
            'valid': valid_str,
            'age_hours': age_hours,
            'distance_miles': round(dist_mi, 1),
            'latitude': report_lat,
            'longitude': report_lon
        })

    # Sort reports by distance ascending
    reports.sort(key=lambda x: (x['distance_miles'], x['age_hours']))
    return reports

def fetch_open_meteo_convective(lat, lon):
    """
    Fetch atmospheric convective instability parameters (CAPE, Lifted Index, CIN,
    freezing level height, surface pressure, wind) from Open-Meteo.
    Free, open source, no API key required.
    """
    url = (
        f"https://api.open-meteo.com/v1/forecast?latitude={lat:.4f}&longitude={lon:.4f}"
        "&current=temperature_2m,relative_humidity_2m,precipitation,rain,showers,weather_code,wind_speed_10m,wind_gusts_10m,surface_pressure,wind_direction_10m"
        "&hourly=cape,lifted_index,convective_inhibition,precipitation,freezing_level_height,wind_speed_10m,wind_gusts_10m&forecast_days=1"
    )
    data = http_get_json(url)
    if not data:
        return {}

    current = data.get('current') if isinstance(data.get('current'), dict) else {}
    hourly = data.get('hourly') if isinstance(data.get('hourly'), dict) else {}

    capes = _safe_float_list(hourly.get('cape'), 0.0)
    lifted_indices = _safe_float_list(hourly.get('lifted_index'), 0.0)
    cins = _safe_float_list(hourly.get('convective_inhibition'), 0.0)
    raw_fz = [v for v in _safe_float_list(hourly.get('freezing_level_height'), 0.0) if v > 0]
    freezing_lvl_m = raw_fz[0] if raw_fz else 3500.0
    freezing_lvl_ft = round(freezing_lvl_m * 3.28084)

    cape_val = capes[0] if capes else 0.0
    li_val = lifted_indices[0] if lifted_indices else 0.0
    max_cape = max(capes) if capes else 0.0
    min_li = min(lifted_indices) if lifted_indices else 0.0
    cin_val = abs(cins[0]) if cins else 0.0

    # Convective Inhibition (Cap) Classification
    if cin_val < 25:
        cin_status = "Weak / No Cap (Explosive Updrafts Possible)"
        cap_strength = "WEAK"
    elif cin_val < 75:
        cin_status = "Moderate Cap (Selective Cell Initiation)"
        cap_strength = "MODERATE"
    else:
        cin_status = "Strong Cap (Convection Suppressed / Needs Trigger)"
        cap_strength = "STRONG"

    # Hail Melting / Survival Factor
    if freezing_lvl_ft <= 9500:
        survival_factor = "High Hail Survival (Low Freezing Level, Minimal Melting)"
        survival_rating = "HIGH"
    elif freezing_lvl_ft <= 12500:
        survival_factor = "Moderate Hail Survival (Standard Melting Layer)"
        survival_rating = "MODERATE"
    else:
        survival_factor = "High Melting in Deep Warm Layer (Small Hail Melts to Rain)"
        survival_rating = "LOW"

    # Hail Potential Index (HPI, 0-100)
    # Combines CAPE updraft energy, Lifted Index instability, and Freezing Level preservation
    cape_pts = min(45, (max_cape / 3000.0) * 45) if max_cape > 0 else 0
    li_pts = min(35, (abs(min_li) / 8.0) * 35) if min_li < 0 else 0
    fz_pts = 20 if freezing_lvl_ft <= 9500 else (12 if freezing_lvl_ft <= 12500 else 5)
    hpi_score = int(min(100, round(cape_pts + li_pts + fz_pts)))

    raw_temp = current.get('temperature_2m', 20)
    if raw_temp is not None:
        temp_c = _safe_float(raw_temp, 20.0)
        temp_f = round((temp_c * 9/5) + 32, 1)
    else:
        temp_c = None
        temp_f = None
    wind_kmh = _safe_float(current.get('wind_speed_10m'), 0.0)
    wind_mph = round(wind_kmh * 0.621371, 1)
    gusts_kmh = _safe_float(current.get('wind_gusts_10m'), 0.0)
    gusts_mph = round(gusts_kmh * 0.621371, 1)
    surf_press = _safe_float(current.get('surface_pressure'), 1013.25)
    surf_press_inhg = round(surf_press * 0.02953, 2)

    return {
        'temperature_c': temp_c,
        'temperature_f': temp_f,
        'humidity_percent': current.get('relative_humidity_2m'),
        'precipitation_mm': current.get('precipitation', 0),
        'wind_speed_kmh': wind_kmh,
        'wind_speed_mph': wind_mph,
        'wind_gusts_kmh': gusts_kmh,
        'wind_gusts_mph': gusts_mph,
        'wind_direction_deg': current.get('wind_direction_10m', 0),
        'surface_pressure_hpa': round(surf_press, 1),
        'surface_pressure_inhg': surf_press_inhg,
        'weather_code': current.get('weather_code', 0),
        'cape_j_kg': round(cape_val, 0),
        'peak_cape_today': round(max_cape, 0),
        'lifted_index': round(li_val, 1),
        'min_lifted_index_today': round(min_li, 1),
        'cin_j_kg': round(cin_val, 0),
        'cin_cap_status': cin_status,
        'cin_cap_strength': cap_strength,
        'freezing_level_m': round(freezing_lvl_m),
        'freezing_level_ft': freezing_lvl_ft,
        'hail_survival_factor': survival_factor,
        'hail_survival_rating': survival_rating,
        'hail_potential_index': hpi_score
    }

def fetch_rainviewer_radar():
    """
    Fetch current RainViewer radar imagery metadata (timestamps and paths).
    Free, no API key required.
    """
    url = "https://api.rainviewer.com/public/weather-maps.json"
    data = http_get_json(url)
    if not data:
        return {'host': 'https://tilecache.rainviewer.com', 'frames': []}

    host = data.get('host') or 'https://tilecache.rainviewer.com'
    radar = data.get('radar') if isinstance(data.get('radar'), dict) else {}
    past = radar.get('past') if isinstance(radar.get('past'), list) else []
    nowcast = radar.get('nowcast') if isinstance(radar.get('nowcast'), list) else []
    
    frames = []
    for f in past:
        if isinstance(f, dict):
            frames.append({'time': f.get('time'), 'path': f.get('path'), 'type': 'past'})
    for f in nowcast:
        if isinstance(f, dict):
            frames.append({'time': f.get('time'), 'path': f.get('path'), 'type': 'nowcast'})

    return {
        'host': host,
        'frames': frames,
        'latest_frame': past[-1] if past else None
    }

def fetch_active_nws_warnings(lat=None, lon=None, radius_miles=250):
    """
    Fetch active NWS storm-based warnings, advisories, statements, and watches
    with polygon geometry, hail/wind tags, and storm motion vectors.
    Sources from NWS alerts and Iowa Environmental Mesonet SBW GeoJSON.
    Returns GeoJSON FeatureCollection and structured summary list.
    """
    url_nws = "https://api.weather.gov/alerts/active?status=actual&message_type=alert"
    nws_data = http_get_json(url_nws) or {}

    url_iem = "https://mesonet.agron.iastate.edu/geojson/sbw.geojson"
    iem_data = http_get_json(url_iem) or {}

    warnings = []
    seen_ids = set()

    # 1. Process NWS Features
    nws_features = nws_data.get('features')
    if isinstance(nws_features, list):
        for f in nws_features:
            if not isinstance(f, dict):
                continue
            props = f.get('properties') if isinstance(f.get('properties'), dict) else {}
            geom = f.get('geometry') if isinstance(f.get('geometry'), dict) else None
            event = props.get('event', '')
            if not event or not geom:
                continue

            is_severe = any(k in event for k in [
                'Severe Thunderstorm', 'Tornado', 'Flash Flood', 'Special Weather',
                'Severe Weather', 'Flood', 'Watch', 'Advisory', 'High Wind'
            ])
            if not is_severe:
                continue

            alert_id = props.get('id') or f.get('id')
            if alert_id in seen_ids:
                continue
            seen_ids.add(alert_id)

            params = props.get('parameters') if isinstance(props.get('parameters'), dict) else {}
            hail_tags = params.get('maxHailSize') or []
            wind_tags = params.get('maxWindGust') or []
            motion_tags = params.get('eventMotionDescription') or []
            hail_threat = params.get('hailThreat') or ['RADAR INDICATED']
            wind_threat = params.get('windThreat') or ['RADAR INDICATED']
            tornado_det = params.get('tornadoDetection') or ['']

            hail_size = None
            if isinstance(hail_tags, list) and hail_tags:
                try:
                    hail_size = float(hail_tags[0])
                except (ValueError, TypeError):
                    hail_size = None
            elif isinstance(hail_tags, (int, float, str)):
                try:
                    hail_size = float(hail_tags)
                except (ValueError, TypeError):
                    hail_size = None
            
            description = props.get('description', '') or ''
            headline = props.get('headline', '') or ''
            instruction = props.get('instruction', '') or ''

            if hail_size is None and description and isinstance(description, str):
                m = re.search(r'HAIL(?:\s+THREAT)?\.\.\.([0-9\.]+)\s*(?:IN|INCHES)?', description, re.IGNORECASE)
                if m:
                    try:
                        hail_size = float(m.group(1))
                    except (ValueError, TypeError):
                        hail_size = None

            # Calculate centroid & distance
            center_lat, center_lon = None, None
            coords = geom.get('coordinates', []) if isinstance(geom.get('coordinates'), list) else []
            pts = []
            try:
                if geom.get('type') == 'Polygon' and coords:
                    pts = coords[0]
                elif geom.get('type') == 'MultiPolygon' and coords and coords[0]:
                    pts = coords[0][0]
                if pts:
                    center_lon = sum(p[0] for p in pts) / len(pts)
                    center_lat = sum(p[1] for p in pts) / len(pts)
            except Exception:
                center_lat, center_lon = None, None

            dist_mi = None
            if lat is not None and lon is not None and center_lat is not None:
                dist_mi = round(haversine_distance(lat, lon, center_lat, center_lon), 1)

            # Parse storm motion vector
            motion_obj = None
            m_str = None
            if isinstance(motion_tags, list) and motion_tags:
                cand = motion_tags[0]
                if isinstance(cand, str):
                    m_str = cand
            elif isinstance(motion_tags, str):
                m_str = motion_tags

            if m_str:
                m_match = re.search(r'(\d+)DEG\.\.\.(\d+)KT\.\.\.([\d\.\-]+),([\d\.\-]+)', m_str)
                if m_match:
                    try:
                        deg = int(m_match.group(1))
                        kt = int(m_match.group(2))
                        mph = round(kt * 1.15078, 1)
                        slat = float(m_match.group(3))
                        slon = float(m_match.group(4))
                        
                        eta_mins = None
                        if lat is not None and lon is not None:
                            d_lat = lat - slat
                            d_lon = (lon - slon) * math.cos(math.radians(slat))
                            bearing_to_user = (math.degrees(math.atan2(d_lon, d_lat)) + 360) % 360
                            angle_diff = abs((deg - bearing_to_user + 180) % 360 - 180)
                            if angle_diff < 50:
                                closing_speed = mph * math.cos(math.radians(angle_diff))
                                if closing_speed > 3.0 and dist_mi and dist_mi > 0:
                                    eta_mins = int(round((dist_mi / closing_speed) * 60))

                        # Project forward trajectory (15, 30, 45 mins)
                        proj = []
                        for mins in [15, 30, 45]:
                            d_dist = (mph * (mins / 60.0)) / 69.0
                            rad = math.radians(deg)
                            plat = slat + d_dist * math.cos(rad)
                            cos_lat = math.cos(math.radians(slat))
                            if abs(cos_lat) < 1e-6:
                                cos_lat = 1e-6 if cos_lat >= 0 else -1e-6
                            plon = slon + (d_dist / cos_lat) * math.sin(rad)
                            proj.append({'minutes': mins, 'lat': round(plat, 4), 'lon': round(plon, 4)})

                        motion_obj = {
                            'heading_deg': deg,
                            'speed_kt': kt,
                            'speed_mph': mph,
                            'storm_lat': slat,
                            'storm_lon': slon,
                            'eta_mins': eta_mins,
                            'projected_path': proj
                        }
                    except Exception:
                        motion_obj = None

            wind_first = None
            if isinstance(wind_tags, list) and wind_tags:
                wind_first = wind_tags[0]
            elif isinstance(wind_tags, (str, int, float)):
                wind_first = wind_tags

            has_80_wind = False
            if isinstance(wind_first, str):
                has_80_wind = ('80' in wind_first)
            elif isinstance(wind_first, (int, float)):
                has_80_wind = (wind_first >= 80)

            is_tornado = 'Tornado' in event
            is_severe_tstorm = 'Severe Thunderstorm' in event
            is_flash_flood = 'Flash Flood' in event or 'Flood' in event
            is_special = 'Special Weather' in event or 'Statement' in event
            is_watch = 'Watch' in event
            is_destructive = (is_severe_tstorm and ((hail_size and hail_size >= 2.0) or has_80_wind)) or is_tornado

            if is_tornado:
                color = "#ef4444"
                cat = "TORNADO"
                weight = 3.5
                dash = None
                pulse = True
            elif is_destructive:
                color = "#ec4899"
                cat = "DESTRUCTIVE_HAIL"
                weight = 3.0
                dash = None
                pulse = True
            elif is_severe_tstorm:
                color = "#f59e0b"
                cat = "SEVERE_TSTORM"
                weight = 2.5
                dash = None
                pulse = False
            elif is_flash_flood:
                color = "#06b6d4"
                cat = "FLASH_FLOOD"
                weight = 2.5
                dash = None
                pulse = False
            elif is_special:
                color = "#38bdf8"
                cat = "ADVISORY"
                weight = 2.0
                dash = "6, 6"
                pulse = False
            elif is_watch:
                color = "#eab308"
                cat = "WATCH"
                weight = 2.0
                dash = "4, 4"
                pulse = False
            else:
                color = "#64748b"
                cat = "WEATHER"
                weight = 1.5
                dash = None
                pulse = False

            hail_threat_label = 'RADAR INDICATED'
            if isinstance(hail_threat, list) and hail_threat and hail_threat[0]:
                hail_threat_label = str(hail_threat[0])
            elif isinstance(hail_threat, str) and hail_threat:
                hail_threat_label = hail_threat

            wind_threat_label = 'RADAR INDICATED'
            if isinstance(wind_threat, list) and wind_threat and wind_threat[0]:
                wind_threat_label = str(wind_threat[0])
            elif isinstance(wind_threat, str) and wind_threat:
                wind_threat_label = wind_threat

            tornado_det_val = None
            if isinstance(tornado_det, list) and tornado_det and tornado_det[0]:
                tornado_det_val = str(tornado_det[0])
            elif isinstance(tornado_det, str) and tornado_det:
                tornado_det_val = tornado_det

            warnings.append({
                'id': alert_id,
                'event': event,
                'category': cat,
                'severity': props.get('severity', 'Severe'),
                'urgency': props.get('urgency', 'Immediate'),
                'headline': headline,
                'description': description,
                'instruction': instruction,
                'hail_size_in': hail_size,
                'hail_label': describe_hail_size(hail_size) if hail_size else None,
                'hail_threat_type': hail_threat_label,
                'wind_gust': wind_first,
                'wind_threat_type': wind_threat_label,
                'tornado_detection': tornado_det_val,
                'effective': props.get('effective'),
                'expires': props.get('expires'),
                'wfo': props.get('senderName', 'NWS'),
                'area_desc': props.get('areaDesc', ''),
                'center_lat': center_lat,
                'center_lon': center_lon,
                'distance_miles': dist_mi,
                'motion': motion_obj,
                'style': {
                    'color': color,
                    'fillColor': color,
                    'weight': weight,
                    'dashArray': dash,
                    'pulse': pulse,
                    'fillOpacity': 0.22
                },
                'geometry': geom
            })

    # 2. Process IEM SBW Features
    iem_features = iem_data.get('features')
    if isinstance(iem_features, list):
        for f in iem_features:
            if not isinstance(f, dict):
                continue
            props = f.get('properties') if isinstance(f.get('properties'), dict) else {}
            geom = f.get('geometry') if isinstance(f.get('geometry'), dict) else None
            event = props.get('ps', '')
            if not event or not geom:
                continue

            iem_id = f"{props.get('wfo')}_{props.get('phenomena')}_{props.get('eventid')}_{props.get('year')}"
            if iem_id in seen_ids:
                continue
            seen_ids.add(iem_id)

            hail_f = None
            if props.get('hailtag') or props.get('max_hailtag'):
                try:
                    hail_f = float(props.get('max_hailtag') or props.get('hailtag'))
                except (ValueError, TypeError):
                    hail_f = None

            wind_tag = props.get('max_windtag') or props.get('windtag')
            wind_str = f"{wind_tag} MPH" if wind_tag else None

            center_lat, center_lon = None, None
            coords = geom.get('coordinates', []) if isinstance(geom.get('coordinates'), list) else []
            pts = []
            try:
                if geom.get('type') == 'Polygon' and coords:
                    pts = coords[0]
                elif geom.get('type') == 'MultiPolygon' and coords and coords[0]:
                    pts = coords[0][0]
                if pts:
                    center_lon = sum(p[0] for p in pts) / len(pts)
                    center_lat = sum(p[1] for p in pts) / len(pts)
            except Exception:
                center_lat, center_lon = None, None

            dist_mi = None
            if lat is not None and lon is not None and center_lat is not None:
                dist_mi = round(haversine_distance(lat, lon, center_lat, center_lon), 1)

            is_tornado = 'Tornado' in event or props.get('phenomena') == 'TO'
            is_severe_tstorm = 'Severe Thunderstorm' in event or props.get('phenomena') == 'SV'
            is_flash_flood = 'Flash Flood' in event or props.get('phenomena') == 'FF'
            is_destructive = props.get('is_emergency') or props.get('is_pds') or (hail_f and hail_f >= 2.0)

            if is_tornado:
                color = "#ef4444"
                cat = "TORNADO"
                weight = 3.5
                pulse = True
            elif is_destructive:
                color = "#ec4899"
                cat = "DESTRUCTIVE_HAIL"
                weight = 3.0
                pulse = True
            elif is_severe_tstorm:
                color = "#f59e0b"
                cat = "SEVERE_TSTORM"
                weight = 2.5
                pulse = False
            elif is_flash_flood:
                color = "#06b6d4"
                cat = "FLASH_FLOOD"
                weight = 2.5
                pulse = False
            else:
                color = "#38bdf8"
                cat = "ADVISORY"
                weight = 2.0
                pulse = False

            warnings.append({
                'id': iem_id,
                'event': event,
                'category': cat,
                'severity': 'Extreme' if is_tornado else 'Severe',
                'urgency': 'Immediate',
                'headline': f"{event} for {props.get('wfo')} area",
                'description': f"NWS {props.get('wfo')} issued a {event}. Hail: {hail_f or 'N/A'}\", Wind: {wind_str or 'N/A'}.",
                'instruction': "Take protective shelter immediately. Protect vehicles from severe hail damage.",
                'hail_size_in': hail_f,
                'hail_label': describe_hail_size(hail_f) if hail_f else None,
                'hail_threat_type': props.get('hailthreat') or 'RADAR INDICATED',
                'wind_gust': wind_str,
                'wind_threat_type': props.get('windthreat') or 'RADAR INDICATED',
                'tornado_detection': props.get('tornadotag'),
                'effective': props.get('polygon_begin'),
                'expires': props.get('expire'),
                'wfo': f"NWS {props.get('wfo')}",
                'area_desc': props.get('wfo', 'Active Zone'),
                'center_lat': center_lat,
                'center_lon': center_lon,
                'distance_miles': dist_mi,
                'motion': None,
                'style': {
                    'color': color,
                    'fillColor': color,
                    'weight': weight,
                    'dashArray': None,
                    'pulse': pulse,
                    'fillOpacity': 0.22
                },
                'geometry': geom
            })

    # Sort warnings by distance if user coords available
    if lat is not None and lon is not None:
        warnings.sort(key=lambda w: (w['distance_miles'] if w['distance_miles'] is not None else 9999.0))

    # Build GeoJSON FeatureCollection
    features = []
    for w in warnings:
        feat = {
            'type': 'Feature',
            'id': w['id'],
            'geometry': w['geometry'],
            'properties': {
                'id': w['id'],
                'event': w['event'],
                'category': w['category'],
                'severity': w['severity'],
                'headline': w['headline'],
                'description': w['description'],
                'instruction': w['instruction'],
                'hail_size_in': w['hail_size_in'],
                'hail_label': w['hail_label'],
                'hail_threat_type': w['hail_threat_type'],
                'wind_gust': w['wind_gust'],
                'wind_threat_type': w['wind_threat_type'],
                'tornado_detection': w['tornado_detection'],
                'effective': w['effective'],
                'expires': w['expires'],
                'wfo': w['wfo'],
                'distance_miles': w['distance_miles'],
                'motion': w['motion'],
                'style': w['style']
            }
        }
        features.append(feat)

    geojson = {'type': 'FeatureCollection', 'features': features}
    return {'warnings': warnings, 'geojson': geojson, 'count': len(warnings)}

def fetch_spc_day1_outlook():
    """
    Fetch NOAA Storm Prediction Center (SPC) Day 1 Convective Outlook GeoJSON.
    Returns categorical risk and hail risk outlook polygons.
    """
    url_cat = "https://www.spc.noaa.gov/products/outlook/day1otlk_cat.lyr.geojson"
    url_hail = "https://www.spc.noaa.gov/products/outlook/day1otlk_hail.lyr.geojson"

    cat_data = http_get_json(url_cat, timeout=6) or {'type': 'FeatureCollection', 'features': []}
    hail_data = http_get_json(url_hail, timeout=6) or {'type': 'FeatureCollection', 'features': []}

    return {
        'categorical': cat_data,
        'hail': hail_data
    }

def fetch_active_national_hotspots():
    """
    Queries NWS for locations currently under active severe thunderstorm/tornado
    warnings or statements with hail tags to populate Quick Live Hotspots.
    Prioritizes real severe events with largest hail tags first.
    """
    url = "https://api.weather.gov/alerts/active"
    data = http_get_json(url) or {}
    hotspots = []
    features = data.get('features')
    if not isinstance(features, list):
        features = []

    for f in features:
        if not isinstance(f, dict):
            continue
        props = f.get('properties') if isinstance(f.get('properties'), dict) else {}
        event = props.get('event') or ''
        geom = f.get('geometry') if isinstance(f.get('geometry'), dict) else None
        params = props.get('parameters') if isinstance(props.get('parameters'), dict) else {}
        hail_size_list = params.get('maxHailSize') or []
        hail_size = hail_size_list[0] if (isinstance(hail_size_list, list) and hail_size_list) else hail_size_list
        
        is_relevant = any(k in event for k in ['Severe Thunderstorm', 'Tornado', 'Special Weather'])
        if is_relevant:
            center_lat, center_lon = None, None
            if geom and isinstance(geom.get('coordinates'), list):
                coords = geom['coordinates']
                try:
                    if geom.get('type') == 'Polygon' and coords:
                        pts = coords[0]
                        center_lon = sum(p[0] for p in pts) / len(pts)
                        center_lat = sum(p[1] for p in pts) / len(pts)
                    elif geom.get('type') == 'MultiPolygon' and coords and coords[0]:
                        pts = coords[0][0]
                        center_lon = sum(p[0] for p in pts) / len(pts)
                        center_lat = sum(p[1] for p in pts) / len(pts)
                except Exception:
                    center_lat, center_lon = None, None
            
            area_desc = props.get('areaDesc', 'Active Threat Area') or 'Active Threat Area'
            hail_f = None
            if hail_size is not None:
                try:
                    hail_f = float(hail_size)
                    if math.isnan(hail_f) or math.isinf(hail_f):
                        hail_f = None
                except (ValueError, TypeError):
                    hail_f = None

            if center_lat and center_lon:
                hotspots.append({
                    'event': event,
                    'headline': props.get('headline'),
                    'area': str(area_desc).split(';')[0].strip(),
                    'hail_size_in': hail_f,
                    'hail_label': describe_hail_size(hail_f) if hail_f else ("Hail Threat" if "Special" in event else "Severe Hazard"),
                    'latitude': round(center_lat, 4),
                    'longitude': round(center_lon, 4),
                    'severity': props.get('severity', 'Severe')
                })

    # Sort hotspots: Tornado first, then largest hail size
    hotspots.sort(key=lambda x: (
        2 if 'Tornado' in x.get('event', '') else (1 if 'Severe Thunderstorm' in x.get('event', '') else 0),
        x.get('hail_size_in') or 0.0
    ), reverse=True)

    # Standard hail alley fallback hotspots if quiet day
    fallbacks = [
        {'area': 'Dallas / Fort Worth, TX', 'latitude': 32.7767, 'longitude': -96.7970, 'event': 'Hail Alley Zone', 'hail_label': 'High Hail Risk Zone'},
        {'area': 'Oklahoma City, OK', 'latitude': 35.4676, 'longitude': -97.5164, 'event': 'Hail Alley Zone', 'hail_label': 'Supercell Corridor'},
        {'area': 'Denver, CO', 'latitude': 39.7392, 'longitude': -104.9903, 'event': 'Front Range Hail Corridor', 'hail_label': 'High Frequency Hail'},
        {'area': 'Wichita, KS', 'latitude': 37.6872, 'longitude': -97.3301, 'event': 'Central Plains Hail Belt', 'hail_label': 'Severe Hail Hotspot'}
    ]
    for fb in fallbacks:
        if len(hotspots) >= 8:
            break
        if not any(h['area'] == fb['area'] for h in hotspots):
            hotspots.append(fb)

    return hotspots[:8]

def evaluate_hail_risk(alerts, lsr_reports, convective_data, regional_warnings=None):
    """
    Multi-sensor fusion assessment:
    Combines NWS alerts, ground-truth mPING/LSR reports, convective sounding physics,
    and approaching storm vectors from regional warnings.
    """
    score = 0
    reasons = []
    max_hail_in = 0.0

    # 1. NWS Direct Point Alerts Evaluation
    for a in (alerts or []):
        if not isinstance(a, dict):
            continue
        event = (a.get('event') or '').lower() if isinstance(a.get('event'), str) else ''
        hail = _safe_float(a.get('hail_size_in'), 0.0)
        if 0.0 < hail <= 8.0 and hail > max_hail_in:
            max_hail_in = hail

        if 'tornado' in event:
            score = max(score, 88)
            reasons.append(f"CRITICAL: Active {a.get('event', 'Tornado')} affecting your coordinates.")
        elif 'severe thunderstorm' in event:
            s = 70
            if hail >= 1.75:
                s = 85
            if hail >= 2.50:
                s = 95
            score = max(score, s)
            reasons.append(f"WARNING: Active Severe Thunderstorm Warning with {describe_hail_size(hail)} hail tag.")
        elif 'special weather' in event and hail > 0:
            score = max(score, 45)
            reasons.append(f"ADVISORY: Special Weather Statement citing {describe_hail_size(hail)} hail.")
        elif 'watch' in event:
            score = max(score, 35)
            reasons.append(f"WATCH: Severe weather watch in effect ({a.get('event', 'Watch')}).")

    # 2. Regional Warnings & Approaching Storm Vectors
    if regional_warnings:
        for rw in regional_warnings:
            if not isinstance(rw, dict):
                continue
            raw_dist = rw.get('distance_miles')
            if raw_dist is None:
                continue
            dist = _safe_float(raw_dist, -1.0)
            if dist < 0:
                continue
            hail = _safe_float(rw.get('hail_size_in'), 0.0)
            motion = rw.get('motion')
            ev = rw.get('event') or ''
            eta = None
            if isinstance(motion, dict) and motion.get('eta_mins') is not None:
                eta = _safe_float(motion.get('eta_mins'), -1.0)
                if eta < 0:
                    eta = None

            if eta is not None and eta <= 50 and (hail >= 0.75 or 'Severe' in ev or 'Tornado' in ev):
                b_score = 85 if hail >= 1.75 else 75
                score = max(score, b_score)
                if hail > max_hail_in:
                    max_hail_in = hail
                reasons.append(
                    f"APPROACHING THREAT: {ev} with {describe_hail_size(hail)} hail tracked {dist:.1f} mi away, ETA ~{eta:.0f} mins!"
                )
            elif dist <= 12.0 and any(k in ev for k in ['Severe Thunderstorm', 'Tornado']):
                score = max(score, 70)
                if hail > max_hail_in:
                    max_hail_in = hail
                reasons.append(
                    f"PROXIMITY WARNING: Active {ev} within {dist:.1f} mi ({describe_hail_size(hail)} hail tag)."
                )

    # 3. Ground Reports (mPING + NWS Spotters) Evaluation
    closest_recent_report = None
    closest_dist = 999.0
    for r in (lsr_reports or []):
        if not isinstance(r, dict):
            continue
        dist = _safe_float(r.get('distance_miles'), 999.0)
        hail = _safe_float(r.get('hail_size_in'), 0.0)
        age = _safe_float(r.get('age_hours'), 999.0)
        
        if age <= 24.0 and 0.0 < hail <= 8.0 and hail > max_hail_in:
            max_hail_in = hail

        if age <= 24.0 and dist < closest_dist and hail > 0:
            closest_dist = dist
            closest_recent_report = r

    if closest_recent_report:
        hail = _safe_float(closest_recent_report.get('hail_size_in'), 0.0)
        dist = _safe_float(closest_recent_report.get('distance_miles'), 999.0)
        is_mping = bool(closest_recent_report.get('is_mping', False))
        age = _safe_float(closest_recent_report.get('age_hours'), 999.0)
        src_label = "mPING citizen report" if is_mping else "Official NWS spotter"
        time_desc = f"{age:.1f}h ago" if age < 24 else "recently"

        if dist <= 5.0:
            boost = 35 if hail >= 1.0 else 20
            score = max(score, min(95, score + boost))
            reasons.append(f"GROUND TRUTH: Confirmed {describe_hail_size(hail)} hail {dist:.1f} mi away ({time_desc}) via {src_label}!")
        elif dist <= 15.0:
            boost = 25 if hail >= 1.0 else 15
            score = max(score, min(80, score + boost))
            reasons.append(f"GROUND TRUTH: {describe_hail_size(hail)} hail reported {dist:.1f} mi away ({src_label}).")
        elif dist <= 30.0:
            boost = 15 if hail >= 1.0 else 10
            score = max(score, min(60, score + boost))
            reasons.append(f"NEARBY REPORT: {describe_hail_size(hail)} hail within {dist:.1f} mi ({src_label}).")

    # 4. Atmospheric Sounding Physics (Open-Meteo CAPE, LI, CIN, Freezing Level)
    c_data = convective_data if isinstance(convective_data, dict) else {}
    cape = _safe_float(c_data.get('cape_j_kg'), 0.0)
    li = _safe_float(c_data.get('lifted_index'), 0.0)
    peak_cape = _safe_float(c_data.get('peak_cape_today'), 0.0)
    cin_cap = c_data.get('cin_cap_strength') or 'WEAK'
    fz_ft = _safe_float(c_data.get('freezing_level_ft'), 12000.0)

    if cape >= 2500 or peak_cape >= 3000:
        score = max(score, score + 15)
        reasons.append(f"ATMOSPHERIC INSTABILITY: Extreme CAPE ({peak_cape:.0f} J/kg) supports violent storm updrafts & large hail growth.")
    elif cape >= 1500 or peak_cape >= 2000:
        score = max(score, score + 10)
        reasons.append(f"ATMOSPHERIC INSTABILITY: High CAPE ({peak_cape:.0f} J/kg) favorable for hail-producing thunderstorms.")
    elif cape >= 800 and score > 20:
        score += 5
        reasons.append(f"Moderate convective instability (CAPE {peak_cape:.0f} J/kg).")

    if li <= -6:
        score += 5
        reasons.append(f"Severely unstable lapse rate (Lifted Index {li:.1f}).")

    if fz_ft <= 9500 and score >= 35:
        score += 5
        reasons.append(f"Low freezing level ({int(fz_ft):,} ft AGL) promotes rapid hail survival to ground level.")

    # Score clamping
    score = max(0, min(100, int(score)))

    # Determine Estimated dBZ Range
    if score >= 85:
        est_dbz = "60 - 70+ dBZ (Severe Hail Core / Wet Hail)"
    elif score >= 70:
        est_dbz = "52 - 60 dBZ (Large Hail & Heavy Torrent)"
    elif score >= 45:
        est_dbz = "45 - 52 dBZ (Small Hail / Heavy Rain)"
    elif score >= 20:
        est_dbz = "35 - 45 dBZ (Moderate Thunderstorm)"
    else:
        est_dbz = "< 30 dBZ (Light Rain / Clear)"

    # Determine Threat Level Category
    if score >= 90:
        level = "EMERGENCY"
        badge_class = "danger-emergency"
        action = "DESTRUCTIVE HAIL IMMINENT! Move immediately to interior shelter away from skylights & windows. Park vehicles under sturdy cover immediately."
    elif score >= 70:
        level = "WARNING"
        badge_class = "danger-warning"
        action = "SEVERE HAIL WARNING! Damaging hail (1+ inch) expected. Protect vehicles, stay indoors, and avoid exterior glass."
    elif score >= 45:
        level = "WATCH"
        badge_class = "danger-watch"
        action = "HAIL WATCH: Conditions favorable for severe hail. Monitor radar loop and prepare to move vehicles under cover."
    elif score >= 20:
        level = "MONITOR"
        badge_class = "danger-monitor"
        action = "ELEVATED CONVECTION: Minor hail / graupel possible in surrounding cells. Stay alert for updating radar signatures."
    else:
        level = "NONE"
        badge_class = "danger-none"
        action = "NO ACTIVE HAIL THREAT: No severe hail signatures or active warnings detected in your immediate zone."

    if score < 20:
        max_hail_in = 0.0
    elif max_hail_in == 0.0 and score >= 70:
        max_hail_in = 1.00
    elif max_hail_in == 0.0 and score >= 45:
        max_hail_in = 0.50

    return {
        'score': score,
        'level': level,
        'badge_class': badge_class,
        'max_hail_inches': max_hail_in,
        'max_hail_label': describe_hail_size(max_hail_in),
        'estimated_dbz': est_dbz,
        'action_recommendation': action,
        'reasons': reasons if reasons else ["Clear skies or stable atmospheric conditions. Zero active radar hail signatures."]
    }

def perform_full_assessment(lat, lon, radius_miles=45, hours=168):
    """
    Runs parallel queries across NWS, IEM LSR/mPING, Open-Meteo, RainViewer,
    active regional NWS warnings, and SPC convective outlooks.
    Combines results through the fusion engine and returns a comprehensive report.
    """
    with ThreadPoolExecutor(max_workers=6) as executor:
        f_alerts = executor.submit(fetch_nws_point_alerts, lat, lon)
        f_lsr = executor.submit(fetch_iem_lsr_reports, lat, lon, radius_miles=radius_miles, hours=hours)
        f_convective = executor.submit(fetch_open_meteo_convective, lat, lon)
        f_radar = executor.submit(fetch_rainviewer_radar)
        f_regional = executor.submit(fetch_active_nws_warnings, lat, lon, radius_miles=max(radius_miles * 2, 120))
        f_spc = executor.submit(fetch_spc_day1_outlook)

        def safe_future_result(future, default, sensor_name="Unknown"):
            try:
                res = future.result()
                return res if res is not None else default
            except Exception as e:
                print(f"[HailCore Sensor Degradation] {sensor_name} sensor failed: {e}")
                return default

        alerts = safe_future_result(f_alerts, [], "NWS Point Alerts")
        lsr_reports = safe_future_result(f_lsr, [], "IEM LSR / mPING")
        convective = safe_future_result(f_convective, {}, "Open-Meteo Convective")
        radar = safe_future_result(f_radar, {'host': 'https://tilecache.rainviewer.com', 'frames': []}, "RainViewer Radar")
        regional_res = safe_future_result(f_regional, {'warnings': [], 'geojson': {'type': 'FeatureCollection', 'features': []}}, "Regional NWS Warnings")
        spc_res = safe_future_result(f_spc, {'categorical': {'type': 'FeatureCollection', 'features': []}, 'hail': {'type': 'FeatureCollection', 'features': []}}, "SPC Outlook")

    regional_warnings = regional_res.get('warnings', [])
    assessment = evaluate_hail_risk(alerts, lsr_reports, convective, regional_warnings=regional_warnings)

    return {
        'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'location': {'latitude': lat, 'longitude': lon, 'radius_miles': radius_miles, 'hours': hours},
        'assessment': assessment,
        'nws_alerts': alerts,
        'lsr_reports': lsr_reports,
        'convective_data': convective,
        'radar_metadata': radar,
        'regional_warnings': regional_res,
        'spc_outlook': spc_res,
        'nearest_radar': find_nearest_nexrad(lat, lon)
    }


def evaluate_threat_threshold(lat, lon, min_hail=1.0, min_score=70, max_eta=45, radius_miles=45.0):
    """
    Evaluates current hail threat metrics against user-configured alert thresholds (FEAT-01).
    Prevents alert fatigue by validating if approaching severe storm ETA, hail diameter,
    and composite threat score breach user-specified alarm criteria.
    """
    min_hail_f = _safe_float(min_hail, 1.0)
    min_score_i = int(_safe_float(min_score, 70))
    max_eta_i = int(_safe_float(max_eta, 45))
    radius_f = _safe_float(radius_miles, 45.0)

    assessment_data = perform_full_assessment(lat, lon, radius_miles=radius_f)
    assessment = assessment_data.get('assessment', {}) if isinstance(assessment_data, dict) else {}

    score = _safe_float(assessment.get('score'), 0.0)
    level = str(assessment.get('level') or 'NONE')
    max_hail = _safe_float(assessment.get('max_hail_inches'), 0.0)
    max_hail_label = str(assessment.get('max_hail_label') or describe_hail_size(max_hail))

    # Identify nearest approaching storm ETA and warning proximity
    nearest_eta = None
    is_in_warning_zone = False

    regional_res = assessment_data.get('regional_warnings') if isinstance(assessment_data, dict) else {}
    regional_warnings = regional_res.get('warnings', []) if isinstance(regional_res, dict) else []

    for rw in regional_warnings:
        if not isinstance(rw, dict):
            continue
        dist = rw.get('distance_miles')
        if dist is not None and _safe_float(dist, 999.0) <= 12.0:
            is_in_warning_zone = True
        motion = rw.get('motion')
        if isinstance(motion, dict) and motion.get('eta_mins') is not None:
            eta = _safe_float(motion.get('eta_mins'), -1.0)
            if eta >= 0:
                if nearest_eta is None or eta < nearest_eta:
                    nearest_eta = int(round(eta))

    # Check point alerts for direct overhead warning
    nws_alerts = assessment_data.get('nws_alerts', []) if isinstance(assessment_data, dict) else []
    for a in nws_alerts:
        if isinstance(a, dict) and a.get('event'):
            ev = (a.get('event') or '').lower()
            if 'severe thunderstorm' in ev or 'tornado' in ev:
                is_in_warning_zone = True

    hail_met = (max_hail >= min_hail_f)
    score_met = (score >= min_score_i)
    eta_met = ((nearest_eta is not None and nearest_eta <= max_eta_i) or is_in_warning_zone)

    matched_reasons = []
    if hail_met:
        matched_reasons.append(f"Hail size ({max_hail:.2f}\") meets or exceeds threshold of {min_hail_f:.2f}\"")
    if score_met:
        matched_reasons.append(f"Threat score ({int(score)}%) meets or exceeds threshold of {min_score_i}%")
    if eta_met:
        if nearest_eta is not None:
            matched_reasons.append(f"Approaching storm ETA ({nearest_eta} mins) is within alert window of {max_eta_i} mins")
        else:
            matched_reasons.append("Target location is within active severe warning zone")

    # Trigger rule: at least one severity threshold met (hail or score) AND ETA is within window (or overhead)
    # If no storm motion ETA is available, trigger if hail or score met (overhead/in-zone threat)
    triggered = bool((hail_met or score_met) and (eta_met or nearest_eta is None))

    if triggered:
        directive = f"CRITICAL ACTION REQUIRED: {max_hail_label} hail threat breached alert threshold (Score: {int(score)}%). Sheltering vehicles and personnel mandatory."
    else:
        directive = "MONITORING ACTIVE: Current threat metrics remain within acceptable safety thresholds."

    return {
        "status": "ok",
        "timestamp": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        "rule_criteria": {
            "min_hail_inches": float(min_hail_f),
            "min_threat_score": int(min_score_i),
            "max_eta_minutes": int(max_eta_i),
            "radius_miles": float(radius_f)
        },
        "current_metrics": {
            "threat_score": int(score),
            "threat_level": level,
            "max_hail_inches": float(max_hail),
            "max_hail_label": max_hail_label,
            "nearest_storm_eta_mins": nearest_eta
        },
        "evaluation": {
            "triggered": triggered,
            "hail_threshold_met": bool(hail_met),
            "score_threshold_met": bool(score_met),
            "eta_threshold_met": bool(eta_met),
            "matched_reasons": matched_reasons
        },
        "directive": directive
    }


def is_point_in_bbox(lat, lon, min_lat, min_lon, max_lat, max_lon):
    """Checks if a point coordinate (lat, lon) is contained in the bounding box."""
    if lat is None or lon is None:
        return False
    try:
        f_lat = float(lat)
        f_lon = float(lon)
        if math.isnan(f_lat) or math.isinf(f_lat) or math.isnan(f_lon) or math.isinf(f_lon):
            return False
        return (min_lat <= f_lat <= max_lat) and (min_lon <= f_lon <= max_lon)
    except (TypeError, ValueError):
        return False


def is_geometry_intersecting_bbox(geom, min_lat, min_lon, max_lat, max_lon):
    """Checks if a GeoJSON polygon, multipolygon, or point intersects the bounding box."""
    if not isinstance(geom, dict):
        return False
    coords = geom.get('coordinates')
    gtype = geom.get('type')
    if not coords or not isinstance(coords, list):
        return False

    pts = []
    if gtype == 'Point' and len(coords) >= 2:
        return is_point_in_bbox(coords[1], coords[0], min_lat, min_lon, max_lat, max_lon)
    elif gtype == 'Polygon' and coords:
        pts = coords[0]
    elif gtype == 'MultiPolygon' and coords and coords[0]:
        pts = coords[0][0]

    if not pts:
        return False

    poly_min_lat = 90.0
    poly_max_lat = -90.0
    poly_min_lon = 180.0
    poly_max_lon = -180.0

    for p in pts:
        if isinstance(p, (list, tuple)) and len(p) >= 2:
            try:
                p_lon = float(p[0])
                p_lat = float(p[1])
                if is_point_in_bbox(p_lat, p_lon, min_lat, min_lon, max_lat, max_lon):
                    return True
                if p_lat < poly_min_lat: poly_min_lat = p_lat
                if p_lat > poly_max_lat: poly_max_lat = p_lat
                if p_lon < poly_min_lon: poly_min_lon = p_lon
                if p_lon > poly_max_lon: poly_max_lon = p_lon
            except (TypeError, ValueError):
                continue

    # Bounding box rectangle overlap test
    overlap = not (
        poly_max_lat < min_lat or
        poly_min_lat > max_lat or
        poly_max_lon < min_lon or
        poly_min_lon > max_lon
    )
    return overlap


def scan_hail_bbox(min_lat, min_lon, max_lat, max_lon, min_hail=0.0, hours=24):
    """
    Scans a geographic bounding box or highway transit corridor for severe hail threats (FEAT-02).
    Queries active NWS warnings and IEM LSR hail reports, spatial filters them to the box,
    and computes aggregated corridor metrics and composite danger score.
    """
    min_lat_f = float(min_lat)
    min_lon_f = float(min_lon)
    max_lat_f = float(max_lat)
    max_lon_f = float(max_lon)
    min_hail_f = _safe_float(min_hail, 0.0)
    hours_i = max(1, min(720, int(_safe_float(hours, 24))))

    center_lat = (min_lat_f + max_lat_f) / 2.0
    center_lon = (min_lon_f + max_lon_f) / 2.0

    corners = [
        (min_lat_f, min_lon_f),
        (min_lat_f, max_lon_f),
        (max_lat_f, min_lon_f),
        (max_lat_f, max_lon_f)
    ]
    corner_dists = [haversine_distance(center_lat, center_lon, c_lat, c_lon) for c_lat, c_lon in corners]
    query_radius = max(corner_dists) + 15.0

    # Fetch active warnings and LSR reports within the enclosing radius
    warnings_res = fetch_active_nws_warnings(lat=center_lat, lon=center_lon, radius_miles=max(query_radius, 60.0))
    all_warnings = warnings_res.get('warnings', []) if isinstance(warnings_res, dict) else []
    all_reports = fetch_iem_lsr_reports(lat=center_lat, lon=center_lon, radius_miles=max(query_radius, 60.0), hours=hours_i)

    contained_warnings = []
    for w in all_warnings:
        if not isinstance(w, dict):
            continue
        c_lat = w.get('center_lat')
        c_lon = w.get('center_lon')
        if c_lat is not None and c_lon is not None and is_point_in_bbox(c_lat, c_lon, min_lat_f, min_lon_f, max_lat_f, max_lon_f):
            contained_warnings.append(w)
        elif is_geometry_intersecting_bbox(w.get('geometry'), min_lat_f, min_lon_f, max_lat_f, max_lon_f):
            contained_warnings.append(w)

    contained_reports = []
    for r in all_reports:
        if not isinstance(r, dict):
            continue
        r_lat = r.get('latitude')
        r_lon = r.get('longitude')
        r_hail = _safe_float(r.get('hail_size_in'), 0.0)
        if is_point_in_bbox(r_lat, r_lon, min_lat_f, min_lon_f, max_lat_f, max_lon_f) and r_hail >= min_hail_f:
            contained_reports.append(r)

    # Compute corridor metrics
    total_warnings = len(contained_warnings)
    total_hail_reports = len(contained_reports)

    max_hail_inches = 0.0
    for w in contained_warnings:
        wh = _safe_float(w.get('hail_size_in'), 0.0)
        if wh > max_hail_inches:
            max_hail_inches = wh
    for r in contained_reports:
        rh = _safe_float(r.get('hail_size_in'), 0.0)
        if rh > max_hail_inches:
            max_hail_inches = rh

    max_hail_label = describe_hail_size(max_hail_inches)

    highest_severity = "NONE"
    if any(w.get('category') == 'TORNADO' or 'tornado' in (w.get('event') or '').lower() for w in contained_warnings):
        highest_severity = "TORNADO"
    elif any(w.get('category') == 'DESTRUCTIVE_HAIL' for w in contained_warnings) or max_hail_inches >= 2.0:
        highest_severity = "DESTRUCTIVE_HAIL"
    elif any(w.get('category') == 'SEVERE_TSTORM' for w in contained_warnings) or max_hail_inches >= 1.0:
        highest_severity = "SEVERE_TSTORM"
    elif any(w.get('category') == 'WATCH' for w in contained_warnings):
        highest_severity = "WATCH"
    elif any(w.get('category') == 'ADVISORY' or w.get('category') == 'FLASH_FLOOD' for w in contained_warnings) or max_hail_inches > 0:
        highest_severity = "ADVISORY"

    sev_map = {"TORNADO": 85, "DESTRUCTIVE_HAIL": 75, "SEVERE_TSTORM": 60, "WATCH": 35, "ADVISORY": 25, "NONE": 0}
    base_score = sev_map.get(highest_severity, 0)
    warn_bonus = min(20, total_warnings * 5)
    report_bonus = min(20, total_hail_reports * 3)
    hail_bonus = 15 if max_hail_inches >= 2.0 else (10 if max_hail_inches >= 1.5 else (5 if max_hail_inches >= 1.0 else 0))

    composite_score = max(0, min(100, int(base_score + warn_bonus + report_bonus + hail_bonus)))
    if highest_severity == "TORNADO":
        composite_score = max(composite_score, 88)
    elif highest_severity == "DESTRUCTIVE_HAIL":
        composite_score = max(composite_score, 75)
    elif highest_severity == "SEVERE_TSTORM":
        composite_score = max(composite_score, 60)

    # Build GeoJSON FeatureCollection
    features = []
    for w in contained_warnings:
        features.append({
            'type': 'Feature',
            'id': w.get('id'),
            'geometry': w.get('geometry'),
            'properties': {
                'id': w.get('id'),
                'event': w.get('event'),
                'category': w.get('category'),
                'severity': w.get('severity'),
                'hail_size_in': w.get('hail_size_in'),
                'hail_label': w.get('hail_label'),
                'headline': w.get('headline'),
                'style': w.get('style')
            }
        })
    for r in contained_reports:
        features.append({
            'type': 'Feature',
            'geometry': {
                'type': 'Point',
                'coordinates': [r.get('longitude'), r.get('latitude')]
            },
            'properties': {
                'type': 'LSR_REPORT',
                'source': r.get('source_type'),
                'hail_size_in': r.get('hail_size_in'),
                'hail_description': r.get('hail_description'),
                'valid': r.get('valid'),
                'city': r.get('city'),
                'remark': r.get('remark')
            }
        })

    return {
        "status": "ok",
        "bbox": {
            "min_lat": min_lat_f,
            "min_lon": min_lon_f,
            "max_lat": max_lat_f,
            "max_lon": max_lon_f
        },
        "corridor_metrics": {
            "total_warnings": total_warnings,
            "total_hail_reports": total_hail_reports,
            "max_hail_inches": round(max_hail_inches, 2),
            "max_hail_label": max_hail_label,
            "highest_severity": highest_severity,
            "composite_corridor_score": composite_score
        },
        "contained_warnings": contained_warnings,
        "contained_reports": contained_reports,
        "geojson": {
            "type": "FeatureCollection",
            "features": features
        }
    }


def generate_threat_dossier(lat, lon, radius_miles=45.0, format_type='text'):
    """
    Generates an authoritative Operations Threat Dossier export (FEAT-03).
    Supports plain text operations briefing, RFC 4180 CSV, and structured JSON.
    """
    lat_f = float(lat)
    lon_f = float(lon)
    radius_f = _safe_float(radius_miles, 45.0)
    fmt = str(format_type).lower().strip()

    assessment_data = perform_full_assessment(lat_f, lon_f, radius_miles=radius_f)
    assessment = assessment_data.get('assessment', {}) if isinstance(assessment_data, dict) else {}
    nws_alerts = assessment_data.get('nws_alerts', []) if isinstance(assessment_data, dict) else []
    lsr_reports = assessment_data.get('lsr_reports', []) if isinstance(assessment_data, dict) else []
    regional_res = assessment_data.get('regional_warnings', {}) if isinstance(assessment_data, dict) else {}
    regional_warnings = regional_res.get('warnings', []) if isinstance(regional_res, dict) else []
    convective = assessment_data.get('convective_data', {}) if isinstance(assessment_data, dict) else {}

    timestamp = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    lat_card = 'N' if lat_f >= 0 else 'S'
    lon_card = 'E' if lon_f >= 0 else 'W'
    dossier_id = f"HW-{time.strftime('%Y%m%d', time.gmtime())}-{abs(lat_f):.2f}{lat_card}-{abs(lon_f):.2f}{lon_card}"

    if fmt == 'json':
        return {
            "status": "ok",
            "dossier_id": dossier_id,
            "timestamp": timestamp,
            "location": {
                "latitude": lat_f,
                "longitude": lon_f,
                "radius_miles": radius_f
            },
            "assessment": assessment,
            "contained_warnings": regional_warnings,
            "nws_alerts": nws_alerts,
            "contained_reports": lsr_reports,
            "convective_data": convective,
            "geojson": regional_res.get('geojson', {'type': 'FeatureCollection', 'features': []})
        }

    elif fmt == 'csv':
        output = io.StringIO()
        writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL, lineterminator='\r\n')

        # Section 1: Assessment Key Metrics
        writer.writerow(["# HAILWARN OPERATIONS THREAT DOSSIER - ASSESSMENT KEY METRICS"])
        writer.writerow([
            "Dossier ID", "Timestamp UTC", "Latitude", "Longitude", "Radius Miles",
            "Threat Score", "Threat Level", "Max Hail Inches", "Max Hail Label",
            "Estimated dBZ", "CAPE J/kg", "Lifted Index", "CIN J/kg",
            "Freezing Level Ft", "Hail Potential Index", "Action Recommendation"
        ])
        writer.writerow([
            dossier_id,
            timestamp,
            f"{lat_f:.4f}",
            f"{lon_f:.4f}",
            f"{radius_f:.1f}",
            assessment.get('score', 0),
            assessment.get('level', 'NONE'),
            assessment.get('max_hail_inches', 0.0),
            assessment.get('max_hail_label', 'None'),
            assessment.get('estimated_dbz', 'N/A'),
            convective.get('cape_j_kg', 'N/A'),
            convective.get('lifted_index', 'N/A'),
            convective.get('cin_j_kg', 'N/A'),
            convective.get('freezing_level_ft', 'N/A'),
            convective.get('hail_potential_index', 'N/A'),
            assessment.get('action_recommendation', 'N/A')
        ])
        writer.writerow([])

        # Section 2: Detailed Warnings and Reports
        writer.writerow(["# DETAILED WARNINGS AND GROUND OBSERVATIONS LOG"])
        writer.writerow([
            "Record Type", "ID/Source", "Event/Category", "Severity/Threat",
            "Hail Size Inches", "Hail Description", "Distance Miles", "Valid Time",
            "Latitude", "Longitude", "Remark/Description"
        ])

        for w in regional_warnings:
            writer.writerow([
                "WARNING",
                w.get('id', 'N/A'),
                w.get('event', 'N/A'),
                w.get('severity', 'N/A'),
                w.get('hail_size_in') if w.get('hail_size_in') is not None else "0.0",
                w.get('hail_label', 'N/A'),
                w.get('distance_miles', 'N/A'),
                w.get('effective', 'N/A'),
                w.get('center_lat', 'N/A'),
                w.get('center_lon', 'N/A'),
                (w.get('headline') or w.get('description', ''))[:200]
            ])

        for r in lsr_reports:
            writer.writerow([
                "GROUND_REPORT",
                r.get('source_type', 'Spotter/mPING'),
                r.get('type', 'HAIL'),
                "CONFIRMED",
                r.get('hail_size_in', 0.0),
                r.get('hail_description', 'N/A'),
                r.get('distance_miles', 'N/A'),
                r.get('valid', 'N/A'),
                r.get('latitude', 'N/A'),
                r.get('longitude', 'N/A'),
                r.get('remark', '')
            ])

        return output.getvalue()

    else:
        # Default: Formatted ASCII text
        score = assessment.get('score', 0)
        level = assessment.get('level', 'NONE')
        max_hail_in = assessment.get('max_hail_inches', 0.0)
        max_hail_lbl = assessment.get('max_hail_label', 'None')
        est_dbz = assessment.get('estimated_dbz', '< 30 dBZ')
        rec = assessment.get('action_recommendation', 'No action required.')
        reasons = assessment.get('reasons', ['Clear skies or stable conditions.'])

        cape = convective.get('cape_j_kg', 'N/A')
        li = convective.get('lifted_index', 'N/A')
        cin = convective.get('cin_j_kg', 'N/A')
        cin_status = convective.get('cin_cap_status', 'Unknown')
        fz_ft = convective.get('freezing_level_ft', 'N/A')
        fz_factor = convective.get('hail_survival_factor', 'Standard')
        hpi = convective.get('hail_potential_index', 'N/A')

        reasons_txt = "\n".join(f"  * {r}" for r in reasons)

        warnings_txt = ""
        if regional_warnings:
            warnings_txt = "\n".join(
                f"  [{w.get('severity', 'Severe').upper()}] {w.get('event', 'Warning')} - Hail: {w.get('hail_size_in', 'N/A')}\" "
                f"(Distance: {w.get('distance_miles', 'N/A')} mi) | {w.get('headline', '')}"
                for w in regional_warnings[:10]
            )
        else:
            warnings_txt = "  No active NWS warnings currently in perimeter."

        reports_txt = ""
        if lsr_reports:
            reports_txt = "\n".join(
                f"  * {r.get('source_type', 'Observer')}: {r.get('hail_description', 'Hail')} at {r.get('city', 'Area')}, {r.get('state', '')} "
                f"({r.get('distance_miles', 'N/A')} mi away) - {r.get('remark', '')}"
                for r in lsr_reports[:10]
            )
        else:
            reports_txt = "  No ground truth hail reports filed in time horizon."

        return f"""================================================================================
HAILWARN SEVERE WEATHER OPERATIONS THREAT DOSSIER
================================================================================
DOSSIER ID:      {dossier_id}
GENERATED (UTC): {timestamp}
COORDINATES:     {abs(lat_f):.4f}° {lat_card}, {abs(lon_f):.4f}° {lon_card}
SEARCH RADIUS:   {radius_f:.1f} statute miles
--------------------------------------------------------------------------------
1. EXECUTIVE THREAT ASSESSMENT
--------------------------------------------------------------------------------
HAIL RISK SCORE:         {score}%
THREAT SEVERITY LEVEL:   {level}
MAX HAIL DIAMETER:       {max_hail_in:.2f}" ({max_hail_lbl})
EST. RADAR REFLECTIVITY: {est_dbz}

OPERATIONAL DIRECTIVE:
>> {rec}

EVALUATION REASONS:
{reasons_txt}

--------------------------------------------------------------------------------
2. ACTIVE NWS WARNING BULLETINS ({len(regional_warnings)})
--------------------------------------------------------------------------------
{warnings_txt}

--------------------------------------------------------------------------------
3. VERIFIED GROUND TRUTH OBSERVATIONS ({len(lsr_reports)})
--------------------------------------------------------------------------------
{reports_txt}

--------------------------------------------------------------------------------
4. ATMOSPHERIC CONVECTIVE SOUNDING PROFILE
--------------------------------------------------------------------------------
CAPE ENERGY:          {cape} J/kg
LIFTED INDEX:         {li}
CONVECTIVE CAP (CIN): {cin} J/kg ({cin_status})
FREEZING LEVEL (AGL): {fz_ft} ft ({fz_factor})
HAIL POTENTIAL INDEX: {hpi}/100

--------------------------------------------------------------------------------
5. PROTECTIVE ACTION CHECKLIST
--------------------------------------------------------------------------------
[ ] Alert operations dispatch and mobile ground units
[ ] Secure open vehicle inventory, fleet assets, and aircraft in hangars
[ ] Close high-exposure skylights and protect solar array installations
[ ] Direct personnel indoors away from exterior glass & structural openings
================================================================================
END OF OPERATIONS DOSSIER - HAILWARN RAPID RESPONSE ENGINE
================================================================================
"""

