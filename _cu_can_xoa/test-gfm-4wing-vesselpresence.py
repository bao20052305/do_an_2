# Truy vấn 4Wings: lớp hiện diện tàu (AIS)
# Chạy: $env:GFW_API_ACCESS_TOKEN="token_moi"   (PowerShell) - KHÔNG ghi token vào code
import asyncio
import os

import gfwapiclient as gfw

gfw_client = gfw.Client(access_token="eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCIsImtpZCI6ImtpZEtleSJ9.eyJkYXRhIjp7Im5hbWUiOiJkZW1vIiwidXNlcklkIjo3MjAyNiwiYXBwbGljYXRpb25OYW1lIjoiZGVtbyIsImlkIjoxNTE3MCwidHlwZSI6InVzZXItYXBwbGljYXRpb24ifSwiaWF0IjoxNzkwNTc4MTMyLCJleHAiOjIxMDU5MzgxMzIsImF1ZCI6ImdmdyIsImlzcyI6ImdmdyJ9.NxhhsPsTnRIeu1z1GqVt7yOgHT2pNweI_HjO1V67Rqgz7vn4LVKurOQaQTSzzdanm2eInMdgGbroMTIUenSlIJFpRn7kUemHVxEoq-bCOZewQgFYjILTXowq47i-GjN-L5g7j1gYkMDQ4xxCnkmmBVevngjvz5AnqUIhREFmc9JnpqaE_gPjfPwSWe0oUo7RNFFdNTAyrE_lAnPLlTwHzW2K68u0Lvs1DzI6NOH6prt59Q_27j3FgTwY99bXs9loa70rKal3m15M8c25CILgFusb00xSQmv8Qx-dW0CqDPPWE8_xbyB2BLRAt9eOXq3shFh2YuM8BJvBnRJNnXM7b_S2dVnGhUX67m5meWW5RDeVcUS6uvGucyPBl5ZjWW5ojzGuqElJYRtiXNr3H3L6EpSLTaBVOsgOo4xAm_OquPCCBgF6BtslBunDyLrpuRBawDETqBljjwAMpn-rSpOX1UYotiZIK3cZiiHRxJuUMVDLvJMp9oKK21N_n5vdjaIq")

LAYERS = {
    "presence": {
        "dataset": "public-global-presence:latest",   # mọi loại tàu, mọi hoạt động (AIS)
        "group_by": "VESSEL_ID",                       # biết được từng tàu
        "value": "hours",                              # số giờ có mặt trong ô
        "start": "2026-09-27", "end": "2026-09-28",
        "temporal": "DAILY",
    }
}
LAYERS_TO_RUN = ["presence"]

# (tên, vĩ độ min, vĩ độ max, kinh độ min, kinh độ max)
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

# Nên chạy thử 1 vùng trước: lớp presence ở vùng đông tàu (Singapore, HK) có thể rất nhiều dòng
SELECTED = ["Vinh Bac Bo", "HK - Quang Dong", "Eo Dai Loan", "Hoang Sa & giua bien"]
SPATIAL_RES = "LOW"   # LOW ~0.1 độ, HIGH ~0.01 độ


def bbox_to_geojson(lat_min, lat_max, lon_min, lon_max):
    """GeoJSON dùng thứ tự [kinh độ, vĩ độ] và đa giác phải khép kín."""
    return {
        "type": "Polygon",
        "coordinates": [[
            [lon_min, lat_min], [lon_max, lat_min], [lon_max, lat_max],
            [lon_min, lat_max], [lon_min, lat_min],
        ]],
    }


async def create_report(layer, geometry):
    kwargs = dict(
        datasets=[layer["dataset"]],
        spatial_resolution=SPATIAL_RES,
        temporal_resolution=layer["temporal"],
        start_date=layer["start"],
        end_date=layer["end"],
    )
    if layer["group_by"]:
        kwargs["group_by"] = layer["group_by"]
    try:
        return await gfw_client.fourwings.create_report(geojson=geometry, **kwargs)
    except Exception as e:
        if "geojson" in str(e).lower():
            feature = {"type": "Feature", "properties": {}, "geometry": geometry}
            return await gfw_client.fourwings.create_report(geojson=feature, **kwargs)
        raise


def summarize_presence(df):
    cols = [c for c in ["date", "ship_name", "mmsi", "flag", "vessel_type",
                        "hours", "lat", "lon", "vessel_id"] if c in df.columns]
    print(f"{len(df)} dòng, {df['vessel_id'].nunique()} tàu khác nhau")
    if "vessel_type" in df.columns:
        print("\nTổng giờ hiện diện theo loại tàu:")
        print(df.groupby("vessel_type", dropna=False)["hours"].sum()
                .sort_values(ascending=False).round(1).to_string())
    print("\n15 dòng nhiều giờ nhất:")
    print(df[cols].sort_values("hours", ascending=False).head(15).to_string(index=False))


async def main():
    for layer_name in LAYERS_TO_RUN:
        layer = LAYERS[layer_name]
        for name, la1, la2, lo1, lo2 in REGIONS:
            if SELECTED and name not in SELECTED:
                continue
            print(f"\n=== [{layer_name}] {name} ({layer['start']} -> {layer['end']}) ===")
            try:
                result = await create_report(layer, bbox_to_geojson(la1, la2, lo1, lo2))
            except Exception as e:
                print(f"Lỗi: {e}")
                continue
            df = result.df()
            if df.empty:
                print("Không có dữ liệu.")
                continue

            summarize_presence(df)

            fname = f"4wings_{layer_name}_" + name.replace(" ", "_").replace("&", "va") + ".csv"
            df.to_csv(fname, index=False, encoding="utf-8-sig")
            print("Đã lưu", fname)


asyncio.run(main())