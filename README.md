# NDX Watchlist Engine

Every evening after the US close, this pulls options data on Nasdaq-100 stocks, computes implied vol and a handful of market features, runs a two-stage model, and writes a ranked watchlist to disk. No web UI, no broker hooks, no real-time anything — just a batch script, a DuckDB file, and markdown output.

The full design lives in [SPEC.md](SPEC.md). Read that if you want the math, the feature list, or the monitoring rules. This file is just enough to get it running.

## What you get out

After a daily run, look in `watchlists/`:

- `YYYY-MM-DD.json` — structured output (source of truth)
- `YYYY-MM-DD.md` — human-readable version of the same thing

Each signal names a ticker, whether a big move is likely over the next 5 sessions, and a directional lean (LONG / SHORT / WATCH). Thresholds are scaled per stock using its own realized vol, not a flat percentage.

Monthly health reports land in `monitoring/` once you've been running long enough to have history.

## Requirements

- Python 3.11+
- A [Polygon / Massive.com](https://massive.com/dashboard) API key (free tier works, with caveats below)
- A free [FRED](https://fred.stlouisfed.org/docs/api/api_key.html) API key (used for credit spread and T-bill rate)

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env
# edit .env — paste both keys
```

Initialize the database (only needed once):

```bash
duckdb data/db.duckdb < data/schema.sql
```

If you don't have the `duckdb` CLI, the app creates the file on first run — but applying `schema.sql` upfront is cleaner.

## First-time data load

Before the daily script can do anything useful, you need ~2 years of options history in the database. That's a one-time job:

```bash
python scripts/backfill.py 2>&1 | tee backfill.log
```

It's resumable. Progress is tracked in the `backfill_progress` table, so you can kill it and pick up later.

**On the free Polygon tier, expect this to take roughly half a day.** The API doesn't offer bulk options pulls — each contract is a separate call, rate-limited to 5/min. We cap scope to ~42 contracts per stock (ATM ± 3 strikes, 3 expiries, calls + puts). See the comment block at the top of `src/pull.py` for the exact numbers from our pre-build test.

Greeks and snapshot endpoints also 403 on free tier. IV is inverted from OHLCV via Black-Scholes instead.

## Train the model

After backfill and feature computation have populated the database, fit the models (do this quarterly, or whenever you want a refresh):

```bash
python scripts/train.py
```

Artifacts go to `models/`. Stage B has to beat a simple IV-only baseline by at least 0.03 AUC or training warns you the model isn't adding much.

## Daily run

```bash
python scripts/run_daily.py
```

Pipeline order: pull → compute features → screen (Stage A) → predict (Stage B/C) → write watchlist → quick health check.

Reasonable cron (weekdays, after data settles):

```
30 17 * * 1-5 cd /path/to/Model_M1 && .venv/bin/python scripts/run_daily.py
```

## Project layout

```
src/
  pull.py      fetch Polygon, yfinance, FRED, CBOE
  compute.py   BSM IV, 15 features
  screen.py    Stage A — who even gets scored
  model.py     Stage B (magnitude) + Stage C (direction)
  output.py    JSON first, markdown second
  monitor.py   calibration, drift, pause conditions

scripts/
  backfill.py  one-time history pull
  train.py     quarterly refit
  run_daily.py the thing you actually run every night

settings.py    all thresholds — change things here, not scattered in source files
data/
  schema.sql   table definitions
  db.duckdb    local database (gitignored)
```

## Configuration

Everything tunable is in `settings.py`: horizon length, screening cutoffs, model thresholds, rate limit sleep, feature names. API keys stay in `.env`.

Don't commit `.env` or `data/db.duckdb`.

## Tests

```bash
pytest
```

Eleven tests across pull, compute, model, and output. They mock external calls — passing tests doesn't mean your backfill finished.

## API explorer

Before building anything, we ran `explore_api.py` against the free tier to see what endpoints actually work. Results are in [api_report.md](api_report.md). You can re-run it anytime:

```bash
python explore_api.py
```

## Honest limitations

This is a research watchlist, not a trading system. No position sizing, no execution, no backtest harness in the repo.

Nightly pulls on the free tier are slow for the full NDX universe. If that's a problem, the paid Polygon tier is the fix — but that's a cost decision, not a code change.

Some features depend on CBOE dispersion/correlation data scraped from their site. When that page changes shape, `fetch_cboe_metrics` may need a touch-up.
