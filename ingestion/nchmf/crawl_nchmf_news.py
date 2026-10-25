"""
Crawl bản tin trong mục Hải văn của nchmf.gov.vn (Sóng, Thủy triều) và ghi vào bronze.nchmf_bulletin.
Mỗi lần chạy: với mỗi chuyên mục, lấy bài mới nhất (hoặc mọi bài đang hiện trên trang với
--all-on-page) -> tải trang bài -> ghi 1 dòng mỗi bài.
Chống trùng theo post_id lấy từ URL: bài đã có trong bảng thì không tải lại.

Cài đặt:  pip install -r requirements.txt
Chạy:     python ingestion/nchmf/crawl_nchmf_news.py
          python ingestion/nchmf/crawl_nchmf_news.py --all-on-page   (nạp lần đầu)
          python ingestion/nchmf/crawl_nchmf_news.py --force         (ghi lại kể cả bài đã có)
"""
import argparse
import hashlib
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ingestion.common.bronze_schemas import NCHMF_BULLETIN  # noqa: E402
from ingestion.common.lakehouse import append, existing_keys, load_table, new_run_id  # noqa: E402

CRAWLER_VERSION = "nchmf-haivan/0.2"
VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
HEADERS = {"User-Agent": "do-an-canh-bao-bao/0.1 (student project; contact: your_email@example.com)"}

CATEGORIES = {
    "song": {
        "url": "https://nchmf.gov.vn/kttvsite/vi-VN/1/song-25-15.html",
        "keywords": ["sóng"],
    },
    "thuy_trieu": {
        "url": "https://nchmf.gov.vn/kttvsite/vi-VN/1/thuy-trieu-23-15.html",
        "keywords": ["thủy triều", "thuỷ triều"],
    },
}


LIST_DT_RE = re.compile(r"\(\s*(\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}:\d{2})\s*\)")
POST_ID_RE = re.compile(r"-post(\d+)\.html")
ISSUED_RE = re.compile(
    r"phát lúc\s*(\d{1,2})\s*h\s*(\d{2})?\s*(?:phút)?\s*ngày\s*(\d{1,2})/(\d{1,2})/(\d{4})", re.I)


# ---------------------------------------------------------------- tiện ích
def norm(s: str) -> str:
    """Chuẩn hóa unicode (NFC), gộp khoảng trắng, chữ thường: để so sánh chuỗi tiếng Việt."""
    return " ".join(unicodedata.normalize("NFC", s or "").split()).lower()


def get(url: str, retries: int = 3) -> requests.Response:
    for i in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            r.encoding = r.apparent_encoding or "utf-8"
            return r
        except requests.RequestException as e:
            if i == retries - 1:
                raise
            print(f"  lỗi {e}, thử lại sau {2 ** i}s")
            time.sleep(2 ** i)


def local_to_utc(d: datetime) -> datetime:
    return d.replace(tzinfo=VN_TZ).astimezone(timezone.utc)


def post_id_of(url: str) -> int | None:
    m = POST_ID_RE.search(url or "")
    return int(m.group(1)) if m else None


def sha256(text: str | None) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- bước 1: trang chuyên mục
def list_items(cat: str) -> list[dict]:
    """Mọi bài đúng loại đang hiện trên trang chuyên mục, mới nhất trước."""
    cfg = CATEGORIES[cat]
    soup = BeautifulSoup(get(cfg["url"]).text, "html.parser")
    items = {}
    for a in soup.find_all("a", href=True):
        href = urljoin(cfg["url"], a["href"])
        m_id = POST_ID_RE.search(href)
        text = " ".join(a.get_text(" ").split())
        if not m_id or not text:
            continue
        if not any(k in norm(text) for k in cfg["keywords"]):      # lọc đúng loại bản tin
            continue
        m_dt = LIST_DT_RE.search(text)
        listed = datetime.strptime(" ".join(m_dt.groups()), "%d/%m/%Y %H:%M") if m_dt else None
        title = re.sub(r"\bnew\s*$", "", LIST_DT_RE.sub("", text)).strip()
        post_id = int(m_id.group(1))
        items[post_id] = {"post_id": post_id, "url": href, "title": title, "listed_local": listed}
    # mới nhất: theo thời gian hiển thị, nếu thiếu thì theo post_id (id lớn hơn là bài mới hơn)
    return sorted(items.values(), key=lambda x: (x["listed_local"] or datetime.min, x["post_id"]),
                  reverse=True)


# ---------------------------------------------------------------- bước 2: trang bài viết
def parse_article(item: dict, cat: str, run_id: str) -> dict:
    resp = get(item["url"])
    soup = BeautifulSoup(resp.text, "html.parser")

    # tìm tiêu đề bài trong trang (thẻ h1-h3 khớp với tiêu đề ở trang chuyên mục)
    want = norm(item["title"])[:40]
    heading = next((h for h in soup.find_all(["h1", "h2", "h3"])
                    if want and norm(h.get_text()).startswith(want)), None)
    container = heading.find_parent("div") if heading else soup.body

    text = container.get_text("\n")
    text = text.split("Tin mới")[0]                               # bỏ khối "Tin mới" phía sau
    lines = [" ".join(l.split()) for l in text.splitlines()]
    content_text = "\n".join(l for l in lines if l)
    if heading:                                                    # bỏ dòng tiêu đề lặp lại
        content_text = content_text.replace(" ".join(heading.get_text().split()), "", 1).strip()

    m = ISSUED_RE.search(content_text)
    issued_local = None
    if m:
        hh, mm, d, mo, y = m.groups()
        issued_local = datetime(int(y), int(mo), int(d), int(hh), int(mm or 0))
    issued_local = issued_local or item["listed_local"]

    attachments = sorted({urljoin(item["url"], a["href"]) for a in container.find_all("a", href=True)
                          if re.search(r"\.(pdf|docx?|xlsx?)$", a["href"], re.I)})

    return {
        "post_id": item["post_id"],
        "source": "nchmf",
        "category": cat,
        "url": item["url"],
        "title": item["title"],
        "listed_at_local": item["listed_local"],
        "issued_at_text": m.group(0) if m else None,
        "issued_at_utc": local_to_utc(issued_local) if issued_local else None,
        "content_text": content_text,
        "content_html": str(container),
        "content_sha256": sha256(content_text),
        "attachment_urls": attachments,
        "http_status": resp.status_code,
        "crawl_at": datetime.now(timezone.utc),
        "crawler_version": CRAWLER_VERSION,
        "parse_warning": None if heading and m else
                         ("khong_tim_thay_tieu_de" if not heading else "khong_tim_thay_thoi_diem_phat"),
        "run_id": run_id,
    }


# ---------------------------------------------------------------- chạy
def main(force: bool = False, all_on_page: bool = False):
    table = load_table(NCHMF_BULLETIN)
    run_id = new_run_id("nchmf")
    done = {pid for (pid,) in existing_keys(table, ["post_id"])}
    rows = []

    for cat in CATEGORIES:
        print(f"[{cat}] đọc trang chuyên mục...")
        items = list_items(cat)
        if not items:
            print(f"[{cat}] không tìm thấy bài nào, kiểm tra lại bộ lọc từ khóa / cấu trúc trang")
            continue
        for item in (items if all_on_page else items[:1]):
            if not force and item["post_id"] in done:          # đã có -> không tải trang bài
                print(f"[{cat}] đã có: {item['title']}")
                continue
            time.sleep(1)                                       # lịch sự với máy chủ
            rec = parse_article(item, cat, run_id)
            rows.append(rec)
            done.add(item["post_id"])
            print(f"[{cat}] mới: {item['title']}  (cảnh báo: {rec['parse_warning']})")

    n = append(table, rows)
    print(f"Đã ghi {n} dòng vào {NCHMF_BULLETIN} (run_id={run_id})")
    return n


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="ghi lại kể cả bài đã có")
    ap.add_argument("--all-on-page", action="store_true",
                    help="lấy mọi bài đang hiện trên trang chuyên mục, không chỉ bài mới nhất")
    a = ap.parse_args()
    main(a.force, a.all_on_page)
