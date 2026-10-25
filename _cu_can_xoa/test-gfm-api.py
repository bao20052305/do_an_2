# pip install gfw-api-python-client
# Chạy: đặt biến môi trường GFW_API_ACCESS_TOKEN trước (không ghi token vào code)
#   PowerShell: $env:GFW_API_ACCESS_TOKEN="token_moi_cua_ban"
import asyncio
import os

import gfwapiclient as gfw

gfw_client = gfw.Client(access_token="eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCIsImtpZCI6ImtpZEtleSJ9.eyJkYXRhIjp7Im5hbWUiOiJkZW1vIiwidXNlcklkIjo3MjAyNiwiYXBwbGljYXRpb25OYW1lIjoiZGVtbyIsImlkIjoxNTE3MCwidHlwZSI6InVzZXItYXBwbGljYXRpb24ifSwiaWF0IjoxNzkwNTc4MTMyLCJleHAiOjIxMDU5MzgxMzIsImF1ZCI6ImdmdyIsImlzcyI6ImdmdyJ9.NxhhsPsTnRIeu1z1GqVt7yOgHT2pNweI_HjO1V67Rqgz7vn4LVKurOQaQTSzzdanm2eInMdgGbroMTIUenSlIJFpRn7kUemHVxEoq-bCOZewQgFYjILTXowq47i-GjN-L5g7j1gYkMDQ4xxCnkmmBVevngjvz5AnqUIhREFmc9JnpqaE_gPjfPwSWe0oUo7RNFFdNTAyrE_lAnPLlTwHzW2K68u0Lvs1DzI6NOH6prt59Q_27j3FgTwY99bXs9loa70rKal3m15M8c25CILgFusb00xSQmv8Qx-dW0CqDPPWE8_xbyB2BLRAt9eOXq3shFh2YuM8BJvBnRJNnXM7b_S2dVnGhUX67m5meWW5RDeVcUS6uvGucyPBl5ZjWW5ojzGuqElJYRtiXNr3H3L6EpSLTaBVOsgOo4xAm_OquPCCBgF6BtslBunDyLrpuRBawDETqBljjwAMpn-rSpOX1UYotiZIK3cZiiHRxJuUMVDLvJMp9oKK21N_n5vdjaIq")

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

    df[cols + ["id"]].to_csv(f"gfw_{name}_vietnam.csv", index=False, encoding="utf-8-sig")
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