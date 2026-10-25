"""
Crawl bản tin MỚI NHẤT trong mục Hải văn của nchmf.gov.vn: Sóng và Thủy triều.
Mỗi lần chạy: với mỗi chuyên mục, lấy bài mới nhất -> tải trang bài -> tạo 1 bản ghi bronze.
Bài có tiêu đề đã crawl trước đó thì bỏ qua (tiêu đề chứa khoảng ngày nên mỗi bản tin một tiêu đề).

Cài đặt:  pip install requests beautifulsoup4
Chạy:     python crawl_nchmf_haivan.py            (ghi ra ./bronze_nchmf/<ngày>.jsonl)
          python crawl_nchmf_haivan.py --force    (ghi lại kể cả khi đã thấy)
"""
import argparse
import json
import re
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

CRAWLER_VERSION = "nchmf-haivan/0.1"
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

OUT_DIR = Path("bronze_nchmf")
STATE_FILE = OUT_DIR / "_seen_titles.json"   # danh sách tiêu đề đã crawl (đã chuẩn hóa)

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


def local_to_utc(d: datetime) -> str:
    return d.replace(tzinfo=VN_TZ).astimezone(timezone.utc).isoformat()


# ---------------------------------------------------------------- bước 1: trang chuyên mục
def find_latest(cat: str) -> dict | None:
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
    if not items:
        return None
    # mới nhất: theo thời gian hiển thị, nếu thiếu thì theo post_id (id lớn hơn là bài mới hơn)
    return max(items.values(), key=lambda x: (x["listed_local"] or datetime.min, x["post_id"]))


# ---------------------------------------------------------------- bước 2: trang bài viết
def parse_article(item: dict, cat: str) -> dict:
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
        "source": "nchmf",
        "category": cat,
        "url": item["url"],
        "title": item["title"],
        "listed_at_local": item["listed_local"].isoformat() if item["listed_local"] else None,
        "issued_at_text": m.group(0) if m else None,
        "issued_at_utc": local_to_utc(issued_local) if issued_local else None,
        "content_text": content_text,
        "attachment_urls": attachments,
        "http_status": resp.status_code,
        "crawl_at": datetime.now(timezone.utc).isoformat(),
        "crawler_version": CRAWLER_VERSION,
        "parse_warning": None if heading and m else
                         ("khong_tim_thay_tieu_de" if not heading else "khong_tim_thay_thoi_diem_phat"),
    }


# ---------------------------------------------------------------- chạy
def main(force: bool = False):
    OUT_DIR.mkdir(exist_ok=True)
    seen = set(json.loads(STATE_FILE.read_text(encoding="utf-8"))) if STATE_FILE.exists() else set()
    out_file = OUT_DIR / f"{datetime.now(VN_TZ):%Y-%m-%d}.jsonl"

    for cat in CATEGORIES:
        print(f"[{cat}] đọc trang chuyên mục...")
        item = find_latest(cat)
        if not item:
            print(f"[{cat}] không tìm thấy bài nào, kiểm tra lại bộ lọc từ khóa / cấu trúc trang")
            continue
        print(f"[{cat}] mới nhất: {item['title']}")
        key = norm(item["title"])
        if not force and key in seen:                              # đã crawl -> không tải trang bài
            print(f"[{cat}] đã có, bỏ qua")
            continue
        time.sleep(1)                                              # lịch sự với máy chủ
        rec = parse_article(item, cat)
        with out_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        seen.add(key)
        print(f"[{cat}] đã ghi -> {out_file}  (cảnh báo: {rec['parse_warning']})")

    STATE_FILE.write_text(json.dumps(sorted(seen), ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="ghi lại kể cả bài đã thấy")
    main(ap.parse_args().force)