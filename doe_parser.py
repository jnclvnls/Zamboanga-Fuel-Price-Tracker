"""Parser for the DOE Mindanao Field Office weekly "Price Monitoring of Liquid Fuels" PDF.

Each page of the PDF is a table. For every city there is a block of 7 product rows
(RON 100, RON 97, RON 95, RON 91, DIESEL, DIESEL PLUS, KEROSENE). The city/province
names sit in merged cells on the left, so they are matched to rows by vertical position.

A row looks like this once flattened to text:

    RON 95 75.40 - 75.40 81.20 - 84.40 - 82.90 - 82.90 - 75.40 - 75.40 - - 79.69 - 82.80 75.40 - 84.40 75.40

That is 9 brand cells, then the OVERALL RANGE cell, then the COMMON PRICE. A brand cell is
either "-" (no station monitored) or "low - high". The common price is a number or NONE.
"""
from __future__ import annotations

import re
from datetime import date

BRANDS = ["Petron", "Shell", "Caltex", "Phoenix", "Flying V", "Seaoil", "Jetti", "My Gas", "Independent"]
PRODUCTS = ["RON 100", "RON 97", "RON 95", "RON 91", "DIESEL", "DIESEL PLUS", "KEROSENE"]

_ROW_RE = re.compile(r"^(RON 100|RON 97|RON 95|RON 91|DIESEL PLUS|DIESEL|KEROSENE)\s+(.*)$")
_NUM_RE = re.compile(r"^\d{1,3}(?:,\d{3})*(?:\.\d+)?$")
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def _is_num(tok: str) -> bool:
    return bool(_NUM_RE.match(tok))


def _num(tok: str) -> float:
    return float(tok.replace(",", ""))


def _read_cells(tokens: list[str]) -> list[tuple[float, float] | None]:
    cells: list[tuple[float, float] | None] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t == "-":
            cells.append(None)
            i += 1
        elif _is_num(t) and i + 2 < len(tokens) and tokens[i + 1] == "-" and _is_num(tokens[i + 2]):
            cells.append((_num(t), _num(tokens[i + 2])))
            i += 3
        else:
            raise ValueError(f"unexpected token {t!r} at position {i}")
    return cells


def parse_row(line: str) -> dict:
    """Parse one product row. Raises ValueError if it does not fit the expected layout."""
    m = _ROW_RE.match(line.strip())
    if not m:
        raise ValueError(f"not a product row: {line!r}")
    product, rest = m.group(1), m.group(2).split()
    if not rest:
        raise ValueError("empty row")

    expected = len(BRANDS) + 1  # brand cells + overall range
    common = None
    cells = None
    err = None
    # Normal case: last token is the common price (number or NONE).
    last = rest[-1]
    if last == "NONE" or _is_num(last):
        try:
            cells = _read_cells(rest[:-1])
            common = None if last == "NONE" else _num(last)
            if len(cells) != expected:
                cells = None
        except ValueError as e:
            err = e
    # Fallback: some rows omit the common-price column entirely.
    if cells is None:
        try:
            cells = _read_cells(rest)
            common = None
            if len(cells) != expected:
                raise ValueError(f"expected {expected} cells, found {len(cells)}")
        except ValueError as e:
            raise ValueError(f"{product}: could not parse row ({err or e})") from None

    brands = {b: (list(c) if c else None) for b, c in zip(BRANDS, cells[:-1])}
    overall = list(cells[-1]) if cells[-1] else None
    return {"product": product, "brands": brands, "overall": overall, "common": common}


def parse_block(lines: list[str]) -> tuple[dict, list[str]]:
    """Parse a block of product rows. Returns ({product: row}, [warnings])."""
    out: dict[str, dict] = {}
    warnings: list[str] = []
    for line in lines:
        try:
            row = parse_row(line)
        except ValueError as e:
            warnings.append(str(e))
            continue
        # sanity check: overall range should match the brand cells
        prices = [p for c in row["brands"].values() if c for p in c]
        if prices and row["overall"]:
            lo, hi = min(prices), max(prices)
            if abs(lo - row["overall"][0]) > 0.011 or abs(hi - row["overall"][1]) > 0.011:
                warnings.append(f"{row['product']}: overall range {row['overall']} does not match brand cells ({lo}-{hi})")
        out[row["product"]] = row
    return out, warnings


def parse_week(text: str, fallback_year: int | None = None) -> tuple[date, date] | None:
    """Parse '(For the week: August 18-24, 2026)' or 'December 30-January 5, 2026'."""
    m = re.search(r"For the week:?\s*([A-Za-z]+)\.?\s+(\d{1,2})\s*(?:-|–|to)\s*(?:([A-Za-z]+)\.?\s+)?(\d{1,2}),?\s*(\d{4})?", text)
    if not m:
        return None
    m1, d1, m2, d2, year = m.groups()
    year = int(year) if year else fallback_year
    if year is None:
        return None
    mo1 = _MONTHS.get(m1[:3].lower())
    mo2 = _MONTHS.get((m2 or m1)[:3].lower())
    if not mo1 or not mo2:
        return None
    end = date(year, mo2, int(d2))
    start = date(year - 1 if mo1 > mo2 else year, mo1, int(d1))
    return start, end


# ---------------------------------------------------------------------------
# PDF layer
# ---------------------------------------------------------------------------

def _group_lines(words: list[dict], tol: float = 3.0) -> list[list[dict]]:
    lines: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if lines and abs(lines[-1][0]["top"] - w["top"]) <= tol:
            lines[-1].append(w)
        else:
            lines.append([w])
    return [sorted(l, key=lambda w: w["x0"]) for l in lines]


def extract_city_blocks(pdf_path: str) -> tuple[list[dict], date | None, date | None]:
    """Return ([{city, page, rows: [line, ...]}], week_start, week_end) for every block in the PDF."""
    import pdfplumber

    blocks_out: list[dict] = []
    week: tuple[date, date] | None = None

    with pdfplumber.open(pdf_path) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            if week is None:
                week = parse_week(page.extract_text() or "")
            words = page.extract_words(x_tolerance=2, y_tolerance=2, keep_blank_chars=False)
            first_words = [w for w in words if w["text"] in ("RON", "DIESEL", "KEROSENE")]
            if not first_words:
                continue
            product_x0 = min(w["x0"] for w in first_words)

            # product rows
            rows = []
            for line in _group_lines([w for w in words if w["x0"] >= product_x0 - 1]):
                text = " ".join(w["text"] for w in line)
                if _ROW_RE.match(text):
                    rows.append({"text": text, "top": min(w["top"] for w in line), "bottom": max(w["bottom"] for w in line)})
            if not rows:
                continue

            # group rows into blocks: a new block starts whenever the product order resets
            blocks: list[list[dict]] = []
            last_idx = -1
            for r in rows:
                idx = PRODUCTS.index(_ROW_RE.match(r["text"]).group(1))
                if not blocks or idx <= last_idx:
                    blocks.append([])
                blocks[-1].append(r)
                last_idx = idx

            spans = [(b[0]["top"], b[-1]["bottom"]) for b in blocks]
            # boundaries halfway between consecutive blocks
            bounds = []
            for i, (top, bottom) in enumerate(spans):
                lo = top - 40 if i == 0 else (spans[i - 1][1] + top) / 2
                hi = bottom + 40 if i == len(spans) - 1 else (bottom + spans[i + 1][0]) / 2
                bounds.append((lo, hi))

            # label words: left of the product column, inside the table body
            body_top, body_bottom = spans[0][0] - 5, spans[-1][1] + 5
            labels = [w for w in words if w["x1"] <= product_x0 + 1 and body_top <= (w["top"] + w["bottom"]) / 2 <= body_bottom]

            # find the city column: header word "CITY" if present, else the right-most x cluster
            header_city = [w for w in words if w["text"] == "CITY" and w["top"] < spans[0][0]]
            if header_city:
                city_x0 = min(header_city, key=lambda w: w["top"])["x0"] - 4
            elif labels:
                xs = sorted({round(w["x0"]) for w in labels})
                city_x0 = xs[0]
                for a, b in zip(xs, xs[1:]):
                    if b - a > 15:
                        city_x0 = b - 4
            else:
                city_x0 = 0

            for block, (lo, hi) in zip(blocks, bounds):
                ws = [w for w in labels if w["x0"] >= city_x0 and lo <= (w["top"] + w["bottom"]) / 2 < hi]
                city = " ".join(w["text"] for w in sorted(ws, key=lambda w: (round(w["top"]), w["x0"])))
                blocks_out.append({"city": " ".join(city.split()), "page": page_no, "rows": [r["text"] for r in block]})

    return blocks_out, (week[0] if week else None), (week[1] if week else None)


def parse_pdf_for_city(pdf_path: str, city: str = "Zamboanga City") -> dict:
    """Parse the PDF and return the data for one city.

    Returns {"week_start", "week_end", "products": {...}, "warnings": [...]}.
    Raises LookupError if the city is not found.
    """
    blocks, start, end = extract_city_blocks(pdf_path)
    want = city.lower()
    hit = next((b for b in blocks if b["city"].lower() == want), None)
    if hit is None:
        seen = ", ".join(b["city"] or "?" for b in blocks[:12])
        raise LookupError(f"'{city}' not found in {pdf_path}. First cities seen: {seen}")
    products, warnings = parse_block(hit["rows"])
    if start is None:
        warnings.append("could not read the week dates from the PDF")
    return {
        "week_start": start.isoformat() if start else None,
        "week_end": end.isoformat() if end else None,
        "products": products,
        "warnings": warnings,
    }
