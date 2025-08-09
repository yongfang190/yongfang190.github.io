# scripts/fetch_ndss.py
import requests, json, pathlib, datetime as dt, re
from urllib.parse import urlencode

BASE = "https://dblp.org/search/publ/api"

def slugify(s: str) -> str:
    s = re.sub(r"\s+", "-", s.strip().lower())
    s = re.sub(r"[^a-z0-9\-]", "", s)
    return s[:80]

def fetch_toc(year):
    """TOC 精确抓取"""
    params = {"q": f"toc:db/conf/ndss/ndss{year}.bht", "h": "1000", "format": "json"}
    r = requests.get(BASE, params=params, timeout=30)
    r.raise_for_status()
    hits = r.json().get("result", {}).get("hits", {}).get("hit", [])
    return hits

def fetch_venue():
    """Venue 兜底抓取（会抓到所有年份，需要再按年份分组）"""
    params = {"q": "venue:NDSS", "h": "1000", "format": "json"}
    r = requests.get(BASE, params=params, timeout=30)
    r.raise_for_status()
    hits = r.json().get("result", {}).get("hits", {}).get("hit", [])
    return hits

def parse_hits(hits, current_year, min_year):
    """解析 hits 并按年份分组"""
    items_by_year = {}
    for h in hits:
        info = h.get("info", {})
        title = info.get("title", "").strip()
        try:
            year = int(info.get("year", current_year))
        except ValueError:
            continue
        if year < min_year:
            continue

        authors = []
        if "authors" in info and "author" in info["authors"]:
            a = info["authors"]["author"]
            if isinstance(a, list):
                authors = [x.get("text") if isinstance(x, dict) else str(x) for x in a]
            elif isinstance(a, dict):
                authors = [a.get("text")]

        pdf = info.get("ee")
        doi = info.get("doi")

        pid = f"ndss-{year}-{slugify(title)}"
        item = {
            "id": pid,
            "title": title,
            "authors": authors,
            "abstract": "",
            "pdf_url": pdf,
            "doi": doi,
            "source": "DBLP",
            "published_at": f"{year}-01-01",
            "year": year,
            "tags": []
        }
        items_by_year.setdefault(year, []).append(item)
    return items_by_year

def main():
    year_now = dt.datetime.utcnow().year
    min_year = year_now - 4

    out_dir = pathlib.Path("public/data/ndss")
    out_dir.mkdir(parents=True, exist_ok=True)

    index_data = {
        "conference": "NDSS",
        "years": {},
        "last_updated": dt.datetime.utcnow().isoformat() + "Z"
    }

    # 先尝试 TOC 抓取每年
    all_years_data = {}
    for y in range(year_now, min_year - 1, -1):
        hits = fetch_toc(y)
        if not hits:  # TOC 无数据 -> venue 兜底
            print(f"[WARN] TOC 无 {y} 数据，尝试 venue 兜底")
            venue_hits = fetch_venue()
            # venue_hits 包含所有年份，这里按 parse 过滤
            items_by_year = parse_hits(venue_hits, year_now, min_year)
            hits_year = items_by_year.get(y, [])
        else:
            hits_year = parse_hits(hits, year_now, min_year).get(y, [])

        all_years_data[y] = hits_year
        # 写每年的 JSON
        (out_dir / f"{y}.json").write_text(
            json.dumps({
                "conference": "NDSS",
                "year": y,
                "items": hits_year
            }, ensure_ascii=False, indent=2)
        )

        index_data["years"][y] = {
            "count": len(hits_year)
        }
        print(f"Year {y}: {len(hits_year)} items")

    # 写 index.json
    (out_dir / "index.json").write_text(json.dumps(index_data, ensure_ascii=False, indent=2))
    print(f"Saved index.json with {len(index_data['years'])} years")

if __name__ == "__main__":
    main()
