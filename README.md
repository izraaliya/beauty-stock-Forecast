# 📦 Beauty Stock AI

Upload sales, refund and stock CSVs from a beauty retail chain (skincare & makeup) and get a **stock plan per store per SKU**: what to order, what is about to run out, what is overstocked, how each store behaves by weekday, and whether Sunday's bigger shelf display can be filled from the store's own backroom.

An AI summary (Gemini) turns the numbers into prioritised actions. Without an API key the app falls back to a rule-based summary, so it works either way.

![Screenshot](docs/screenshot.png)

## What it does

| Output | Details |
|--------|---------|
| **Stock status per store × SKU** | `RISIKO HABIS` (will run out before the order arrives), `ORDER SEKARANG`, `AMAN`, `OVERSTOCK` |
| **Order quantity** | Order-up-to level minus stock on hand, rounded up to pack size |
| **Weekday trends** | Day-of-week index per store and category (heatmap), peak day per SKU |
| **Shelf vs backroom** | For the next Sunday: display target vs stock, units to move backroom → shelf, units short, restock runs needed |
| **Refund analysis** | By reason, store and SKU; only resellable returns go back into stock |
| **AI summary** | Headline, prioritised actions and watch-outs written by Gemini from the computed numbers |
| **Excel export** | Workbook with live formulas: edit shelf/backroom counts and status and order quantities recalculate |

## How the numbers are computed

1. Missing days are filled with 0, and campaign spike days (e.g. 11.11) are detected automatically and excluded from the baseline.
2. **Day-of-week index** per store × SKU, shrunk toward the store × category index when volume is low.
3. **Base demand** = exponentially weighted average of `units / index` over recent days, with a damped trend.
4. **Forecast** over `lead time + review period` on the real calendar (a window with two Sundays gets two Sunday peaks), net of resellable refunds.
5. **Safety stock** = `z × residual std × √(lead time + review)`, with `z` by ABC class (95% / 90% / 80%).
6. Reorder point, order-up-to level, order quantity rounded to pack size.

The Gemini model only sees aggregated results, never raw rows, and is told to use only those numbers.

## Input files

Upload several CSVs at once. The kind of each file is detected from its columns, so file names do not matter.

| File | Required | Columns |
|------|----------|---------|
| Sales | yes | `date, store_id, sku, units_sold` |
| Stock | yes | `store_id, sku, shelf_stock, backroom_stock` |
| SKU master | yes | `sku, name, category, price, unit_cost, lead_time_days, pack_size` (optional: `subcategory`, `hero_sunday`) |
| Refunds | no | `date, store_id, sku, units_refunded` (optional: `reason`, `units_resellable`) |
| Planogram | no | `store_id, sku, shelf_target_weekday, shelf_target_sunday` |
| Stores | no | `store_id, store_name` |

Without refunds the app ignores them, without a planogram it estimates display targets from sales. Sample files are in [`sample_data/`](sample_data) and the **Try with sample data** button uses them. The sample data is synthetic.

## Run

### Python

```bash
git clone https://github.com/izraaliya/beauty-stock-forecast.git
cd beauty-stock-forecast
python -m venv .venv
source .venv/bin/activate          # Git Bash on Windows: source .venv/Scripts/activate
pip install -r requirements-dev.txt

export GEMINI_API_KEY=your-key-here    # optional; PowerShell: $env:GEMINI_API_KEY="your-key-here"
uvicorn app.main:app --reload
```

Open http://localhost:8000.

### Docker

```bash
cp .env.example .env      # optional: add GEMINI_API_KEY
docker compose up --build
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `GEMINI_API_KEY` | none | Enables the AI summary (free key at https://aistudio.google.com/apikey) |
| `GEMINI_MODEL` | `gemini-3.5-flash` | Any Gemini model that supports JSON output |

## API

Docs at `/docs`.

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/analyze` | Multipart: `files` (several CSVs), `review_days`, `overstock_days`, `use_ai` |
| `POST` | `/api/analyze/sample` | Same, using the bundled sample data |
| `GET` | `/api/report/{job_id}.xlsx` | Excel export of an analysis (kept 1 hour) |
| `GET` | `/api/schema` | Expected files and columns |

## Tests

```bash
pytest
```

18 tests cover the analysis (invariants, planted weekday patterns recovered, sparse data, validation errors) and the API. Gemini is not called in tests.

## Limitations

- Sales on days a SKU was out of stock are not recorded, so lost demand is not counted. Real data needs a correction for this.
- Reports and results live in memory and are dropped after an hour or a restart.
- Up to 5,000 store × SKU combinations and 25 MB per file per analysis.
- AI text can be wrong; the numbers in the tables and the Excel are the source of truth.

## Project structure

```
app/
  analysis.py   # validation + forecasting + stock logic
  insights.py   # Gemini summary with rule-based fallback
  report.py     # Excel builder
  main.py       # FastAPI routes
  static/       # single-page UI
sample_data/    # synthetic example CSVs
tests/
```
