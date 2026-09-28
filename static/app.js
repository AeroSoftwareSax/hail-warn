/**
 * HAILWARN - Severe Hail Detection & Threat Warning Operations Client
 * Sensor fusion interface combining NWS alerts, mPING reports, NEXRAD dBZ, and Open-Meteo.
 */

// Application State
const state = {
  currentLat: 32.7767, // Default: Dallas, TX
  currentLon: -96.7970,
  currentPlaceName: "Dallas, TX",
  radiusMiles: 45,
  threatScore: 0,
  threatLevel: "NONE",
  sirenEnabled: true,
  autoRefreshInterval: 60,
  secondsUntilRefresh: 60,
  radarFrames: [],
  radarIndex: 0,
  radarPlaying: false,
  radarTimer: null,
  radarOpacity: 0.75,
  radarLayers: {}, // Map timestamp -> L.TileLayer
  activeFeedTab: "all",
  assessmentData: null
};

// Global Map and Layer Groups
let map = null;
let userMarker = null;
let threatRingsGroup = null;
let warningPolygonsGroup = null;
let groundReportsGroup = null;
let rainviewerRadarLayer = null;
let iemNexradLayer = null;

// Web Audio API Context for Alert Siren
let audioCtx = null;
let sirenPlaying = false;

// -------------------------------------------------------------
// INITIALIZATION
// -------------------------------------------------------------
document.addEventListener("DOMContentLoaded", () => {
  initMap();
  initEventListeners();
  loadLiveHotspots();
  
  // Try GPS location or default
  detectUserLocation(false);
  
  // Start 1-second interval for countdown timer
  setInterval(handleTick, 1000);
});

// -------------------------------------------------------------
// LEAFLET MAP SETUP
// -------------------------------------------------------------
function initMap() {
  map = L.map("radar-map", {
    center: [state.currentLat, state.currentLon],
    zoom: 9,
    minZoom: 3,
    maxZoom: 15,
    zoomControl: true
  });

  // Dark Tactical Base Cartography (CartoDB DarkMatter)
  L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
    attribution: '&copy; <a href="https://openstreetmap.org">OSM</a> &copy; <a href="https://carto.com/">CARTO</a>',
    subdomains: "abcd",
    maxZoom: 19
  }).addTo(map);

  // Layer Groups
  threatRingsGroup = L.layerGroup().addTo(map);
  warningPolygonsGroup = L.layerGroup().addTo(map);
  groundReportsGroup = L.layerGroup().addTo(map);

  // High-Resolution IEM NEXRAD Composite Layer (Optional toggle)
  iemNexradLayer = L.tileLayer("https://mesonet.agron.iastate.edu/cache/tile.py/1.0.0/nexrad-n0q-900913/{z}/{x}/{y}.png", {
    opacity: 0.75,
    zIndex: 10
  });

  // Map Click to Inspect Threat
  map.on("click", (e) => {
    state.currentLat = e.latlng.lat;
    state.currentLon = e.latlng.lng;
    reverseGeocode(state.currentLat, state.currentLon);
    refreshAssessment();
  });
}

// -------------------------------------------------------------
// WEB AUDIO API SIREN / ALARM SYNTHESIZER
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
    
    // Pleasant dual chime
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
      console.warn("GPS Location access denied or unavailable:", err.message);
      btn.innerHTML = `<i class="fa-solid fa-location-crosshairs"></i> <span>My Location</span>`;
      if (notifyIfDenied) {
        alert("Could not access GPS location. Falling back to default region.");
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
    state.currentPlaceName = data.display_name ? data.display_name.split(",").slice(0, 3).join(",") : `${lat.toFixed(3)}, ${lon.toFixed(3)}`;
    updateLocationDisplay();
  } catch (e) {
    state.currentPlaceName = `${lat.toFixed(3)}, ${lon.toFixed(3)}`;
    updateLocationDisplay();
  }
}

function updateLocationDisplay() {
  document.getElementById("loc-display-name").textContent = state.currentPlaceName;
  document.getElementById("loc-coords").textContent = `(${state.currentLat.toFixed(3)}°, ${state.currentLon.toFixed(3)}°)`;
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

  if (!results || results.length === 0) {
    dropdown.innerHTML = `<div class="search-dropdown-item text-dim">No locations found</div>`;
    dropdown.style.display = "block";
    return;
  }

  results.forEach((item) => {
    const el = document.createElement("div");
    el.className = "search-dropdown-item";
    el.innerHTML = `<i class="fa-solid fa-location-dot text-cyan"></i> <span>${item.display_name}</span>`;
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
          <div class="hotspot-area">${h.area}</div>
          <div class="hotspot-event">${h.event}</div>
        </div>
        <div class="hotspot-tag">${h.hail_label || "Hail Risk"}</div>
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
    const url = `/api/assess?lat=${state.currentLat}&lon=${state.currentLon}&radius=${state.radiusMiles}`;
    const res = await fetch(url);
    const data = await res.json();
    state.assessmentData = data;

    renderThreatAssessment(data);
    renderWarningPolygons(data.nws_alerts || []);
    renderGroundReports(data.lsr_reports || []);
    await loadRainViewerRadar();

    // Check if new level is high threat and siren is enabled
    const level = data.assessment.level;
    if ((level === "WARNING" || level === "EMERGENCY") && level !== state.threatLevel) {
      playWarningSiren(4.5);
    }
    state.threatLevel = level;

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

  // User icon
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

  if (document.getElementById("toggle-rings").checked) {
    // 5-Mile Immediate Danger Ring (Red)
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
// MAP LAYERS: NWS WARNING POLYGONS
// -------------------------------------------------------------
function renderWarningPolygons(alerts) {
  warningPolygonsGroup.clearLayers();
  if (!document.getElementById("toggle-polygons").checked) return;

  alerts.forEach((a) => {
    if (!a.geometry) return;

    const isSevereTstorm = a.event.includes("Severe Thunderstorm");
    const isTornado = a.event.includes("Tornado");
    const hailTag = a.hail_size_in ? `${a.hail_size_in.toFixed(2)}"` : "Hail Risk";

    let color = "#3b82f6";
    if (isTornado) color = "#ef4444";
    else if (isSevereTstorm) color = "#f59e0b";
    if (a.hail_size_in && a.hail_size_in >= 1.75) color = "#ec4899";

    const layer = L.geoJSON(a.geometry, {
      style: {
        color: color,
        weight: 2.5,
        opacity: 0.9,
        fillColor: color,
        fillOpacity: 0.22
      }
    });

    layer.bindPopup(`
      <div style="font-family: var(--font-sans); min-width: 220px;">
        <div style="font-weight: 800; color: ${color}; font-size: 0.9rem; margin-bottom: 4px;">
          <i class="fa-solid fa-triangle-exclamation"></i> ${a.event}
        </div>
        <div style="font-size: 0.78rem; font-family: var(--font-mono); margin-bottom: 6px;">
          <b>HAIL TAG:</b> <span style="color: var(--color-amber);">${hailTag} (${a.hail_threat_type})</span>
        </div>
        <div style="font-size: 0.72rem; color: #94a3b8; max-height: 120px; overflow-y: auto; line-height: 1.3;">
          ${a.headline || a.description.slice(0, 180) + '...'}
        </div>
      </div>
    `);

    warningPolygonsGroup.addLayer(layer);
  });
}

// -------------------------------------------------------------
// MAP LAYERS: mPING & LSR GROUND REPORTS
// -------------------------------------------------------------
function renderGroundReports(reports) {
  groundReportsGroup.clearLayers();
  if (!document.getElementById("toggle-reports").checked) return;

  reports.forEach((r) => {
    const size = r.hail_size_in || 0;
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
    marker.bindPopup(`
      <div style="font-family: var(--font-sans); min-width: 200px;">
        <div style="font-weight: 700; color: ${color}; font-size: 0.88rem; margin-bottom: 4px;">
          <i class="fa-solid fa-users"></i> ${r.source_type}
        </div>
        <div style="font-family: var(--font-mono); font-size: 0.8rem; margin-bottom: 4px;">
          <b>HAIL:</b> <span style="color: var(--color-amber);">${r.hail_description}</span>
        </div>
        <div style="font-size: 0.72rem; color: #94a3b8; margin-bottom: 2px;">
          <b>Distance:</b> ${r.distance_miles} miles away
        </div>
        <div style="font-size: 0.72rem; color: #94a3b8; margin-bottom: 6px;">
          <b>Observed:</b> ${r.age_hours < 24 ? r.age_hours + 'h ago' : r.valid}
        </div>
        ${r.remark ? `<div style="font-size: 0.7rem; color: #cbd5e1; font-style: italic; background: rgba(0,0,0,0.3); padding: 4px 6px; border-radius: 4px;">"${r.remark}"</div>` : ''}
      </div>
    `);

    groundReportsGroup.addLayer(marker);
  });
}

// -------------------------------------------------------------
// RAINVIEWER ANIMATED NEXRAD RADAR
// -------------------------------------------------------------
async function loadRainViewerRadar() {
  try {
    const res = await fetch("/api/radar");
    const data = await res.json();
    state.radarFrames = data.frames || [];

    const scrubber = document.getElementById("radar-scrubber");
    scrubber.max = Math.max(0, state.radarFrames.length - 1);

    if (state.radarFrames.length > 0) {
      state.radarIndex = state.radarFrames.length - 1;
      scrubber.value = state.radarIndex;
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
  // Color scheme 2 is NOAA NEXRAD reflectivity (dBZ) scale
  const tileUrl = `${baseHost}${frame.path}/512/{z}/{x}/{y}/2/1_1.png`;

  if (rainviewerRadarLayer) {
    map.removeLayer(rainviewerRadarLayer);
  }

  rainviewerRadarLayer = L.tileLayer(tileUrl, {
    opacity: state.radarOpacity,
    zIndex: 20
  }).addTo(map);

  // Update time indicator
  const timeDate = new Date(frame.time * 1000);
  const timeStr = timeDate.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  const typeBadge = frame.type === "nowcast" ? "NOWCAST PREDICTIVE" : "LIVE NEXRAD dBZ";
  
  document.getElementById("radar-time-label").textContent = `${timeStr} (${index + 1}/${state.radarFrames.length})`;
  document.getElementById("radar-type-label").textContent = typeBadge;
  document.getElementById("radar-scrubber").value = index;
}

function playRadarLoop() {
  if (state.radarPlaying) {
    clearInterval(state.radarTimer);
    state.radarPlaying = false;
    document.getElementById("play-btn-icon").className = "fa-solid fa-play";
  } else {
    state.radarPlaying = true;
    document.getElementById("play-btn-icon").className = "fa-solid fa-pause";
    state.radarTimer = setInterval(() => {
      state.radarIndex = (state.radarIndex + 1) % state.radarFrames.length;
      showRadarFrame(state.radarIndex);
    }, 700);
  }
}

// -------------------------------------------------------------
// RENDER THREAT ASSESSMENT & INTEL MATRICES
// -------------------------------------------------------------
function renderThreatAssessment(data) {
  const a = data.assessment;
  const convective = data.convective_data || {};
  const alerts = data.nws_alerts || [];
  const reports = data.lsr_reports || [];

  // Update Status Bar
  const badge = document.getElementById("threat-badge");
  badge.textContent = a.level;
  badge.className = `danger-badge ${a.badge_class}`;

  document.getElementById("threat-score").textContent = `${a.score}%`;
  document.getElementById("max-hail-val").textContent = a.max_hail_label;
  document.getElementById("dbz-val").textContent = a.estimated_dbz.split(" ")[0] + " dBZ";
  document.getElementById("intel-timestamp").textContent = `${new Date().toLocaleTimeString()} LOCAL`;

  // Directive Card
  document.getElementById("directive-text").textContent = a.action_recommendation;
  const reasonsContainer = document.getElementById("threat-reasons-container");
  reasonsContainer.innerHTML = "";

  a.reasons.forEach((r) => {
    const el = document.createElement("div");
    let cls = "reason-item";
    if (r.startsWith("CRITICAL") || r.startsWith("WARNING")) cls += " critical";
    else if (r.startsWith("GROUND TRUTH") || r.startsWith("ADVISORY")) cls += " warning";
    el.className = cls;
    el.textContent = r;
    reasonsContainer.appendChild(el);
  });

  // Matrix Cell 1: NWS
  const severeAlert = alerts.find(x => x.event.includes("Thunderstorm") || x.event.includes("Tornado") || x.event.includes("Special"));
  if (severeAlert) {
    document.getElementById("nws-status").textContent = "WARNING ACTIVE";
    document.getElementById("nws-status").style.color = "var(--color-red)";
    document.getElementById("nws-hail-tag").textContent = severeAlert.hail_description || `${severeAlert.hail_size_in || 1.0}" Hail`;
    document.getElementById("nws-threat-mode").textContent = severeAlert.hail_threat_type || "RADAR INDICATED";
    document.getElementById("nws-event-summary").textContent = severeAlert.headline || severeAlert.event;
  } else {
    document.getElementById("nws-status").textContent = "ALL CLEAR";
    document.getElementById("nws-status").style.color = "var(--color-green)";
    document.getElementById("nws-hail-tag").textContent = "None";
    document.getElementById("nws-threat-mode").textContent = "No Signatures";
    document.getElementById("nws-event-summary").textContent = "No active hail bulletins for point";
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
    document.getElementById("mping-summary").textContent = "No hail observations in 45-mi radius";
  }

  // Matrix Cell 3: Radar
  document.getElementById("radar-core-level").textContent = a.estimated_dbz.split(" ")[0];
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

  // Hail Diameter Disc Graphic Scaling
  renderHailDisc(a.max_hail_inches);

  // Render Feed List
  renderIntelFeed(alerts, reports);
}

// -------------------------------------------------------------
// HAIL DIAMETER DISC VISUALIZER
// -------------------------------------------------------------
function renderHailDisc(inches) {
  const disc = document.getElementById("hail-disc");
  const diamText = document.getElementById("hail-disc-diameter");
  const title = document.getElementById("hail-scale-title");
  const desc = document.getElementById("hail-scale-desc");
  const riskVehicle = document.getElementById("risk-vehicle");
  const riskRoof = document.getElementById("risk-roof");
  const riskHuman = document.getElementById("risk-human");

  if (!inches || inches <= 0) {
    disc.style.width = "48px";
    disc.style.height = "48px";
    disc.style.background = "radial-gradient(circle, #334155, #1e293b)";
    disc.style.borderColor = "#475569";
    diamText.textContent = "0\"";
    title.textContent = "No Active Hail Threat";
    desc.textContent = "Zero severe hail cores detected in your proximity.";
    setRiskStatus(riskVehicle, "SAFE", "var(--color-green)");
    setRiskStatus(riskRoof, "SAFE", "var(--color-green)");
    setRiskStatus(riskHuman, "SAFE", "var(--color-green)");
    return;
  }

  // Scale disc size proportionally (from 48px to 110px)
  const pixelSize = Math.min(110, Math.max(48, 40 + inches * 18));
  disc.style.width = `${pixelSize}px`;
  disc.style.height = `${pixelSize}px`;
  diamText.textContent = `${inches.toFixed(2)}"`;

  // Realistic ice texture lighting
  disc.style.background = "radial-gradient(circle at 35% 35%, #ffffff 0%, #cbd5e1 55%, #64748b 100%)";
  disc.style.borderColor = inches >= 1.75 ? "#ec4899" : (inches >= 1.0 ? "#f97316" : "#f59e0b");

  if (inches >= 2.50) {
    title.textContent = `Tennis / Baseball Size (${inches.toFixed(2)}")`;
    desc.textContent = "CATASTROPHIC IMPACT! Windshields shattered, severe roof punctures, serious human injury risk.";
    setRiskStatus(riskVehicle, "EXTREME", "var(--color-red)");
    setRiskStatus(riskRoof, "SEVERE", "var(--color-red)");
    setRiskStatus(riskHuman, "DANGER", "var(--color-red)");
  } else if (inches >= 1.75) {
    title.textContent = `Golf Ball Size (${inches.toFixed(2)}")`;
    desc.textContent = "HIGH DAMAGE: Dented vehicle hoods, cracked glass, structural roof shingle bruising.";
    setRiskStatus(riskVehicle, "HIGH", "var(--color-red)");
    setRiskStatus(riskRoof, "MODERATE", "var(--color-amber)");
    setRiskStatus(riskHuman, "WARNING", "var(--color-amber)");
  } else if (inches >= 1.00) {
    title.textContent = `Quarter Size (${inches.toFixed(2)}") - Severe`;
    desc.textContent = "NWS SEVERE CRITERIA: Cosmetic vehicle dings, foliage stripped, outdoor hazard.";
    setRiskStatus(riskVehicle, "ELEVATED", "var(--color-amber)");
    setRiskStatus(riskRoof, "MINOR", "var(--color-amber)");
    setRiskStatus(riskHuman, "ALERT", "var(--color-amber)");
  } else {
    title.textContent = `Pea / Marble Size (${inches.toFixed(2)}")`;
    desc.textContent = "Sub-severe graupel / hail pellets. Minimal property damage expected.";
    setRiskStatus(riskVehicle, "LOW", "var(--color-green)");
    setRiskStatus(riskRoof, "LOW", "var(--color-green)");
    setRiskStatus(riskHuman, "SAFE", "var(--color-green)");
  }
}

function setRiskStatus(el, text, color) {
  el.textContent = text;
  el.style.color = color;
}

// -------------------------------------------------------------
// INTEL FEED LIST (mPING, SPOTTERS & NWS)
// -------------------------------------------------------------
function renderIntelFeed(alerts, reports) {
  const container = document.getElementById("intel-feed-list");
  const tab = state.activeFeedTab;
  let items = [];

  if (tab === "all" || tab === "alerts") {
    alerts.forEach((a) => {
      items.push({
        type: "alert",
        source: "NWS Official Alert",
        time: a.effective ? new Date(a.effective).toLocaleTimeString() : "Active",
        title: a.event,
        tag: a.hail_size_in ? `${a.hail_size_in.toFixed(2)}" Hail` : "Warning",
        remark: a.headline || a.description.slice(0, 140) + "..."
      });
    });
  }

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

  document.getElementById("feed-count").textContent = items.length;

  if (items.length === 0) {
    container.innerHTML = `
      <div class="empty-feed">
        <i class="fa-solid fa-check-double text-green"></i>
        <p>No reports matching this category within 45 miles.</p>
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
        <span class="feed-source ${item.type === 'alert' ? 'nws' : ''}">${item.source}</span>
        <span class="feed-time">${item.time}</span>
      </div>
      <div class="feed-body">
        <span>${item.title}</span>
        <span class="feed-hail-tag">${item.tag}</span>
      </div>
      <div class="feed-remark">"${item.remark}"</div>
    `;
    container.appendChild(el);
  });
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

  // Radar Controls
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

  // Radar Opacity
  const opacitySlider = document.getElementById("radar-opacity");
  opacitySlider.addEventListener("input", (e) => {
    state.radarOpacity = parseFloat(e.target.value) / 100;
    document.getElementById("opacity-label").textContent = `${e.target.value}%`;
    if (rainviewerRadarLayer) rainviewerRadarLayer.setOpacity(state.radarOpacity);
    if (iemNexradLayer) iemNexradLayer.setOpacity(state.radarOpacity);
  });

  // Layer Toggles
  document.getElementById("toggle-polygons").addEventListener("change", () => {
    if (state.assessmentData) renderWarningPolygons(state.assessmentData.nws_alerts || []);
  });
  document.getElementById("toggle-reports").addEventListener("change", () => {
    if (state.assessmentData) renderGroundReports(state.assessmentData.lsr_reports || []);
  });
  document.getElementById("toggle-rings").addEventListener("change", updateUserMarkerAndRings);
  document.getElementById("toggle-nexrad-base").addEventListener("change", (e) => {
    if (e.target.checked) map.addLayer(iemNexradLayer);
    else map.removeLayer(iemNexradLayer);
  });

  // Feed Tabs
  document.querySelectorAll(".feed-tab").forEach((tabBtn) => {
    tabBtn.addEventListener("click", () => {
      document.querySelectorAll(".feed-tab").forEach(b => b.classList.remove("active"));
      tabBtn.classList.add("active");
      state.activeFeedTab = tabBtn.getAttribute("data-tab");
      if (state.assessmentData) {
        renderIntelFeed(state.assessmentData.nws_alerts || [], state.assessmentData.lsr_reports || []);
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
