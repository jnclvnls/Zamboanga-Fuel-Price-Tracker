#!/usr/bin/env python3
"""Fetch the latest DOE Mindanao fuel price report and update the tracker data.

Usage:
    python fetch_prices.py                    # find the newest report on doe.gov.ph, parse it, update data/
    python fetch_prices.py --backfill 8       # also load the previous 8 weekly reports
    python fetch_prices.py --list             # just show the reports found on the DOE page
    python fetch_prices.py --pdf-file x.pdf   # parse a PDF you downloaded yourself
    python fetch_prices.py --city "Zamboanga City"

Writes data/prices.json (history) and data/prices.js (same data, for the web page).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin

import requests

from doe_parser import _MONTHS, parse_pdf_for_city

INDEX_URL = "https://doe.gov.ph/data-and-prices/liquid-fuels/retail-pump-prices/mindanao-pump-prices"
DATA_DIR = Path(__file__).parent / "data"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; zamboanga-fuel-tracker/1.0)"}
PHT = timezone(timedelta(hours=8))

_WEEK_TEXT = re.compile(r"([A-Za-z]+)\.?\s+(\d{1,2})\s+to\s+(?:([A-Za-z]+)\.?\s+)?(\d{1,2})", re.I)


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: list[tuple[str, str, int | None]] = []  # (href, text, year heading seen before it)
        self._href = None
        self._buf: list[str] = []
        self._year = None

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._buf = []

    def handle_data(self, data):
        if self._href is not None:
            self._buf.append(data)
        elif re.fullmatch(r"\s*(20\d\d)\s*", data):
            self._year = int(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join("".join(self._buf).split()), self._year))
            self._href = None


def discover_reports(html: str, base_url: str = INDEX_URL) -> list[dict]:
    """Return weekly report links found on the DOE page, newest first."""
    p = _Links()
    p.feed(html)
    reports = []
    for href, text, heading_year in p.links:
        m = _WEEK_TEXT.fullmatch(text)
        if not m or not re.search(r"\.pdf|/documents/d/|/api/media/file/", href, re.I):
            continue
        m1, d1, m2, d2 = m.groups()
        mo1, mo2 = _MONTHS.get(m1[:3].lower()), _MONTHS.get((m2 or m1)[:3].lower())
        if not mo1 or not mo2:
            continue
        # decode %20 etc. first, otherwise "%2013" looks like the year 2013
        years = re.findall(r"(?<!\d)(20\d\d)(?!\d)", unquote(href))
        year = int(years[-1]) if years else heading_year
        if not year:
            continue
        try:
            start = date(year, mo1, int(d1))
            end = date(year + (1 if mo2 < mo1 else 0), mo2, int(d2))
        except ValueError:
            continue
        reports.append({"label": text, "url": urljoin(base_url, href), "start": start, "end": end})
    reports.sort(key=lambda r: r["end"], reverse=True)
    return reports


def download(url: str, dest: Path) -> None:
    r = requests.get(url, headers=HEADERS, timeout=90)
    r.raise_for_status()
    if not r.content.startswith(b"%PDF"):
        raise RuntimeError(f"{url} did not return a PDF (got {r.headers.get('content-type')})")
    dest.write_bytes(r.content)


def load_history(city: str) -> dict:
    path = DATA_DIR / "prices.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("city") == city:
            return data
    return {"city": city, "source": "DOE Mindanao Field Office, Price Monitoring of Liquid Fuels", "weeks": []}


def save_history(data: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    data["updated"] = datetime.now(PHT).isoformat(timespec="seconds")
    data["weeks"].sort(key=lambda w: w["week_start"])
    text = json.dumps(data, indent=2, ensure_ascii=False)
    (DATA_DIR / "prices.json").write_text(text + "\n", encoding="utf-8")
    # same data as a script, so index.html also works when opened straight from disk
    (DATA_DIR / "prices.js").write_text("window.PRICE_DATA = " + text + ";\n", encoding="utf-8")


def add_week(history: dict, parsed: dict, source_url: str | None) -> None:
    entry = {
        "week_start": parsed["week_start"],
        "week_end": parsed["week_end"],
        "source_url": source_url,
        "products": {p: {"brands": r["brands"], "overall": r["overall"], "common": r["common"]}
                     for p, r in parsed["products"].items()},
    }
    history["weeks"] = [w for w in history["weeks"] if w["week_start"] != entry["week_start"]]
    history["weeks"].append(entry)


def process(pdf: Path, city: str, source_url: str | None, history: dict) -> bool:
    try:
        parsed = parse_pdf_for_city(str(pdf), city)
    except LookupError as e:
        print(f"  ! {e}", file=sys.stderr)
        return False
    if not parsed["week_start"]:
        print("  ! could not read the week dates; skipped", file=sys.stderr)
        return False
    for w in parsed["warnings"]:
        print(f"  warning: {w}", file=sys.stderr)
    add_week(history, parsed, source_url)
    g = parsed["products"].get("RON 95", {}).get("overall")
    d = parsed["products"].get("DIESEL", {}).get("overall")
    print(f"  ok  week {parsed['week_start']} to {parsed['week_end']}  RON 95 {g}  Diesel {d}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--city", default="Zamboanga City")
    ap.add_argument("--backfill", type=int, default=0, metavar="N", help="also load the N weeks before the latest")
    ap.add_argument("--list", action="store_true", help="list reports found on the DOE page and exit")
    ap.add_argument("--pdf-file", type=Path, help="parse this local PDF instead of downloading")
    ap.add_argument("--source-url", help="URL to record for --pdf-file")
    args = ap.parse_args()

    history = load_history(args.city)

    if args.pdf_file:
        ok = process(args.pdf_file, args.city, args.source_url, history)
        if ok:
            save_history(history)
        return 0 if ok else 1

    page = requests.get(INDEX_URL, headers=HEADERS, timeout=60)
    page.raise_for_status()
    reports = discover_reports(page.text)
    if not reports:
        print("No weekly reports found on the DOE page. The page layout may have changed.", file=sys.stderr)
        return 2
    if args.list:
        for r in reports[:20]:
            print(f"{r['start']} to {r['end']}  {r['url']}")
        return 0

    wanted = reports[: 1 + args.backfill]
    have = {w["week_start"] for w in history["weeks"]}
    any_ok = False
    with tempfile.TemporaryDirectory() as tmp:
        for i, r in enumerate(wanted):
            if i > 0 and r["start"].isoformat() in have:
                continue  # backfill only fills gaps; the newest report is always refreshed
            print(f"{r['label']}  {r['url']}")
            pdf = Path(tmp) / f"report_{i}.pdf"
            try:
                download(r["url"], pdf)
            except Exception as e:  # noqa: BLE001
                print(f"  ! download failed: {e}", file=sys.stderr)
                continue
            any_ok |= process(pdf, args.city, r["url"], history)
    if any_ok:
        save_history(history)
        print(f"Saved {len(history['weeks'])} week(s) to {DATA_DIR}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
