"""Sinh lưới H3 cho Biển Đông và vùng xung quanh, gán vùng cho từng ô, ghi 3 bảng gold:
    gold.dim_region        region_id, region_name, region_type, country, area_km2
    gold.dim_h3_index      h3_index, resolution, center_lat, center_lon, area_km2, region_id
    gold.dim_region_alias  alias, region_id
Cấu hình: batch/reference/regions.yaml. Ba bảng được ghi đè toàn bộ mỗi lần chạy.

Chạy:
  python batch/reference/build_h3_regions.py              (ghi gold, cần docker compose đang chạy)
  python batch/reference/build_h3_regions.py --dry-run    (chỉ kiểm tra, xuất bản đồ HTML)
Bản đồ kiểm tra: data/reference/h3_regions_map.html
"""
import argparse
import json
import sys
import urllib.request
from pathlib import Path

import h3
import numpy as np
import pyarrow as pa
import shapely
import yaml
from shapely.geometry import box, shape

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from batch.reference.region_resolver import CONFIG, RegionResolver, norm, strip_words  # noqa: E402

OUT_DIR = ROOT / "data" / "reference"

# điểm mẫu: (tên, vĩ độ, kinh độ, vùng mong đợi)
SAMPLE_POINTS = [
    ("Đảo Phú Lâm", 16.83, 112.33, "Đặc khu Hoàng Sa"),
    ("Đảo Trường Sa Lớn", 8.65, 111.92, "Đặc khu Trường Sa"),
    ("Vịnh Hạ Long", 20.9, 107.2, "Bắc vịnh Bắc Bộ"),
    ("Ngoài khơi Hồng Kông", 21.5, 114.5, "Bắc Biển Đông"),
    ("Hà Nội", 21.03, 105.85, "Đất liền Việt Nam"),
    ("Manila", 14.6, 121.0, "Quần đảo Philippines"),
    ("Hải Khẩu", 19.9, 110.3, "Đảo Hải Nam"),
    ("Ngoài khơi Nha Trang", 12.2, 109.6, "Gia Lai - Khánh Hòa"),
    ("Giữa vịnh Thái Lan", 10.0, 102.0, "Vịnh Thái Lan"),
]
SAMPLE_BULLETIN = {
    "Khu vực vịnh Bắc Bộ (05-06/10)": {"Bắc vịnh Bắc Bộ", "Nam vịnh Bắc Bộ"},
    "khu vực Nam Quảng Trị - Quảng Ngãi (06-08/10)": {"Nam Quảng Trị - Quảng Ngãi"},
    "khu vực Gia Lai - Cà Mau (07-08/10)": {"Gia Lai - Khánh Hòa", "Lâm Đồng - Cà Mau"},
    "khu vực Bắc Biển Đông (05-08/10)": {"Bắc Biển Đông"},
    "khu vực Giữa Biển Đông và đặc khu Hoàng Sa (07-08/10)": {"Giữa Biển Đông", "Đặc khu Hoàng Sa"},
}


def in_box(lat, lon, b) -> np.ndarray:
    return (lat >= b["lat"][0]) & (lat < b["lat"][1]) & (lon >= b["lon"][0]) & (lon < b["lon"][1])


def load_countries(cfg: dict) -> dict:
    """Đa giác đất liền theo mã ADM0_A3, cắt theo khung lưới. Tải Natural Earth một lần rồi lưu lại."""
    path = OUT_DIR / "ne_10m_admin_0_countries.geojson"
    if not path.exists():
        print(f"Tải bản đồ biên giới về {path} ...")
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(cfg["natural_earth_url"], path)
    g = cfg["grid_bbox"]
    frame = box(g["lon_min"] - 1, g["lat_min"] - 1, g["lon_max"] + 1, g["lat_max"] + 1)
    out = {}
    for f in json.loads(path.read_text(encoding="utf-8"))["features"]:
        code = f["properties"]["ADM0_A3"]
        if code in cfg["countries"]:
            geom = shape(f["geometry"]).intersection(frame)
            if not geom.is_empty:
                out[code] = shapely.union(out[code], geom) if code in out else geom
    return out


def build(cfg: dict):
    g, res = cfg["grid_bbox"], cfg["resolution"]

    # ---- 1. sinh ô
    poly = h3.LatLngPoly([(g["lat_min"], g["lon_min"]), (g["lat_min"], g["lon_max"]),
                          (g["lat_max"], g["lon_max"]), (g["lat_max"], g["lon_min"])])
    cells = sorted(h3.polygon_to_cells_experimental(poly, res, contain="overlap"))
    centers = np.array([h3.cell_to_latlng(c) for c in cells])
    lat, lon = centers[:, 0], centers[:, 1]
    print(f"{len(cells)} ô H3 cấp {res}")

    # ---- 2. danh sách vùng (thứ tự = region_id)
    regions = []                                   # dict: name, type, country
    def region_id(name, rtype, country):
        for i, r in enumerate(regions):
            if r["region_name"] == name:
                return i + 1
        regions.append({"region_name": name, "region_type": rtype, "country": country})
        return len(regions)

    assigned = np.zeros(len(cells), dtype=np.int32)   # 0 = chưa gán

    def assign(mask, rid):
        m = mask & (assigned == 0)
        assigned[m] = rid

    sea_regions = sorted(cfg["regions"], key=lambda r: r["priority"])
    for r in sea_regions:                          # đăng ký trước để region_id ổn định
        region_id(r["name"], r["type"], r["country"])

    # vùng xét trước đất liền (quần đảo xa bờ)
    for r in sea_regions:
        if r["priority"] < cfg["land_priority"] and not r.get("fallback"):
            assign(in_box(lat, lon, r["box"]), region_id(r["name"], r["type"], r["country"]))

    # đất liền theo nước, đảo tách riêng
    countries = load_countries(cfg)
    for code, geom in countries.items():
        c = cfg["countries"][code]
        on_land = shapely.contains_xy(geom, lon, lat)
        for isl in cfg.get("islands", []):
            if isl["of"] == code:
                assign(on_land & in_box(lat, lon, isl["box"]),
                       region_id(isl["name"], isl["type"], isl["country"]))
        assign(on_land, region_id(c["name"], c["type"], c["country"]))

    # vùng biển theo thứ tự ưu tiên, cuối cùng là fallback
    for r in sea_regions:
        rid = region_id(r["name"], r["type"], r["country"])
        if r.get("fallback"):
            assign(np.ones(len(cells), dtype=bool), rid)
        elif r["priority"] >= cfg["land_priority"]:
            assign(in_box(lat, lon, r["box"]), rid)

    # ---- 3. bảng
    area = np.array([h3.cell_area(c, unit="km^2") for c in cells])
    dim_h3 = {
        "h3_index": [h3.str_to_int(c) for c in cells],
        "resolution": [res] * len(cells),
        "center_lat": lat.round(5).tolist(),
        "center_lon": lon.round(5).tolist(),
        "area_km2": area.round(3).tolist(),
        "region_id": assigned.tolist(),
    }
    for i, r in enumerate(regions):
        r["region_id"] = i + 1
        r["area_km2"] = round(float(area[assigned == i + 1].sum()), 1)

    resolver = RegionResolver(cfg)
    name_to_id = {r["region_name"]: r["region_id"] for r in regions}
    alias_rows = [{"alias": a, "region_id": name_to_id[n]}
                  for a, names in sorted(resolver.aliases.items()) for n in sorted(names)]
    return cells, assigned, regions, dim_h3, alias_rows, resolver


def check(cells, assigned, regions, resolver):
    errors = []
    if (assigned == 0).any():
        errors.append(f"{int((assigned == 0).sum())} ô chưa có vùng")
    counts = np.bincount(assigned, minlength=len(regions) + 1)
    for r in regions:
        if counts[r["region_id"]] == 0:
            errors.append(f"vùng '{r['region_name']}' không có ô nào")
    by_id = {r["region_id"]: r["region_name"] for r in regions}
    idx = {c: i for i, c in enumerate(cells)}
    res = h3.get_resolution(cells[0])
    for name, la, lo, want in SAMPLE_POINTS:
        got = by_id.get(int(assigned[idx[h3.latlng_to_cell(la, lo, res)]]))
        if got != want:
            errors.append(f"{name} ({la}, {lo}) ra '{got}', mong đợi '{want}'")
    for text, want in SAMPLE_BULLETIN.items():
        got, unresolved = resolver.resolve(text)
        if got != want or unresolved:
            errors.append(f"bản tin '{text}' ra {got} {unresolved}, mong đợi {want}")
    print("\nSố ô theo vùng:")
    for r in regions:
        print(f"  {r['region_id']:3d} {r['region_name']:40s} {r['region_type']:9s} "
              f"{counts[r['region_id']]:6d} ô  {r['area_km2']:>11,.0f} km²  {r['country']}")
    return errors


def write_map(cells, assigned, regions):
    import folium
    m = folium.Map(location=[14, 113], zoom_start=5, tiles="OpenStreetMap")
    palette = ["#e6194b", "#3cb44b", "#ffe119", "#4363d8", "#f58231", "#911eb4", "#46f0f0",
               "#f032e6", "#bcf60c", "#fabebe", "#008080", "#e6beff", "#9a6324", "#800000",
               "#aaffc3", "#808000", "#000075", "#808080"]
    cells = np.array(cells)
    for r in regions:
        cs = cells[assigned == r["region_id"]].tolist()
        if not cs:
            continue
        geo = h3.cells_to_geo(cs)
        folium.GeoJson(
            {"type": "Feature", "geometry": geo, "properties": {}},
            style_function=lambda _, c=palette[r["region_id"] % len(palette)]: {
                "fillColor": c, "color": c, "weight": 0.5, "fillOpacity": 0.45},
            tooltip=f"{r['region_name']} ({r['region_type']}, {r['country']})",
        ).add_to(m)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / "h3_regions_map.html"
    m.save(str(path))
    print(f"\nBản đồ kiểm tra: {path}")


def write_gold(regions, dim_h3, alias_rows):
    from pyiceberg.schema import Schema
    from pyiceberg.types import DoubleType, IntegerType, LongType, NestedField, StringType

    from ingestion.common.lakehouse import ensure_namespace, ensure_table
    from pyiceberg.io.pyarrow import schema_to_pyarrow
    from pyiceberg.partitioning import UNPARTITIONED_PARTITION_SPEC

    tables = {
        "gold.dim_region": (Schema(
            NestedField(1, "region_id", IntegerType()),
            NestedField(2, "region_name", StringType()),
            NestedField(3, "region_type", StringType()),
            NestedField(4, "country", StringType()),
            NestedField(5, "area_km2", DoubleType())), regions),
        "gold.dim_h3_index": (Schema(
            NestedField(1, "h3_index", LongType()),
            NestedField(2, "resolution", IntegerType()),
            NestedField(3, "center_lat", DoubleType()),
            NestedField(4, "center_lon", DoubleType()),
            NestedField(5, "area_km2", DoubleType()),
            NestedField(6, "region_id", IntegerType())), dim_h3),
        "gold.dim_region_alias": (Schema(
            NestedField(1, "alias", StringType()),
            NestedField(2, "region_id", IntegerType())), alias_rows),
    }
    ensure_namespace("gold")
    for name, (schema, data) in tables.items():
        table = ensure_table(name, schema, UNPARTITIONED_PARTITION_SPEC)
        arrow_schema = schema_to_pyarrow(table.schema())
        df = (pa.Table.from_pydict(data, schema=arrow_schema) if isinstance(data, dict)
              else pa.Table.from_pylist(data, schema=arrow_schema))
        table.overwrite(df)
        print(f"Đã ghi đè {name}: {df.num_rows} dòng")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="chỉ kiểm tra và xuất bản đồ, không ghi gold")
    a = ap.parse_args()

    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    cells, assigned, regions, dim_h3, alias_rows, resolver = build(cfg)
    errors = check(cells, assigned, regions, resolver)
    write_map(cells, assigned, regions)
    if errors:
        print("\nLỖI KIỂM TRA:\n  " + "\n  ".join(errors))
        sys.exit(1)
    print("\nKiểm tra: đạt")
    if not a.dry_run:
        write_gold(regions, dim_h3, alias_rows)


if __name__ == "__main__":
    main()
