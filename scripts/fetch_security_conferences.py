#!/usr/bin/env python3
"""Fetch security conference paper metadata.

Official conference pages are preferred. DBLP is kept as a resilience fallback
because official page structures and paths change frequently, especially for
future or just-announced conference editions.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import subprocess
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from typing import Iterable
from urllib.parse import urlencode, urljoin

import requests

try:
    from bs4 import BeautifulSoup
except ImportError:  # GitHub's existing workflow only installs requests.
    BeautifulSoup = None


DBLP_API = "https://dblp.org/search/publ/api"
USER_AGENT = "yongfang190.github.io paper-ingest/1.0"


@dataclass(frozen=True)
class Conference:
    slug: str
    name: str
    short_name: str
    official_home: str
    dblp_toc_prefix: str
    dblp_venue_query: str
    dblp_venues: tuple[str, ...]


CONFERENCES: dict[str, Conference] = {
    "ndss": Conference(
        slug="ndss",
        name="Network and Distributed System Security Symposium",
        short_name="NDSS",
        official_home="https://www.ndss-symposium.org/",
        dblp_toc_prefix="ndss",
        dblp_venue_query="venue:NDSS",
        dblp_venues=("NDSS",),
    ),
    "sp": Conference(
        slug="sp",
        name="IEEE Symposium on Security and Privacy",
        short_name="IEEE S&P",
        official_home="https://www.ieee-security.org/TC/SP-Index.html",
        dblp_toc_prefix="sp",
        dblp_venue_query="venue:IEEE Symposium on Security and Privacy",
        dblp_venues=("IEEE Symposium on Security and Privacy",),
    ),
    "usenix": Conference(
        slug="usenix",
        name="USENIX Security Symposium",
        short_name="USENIX Security",
        official_home="https://www.usenix.org/conferences/byname/108",
        dblp_toc_prefix="uss",
        dblp_venue_query="venue:USENIX Security Symposium",
        dblp_venues=("USENIX Security Symposium",),
    ),
    "ccs": Conference(
        slug="ccs",
        name="ACM Conference on Computer and Communications Security",
        short_name="ACM CCS",
        official_home="https://www.sigsac.org/ccs.html",
        dblp_toc_prefix="ccs",
        dblp_venue_query="venue:CCS",
        dblp_venues=("CCS",),
    ),
}


NOISE_PATTERNS = (
    "accepted papers",
    "call for",
    "committee",
    "contact",
    "copyright",
    "deadline",
    "home",
    "hotel",
    "keynote",
    "location",
    "program",
    "registration",
    "schedule",
    "session",
    "sponsor",
    "symposium",
    "workshop",
)


def official_urls(conf: Conference, year: int) -> list[str]:
    yy = str(year)[-2:]
    if conf.slug == "ndss":
        return [f"https://www.ndss-symposium.org/ndss{year}/accepted-papers/"]
    if conf.slug == "sp":
        return [f"https://www.ieee-security.org/TC/SP{year}/program-papers.html"]
    if conf.slug == "usenix":
        return [f"https://www.usenix.org/conference/usenixsecurity{yy}/technical-sessions"]
    if conf.slug == "ccs":
        base = f"https://www.sigsac.org/ccs/CCS{year}"
        return [
            f"{base}/program/accepted-papers.html",
            f"{base}/accepted-papers.html",
            f"{base}/",
        ]
    return [conf.official_home]


def slugify(text: str) -> str:
    text = re.sub(r"\s+", "-", text.strip().lower())
    text = re.sub(r"[^a-z0-9\-]", "", text)
    return text[:80] or "paper"


def clean_text(text: str) -> str:
    text = unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" \t\n\r-–—|")


def looks_like_title(text: str) -> bool:
    text = clean_text(text)
    lower = text.lower()
    if not (24 <= len(text) <= 220):
        return False
    if any(pattern in lower for pattern in NOISE_PATTERNS):
        return False
    if len(text.split()) < 4:
        return False
    alpha_count = sum(ch.isalpha() for ch in text)
    return alpha_count >= max(12, len(text) * 0.45)


def fetch_json(url: str, params: dict[str, str | int]) -> dict:
    try:
        res = requests.get(url, params=params, timeout=(8, 20), headers={"User-Agent": USER_AGENT})
        res.raise_for_status()
        return res.json()
    except requests.RequestException as exc:
        full_url = f"{url}?{urlencode(params)}"
        print(f"[WARN] requests JSON fetch failed, trying curl: {full_url} ({exc})", flush=True)
        completed = subprocess.run(
            ["curl", "-L", "--silent", "--show-error", "--max-time", "25", full_url],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(completed.stdout)


def fetch_html(url: str) -> str | None:
    try:
        res = requests.get(url, timeout=(8, 20), headers={"User-Agent": USER_AGENT})
        if not 200 <= res.status_code < 300:
            raise requests.RequestException(f"HTTP {res.status_code}")
        content_type = res.headers.get("content-type", "")
        if "html" not in content_type.lower():
            raise requests.RequestException(f"unexpected content-type {content_type}")
        return res.text
    except requests.RequestException as exc:
        print(f"[WARN] requests HTML fetch failed, trying curl: {url} ({exc})", flush=True)
        try:
            completed = subprocess.run(
                ["curl", "-L", "--silent", "--show-error", "--max-time", "30", url],
                check=True,
                capture_output=True,
                text=True,
            )
            return completed.stdout if "<html" in completed.stdout.lower() else None
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as curl_exc:
            print(f"[WARN] official fetch failed: {url} ({curl_exc})", flush=True)
            return None


class CandidateParser(HTMLParser):
    def __init__(self, source_url: str):
        super().__init__(convert_charrefs=True)
        self.source_url = source_url
        self.stack: list[dict] = []
        self.candidates: list[tuple[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in {"a", "b", "h2", "h3", "h4", "li"}:
            return
        attr_map = {key: value for key, value in attrs}
        href = urljoin(self.source_url, attr_map["href"]) if attr_map.get("href") else None
        self.stack.append({"tag": tag, "href": href, "parts": []})

    def handle_data(self, data: str) -> None:
        for item in self.stack:
            item["parts"].append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self.stack:
            return
        item = self.stack[-1]
        if item["tag"] != tag:
            return
        self.stack.pop()
        text = clean_text(" ".join(item["parts"]))
        if looks_like_title(text):
            self.candidates.append((text, item["href"]))


def parse_official_fallback(html: str, conf: Conference, year: int, source_url: str) -> list[dict]:
    parser = CandidateParser(source_url)
    parser.feed(html)
    seen: set[str] = set()
    items: list[dict] = []
    for title, href in parser.candidates:
        key = re.sub(r"\W+", "", title.lower())
        if key in seen:
            continue
        seen.add(key)
        items.append(
            {
                "id": f"{conf.slug}-{year}-{slugify(title)}",
                "title": title,
                "authors": [],
                "abstract": "",
                "pdf_url": href,
                "doi": None,
                "source": f"{conf.short_name} official",
                "source_url": source_url,
                "published_at": f"{year}-01-01",
                "year": year,
                "tags": [],
            }
        )
    return items


def parse_official_page(html: str, conf: Conference, year: int, source_url: str) -> list[dict]:
    if BeautifulSoup is None:
        return parse_official_fallback(html, conf, year, source_url)

    soup = BeautifulSoup(html, "html.parser")
    for node in soup(["script", "style", "noscript", "svg", "header", "footer", "nav"]):
        node.decompose()

    if conf.slug == "sp":
        return parse_sp_page(soup, conf, year, source_url)
    if conf.slug == "usenix":
        return parse_usenix_page(soup, conf, year, source_url)

    candidates: list[tuple[str, str | None]] = []
    selectors = [
        ".paper-title",
        ".views-field-title",
        ".field-name-title",
        ".views-row h2",
        ".views-row h3",
        ".program-paper-title",
        ".title",
        "h2",
        "h3",
        "h4",
        "li",
        "a",
    ]
    for selector in selectors:
        for node in soup.select(selector):
            title = clean_text(node.get_text(" ", strip=True))
            if not looks_like_title(title):
                continue
            href = None
            link = node if node.name == "a" else node.find("a")
            if link and link.get("href"):
                href = urljoin(source_url, str(link["href"]))
            candidates.append((title, href))

    seen: set[str] = set()
    items: list[dict] = []
    for title, href in candidates:
        key = re.sub(r"\W+", "", title.lower())
        if key in seen:
            continue
        seen.add(key)
        items.append(
            {
                "id": f"{conf.slug}-{year}-{slugify(title)}",
                "title": title,
                "authors": [],
                "abstract": "",
                "pdf_url": href,
                "doi": None,
                "source": f"{conf.short_name} official",
                "source_url": source_url,
                "published_at": f"{year}-01-01",
                "year": year,
                "tags": [],
            }
        )
    return items


def parse_sp_page(soup: BeautifulSoup, conf: Conference, year: int, source_url: str) -> list[dict]:
    items: list[dict] = []
    seen: set[str] = set()
    for node in soup.select(".list-group-item"):
        title_node = node.find("b")
        if not title_node:
            continue
        title = clean_text(title_node.get_text(" ", strip=True))
        if not looks_like_title(title):
            continue
        key = re.sub(r"\W+", "", title.lower())
        if key in seen:
            continue
        seen.add(key)
        title_node.extract()
        author_text = clean_text(node.get_text(" ", strip=True))
        authors = [part.strip() for part in re.split(r"\),\s*", author_text) if part.strip()]
        items.append(
            {
                "id": f"{conf.slug}-{year}-{slugify(title)}",
                "title": title,
                "authors": authors,
                "abstract": "",
                "pdf_url": source_url,
                "doi": None,
                "source": f"{conf.short_name} official",
                "source_url": source_url,
                "published_at": f"{year}-01-01",
                "year": year,
                "tags": [],
            }
        )
    return items


def parse_usenix_page(soup: BeautifulSoup, conf: Conference, year: int, source_url: str) -> list[dict]:
    items: list[dict] = []
    seen: set[str] = set()
    for node in soup.select("article.node-paper"):
        link = node.find("a", href=True)
        if not link:
            continue
        title = clean_text(link.get_text(" ", strip=True))
        if not looks_like_title(title):
            continue
        key = re.sub(r"\W+", "", title.lower())
        if key in seen:
            continue
        seen.add(key)
        author_node = node.select_one(".field-name-field-paper-people-text")
        author_text = clean_text(author_node.get_text(" ", strip=True)) if author_node else ""
        authors = [part.strip() for part in re.split(r";|,\s+(?=[A-Z][A-Za-z.'-]+(?:\s|$))", author_text) if part.strip()]
        href = urljoin(source_url, str(link["href"]))
        items.append(
            {
                "id": f"{conf.slug}-{year}-{slugify(title)}",
                "title": title,
                "authors": authors,
                "abstract": "",
                "pdf_url": href,
                "doi": None,
                "source": f"{conf.short_name} official",
                "source_url": source_url,
                "published_at": f"{year}-01-01",
                "year": year,
                "tags": [],
            }
        )
    return items


def fetch_official(conf: Conference, year: int) -> tuple[list[dict], str | None]:
    for url in official_urls(conf, year):
        html = fetch_html(url)
        if not html:
            continue
        items = parse_official_page(html, conf, year, url)
        if len(items) >= 10:
            print(f"[OK] {conf.slug} {year}: {len(items)} official items from {url}", flush=True)
            return items, url
        print(f"[WARN] {conf.slug} {year}: official page parsed only {len(items)} items from {url}", flush=True)
    return [], None


def parse_dblp_hits(conf: Conference, hits: Iterable[dict], min_year: int, max_year: int) -> dict[int, list[dict]]:
    items_by_year: dict[int, list[dict]] = {}
    for hit in hits:
        info = hit.get("info", {})
        title = clean_text(info.get("title", ""))
        if not title:
            continue
        if info.get("venue") not in conf.dblp_venues:
            continue
        if info.get("type") == "Editorship":
            continue
        try:
            year = int(info.get("year", max_year))
        except (TypeError, ValueError):
            continue
        if year < min_year or year > max_year:
            continue

        raw_authors = info.get("authors", {}).get("author", [])
        if isinstance(raw_authors, dict):
            raw_authors = [raw_authors]
        authors = [
            author.get("text") if isinstance(author, dict) else str(author)
            for author in raw_authors
        ]

        item = {
            "id": f"{conf.slug}-{year}-{slugify(title)}",
            "title": title,
            "authors": [a for a in authors if a],
            "abstract": "",
            "pdf_url": info.get("ee"),
            "doi": info.get("doi"),
            "source": "DBLP fallback",
            "source_url": info.get("url") or conf.official_home,
            "published_at": f"{year}-01-01",
            "year": year,
            "tags": [],
        }
        items_by_year.setdefault(year, []).append(item)
    return items_by_year


def fetch_dblp_toc(conf: Conference, year: int, min_year: int, max_year: int) -> list[dict]:
    params = {
        "q": f"toc:db/conf/{conf.dblp_toc_prefix}/{conf.dblp_toc_prefix}{year}.bht",
        "h": "100",
        "format": "json",
    }
    data = fetch_json(DBLP_API, params)
    hits = data.get("result", {}).get("hits", {}).get("hit", [])
    return parse_dblp_hits(conf, hits, min_year, max_year).get(year, [])


def fetch_dblp_venue(conf: Conference, year: int, min_year: int, max_year: int) -> list[dict]:
    params = {"q": f"{conf.dblp_venue_query} year:{year}", "h": "100", "format": "json"}
    data = fetch_json(DBLP_API, params)
    hits = data.get("result", {}).get("hits", {}).get("hit", [])
    return parse_dblp_hits(conf, hits, min_year, max_year).get(year, [])


def fetch_dblp_html(conf: Conference, year: int) -> list[dict]:
    if BeautifulSoup is None:
        return []
    source_url = f"https://dblp.org/db/conf/{conf.dblp_toc_prefix}/{conf.dblp_toc_prefix}{year}.html"
    html = fetch_html(source_url)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    items: list[dict] = []
    seen: set[str] = set()
    for node in soup.select("li.entry.inproceedings"):
        title_node = node.select_one("span.title[itemprop='name']")
        if not title_node:
            continue
        title = clean_text(title_node.get_text(" ", strip=True))
        if not looks_like_title(title):
            continue
        key = re.sub(r"\W+", "", title.lower())
        if key in seen:
            continue
        seen.add(key)
        authors = [
            clean_text(author.get_text(" ", strip=True))
            for author in node.select("span[itemprop='author'] span[itemprop='name']")
        ]
        link = node.select_one("li.ee a[href]")
        href = str(link["href"]) if link else source_url
        items.append(
            {
                "id": f"{conf.slug}-{year}-{slugify(title)}",
                "title": title,
                "authors": [author for author in authors if author],
                "abstract": "",
                "pdf_url": href,
                "doi": href.removeprefix("https://doi.org/") if href.startswith("https://doi.org/") else None,
                "source": "DBLP HTML fallback",
                "source_url": source_url,
                "published_at": f"{year}-01-01",
                "year": year,
                "tags": [],
            }
        )
    return items


def fetch_year(conf: Conference, year: int, min_year: int, max_year: int) -> tuple[list[dict], str, str | None]:
    official_items, official_source = fetch_official(conf, year)
    if official_items:
        return official_items, "official", official_source

    try:
        items = fetch_dblp_toc(conf, year, min_year, max_year)
        if items:
            print(f"[OK] {conf.slug} {year}: {len(items)} DBLP TOC fallback items", flush=True)
            return items, "dblp_toc", None
    except Exception as exc:
        print(f"[WARN] {conf.slug} {year}: DBLP TOC failed ({exc})", flush=True)

    try:
        items = fetch_dblp_venue(conf, year, min_year, max_year)
        if items:
            print(f"[OK] {conf.slug} {year}: {len(items)} DBLP venue fallback items", flush=True)
            return items, "dblp_venue", None
    except Exception as exc:
        print(f"[WARN] {conf.slug} {year}: DBLP venue failed ({exc})", flush=True)

    try:
        items = fetch_dblp_html(conf, year)
        if items:
            print(f"[OK] {conf.slug} {year}: {len(items)} DBLP HTML fallback items", flush=True)
            return items, "dblp_html", None
    except Exception as exc:
        print(f"[WARN] {conf.slug} {year}: DBLP HTML failed ({exc})", flush=True)

    print(f"[WARN] {conf.slug} {year}: no items", flush=True)
    return [], "empty", official_source


def write_conference(conf: Conference, years: list[int], out_root: pathlib.Path) -> None:
    out_dir = out_root / conf.slug
    out_dir.mkdir(parents=True, exist_ok=True)

    index_data = {
        "conference": conf.short_name,
        "name": conf.name,
        "slug": conf.slug,
        "official_home": conf.official_home,
        "official_url_pattern": official_urls(conf, years[0])[0] if years else conf.official_home,
        "years": {},
        "last_updated": dt.datetime.utcnow().isoformat() + "Z",
    }

    min_year = min(years)
    max_year = max(years)
    for year in years:
        items, source, official_source = fetch_year(conf, year, min_year, max_year)
        payload = {
            "conference": conf.short_name,
            "name": conf.name,
            "slug": conf.slug,
            "year": year,
            "source": source,
            "official_source_url": official_source or official_urls(conf, year)[0],
            "items": items,
        }
        (out_dir / f"{year}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        index_data["years"][str(year)] = {
            "count": len(items),
            "source": source,
            "official_source_url": payload["official_source_url"],
        }
        print(f"[SAVE] {conf.slug} {year}: {len(items)} items ({source})", flush=True)

    (out_dir / "index.json").write_text(json.dumps(index_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[SAVE] {out_dir / 'index.json'}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--conference",
        choices=["all", *CONFERENCES.keys()],
        default="all",
        help="Conference slug to fetch.",
    )
    parser.add_argument("--years", type=int, default=5, help="Rolling year window.")
    parser.add_argument("--current-year", type=int, default=dt.datetime.utcnow().year)
    parser.add_argument("--out", default="public/data", help="Output data directory.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    slugs = list(CONFERENCES) if args.conference == "all" else [args.conference]
    years = list(range(args.current_year, args.current_year - args.years, -1))
    out_root = pathlib.Path(args.out)
    for slug in slugs:
        write_conference(CONFERENCES[slug], years, out_root)


if __name__ == "__main__":
    main()
