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
- 🔊 **Voice Speech Synthesis & Web Audio Alert Siren**:
  - Spoken Voice Announcements (Web Speech API) declaring incoming hail threats and ETA.
  - Browser-synthesized dual-tone warning siren and attention chime using Web Audio API.
- 📄 **Operations Threat Intelligence Briefing Export**:
  - One-click export modal generating formatted text briefs, downloadable reports, and full JSON payloads.
- 🚀 **Zero External Dependencies**:
  - Built entirely on Python 3 standard library (`http.server`, `urllib.request`, `concurrent.futures`, `json`).

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
