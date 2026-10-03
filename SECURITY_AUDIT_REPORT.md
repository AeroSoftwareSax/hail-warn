# HailWarn Security Audit & Defect Remediation Report

**Date**: 2026-10-03  
**Auditor & Remediation Engineer**: security-auditor (`security-auditor@agents.noreply.local`)  
**Target Repository**: `/home/aerosoftwaresax/git/hail_warn`  
**Branch**: `agent/security-auditor/audit-remediation`  
**Milestone**: M1 (Security & Defect Remediation + Attribution Policy)  

---

## Executive Summary

A comprehensive code audit and hardening engagement was conducted across the HailWarn severe hail monitoring codebase. All 26 security vulnerabilities and code defect findings (SEC-01..07, DEF-01..19) identified during the architecture survey and adversarial fuzzing evaluation have been successfully remediated, verified with automated regression tests, and audited with static analysis and security scanning tools.

### Key Metrics
- **Total Findings Audited**: 26
- **Remediated & Verified**: 26 (100%)
- **Test Suite Pass Rate**: 100% (53 of 53 tests passing offline in 0.84s)
- **SAST (Bandit -ll)**: 0 Medium / 0 High issues
- **Dependency Audit (pip-audit)**: 0 CVE vulnerabilities found
- **Secret Scan (Gitleaks)**: 0 secrets detected
- **Filesystem Audit (Trivy)**: 0 Critical / 0 High vulnerabilities

---

## Audit Findings Matrix

| Finding ID | Title | Severity | Location | Status | Regression Test Name |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **SEC-01** | TLS Certificate Verification Disabled (`CERT_NONE`) | Critical | `hail_core.py:20-35`, `server.py:26` | Fixed | `test_sec01_tls_verification_enabled` |
| **SEC-02** | Stored / DOM Cross-Site Scripting in mPING & Alert Renderers | High | `static/app.js:8-16, 393-410, 878-885, 916-924, 1140-1175, 1295-1315, 1839-1851` | Fixed | `test_sec02_xss_sanitization_in_feed_and_popups` |
| **SEC-03** | Missing HTTP Security Headers & Unhandled CORS Preflight | Medium | `server.py:113-145` | Fixed | `test_sec03_security_headers_and_options_preflight` |
| **SEC-04** | Server Default Binding to All Network Interfaces (`0.0.0.0`) | Medium | `server.py:297` | Fixed | `test_sec04_default_host_binding` |
| **SEC-05** | Unbounded Numerical Inputs & Missing Coordinate Sanitization | Medium | `server.py:30-70, 150-165, 170-180, 210-220`, `hail_core.py:245-255` | Fixed | `test_sec05_coordinate_and_parameter_bounds` |
| **SEC-06** | Missing Subresource Integrity (SRI) on External CDN Asset | Low | `static/index.html:10, 13, 666` | Fixed | `test_sec06_cdn_subresource_integrity` |
| **SEC-07** | Information Disclosure of Internal Exception Details | Low | `server.py:165-167` | Fixed | `test_sec07_no_internal_stack_leak_on_500` |
| **DEF-01** | Unhandled `None` Geometry in IEM GeoJSON Feature Parsing | Critical | `hail_core.py:255-275` | Fixed | `test_def01_iem_lsr_null_geometry_handling` |
| **DEF-02** | Unhandled `None` in Open-Meteo Hourly Sounding List Iteration | Critical | `hail_core.py:365-375` | Fixed | `test_def02_open_meteo_null_hourly_iterables` |
| **DEF-03** | Unhandled `None` Comparisons in Risk Score Evaluation | Critical | `hail_core.py:920-1015` | Fixed | `test_def03_risk_evaluation_none_comparisons` |
| **DEF-04** | Unbounded Memory Growth & Missing Thread Lock in Global Cache | High | `hail_core.py:38-65, 145-165` | Fixed | `test_def04_cache_max_capacity_and_thread_safety` |
| **DEF-05** | Division by Zero in Motion Tracking & Haversine Domain Error | High | `hail_core.py:135-144, 570-575` | Fixed | `test_def05_haversine_and_storm_motion_boundary_cases` |
| **DEF-06** | Zero Division & Infinite Loop in Radar Player When Offline | High | `static/app.js:1395-1415` | Fixed | `test_def06_radar_playback_with_zero_frames` |
| **DEF-07** | Flawed Freezing Level Scoring on Default/Missing Data | Medium | `hail_core.py:372-375` | Fixed | `test_def07_freezing_level_missing_data_default` |
| **DEF-08** | NWS Alert & RainViewer Schema Changes Crash on `None` Values | Medium | `hail_core.py:180-225, 444-455, 499-510, 840-855` | Fixed | `test_def08_nws_and_rainviewer_null_properties` |
| **DEF-09** | Non-Deterministic Live Network Dependency in Test Suite | Medium | `test_server.py:45-200`, `test_hail_core.py:1-96` | Fixed | `test_def09_offline_deterministic_test_suite` |
| **DEF-10** | Unclosed `HTTPError` Generating `ResourceWarning` Tempfile Leak | Low | `server.py:245, 287`, `hail_core.py:160`, `test_server.py:228, 510, 645` | Fixed | `test_def10_http_error_closed_without_resource_warning` |
| **DEF-11** | Lack of Fault Isolation in Multi-Sensor `perform_full_assessment` | High | `hail_core.py:1105-1125` | Fixed | `test_def11_assessment_graceful_sensor_degradation` |
| **DEF-12** | GeoJSON Top-Level `features: null` Handling | High | `hail_core.py:215-225, 290-305, 540-580` | Fixed | `test_def12_fuzz_null_features_handling` |
| **DEF-13** | Null Feature Items & Corrupt Property Dictionaries in Feature Collections | High | `hail_core.py:220-230, 305-320, 540-555, 615-630` | Fixed | `test_def13_fuzz_null_feature_items_and_properties` |
| **DEF-14** | Type Invariant Crashes on Non-String Motion & Wind Parameter Tags | High | `hail_core.py:560-600, 630-660` | Fixed | `test_def14_fuzz_non_string_motion_and_wind_tags` |
| **DEF-15** | String Floats, Negative, and Garbage Input Rejection in Hail Size Description | Medium | `hail_core.py:90-125` | Fixed | `test_def15_fuzz_string_hail_size_coercion` |
| **DEF-16** | String Floats and Corrupted Arrays in Open-Meteo Physics Model | Medium | `hail_core.py:385-480` | Fixed | `test_def16_fuzz_open_meteo_string_and_malformed_physics` |
| **DEF-17** | Non-Float Attributes in Multi-Sensor Risk Evaluation and LSR Reports | High | `hail_core.py:1010-1120` | Fixed | `test_def17_fuzz_lsr_and_multi_sensor_non_float_attributes` |
| **DEF-18** | Upstream Feed Outage Handling & Fallback Schema Preservation in Hotspots | High | `hail_core.py:890-950`, `server.py:248-260` | Fixed | `test_def18_national_hotspots_upstream_outage_null_response`, `test_def18_api_hotspots_outage_fallback_endpoint` |
| **DEF-19** | Strict Socket Transport Firewall & Deterministic Offline Mock Coverage | High | `test_server.py:883-941` | Fixed | `test_def19_offline_determinism_socket_firewall`, `test_def19_offline_mock_coverage_completeness` |

---

## Detailed Remediation Reports

### SEC-01: TLS Certificate Verification Disabled (`CERT_NONE`)
- **Severity**: Critical (CWE-295, Bandit B501)
- **Files**: `hail_core.py:20-35`, `server.py:26`
- **Root Cause**: `SSL_CTX.verify_mode = ssl.CERT_NONE` and `SSL_CTX.check_hostname = False` disabled all TLS authentication, permitting MITM tampering of severe weather alerts and coordinates.
- **Fix Applied**: Implemented `create_ssl_context()` which sets `check_hostname = True` and `verify_mode = ssl.CERT_REQUIRED`. Added support for custom CA bundles via `SSL_CERT_FILE` or `HAILWARN_CA_BUNDLE`. Removed all `# nosec B501` bypass comments.
- **Regression Test**: `test_sec01_tls_verification_enabled` asserts `SSL_CTX.verify_mode == ssl.CERT_REQUIRED` and `SSL_CTX.check_hostname is True`.

### SEC-02: Stored / DOM Cross-Site Scripting in mPING & Alert Renderers
- **Severity**: High (CWE-79)
- **Files**: `static/app.js`
- **Root Cause**: Untrusted crowdsourced mPING report remarks (`item.remark`), NWS alert descriptions and directives (`w.instruction`, `w.headline`, `w.description`), and search result labels (`item.display_name`) were interpolated directly into `innerHTML` and Leaflet `bindPopup()` cards.
- **Fix Applied**: Implemented `escapeHtml()` sanitization utility converting `&`, `<`, `>`, `"`, and `'` to safe HTML entities. All dynamic fields rendered to DOM or popup templates now pass through `escapeHtml()` or use safe string truncation.
- **Regression Test**: `test_sec02_xss_sanitization_in_feed_and_popups` asserts existence and invocation of `escapeHtml()` across all report, popup, and search renderers.

### SEC-03: Missing HTTP Security Headers & Unhandled CORS Preflight
- **Severity**: Medium (CWE-693)
- **Files**: `server.py:113-145`
- **Root Cause**: Missing security defense headers (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Content-Security-Policy`). Cross-origin requests issuing `OPTIONS` preflight resulted in HTTP 501.
- **Fix Applied**: Overrode `end_headers()` in `HailWarnRequestHandler` to automatically attach security headers to all responses (static and API). Added `do_OPTIONS()` handler returning HTTP 204 with `Access-Control-Allow-Methods` and `Access-Control-Allow-Headers`.
- **Regression Test**: `test_sec03_security_headers_and_options_preflight` verifies response headers on GET and status 204 with preflight headers on OPTIONS.

### SEC-04: Server Default Binding to All Network Interfaces (`0.0.0.0`)
- **Severity**: Medium (CWE-1327, Bandit B104)
- **Files**: `server.py:297`
- **Root Cause**: Default binding to `0.0.0.0` exposed local development server to external LAN/WAN interfaces.
- **Fix Applied**: Changed default binding to `127.0.0.1` (`host=os.environ.get('HOST', '127.0.0.1')`). Containerized environments can explicitly configure `HOST=0.0.0.0`.
- **Regression Test**: `test_sec04_default_host_binding` checks `run_server.__defaults__` is `'127.0.0.1'`.

### SEC-05: Unbounded Numerical Inputs & Missing Coordinate Sanitization
- **Severity**: Medium (CWE-1284, CWE-400)
- **Files**: `server.py:30-70, 150-165, 170-180`, `hail_core.py:245-255`
- **Root Cause**: `float()` allowed `"nan"`, `"inf"`, and out-of-bounds coordinates to pass to upstream APIs. Unbounded `hours` (`hours=1000000`) caused upstream DoS and memory exhaustion.
- **Fix Applied**: Implemented `validate_coordinates()` checking `math.isnan`, `math.isinf`, `-90.0 <= lat <= 90.0`, `-180.0 <= lon <= 180.0`. Added `validate_radius()` (`[1.0, 500.0]`) and `validate_hours()` (`[1, 720]`). Returns HTTP 400 with descriptive error messages. Clamped `hours` in `fetch_iem_lsr_reports`.
- **Regression Test**: `test_sec05_coordinate_and_parameter_bounds` exercises NaN, Inf, out-of-range latitude/longitude, negative radius, and out-of-range hours, verifying HTTP 400 responses.

### SEC-06: Missing Subresource Integrity (SRI) on External CDN Asset
- **Severity**: Low (CWE-353)
- **Files**: `static/index.html:10, 13, 666`
- **Root Cause**: FontAwesome CDN stylesheet lacked `integrity` and `crossorigin` attributes, allowing potential code manipulation if CDN was compromised.
- **Fix Applied**: Added SHA-512 SRI hash (`integrity="sha512-DTOQO9RWCH3ppGqcWaEA1BIZOC6xxalwEsw9c2QQeAIftl+Vegovlnee1c9QX4TctnWMn13TZye+giMm8e2LwA=="`) and `crossorigin="anonymous"` to FontAwesome stylesheet. Set `crossorigin="anonymous"` on Leaflet CSS and script.
- **Regression Test**: `test_sec06_cdn_subresource_integrity` validates presence of integrity hashes and crossorigin attributes on all external CDN tags in `index.html`.

### SEC-07: Information Disclosure of Internal Exception Details
- **Severity**: Low (CWE-209)
- **Files**: `server.py:165-167`
- **Root Cause**: HTTP 500 responses included `'details': str(e)`, leaking internal exceptions, paths, and modules to clients.
- **Fix Applied**: Log full exception to `sys.stderr` internally; return sanitized client payload: `{'error': 'Assessment failure. Please check server logs.'}`.
- **Regression Test**: `test_sec07_no_internal_stack_leak_on_500` mocks an internal exception containing confidential path strings and verifies the response contains no internal details.

### DEF-01: Unhandled `None` Geometry in IEM GeoJSON Feature Parsing
- **Severity**: Critical (CWE-476)
- **Files**: `hail_core.py:255-275`
- **Root Cause**: When IEM LSR report features had `"geometry": null`, `geom.get('coordinates')` raised `AttributeError: 'NoneType' object has no attribute 'get'`.
- **Fix Applied**: Added defensive type and null validation: `geom = f.get('geometry') or {}`, checked `isinstance(geom, dict)`, verified coordinates list has length >= 2 before unpacking.
- **Regression Test**: `test_def01_iem_lsr_null_geometry_handling` feeds features with null geometry and empty coordinate lists, verifying clean processing without exceptions.

### DEF-02: Unhandled `None` in Open-Meteo Hourly Sounding List Iteration
- **Severity**: Critical (CWE-476)
- **Files**: `hail_core.py:365-375`
- **Root Cause**: Null hourly parameter values caused `TypeError: 'NoneType' object is not iterable` in list comprehensions.
- **Fix Applied**: Applied defensive fallback: `(hourly.get('cape') or [0])`, `(hourly.get('lifted_index') or [0])`, `(hourly.get('convective_inhibition') or [0])`, and `(hourly.get('freezing_level_height') or [])`.
- **Regression Test**: `test_def02_open_meteo_null_hourly_iterables` passes null hourly values and verifies valid dictionary output.

### DEF-03: Unhandled `None` Comparisons in Risk Score Evaluation
- **Severity**: Critical (CWE-476)
- **Files**: `hail_core.py:920-1015`
- **Root Cause**: Missing or null fields in reports or convective soundings resulted in comparisons like `None >= 2500` or `None <= 24.0`, raising `TypeError`.
- **Fix Applied**: Null-coalesced all numerical parameters before evaluation: default `cape` to 0, `li` to 0.0, `fz_ft` to 12000, `age` to 999.0, `dist` to 999.0.
- **Regression Test**: `test_def03_risk_evaluation_none_comparisons` evaluates threat score with completely null metrics and verifies successful risk calculation.

### DEF-04: Unbounded Memory Growth & Missing Thread Lock in Global Cache
- **Severity**: High (CWE-400)
- **Files**: `hail_core.py:38-65, 145-165`
- **Root Cause**: `_CACHE` dictionary had no eviction, no upper limit on size, and lacked thread locking across `ThreadPoolExecutor` and `ThreadedHTTPServer` threads.
- **Fix Applied**: Converted `_CACHE` to an `OrderedDict` guarded by `threading.Lock()`. Implemented TTL eviction and capped maximum size to `MAX_CACHE_ENTRIES = 500`. Oldest items evicted via `popitem(last=False)`.
- **Regression Test**: `test_def04_cache_max_capacity_and_thread_safety` inserts 550 entries and asserts `len(_CACHE) <= 500` with correct LRU eviction.

### DEF-05: Division by Zero in Motion Tracking & Haversine Domain Error
- **Severity**: High (CWE-369)
- **Files**: `hail_core.py:135-144, 570-575`
- **Root Cause**: Near-polar storm vectors caused `cos(slat) == 0`, raising `ZeroDivisionError`. Floating-point rounding on antipodal Haversine points produced `a > 1.0`, raising `ValueError: math domain error`.
- **Fix Applied**: Clamped cosine denominator in storm motion projection (`abs(cos_lat) >= 1e-6`). Clamped `a` in `haversine_distance` to `[0.0, 1.0]`.
- **Regression Test**: `test_def05_haversine_and_storm_motion_boundary_cases` tests antipodal Haversine distance and storm vectors at latitude 90.0°.

### DEF-06: Zero Division & Infinite Loop in Radar Player When Offline
- **Severity**: High (CWE-835)
- **Files**: `static/app.js:1395-1415`
- **Root Cause**: `(state.radarIndex + 1) % state.radarFrames.length` evaluated to `NaN` when `radarFrames` was empty, causing `radarIndex` to corrupt and timer to loop endlessly.
- **Fix Applied**: Added guard condition in `playRadarLoop()`: if `!state.radarFrames || state.radarFrames.length === 0`, return immediately. Also clears interval inside tick if frames become empty.
- **Regression Test**: `test_def06_radar_playback_with_zero_frames` validates guard condition exists in `app.js`.

### DEF-07: Flawed Freezing Level Scoring on Default/Missing Data
- **Severity**: Medium (CWE-670)
- **Files**: `hail_core.py:372-375`
- **Root Cause**: Missing freezing level height defaulted to 0, matching `0 <= 9500 ft` and awarding maximum hail survival score (20 points).
- **Fix Applied**: Default missing freezing level height to standard reference (3500 meters / ~11,483 feet AGL), awarding standard `MODERATE` melting rating.
- **Regression Test**: `test_def07_freezing_level_missing_data_default` calls convective function with empty hourly data and confirms freezing level defaults to >10,000 ft with `MODERATE` survival rating.

### DEF-08: NWS Alert & RainViewer Schema Changes Crash on `None` Values
- **Severity**: Medium (CWE-476)
- **Files**: `hail_core.py:180-225, 444-455, 499-510, 840-855`
- **Root Cause**: Null `parameters`, null `description`, or null `radar` dictionaries caused `AttributeError` during `.get()` or `.lower()`.
- **Fix Applied**: Uniformly applied null-coalescing across all NWS alerts, warnings, hotspots, and RainViewer parser methods.
- **Regression Test**: `test_def08_nws_and_rainviewer_null_properties` tests alert feeds with null parameters and radar with null frames.

### DEF-09: Non-Deterministic Live Network Dependency in Test Suite
- **Severity**: Medium (Test Reliability)
- **Files**: `test_server.py:45-200`, `test_hail_core.py:1-96`
- **Root Cause**: Integration tests made live HTTP requests to public external APIs, leading to timeouts, network dependencies, and test flakiness.
- **Fix Applied**: Implemented deterministic offline mock interceptor in `test_server.py` and `test_hail_core.py`. External calls receive instant mock GeoJSON/JSON responses. Suite runs 100% offline in <1 second.
- **Regression Test**: `test_def09_offline_deterministic_test_suite` verifies mock interceptor handles all external weather domains.

### DEF-10: Unclosed `HTTPError` Generating `ResourceWarning` Tempfile Leak
- **Severity**: Low (CWE-775)
- **Files**: `server.py:245, 287`, `hail_core.py:160`, `test_server.py:228, 510, 645`
- **Root Cause**: Caught `HTTPError` exceptions held unclosed response sockets, emitting `ResourceWarning: Implicitly cleaning up <HTTPError 400>`.
- **Fix Applied**: Explicitly called `.close()` on all caught `HTTPError` instances across `server.py`, `hail_core.py`, and test validation methods.
- **Regression Test**: `test_def10_http_error_closed_without_resource_warning` executes validation requests under strict `ResourceWarning` filtering and verifies 0 warnings emitted.

### DEF-11: Lack of Fault Isolation in Multi-Sensor `perform_full_assessment`
- **Severity**: High (CWE-703)
- **Files**: `hail_core.py:1105-1125`
- **Root Cause**: `perform_full_assessment` unpacked thread pool futures without `try...except`, crashing the entire sensor fusion engine if a single upstream feed threw an error.
- **Fix Applied**: Implemented `safe_future_result(future, default, sensor_name)` which catches exceptions per-future, logs sensor degradation, and returns safe fallback data.
- **Regression Test**: `test_def11_assessment_graceful_sensor_degradation` simulates failure in Open-Meteo convective sensor and confirms the multi-sensor pipeline continues cleanly.

### DEF-12: GeoJSON Top-Level `features: null` Handling
- **Severity**: High (CWE-476)
- **Files**: `hail_core.py:215-225, 290-305, 540-580`
- **Root Cause**: Upstream NWS and IEM GeoJSON endpoints intermittently return `{"type": "FeatureCollection", "features": null}` when zero warnings are active. Traversing `data.get('features')` without type checks caused `TypeError: 'NoneType' object is not iterable`.
- **Fix Applied**: Added explicit `isinstance(data.get('features'), list)` checks across `fetch_nws_point_alerts`, `fetch_iem_lsr_reports`, and `fetch_active_nws_warnings`. If `features` is null or non-list, functions return safe empty list/collections.
- **Regression Test**: `test_def12_fuzz_null_features_handling` passes `{"features": null}` across all warning and report collectors, confirming clean execution without exceptions.

### DEF-13: Null Feature Items & Corrupt Property Dictionaries in Feature Collections
- **Severity**: High (CWE-476, CWE-754)
- **Files**: `hail_core.py:220-230, 305-320, 540-555, 615-630`
- **Root Cause**: Feeds containing non-dict or null items inside `features` arrays (e.g. `[None, 42, {"properties": null}]`) caused `AttributeError: 'NoneType' object has no attribute 'get'` when accessing properties or geometry.
- **Fix Applied**: Validated `isinstance(f, dict)` for each feature item; coerced `properties` and `geometry` to dictionaries using defensive ternary expressions (`f.get('properties') if isinstance(f.get('properties'), dict) else {}`), and skipped features with missing or empty `event` names.
- **Regression Test**: `test_def13_fuzz_null_feature_items_and_properties` passes collections containing None, integers, strings, and null properties, verifying valid features are preserved while corrupt items are cleanly dropped.

### DEF-14: Type Invariant Crashes on Non-String Motion & Wind Parameter Tags
- **Severity**: High (CWE-704, CWE-248)
- **Files**: `hail_core.py:560-600, 630-660`
- **Root Cause**: NWS warning parameters dictionaries containing non-string or null elements (e.g. `eventMotionDescription: [None]`, `maxWindGust: [80]`) caused `TypeError` in regex searches or container containment checks (`'80' in wind_tags[0]`).
- **Fix Applied**: Added type checking and safe string extraction for `motion_str` before regex matching (`isinstance(m_raw, str)`). Coerced `wind_tags` to string before inspection, and defaulted unparseable motion vectors to `None`.
- **Regression Test**: `test_def14_fuzz_non_string_motion_and_wind_tags` tests NoneType, integer, dict, and boolean tags across motion and wind parameters, confirming zero crashes and safe fallbacks.

### DEF-15: String Floats, Negative, and Garbage Input Rejection in Hail Size Description
- **Severity**: Medium (CWE-704, CWE-1284)
- **Files**: `hail_core.py:90-125`
- **Root Cause**: `describe_hail_size` compared `inches <= 0` directly. When string floats (`"1.75"`) or non-numeric strings (`"unknown"`) were provided, Python raised `TypeError: '<=' not supported between instances of 'str' and 'int'`.
- **Fix Applied**: Implemented `_safe_float` coercion on `inches`. Coerced positive string floats into standard hail category descriptions (e.g., `"1.0"` -> `"Quarter"`, `"1.75"` -> `"Golf Ball"`, `"2.75"` -> `"Baseball"`). Filtered out non-positive values, NaNs, infinities, and unparseable strings to safely return `"None"`.
- **Regression Test**: `test_def15_fuzz_string_hail_size_coercion` exercises valid string floats, negative values, NaNs, infinities, and non-string inputs.

### DEF-16: String Floats and Corrupted Arrays in Open-Meteo Physics Model
- **Severity**: Medium (CWE-704)
- **Files**: `hail_core.py:385-480`
- **Root Cause**: Upstream Open-Meteo current temperatures or hourly physics soundings serialized as strings caused `TypeError: unsupported operand type(s) for /: 'str' and 'int'` during Celsius-to-Fahrenheit conversion and Hail Potential Index calculation.
- **Fix Applied**: Implemented `_safe_float` for scalar temperatures, pressures, and wind speeds. Implemented `_safe_float_list` for hourly sounding arrays (`cape`, `lifted_index`, `convective_inhibition`, `freezing_level_height`), discarding corrupt non-numeric elements.
- **Regression Test**: `test_def16_fuzz_open_meteo_string_and_malformed_physics` feeds string temperatures and hourly arrays with corrupt elements, verifying accurate physical calculations.

### DEF-17: Non-Float Attributes in Multi-Sensor Risk Evaluation and LSR Reports
- **Severity**: High (CWE-704, CWE-754)
- **Files**: `hail_core.py:1010-1120`
- **Root Cause**: LSR reports, NWS alerts, and regional warnings with string numbers (`distance_miles: "3.5"`, `hail_size_in: "1.75"`) caused `TypeError` in threshold comparisons and `ValueError: Unknown format code 'f' for object of type 'str'` during risk rationale string formatting (`{dist:.1f} mi`).
- **Fix Applied**: Applied `_safe_float` across all distance, hail size, and age metrics before threshold comparisons and string formatting. Invalid non-numeric entries default safely to null or are discarded.
- **Regression Test**: `test_def17_fuzz_lsr_and_multi_sensor_non_float_attributes` exercises string-encoded floats and non-numeric garbage across LSR reports, point alerts, convective soundings, and regional warnings.

### DEF-18: Upstream Feed Outage Handling & Fallback Schema Preservation in Hotspots
- **Severity**: High (CWE-754, CWE-390)
- **Files**: `hail_core.py:890-950`, `server.py:248-260`
- **Root Cause**: When upstream NWS feed experienced network timeouts or outages, `http_get_json()` returned `None`. In `fetch_active_national_hotspots`, attempting `data.get('features')` threw `AttributeError`, causing `/api/hotspots` to catch the error and return empty `[]` instead of populating standard hail alley fallback zones.
- **Fix Applied**: Coalesced `data = http_get_json(url) or {}`. If `data` is empty or lacks features, `fetch_active_national_hotspots` returns the 4 standard hail alley zones (DFW, OKC, Denver, Wichita). Hardened `server.py` `handle_hotspots` to guarantee consistent JSON schema `{ 'hotspots': [...] }`.
- **Regression Test**: `test_def18_national_hotspots_upstream_outage_null_response` and `test_def18_api_hotspots_outage_fallback_endpoint` simulate NWS outages and verify HTTP 200 with 4 fallback hotspots.

### DEF-19: Strict Socket Transport Firewall & Deterministic Offline Mock Coverage
- **Severity**: High (Reliability & Test Integrity)
- **Files**: `test_server.py:883-941`
- **Root Cause**: Without socket-level transport firewalls, integration tests could inadvertently attempt live external connections if mocks were incomplete, causing test flakiness and CI failures in offline environments.
- **Fix Applied**: Added transport-level `socket.socket.connect` firewall monkeypatch in `test_def19_offline_determinism_socket_firewall`, asserting zero external outbound connections during full multi-sensor assessment, warnings, hotspots, radar, and outlook queries. Verified complete mock fixture coverage for all external endpoints in `test_def19_offline_mock_coverage_completeness`.
- **Regression Test**: `test_def19_offline_determinism_socket_firewall` and `test_def19_offline_mock_coverage_completeness`.

---

## Verification & Compliance Results

### 1. Automated Test Suite
- Command: `python3 -m unittest discover -v`
- Result: **OK (53 tests passed in 0.836s)**
- Coverage:
  - 17 baseline integration and unit tests
  - 26 regression tests covering SEC-01..07 and DEF-01..19
  - 4 UI XSS/CSP and server fallback verification tests
  - 6 offline core algorithm unit tests in `test_hail_core.py`

### 2. Python SAST (Bandit)
- Command: `bandit -r . -c .bandit.yaml -ll`
- Result: **0 High / 0 Medium issues**

### 3. Dependency CVE Audit (pip-audit)
- Command: `pip-audit -r requirements.txt`
- Result: **No known vulnerabilities found**

### 4. Secret Scanning (Gitleaks)
- Command: `gitleaks detect --config=.gitleaks.toml -v`
- Result: **No leaks found**

### 5. Vulnerability & Filesystem Scanning (Trivy)
- Command: `trivy fs --ignore-unfixed --severity CRITICAL,HIGH .`
- Result: **0 Critical / 0 High issues detected**
