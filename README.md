# ⚡ HAILWARN - Severe Hail Detection & Early Warning Radar System

[![Security & Vulnerability Pipeline](https://github.com/AeroSoftwareSax/hail-warn/actions/workflows/security.yml/badge.svg)](https://github.com/AeroSoftwareSax/hail-warn/actions/workflows/security.yml)
[![CodeQL Security Analysis](https://github.com/AeroSoftwareSax/hail-warn/actions/workflows/codeql.yml/badge.svg)](https://github.com/AeroSoftwareSax/hail-warn/actions/workflows/codeql.yml)
[![Security: Gitleaks](https://img.shields.io/badge/security-gitleaks-blue.svg)](https://github.com/gitleaks/gitleaks)
[![Security: Bandit](https://img.shields.io/badge/security-bandit-yellow.svg)](https://github.com/PyCQA/bandit)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)

A web application and sensor-fusion warning system for real-time hail detection. Analyzes severe convective storm threats at your current GPS location, any searched city/address, or clickable map coordinate.

Combines multiple authoritative meteorological feeds into a unified **Hail Risk Score (0-100%)** and **Threat Warning Level (NONE, MONITOR, WATCH, WARNING, EMERGENCY)** using **100% free, open-source APIs with zero API keys required**.

---

## 🌟 Key Features

- 🗺️ **Multi-Basemap Operations Engine (No API Keys Required)**:
  - **Tactical Dark**: Esri World Dark Gray Canvas with high-contrast county/city reference labels (zero watermarks, zero API keys).
  - **Satellite Hybrid**: Esri World Imagery combined with tactical dark reference boundaries.
  - **Clean Streets**: Crisp OpenStreetMap roadway and street navigation map.
- 🛰️ **Multi-Source NEXRAD Radar Map (No Zoom Errors)**:
  - Dynamic radar loop animation powered by RainViewer API with playback controls, scrub timeline, and opacity slider.
  - Smooth scaling configuration (`maxNativeZoom: 7, maxZoom: 18`) completely eliminating "Zoom Level Not Supported" tiles.
  - Switchable high-resolution Iowa Environmental Mesonet (IEM) NEXRAD Base Reflectivity composite supporting deep native zoom.
- 🚨 **Modernized NWS Weather Alerts & Storm-Based Warnings (SBW)**:
  - Real-time active Severe Thunderstorm and Tornado Warning polygons drawn with neon glowing outlines and pulsating animations.
  - Flash Flood Warnings, Special Weather Statements & Hail Advisories, and Severe Watches categorized with distinct styling.
  - Glassmorphism interactive popups displaying official NWS `maxHailSize` tags (e.g. `1.00"`, `1.75"`, `2.75"`), wind gusts, tornado detection, WFO office, and countdown expiration timers.
  - **Storm Motion Vectors & Swath Forecast**: Parses storm motion heading and speed, projects forward 15, 30, and 45-minute cell positions, and computes **Estimated Time of Arrival (ETA)** if bearing towards your location.
  - **NOAA SPC Day 1 Convective Outlook**: Overlay showing official Storm Prediction Center categorical and hail risk zones.
- 🎯 **Localized Single-Site WSR-88D NEXRAD Doppler Radars**:
  - Direct Level-III 0.5° Base Reflectivity tiles directly from individual radar dishes (e.g. **KFWS** for Fort Worth/Dallas, **KTLX** for Oklahoma City, **KHGX** for Houston) for superior gate accuracy, lowest ground-level beam angle, and zero mosaic smoothing.
  - **Auto-Nearest Radar Detection**: Automatically locks onto the closest WSR-88D Doppler station whenever you search, double-click, or locate.
  - **Manual Station Selector**: Select from all 160 national NEXRAD radar stations grouped by state.
  - **Interactive Radar Tower & Range Rings**: Pulsing tower beacon marker with 50 km (27 nmi), 100 km (54 nmi), and 230 km (124 nmi standard Doppler coverage) distance rings.
- 🔄 **Real-Time 360° Radar Sweep Beam**:
  - High-performance GPU-accelerated tactical Plan Position Indicator (PPI) sweep beam rotating clockwise with an authentic glowing phosphor decay trail.
  - Anchored dynamically to the localized radar antenna dish (e.g. KFWS at Fort Worth Spinks Airport).
  - One-click toggle in both HUD and overlay controls.
- 👥 **mPING & Spotter Ground Truth Verification**:
  - Ingests crowdsourced citizen reports from **NOAA mPING** and official NWS Trained Spotters, Emergency Managers, and Law Enforcement via Iowa State IEM LSR feeds.
  - Filter reports by radius (15, 30, 45, 75, 120 mi) and time horizon (6h, 24h, 3 days, 7 days).
- 🌡️ **Atmospheric Convective Barometer & Thermodynamic Soundings**:
  - Visual CAPE gauge bar (J/kg) and Lifted Index (LI).
  - Convective Inhibition (CIN) "Cap" status analyzer (Weak/Explosive, Moderate, Strong Cap).
  - Freezing Level Height (ft / m AGL) with Hail Survival rating.
  - Surface Barometric Pressure (inHg / hPa) and Wind Gusts (mph).
  - Composite **Hail Potential Index (HPI, 0-100)**.
- 🛡️ **Interactive Hail Property Damage Simulator**:
  - Interactive scenario slider (0.25" to 4.50") allowing users to simulate impact on vehicles, roofs & siding, solar panels & glass, and human safety.
  - Dynamic actionable protective action checklists.
- 🚨 **Custom Hail Threat & Severe Threshold Alert Engine (FEAT-01)**:
  - Configurable minimum hail size (in), minimum threat score (0-100), and maximum storm arrival time (ETA min).
  - Tactical active rule HUD status badge indicating live threshold criteria.
  - Smart audio and speech synthesis gating ensuring warning sirens (`playWarningSiren`) and voice alerts (`speakAlert`) only trigger when configured conditions are breached, eliminating alert fatigue.
- 🛣️ **Bounding Box & Transit Corridor Hail Threat Scanner (FEAT-02)**:
  - Geospatial rectangular corridor scanner querying active storm warnings and local storm reports across custom geographic bounds.
  - Computes composite corridor danger score, maximum hail size, total active warnings, and ground truth reports.
  - Preset high-threat transit corridors (I-35 Texas-Oklahoma, I-70 Colorado-Kansas, I-80 Nebraska-Iowa) with dedicated tactical hazard drawer.
- 📄 **Operations Threat Intelligence Dossier Export Engine (FEAT-03)**:
  - Export comprehensive threat intelligence in three standardized formats: Formatted ASCII Operations Briefing (`text/plain`), RFC 4180 CSV Spreadsheet (`text/csv`), and Structured JSON (`application/json`).
  - One-click modal controls for direct file downloads and clipboard copying.
- 🚀 **Zero External Dependencies**:
  - Built entirely on Python 3 standard library (`http.server`, `urllib.request`, `concurrent.futures`, `json`, `csv`).

---

## 📡 Free & Open-Source APIs Used (No API Keys Required)

| Data Source | Provider | Purpose | Authentication |
| :--- | :--- | :--- | :--- |
| **Tactical Cartography** | Esri & OpenStreetMap | Dark Canvas, Satellite Hybrid, Clean Streets basemaps | **None (Free / Public)** |
| **NWS Active Alerts & SBW** | National Weather Service (`api.weather.gov`) | Severe Thunderstorm & Tornado warning polygons, headlines, hail tags, storm motion | **None (Free / Public)** |
| **IEM Storm-Based Warnings** | Iowa Environmental Mesonet (`mesonet.agron.iastate.edu`) | Active storm-based polygons, VTEC tags, hail/wind tags | **None (Free / Public)** |
| **Local Storm Reports (LSR) & mPING** | Iowa Environmental Mesonet (`mesonet.agron.iastate.edu`) | Ground-truth hail observations, mPING citizen reports, diameter measurements | **None (Free / Public)** |
| **Radar Reflectivity (dBZ)** | RainViewer (`api.rainviewer.com`) & IEM NEXRAD | Timestamped NEXRAD composite radar tiles and animation frames | **None (Free / Public)** |
| **Convective Soundings** | Open-Meteo (`api.open-meteo.com`) | CAPE, Lifted Index, Convective Inhibition (CIN), Freezing Level Height, surface pressure | **None (Free / Public)** |
| **SPC Day 1 Outlook** | NOAA Storm Prediction Center (`spc.noaa.gov`) | Severe thunderstorm and hail probability outlook polygons | **None (Free / Public)** |
| **Geocoding & Reverse Geocoding** | OpenStreetMap Nominatim (`nominatim.openstreetmap.org`) | Address, city, and coordinate name resolution | **None (Free / Public)** |

---

## 🚀 Quick Start

### 1. Launch the Server

Run the Python server (Python 3.10+):

```bash
python3 server.py
```

To specify a custom port:

```bash
python3 server.py 8080
# or
PORT=9000 python3 server.py
```

### 2. Open the Web Application

Open your browser to:

```text
http://localhost:8080
```

---

## 🧪 Running the Automated Test Suite

A comprehensive test suite verifies API connectivity, JSON response schemas, static file delivery, and the multi-sensor threat evaluation logic:

```bash
python3 test_server.py
```

---

## 🔬 Multi-Sensor Fusion Threat Algorithm

The threat evaluation engine in `hail_core.py` performs weighted sensor fusion:

1. **Official NWS Warning Layer**:
   - Active Tornado Warning: Base score 88 (EMERGENCY).
   - Active Severe Thunderstorm Warning: Base score 70 (WARNING), scaling to 85 (Golf Ball 1.75") or 95 (Baseball 2.5"+).
   - Special Weather Statement with Hail Tag: Base score 45-65 (WATCH).
2. **Ground Truth Report Layer (mPING & Spotters)**:
   - Evaluates confirmed hail diameter within 5, 15, and 30 miles with time decay weighting.
   - Ground report < 5 miles elevates threat level immediately.
3. **Atmospheric Physics Layer**:
   - CAPE > 2,000 J/kg and Lifted Index < -6 indicate violent updrafts capable of producing giant hailstones.
4. **Estimated Reflectivity (dBZ)**:
   - Computes expected radar core intensity (e.g. 52-60 dBZ for large hail, 60-70+ dBZ for wet hail cores).

---

## ⚡ Threat Prototyping Features & REST API Documentation

### 1. Custom Hail Threat & Severe Threshold Alert Engine (FEAT-01)
- **Overview & User Value**: Enables operators and emergency response personnel to define custom alert triggering criteria tailored to their risk tolerance (e.g. vehicle fleet protection vs. human safety). Eliminates alert fatigue by intelligently gating audio sirens (`playWarningSiren`) and Web Speech alerts (`speakAlert`) so they only fire when user-configured threshold rules are breached.
- **REST API Endpoint**: `GET /api/threat/threshold-check`
- **Query Parameters**:
  - `lat` (float, required): Target latitude (-90.0 to 90.0).
  - `lon` (float, required): Target longitude (-180.0 to 180.0).
  - `min_hail` (float, optional, default: `1.0`): Minimum hail diameter in inches to trigger alert (e.g., `0.75`, `1.00`, `1.75`).
  - `min_score` (int, optional, default: `70`): Minimum composite threat risk score (0-100) to trigger alert.
  - `max_eta` (int, optional, default: `45`): Maximum incoming storm cell arrival time in minutes (1-240) to trigger alert.
  - `radius` (float, optional, default: `45`): Search radius around target coordinates in miles (5.0-150.0).
- **Sample Curl Request**:
  ```bash
  curl -s "http://localhost:8080/api/threat/threshold-check?lat=32.7767&lon=-96.7970&min_hail=1.25&min_score=65&max_eta=45"
  ```
- **Sample JSON Response**:
  ```json
  {
    "status": "ok",
    "timestamp": "2026-10-04T01:10:00Z",
    "rule_criteria": {
      "min_hail_in": 1.25,
      "min_threat_score": 65,
      "max_storm_eta_min": 45,
      "scan_radius_mi": 45.0
    },
    "current_metrics": {
      "threat_score": 75,
      "warning_level": "WARNING",
      "max_hail_size": 1.75,
      "min_storm_eta": 28,
      "active_warnings_count": 1,
      "ground_reports_count": 3
    },
    "evaluation": {
      "triggered": true,
      "hail_threshold_met": true,
      "score_threshold_met": true,
      "eta_threshold_met": true,
      "matched_reasons": [
        "Observed/predicted hail diameter (1.75 in) exceeds minimum threshold (1.25 in)",
        "Threat score (75) meets or exceeds minimum threshold (65)",
        "Nearest storm arrival (28 min) is within alert window (45 min)"
      ]
    },
    "directive": "CRITICAL: Custom threat criteria met. Protective action recommended."
  }
  ```
- **Frontend UI Controls & Behavior**:
  - Click the **Threshold Settings** button (`#threshold-cfg-btn`, ⚙️ icon) in the header navigation or operations bar.
  - The modal dialog (`#threshold-modal`) allows operators to configure Minimum Hail Size (in), Minimum Risk Score (0-100), and Maximum ETA (minutes), with a live preview chip showing current active criteria.
  - An **Active Rule Badge** (`#active-rule-badge`) in the tactical HUD displays configured thresholds at a glance (e.g. `RULE: ≥1.00" | ≥70% | ≤45m`).
  - When new assessment data arrives, audio siren and voice alerts evaluate against these rules; if conditions are below threshold, alerts are suppressed to prevent operator desensitization.

---

### 2. Bounding Box & Transit Corridor Hail Threat Scanner (FEAT-02)
- **Overview & User Value**: Provides corridor-level threat intelligence for logistics dispatchers, long-haul trucking, rail operations, and mobile field units traversing major transit corridors. Aggregates all active warnings and ground truth hail reports intersecting the bounding box and calculates a composite corridor danger score.
- **REST API Endpoint**: `GET /api/hail/bbox`
- **Query Parameters**:
  - `min_lat` (float, required): Southern bounding box latitude (-90.0 to 90.0).
  - `min_lon` (float, required): Western bounding box longitude (-180.0 to 180.0).
  - `max_lat` (float, required): Northern bounding box latitude (-90.0 to 90.0, > `min_lat`).
  - `max_lon` (float, required): Eastern bounding box longitude (-180.0 to 180.0, > `min_lon`).
  - `min_hail` (float, optional, default: `0.0`): Filter minimum hail report size in inches.
  - `hours` (int, optional, default: `24`): Historical report lookup window in hours (1-168).
- **Sample Curl Request**:
  ```bash
  curl -s "http://localhost:8080/api/hail/bbox?min_lat=32.0&min_lon=-98.0&max_lat=36.0&max_lon=-96.0&min_hail=0.75&hours=24"
  ```
- **Sample JSON Response**:
  ```json
  {
    "status": "ok",
    "bbox": {
      "min_lat": 32.0,
      "min_lon": -98.0,
      "max_lat": 36.0,
      "max_lon": -96.0
    },
    "corridor_metrics": {
      "total_warnings": 2,
      "total_hail_reports": 6,
      "max_hail_size": 2.25,
      "highest_severity": "WARNING",
      "danger_score": 82
    },
    "contained_warnings": [
      {
        "event": "Severe Thunderstorm Warning",
        "severity": "WARNING",
        "hail_size": 2.25,
        "wind_gust": 70,
        "headline": "Severe Thunderstorm Warning for Denton and Collin Counties"
      }
    ],
    "contained_reports": [
      {
        "type": "HAIL",
        "magnitude": 2.0,
        "location": "Denton, TX",
        "source": "Trained Spotter"
      }
    ],
    "geojson": {
      "type": "FeatureCollection",
      "features": [...]
    }
  }
  ```
- **Frontend UI Controls & Behavior**:
  - Toggle **Corridor Mode** (`#btn-bbox-mode`) on the tactical map HUD to slide out the **Corridor Hazard Drawer** (`#corridor-drawer`).
  - Select quick-access high-threat transit corridor presets:
    - **I-35 Texas-Oklahoma Corridor** (`31.5°N, -98.0°W` to `36.5°N, -96.5°W`)
    - **I-70 Colorado-Kansas Corridor** (`38.5°N, -104.5°W` to `40.0°N, -95.0°W`)
    - **I-80 Nebraska-Iowa Corridor** (`40.5°N, -101.5°W` to `42.0°N, -91.0°W`)
  - A glowing cyan rectangle overlay is rendered dynamically on Leaflet cartography, and corridor metrics (Danger Score, Active Warnings, and Ground Truth Reports) update instantly in the drawer HUD.

---

### 3. Operations Threat Dossier Export Engine (FEAT-03)
- **Overview & User Value**: Standardizes meteorological hazard data dissemination for incident commanders, insurance adjusters, fleet directors, and risk analysts. Supports plain ASCII operations briefing text, structured machine-readable JSON, and RFC 4180 compliant CSV spreadsheets for post-incident audit and spreadsheet analysis.
- **REST API Endpoint**: `GET /api/export/threat-dossier`
- **Query Parameters**:
  - `lat` (float, required): Target latitude (-90.0 to 90.0).
  - `lon` (float, required): Target longitude (-180.0 to 180.0).
  - `radius` (float, optional, default: `45`): Scan radius in miles (5.0-150.0).
  - `format` (string, optional, default: `text`): Output format: `text` (`text/plain`), `csv` (`text/csv`), or `json` (`application/json`).
- **Sample Curl Requests**:
  ```bash
  # Plaintext Operations Briefing
  curl -s "http://localhost:8080/api/export/threat-dossier?lat=32.7767&lon=-96.7970&format=text"

  # RFC 4180 CSV Spreadsheet
  curl -s -O -J "http://localhost:8080/api/export/threat-dossier?lat=32.7767&lon=-96.7970&format=csv"

  # Structured JSON Payload
  curl -s "http://localhost:8080/api/export/threat-dossier?lat=32.7767&lon=-96.7970&format=json"
  ```
- **Sample Plaintext Briefing Output (`format=text`)**:
  ```text
  ================================================================================
  HAILWARN OPERATIONS THREAT INTELLIGENCE BRIEFING
  Document ID: HW-20261004-3277-9679
  Generated  : 2026-10-04T01:10:00Z (UTC)
  Location   : 32.7767° N, 96.7970° W (Radius: 45.0 mi)
  ================================================================================

  [1] THREAT ASSESSMENT
  --------------------------------------------------------------------------------
  Overall Threat Level : WARNING
  Hail Risk Score      : 75 / 100
  Max Hail Observed    : 1.75 in (Golf Ball)
  Earliest Storm ETA   : 28 min (Bearing: 245°)
  Confidence Score     : 85%
  ...
  ================================================================================
  END OF BRIEFING - HAILWARN TACTICAL SYSTEM
  ================================================================================
  ```
- **Sample RFC 4180 CSV Output (`format=csv`)**:
  ```csv
  SECTION,METRIC,VALUE,UNIT,DESCRIPTION
  METADATA,REPORT_ID,HW-20261004-3277-9679,,Unique Dossier Identifier
  METADATA,GENERATED_UTC,2026-10-04T01:10:00Z,,ISO-8601 Timestamp
  METADATA,LATITUDE,32.7767,deg,Target Latitude
  METADATA,LONGITUDE,-96.7970,deg,Target Longitude
  METADATA,RADIUS_MILES,45.0,mi,Sensor Scan Radius
  ASSESSMENT,THREAT_LEVEL,WARNING,,Multi-Sensor Threat Warning Level
  ASSESSMENT,RISK_SCORE,75,pct,Hail Risk Score (0-100)
  ASSESSMENT,MAX_HAIL_SIZE,1.75,in,Maximum Observed/Reported Hail Diameter
  ASSESSMENT,MIN_STORM_ETA,28,min,Estimated Time of Arrival for Nearest Hail Core
  ...
  SECTION,REPORT_ID,TYPE,SOURCE,MAGNITUDE_IN,LATITUDE,LONGITUDE,TIME_UTC,REMARKS
  REPORT,LSR-1,HAIL,Trained Spotter,1.75,32.8123,-96.8450,2026-10-04T00:45:00Z,"Golf ball hail observed"
  ```
- **Sample JSON Output (`format=json`)**:
  ```json
  {
    "status": "ok",
    "dossier_id": "HW-20261004-3277-9679",
    "generated_utc": "2026-10-04T01:10:00Z",
    "coordinates": {
      "latitude": 32.7767,
      "longitude": -96.7970,
      "radius_miles": 45.0
    },
    "assessment": {
      "threat_level": "WARNING",
      "risk_score": 75,
      "max_hail_size": 1.75,
      "min_storm_eta": 28
    },
    "warnings": [],
    "ground_reports": [],
    "soundings": {}
  }
  ```
- **Frontend UI Controls & Behavior**:
  - Open the **Operations Threat Briefing** modal (`#briefing-modal`) from the header toolbar.
  - Action buttons inside the modal footer provide immediate export actions:
    - **Download CSV Dossier** (`#download-csv-btn`): Downloads formatted RFC 4180 spreadsheet (`text/csv`).
    - **Download Plaintext Briefing** (`#download-briefing-btn`): Downloads complete formatted `.txt` intelligence brief (`text/plain`).
    - **Copy Operations Brief** (`#copy-briefing-btn`): Copies the intelligence brief text directly to the system clipboard with tactile HUD notification.

---

## 🔒 Automated Security & DevSecOps Pipelines

The repository includes a comprehensive GitHub Actions CI/CD and DevSecOps security pipeline running on every push, pull request, manual dispatch, and weekly schedule:

| Security Layer | Tool | Scope & Functionality |
| :--- | :--- | :--- |
| **Secret Scanning** | **Gitleaks** (`gitleaks-action`) | Full git history and repository commit scan for leaked credentials, API tokens, and private keys. |
| **Python SAST** | **Bandit** (`bandit`) | Static Application Security Testing for Python security vulnerabilities (insecure calls, injection, deserialization). Uploads SARIF reports to GitHub Security. |
| **Vulnerability Scanning** | **Trivy** (`aquasecurity/trivy-action`) | Scans file systems, actions, and code for CVEs and misconfigurations. Produces both table and SARIF alerts. |
| **Code Scanning** | **GitHub CodeQL** | Semantic code analysis and taint tracking across Python backend and JavaScript frontend. |
| **Supply Chain & SCA** | **pip-audit** | Audits runtime and development dependencies against the Python Advisory Database (PyPA/OSV). |
| **Dependency Updates** | **Dependabot** | Weekly automated security audits and pull requests for GitHub Actions and pip packages. |
| **Automated Testing** | **unittest** | Multi-version Python test matrix (3.10, 3.11, 3.12, 3.13) verifying sensor fusion algorithms and API contracts. |

---

## 📂 Project Structure

```text
hail_warn/
├── .github/
│   ├── dependabot.yml            # Automated dependency & security update config
│   └── workflows/
│       ├── security.yml          # Secret scanning, Bandit, Trivy, pip-audit & tests
│       └── codeql.yml            # GitHub CodeQL semantic analysis (Python & JS)
├── .bandit.yaml                  # Bandit SAST analyzer configuration
├── .gitleaks.toml                # Gitleaks secret scanning configuration
├── pyproject.toml                # Project metadata, tool configurations, and linter settings
├── requirements-dev.txt          # Security scanning and development tools
├── requirements.txt              # Runtime dependency documentation (zero external dependencies)
├── AGENTS.md                     # Agent operating guidelines & commit author rules
├── hail_core.py                  # Multi-sensor fusion engine, API fetchers, hail size logic
├── server.py                     # Multithreaded Python HTTP server & REST API
├── test_server.py                # Automated unit & integration test suite
├── static/
│   ├── index.html                # Operations dashboard HTML5 structure
│   ├── styles.css                # Tactical dark theme, radar HUD, and responsive styling
│   ├── app.js                    # Client-side map logic, radar sweep engine, Web Audio siren
│   └── nexrad_stations.json      # 160 US WSR-88D Doppler radar stations database
└── README.md                     # Documentation & operational architecture
```
