# Zamboanga City fuel price tracker

Pulls the DOE Mindanao Field Office weekly "Price Monitoring of Liquid Fuels" report, extracts the
Zamboanga City rows, saves them to `data/`, and shows them on `index.html`.

## Files
- `doe_parser.py` reads one DOE PDF and returns the Zamboanga City prices
- `fetch_prices.py` finds the newest report on the DOE site and updates `data/`
- `index.html` the page (open it in a browser, or host the folder on GitHub Pages / Netlify)
- `data/prices.json`, `data/prices.js` the stored history (the page reads `prices.js`)
- `tests/` checks, run with `python -m pytest tests`
- `.github/workflows/weekly.yml` runs the update every Wednesday on GitHub Actions (free)

## Run it yourself
```
pip install -r requirements.txt
python fetch_prices.py --list               # see which reports the DOE page lists
python fetch_prices.py --backfill 8         # load the latest report and the 8 before it
python -m pytest -q tests                   # run the checks
```
Then open `index.html` in a browser.

## Set up the weekly updates
1. Create a GitHub repository and upload this folder.
2. In the repository settings, turn on **Actions** and give it **Read and write** permission (Settings → Actions → General → Workflow permissions).
3. Optional: turn on **GitHub Pages** (Settings → Pages → deploy from the main branch) to publish the page.

## Limits
- The DOE publishes a range per brand for each week, not each station's price.
- If the DOE changes its page layout or PDF format, the parser's tests will fail; that is the signal to update `doe_parser.py`.
- Only the two weeks in `tests/fixtures.py` were verified against a real PDF in this build. Run `fetch_prices.py --backfill` on your own machine to load the rest, then check a few values against the PDF.
