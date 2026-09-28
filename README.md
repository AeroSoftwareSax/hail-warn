# ⚡ HAILWARN - Severe Hail Detection & Early Warning Radar System

A web application and sensor-fusion warning system for real-time hail detection. Analyzes severe convective storm threats at your current GPS location, any searched city/address, or clickable map coordinate.

Combines multiple authoritative meteorological feeds into a unified **Hail Risk Score (0-100%)** and **Threat Warning Level (NONE, MONITOR, WATCH, WARNING, EMERGENCY)** using **100% free, open-source APIs with zero API keys required**.

---

## 🌟 Key Features

- 🛰️ **Interactive NEXRAD Radar Map (dBZ)**:
  - Dynamic radar loop animation powered by RainViewer API with playback controls, scrub timeline, and opacity slider.
  - Calibrated dBZ reflectivity scale indicating rain, graupel, severe hail, and destructive hail cores (>60 dBZ).
  - Optional toggle for high-resolution Iowa Environmental Mesonet (IEM) NEXRAD Base Reflectivity composite.
- 👥 **mPING & Spotter Ground Truth Verification**:
  - Ingests crowdsourced citizen reports from **NOAA mPING** and official NWS Trained Spotters, Emergency Managers, and Law Enforcement via the Iowa State IEM LSR feed.
  - Shows exact hailstone diameter (inches), report age, distance from your location, and raw observer remarks.
- 🚨 **Official NWS Weather Alerts & Storm-Based Warnings (SBW)**:
  - Real-time active Severe Thunderstorm and Tornado Warning polygons drawn directly on the map.
  - Extracts official NWS `maxHailSize` tags (e.g. `1.00"`, `1.75"`, `2.75"`) and threat types (`RADAR INDICATED` vs `OBSERVED`).
- 🌡️ **Atmospheric Sounding & Convective Physics**:
  - Incorporates Convective Available Potential Energy (CAPE in J/kg) and Lifted Index (LI) from Open-Meteo to gauge updraft strength capable of suspending large hailstones.
- 📍 **Location Flexibility**:
  - **My Location**: HTML5 Geolocation API with high accuracy GPS fix.
  - **Address Search**: Real-time debounced geocoding via OpenStreetMap Nominatim.
  - **Live Severe Hotspots**: Instant quick-jump menu scanning nationwide NWS bulletins for active severe weather cells right now.
  - **Click-to-Inspect**: Click anywhere on the map to evaluate hail risk at that coordinate.
- 🔊 **Synthesized Web Audio Alert Siren**:
  - Browser-synthesized dual-tone warning siren and attention chime using the Web Audio API (zero audio files needed).
  - Automatically sounds an alarm when threat level escalates to `WARNING` or `EMERGENCY`.
- 🛡️ **Hail Diameter Scale & Damage Profile**:
  - Realistically lit hailstone disc graphic scaled to estimated diameter.
  - Physical object comparisons (Quarter, Golf Ball, Tennis Ball, Baseball, Softball).
  - Damage impact assessment for vehicles, roofs, and outdoor human safety.
- 🚀 **Zero External Pip Dependencies**:
  - Built entirely on Python 3 standard library (`http.server`, `urllib.request`, `concurrent.futures`, `json`).

---

## 📡 Free & Open-Source APIs Used (No API Keys Required)

| Data Source | Provider | Purpose | Authentication |
| :--- | :--- | :--- | :--- |
| **NWS Active Alerts** | National Weather Service (`api.weather.gov`) | Severe Thunderstorm & Tornado warning polygons, headlines, hail tags | **None (Free / Public)** |
| **Local Storm Reports (LSR) & mPING** | Iowa Environmental Mesonet (`mesonet.agron.iastate.edu`) | Ground-truth hail observations, mPING citizen reports, diameter measurements | **None (Free / Public)** |
| **Radar Reflectivity (dBZ)** | RainViewer (`api.rainviewer.com`) | Timestamped NEXRAD composite radar tiles and animation frames | **None (Free / Public)** |
| **Convective Soundings** | Open-Meteo (`api.open-meteo.com`) | Convective Available Potential Energy (CAPE), Lifted Index, convective rain | **None (Free / Public)** |
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

## 📂 Project Structure

```text
hail_warn/
├── hail_core.py         # Multi-sensor fusion engine, API fetchers, hail size logic
├── server.py            # Multithreaded Python HTTP server & REST API
├── test_server.py       # Automated unit & integration test suite
├── static/
│   ├── index.html       # Operations dashboard HTML5 structure
│   ├── styles.css       # Tactical dark theme, radar HUD, and responsive styling
│   └── app.js           # Client-side map logic, radar loop, Web Audio siren
└── README.md            # Documentation
```
