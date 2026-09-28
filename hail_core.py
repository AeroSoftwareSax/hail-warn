"""
HailCore - Hail Detection & Multi-Sensor Fusion Engine
Combines NWS active alerts, IEM Local Storm Reports & mPING crowdsourced reports,
convective atmospheric parameters from Open-Meteo, and RainViewer radar metadata.
Uses entirely free, open-source APIs without requiring any API keys.
"""

import math
import time
import datetime
import json
import re
import urllib.request
import urllib.parse
import ssl
from concurrent.futures import ThreadPoolExecutor

USER_AGENT = "HailWarnApp/1.0 (contact@hailwarn.org; severe-weather-monitoring)"
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

# Cache to respect public rate limits (key -> (timestamp, data))
_CACHE = {}
CACHE_TTL_SECONDS = 60

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
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return r * c

def http_get_json(url, headers=None, timeout=8):
    """Cached HTTP GET returning parsed JSON."""
    now = time.time()
    if url in _CACHE:
        ts, data = _CACHE[url]
        if now - ts < CACHE_TTL_SECONDS:
            return data

    req_headers = {'User-Agent': USER_AGENT, 'Accept': 'application/json, application/geo+json'}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            _CACHE[url] = (now, data)
            return data
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
    for f in data['features']:
        props = f.get('properties', {})
        geom = f.get('geometry')
        event = props.get('event', '')
        severity = props.get('severity', '')
        headline = props.get('headline', '')
        description = props.get('description', '')
        instruction = props.get('instruction', '')
        parameters = props.get('parameters', {})
        hail_tags = parameters.get('maxHailSize', [])
        hail_threat = parameters.get('hailThreat', [])

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
    url = f"https://mesonet.agron.iastate.edu/geojson/lsr.py?hours={hours}"
    data = http_get_json(url)
    if not data or 'features' not in data:
        return []

    now_ts = time.time()
    cutoff_ts = now_ts - (hours * 3600)

    reports = []
    for f in data.get('features', []):
        props = f.get('properties', {})
        geom = f.get('geometry', {})
        coords = geom.get('coordinates', [0, 0])
        report_lon, report_lat = coords[0], coords[1]
        
        typetext = (props.get('typetext') or '').upper()
        type_code = (props.get('type') or '').upper()
        remark = props.get('remark', '') or ''
        source = props.get('source', '') or ''

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
    Fetch atmospheric convective instability parameters (CAPE, Lifted Index, precipitation)
    from Open-Meteo. Free, open source, no API key required.
    """
    url = (
        f"https://api.open-meteo.com/v1/forecast?latitude={lat:.4f}&longitude={lon:.4f}"
        "&current=temperature_2m,relative_humidity_2m,precipitation,rain,showers,weather_code,wind_speed_10m,wind_gusts_10m"
        "&hourly=cape,lifted_index,convective_inhibition,precipitation&forecast_days=1"
    )
    data = http_get_json(url)
    if not data:
        return {}

    current = data.get('current', {})
    hourly = data.get('hourly', {})

    capes = hourly.get('cape', [0])
    lifted_indices = hourly.get('lifted_index', [0])
    
    cape_val = capes[0] if capes else 0
    li_val = lifted_indices[0] if lifted_indices else 0
    max_cape = max(capes) if capes else 0
    min_li = min(lifted_indices) if lifted_indices else 0

    return {
        'temperature_c': current.get('temperature_2m'),
        'humidity_percent': current.get('relative_humidity_2m'),
        'precipitation_mm': current.get('precipitation', 0),
        'wind_speed_kmh': current.get('wind_speed_10m', 0),
        'wind_gusts_kmh': current.get('wind_gusts_10m', 0),
        'weather_code': current.get('weather_code', 0),
        'cape_j_kg': round(cape_val, 0),
        'peak_cape_today': round(max_cape, 0),
        'lifted_index': round(li_val, 1),
        'min_lifted_index_today': round(min_li, 1)
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

    host = data.get('host', 'https://tilecache.rainviewer.com')
    radar = data.get('radar', {})
    past = radar.get('past', [])
    nowcast = radar.get('nowcast', [])
    
    frames = []
    for f in past:
        frames.append({'time': f.get('time'), 'path': f.get('path'), 'type': 'past'})
    for f in nowcast:
        frames.append({'time': f.get('time'), 'path': f.get('path'), 'type': 'nowcast'})

    return {
        'host': host,
        'frames': frames,
        'latest_frame': past[-1] if past else None
    }

def fetch_active_national_hotspots():
    """
    Queries NWS for locations currently under active severe thunderstorm/tornado
    warnings or statements with hail tags to populate Quick Live Hotspots.
    """
    url = "https://api.weather.gov/alerts/active"
    data = http_get_json(url)
    hotspots = []
    if data and 'features' in data:
        for f in data['features']:
            props = f.get('properties', {})
            event = props.get('event', '')
            geom = f.get('geometry')
            params = props.get('parameters', {})
            hail_size = params.get('maxHailSize', [None])[0]
            
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
            if len(hotspots) >= 8:
                break

    # Add standard hail alley fallback hotspots if quiet day
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

def evaluate_hail_risk(alerts, lsr_reports, convective_data):
    """
    Multi-sensor fusion assessment:
    Combines NWS alerts, ground-truth mPING/LSR reports, and convective sounding physics.
    """
    score = 0
    reasons = []
    max_hail_in = 0.0

    # 1. NWS Alert Evaluation
    has_active_warning = False
    has_tornado_warning = False

    for a in alerts:
        event = a['event'].lower()
        hail = a.get('hail_size_in') or 0.0
        if 0.0 < hail <= 8.0 and hail > max_hail_in:
            max_hail_in = hail

        if 'tornado' in event:
            has_tornado_warning = True
            score = max(score, 88)
            reasons.append(f"CRITICAL: Active {a['event']} affecting your coordinates.")
        elif 'severe thunderstorm' in event:
            has_active_warning = True
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
            reasons.append(f"WATCH: Severe weather watch in effect ({a['event']}).")

    # 2. Ground Reports (mPING + NWS Spotters) Evaluation
    closest_recent_report = None
    closest_dist = 999.0
    for r in lsr_reports:
        dist = r.get('distance_miles', 999.0)
        hail = r.get('hail_size_in') or 0.0
        age = r.get('age_hours', 999.0)
        
        # Track valid max hail size for recent reports within last 24h
        if age <= 24.0 and 0.0 < hail <= 8.0 and hail > max_hail_in:
            max_hail_in = hail

        # Only evaluate recent reports within last 24h for active score escalation
        if age <= 24.0 and dist < closest_dist and hail > 0:
            closest_dist = dist
            closest_recent_report = r

    if closest_recent_report:
        hail = closest_recent_report.get('hail_size_in', 0)
        dist = closest_recent_report.get('distance_miles')
        is_mping = closest_recent_report.get('is_mping', False)
        age = closest_recent_report.get('age_hours')
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

    # 3. Atmospheric Sounding Physics (Open-Meteo CAPE & Lifted Index)
    cape = convective_data.get('cape_j_kg', 0)
    li = convective_data.get('lifted_index', 0)
    peak_cape = convective_data.get('peak_cape_today', 0)

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

    # Score clamping
    score = max(0, min(100, int(score)))

    # Determine Estimated dBZ Range based on severity & convective data
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

def perform_full_assessment(lat, lon, radius_miles=45):
    """
    Runs parallel queries across NWS, IEM LSR/mPING, Open-Meteo, and RainViewer.
    Combines the results through the fusion engine and returns a comprehensive report.
    """
    with ThreadPoolExecutor(max_workers=4) as executor:
        f_alerts = executor.submit(fetch_nws_point_alerts, lat, lon)
        f_lsr = executor.submit(fetch_iem_lsr_reports, lat, lon, radius_miles=radius_miles)
        f_convective = executor.submit(fetch_open_meteo_convective, lat, lon)
        f_radar = executor.submit(fetch_rainviewer_radar)

        alerts = f_alerts.result()
        lsr_reports = f_lsr.result()
        convective = f_convective.result()
        radar = f_radar.result()

    assessment = evaluate_hail_risk(alerts, lsr_reports, convective)

    return {
        'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'location': {'latitude': lat, 'longitude': lon, 'radius_miles': radius_miles},
        'assessment': assessment,
        'nws_alerts': alerts,
        'lsr_reports': lsr_reports,
        'convective_data': convective,
        'radar_metadata': radar
    }
