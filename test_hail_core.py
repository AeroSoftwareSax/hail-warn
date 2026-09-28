import json
from hail_core import perform_full_assessment, fetch_active_national_hotspots, describe_hail_size

def test_runs():
    print("Testing hail size descriptions...")
    assert "Quarter" in describe_hail_size(1.0)
    assert "Golf Ball" in describe_hail_size(1.75)
    print("  -> Hail size descriptions OK")

    print("Testing active hotspots query...")
    hotspots = fetch_active_national_hotspots()
    print(f"  -> Hotspots returned: {len(hotspots)}")
    for h in hotspots[:3]:
        print(f"     * {h.get('area')}: {h.get('event')} ({h.get('hail_label')})")

    print("\nTesting full assessment for Austin, TX (30.2672, -97.7431)...")
    res_atx = perform_full_assessment(30.2672, -97.7431, radius_miles=40)
    print("Assessment:", res_atx['assessment']['level'], f"Score: {res_atx['assessment']['score']}%")
    print("Max hail:", res_atx['assessment']['max_hail_label'])
    print("Alerts found:", len(res_atx['nws_alerts']))
    print("LSR reports found:", len(res_atx['lsr_reports']))
    print("Convective CAPE:", res_atx['convective_data'].get('cape_j_kg'))
    print("Reasons:", res_atx['assessment']['reasons'])

    print("\nTesting full assessment for North Platte, NE (41.1239, -100.7654)...")
    res_lbf = perform_full_assessment(41.1239, -100.7654, radius_miles=40)
    print("Assessment:", res_lbf['assessment']['level'], f"Score: {res_lbf['assessment']['score']}%")
    print("Max hail:", res_lbf['assessment']['max_hail_label'])
    print("Alerts found:", len(res_lbf['nws_alerts']))
    for a in res_lbf['nws_alerts']:
        print(f"  Alert: {a['event']} | Hail tag: {a['hail_size_in']}")
    print("Reasons:", res_lbf['assessment']['reasons'])

if __name__ == '__main__':
    test_runs()
