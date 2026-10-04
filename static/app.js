/**
 * HAILWARN - Severe Hail Detection & Threat Warning Operations Client
 * Multi-source sensor fusion interface combining NWS alerts, mPING ground reports,
 * NEXRAD dBZ radar, and Open-Meteo convective soundings.
 * Zero API keys required - 100% free open data.
 */

// HTML sanitization helper for XSS prevention (SEC-02)
function escapeHtml(str) {
  if (str === null || str === undefined) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;")
    .replace(/`/g, "&#96;");
}

// Application State
const state = {
  currentLat: 32.7767, // Default: Dallas, TX
  currentLon: -96.7970,
  currentPlaceName: "Dallas, TX",
  radiusMiles: 45,
  filterHours: 168,
  threatScore: 0,
  threatLevel: "NONE",
  sirenEnabled: true,
  voiceEnabled: false,
  soundTone: "siren",
  autoRefreshInterval: 60,
  secondsUntilRefresh: 60,
  radarFrames: [],
  radarIndex: 0,
  radarPlaying: false,
  radarTimer: null,
  radarOpacity: 0.75,
  radarSource: "single_site", // "single_site", "rainviewer", "mrms", or "iem"
  selectedStationId: "auto",  // "auto" or 3-letter site id like "FWS"
  activeStation: null,        // station object { id, icao, name, state, lat, lon, elevation, distance_miles }
  sweepEnabled: true,
  showStationRings: true,
  allStations: [],
  radarPalette: 6,           // 6: NEXRAD Level III NOAA Severe, 8: Dark Sky, 2: Classic, 3: TITAN
  radarSmooth: "1_1",        // "1_1": HD Smooth, "0_1": Crisp Raw Bins
  activeBasemap: "dark",     // "dark", "satellite", "streets"
  cityWeatherVisible: true,
  simulatedHail: null,
  activeFeedTab: "all",
  assessmentData: null,
  thresholdConfig: {
    minHail: 1.0,
    minScore: 70,
    maxEta: 45
  },
  thresholdTriggered: false,
  corridorMode: false,
  corridorLayer: null,
  corridorData: null
};

// Global Map and Layer Groups
let map = null;
let userMarker = null;
let currentBasemap = null;
const basemapLayers = {};

// Specialized Map Layer Groups
let threatRingsGroup = null;
let warningPolygonsGroup = null;
let floodGroup = null;
let statementsGroup = null;
let watchesGroup = null;
let stormTracksGroup = null;
let spcOutlookGroup = null;
let groundReportsGroup = null;

let singleSiteRadarLayer = null;
let radarStationMarkerGroup = null;
let radarSweepEngine = null;
let rainviewerRadarLayer = null;
let iemNexradLayer = null;
let mrmsRadarLayer = null;

// Web Audio API Context for Alert Siren
let audioCtx = null;

// -------------------------------------------------------------
// INITIALIZATION
// -------------------------------------------------------------
// RADAR SWEEP BEAM CANVAS ENGINE
// -------------------------------------------------------------
class RadarSweepEngine {
  constructor(map) {
    this.map = map;
    this.canvas = document.createElement("canvas");
    this.canvas.className = "radar-sweep-overlay";
    this.ctx = this.canvas.getContext("2d");
    this.angle = 0;
    this.speed = 0.024; // ~1.37 deg per frame, 360 deg in ~4.5 seconds at 60fps
    this.animId = null;
    this.enabled = true;
    this.running = false;

    // Attach to Leaflet overlay pane so it stays georeferenced
    const pane = map.getPanes().overlayPane;
    pane.appendChild(this.canvas);

    this.onMapMove = this.onMapMove.bind(this);
    this.draw = this.draw.bind(this);

    map.on("move", this.onMapMove);
    map.on("resize", this.onMapMove);
    map.on("zoom", this.onMapMove);
    map.on("viewreset", this.onMapMove);

    this.onMapMove();
    this.start();
  }

  onMapMove() {
    if (!this.map || !this.canvas) return;
    const bounds = this.map.getBounds();
    const topLeft = this.map.latLngToLayerPoint(bounds.getNorthWest());
    const size = this.map.getSize();

    L.DomUtil.setPosition(this.canvas, topLeft);
    this.canvas.width = size.x;
    this.canvas.height = size.y;
  }

  start() {
    if (this.animId) cancelAnimationFrame(this.animId);
    this.running = true;
    this.draw();
  }

  stop() {
    this.running = false;
    if (this.animId) {
      cancelAnimationFrame(this.animId);
      this.animId = null;
    }
    if (this.ctx && this.canvas) {
      this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    }
  }

  setEnabled(enable) {
    this.enabled = enable;
    if (enable) {
      this.start();
    } else {
      this.stop();
    }
  }

  draw() {
    if (!this.enabled || !this.running) return;

    const ctx = this.ctx;
    const canvas = this.canvas;
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // Center sweep on active localized radar station dish if on single_site, otherwise target location
    const isSingleSite = state.radarSource === "single_site" && state.activeStation;
    let centerLat = isSingleSite ? state.activeStation.lat : state.currentLat;
    let centerLon = isSingleSite ? state.activeStation.lon : state.currentLon;

    const centerPoint = this.map.latLngToLayerPoint([centerLat, centerLon]);
    const bounds = this.map.getBounds();
    const topLeft = this.map.latLngToLayerPoint(bounds.getNorthWest());

    const cx = centerPoint.x - topLeft.x;
    const cy = centerPoint.y - topLeft.y;

    // NEXRAD 124 nmi (~230 km) maximum Level-II reflectivity radius (~2.068 degrees latitude)
    const edgePoint = this.map.latLngToLayerPoint([centerLat + 2.068, centerLon]);
    const radiusPx = Math.max(30, Math.hypot(edgePoint.x - centerPoint.x, edgePoint.y - centerPoint.y));

    // Only render if within viewport
    if (
      cx + radiusPx >= -50 &&
      cx - radiusPx <= canvas.width + 50 &&
      cy + radiusPx >= -50 &&
      cy - radiusPx <= canvas.height + 50
    ) {
      this.angle = (this.angle + this.speed) % (Math.PI * 2);

      ctx.save();
      // Clip within radar coverage circle
      ctx.beginPath();
      ctx.arc(cx, cy, radiusPx, 0, Math.PI * 2);
      ctx.clip();

      // Sweeping phosphor trailing sector (50 deg behind lead beam)
      const trailAngle = (50 * Math.PI) / 180;
      const steps = 18;
      for (let i = 0; i < steps; i++) {
        const a1 = this.angle - trailAngle + (i / steps) * trailAngle;
        const a2 = this.angle - trailAngle + ((i + 1) / steps) * trailAngle;
        const alpha = Math.pow(i / steps, 1.6) * 0.22;
        ctx.fillStyle = `rgba(34, 197, 94, ${alpha})`;
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.arc(cx, cy, radiusPx, a1, a2);
        ctx.closePath();
        ctx.fill();
      }

      // Bright leading sweep beam
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.lineTo(cx + radiusPx * Math.cos(this.angle), cy + radiusPx * Math.sin(this.angle));
      ctx.lineWidth = 2.0;
      ctx.strokeStyle = "rgba(74, 222, 128, 0.95)";
      ctx.shadowColor = "rgba(34, 197, 94, 0.85)";
      ctx.shadowBlur = 8;
      ctx.stroke();

      // Subtle perimeter ring
      ctx.beginPath();
      ctx.arc(cx, cy, radiusPx, 0, Math.PI * 2);
      ctx.lineWidth = 1.0;
      ctx.strokeStyle = "rgba(34, 197, 94, 0.35)";
      ctx.shadowBlur = 0;
      ctx.stroke();

      ctx.restore();
    } else {
      this.angle = (this.angle + this.speed) % (Math.PI * 2);
    }

    this.animId = requestAnimationFrame(this.draw);
  }
}

// -------------------------------------------------------------
// LOCALIZED NEXRAD RADARS (WSR-88D Single-Site)
// -------------------------------------------------------------
function haversineMiles(lat1, lon1, lat2, lon2) {
  const r = 3958.8;
  const p1 = (lat1 * Math.PI) / 180;
  const p2 = (lat2 * Math.PI) / 180;
  const dp = ((lat2 - lat1) * Math.PI) / 180;
  const dl = ((lon2 - lon1) * Math.PI) / 180;
  const a = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
  return r * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

async function loadNexradStations() {
  try {
    const res = await fetch(`/api/nexrad/stations?lat=${state.currentLat}&lon=${state.currentLon}`);
    const data = await res.json();
    state.allStations = data.stations || [];

    const select = document.getElementById("radar-station-select");
    if (select) {
      select.innerHTML = `<option value="auto">🎯 Auto-Nearest Radar</option>`;

      // High-priority quick jump sites with active weather or major metros
      const activeHotspots = [
        { id: "FWS", icao: "KFWS", name: "Dallas/Fort Worth", state: "TX", desc: "Local Primary WSR-88D" },
        { id: "EPZ", icao: "KEPZ", name: "El Paso", state: "TX", desc: "Active Flood Warning / Storms" },
        { id: "MVX", icao: "KMVX", name: "Mayville / Fargo", state: "ND", desc: "Active Thunderstorm Cells" },
        { id: "ICX", icao: "KICX", name: "Cedar City", state: "UT", desc: "Convective Precipitation" },
        { id: "TLX", icao: "KTLX", name: "Oklahoma City", state: "OK", desc: "Severe Storm Hotspot" },
        { id: "TBW", icao: "KTBW", name: "Tampa Bay Area", state: "FL", desc: "Gulf Coastal Convection" },
        { id: "OKX", icao: "KOKX", name: "New York / Upton", state: "NY", desc: "Northeast Corridor" }
      ];

      const stormGroup = document.createElement("optgroup");
      stormGroup.label = "⚡ QUICK JUMP: Active & Key Radar Sites";
      activeHotspots.forEach((h) => {
        const opt = document.createElement("option");
        opt.value = h.id;
        opt.textContent = `⚡ ${h.icao} - ${h.name}, ${h.state} (${h.desc})`;
        stormGroup.appendChild(opt);
      });
      select.appendChild(stormGroup);

      const byState = {};
      state.allStations.forEach((s) => {
        if (!byState[s.state]) byState[s.state] = [];
        byState[s.state].push(s);
      });

      const sortedStates = Object.keys(byState).sort();
      sortedStates.forEach((st) => {
        const optgroup = document.createElement("optgroup");
        optgroup.label = `State: ${st}`;
        byState[st].forEach((s) => {
          const opt = document.createElement("option");
          opt.value = s.id;
          opt.textContent = `${s.icao} - ${s.name}, ${s.state}`;
          optgroup.appendChild(opt);
        });
        select.appendChild(optgroup);
      });
    }

    if (data.nearest) {
      state.activeStation = data.nearest;
    } else {
      updateNearestStation();
    }

    renderLocalizedRadarStation();
    if (state.radarSource === "single_site") {
      updateSingleSiteRadarLayer();
    }
  } catch (err) {
    console.error("Failed loading NEXRAD stations:", err);
  }
}

function updateNearestStation() {
  if (!state.allStations || state.allStations.length === 0) return;
  let best = null;
  let minDist = Infinity;
  state.allStations.forEach((s) => {
    const d = haversineMiles(state.currentLat, state.currentLon, s.lat, s.lon);
    if (d < minDist) {
      minDist = d;
      best = Object.assign({}, s, { distance_miles: Math.round(d * 10) / 10 });
    }
  });
  if (best) {
    state.activeStation = best;
    const select = document.getElementById("radar-station-select");
    if (select && state.selectedStationId === "auto") {
      const autoOpt = select.querySelector("option[value='auto']");
      if (autoOpt) {
        autoOpt.textContent = `🎯 Auto: ${best.icao} (${best.name} - ${best.distance_miles} mi)`;
      }
    }
  }
}

function updateSingleSiteRadarLayer() {
  if (!state.activeStation) return;
  if (singleSiteRadarLayer && map && map.hasLayer(singleSiteRadarLayer)) {
    map.removeLayer(singleSiteRadarLayer);
  }
  const siteId = (state.activeStation.id || state.activeStation.icao || "").toUpperCase().trim();
  const tileUrl = `https://mesonet.agron.iastate.edu/cache/tile.py/1.0.0/ridge::${siteId}-N0Q-0/{z}/{x}/{y}.png`;
  
  // KEY FIX: maxNativeZoom: 12 allows Leaflet to upscale IEM RIDGE Level-III tiles
  // smoothly up to zoom 18 without requesting empty 334-byte tiles from server!
  singleSiteRadarLayer = L.tileLayer(tileUrl, {
    opacity: state.radarOpacity,
    zIndex: 20,
    minZoom: 3,
    maxZoom: 18,
    maxNativeZoom: 12,
    attribution: `NOAA/NWS WSR-88D ${state.activeStation.icao} / IEM RIDGE`
  });

  if (state.radarSource === "single_site" && map) {
    singleSiteRadarLayer.addTo(map);
    const scrubberBar = document.querySelector(".hud-playback-bar");
    const palettePill = document.getElementById("radar-palette-pill");
    const smoothPill = document.getElementById("radar-smoothing-pill");
    const stationPill = document.getElementById("radar-station-pill");

    if (scrubberBar) scrubberBar.style.opacity = "0.45";
    if (palettePill) palettePill.style.display = "none";
    if (smoothPill) smoothPill.style.display = "none";
    if (stationPill) stationPill.style.display = "inline-flex";

    const typeLabel = document.getElementById("radar-type-label");
    const timeLabel = document.getElementById("radar-time-label");
    if (typeLabel) typeLabel.textContent = `${state.activeStation.icao} 0.5° BASE`;
    const distText = state.activeStation.distance_miles !== undefined ? ` • ${state.activeStation.distance_miles} mi` : "";
    if (timeLabel) timeLabel.textContent = `Live WSR-88D (${state.activeStation.name}, ${state.activeStation.state}${distText}) • Level-III Base Reflectivity`;
  }
}

function renderLocalizedRadarStation() {
  if (!radarStationMarkerGroup) return;
  radarStationMarkerGroup.clearLayers();

  if (!state.activeStation || !state.showStationRings) return;

  const st = state.activeStation;
  const lat = st.lat;
  const lon = st.lon;

  // Radar tower beacon icon
  const distTitle = st.distance_miles !== undefined ? ` (${escapeHtml(st.distance_miles)} mi)` : "";
  const beaconTitle = `${escapeHtml(st.icao)} - ${escapeHtml(st.name)} WSR-88D${distTitle}`;
  const towerIcon = L.divIcon({
    className: "custom-radar-tower-icon",
    html: `
      <div class="radar-tower-beacon" title="${beaconTitle}">
        <i class="fa-solid fa-tower-broadcast"></i>
        <div class="beacon-wave"></div>
      </div>
    `,
    iconSize: [32, 32],
    iconAnchor: [16, 16]
  });

  const marker = L.marker([lat, lon], { icon: towerIcon, zIndexOffset: 2000 });
  const popupHtml = `
    <div class="radar-station-popup">
      <h4><i class="fa-solid fa-tower-broadcast"></i> ${escapeHtml(st.icao)} &bull; ${escapeHtml(st.name)}</h4>
      <div class="station-meta">
        <div><b>Type:</b> NOAA WSR-88D Doppler Radar</div>
        <div><b>Location:</b> ${escapeHtml(st.name)}, ${escapeHtml(st.state)}</div>
        <div><b>Coordinates:</b> ${lat.toFixed(4)}°, ${lon.toFixed(4)}°</div>
        <div><b>Elevation:</b> ${escapeHtml(st.elevation)} m MSL</div>
        ${st.distance_miles !== undefined ? `<div><b>Distance:</b> ${escapeHtml(st.distance_miles)} miles to target</div>` : ""}
      </div>
      <div class="station-badge">Level-III 0.5° Base Reflectivity</div>
      <button id="btn-center-radar-${escapeHtml(st.id)}" class="hud-btn" style="margin-top: 8px; width: 100%; justify-content: center; font-size: 0.75rem; padding: 5px 8px; background: rgba(6,182,212,0.18); border: 1px solid rgba(6,182,212,0.5); cursor: pointer;">
        <i class="fa-solid fa-crosshairs text-cyan"></i> <span>Center On Radar Tower</span>
      </button>
    </div>
  `;
  marker.bindPopup(popupHtml);
  marker.on('popupopen', () => {
    const centerBtn = document.getElementById(`btn-center-radar-${st.id}`);
    if (centerBtn) {
      centerBtn.onclick = () => {
        if (map) map.flyTo([lat, lon], 9, { duration: 1.2 });
      };
    }
  });
  marker.bindTooltip(`<b>${escapeHtml(st.icao)}</b> - ${escapeHtml(st.name)} WSR-88D`, { direction: "top", offset: [0, -10] });
  radarStationMarkerGroup.addLayer(marker);

  // Range rings: 50 km (27 nmi), 100 km (54 nmi), 230 km (124 nmi)
  const rings = [
    { radius: 50000, label: "50 KM (27 NMI)", color: "rgba(6, 182, 212, 0.45)", dash: "4, 6" },
    { radius: 100000, label: "100 KM (54 NMI)", color: "rgba(6, 182, 212, 0.40)", dash: "5, 7" },
    { radius: 230000, label: "230 KM (124 NMI) MAX DOPPLER", color: "rgba(34, 197, 94, 0.50)", dash: "6, 8" }
  ];

  rings.forEach((r) => {
    const circle = L.circle([lat, lon], {
      radius: r.radius,
      color: r.color,
      weight: 1.2,
      dashArray: r.dash,
      fill: false,
      interactive: false
    });
    radarStationMarkerGroup.addLayer(circle);

    const latOffset = (r.radius / 111320);
    const labelMarker = L.marker([lat + latOffset, lon], {
      icon: L.divIcon({
        className: "radar-range-ring-label",
        html: `<span>${escapeHtml(st.icao)} ${escapeHtml(r.label)}</span>`,
        iconAnchor: [50, 8]
      }),
      interactive: false
    });
    radarStationMarkerGroup.addLayer(labelMarker);
  });
}

function toggleRadarSweep(enabled) {
  if (enabled === undefined) {
    state.sweepEnabled = !state.sweepEnabled;
  } else {
    state.sweepEnabled = !!enabled;
  }
  if (radarSweepEngine) {
    radarSweepEngine.setEnabled(state.sweepEnabled);
  }

  const btn = document.getElementById("radar-sweep-btn");
  if (btn) {
    btn.classList.toggle("active", state.sweepEnabled);
    const label = document.getElementById("sweep-btn-label");
    if (label) label.textContent = state.sweepEnabled ? "Sweep: ON" : "Sweep: OFF";
  }

  const toggleCheck = document.getElementById("toggle-sweep");
  if (toggleCheck) {
    toggleCheck.checked = state.sweepEnabled;
  }
}

// -------------------------------------------------------------
// INITIALIZATION
// -------------------------------------------------------------
document.addEventListener("DOMContentLoaded", () => {
  initMap();
  initEventListeners();
  updateActiveRuleBadge();
  loadLiveHotspots();
  loadNexradStations().then(() => {
    switchRadarSource(state.radarSource);
  });

  // Try GPS location or default
  detectUserLocation(false);

  // Start 1-second interval for countdown timer
  setInterval(handleTick, 1000);
});

// -------------------------------------------------------------
// LEAFLET MAP SETUP (No API Keys Required)
// -------------------------------------------------------------
function initMap() {
  map = L.map("radar-map", {
    center: [state.currentLat, state.currentLon],
    zoom: 9,
    minZoom: 3,
    maxZoom: 18,
    zoomControl: true,
    doubleClickZoom: false // Double-click is dedicated to repositioning inspection location
  });

  // 1. Tactical Dark Basemap (Esri Dark Canvas + Reference Labels)
  const esriDarkBase = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}", {
    attribution: 'Tiles &copy; Esri &mdash; Esri, DeLorme, NAVTEQ',
    maxZoom: 16
  });
  const esriDarkRef = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}", {
    attribution: '',
    maxZoom: 16,
    zIndex: 6
  });
  basemapLayers.dark = L.layerGroup([esriDarkBase, esriDarkRef]);

  // 2. High-Resolution Satellite Hybrid (Esri World Imagery + Labels)
  const esriSatBase = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
    attribution: 'Tiles &copy; Esri, Maxar, Earthstar Geographics',
    maxZoom: 18
  });
  basemapLayers.satellite = L.layerGroup([esriSatBase, esriDarkRef]);

  // 3. Clean Streets (OpenStreetMap Standard)
  const osmBase = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    maxZoom: 19
  });
  basemapLayers.streets = L.layerGroup([osmBase]);

  // Set default basemap: Tactical Dark
  currentBasemap = basemapLayers.dark.addTo(map);

  // Initialize Layer Groups in proper z-order
  threatRingsGroup = L.layerGroup().addTo(map);
  radarStationMarkerGroup = L.layerGroup().addTo(map);
  spcOutlookGroup = L.layerGroup().addTo(map);
  watchesGroup = L.layerGroup().addTo(map);
  floodGroup = L.layerGroup().addTo(map);
  statementsGroup = L.layerGroup().addTo(map);
  warningPolygonsGroup = L.layerGroup().addTo(map);
  stormTracksGroup = L.layerGroup().addTo(map);
  groundReportsGroup = L.layerGroup().addTo(map);

  // Initialize Real-Time Radar Sweep Engine
  radarSweepEngine = new RadarSweepEngine(map);

  // High-Resolution IEM NEXRAD Composite Layer (Native 250m Doppler mosaic)
  iemNexradLayer = L.tileLayer("https://mesonet.agron.iastate.edu/cache/tile.py/1.0.0/nexrad-n0q-900913/{z}/{x}/{y}.png", {
    opacity: state.radarOpacity,
    zIndex: 20,
    minZoom: 3,
    maxZoom: 18,
    maxNativeZoom: 12
  });

  // NOAA MRMS 1-km 3D High-Res Composite Layer (combines all 160 Doppler radars nationwide)
  mrmsRadarLayer = L.tileLayer("https://mesonet.agron.iastate.edu/cache/tile.py/1.0.0/q2-hsr-900913/{z}/{x}/{y}.png", {
    opacity: state.radarOpacity,
    zIndex: 20,
    minZoom: 3,
    maxZoom: 18,
    maxNativeZoom: 12
  });

  // Map Double-Click to Inspect Threat at any coordinate
  map.on("dblclick", async (e) => {
    state.currentLat = e.latlng.lat;
    state.currentLon = e.latlng.lng;
    updateUserMarkerAndRings();
    if (state.selectedStationId === "auto") {
      updateNearestStation();
      renderLocalizedRadarStation();
      if (state.radarSource === "single_site") updateSingleSiteRadarLayer();
    }
    showLocationPing(e.latlng.lat, e.latlng.lng);
    await reverseGeocode(state.currentLat, state.currentLon);
    refreshAssessment();
  });
}

function showLocationPing(lat, lon) {
  const pingIcon = L.divIcon({
    className: "location-ping-icon",
    html: `<div class="target-reloc-pulse"></div>`,
    iconSize: [44, 44],
    iconAnchor: [22, 22]
  });
  const pingMarker = L.marker([lat, lon], { icon: pingIcon, zIndexOffset: 1500 }).addTo(map);
  setTimeout(() => {
    if (map) map.removeLayer(pingMarker);
  }, 1300);
}

function switchBasemap(type) {
  if (basemapLayers[type]) {
    if (currentBasemap) map.removeLayer(currentBasemap);
    currentBasemap = basemapLayers[type].addTo(map);
    state.activeBasemap = type;
  }
}

function switchRadarSource(source) {
  state.radarSource = source;
  const srcSelect = document.getElementById("radar-source-select");
  if (srcSelect && srcSelect.value !== source) {
    srcSelect.value = source;
  }

  const scrubberBar = document.querySelector(".hud-playback-bar");
  const palettePill = document.getElementById("radar-palette-pill");
  const smoothPill = document.getElementById("radar-smoothing-pill");
  const stationPill = document.getElementById("radar-station-pill");

  if (rainviewerRadarLayer && map.hasLayer(rainviewerRadarLayer)) map.removeLayer(rainviewerRadarLayer);
  if (iemNexradLayer && map.hasLayer(iemNexradLayer)) map.removeLayer(iemNexradLayer);
  if (mrmsRadarLayer && map.hasLayer(mrmsRadarLayer)) map.removeLayer(mrmsRadarLayer);
  if (singleSiteRadarLayer && map.hasLayer(singleSiteRadarLayer)) map.removeLayer(singleSiteRadarLayer);

  if (source === "single_site") {
    if (stationPill) stationPill.style.display = "inline-flex";
    updateSingleSiteRadarLayer();
    renderLocalizedRadarStation();
  } else if (source === "mrms") {
    mrmsRadarLayer.setOpacity(state.radarOpacity);
    mrmsRadarLayer.addTo(map);
    if (scrubberBar) scrubberBar.style.opacity = "0.45";
    if (palettePill) palettePill.style.display = "none";
    if (smoothPill) smoothPill.style.display = "none";
    if (stationPill) stationPill.style.display = "none";
    document.getElementById("radar-type-label").textContent = "NOAA MRMS 1-km 3D";
    document.getElementById("radar-time-label").textContent = "Live National 3D Reflectivity (160 NEXRADs)";
  } else if (source === "iem") {
    iemNexradLayer.setOpacity(state.radarOpacity);
    iemNexradLayer.addTo(map);
    if (scrubberBar) scrubberBar.style.opacity = "0.45";
    if (palettePill) palettePill.style.display = "none";
    if (smoothPill) smoothPill.style.display = "none";
    if (stationPill) stationPill.style.display = "none";
    document.getElementById("radar-type-label").textContent = "IEM NEXRAD SUPER-RES";
    document.getElementById("radar-time-label").textContent = "Live 250m Doppler Base Reflectivity";
  } else {
    if (scrubberBar) scrubberBar.style.opacity = "1.0";
    if (palettePill) palettePill.style.display = "inline-flex";
    if (smoothPill) smoothPill.style.display = "inline-flex";
    if (stationPill) stationPill.style.display = "none";
    showRadarFrame(state.radarIndex);
  }
}

// -------------------------------------------------------------
// WEB AUDIO API & SPEECH SYNTHESIS ALERTS
// -------------------------------------------------------------
function getAudioContext() {
  if (!audioCtx) {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    audioCtx = new AudioContextClass();
  }
  if (audioCtx.state === "suspended") {
    audioCtx.resume();
  }
  return audioCtx;
}

function playWarningSiren(durationSeconds = 3.5) {
  if (!state.sirenEnabled) return;
  try {
    const ctx = getAudioContext();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();

    osc.type = "sawtooth";
    osc.frequency.setValueAtTime(440, ctx.currentTime);

    // Two-tone rising/falling alert frequency modulation
    const cycles = 5;
    for (let i = 0; i < cycles; i++) {
      const t = ctx.currentTime + (i * 0.7);
      osc.frequency.linearRampToValueAtTime(880, t + 0.35);
      osc.frequency.linearRampToValueAtTime(440, t + 0.7);
    }

    gain.gain.setValueAtTime(0.2, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + durationSeconds);

    osc.connect(gain);
    gain.connect(ctx.destination);

    osc.start();
    osc.stop(ctx.currentTime + durationSeconds);
  } catch (err) {
    console.warn("Audio playback error:", err);
  }
}

function playAttentionChime() {
  try {
    const ctx = getAudioContext();
    const now = ctx.currentTime;
    
    [523.25, 659.25, 783.99].forEach((freq, idx) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.setValueAtTime(freq, now + idx * 0.12);
      gain.gain.setValueAtTime(0.18, now + idx * 0.12);
      gain.gain.exponentialRampToValueAtTime(0.001, now + idx * 0.12 + 0.6);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(now + idx * 0.12);
      osc.stop(now + idx * 0.12 + 0.6);
    });
  } catch (err) {
    console.warn("Chime error:", err);
  }
}

function speakAlert(text) {
  if (!state.voiceEnabled || !window.speechSynthesis) return;
  try {
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.rate = 1.05;
    utterance.pitch = 1.0;
    window.speechSynthesis.speak(utterance);
  } catch (err) {
    console.warn("Speech synthesis error:", err);
  }
}

// -------------------------------------------------------------
// LOCATION & SEARCH HANDLING
// -------------------------------------------------------------
function detectUserLocation(notifyIfDenied = true) {
  if (!navigator.geolocation) {
    if (notifyIfDenied) alert("Geolocation is not supported by your browser.");
    refreshAssessment();
    return;
  }

  const btn = document.getElementById("geo-btn");
  btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> <span>Locating...</span>`;

  navigator.geolocation.getCurrentPosition(
    (pos) => {
      state.currentLat = pos.coords.latitude;
      state.currentLon = pos.coords.longitude;
      btn.innerHTML = `<i class="fa-solid fa-location-crosshairs"></i> <span>My Location</span>`;
      map.setView([state.currentLat, state.currentLon], 10);
      reverseGeocode(state.currentLat, state.currentLon);
      refreshAssessment();
    },
    (err) => {
      console.warn("GPS Location access denied:", err.message);
      btn.innerHTML = `<i class="fa-solid fa-location-crosshairs"></i> <span>My Location</span>`;
      if (notifyIfDenied) {
        alert("Could not access GPS location. Using default location.");
      }
      refreshAssessment();
    },
    { enableHighAccuracy: true, timeout: 8000 }
  );
}

async function reverseGeocode(lat, lon) {
  try {
    const res = await fetch(`/api/reverse-geocode?lat=${lat}&lon=${lon}`);
    const data = await res.json();
    state.currentPlaceName = data.clean_name || (data.display_name ? data.display_name.split(",").slice(0, 3).join(",") : `${lat.toFixed(3)}, ${lon.toFixed(3)}`);
    updateLocationDisplay();
  } catch (e) {
    state.currentPlaceName = `${lat.toFixed(3)}, ${lon.toFixed(3)}`;
    updateLocationDisplay();
  }
}

function updateLocationDisplay() {
  document.getElementById("loc-display-name").textContent = state.currentPlaceName;
  document.getElementById("loc-coords").textContent = `(${state.currentLat.toFixed(3)}°, ${state.currentLon.toFixed(3)}°)`;
  const cwCity = document.getElementById("cw-city-name");
  if (cwCity) cwCity.textContent = state.currentPlaceName;
  const cwCoords = document.getElementById("cw-coords");
  if (cwCoords) {
    const latStr = `${Math.abs(state.currentLat).toFixed(3)}° ${state.currentLat >= 0 ? 'N' : 'S'}`;
    const lonStr = `${Math.abs(state.currentLon).toFixed(3)}° ${state.currentLon >= 0 ? 'E' : 'W'}`;
    cwCoords.textContent = `${latStr}, ${lonStr}`;
  }
}

function getWmoWeatherInfo(code) {
  switch (code) {
    case 0:
      return { desc: "Clear Sky", icon: "fa-solid fa-sun text-amber" };
    case 1:
      return { desc: "Mainly Clear", icon: "fa-solid fa-cloud-sun text-cyan" };
    case 2:
      return { desc: "Partly Cloudy", icon: "fa-solid fa-cloud-sun text-cyan" };
    case 3:
      return { desc: "Overcast", icon: "fa-solid fa-cloud text-dim" };
    case 45:
    case 48:
      return { desc: "Fog / Mist", icon: "fa-solid fa-smog text-dim" };
    case 51:
    case 53:
    case 55:
      return { desc: "Drizzle", icon: "fa-solid fa-cloud-rain text-cyan" };
    case 56:
    case 57:
      return { desc: "Freezing Drizzle", icon: "fa-solid fa-snowflake text-cyan" };
    case 61:
    case 63:
      return { desc: "Rain Showers", icon: "fa-solid fa-cloud-showers-heavy text-blue" };
    case 65:
      return { desc: "Heavy Rain", icon: "fa-solid fa-cloud-showers-heavy text-blue" };
    case 66:
    case 67:
      return { desc: "Freezing Rain", icon: "fa-solid fa-icicles text-cyan" };
    case 71:
    case 73:
    case 75:
    case 77:
      return { desc: "Snow", icon: "fa-solid fa-snowflake text-cyan" };
    case 80:
    case 81:
    case 82:
      return { desc: "Torrential Showers", icon: "fa-solid fa-cloud-showers-water text-blue" };
    case 85:
    case 86:
      return { desc: "Snow Showers", icon: "fa-solid fa-snowflake text-cyan" };
    case 95:
      return { desc: "Thunderstorm", icon: "fa-solid fa-cloud-bolt text-amber" };
    case 96:
    case 99:
      return { desc: "Severe Hail Storm", icon: "fa-solid fa-cloud-meatball text-red" };
    default:
      return { desc: "Fair / Variable", icon: "fa-solid fa-cloud-sun text-cyan" };
  }
}

function getCompassDirection(deg) {
  if (deg === null || deg === undefined) return "N";
  const directions = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
  const idx = Math.round(deg / 22.5) % 16;
  return directions[idx];
}

// Debounced search
let searchTimer = null;
function handleSearchInput(e) {
  const query = e.target.value.trim();
  const clearBtn = document.getElementById("clear-search-btn");
  const dropdown = document.getElementById("search-dropdown");

  clearBtn.style.display = query.length > 0 ? "block" : "none";
  clearTimeout(searchTimer);

  if (query.length < 2) {
    dropdown.style.display = "none";
    dropdown.innerHTML = "";
    return;
  }

  searchTimer = setTimeout(async () => {
    try {
      const res = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
      const results = await res.json();
      renderSearchResults(results);
    } catch (err) {
      console.error("Search error:", err);
    }
  }, 300);
}

function renderSearchResults(results) {
  const dropdown = document.getElementById("search-dropdown");
  dropdown.innerHTML = "";

  if (!Array.isArray(results) || results.length === 0) {
    dropdown.innerHTML = `<div class="search-dropdown-item text-dim">No locations found</div>`;
    dropdown.style.display = "block";
    return;
  }

  results.forEach((item) => {
    const el = document.createElement("div");
    el.className = "search-dropdown-item";
    el.innerHTML = `<i class="fa-solid fa-location-dot text-cyan"></i> <span>${escapeHtml(item.display_name)}</span>`;
    el.addEventListener("click", () => {
      state.currentLat = item.lat;
      state.currentLon = item.lon;
      state.currentPlaceName = item.display_name.split(",").slice(0, 3).join(",");
      document.getElementById("location-input").value = "";
      document.getElementById("clear-search-btn").style.display = "none";
      dropdown.style.display = "none";
      updateLocationDisplay();
      map.setView([state.currentLat, state.currentLon], 10);
      refreshAssessment();
    });
    dropdown.appendChild(el);
  });

  dropdown.style.display = "block";
}

// -------------------------------------------------------------
// LIVE HOTSPOTS QUICK JUMP
// -------------------------------------------------------------
async function loadLiveHotspots() {
  const listEl = document.getElementById("hotspots-list");
  try {
    const res = await fetch("/api/hotspots");
    const data = await res.json();
    const spots = data.hotspots || [];

    if (spots.length === 0) {
      listEl.innerHTML = `<div class="search-dropdown-item text-dim">No severe warnings nationwide</div>`;
      return;
    }

    listEl.innerHTML = "";
    spots.forEach((h) => {
      const item = document.createElement("div");
      item.className = "hotspot-item";
      item.innerHTML = `
        <div>
          <div class="hotspot-area">${escapeHtml(h.area)}</div>
          <div class="hotspot-event">${escapeHtml(h.event)}</div>
        </div>
        <div class="hotspot-tag">${escapeHtml(h.hail_label || "Hail Risk")}</div>
      `;
      item.addEventListener("click", () => {
        state.currentLat = h.latitude;
        state.currentLon = h.longitude;
        state.currentPlaceName = h.area;
        updateLocationDisplay();
        map.setView([state.currentLat, state.currentLon], 10);
        document.getElementById("hotspots-menu").style.display = "none";
        refreshAssessment();
      });
      listEl.appendChild(item);
    });
  } catch (err) {
    console.error("Hotspots error:", err);
  }
}

// -------------------------------------------------------------
// FULL ASSESSMENT & MULTI-SENSOR REFRESH
// -------------------------------------------------------------
async function refreshAssessment() {
  const icon = document.getElementById("refresh-icon");
  icon.classList.add("fa-spin");
  state.secondsUntilRefresh = state.autoRefreshInterval;

  updateUserMarkerAndRings();

  try {
    const url = `/api/assess?lat=${state.currentLat}&lon=${state.currentLon}&radius=${state.radiusMiles}&hours=${state.filterHours}`;
    const res = await fetch(url);
    const data = await res.json();
    if (!res.ok || data.error || !data.assessment) {
      console.warn("Assessment API error response:", data?.error || res.statusText);
      return;
    }
    state.assessmentData = data;

    renderThreatAssessment(data);
    renderWarningOverlays(data);
    renderGroundReports(data.lsr_reports || []);
    renderConvectiveSounding(data.convective_data || {});
    updateDamageSimulator(state.simulatedHail !== null ? state.simulatedHail : data.assessment?.max_hail_inches);

    if (data.nearest_radar && state.selectedStationId === "auto") {
      state.activeStation = data.nearest_radar;
      renderLocalizedRadarStation();
      if (state.radarSource === "single_site") {
        updateSingleSiteRadarLayer();
      }
      const select = document.getElementById("radar-station-select");
      if (select) {
        const autoOpt = select.querySelector("option[value='auto']");
        if (autoOpt) {
          autoOpt.textContent = `🎯 Auto: ${data.nearest_radar.icao} (${data.nearest_radar.name} - ${data.nearest_radar.distance_miles} mi)`;
        }
      }
    }

    await loadRainViewerRadar();

    // High threat alarm notification gated by custom thresholds (FEAT-01)
    const level = data.assessment?.level || "NONE";
    try {
      const thRes = await fetch(
        `/api/threat/threshold-check?lat=${state.currentLat}&lon=${state.currentLon}&min_hail=${state.thresholdConfig.minHail}&min_score=${state.thresholdConfig.minScore}&max_eta=${state.thresholdConfig.maxEta}&radius=${state.radiusMiles}`
      );
      if (thRes.ok) {
        const thData = await thRes.json();
        const triggered = !!thData.evaluation?.triggered;
        if (triggered && (!state.thresholdTriggered || level !== state.threatLevel)) {
          playWarningSiren(4.5);
          speakAlert(
            `Hail threat alert for ${state.currentPlaceName}. ${thData.directive || 'Take protective shelter immediately.'}`
          );
        }
        state.thresholdTriggered = triggered;
      } else {
        // Fallback threshold gating
        const hailMet = (data.assessment?.max_hail_inches || 0) >= state.thresholdConfig.minHail;
        const scoreMet = (data.assessment?.score || 0) >= state.thresholdConfig.minScore;
        if ((hailMet || scoreMet) && level !== state.threatLevel) {
          playWarningSiren(4.5);
          speakAlert(`Hail warning alert for ${state.currentPlaceName}. Projected hail diameter: ${data.assessment?.max_hail_label || 'Severe hail'}. Take protective shelter immediately.`);
        }
      }
    } catch (e) {
      const hailMet = (data.assessment?.max_hail_inches || 0) >= state.thresholdConfig.minHail;
      const scoreMet = (data.assessment?.score || 0) >= state.thresholdConfig.minScore;
      if ((hailMet || scoreMet) && level !== state.threatLevel) {
        playWarningSiren(4.5);
        speakAlert(`Hail warning alert for ${state.currentPlaceName}. Projected hail diameter: ${data.assessment?.max_hail_label || 'Severe hail'}. Take protective shelter immediately.`);
      }
    }
    state.threatLevel = level;
    updateActiveRuleBadge();

  } catch (err) {
    console.error("Assessment fetch error:", err);
  } finally {
    icon.classList.remove("fa-spin");
  }
}

// -------------------------------------------------------------
// MAP LAYERS: USER MARKER & THREAT RINGS
// -------------------------------------------------------------
function updateUserMarkerAndRings() {
  threatRingsGroup.clearLayers();

  const userIcon = L.divIcon({
    className: "custom-div-icon",
    html: `<div class="marker-user"></div>`,
    iconSize: [16, 16],
    iconAnchor: [8, 8]
  });

  if (userMarker) {
    userMarker.setLatLng([state.currentLat, state.currentLon]);
  } else {
    userMarker = L.marker([state.currentLat, state.currentLon], { icon: userIcon }).addTo(threatRingsGroup);
    userMarker.bindPopup("<b>Target Monitoring Location</b><br>Hail danger zones projected outwards.");
  }

  if (document.getElementById("toggle-rings")?.checked) {
    // 5-Mile Critical Zone (Red)
    L.circle([state.currentLat, state.currentLon], {
      radius: 5 * 1609.34,
      color: "#ef4444",
      weight: 1.5,
      dashArray: "4, 6",
      fillColor: "#ef4444",
      fillOpacity: 0.05
    }).addTo(threatRingsGroup).bindTooltip("5-Mile Critical Zone");

    // 15-Mile Warning Perimeter (Amber)
    L.circle([state.currentLat, state.currentLon], {
      radius: 15 * 1609.34,
      color: "#f59e0b",
      weight: 1.2,
      dashArray: "6, 8",
      fillColor: "#f59e0b",
      fillOpacity: 0.03
    }).addTo(threatRingsGroup).bindTooltip("15-Mile Approach Zone");

    // 30-Mile Monitoring Buffer (Cyan)
    L.circle([state.currentLat, state.currentLon], {
      radius: 30 * 1609.34,
      color: "#06b6d4",
      weight: 1.0,
      dashArray: "8, 10",
      fillColor: "#06b6d4",
      fillOpacity: 0.02
    }).addTo(threatRingsGroup).bindTooltip("30-Mile Radar Perimeter");
  }
}

// -------------------------------------------------------------
// MODERNIZED NWS WARNING & ADVISORY OVERLAYS
// -------------------------------------------------------------
function renderWarningOverlays(data) {
  warningPolygonsGroup.clearLayers();
  floodGroup.clearLayers();
  statementsGroup.clearLayers();
  watchesGroup.clearLayers();
  stormTracksGroup.clearLayers();
  spcOutlookGroup.clearLayers();

  const regWarnings = data.regional_warnings?.warnings || [];
  const showSevere = document.getElementById("toggle-polygons")?.checked;
  const showFlood = document.getElementById("toggle-floods")?.checked;
  const showAdvisories = document.getElementById("toggle-advisories")?.checked;
  const showWatches = document.getElementById("toggle-watches")?.checked;
  const showTracks = document.getElementById("toggle-tracks")?.checked;
  const showSpc = document.getElementById("toggle-spc")?.checked;

  let nearestApproachingEta = null;

  regWarnings.forEach((w) => {
    if (!w.geometry) return;

    const cat = w.category;
    let targetGroup = warningPolygonsGroup;
    let isVisible = showSevere;

    if (cat === "FLASH_FLOOD") {
      targetGroup = floodGroup;
      isVisible = showFlood;
    } else if (cat === "ADVISORY") {
      targetGroup = statementsGroup;
      isVisible = showAdvisories;
    } else if (cat === "WATCH") {
      targetGroup = watchesGroup;
      isVisible = showWatches;
    } else {
      isVisible = showSevere;
    }

    if (!isVisible) return;

    const s = w.style || {};
    const color = (s.color && /^#[0-9a-fA-F]{3,8}$/.test(s.color)) ? s.color : "#f59e0b";

    const layer = L.geoJSON(w.geometry, {
      style: {
        color: color,
        weight: s.weight || 2.5,
        opacity: 0.95,
        dashArray: s.dashArray || null,
        fillColor: s.fillColor || color,
        fillOpacity: s.fillOpacity || 0.22
      }
    });

    // Interactive Hover effect
    layer.on("mouseover", function() {
      this.setStyle({ weight: (s.weight || 2.5) + 1.5, fillOpacity: 0.35 });
    });
    layer.on("mouseout", function() {
      this.setStyle({ weight: s.weight || 2.5, fillOpacity: s.fillOpacity || 0.22 });
    });

    // Calculate time remaining countdown
    let expiresText = "Active";
    if (w.expires) {
      const expDate = new Date(w.expires);
      const diffMins = Math.round((expDate.getTime() - Date.now()) / 60000);
      if (diffMins > 0) {
        expiresText = `Expires in ${diffMins} mins (${expDate.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })})`;
      } else {
        expiresText = `Expired ${Math.abs(diffMins)} mins ago`;
      }
    }

    // Modern glassmorphism popup card
    const hailTag = w.hail_label || (w.hail_size_in ? `${w.hail_size_in.toFixed(2)}" Hail` : "N/A");
    const windTag = w.wind_gust || "N/A";
    const motion = w.motion;
    let motionSafe = "Radar Scan";
    if (motion) {
      motionSafe = `Moving ${escapeHtml(motion.heading_deg)}° at ${escapeHtml(motion.speed_mph)} mph`;
      if (motion.eta_mins !== null && motion.eta_mins !== undefined) {
        motionSafe += ` &bull; <b style="color:var(--color-magenta);">ETA ~${escapeHtml(motion.eta_mins)} mins</b>`;
        if (nearestApproachingEta === null || motion.eta_mins < nearestApproachingEta) {
          nearestApproachingEta = motion.eta_mins;
        }
      }
    }

    const directiveRaw = w.instruction || w.headline || (w.description ? w.description.slice(0, 160) + '...' : 'Monitor local conditions.');
    const directiveSafe = escapeHtml(directiveRaw);

    layer.bindPopup(`
      <div class="nws-popup-card">
        <div class="nws-popup-top">
          <div class="nws-event-name" style="color: ${color};">
            <span class="nws-pulse-dot" style="background: ${color};"></span>
            ${escapeHtml(w.event)}
          </div>
          <span style="font-size: 0.68rem; color: var(--text-dim); font-family: var(--font-mono);">${escapeHtml(w.wfo || 'NWS')}</span>
        </div>

        <div class="nws-badge-row">
          <span class="nws-tag-pill" style="color: var(--color-amber);">
            <i class="fa-solid fa-cloud-meatball"></i> HAIL: ${escapeHtml(hailTag)}
          </span>
          <span class="nws-tag-pill" style="color: var(--color-cyan);">
            <i class="fa-solid fa-wind"></i> WIND: ${escapeHtml(windTag)}
          </span>
          ${w.tornado_detection ? `
            <span class="nws-tag-pill" style="color: var(--color-red);">
              <i class="fa-solid fa-tornado"></i> ${escapeHtml(w.tornado_detection)}
            </span>
          ` : ''}
        </div>

        <div class="nws-meta-row">
          <div><b>Timing:</b> ${escapeHtml(expiresText)}</div>
          <div><b>Distance:</b> ${w.distance_miles !== null && w.distance_miles !== undefined ? escapeHtml(w.distance_miles) + ' mi from target' : 'In affected zone'}</div>
          <div><b>Motion:</b> ${motionSafe}</div>
        </div>

        <div class="nws-instruction-box">
          <i class="fa-solid fa-triangle-exclamation"></i>
          <b>DIRECTIVE:</b> ${directiveSafe}
        </div>
      </div>
    `);

    targetGroup.addLayer(layer);

    // Render Storm Motion Vector Arrow & Projected Track if available
    if (showTracks && motion && motion.projected_path && motion.projected_path.length > 0) {
      const slat = motion.storm_lat;
      const slon = motion.storm_lon;
      const pathPts = [[slat, slon], ...motion.projected_path.map(p => [p.lat, p.lon])];

      // Swath Track Line
      const trackLine = L.polyline(pathPts, {
        color: "#d946ef",
        weight: 3,
        dashArray: "5, 8",
        opacity: 0.85
      }).addTo(stormTracksGroup);

      // Origin Storm Marker
      L.circleMarker([slat, slon], {
        radius: 6,
        color: "#d946ef",
        fillColor: "#ffffff",
        fillOpacity: 0.9,
        weight: 2
      }).addTo(stormTracksGroup).bindTooltip(`Storm Core (${escapeHtml(motion.speed_mph)} mph)`);

      // 15, 30, 45-min forecast projection pins
      motion.projected_path.forEach((p) => {
        const pin = L.circleMarker([p.lat, p.lon], {
          radius: 4,
          color: "#a855f7",
          fillColor: "#a855f7",
          fillOpacity: 0.8,
          weight: 1
        }).addTo(stormTracksGroup);
        pin.bindTooltip(`+${escapeHtml(p.minutes)}m Projected Position`);
      });

      // Target ETA connector if storm is moving toward user
      if (motion.eta_mins !== null) {
        L.polyline([[slat, slon], [state.currentLat, state.currentLon]], {
          color: "#ec4899",
          weight: 2,
          dashArray: "3, 6",
          opacity: 0.9
        }).addTo(stormTracksGroup).bindTooltip(`Projected Path to Target: ETA ~${escapeHtml(motion.eta_mins)}m`);
      }
    }
  });

  // Update Status Bar Approach ETA Pill
  const etaPill = document.getElementById("eta-pill");
  const etaVal = document.getElementById("eta-val");
  if (etaPill && etaVal) {
    if (nearestApproachingEta !== null) {
      etaPill.style.display = "flex";
      etaVal.textContent = `~${nearestApproachingEta}m`;
    } else {
      etaPill.style.display = "none";
    }
  }

  // Render NOAA SPC Day 1 Convective Outlook if toggled
  if (showSpc && data.spc_outlook?.categorical?.features) {
    const spcLayer = L.geoJSON(data.spc_outlook.categorical, {
      style: function(feature) {
        const props = feature.properties || {};
        return {
          color: props.stroke || "#10b981",
          fillColor: props.fill || "#10b981",
          weight: 1.5,
          fillOpacity: 0.12,
          dashArray: "3, 5"
        };
      },
      onEachFeature: function(feature, layer) {
        const p = feature.properties || {};
        const safeLabel = escapeHtml(p.LABEL || "");
        const safeLabel2 = escapeHtml(p.LABEL2 || "Convective Risk");
        layer.bindTooltip(`<b>SPC Day 1 Outlook:</b> ${safeLabel} - ${safeLabel2}`);
      }
    });
    spcOutlookGroup.addLayer(spcLayer);
  }
}

// -------------------------------------------------------------
// MAP LAYERS: mPING & LSR GROUND REPORTS
// -------------------------------------------------------------
function renderGroundReports(reports) {
  groundReportsGroup.clearLayers();
  if (!document.getElementById("toggle-reports")?.checked) return;

  (Array.isArray(reports) ? reports : []).forEach((r) => {
    const rawSize = r.hail_size_in;
    const size = typeof rawSize === 'number' && !isNaN(rawSize) ? rawSize : (parseFloat(rawSize) || 0.0);
    let color = "#10b981";
    let radius = 10;

    if (size >= 2.50) {
      color = "#ec4899";
      radius = 18;
    } else if (size >= 1.75) {
      color = "#ef4444";
      radius = 16;
    } else if (size >= 1.00) {
      color = "#f97316";
      radius = 14;
    } else if (size >= 0.75) {
      color = "#f59e0b";
      radius = 12;
    }

    const iconHtml = `<div class="hail-marker-pulse" style="background:${color}; width:${radius*2}px; height:${radius*2}px;">${size > 0 ? size.toFixed(1) : 'H'}</div>`;
    const markerIcon = L.divIcon({
      className: "custom-div-icon",
      html: iconHtml,
      iconSize: [radius * 2, radius * 2],
      iconAnchor: [radius, radius]
    });

    const marker = L.marker([r.latitude, r.longitude], { icon: markerIcon });
    const srcTypeEsc = escapeHtml(r.source_type || "Observer");
    const hailDescEsc = escapeHtml(r.hail_description || "Hail");
    const distMiEsc = r.distance_miles !== null && r.distance_miles !== undefined ? escapeHtml(r.distance_miles) : "N/A";
    const obsTimeEsc = escapeHtml(r.age_hours !== null && r.age_hours !== undefined && r.age_hours < 24 ? r.age_hours + 'h ago' : (r.valid || ''));
    const remarkEsc = escapeHtml(r.remark || '');

    marker.bindPopup(`
      <div style="font-family: var(--font-sans); min-width: 200px;">
        <div style="font-weight: 700; color: ${color}; font-size: 0.88rem; margin-bottom: 4px;">
          <i class="fa-solid fa-users"></i> ${srcTypeEsc}
        </div>
        <div style="font-family: var(--font-mono); font-size: 0.8rem; margin-bottom: 4px;">
          <b>HAIL:</b> <span style="color: var(--color-amber);">${hailDescEsc}</span>
        </div>
        <div style="font-size: 0.72rem; color: #94a3b8; margin-bottom: 2px;">
          <b>Distance:</b> ${distMiEsc} miles away
        </div>
        <div style="font-size: 0.72rem; color: #94a3b8; margin-bottom: 6px;">
          <b>Observed:</b> ${obsTimeEsc}
        </div>
        ${remarkEsc ? `<div style="font-size: 0.7rem; color: #cbd5e1; font-style: italic; background: rgba(0,0,0,0.3); padding: 4px 6px; border-radius: 4px;">"${remarkEsc}"</div>` : ''}
      </div>
    `);

    groundReportsGroup.addLayer(marker);
  });
}

// -------------------------------------------------------------
// RAINVIEWER ANIMATED NEXRAD RADAR (Smooth zoom & No API key)
// -------------------------------------------------------------
async function loadRainViewerRadar() {
  try {
    const res = await fetch("/api/radar");
    const data = await res.json();
    state.radarFrames = data.frames || [];

    const scrubber = document.getElementById("radar-scrubber");
    if (scrubber) {
      scrubber.max = Math.max(0, state.radarFrames.length - 1);
    }

    if (state.radarFrames.length > 0) {
      state.radarIndex = state.radarFrames.length - 1;
      if (scrubber) scrubber.value = state.radarIndex;
      showRadarFrame(state.radarIndex, data.host);
    }
  } catch (err) {
    console.error("RainViewer metadata error:", err);
  }
}

function showRadarFrame(index, host) {
  if (!state.radarFrames || state.radarFrames.length === 0) return;
  const frame = state.radarFrames[index];
  if (!frame) return;

  const baseHost = host || "https://tilecache.rainviewer.com";
  const palette = state.radarPalette || 6;
  const smooth = state.radarSmooth || "1_1";
  const tileUrl = `${baseHost}${frame.path}/512/{z}/{x}/{y}/${palette}/${smooth}.png`;

  if (rainviewerRadarLayer) {
    map.removeLayer(rainviewerRadarLayer);
  }

  // KEY FIX: maxNativeZoom: 7 prevents "Zoom Level Not Supported" tiles from RainViewer!
  // Leaflet automatically scales the zoom 7 tiles up to zoom 18!
  rainviewerRadarLayer = L.tileLayer(tileUrl, {
    opacity: state.radarOpacity,
    zIndex: 20,
    minZoom: 3,
    maxZoom: 18,
    maxNativeZoom: 7
  });

  if (state.radarSource === "rainviewer") {
    rainviewerRadarLayer.addTo(map);
  }

  // Update time indicator
  const timeDate = new Date(frame.time * 1000);
  const timeStr = timeDate.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  const typeBadge = frame.type === "nowcast" ? "NOWCAST PREDICTIVE" : "LIVE NEXRAD dBZ";
  
  const timeLabel = document.getElementById("radar-time-label");
  const typeLabel = document.getElementById("radar-type-label");
  const scrubber = document.getElementById("radar-scrubber");

  if (state.radarSource === "rainviewer") {
    if (timeLabel) timeLabel.textContent = `${timeStr} (${index + 1}/${state.radarFrames.length})`;
    if (typeLabel) typeLabel.textContent = typeBadge;
    if (scrubber) scrubber.value = index;
  }
}

function playRadarLoop() {
  if (!state.radarFrames || state.radarFrames.length === 0) {
    return;
  }
  if (state.radarPlaying) {
    clearInterval(state.radarTimer);
    state.radarPlaying = false;
    document.getElementById("play-btn-icon").className = "fa-solid fa-play";
  } else {
    state.radarPlaying = true;
    document.getElementById("play-btn-icon").className = "fa-solid fa-pause";
    state.radarTimer = setInterval(() => {
      if (!state.radarFrames || state.radarFrames.length === 0) {
        clearInterval(state.radarTimer);
        state.radarPlaying = false;
        document.getElementById("play-btn-icon").className = "fa-solid fa-play";
        return;
      }
      state.radarIndex = (state.radarIndex + 1) % state.radarFrames.length;
      showRadarFrame(state.radarIndex);
    }, 700);
  }
}

// -------------------------------------------------------------
// RENDER THREAT ASSESSMENT & INTEL MATRICES
// -------------------------------------------------------------
function renderThreatAssessment(data) {
  const a = data.assessment || {};
  const convective = data.convective_data || {};
  const alerts = data.nws_alerts || [];
  const reports = data.lsr_reports || [];
  const regWarnings = data.regional_warnings?.warnings || [];

  // Update Status Bar
  const badge = document.getElementById("threat-badge");
  badge.textContent = a.level;
  badge.className = `danger-badge ${a.badge_class}`;

  document.getElementById("threat-score").textContent = `${a.score}%`;
  document.getElementById("max-hail-val").textContent = a.max_hail_label;
  document.getElementById("dbz-val").textContent = a.estimated_dbz ? a.estimated_dbz.split(" ")[0] + " dBZ" : "--";
  document.getElementById("intel-timestamp").textContent = `${new Date().toLocaleTimeString()} LOCAL`;

  // Hail Potential Index (HPI)
  const hpiVal = document.getElementById("hpi-val");
  if (hpiVal) {
    hpiVal.textContent = `${convective.hail_potential_index || a.score}/100`;
  }

  // Directive Card
  document.getElementById("directive-text").textContent = a.action_recommendation;
  const reasonsContainer = document.getElementById("threat-reasons-container");
  reasonsContainer.innerHTML = "";

  (a.reasons || []).forEach((r) => {
    const el = document.createElement("div");
    let cls = "reason-item";
    if (r.startsWith("CRITICAL") || r.startsWith("WARNING") || r.startsWith("APPROACHING")) cls += " critical";
    else if (r.startsWith("GROUND TRUTH") || r.startsWith("ADVISORY") || r.startsWith("PROXIMITY")) cls += " warning";
    el.className = cls;
    el.textContent = r;
    reasonsContainer.appendChild(el);
  });

  // Matrix Cell 1: NWS
  const severeAlert = alerts.find(x => x.event.includes("Thunderstorm") || x.event.includes("Tornado") || x.event.includes("Special")) ||
                      regWarnings.find(x => (x.distance_miles || 999) <= 25);

  if (severeAlert) {
    document.getElementById("nws-status").textContent = severeAlert.category === "TORNADO" ? "TORNADO WARNING" : "WARNING ACTIVE";
    document.getElementById("nws-status").style.color = "var(--color-red)";
    document.getElementById("nws-hail-tag").textContent = severeAlert.hail_label || `${severeAlert.hail_size_in || 1.0}" Hail`;
    document.getElementById("nws-threat-mode").textContent = severeAlert.hail_threat_type || "RADAR INDICATED";
    document.getElementById("nws-event-summary").textContent = severeAlert.headline || severeAlert.event;
  } else {
    document.getElementById("nws-status").textContent = "ALL CLEAR";
    document.getElementById("nws-status").style.color = "var(--color-green)";
    document.getElementById("nws-hail-tag").textContent = "None";
    document.getElementById("nws-threat-mode").textContent = "No Direct Threat";
    document.getElementById("nws-event-summary").textContent = "No active severe hail bulletins for point";
  }

  // Matrix Cell 2: mPING / LSR Ground Reports
  if (reports.length > 0) {
    const closest = reports[0];
    document.getElementById("mping-status").textContent = `${reports.length} REPORTED`;
    document.getElementById("mping-status").style.color = "var(--color-amber)";
    document.getElementById("mping-closest-dist").textContent = `${closest.distance_miles} mi`;
    document.getElementById("mping-closest-size").textContent = closest.hail_description;
    document.getElementById("mping-summary").textContent = `${closest.source_type} (${closest.city || 'Near target'})`;
  } else {
    document.getElementById("mping-status").textContent = "ZERO REPORTS";
    document.getElementById("mping-status").style.color = "var(--color-green)";
    document.getElementById("mping-closest-dist").textContent = "None";
    document.getElementById("mping-closest-size").textContent = "None";
    document.getElementById("mping-summary").textContent = `No hail observations in ${state.radiusMiles}-mi radius`;
  }

  // Matrix Cell 3: Radar
  document.getElementById("radar-core-level").textContent = a.estimated_dbz ? a.estimated_dbz.split(" ")[0] : "Normal";
  document.getElementById("radar-precip-rate").textContent = `${convective.precipitation_mm || 0} mm/h`;
  document.getElementById("radar-summary").textContent = convective.precipitation_mm > 0 ? "Active convective precipitation" : "Clear radar sweep";

  // Matrix Cell 4: Convective Sounding
  document.getElementById("sounding-cape").textContent = `${convective.peak_cape_today || 0} J/kg`;
  document.getElementById("sounding-li").textContent = `${convective.min_lifted_index_today || 0}`;
  if (convective.peak_cape_today >= 2000) {
    document.getElementById("sounding-summary").textContent = "Extreme updraft buoyancy (Violent storms)";
    document.getElementById("sounding-summary").style.color = "var(--color-red)";
  } else if (convective.peak_cape_today >= 1000) {
    document.getElementById("sounding-summary").textContent = "Moderate convective instability";
    document.getElementById("sounding-summary").style.color = "var(--color-amber)";
  } else {
    document.getElementById("sounding-summary").textContent = "Stable atmospheric column";
    document.getElementById("sounding-summary").style.color = "var(--text-dim)";
  }

  // Render City Weather Box on Map
  renderCityWeatherBox(data);

  // Render Feed List
  renderIntelFeed(alerts, reports, regWarnings);
}

// -------------------------------------------------------------
// FLOATING CITY WEATHER BOX ON MAP
// -------------------------------------------------------------
function renderCityWeatherBox(data) {
  const card = document.getElementById("city-weather-card");
  if (!card) return;

  if (!state.cityWeatherVisible) {
    card.style.display = "none";
  } else {
    card.style.display = "flex";
  }

  const convective = data.convective_data || {};
  const assessment = data.assessment || {};

  // City Name & Coordinates
  const cityNameEl = document.getElementById("cw-city-name");
  if (cityNameEl) cityNameEl.textContent = state.currentPlaceName || "Selected Area";

  const coordsEl = document.getElementById("cw-coords");
  if (coordsEl) {
    const latStr = `${Math.abs(state.currentLat).toFixed(3)}° ${state.currentLat >= 0 ? 'N' : 'S'}`;
    const lonStr = `${Math.abs(state.currentLon).toFixed(3)}° ${state.currentLon >= 0 ? 'E' : 'W'}`;
    coordsEl.textContent = `${latStr}, ${lonStr}`;
  }

  // Condition Icon & Description
  const weatherInfo = getWmoWeatherInfo(convective.weather_code);
  const iconEl = document.getElementById("cw-condition-icon");
  const descEl = document.getElementById("cw-condition-desc");
  if (iconEl) iconEl.className = weatherInfo.icon;
  if (descEl) descEl.textContent = weatherInfo.desc;

  // Temperatures (°F and °C)
  const tempF = convective.temperature_f !== null && convective.temperature_f !== undefined ? Math.round(convective.temperature_f) : "--";
  const tempC = convective.temperature_c !== null && convective.temperature_c !== undefined ? convective.temperature_c.toFixed(1) : "--";
  const tempFEl = document.getElementById("cw-temp-f");
  const tempCEl = document.getElementById("cw-temp-c");
  if (tempFEl) tempFEl.textContent = `${tempF}°`;
  if (tempCEl) tempCEl.textContent = `${tempC}°C`;

  // Hail Threat Badge & HPI
  const badgeEl = document.getElementById("cw-threat-badge");
  if (badgeEl) {
    const lvl = assessment.level || "NONE";
    badgeEl.textContent = `HAIL: ${lvl}`;
    badgeEl.className = `cw-badge badge-${lvl.toLowerCase()}`;
  }

  const hpiValEl = document.getElementById("cw-hpi-val");
  if (hpiValEl) {
    const hpi = convective.hail_potential_index !== undefined ? convective.hail_potential_index : (assessment.score || 0);
    hpiValEl.textContent = `${hpi}/100`;
  }

  // Wind Speed, Direction, and Gusts
  const windEl = document.getElementById("cw-wind");
  if (windEl) {
    const speed = convective.wind_speed_mph || 0;
    const dir = getCompassDirection(convective.wind_direction_deg);
    const gusts = convective.wind_gusts_mph;
    windEl.textContent = gusts && gusts > speed + 4 ? `${speed} mph ${dir} (g: ${gusts})` : `${speed} mph ${dir}`;
  }

  // Surface Pressure (Barometer)
  const pressEl = document.getElementById("cw-pressure");
  if (pressEl) {
    const inHg = convective.surface_pressure_inhg || 29.92;
    pressEl.textContent = `${inHg.toFixed(2)} inHg`;
  }

  // Relative Humidity
  const humEl = document.getElementById("cw-humidity");
  if (humEl) {
    humEl.textContent = convective.humidity_percent !== null && convective.humidity_percent !== undefined ? `${convective.humidity_percent}%` : "--%";
  }

  // Rain / Precipitation Rate
  const precipEl = document.getElementById("cw-precip");
  if (precipEl) {
    const p = convective.precipitation_mm || 0;
    precipEl.textContent = `${p.toFixed(1)} mm/h`;
  }

  // Freezing Level (Hail Melting Layer)
  const fzEl = document.getElementById("cw-freezing");
  if (fzEl) {
    const fz = convective.freezing_level_ft;
    fzEl.textContent = fz ? `${fz.toLocaleString()} ft` : "-- ft";
  }

  // Convective Buoyancy (CAPE)
  const capeEl = document.getElementById("cw-cape");
  if (capeEl) {
    const cape = convective.peak_cape_today || convective.cape_j_kg || 0;
    capeEl.textContent = `${cape} J/kg`;
  }

  // Updated timestamp
  const updatedEl = document.getElementById("cw-updated");
  if (updatedEl) {
    updatedEl.textContent = `Observed ${new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`;
  }
}

// -------------------------------------------------------------
// ATMOSPHERIC CONVECTIVE BAROMETER
// -------------------------------------------------------------
function renderConvectiveSounding(convective) {
  const cape = convective.peak_cape_today || convective.cape_j_kg || 0;
  const li = convective.min_lifted_index_today || convective.lifted_index || 0;
  const hpi = convective.hail_potential_index || 0;

  const hpiBadge = document.getElementById("sounding-hpi-badge");
  if (hpiBadge) hpiBadge.textContent = `HPI: ${hpi}/100`;

  const capeVal = document.getElementById("gauge-cape-val");
  const capeBar = document.getElementById("gauge-cape-bar");
  const capeDesc = document.getElementById("gauge-cape-desc");
  if (capeVal) capeVal.textContent = `${cape} J/kg`;
  if (capeBar) {
    const pct = Math.min(100, Math.round((cape / 3500) * 100));
    capeBar.style.width = `${pct}%`;
  }
  if (capeDesc) {
    if (cape >= 2500) capeDesc.textContent = "Extreme buoyancy: Violent updrafts favor giant hail growth.";
    else if (cape >= 1200) capeDesc.textContent = "Moderate instability: Thunderstorm updrafts capable of hail.";
    else capeDesc.textContent = "Low convective energy: Severe storm updrafts unlikely.";
  }

  const cinStatus = document.getElementById("gauge-cin-status");
  const liVal = document.getElementById("gauge-li-val");
  if (cinStatus) cinStatus.textContent = convective.cin_cap_status || "Scanning...";
  if (liVal) liVal.textContent = `${li > 0 ? '+' : ''}${li}`;

  const fzVal = document.getElementById("gauge-fz-val");
  const fzSub = document.getElementById("gauge-fz-sub");
  if (fzVal) fzVal.textContent = convective.freezing_level_ft ? `${convective.freezing_level_ft.toLocaleString()} ft` : "--";
  if (fzSub) fzSub.textContent = convective.hail_survival_factor || "Hail survival factor";

  const pressVal = document.getElementById("gauge-press-val");
  const gustsVal = document.getElementById("gauge-gusts-val");
  if (pressVal) pressVal.textContent = convective.surface_pressure_inhg ? `${convective.surface_pressure_inhg} inHg` : "--";
  if (gustsVal) gustsVal.textContent = `Gusts: ${convective.wind_gusts_mph || 0} mph`;
}

// -------------------------------------------------------------
// HAIL DIAMETER & PROPERTY DAMAGE SIMULATOR
// -------------------------------------------------------------
function updateDamageSimulator(hailInches) {
  const inches = (hailInches !== null && hailInches !== undefined) ? hailInches : (state.assessmentData?.assessment?.max_hail_inches || 0.0);
  
  const disc = document.getElementById("hail-disc");
  const diamText = document.getElementById("hail-disc-diameter");
  const title = document.getElementById("hail-scale-title");
  const desc = document.getElementById("hail-scale-desc");
  const riskVehicle = document.getElementById("risk-vehicle");
  const riskRoof = document.getElementById("risk-roof");
  const riskSolar = document.getElementById("risk-solar");
  const riskHuman = document.getElementById("risk-human");
  const checklist = document.getElementById("checklist-items");

  const pxSize = Math.max(34, Math.min(110, 34 + inches * 18));
  if (disc) {
    disc.style.width = `${pxSize}px`;
    disc.style.height = `${pxSize}px`;
  }
  if (diamText) diamText.textContent = `${inches.toFixed(2)}"`;

  // Scale lighting and border
  if (disc) {
    if (inches <= 0) {
      disc.style.background = "radial-gradient(circle, #334155, #1e293b)";
      disc.style.borderColor = "#475569";
    } else {
      disc.style.background = "radial-gradient(circle at 35% 35%, #ffffff 0%, #cbd5e1 55%, #64748b 100%)";
      disc.style.borderColor = inches >= 2.0 ? "#ec4899" : (inches >= 1.0 ? "#f97316" : "#f59e0b");
    }
  }

  let chkHtml = "";

  if (inches >= 2.50) {
    if (title) title.textContent = `Tennis / Baseball Size (${inches.toFixed(2)}")`;
    if (desc) desc.textContent = "DESTRUCTIVE IMPACT: Windshields shattered, severe roof punctures, serious head trauma risk.";
    setRiskStatus(riskVehicle, "TOTAL LOSS", "var(--color-red)");
    setRiskStatus(riskRoof, "SEVERE", "var(--color-red)");
    if (riskSolar) setRiskStatus(riskSolar, "SHATTERED", "var(--color-red)");
    setRiskStatus(riskHuman, "LETHAL", "var(--color-magenta)");
    chkHtml = `
      <li><b>EMERGENCY:</b> Move to interior room away from windows and skylights.</li>
      <li>Move vehicles under solid garage or concrete cover immediately.</li>
      <li>Close interior window coverings to capture shattered glass.</li>
      <li>Bring all pets and livestock into sturdy shelter immediately.</li>
    `;
  } else if (inches >= 1.75) {
    if (title) title.textContent = `Golf Ball Size (${inches.toFixed(2)}")`;
    if (desc) desc.textContent = "HIGH DAMAGE: Dented vehicle hoods, cracked glass, structural roof shingle bruising.";
    setRiskStatus(riskVehicle, "HIGH", "var(--color-red)");
    setRiskStatus(riskRoof, "HIGH", "var(--color-amber)");
    if (riskSolar) setRiskStatus(riskSolar, "FRACTURE", "var(--color-amber)");
    setRiskStatus(riskHuman, "DANGEROUS", "var(--color-red)");
    chkHtml = `
      <li>Seek interior shelter immediately; avoid outdoor open areas.</li>
      <li>Cover or park vehicles under carport or heavy protective blankets.</li>
      <li>Avoid rooms with overhead skylights.</li>
    `;
  } else if (inches >= 1.00) {
    if (title) title.textContent = `Quarter Size (${inches.toFixed(2)}") - Severe`;
    if (desc) desc.textContent = "NWS SEVERE CRITERIA: Cosmetic vehicle dings, shingle granule loss, outdoor hazard.";
    setRiskStatus(riskVehicle, "ELEVATED", "var(--color-amber)");
    setRiskStatus(riskRoof, "MODERATE", "var(--color-amber)");
    if (riskSolar) setRiskStatus(riskSolar, "MINOR", "var(--color-green)");
    setRiskStatus(riskHuman, "CAUTION", "var(--color-amber)");
    chkHtml = `
      <li>Move indoors until storm core passes.</li>
      <li>Move vehicles under cover if safe to do so.</li>
    `;
  } else if (inches >= 0.25) {
    if (title) title.textContent = `Pea / Marble Size (${inches.toFixed(2)}")`;
    if (desc) desc.textContent = "Sub-severe graupel / hail pellets. Minimal property damage expected.";
    setRiskStatus(riskVehicle, "LOW", "var(--color-green)");
    setRiskStatus(riskRoof, "LOW", "var(--color-green)");
    if (riskSolar) setRiskStatus(riskSolar, "SAFE", "var(--color-green)");
    setRiskStatus(riskHuman, "SAFE", "var(--color-green)");
    chkHtml = `<li>Atmosphere marginally convective. Monitor updating radar.</li>`;
  } else {
    if (title) title.textContent = "No Active Hail Threat";
    if (desc) desc.textContent = "Atmosphere stable. Zero severe hail cores detected in proximity.";
    setRiskStatus(riskVehicle, "SAFE", "var(--color-green)");
    setRiskStatus(riskRoof, "SAFE", "var(--color-green)");
    if (riskSolar) setRiskStatus(riskSolar, "SAFE", "var(--color-green)");
    setRiskStatus(riskHuman, "SAFE", "var(--color-green)");
    chkHtml = `<li>Conditions safe. No vehicle or property movement required.</li>`;
  }

  if (checklist) checklist.innerHTML = chkHtml;
}

function setRiskStatus(el, text, color) {
  if (!el) return;
  el.textContent = text;
  el.style.color = color;
}

// -------------------------------------------------------------
// INTEL FEED LIST (mPING, SPOTTERS, NWS WARNINGS & WATCHES)
// -------------------------------------------------------------
function renderIntelFeed(alerts, reports, regWarnings = []) {
  const container = document.getElementById("intel-feed-list");
  const tab = state.activeFeedTab;
  let items = [];

  // 1. Direct Point Alerts
  if (tab === "all" || tab === "alerts") {
    alerts.forEach((a) => {
      items.push({
        type: "alert",
        source: "NWS Point Bulletin",
        time: a.effective ? new Date(a.effective).toLocaleTimeString() : "Active",
        title: a.event,
        tag: a.hail_size_in ? `${a.hail_size_in.toFixed(2)}" Hail` : "Warning",
        remark: a.headline || a.description.slice(0, 140) + "..."
      });
    });
  }

  // 2. Regional Warnings & Statements
  if (tab === "all" || tab === "alerts" || tab === "watches") {
    regWarnings.forEach((w) => {
      const isWatch = w.category === "WATCH";
      if (tab === "watches" && !isWatch) return;
      if (tab === "alerts" && isWatch) return;

      const distStr = w.distance_miles !== null ? `${w.distance_miles} mi away` : "In zone";
      items.push({
        type: isWatch ? "watch" : "alert",
        source: w.wfo || "NWS Office",
        time: distStr,
        title: w.event,
        tag: w.hail_label || (w.hail_size_in ? `${w.hail_size_in.toFixed(2)}"` : "Active"),
        remark: w.headline || w.description.slice(0, 140) + "..."
      });
    });
  }

  // 3. Ground Reports (mPING + Spotters)
  if (tab === "all" || tab === "mping" || tab === "spotter") {
    reports.forEach((r) => {
      if (tab === "mping" && !r.is_mping) return;
      if (tab === "spotter" && r.is_mping) return;

      items.push({
        type: r.is_mping ? "mping" : "spotter",
        source: r.source_type,
        time: r.age_hours < 24 ? `${r.age_hours}h ago` : r.valid,
        title: `${r.hail_description} (${r.distance_miles} mi away)`,
        tag: `${r.hail_size_in.toFixed(2)}"`,
        remark: r.remark || `Reported in ${r.city || 'local area'}, ${r.state}`
      });
    });
  }

  const feedCount = document.getElementById("feed-count");
  if (feedCount) feedCount.textContent = items.length;

  if (items.length === 0) {
    container.innerHTML = `
      <div class="empty-feed">
        <i class="fa-solid fa-check-double text-green"></i>
        <p>No reports matching this category within perimeter.</p>
      </div>
    `;
    return;
  }

  container.innerHTML = "";
  items.forEach((item) => {
    const el = document.createElement("div");
    el.className = "feed-item";
    el.innerHTML = `
      <div class="feed-head">
        <span class="feed-source ${item.type === 'alert' ? 'nws' : (item.type === 'watch' ? 'watch' : '')}">${escapeHtml(item.source)}</span>
        <span class="feed-time">${escapeHtml(item.time)}</span>
      </div>
      <div class="feed-body">
        <span>${escapeHtml(item.title)}</span>
        <span class="feed-hail-tag">${escapeHtml(item.tag)}</span>
      </div>
      <div class="feed-remark">"${escapeHtml(item.remark)}"</div>
    `;
    container.appendChild(el);
  });
}

// -------------------------------------------------------------
// THREAT INTELLIGENCE BRIEFING GENERATION
// -------------------------------------------------------------
function generateBriefingText() {
  const d = state.assessmentData;
  if (!d) return "No active threat data loaded.";
  const a = d.assessment || {};
  const c = d.convective_data || {};
  const warnings = d.regional_warnings?.warnings || [];
  const reports = d.lsr_reports || [];

  return `================================================================================
HAILWARN SEVERE WEATHER & HAIL THREAT OPERATIONS BRIEFING
Generated: ${new Date().toUTCString()}
Target Location: ${state.currentPlaceName} (${state.currentLat.toFixed(4)}, ${state.currentLon.toFixed(4)})
Primary Radar:   ${state.activeStation ? `${state.activeStation.icao} (${state.activeStation.name}, ${state.activeStation.state} - ${state.activeStation.distance_miles !== undefined ? state.activeStation.distance_miles + ' mi' : 'Active'})` : 'National Composite Mosaic'}
Search Perimeter: ${state.radiusMiles} Miles (Reports Horizon: Past ${state.filterHours >= 24 ? (state.filterHours/24).toFixed(0) + ' Days' : state.filterHours + 'h'})
================================================================================

1. EXECUTIVE THREAT ASSESSMENT
--------------------------------------------------------------------------------
Overall Threat Level: [ ${a.level || 'NONE'} ]
Hail Threat Score:    ${a.score || 0}%
Max Projected Hail:   ${a.max_hail_label || 'None'} (${(a.max_hail_inches || 0).toFixed(2)} inches)
Estimated Radar Core: ${a.estimated_dbz || '< 30 dBZ'}
Hail Potential (HPI): ${c.hail_potential_index || 0} / 100

Protective Directive:
${a.action_recommendation || 'No active severe threat.'}

Key Contributing Factors:
${(a.reasons || []).map(r => ` - ${r}`).join('\n')}

2. ACTIVE NWS WARNINGS & STATEMENTS (${warnings.length} in monitoring region)
--------------------------------------------------------------------------------
${warnings.length === 0 ? 'No active NWS warnings within the regional monitoring perimeter.' : warnings.slice(0, 6).map(w => `* ${w.event} (${w.wfo || 'NWS'})
  Distance: ${w.distance_miles !== null ? w.distance_miles + ' mi away' : 'In zone'}
  Hail Tag: ${w.hail_label || 'N/A'} | Wind Tag: ${w.wind_gust || 'N/A'}
  ${w.motion ? `Storm Motion: Heading ${w.motion.heading_deg}° at ${w.motion.speed_mph} mph${w.motion.eta_mins ? ` | Incoming ETA ~${w.motion.eta_mins} mins` : ''}` : ''}
  Instruction: ${w.instruction ? w.instruction.slice(0, 160) + '...' : 'Take protective shelter.'}`).join('\n\n')}

3. ATMOSPHERIC CONVECTIVE THERMODYNAMICS (Open-Meteo)
--------------------------------------------------------------------------------
Surface Temperature:     ${c.temperature_f ? c.temperature_f + '°F' : 'N/A'} (${c.temperature_c}°C)
Current / Peak CAPE:     ${c.cape_j_kg || 0} J/kg  /  ${c.peak_cape_today || 0} J/kg
Lifted Index (LI):       ${c.lifted_index || 0} (Min: ${c.min_lifted_index_today || 0})
Convective Inhibition:   ${c.cin_j_kg || 0} J/kg [${c.cin_cap_status || 'N/A'}]
Freezing Level (AGL):    ${c.freezing_level_ft ? c.freezing_level_ft.toLocaleString() + ' ft' : 'N/A'} [${c.hail_survival_factor || 'N/A'}]
Surface Barometer:       ${c.surface_pressure_inhg || 29.92} inHg (${c.surface_pressure_hpa || 1013.2} hPa)
Wind & Peak Gusts:       ${c.wind_speed_mph || 0} mph (Gusts to ${c.wind_gusts_mph || 0} mph)

4. VERIFIED GROUND OBSERVATIONS (Past ${state.filterHours >= 24 ? (state.filterHours/24).toFixed(0) + ' Days' : state.filterHours + ' Hours'})
--------------------------------------------------------------------------------
Total Hail Reports:      ${reports.length}
${reports.length === 0 ? 'Zero confirmed ground reports in selected perimeter and time window.' : reports.slice(0, 8).map(r => `* ${r.hail_description} (${r.source_type})
  Distance: ${r.distance_miles} mi | Observed: ${r.valid || r.age_hours + 'h ago'}
  Location: ${r.city ? r.city + ', ' : ''}${r.county || ''} ${r.state || ''}
  ${r.remark ? `Remark: "${r.remark}"` : ''}`).join('\n')}

================================================================================
Report generated by HailWarn Operations System (100% Free Open Data Feeds)
================================================================================`;
}

// -------------------------------------------------------------
// EVENT LISTENERS & UI CONTROLS
// -------------------------------------------------------------
function initEventListeners() {
  // Search Box
  const searchInput = document.getElementById("location-input");
  searchInput.addEventListener("input", handleSearchInput);
  searchInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      const first = document.querySelector(".search-dropdown-item");
      if (first) first.click();
    }
  });

  document.getElementById("clear-search-btn").addEventListener("click", () => {
    searchInput.value = "";
    document.getElementById("clear-search-btn").style.display = "none";
    document.getElementById("search-dropdown").style.display = "none";
  });

  // GPS Button
  document.getElementById("geo-btn").addEventListener("click", () => detectUserLocation(true));

  // Refresh Button
  document.getElementById("refresh-btn").addEventListener("click", refreshAssessment);

  // Basemap Selector
  const basemapSelect = document.getElementById("basemap-select");
  if (basemapSelect) {
    basemapSelect.addEventListener("change", (e) => switchBasemap(e.target.value));
  }

  // Radar Source Selector
  const radarSourceSelect = document.getElementById("radar-source-select");
  if (radarSourceSelect) {
    radarSourceSelect.addEventListener("change", (e) => switchRadarSource(e.target.value));
  }

  // Localized Radar Station Selector (WSR-88D Single-Site)
  const stationSelect = document.getElementById("radar-station-select");
  if (stationSelect) {
    stationSelect.addEventListener("change", (e) => {
      const val = e.target.value;
      state.selectedStationId = val;
      if (val === "auto") {
        updateNearestStation();
        if (state.activeStation) {
          map.flyTo([state.activeStation.lat, state.activeStation.lon], 9, { duration: 1.2 });
        }
      } else {
        const found = state.allStations.find((s) => s.id.toUpperCase() === val.toUpperCase() || s.icao.toUpperCase() === val.toUpperCase());
        if (found) {
          const d = haversineMiles(state.currentLat, state.currentLon, found.lat, found.lon);
          state.activeStation = Object.assign({}, found, { distance_miles: Math.round(d * 10) / 10 });
          map.flyTo([found.lat, found.lon], 9, { duration: 1.2 });
        }
      }
      renderLocalizedRadarStation();
      state.radarSource = "single_site";
      const srcSelect = document.getElementById("radar-source-select");
      if (srcSelect) srcSelect.value = "single_site";
      switchRadarSource("single_site");
    });
  }

  // Radar Sweep Beam Playback Button
  const sweepBtn = document.getElementById("radar-sweep-btn");
  if (sweepBtn) {
    sweepBtn.addEventListener("click", () => toggleRadarSweep());
  }

  // Radar Convective Color Palette Selector
  const radarPaletteSelect = document.getElementById("radar-palette-select");
  if (radarPaletteSelect) {
    radarPaletteSelect.addEventListener("change", (e) => {
      state.radarPalette = parseInt(e.target.value, 10);
      if (state.radarSource === "rainviewer") {
        showRadarFrame(state.radarIndex);
      }
    });
  }

  // Radar Pixel Smoothing / Crisp Bins Selector
  const radarSmoothSelect = document.getElementById("radar-smooth-select");
  if (radarSmoothSelect) {
    radarSmoothSelect.addEventListener("change", (e) => {
      state.radarSmooth = e.target.value === "crisp" ? "0_1" : "1_1";
      if (state.radarSource === "rainviewer") {
        showRadarFrame(state.radarIndex);
      }
    });
  }

  // Filter Selects (Radius & Hours)
  const filterRadius = document.getElementById("filter-radius");
  if (filterRadius) {
    filterRadius.addEventListener("change", (e) => {
      state.radiusMiles = parseInt(e.target.value, 10);
      refreshAssessment();
    });
  }

  const filterHours = document.getElementById("filter-hours");
  if (filterHours) {
    filterHours.addEventListener("change", (e) => {
      state.filterHours = parseInt(e.target.value, 10);
      refreshAssessment();
    });
  }

  // Hotspots Dropdown Toggle
  const hotspotsBtn = document.getElementById("hotspots-btn");
  const hotspotsMenu = document.getElementById("hotspots-menu");
  hotspotsBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    hotspotsMenu.style.display = hotspotsMenu.style.display === "block" ? "none" : "block";
  });
  document.addEventListener("click", () => {
    hotspotsMenu.style.display = "none";
    document.getElementById("search-dropdown").style.display = "none";
  });

  // Voice Toggle Button
  const voiceBtn = document.getElementById("voice-btn");
  if (voiceBtn) {
    voiceBtn.addEventListener("click", () => {
      state.voiceEnabled = !state.voiceEnabled;
      voiceBtn.classList.toggle("active", state.voiceEnabled);
      voiceBtn.innerHTML = state.voiceEnabled
        ? `<i class="fa-solid fa-comment-dots text-purple"></i> <span>Voice: ON</span>`
        : `<i class="fa-solid fa-comment-dots"></i> <span>Voice: OFF</span>`;
      if (state.voiceEnabled) {
        speakAlert(`Voice alerts active for ${state.currentPlaceName}.`);
      }
    });
  }

  // Audio Siren Controls
  const sirenToggleBtn = document.getElementById("alarm-toggle-btn");
  sirenToggleBtn.addEventListener("click", () => {
    state.sirenEnabled = !state.sirenEnabled;
    sirenToggleBtn.classList.toggle("active", state.sirenEnabled);
    sirenToggleBtn.innerHTML = state.sirenEnabled
      ? `<i class="fa-solid fa-volume-high"></i> <span>Siren: ON</span>`
      : `<i class="fa-solid fa-volume-xmark"></i> <span>Siren: OFF</span>`;
  });

  document.getElementById("test-siren-btn").addEventListener("click", () => {
    playAttentionChime();
    setTimeout(() => playWarningSiren(2.0), 700);
  });

  // Radar Animation Controls
  document.getElementById("radar-play-btn").addEventListener("click", playRadarLoop);
  document.getElementById("radar-prev-btn").addEventListener("click", () => {
    if (state.radarFrames.length === 0) return;
    state.radarIndex = (state.radarIndex - 1 + state.radarFrames.length) % state.radarFrames.length;
    showRadarFrame(state.radarIndex);
  });
  document.getElementById("radar-next-btn").addEventListener("click", () => {
    if (state.radarFrames.length === 0) return;
    state.radarIndex = (state.radarIndex + 1) % state.radarFrames.length;
    showRadarFrame(state.radarIndex);
  });
  document.getElementById("radar-scrubber").addEventListener("input", (e) => {
    state.radarIndex = parseInt(e.target.value, 10);
    showRadarFrame(state.radarIndex);
  });

  // Radar Opacity Slider
  const opacitySlider = document.getElementById("radar-opacity");
  opacitySlider.addEventListener("input", (e) => {
    state.radarOpacity = parseFloat(e.target.value) / 100;
    document.getElementById("opacity-label").textContent = `${e.target.value}%`;
    if (rainviewerRadarLayer) rainviewerRadarLayer.setOpacity(state.radarOpacity);
    if (iemNexradLayer) iemNexradLayer.setOpacity(state.radarOpacity);
    if (mrmsRadarLayer) mrmsRadarLayer.setOpacity(state.radarOpacity);
    if (singleSiteRadarLayer) singleSiteRadarLayer.setOpacity(state.radarOpacity);
  });

  // Modern Warning & Advisory Layer Toggles
  const reRenderLayers = () => {
    if (state.assessmentData) renderWarningOverlays(state.assessmentData);
  };

  document.getElementById("toggle-polygons")?.addEventListener("change", reRenderLayers);
  document.getElementById("toggle-floods")?.addEventListener("change", reRenderLayers);
  document.getElementById("toggle-advisories")?.addEventListener("change", reRenderLayers);
  document.getElementById("toggle-watches")?.addEventListener("change", reRenderLayers);
  document.getElementById("toggle-tracks")?.addEventListener("change", reRenderLayers);
  document.getElementById("toggle-spc")?.addEventListener("change", reRenderLayers);

  // City Weather Card Toggle & Close button
  const toggleWeather = document.getElementById("toggle-city-weather");
  const weatherCard = document.getElementById("city-weather-card");
  const closeWeatherBtn = document.getElementById("close-weather-card-btn");

  if (toggleWeather && weatherCard) {
    toggleWeather.addEventListener("change", (e) => {
      state.cityWeatherVisible = e.target.checked;
      weatherCard.style.display = e.target.checked ? "flex" : "none";
    });
  }

  if (closeWeatherBtn && weatherCard && toggleWeather) {
    closeWeatherBtn.addEventListener("click", () => {
      state.cityWeatherVisible = false;
      weatherCard.style.display = "none";
      toggleWeather.checked = false;
    });
  }

  document.getElementById("toggle-reports")?.addEventListener("change", () => {
    if (state.assessmentData) renderGroundReports(state.assessmentData.lsr_reports || []);
  });
  document.getElementById("toggle-rings")?.addEventListener("change", updateUserMarkerAndRings);
  document.getElementById("toggle-sweep")?.addEventListener("change", (e) => {
    toggleRadarSweep(e.target.checked);
  });
  document.getElementById("toggle-radar-site")?.addEventListener("change", (e) => {
    state.showStationRings = e.target.checked;
    renderLocalizedRadarStation();
  });

  // Interactive Simulator Slider
  const simSlider = document.getElementById("sim-hail-slider");
  const simVal = document.getElementById("sim-slider-val");
  const simResetBtn = document.getElementById("sim-reset-btn");

  if (simSlider) {
    simSlider.addEventListener("input", (e) => {
      const val = parseFloat(e.target.value);
      state.simulatedHail = val;
      if (simVal) simVal.textContent = `${val.toFixed(2)}" Simulated`;
      updateDamageSimulator(val);
    });
  }

  if (simResetBtn) {
    simResetBtn.addEventListener("click", () => {
      state.simulatedHail = null;
      const detected = state.assessmentData?.assessment?.max_hail_inches || 0.0;
      if (simSlider) simSlider.value = detected;
      if (simVal) simVal.textContent = "Live Detection";
      updateDamageSimulator(detected);
    });
  }

  // Threat Briefing Modal Controls (FEAT-03: Export Engine)
  const briefingBtn = document.getElementById("briefing-btn");
  const briefingModal = document.getElementById("briefing-modal");
  const closeBriefingBtn = document.getElementById("close-briefing-btn");
  const briefingContent = document.getElementById("briefing-content");

  if (briefingBtn && briefingModal) {
    briefingBtn.addEventListener("click", async () => {
      briefingModal.style.display = "flex";
      try {
        const res = await fetch(`/api/export/threat-dossier?lat=${state.currentLat}&lon=${state.currentLon}&radius=${state.radiusMiles}&format=text`);
        if (res.ok) {
          const text = await res.text();
          if (briefingContent) briefingContent.value = text;
        } else if (briefingContent) {
          briefingContent.value = generateBriefingText();
        }
      } catch (e) {
        if (briefingContent) briefingContent.value = generateBriefingText();
      }
    });
  }

  if (closeBriefingBtn && briefingModal) {
    closeBriefingBtn.addEventListener("click", () => {
      briefingModal.style.display = "none";
    });
  }

  document.getElementById("copy-briefing-btn")?.addEventListener("click", async () => {
    const btn = document.getElementById("copy-briefing-btn");
    try {
      const res = await fetch(`/api/export/threat-dossier?lat=${state.currentLat}&lon=${state.currentLon}&radius=${state.radiusMiles}&format=text`);
      const text = res.ok ? await res.text() : generateBriefingText();
      if (briefingContent) briefingContent.value = text;
      await navigator.clipboard.writeText(text);
      if (btn) {
        btn.innerHTML = `<i class="fa-solid fa-check"></i> Copied Brief!`;
        setTimeout(() => { btn.innerHTML = `<i class="fa-solid fa-copy"></i> Copy Operations Brief`; }, 2000);
      }
    } catch (e) {
      const text = generateBriefingText();
      navigator.clipboard.writeText(text);
    }
  });

  document.getElementById("download-briefing-btn")?.addEventListener("click", () => {
    window.location.href = `/api/export/threat-dossier?lat=${state.currentLat}&lon=${state.currentLon}&radius=${state.radiusMiles}&format=text`;
  });

  document.getElementById("download-csv-btn")?.addEventListener("click", () => {
    window.location.href = `/api/export/threat-dossier?lat=${state.currentLat}&lon=${state.currentLon}&radius=${state.radiusMiles}&format=csv`;
  });

  document.getElementById("json-briefing-btn")?.addEventListener("click", async () => {
    const btn = document.getElementById("json-briefing-btn");
    try {
      const res = await fetch(`/api/export/threat-dossier?lat=${state.currentLat}&lon=${state.currentLon}&radius=${state.radiusMiles}&format=json`);
      let jsonStr;
      if (res.ok) {
        const json = await res.json();
        jsonStr = JSON.stringify(json, null, 2);
      } else {
        jsonStr = JSON.stringify(state.assessmentData || {}, null, 2);
      }
      if (briefingContent) briefingContent.value = jsonStr;
      await navigator.clipboard.writeText(jsonStr);
      if (btn) {
        btn.innerHTML = `<i class="fa-solid fa-check"></i> JSON Copied!`;
        setTimeout(() => { btn.innerHTML = `<i class="fa-solid fa-code"></i> Copy JSON`; }, 2000);
      }
    } catch (e) {
      const jsonStr = JSON.stringify(state.assessmentData || {}, null, 2);
      navigator.clipboard.writeText(jsonStr);
    }
  });

  // Threshold Settings Modal Controls (FEAT-01)
  const thresholdBtn = document.getElementById("threshold-cfg-btn");
  const thresholdModal = document.getElementById("threshold-modal");
  const closeThresholdBtn = document.getElementById("close-threshold-btn");
  const saveThresholdBtn = document.getElementById("save-threshold-btn");
  const resetThresholdBtn = document.getElementById("reset-threshold-btn");

  function updateThresholdPreview() {
    const minHail = parseFloat(document.getElementById("cfg-min-hail")?.value || "1.00");
    const minScore = parseInt(document.getElementById("cfg-min-score")?.value || "70", 10);
    const maxEta = parseInt(document.getElementById("cfg-max-eta")?.value || "45", 10);
    const previewEl = document.getElementById("threshold-preview-text");
    if (previewEl) {
      previewEl.textContent = `Siren and voice alerts will trigger only when hail size is ≥ ${minHail.toFixed(2)}" OR Threat Score is ≥ ${minScore}%, with an approaching storm ETA ≤ ${maxEta} mins.`;
    }
  }

  ["cfg-min-hail", "cfg-min-score", "cfg-max-eta"].forEach(id => {
    document.getElementById(id)?.addEventListener("change", updateThresholdPreview);
  });

  if (thresholdBtn && thresholdModal) {
    thresholdBtn.addEventListener("click", () => {
      const hSelect = document.getElementById("cfg-min-hail");
      const sSelect = document.getElementById("cfg-min-score");
      const eSelect = document.getElementById("cfg-max-eta");
      if (hSelect) hSelect.value = state.thresholdConfig.minHail.toFixed(2);
      if (sSelect) sSelect.value = String(state.thresholdConfig.minScore);
      if (eSelect) eSelect.value = String(state.thresholdConfig.maxEta);
      updateThresholdPreview();
      thresholdModal.style.display = "flex";
    });
  }

  if (closeThresholdBtn && thresholdModal) {
    closeThresholdBtn.addEventListener("click", () => {
      thresholdModal.style.display = "none";
    });
  }

  if (saveThresholdBtn && thresholdModal) {
    saveThresholdBtn.addEventListener("click", () => {
      const minHail = parseFloat(document.getElementById("cfg-min-hail")?.value || "1.00");
      const minScore = parseInt(document.getElementById("cfg-min-score")?.value || "70", 10);
      const maxEta = parseInt(document.getElementById("cfg-max-eta")?.value || "45", 10);
      state.thresholdConfig.minHail = minHail;
      state.thresholdConfig.minScore = minScore;
      state.thresholdConfig.maxEta = maxEta;
      updateActiveRuleBadge();
      thresholdModal.style.display = "none";
      refreshAssessment();
    });
  }

  if (resetThresholdBtn) {
    resetThresholdBtn.addEventListener("click", () => {
      state.thresholdConfig.minHail = 1.00;
      state.thresholdConfig.minScore = 70;
      state.thresholdConfig.maxEta = 45;
      const hSelect = document.getElementById("cfg-min-hail");
      const sSelect = document.getElementById("cfg-min-score");
      const eSelect = document.getElementById("cfg-max-eta");
      if (hSelect) hSelect.value = "1.00";
      if (sSelect) sSelect.value = "70";
      if (eSelect) eSelect.value = "45";
      updateThresholdPreview();
      updateActiveRuleBadge();
    });
  }

  // Corridor Scanner Drawer & Preset Controls (FEAT-02)
  const bboxModeBtn = document.getElementById("btn-bbox-mode");
  const corridorDrawer = document.getElementById("corridor-drawer");
  const closeCorridorBtn = document.getElementById("close-corridor-drawer-btn");

  if (bboxModeBtn && corridorDrawer) {
    bboxModeBtn.addEventListener("click", () => {
      state.corridorMode = !state.corridorMode;
      if (state.corridorMode) {
        bboxModeBtn.classList.add("active");
        corridorDrawer.style.display = "flex";
        if (!state.corridorData) {
          loadCorridorBbox(32.5, -97.6, 35.6, -96.6);
        }
      } else {
        bboxModeBtn.classList.remove("active");
        corridorDrawer.style.display = "none";
        if (state.corridorLayer && map) {
          map.removeLayer(state.corridorLayer);
          state.corridorLayer = null;
        }
      }
    });
  }

  if (closeCorridorBtn && corridorDrawer) {
    closeCorridorBtn.addEventListener("click", () => {
      state.corridorMode = false;
      bboxModeBtn?.classList.remove("active");
      corridorDrawer.style.display = "none";
      if (state.corridorLayer && map) {
        map.removeLayer(state.corridorLayer);
        state.corridorLayer = null;
      }
    });
  }

  document.querySelectorAll(".preset-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const minLat = parseFloat(btn.getAttribute("data-minlat"));
      const minLon = parseFloat(btn.getAttribute("data-minlon"));
      const maxLat = parseFloat(btn.getAttribute("data-maxlat"));
      const maxLon = parseFloat(btn.getAttribute("data-maxlon"));
      if (!isNaN(minLat) && !isNaN(minLon) && !isNaN(maxLat) && !isNaN(maxLon)) {
        loadCorridorBbox(minLat, minLon, maxLat, maxLon);
      }
    });
  });

  // Feed Tabs
  document.querySelectorAll(".feed-tab").forEach((tabBtn) => {
    tabBtn.addEventListener("click", () => {
      document.querySelectorAll(".feed-tab").forEach(b => b.classList.remove("active"));
      tabBtn.classList.add("active");
      state.activeFeedTab = tabBtn.getAttribute("data-tab");
      if (state.assessmentData) {
        renderIntelFeed(
          state.assessmentData.nws_alerts || [],
          state.assessmentData.lsr_reports || [],
          state.assessmentData.regional_warnings?.warnings || []
        );
      }
    });
  });
}

// -------------------------------------------------------------
// TIMER TICK
// -------------------------------------------------------------
function handleTick() {
  state.secondsUntilRefresh--;
  if (state.secondsUntilRefresh <= 0) {
    state.secondsUntilRefresh = state.autoRefreshInterval;
    refreshAssessment();
  }
  const el = document.getElementById("countdown-val");
  if (el) el.textContent = `${state.secondsUntilRefresh}s`;
}

// -------------------------------------------------------------
// FEAT-01: ACTIVE RULE BADGE UPDATE
// -------------------------------------------------------------
function updateActiveRuleBadge() {
  const badge = document.getElementById("active-rule-badge");
  if (badge && state.thresholdConfig) {
    badge.textContent = `HAIL ≥ ${state.thresholdConfig.minHail.toFixed(2)}" | SCORE ≥ ${state.thresholdConfig.minScore}% | ETA ≤ ${state.thresholdConfig.maxEta}m`;
  }
}

// -------------------------------------------------------------
// FEAT-02: CORRIDOR SCANNER DATA LOADER
// -------------------------------------------------------------
async function loadCorridorBbox(minLat, minLon, maxLat, maxLon) {
  const boundsText = document.getElementById("corridor-bounds-text");
  const badge = document.getElementById("corridor-status-badge");
  if (badge) badge.textContent = "SCANNING...";
  if (boundsText) boundsText.textContent = `Scanning corridor bounds: [${minLat.toFixed(2)}, ${minLon.toFixed(2)}] to [${maxLat.toFixed(2)}, ${maxLon.toFixed(2)}]...`;

  try {
    const res = await fetch(`/api/hail/bbox?min_lat=${minLat}&min_lon=${minLon}&max_lat=${maxLat}&max_lon=${maxLon}&min_hail=0.0&hours=24`);
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    state.corridorData = data;

    // Update drawer metrics
    const metrics = data.corridor_metrics || {};
    const scoreVal = document.getElementById("corridor-score-val");
    if (scoreVal) scoreVal.textContent = `${metrics.composite_corridor_score ?? 0}%`;

    const sevVal = document.getElementById("corridor-severity-val");
    if (sevVal) {
      sevVal.textContent = escapeHtml(metrics.highest_severity || "NONE");
      if (metrics.highest_severity === "TORNADO" || metrics.highest_severity === "DESTRUCTIVE_HAIL") {
        sevVal.className = "cm-val text-red";
      } else if (metrics.highest_severity === "SEVERE_TSTORM") {
        sevVal.className = "cm-val text-amber";
      } else {
        sevVal.className = "cm-val";
      }
    }

    const hailVal = document.getElementById("corridor-maxhail-val");
    if (hailVal) hailVal.textContent = `${metrics.max_hail_inches ? metrics.max_hail_inches.toFixed(2) + '"' : '--'}`;

    const warnVal = document.getElementById("corridor-warnings-val");
    if (warnVal) warnVal.textContent = String(metrics.total_warnings || 0);

    const repVal = document.getElementById("corridor-reports-val");
    if (repVal) repVal.textContent = String(metrics.total_hail_reports || 0);

    if (badge) {
      badge.textContent = (metrics.composite_corridor_score >= 70) ? "HIGH DANGER" : ((metrics.composite_corridor_score >= 40) ? "ELEVATED" : "LOW RISK");
    }
    if (boundsText) {
      boundsText.textContent = `Corridor bounds: ${minLat.toFixed(2)}°N to ${maxLat.toFixed(2)}°N, ${Math.abs(minLon).toFixed(2)}°W to ${Math.abs(maxLon).toFixed(2)}°W | Max hail: ${escapeHtml(metrics.max_hail_label || 'None')}`;
    }

    // Render / update corridor bounding box polygon on map
    if (state.corridorLayer && map) {
      map.removeLayer(state.corridorLayer);
      state.corridorLayer = null;
    }
    if (map) {
      const bounds = [[minLat, minLon], [maxLat, maxLon]];
      state.corridorLayer = L.rectangle(bounds, {
        color: "#06b6d4",
        weight: 2.5,
        dashArray: "6, 6",
        fillColor: "#06b6d4",
        fillOpacity: 0.12
      }).addTo(map);
      state.corridorLayer.bindTooltip(`Corridor: ${escapeHtml(metrics.highest_severity || 'Active Scan')} (Danger: ${metrics.composite_corridor_score ?? 0}%)`);
      map.fitBounds(bounds, { padding: [40, 40] });
    }
  } catch (err) {
    console.error("Corridor fetch error:", err);
    if (boundsText) boundsText.textContent = "Error scanning corridor: " + escapeHtml(err.message);
  }
}

