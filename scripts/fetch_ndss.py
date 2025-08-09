# scripts/fetch_ndss.py
import requests, json, pathlib, datetime as dt, re, time
from urllib.parse import urlencode

BASE = "https://dblp.org/search/publ/api"
# 尝试多种查询语法（DBLP 对 NDSS 的索引写法可能不同）
QUERIES = [
    "toc:conf/ndss:*",
    "toc:NDSS:*",
    "venue:ndss",
    "venue:NDSS",
]

def slugify(s: str) -> str:
    s = re.sub(r"\s+", "-", s.strip().lower())
    s = re.sub(r"[^a-z0-9\-]", "", s)
    return s[:80]

def fetch_once(q: str, h: int = 1000):
    params = {"q": q, "h": str(h), "format": "json"}
    url = f"{BASE}?{urlencode(params)}"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return r.json().get("result", {}).get("hits", {}).get("hit", []) or []

def main():
    year_now = dt.datetime.utcnow().year
    out_dir = pathlib.Path("public/data/ndss")
    out_dir.mkdir(parents=True, exist_ok=True)

    all_hits = []
    for q in QUERIES:
        try:
            hits = fetch_once(q)
            print(f"Query `{q}` -> {len(hits)} hits")
            all_hits.extend(hits)
            time.sleep(0.8)  # 轻微限速，避免被限流
        except Exception as e:
            print(f"Query `{q}` failed: {e}")

    # 规范化 & 去重（按 title+year）
    dedup = {}
    for h in all_hits:
        info = h.get("info", {}) if isinstance(h, dict) else {}
        title = (info.get("title") or "").strip()
        year = int(info.get("year") or 0) if info.get("year") else 0
        if not title or not year:
            continue
        key = (title.lower(), year)
        if key in dedup:
            continue
        authors = []
        if "authors" in info and "author" in info["authors"]:
            a = info["authors"]["author"]
            if isinstance(a, list):
                authors = [x.get("text") if isinstance(x, dict) else str(x) for x in a]
            elif isinstance(a, dict):
                authors = [a.get("text")]
        item = {
            "id": f"ndss-{year}-{slugify(title)}",
            "title": title,
            "authors": authors,
            "abstract": "",
            "pdf_url": info.get("ee"),
            "doi": info.get("doi"),
            "source": "DBLP",
            "published_at": f"{year}-01-01",
            "year": year,
            "tags": []
        }
        dedup[key] = item

    # 仅保留近5年（可按需调整）
    items_by_year = {}
    for (_, year), item in dedup.items():
        if year < year_now - 4:
            continue
        items_by_year.setdefault(year, []).append(item)

    # 写文件并打印统计
    years_saved = []
    for y in sorted(items_by_year.keys(), reverse=True):
        items = items_by_year[y]
        out = {"conference": "NDSS", "year": y, "items": items}
        (out_dir / f"{y}.json").write_text(json.dumps(out, ensure_ascii=False, indent=2))
        print(f"Year {y}: {len(items)} items")
        years_saved.append(y)

    print(f"Saved NDSS data for years: {sorted(years_saved)}")

if __name__ == "__main__":
    main()
