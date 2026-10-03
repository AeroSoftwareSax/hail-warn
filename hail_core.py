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

def describe_hail_size(inches):
    """Returns human-readable descriptive name for a hail diameter in inches."""
    if not inches or inches <= 0:
        return "None"
    closest = None
    min_diff = 999.0
    for size, label in HAIL_SIZE_DESCRIPTIONS:
        diff = abs(size - inches)
        if diff < min_diff:
            min_diff = diff
            closest = label
    if inches >= 4.5:
        return f"Softball+ ({inches:.2f} in) - Catastrophic"
    return closest or f"{inches:.2f} in"

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
    data = http_get_json(url)
    if not data or 'features' not in data:
        return []

    alerts = []
    for f in data.get('features', []):
        if not isinstance(f, dict):
            continue
        props = f.get('properties') or {}
        geom = f.get('geometry')
        event = props.get('event') or ''
        severity = props.get('severity') or ''
        headline = props.get('headline') or ''
        description = props.get('description') or ''
        instruction = props.get('instruction') or ''
        parameters = props.get('parameters') or {}
        hail_tags = parameters.get('maxHailSize') or []
        hail_threat = parameters.get('hailThreat') or []

        # Extract hail size from parameters or regex in description
        hail_size = None
        if hail_tags:
            try:
                hail_size = float(hail_tags[0])
            except (ValueError, TypeError):
                pass
        
        if hail_size is None and description:
            # Look for patterns like "HAIL...1.75 INCHES" or "hail up to quarter size"
            match = re.search(r'HAIL(?:\s+THREAT)?\.\.\.([0-9\.]+)\s*(?:IN|INCHES)?', description, re.IGNORECASE)
            if match:
                try:
                    hail_size = float(match.group(1))
                except ValueError:
                    pass

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
            'hail_threat_type': hail_threat[0] if hail_threat else 'RADAR INDICATED',
            'effective': props.get('effective'),
            'expires': props.get('expires'),
            'geometry': geom,
            'is_severe_hail': (hail_size is not None and hail_size >= 1.0) or ('Severe Thunderstorm' in event and 'hail' in description.lower())
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
                pass

        # If magnitude missing or not float, try to extract from remark
        if mag_float is None and remark:
            m = re.search(r'([0-9\.]+)\s*(?:in|inch|\"|\binches\b)', remark, re.IGNORECASE)
            if m:
                try:
                    mag_float = float(m.group(1))
                except ValueError:
                    pass

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

    capes = [v for v in (hourly.get('cape') or [0]) if v is not None]
    lifted_indices = [v for v in (hourly.get('lifted_index') or [0]) if v is not None]
    cins = [v for v in (hourly.get('convective_inhibition') or [0]) if v is not None]
    raw_fz = [v for v in (hourly.get('freezing_level_height') or []) if v is not None and v > 0]
    freezing_lvl_m = raw_fz[0] if raw_fz else 3500
    freezing_lvl_ft = round(freezing_lvl_m * 3.28084)

    cape_val = capes[0] if capes else 0
    li_val = lifted_indices[0] if lifted_indices else 0
    max_cape = max(capes) if capes else 0
    min_li = min(lifted_indices) if lifted_indices else 0
    cin_val = abs(cins[0]) if cins else 0

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

    temp_c = current.get('temperature_2m', 20)
    temp_f = round((temp_c * 9/5) + 32, 1) if temp_c is not None else None
    wind_kmh = current.get('wind_speed_10m', 0) or 0
    wind_mph = round(wind_kmh * 0.621371, 1)
    gusts_kmh = current.get('wind_gusts_10m', 0) or 0
    gusts_mph = round(gusts_kmh * 0.621371, 1)
    surf_press = current.get('surface_pressure', 1013.25) or 1013.25
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
    for f in nws_data.get('features', []):
        props = f.get('properties', {})
        geom = f.get('geometry')
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

        params = props.get('parameters') or {}
        hail_tags = params.get('maxHailSize') or []
        wind_tags = params.get('maxWindGust') or []
        motion_tags = params.get('eventMotionDescription') or []
        hail_threat = params.get('hailThreat') or ['RADAR INDICATED']
        wind_threat = params.get('windThreat') or ['RADAR INDICATED']
        tornado_det = params.get('tornadoDetection') or ['']

        hail_size = None
        if hail_tags:
            try:
                hail_size = float(hail_tags[0])
            except (ValueError, TypeError):
                pass
        
        description = props.get('description', '') or ''
        headline = props.get('headline', '') or ''
        instruction = props.get('instruction', '') or ''

        if hail_size is None and description:
            m = re.search(r'HAIL(?:\s+THREAT)?\.\.\.([0-9\.]+)\s*(?:IN|INCHES)?', description, re.IGNORECASE)
            if m:
                try:
                    hail_size = float(m.group(1))
                except ValueError:
                    pass

        # Calculate centroid & distance
        center_lat, center_lon = None, None
        coords = geom.get('coordinates', [])
        pts = []
        try:
            if geom.get('type') == 'Polygon':
                pts = coords[0]
            elif geom.get('type') == 'MultiPolygon':
                pts = coords[0][0]
            if pts:
                center_lon = sum(p[0] for p in pts) / len(pts)
                center_lat = sum(p[1] for p in pts) / len(pts)
        except Exception:
            pass

        dist_mi = None
        if lat is not None and lon is not None and center_lat is not None:
            dist_mi = round(haversine_distance(lat, lon, center_lat, center_lon), 1)

        # Parse storm motion vector
        motion_obj = None
        if motion_tags:
            m_str = motion_tags[0]
            m_match = re.search(r'(\d+)DEG\.\.\.(\d+)KT\.\.\.([\d\.\-]+),([\d\.\-]+)', m_str)
            if m_match:
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

        is_tornado = 'Tornado' in event
        is_severe_tstorm = 'Severe Thunderstorm' in event
        is_flash_flood = 'Flash Flood' in event or 'Flood' in event
        is_special = 'Special Weather' in event or 'Statement' in event
        is_watch = 'Watch' in event
        is_destructive = (is_severe_tstorm and ((hail_size and hail_size >= 2.0) or (wind_tags and '80' in wind_tags[0]))) or is_tornado

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
            'hail_threat_type': hail_threat[0] if hail_threat else 'RADAR INDICATED',
            'wind_gust': wind_tags[0] if wind_tags else None,
            'wind_threat_type': wind_threat[0] if wind_threat else 'RADAR INDICATED',
            'tornado_detection': tornado_det[0] if tornado_det else None,
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
    for f in iem_data.get('features', []):
        props = f.get('properties', {})
        geom = f.get('geometry')
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
                pass

        wind_tag = props.get('max_windtag') or props.get('windtag')
        wind_str = f"{wind_tag} MPH" if wind_tag else None

        center_lat, center_lon = None, None
        coords = geom.get('coordinates', [])
        pts = []
        try:
            if geom.get('type') == 'Polygon':
                pts = coords[0]
            elif geom.get('type') == 'MultiPolygon':
                pts = coords[0][0]
            if pts:
                center_lon = sum(p[0] for p in pts) / len(pts)
                center_lat = sum(p[1] for p in pts) / len(pts)
        except Exception:
            pass

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
    data = http_get_json(url)
    hotspots = []
    for f in (data.get('features') or []):
        if not isinstance(f, dict):
            continue
        props = f.get('properties') or {}
        event = props.get('event') or ''
        geom = f.get('geometry')
        params = props.get('parameters') or {}
        hail_size_list = params.get('maxHailSize') or [None]
        hail_size = hail_size_list[0] if hail_size_list else None
        
        is_relevant = any(k in event for k in ['Severe Thunderstorm', 'Tornado', 'Special Weather'])
        if is_relevant:
            center_lat, center_lon = None, None
            if geom and geom.get('coordinates'):
                coords = geom['coordinates']
                try:
                    if geom['type'] == 'Polygon':
                        pts = coords[0]
                        center_lon = sum(p[0] for p in pts) / len(pts)
                        center_lat = sum(p[1] for p in pts) / len(pts)
                    elif geom['type'] == 'MultiPolygon':
                        pts = coords[0][0]
                        center_lon = sum(p[0] for p in pts) / len(pts)
                        center_lat = sum(p[1] for p in pts) / len(pts)
                except Exception:
                    pass
            
            area_desc = props.get('areaDesc', 'Active Threat Area')
            hail_f = None
            if hail_size:
                try:
                    hail_f = float(hail_size)
                except ValueError:
                    pass

            if center_lat and center_lon:
                hotspots.append({
                    'event': event,
                    'headline': props.get('headline'),
                    'area': area_desc.split(';')[0].strip(),
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
        event = (a.get('event') or '').lower()
        hail = a.get('hail_size_in') or 0.0
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
            dist = rw.get('distance_miles')
            if dist is None:
                continue
            hail = rw.get('hail_size_in') or 0.0
            motion = rw.get('motion')
            ev = rw.get('event') or ''
            eta = motion.get('eta_mins') if isinstance(motion, dict) else None

            if eta is not None and eta <= 50 and (hail >= 0.75 or 'Severe' in ev or 'Tornado' in ev):
                b_score = 85 if hail >= 1.75 else 75
                score = max(score, b_score)
                if hail > max_hail_in:
                    max_hail_in = hail
                reasons.append(
                    f"APPROACHING THREAT: {ev} with {describe_hail_size(hail)} hail tracked {dist:.1f} mi away, ETA ~{eta} mins!"
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
        dist = r.get('distance_miles') if r.get('distance_miles') is not None else 999.0
        hail = r.get('hail_size_in') if r.get('hail_size_in') is not None else 0.0
        age = r.get('age_hours') if r.get('age_hours') is not None else 999.0
        
        if age <= 24.0 and 0.0 < hail <= 8.0 and hail > max_hail_in:
            max_hail_in = hail

        if age <= 24.0 and dist < closest_dist and hail > 0:
            closest_dist = dist
            closest_recent_report = r

    if closest_recent_report:
        hail = closest_recent_report.get('hail_size_in') if closest_recent_report.get('hail_size_in') is not None else 0.0
        dist = closest_recent_report.get('distance_miles') if closest_recent_report.get('distance_miles') is not None else 999.0
        is_mping = closest_recent_report.get('is_mping', False)
        age = closest_recent_report.get('age_hours') if closest_recent_report.get('age_hours') is not None else 999.0
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
    cape = c_data.get('cape_j_kg') if c_data.get('cape_j_kg') is not None else 0
    li = c_data.get('lifted_index') if c_data.get('lifted_index') is not None else 0.0
    peak_cape = c_data.get('peak_cape_today') if c_data.get('peak_cape_today') is not None else 0
    cin_cap = c_data.get('cin_cap_strength') or 'WEAK'
    fz_ft = c_data.get('freezing_level_ft') if c_data.get('freezing_level_ft') is not None else 12000

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
        reasons.append(f"Low freezing level ({fz_ft:,} ft AGL) promotes rapid hail survival to ground level.")

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
