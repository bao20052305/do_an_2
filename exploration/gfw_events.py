# pip install gfw-api-python-client
# Chạy: điền GFW_API_ACCESS_TOKEN vào file .env (xem .env.example), không ghi token vào code
import asyncio
import os
from pathlib import Path

import gfwapiclient as gfw
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]   # thư mục gốc đồ án
load_dotenv(ROOT / ".env")
OUT_DIR = ROOT / "data" / "exploration" / "gfw" / "events"
OUT_DIR.mkdir(parents=True, exist_ok=True)

gfw_client = gfw.Client(access_token=os.environ["GFW_API_ACCESS_TOKEN"])

# Khoảng thời gian thử: quanh bão Yagi (đổ bộ Quảng Ninh - Hải Phòng ngày 7/9/2024)
START_DATE = "2026-09-26"
END_DATE = "2026-09-28"

# Các loại sự kiện muốn lấy (mỗi loại là một dataset riêng)
EVENT_DATASETS = {
    "port_visit": "public-global-port-visits-events:latest",
    "fishing": "public-global-fishing-events:latest",
    "encounter": "public-global-encounters-events:latest",
}


def get_field(obj, key):
    """Lấy trường từ dict hoặc object (cột trong DataFrame có thể là một trong hai)."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


async def find_vietnam_eez():
    """Tra ID vùng đặc quyền kinh tế (EEZ) của Việt Nam qua Reference Data API."""
    result = await gfw_client.references.get_eez_regions()
    df = result.df()
    vn = df[(df["iso3"] == "VNM") | df["label"].str.contains("Viet", case=False, na=False)]
    print("Các vùng EEZ liên quan đến Việt Nam:")
    print(vn[["id", "label", "iso3"]].to_string(index=False))
    # Chọn vùng EEZ chính của Việt Nam (không phải vùng tranh chấp chồng lấn)
    main = vn[(vn["iso3"] == "VNM") & ~vn["label"].str.contains("Overlapping|Joint", case=False)]
    return str(main.iloc[0]["id"])


async def fetch_events(eez_id, name, dataset):
    result = await gfw_client.events.get_all_events(
        datasets=[dataset],
        start_date=START_DATE,
        end_date=END_DATE,
        region={"dataset": "public-eez-areas", "id": eez_id},
        limit=200,  # giới hạn để thử nghiệm, bỏ đi khi lấy thật
    )
    df = result.df()
    if df.empty:
        print(f"\n[{name}] Không có sự kiện nào.")
        return df

    # Tách các trường lồng nhau ra cột phẳng cho dễ đọc
    df["lat"] = df["position"].apply(lambda p: get_field(p, "lat"))
    df["lon"] = df["position"].apply(lambda p: get_field(p, "lon"))
    df["vessel_name"] = df["vessel"].apply(lambda v: get_field(v, "name"))
    df["mmsi"] = df["vessel"].apply(lambda v: get_field(v, "ssvid"))
    df["flag"] = df["vessel"].apply(lambda v: get_field(v, "flag"))

    cols = ["start", "end", "type", "vessel_name", "mmsi", "flag", "lat", "lon"]
    print(f"\n[{name}] {len(df)} sự kiện. Ví dụ:")
    print(df[cols].head(10).to_string(index=False))

    df[cols + ["id"]].to_csv(OUT_DIR / f"gfw_{name}_vietnam.csv", index=False, encoding="utf-8-sig")
    return df


async def main():
    eez_id = await find_vietnam_eez()
    print(f"\nDùng EEZ id = {eez_id}, từ {START_DATE} đến {END_DATE}")

    for name, dataset in EVENT_DATASETS.items():
        try:
            await fetch_events(eez_id, name, dataset)
        except Exception as e:  # in lỗi từng loại, không dừng cả chương trình
            print(f"\n[{name}] Lỗi: {e}")


asyncio.run(main())