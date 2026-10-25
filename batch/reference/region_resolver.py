"""Đổi tên vùng trong bản tin (ví dụ "khu vực Gia Lai - Cà Mau") ra tập vùng của dim_region.

Quy tắc, xét lần lượt cho mỗi cụm:
  1. Chuẩn hóa: chữ thường, bỏ "khu vực", "vùng biển", thống nhất gạch nối.
  2. Tách cụm ghép theo "và", dấu phẩy, "(bao gồm ...)".
  3. Tra alias (tên vùng tự nó cũng là alias).
  4. Khoảng tỉnh "A - B" hoặc một tỉnh: lấy mọi vùng ven bờ giao với đoạn bờ từ A tới B.
  5. Không khớp: trả về trong danh sách unresolved, không đoán.

Dùng:
    r = RegionResolver.from_yaml()
    r.resolve("Giữa Biển Đông và đặc khu Hoàng Sa")
    # -> ({'Giữa Biển Đông', 'Đặc khu Hoàng Sa'}, [])
"""
import re
import unicodedata
from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parent / "regions.yaml"

DROP_WORDS = ["khu vực", "vùng biển", "các vùng", "vùng", "ngoài khơi", "đặc khu", "tỉnh", "thành phố", "tp."]


def norm(s: str) -> str:
    s = unicodedata.normalize("NFC", s or "").lower()
    s = re.sub(r"[–—−]", "-", s)                 # các kiểu gạch nối về "-"
    s = re.sub(r"\s*-\s*", " - ", s)
    s = re.sub(r"[.;:]+$", "", s.strip())
    return " ".join(s.split())


def strip_words(s: str) -> str:
    for w in DROP_WORDS:
        s = re.sub(rf"(^|\s){re.escape(w)}(?=\s|$)", " ", s)
    return " ".join(s.split())


class RegionResolver:
    def __init__(self, cfg: dict):
        names = [r["name"] for r in cfg["regions"]]
        self.aliases: dict[str, set[str]] = {}
        for n in names:
            self.aliases[strip_words(norm(n))] = {n}
        for a, targets in (cfg.get("aliases") or {}).items():
            unknown = set(targets) - set(names)
            if unknown:
                raise ValueError(f"alias '{a}' trỏ tới vùng không có: {unknown}")
            self.aliases[strip_words(norm(a))] = set(targets)
        self.provinces = {strip_words(norm(p)): pos for p, pos in cfg["coastal_provinces"].items()}
        self.spans = {name: tuple(span) for name, span in cfg["coastal_spans"].items()}

    @classmethod
    def from_yaml(cls, path: Path = CONFIG) -> "RegionResolver":
        return cls(yaml.safe_load(path.read_text(encoding="utf-8")))

    # ------------------------------------------------------------ tỉnh ven biển
    def _province(self, s: str) -> list[tuple[float, float]] | None:
        """Các đoạn bờ [từ, đến) của một tỉnh, có xét tiền tố Bắc/Nam."""
        half = None
        m = re.match(r"^(bắc|nam)\s+(.+)$", s)
        if m and m.group(2) in self.provinces:
            half, s = m.group(1), m.group(2)
        if s not in self.provinces:
            return None
        out = []
        for p in self.provinces[s]:
            if half == "bắc":
                out.append((p, p + 0.5))
            elif half == "nam":
                out.append((p + 0.5, p + 1))
            else:
                out.append((p, p + 1))
        return out

    def _coastal(self, lo: float, hi: float) -> set[str]:
        return {name for name, (a, b) in self.spans.items() if min(b, hi) - max(a, lo) > 0}

    def _range(self, part: str) -> set[str] | None:
        if " - " in part:
            a, b = (x.strip() for x in part.split(" - ", 1))
            pa, pb = self._province(a), self._province(b)
            if not pa or not pb:
                return None
            # tỉnh có nhiều đoạn bờ (Cà Mau): chọn cặp đoạn gần nhau nhất
            sa, sb = min(((x, y) for x in pa for y in pb), key=lambda xy: abs(xy[0][0] - xy[1][0]))
            return self._coastal(min(sa[0], sb[0]), max(sa[1], sb[1]))
        p = self._province(part)
        if p:
            return set().union(*(self._coastal(lo, hi) for lo, hi in p))
        return None

    # ------------------------------------------------------------ chính
    def resolve(self, text: str) -> tuple[set[str], list[str]]:
        """Trả về (tập tên vùng, danh sách cụm không hiểu)."""
        s = norm(text)
        parts = []
        for inner in re.findall(r"\(\s*bao gồm\s+([^)]*)\)", s):   # "(bao gồm ...)"
            parts.append(inner)
        s = re.sub(r"\([^)]*\)", " ", s)                               # bỏ ngoặc (kể cả ngày)
        parts += re.split(r"\s+và\s+|,|;", s)

        regions, unresolved = set(), []
        for raw in parts:
            part = strip_words(norm(raw))
            if not part:
                continue
            hit = self.aliases.get(part) or self._range(part)
            if hit:
                regions |= hit
            else:
                unresolved.append(raw.strip())
        return regions, unresolved


if __name__ == "__main__":
    r = RegionResolver.from_yaml()
    for t in ["Khu vực vịnh Bắc Bộ (05-06/10)",
              "khu vực Nam Quảng Trị - Quảng Ngãi (06-08/10)",
              "khu vực Gia Lai - Cà Mau (07-08/10)",
              "khu vực Bắc Biển Đông (05-08/10)",
              "khu vực Giữa Biển Đông và đặc khu Hoàng Sa (07-08/10)",
              "Bắc Biển Đông (bao gồm đặc khu Hoàng Sa)"]:
        print(t, "->", r.resolve(t))
