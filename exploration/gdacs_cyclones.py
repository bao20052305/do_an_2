# Demo lấy dữ liệu bão nhiệt đới từ GDACS: danh sách bão -> đường đi (quan sát + dự báo) -> vùng gió
# pip install requests pandas
import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]   # thư mục gốc đồ án
OUT_DIR = ROOT / "data" / "exploration" / "gdacs"
OUT_DIR.mkdir(parents=True, exist_ok=True)

BASE = "https://www.gdacs.org/gdacsapi/api"
HEADERS = {"User-Agent": "do-an-canh-bao-bao/0.1 (student project)"}

# Khung Biển Đông: (kinh độ min, vĩ độ min, kinh độ max, vĩ độ max)
BIEN_DONG = (99.0, 0.0, 122.0, 25.0)

# Chọn khoảng thời gian: 60 ngày gần nhất, hoặc bỏ comment dòng dưới để xem bão Yagi 2024
#FROM_DATE, TO_DATE = str(date.today() - timedelta(days=60)), str(date.today())
FROM_DATE, TO_DATE = "2026-09-29", "2026-10-03"

EVENT_ID = None   # để None thì tự chọn bão mới nhất trong Biển Đông; hoặc điền eventid cụ thể


def get_json(url, params=None):
    r = requests.get(url, params=params, headers=HEADERS, timeout=30)
    if r.status_code == 204 or not r.text.strip():
        return None                      # GDACS trả rỗng khi không có sự kiện
    r.raise_for_status()
    return r.json()


def in_bien_dong(feature):
    """Kiểm tra sự kiện có chạm khung Biển Đông không, dựa vào bbox hoặc tọa độ điểm."""
    lo1, la1, lo2, la2 = BIEN_DONG
    bbox = feature.get("bbox")
    if bbox and len(bbox) == 4:
        return not (bbox[2] < lo1 or bbox[0] > lo2 or bbox[3] < la1 or bbox[1] > la2)
    geom = feature.get("geometry") or {}
    if geom.get("type") == "Point":
        lon, lat = geom["coordinates"][:2]
        return lo1 <= lon <= lo2 and la1 <= lat <= la2
    return False


def beaufort(ms):
    """Đổi tốc độ gió (m/s) sang cấp gió Beaufort mở rộng như bản tin bão Việt Nam (tới cấp 17)."""
    limits = [0.3, 1.6, 3.4, 5.5, 8.0, 10.8, 13.9, 17.2, 20.8, 24.5, 28.5, 32.7,
              37.0, 41.5, 46.2, 51.0, 56.1]
    level = 0
    for i, lim in enumerate(limits):
        if ms >= lim:
            level = i + 1
    return min(level, 17)


# ---------- Bước 1: danh sách bão ----------
def list_cyclones():
    data = get_json(f"{BASE}/events/geteventlist/SEARCH", {
        "eventlist": "TC",
        "fromdate": FROM_DATE,
        "todate": TO_DATE,
        "alertlevel": "green;orange;red",
    })
    features = (data or {}).get("features", [])
    rows = []
    for f in features:
        p = f.get("properties", {})
        sev = p.get("severitydata") or {}
        rows.append({
            "eventid": p.get("eventid"),
            "episodeid": p.get("episodeid"),
            "name": p.get("name"),
            "alertlevel": p.get("alertlevel"),
            "iscurrent": p.get("iscurrent"),
            "fromdate": p.get("fromdate"),
            "todate": p.get("todate"),
            "country": p.get("country"),
            "severity": sev.get("severitytext"),
            "in_bien_dong": in_bien_dong(f),
        })
    df = pd.DataFrame(rows)
    print(f"=== {len(df)} bão từ {FROM_DATE} đến {TO_DATE} ===")
    if not df.empty:
        print(df.drop(columns=["severity"]).to_string(index=False))
        df.to_csv(OUT_DIR / "gdacs_tc_events.csv", index=False, encoding="utf-8-sig")
    return df


# ---------- Bước 2: đường đi bão (quan sát + dự báo) ----------
def get_timeline(event_id):
    detail = get_json(f"{BASE}/events/geteventdata", {"eventtype": "TC", "eventid": event_id})
    props = (detail or {}).get("properties", {})
    print(f"\n=== Bão {props.get('name')} (eventid={event_id}, episode={props.get('episodeid')}) ===")

    impacts = props.get("impacts") or []
    if not impacts:
        print("Không có mục 'impacts' -> không lấy được timeline. Các khóa có:", list(props.keys()))
        return None, props

    rows = []
    for imp in impacts:                          # mỗi nguồn dự báo (ví dụ JTWC) một timeline
        url = (imp.get("resource") or {}).get("timeline")
        if not url:
            continue
        tl = get_json(url) or {}
        items = (tl.get("channel") or {}).get("item") or []
        for it in items:
            lon, lat = [float(x) for x in str(it.get("coordinates", "0,0")).split(",")]
            wind = float(it.get("wind_speed") or 0)
            rows.append({
                "source": imp.get("source"),
                "point_id": it.get("id"),                     # khóa ổn định giữa các episode
                "advisory_number": it.get("advisory_number"),
                "time_utc": datetime.strptime(it.get("advisory_datetime"), "%d %b %Y %H:%M"),
                "type": "quan_sat" if str(it.get("actual")).lower() == "true" else "du_bao",
                "is_current": str(it.get("current")).lower() == "true",
                "lat": lat,
                "lon": lon,
                "wind_ms": wind,
                "wind_kmh": round(wind * 3.6, 1),
                "cap_gio": beaufort(wind),
                "pop39": it.get("pop39"),
                "pop74": it.get("pop74"),
            })

    df = pd.DataFrame(rows).sort_values("time_utc")
    print(df[["time_utc", "type", "is_current", "lat", "lon", "wind_kmh", "cap_gio"]]
          .to_string(index=False))
    df.to_csv(OUT_DIR / f"gdacs_tc_timeline_{event_id}.csv", index=False, encoding="utf-8-sig")
    return df, props


# ---------- Bước 3: vùng gió / hình nón dự báo dạng GeoJSON ----------
def get_geometry(event_id, episode_id):
    geo = get_json(f"{BASE}/polygons/getgeometry",
                   {"eventtype": "TC", "eventid": event_id, "episodeid": episode_id})
    feats = (geo or {}).get("features", [])
    classes = pd.Series([f.get("properties", {}).get("Class") for f in feats]).value_counts()
    print(f"\n=== {len(feats)} đối tượng hình học, phân theo 'Class' ===")
    print(classes.to_string())
    fname = OUT_DIR / f"gdacs_tc_geometry_{event_id}_{episode_id}.geojson"
    with open(fname, "w", encoding="utf-8") as f:
        json.dump(geo, f, ensure_ascii=False)
    print(f"Đã lưu {fname} (mở bằng geojson.io hoặc QGIS để xem)")


def main():
    events = list_cyclones()
    event_id = EVENT_ID
    if event_id is None:
        if events.empty or not events["in_bien_dong"].any():
            print("\nKhông có bão nào chạm Biển Đông trong khoảng này. "
                  "Thử đổi sang khoảng thời gian bão Yagi hoặc điền EVENT_ID.")
            return
        event_id = events[events["in_bien_dong"]].sort_values("fromdate").iloc[-1]["eventid"]

    timeline, props = get_timeline(event_id)
    if props.get("episodeid"):
        get_geometry(event_id, props["episodeid"])


if __name__ == "__main__":
    main()