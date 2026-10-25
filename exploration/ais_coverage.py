# Đo độ phủ aisstream trên Biển Đông theo từng tiểu vùng
# Chạy: điền AISSTREAM_API_KEY vào file .env (xem .env.example)
import asyncio, csv, json, os, time
from collections import Counter, defaultdict
from pathlib import Path

import websockets
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]   # thư mục gốc đồ án
load_dotenv(ROOT / ".env")
OUT_DIR = ROOT / "data" / "exploration" / "ais"

API_KEY = os.environ["AISSTREAM_API_KEY"]
BBOX = [[[25.0, 99.0], [0.0, 122.0]]]      # toàn Biển Đông
DURATION_MIN = 30                          # thời gian đo (phút)
REPORT_EVERY = 60                          # in tóm tắt mỗi 60 giây

# Tiểu vùng: (tên, vĩ độ min, vĩ độ max, kinh độ min, kinh độ max), xét theo thứ tự
REGIONS = [
    ("Vinh Bac Bo",           17.0, 22.0, 105.5, 110.0),
    ("HK - Quang Dong",       20.0, 23.5, 110.0, 117.5),
    ("Eo Dai Loan",           21.0, 25.0, 117.5, 122.0),
    ("Hoang Sa & giua bien",  13.0, 17.5, 110.0, 114.0),
    ("Ven bo Nam Trung Bo-Nam Bo", 8.0, 13.0, 105.0, 111.0),
    ("Truong Sa",              6.0, 12.0, 111.0, 117.0),
    ("Tay Philippines",        7.0, 20.0, 117.0, 122.0),
    ("Vinh Thai Lan",          6.0, 13.5,  99.0, 105.0),
    ("Singapore - Malaysia",   0.0,  6.0,  99.0, 110.0),
    ("Bac Borneo",             0.0,  7.0, 110.0, 117.0),
]

def find_region(lat, lon):
    for name, la1, la2, lo1, lo2 in REGIONS:
        if la1 <= lat <= la2 and lo1 <= lon <= lo2:
            return name
    return "Khac"

msg_count = Counter()                 # số bản tin theo tiểu vùng
ships = defaultdict(set)              # MMSI khác nhau theo tiểu vùng
msg_types = Counter()                 # số bản tin theo loại
last_pos = {}                         # vị trí cuối của mỗi tàu

def print_summary(elapsed):
    print(f"\n=== Sau {elapsed/60:.1f} phút | tổng {len(last_pos)} tàu ===")
    for name, *_ in REGIONS + [("Khac",)]:
        print(f"{name:30s} {len(ships[name]):5d} tàu  {msg_count[name]:7d} bản tin")
    print("Loại bản tin:", dict(msg_types.most_common()))

def save_results():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_DIR / "ais_last_positions.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["mmsi", "name", "msg_type", "lat", "lon", "region", "time_utc"])
        for mmsi, row in last_pos.items():
            w.writerow([mmsi, *row])
    with open(OUT_DIR / "ais_coverage_summary.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["region", "unique_ships", "messages"])
        for name, *_ in REGIONS + [("Khac",)]:
            w.writerow([name, len(ships[name]), msg_count[name]])
    print(f"\nĐã lưu ais_last_positions.csv và ais_coverage_summary.csv vào {OUT_DIR}")

async def main():
    start = time.time()
    end = start + DURATION_MIN * 60
    next_report = start + REPORT_EVERY
    try:
        async with websockets.connect(
            "wss://stream.aisstream.io/v0/stream", compression="deflate"
        ) as ws:
            await ws.send(json.dumps({"APIKey": API_KEY, "BoundingBoxes": BBOX}))
            print(f"Đang đo độ phủ trong {DURATION_MIN} phút...")
            while time.time() < end:
                try:
                    payload = await asyncio.wait_for(ws.recv(), timeout=5)
                except asyncio.TimeoutError:
                    payload = None          # không có bản tin trong 5s, vẫn tiếp tục

                if payload:
                    data = json.loads(payload)
                    mtype = data.get("MessageType", "?")
                    msg_types[mtype] += 1
                    meta = data.get("MetaData", {})
                    lat, lon = meta.get("latitude"), meta.get("longitude")
                    if lat is not None and lon is not None and not (lat == 0 and lon == 0):
                        region = find_region(lat, lon)
                        mmsi = meta.get("MMSI")
                        msg_count[region] += 1
                        ships[region].add(mmsi)
                        last_pos[mmsi] = (str(meta.get("ShipName", "")).strip(), mtype,
                                          lat, lon, region, meta.get("time_utc"))

                if time.time() >= next_report:
                    print_summary(time.time() - start)
                    next_report += REPORT_EVERY
    except websockets.exceptions.ConnectionClosed as e:
        print(f"Kết nối bị đóng: {e}")
    finally:                                # luôn lưu kết quả, kể cả khi bấm Ctrl+C
        print_summary(time.time() - start)
        save_results()

try:
    asyncio.run(main())
except KeyboardInterrupt:
    print("Đã dừng.")