"""Lấy dữ liệu bão nhiệt đới từ GDACS và ghi nguyên văn vào bronze.gdacs_raw.

Mỗi lần chạy:
  1. Gọi danh sách bão trong khoảng ngày (event_list). Chỉ ghi khi nội dung khác mọi lần trước.
  2. Với bão chạm khung Biển Đông mở rộng: lấy chi tiết (event_detail), đường đi (timeline,
     mỗi nguồn dự báo một dòng) và vùng gió (geometry) của episode hiện tại.
     Episode đã có trong bảng thì không gọi lại: mỗi bản tin bão chỉ lấy đúng một lần.

Chạy:
  python ingestion/gdacs/fetch_gdacs.py                                  (3 ngày gần nhất)
  python ingestion/gdacs/fetch_gdacs.py --from 2024-09-01 --to 2024-09-08 --all-episodes
  python ingestion/gdacs/fetch_gdacs.py --event-id 1001307 --all-episodes
"""
import argparse
import hashlib
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pyiceberg.expressions import EqualTo  # noqa: E402

from ingestion.common.bronze_schemas import GDACS_RAW  # noqa: E402
from ingestion.common.lakehouse import append, existing_keys, load_table, new_run_id  # noqa: E402

INGEST_VERSION = "gdacs/0.1"
BASE = "https://www.gdacs.org/gdacsapi/api"
HEADERS = {"User-Agent": "do-an-canh-bao-bao/0.1 (student project)"}
# Khung Biển Đông mở rộng (kinh độ min, vĩ độ min, kinh độ max, vĩ độ max):
# rộng hơn Biển Đông để thấy cả bão từ Thái Bình Dương đang tiến vào.
WATCH_BBOX = (95.0, 0.0, 135.0, 30.0)


# ---------------------------------------------------------------- HTTP
def fetch(url: str, params: dict | None = None, retries: int = 3) -> requests.Response:
    for i in range(retries):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=60)
            if r.status_code in (200, 204):
                return r
            r.raise_for_status()
        except requests.RequestException as e:
            if i == retries - 1:
                raise
            print(f"  lỗi {e}, thử lại sau {2 ** i}s")
            time.sleep(2 ** i)
    raise RuntimeError("không tới được đây")


def as_json(r: requests.Response) -> dict:
    return r.json() if r.status_code == 200 and r.text.strip() else {}


def make_row(endpoint, r, run_id, eventid=None, episodeid=None, forecast_source=None) -> dict:
    payload = r.text if r.status_code == 200 else ""
    return {
        "endpoint": endpoint,
        "eventid": int(eventid) if eventid is not None else None,
        "episodeid": int(episodeid) if episodeid is not None else None,
        "forecast_source": forecast_source,
        "request_url": r.url,
        "http_status": r.status_code,
        "payload": payload,
        "payload_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        "fetched_at": datetime.now(timezone.utc),
        "run_id": run_id,
        "ingest_version": INGEST_VERSION,
    }


# ---------------------------------------------------------------- lọc bão
def in_watch_area(feature: dict) -> bool:
    """Bão có bbox (hoặc tọa độ) chạm khung theo dõi không."""
    lo1, la1, lo2, la2 = WATCH_BBOX
    bbox = feature.get("bbox")
    if bbox and len(bbox) == 4:
        return not (bbox[2] < lo1 or bbox[0] > lo2 or bbox[3] < la1 or bbox[1] > la2)
    geom = feature.get("geometry") or {}
    if geom.get("type") == "Point":
        lon, lat = geom["coordinates"][:2]
        return lo1 <= lon <= lo2 and la1 <= lat <= la2
    return False


# ---------------------------------------------------------------- một episode
def fetch_episode(eventid: int, episodeid: int, done: set, run_id: str, force: bool) -> list[dict]:
    """Lấy chi tiết, timeline và vùng gió của một episode. Bỏ qua phần đã có trong bảng."""
    rows = []
    have = {k[0] for k in done if k[1] == eventid and k[2] == episodeid}
    if not force and {"event_detail", "timeline", "geometry"} <= have:
        return rows                          # episode đã lấy đủ, không gọi API

    # chi tiết bão: chứa link timeline theo từng nguồn dự báo
    r = fetch(f"{BASE}/events/geteventdata",
              {"eventtype": "TC", "eventid": eventid, "episodeid": episodeid})
    detail = as_json(r)
    got_episode = (detail.get("properties") or {}).get("episodeid")
    if got_episode is not None and int(got_episode) != episodeid:
        # API trả episode khác với episode yêu cầu: không ghi để tránh gắn nhầm nhãn
        print(f"  [{eventid}/{episodeid}] API trả episode {got_episode}, bỏ qua")
        return rows
    if force or ("event_detail", eventid, episodeid, None) not in done:
        rows.append(make_row("event_detail", r, run_id, eventid, episodeid))

    # đường đi: mỗi nguồn dự báo (impacts[]) một timeline
    for imp in (detail.get("properties") or {}).get("impacts") or []:
        url = (imp.get("resource") or {}).get("timeline")
        src = imp.get("source")
        if not url or (not force and ("timeline", eventid, episodeid, src) in done):
            continue
        rows.append(make_row("timeline", fetch(url), run_id, eventid, episodeid, src))

    # vùng gió, hình nón dự báo
    if force or ("geometry", eventid, episodeid, None) not in done:
        r = fetch(f"{BASE}/polygons/getgeometry",
                  {"eventtype": "TC", "eventid": eventid, "episodeid": episodeid})
        rows.append(make_row("geometry", r, run_id, eventid, episodeid))

    print(f"  [{eventid}/{episodeid}] {len(rows)} dòng mới")
    return rows


# ---------------------------------------------------------------- chạy
def run(date_from: date, date_to: date, event_ids: list[int] | None = None,
        all_episodes: bool = False, force: bool = False) -> int:
    table = load_table(GDACS_RAW)
    run_id = new_run_id("gdacs")
    rows: list[dict] = []

    # khóa đã có: (endpoint, eventid, episodeid, forecast_source)
    done = existing_keys(table, ["endpoint", "eventid", "episodeid", "forecast_source"])

    # 1. danh sách bão
    targets: dict[int, int] = {}            # eventid -> episodeid hiện tại
    if event_ids:
        for eid in event_ids:
            d = as_json(fetch(f"{BASE}/events/geteventdata", {"eventtype": "TC", "eventid": eid}))
            ep = (d.get("properties") or {}).get("episodeid")
            if ep is not None:
                targets[eid] = int(ep)
    else:
        r = fetch(f"{BASE}/events/geteventlist/SEARCH", {
            "eventlist": "TC", "fromdate": str(date_from), "todate": str(date_to),
            "alertlevel": "green;orange;red",
        })
        row = make_row("event_list", r, run_id)
        seen_lists = existing_keys(table, ["payload_sha256"], EqualTo("endpoint", "event_list"))
        if force or (row["payload_sha256"],) not in seen_lists:
            rows.append(row)
        features = as_json(r).get("features", [])
        for f in features:
            p = f.get("properties", {})
            if in_watch_area(f) and p.get("eventid") is not None and p.get("episodeid") is not None:
                targets[int(p["eventid"])] = int(p["episodeid"])
        print(f"{len(features)} bão từ {date_from} đến {date_to}, "
              f"{len(targets)} bão trong khung theo dõi: {targets}")

    # 2. chi tiết từng episode
    for eid, current_ep in targets.items():
        episodes = range(1, current_ep + 1) if all_episodes else [current_ep]
        for ep in episodes:
            rows += fetch_episode(eid, ep, done, run_id, force)
            time.sleep(0.5)                  # lịch sự với máy chủ

    n = append(table, rows)
    print(f"Đã ghi {n} dòng vào {GDACS_RAW} (run_id={run_id})")
    return n


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--from", dest="date_from", type=date.fromisoformat,
                    default=date.today() - timedelta(days=3))
    ap.add_argument("--to", dest="date_to", type=date.fromisoformat, default=date.today())
    ap.add_argument("--event-id", type=int, action="append",
                    help="lấy thẳng một bão theo eventid (bỏ qua danh sách), lặp lại được")
    ap.add_argument("--all-episodes", action="store_true",
                    help="lấy mọi episode từ 1 tới hiện tại (dùng khi nạp lại bão cũ)")
    ap.add_argument("--force", action="store_true", help="ghi lại kể cả phần đã có")
    a = ap.parse_args()
    run(a.date_from, a.date_to, a.event_id, a.all_episodes, a.force)


if __name__ == "__main__":
    main()
