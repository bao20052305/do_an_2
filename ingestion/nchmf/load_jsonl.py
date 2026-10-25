"""Nạp một lần các file JSONL cũ (data/bronze/nchmf/*.jsonl) của crawler bản trước vào bronze.nchmf_bulletin.
Bài đã có (theo post_id) thì bỏ qua, nên chạy lại không sinh trùng.

Chạy:  python ingestion/nchmf/load_jsonl.py
"""
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ingestion.common.bronze_schemas import NCHMF_BULLETIN  # noqa: E402
from ingestion.common.lakehouse import ROOT, append, existing_keys, load_table, new_run_id  # noqa: E402
from ingestion.nchmf.crawl_nchmf_news import post_id_of, sha256  # noqa: E402

JSONL_DIR = ROOT / "data" / "bronze" / "nchmf"


def parse_ts(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


def main():
    table = load_table(NCHMF_BULLETIN)
    run_id = new_run_id("nchmf-jsonl")
    done = {pid for (pid,) in existing_keys(table, ["post_id"])}
    rows = []
    for path in sorted(JSONL_DIR.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            pid = post_id_of(rec.get("url"))
            if pid is None or pid in done:
                continue
            rows.append({
                **rec,
                "post_id": pid,
                "listed_at_local": parse_ts(rec.get("listed_at_local")),
                "issued_at_utc": parse_ts(rec.get("issued_at_utc")),
                "crawl_at": parse_ts(rec.get("crawl_at")),
                "content_html": None,                       # bản cũ không lưu HTML
                "content_sha256": sha256(rec.get("content_text")),
                "run_id": run_id,
            })
            done.add(pid)
    n = append(table, rows)
    print(f"Đã nạp {n} bài từ {JSONL_DIR} vào {NCHMF_BULLETIN}")


if __name__ == "__main__":
    main()
