# NDX-100 Watchlist Engine — Technical Specification

> **Single source of truth for Cursor.**
> Read every section before writing any code.
> Small. Smart. Smooth.

---

## Mission

Read public options price and volume data on NDX-100 stocks, compute
implied volatility from those prices, and produce a daily ranked watchlist
of stocks likely to move beyond their typical volatility range over the
next 5 trading days, with a directional lean.

One nightly script. One database. One markdown file. That is the product.

---

## Cursor autonomous development rules

### Proceed autonomously for
- All implementation within the defined architecture
- Bug fixes, refactoring, error handling, logging
- Tests for defined modules
- Standard Python best practices

### Stop and ask for
- Any data source change or substitution
- Any feature addition beyond the 15 defined
- Any threshold or hyperparameter change
- Any dependency not in the approved list
- Any architectural change (new files, new modules)
- Any operation that incurs cost

### Ask format
```
[QUESTION N of TOTAL]
Context: what you are trying to do
Issue: what is blocking you
Options: A / B / C with tradeoffs
Recommendation: your best guess and why
```

### Questions to ask the user before writing any code

```
[QUESTION 1 of 2]
Context: Polygon API key required to pull options data.
Issue: Without it nothing runs.
Action: Please paste your Polygon / Massive.com API key (looks like pk_...).
Find it at: https://massive.com/dashboard
```

```
[QUESTION 2 of 2]
Context: FRED API key required for credit spread and T-bill rate data.
Issue: Without it 2 features cannot be computed.
Action: Please paste your FRED API key (free, register at fred.stlouisfed.org).
```

Store both in `.env`. Add `.env` to `.gitignore` immediately.

---

## Critical pre-build test — do this before any pipeline code

Before building the data pipeline, run this single test to determine
whether Polygon requires per-contract calls or supports bulk pulls.
This determines whether the free tier is viable or $29/mo is needed.

```python
# Run manually, takes 2 minutes
import requests, os, time
from dotenv import load_dotenv
load_dotenv()
KEY = os.getenv("POLYGON_API_KEY")

# Test 1: Can we get ALL contracts for AAPL in one call?
r = requests.get(
    "https://api.polygon.io/v2/aggs/grouped/locale/us/market/fx/2024-01-15",
    params={"apiKey": KEY}
)
# If 200: bulk endpoint exists

# Test 2: How many contracts does one ticker have in the ATM window?
r2 = requests.get(
    "https://api.polygon.io/v3/reference/options/contracts",
    params={
        "underlying_ticker": "AAPL",
        "as_of": "2024-01-15",
        "strike_price.gte": 180,   # approximate ATM ± 3
        "strike_price.lte": 200,
        "expiration_date.gte": "2024-01-15",
        "expiration_date.lte": "2024-03-15",
        "limit": 100,
        "apiKey": KEY
    }
)
print(f"Contract count in ATM window: {len(r2.json().get('results', []))}")
# If > 42: our ±3 strike filter needs tightening
# If per-contract pulls required: 42 × 100 stocks ÷ 5/min = 14 hours → pay $29/mo
```

Document the result as a comment at the top of `pull.py`.
If per-contract pulls are required and take > 2 hours nightly,
**stop and ask the user whether to upgrade to paid tier**.

---

## File structure — the complete application

```
ndx_watchlist/
├── .env                    # API keys — never commit
├── .gitignore
├── pyproject.toml
├── settings.py             # ALL thresholds live here, nowhere else
│
├── data/
│   ├── db.duckdb           # single database file
│   └── schema.sql          # table definitions — source of truth for schema
│
├── src/
│   ├── pull.py             # fetches all external data
│   ├── compute.py          # IV, Greeks, all 15 features
│   ├── screen.py           # Stage A screening
│   ├── model.py            # Stage B + C: train, predict, calibrate
│   ├── output.py           # writes JSON then renders markdown from it
│   └── monitor.py          # monthly metrics and health checks
│
├── scripts/
│   ├── run_daily.py        # ENTRY POINT — run this every evening
│   ├── train.py            # run quarterly to refit models
│   └── backfill.py         # one-time 2-year history pull from Polygon
│
├── watchlists/             # daily outputs: YYYY-MM-DD.json + YYYY-MM-DD.md
├── monitoring/             # monthly reports: YYYY-MM.md
├── models/                 # saved model artifacts: stage_b_YYYYMMDD.pkl etc.
└── tests/
    ├── test_pull.py
    ├── test_compute.py
    ├── test_model.py
    └── test_output.py
```

**Seven source files. That is the entire application.**
Do not create additional source files without asking.

---

## Database schema

```sql
-- data/schema.sql
-- Source of truth. Apply once with: duckdb data/db.duckdb < data/schema.sql

CREATE TABLE IF NOT EXISTS options_daily (
    date            DATE        NOT NULL,
    ticker          VARCHAR     NOT NULL,   -- option contract ticker e.g. O:AAPL240119C00180000
    underlying      VARCHAR     NOT NULL,   -- e.g. AAPL
    strike          DOUBLE      NOT NULL,
    expiry          DATE        NOT NULL,
    contract_type   VARCHAR     NOT NULL,   -- 'call' or 'put'
    open            DOUBLE,
    high            DOUBLE,
    low             DOUBLE,
    close           DOUBLE,
    volume          DOUBLE,
    vwap            DOUBLE,
    transactions    INTEGER,                -- n field from Polygon — used for staleness filter
    mid_price       DOUBLE,                 -- computed: (open + close) / 2
    iv_computed     DOUBLE,                 -- BSM inversion result, NULL if failed
    delta           DOUBLE,                 -- analytical from BSM
    gamma           DOUBLE,
    PRIMARY KEY (date, ticker)
);

CREATE TABLE IF NOT EXISTS stock_daily (
    date            DATE        NOT NULL,
    ticker          VARCHAR     NOT NULL,
    open            DOUBLE,
    high            DOUBLE,
    low             DOUBLE,
    close           DOUBLE,
    volume          DOUBLE,
    realized_vol_20d    DOUBLE,             -- annualized, computed from 20d returns
    ewma_var            DOUBLE,             -- EWMA variance, lambda=0.94
    PRIMARY KEY (date, ticker)
);

CREATE TABLE IF NOT EXISTS market_daily (
    date            DATE        NOT NULL PRIMARY KEY,
    vix             DOUBLE,
    vix3m           DOUBLE,
    hy_oas          DOUBLE,                 -- BAML HY OAS from FRED
    t_bill_3m       DOUBLE,                 -- 3-month T-bill rate from FRED (for BSM)
    dspx            DOUBLE,                 -- CBOE implied dispersion
    cor1m           DOUBLE                  -- CBOE 1-month implied correlation
);

CREATE TABLE IF NOT EXISTS signals (
    date            DATE        NOT NULL,
    ticker          VARCHAR     NOT NULL,
    activity_score  DOUBLE,
    p_magnitude     DOUBLE,
    p_up            DOUBLE,
    signal          VARCHAR,                -- 'LONG', 'SHORT', 'NO_DIRECTION', 'WATCH'
    confidence      VARCHAR,                -- 'high', 'medium', 'low'
    threshold_pct   DOUBLE,                 -- k_i * sigma_i expressed as percentage
    top_features    VARCHAR,                -- JSON string of top 3 SHAP features
    leakage_caveats VARCHAR,                -- JSON array of known caveats
    PRIMARY KEY (date, ticker)
);
```

---

## Settings

```python
# settings.py — change values here only, never hardcode in src/

# Universe
UNIVERSE_TICKER = "QQQ"           # proxy for NDX-100 membership
NDX_MEMBERSHIP_URL = "https://en.wikipedia.org/wiki/Nasdaq-100"

# Signal
HORIZON_DAYS = 5
BASE_RATE_TARGET = 0.30            # calibrate k_i so ~30% of obs are positive targets
STAGE_B_THRESHOLD = 0.55           # p_magnitude threshold to pass to Stage C
DIRECTION_HIT_RATE_TARGET = 0.55   # empirical threshold calibration target

# Stage A
MIN_OPTION_VOLUME_20D = 500        # contracts/day avg, below this excluded
MIN_TRANSACTIONS_FOR_IV = 10       # Polygon `n` field — staleness filter for BSM
MIN_HISTORY_DAYS = 252
OPEX_BUFFER_DAYS = 3
EARNINGS_BUFFER_DAYS = 5
STAGE_A_BASE = 5
STAGE_A_SLOPE = 5
STAGE_A_MAX = 25

# Options chain scope — the key rate-limit lever
# 7 strikes × 3 expiries × 2 types = 42 contracts per stock maximum
STRIKES_EACH_SIDE = 3              # ATM ± this many strikes
NUM_EXPIRIES = 3                   # nearest N expiries between 14 and 60 DTE
MIN_DTE = 14
MAX_DTE = 60

# IV computation
EWMA_LAMBDA = 0.94                 # RiskMetrics standard

# Model
IV_BASELINE_MIN_AUC_LIFT = 0.03    # LightGBM must beat IV-only logistic by this
TRAINING_WINDOW_YEARS = 2
CV_N_FOLDS = 5
CV_EMBARGO_DAYS = 5                # purged k-fold embargo
REFIT_FREQUENCY_MONTHS = 3

# Monitoring thresholds
CALIBRATION_ERROR_MAX = 0.10       # max acceptable decile calibration error
HIT_RATE_MIN = 0.50                # below this triggers pause warning
MARGINAL_AUC_MIN = 0.02            # below this 30d rolling avg → low value add warning
KS_DRIFT_ALERT = 0.25              # feature drift alert
KS_DRIFT_PAUSE = 0.30              # feature drift pause

# Data
POLYGON_BASE = "https://api.polygon.io"
RATE_LIMIT_CALLS_PER_MIN = 5
RATE_LIMIT_SLEEP = 12.5            # seconds between Polygon calls (conservative)
FRED_SERIES = {
    "hy_oas": "BAMLH0A0HYM2",
    "t_bill_3m": "DGS3MO",
}
CBOE_DSPX_URL = "https://www.cboe.com/us/options/market_statistics/daily/"
```

---

## Source files — full specification

### `src/pull.py` — all data fetching

Responsibilities:
- Fetch NDX-100 membership from Wikipedia (weekly cache)
- Fetch stock OHLCV from yfinance for all NDX-100 names
- Fetch options OHLCV from Polygon for ATM window contracts only
- Fetch VIX and VIX3M from yfinance
- Fetch HY OAS and T-bill rate from FRED
- Fetch DSPX and COR1M from CBOE website
- Store everything raw into DuckDB (no computation here)

Key constraint: Polygon rate limiter must be a token bucket, not a simple sleep.
If a call fails with 429, back off exponentially. Never crash on a single failed call.

```python
class PolygonRateLimiter:
    """Token bucket: 5 calls per 60 seconds."""
    # implement properly — not just time.sleep(12.5)
    # track actual call timestamps, compute real wait time needed

def fetch_options_chain(ticker: str, date: str) -> list[dict]:
    """
    Fetch daily OHLCV for options contracts in the ATM window.
    
    Scope: strikes within STRIKES_EACH_SIDE of ATM, expirations
    between MIN_DTE and MAX_DTE, both calls and puts.
    
    Staleness check: only keep contracts where transactions >= MIN_TRANSACTIONS_FOR_IV.
    This prevents BSM inversion on stale last-traded prices.
    
    Returns list of raw contract dicts ready for storage.
    """
```

Contract ticker format for Polygon daily OHLCV:
`/v2/aggs/ticker/O:AAPL240119C00180000/range/1/day/{date}/{date}`

One call per contract. This is why the rate limit math matters.
Document actual observed pull time in a comment after first run.

### `src/compute.py` — IV, Greeks, feature engineering

Responsibilities:
- Compute mid_price, IV, delta, gamma for each contract-day
- Compute all 15 features for each stock-day
- Store computed values back to DuckDB

**BSM inversion:**

```python
def compute_iv(mid_price, S, K, T, r, flag) -> Optional[float]:
    """
    Compute implied volatility via BSM inversion.
    
    Guard conditions (return None if any fail):
    - mid_price <= 0
    - T <= 0
    - mid_price >= S (call) or mid_price >= K (put) — arbitrage violation
    - BSM convergence fails after 100 iterations
    - Resulting IV < 0.01 or > 5.0 (clearly wrong)
    
    Uses py_vollib. Failures are expected on illiquid strikes — log at DEBUG
    level only, do not warn. Only ATM contracts are critical.
    """
```

**Feature computation:**

All features are Z-scored against a rolling per-stock 252-day window
unless explicitly noted as a level (vix_level, dspx_level).

Feature computation order matters — compute in this sequence:
1. Realized vol and EWMA variance from stock returns (needs stock_daily)
2. ATM IV identification (find contract closest to delta=0.5 call)
3. IV features (iv_atm_30dte, iv_pct_rank, z_iv_minus_ewma_rv)
4. Skew (25-delta put IV minus 25-delta call IV — find by delta proximity)
5. Term structure (60-DTE IV / 30-DTE IV ratio)
6. Volume ratio (call volume / put volume for the day)
7. Stock return features (5-day log return, Z-score)
8. Market features (VIX, VIX3M, sector OAS — from market_daily)
9. CW IV spread (ATM call IV minus ATM put IV — signed, Stage C only)

**25-delta identification from computed IV:**

Since we don't have vendor-supplied delta, we compute it analytically:
```python
from scipy.stats import norm
def bsm_delta(S, K, T, r, sigma, flag):
    d1 = (log(S/K) + (r + 0.5*sigma**2)*T) / (sigma * sqrt(T))
    if flag == 'c':
        return norm.cdf(d1)
    else:
        return norm.cdf(d1) - 1

# Find the contract whose delta is closest to 0.25 (put) or 0.50 (call)
```

### `src/screen.py` — Stage A

Responsibilities:
- Apply hard exclusions
- Compute ActivityScore_relative
- Return top N candidates for the day

Hard exclusions are applied first. Each exclusion returns a reason string.
Log all exclusions at INFO level so the user can see why stocks were dropped.

```python
def apply_exclusions(ticker: str, date: str, db) -> tuple[bool, str]:
    """Returns (passes, reason_if_excluded)."""
    # Check in this order:
    # 1. earnings window
    # 2. ex-dividend window
    # 3. OpEx window
    # 4. insufficient history
    # 5. insufficient option volume
```

ActivityScore_relative:
```python
score_stock = (
    0.5 * z_score(call_put_volume_ratio, ticker, date, window=252)
  + 0.3 * z_score(iv_pct_rank, ticker, date, window=252)
  + 0.2 * z_score(iv_minus_ewma_rv, ticker, date, window=252)
)
score_qqq = same_formula_for_qqq_options(date)
score_relative = score_stock - score_qqq
```

### `src/model.py` — Stage B and C

Responsibilities:
- Training: purged k-fold CV, fit LightGBM, Platt calibration
- Inference: load saved model, predict probabilities
- Quarterly refit coordination

**Purged k-fold CV — implement from scratch:**

```python
class PurgedKFold:
    """
    K-fold CV for time-series data with overlapping forward returns.
    
    For each test fold:
    1. Purge: remove training observations whose forward return window
       overlaps with any test observation.
       Overlap occurs when |train_date - test_date| < HORIZON_DAYS.
    2. Embargo: additionally remove EMBARGO_DAYS before the test fold
       and after the test fold.
    
    This prevents any form of temporal leakage.
    Do not use sklearn TimeSeriesSplit — it does not purge overlapping labels.
    """
```

**IV-only baseline — mandatory:**

```python
def train_iv_baseline(X, y) -> float:
    """
    Fit logistic regression on iv_atm_30dte only.
    Return AUC on the purged CV folds.
    This is the benchmark LightGBM must beat by >= IV_BASELINE_MIN_AUC_LIFT.
    If not, emit a WARNING: 'Model is not adding value beyond raw IV.'
    """
```

**Target variable:**

```python
# Stage B target: stock-vol-normalized magnitude
sigma_window = realized_vol_20d * sqrt(HORIZON_DAYS / 252)
k_i = calibrate_k(ticker, historical_returns, BASE_RATE_TARGET)
threshold = k_i * sigma_window
y_magnitude = abs(log(close_t5 / open_t1)) > threshold

# Stage C target: direction
y_direction = log(close_t5 / open_t1) > 0

# Note: both use Open[t+1] to Close[t+5], NOT Close[t] to Close[t+5]
# This matches the user's actual execution window.
```

**Empirical threshold calibration for Stage C:**

Do not hardcode 0.58/0.42. After fitting Stage C, find the p_up threshold
where the historical hit rate on the validation fold equals
`DIRECTION_HIT_RATE_TARGET`. These thresholds will be asymmetric.
Save them alongside the model artifact.

**SHAP pruning — regime-conditional:**

```python
def prune_features(model, X, vix_series) -> list[str]:
    """
    Compute SHAP on calm regime (bottom VIX tercile)
    and stressed regime (top VIX tercile) separately.
    
    Keep feature if mean|SHAP| > 0.005 in EITHER regime.
    Drop only if weak in BOTH.
    
    Return list of kept feature names.
    Log dropped features at INFO level.
    """
```

### `src/output.py` — signal writing

Responsibilities:
- Write `watchlists/YYYY-MM-DD.json` as source of truth
- Render `watchlists/YYYY-MM-DD.md` from the JSON
- Never write markdown directly — always JSON first, then render

JSON schema:
```json
{
  "date": "2026-05-21",
  "generated_at": "2026-05-21T17:42:00",
  "market": {
    "regime": "elevated",
    "vix": 22.3,
    "vix_3d_change_zscore": 1.2,
    "dspx": 18.4,
    "hy_oas_5d_change_zscore": 0.8
  },
  "screening": {
    "universe_size": 100,
    "after_exclusions": 87,
    "candidates_screened": 14,
    "magnitude_passes": 4
  },
  "signals": [
    {
      "ticker": "NVDA",
      "activity_score": 2.34,
      "p_magnitude": 0.68,
      "iv_baseline_p": 0.62,
      "marginal_lift": 0.06,
      "p_up": 0.31,
      "long_threshold": 0.59,
      "short_threshold": 0.43,
      "signal": "SHORT",
      "confidence": "medium",
      "threshold_pct": 5.1,
      "top_features": ["skew_pct_rank_252d", "z_iv_minus_ewma_rv", "term_structure_60_30"],
      "leakage_caveats": ["forward_earnings_calendar"]
    }
  ],
  "health": {
    "iv_baseline_auc_lift_30d": 0.038,
    "stage_b_calibration_error_30d": 0.07,
    "last_refit": "2026-04-01",
    "next_refit": "2026-07-01",
    "pause_warnings": []
  }
}
```

Markdown rendering — produce this structure:
```markdown
# Watchlist — 2026-05-21

**Market:** ELEVATED (VIX 22.3, +1.2σ 3d) | DSPX 18.4 | HY OAS +0.8σ

Screened: 87 stocks → 14 candidates → 4 magnitude passes → 3 signals

---

## Signals

### NVDA — SHORT ● medium confidence
Move probability: 68% | Direction: down (p_up=0.31)
Stock-adjusted threshold: ±5.1% over 5 days
Key drivers: skew_pct_rank, iv_vs_realized, term_structure
⚠️ Caveat: forward earnings calendar used

### META — LONG ● high confidence
...

### AMD — WATCH (move likely, direction uncertain)
...

---

## System health
IV baseline lift (30d): 0.038 ✅ | Calibration error (30d): 0.07 ✅
Last refit: 2026-04-01 | Next: 2026-07-01
```

### `src/monitor.py` — monthly health checks

Responsibilities:
- Stage B decile calibration (10 buckets, realized vs predicted frequency)
- Stage C hit rate by signal type with 80% bootstrap CI
- Marginal AUC lift over IV baseline, rolling 30 days
- KS-statistic drift detection per feature
- Pause condition evaluation
- Write `monitoring/YYYY-MM.md`

```python
def bootstrap_ci(data: list[float], stat_fn, n_boot=1000, ci=0.80) -> tuple:
    """
    Compute bootstrap confidence interval for a statistic.
    Returns (point_estimate, lower_bound, upper_bound).
    """

def check_pause_conditions(db) -> list[str]:
    """
    Returns list of active pause warnings.
    Empty list = system healthy.
    
    Conditions checked (point estimate AND lower 80% CI must violate):
    - Stage B calibration error > CALIBRATION_ERROR_MAX in 3+ deciles
    - Stage C hit rate < HIT_RATE_MIN across all signal types
    - LONG hit rate > SHORT hit rate (asymmetry reversed)
    - 30d marginal AUC lift < MARGINAL_AUC_MIN
    - Any feature KS > KS_DRIFT_PAUSE
    """
```

---

## Entry points

### `scripts/run_daily.py`

```python
"""
Run every evening after US market close.
Suggested time: 5:30 PM ET (after 4:00 PM close, data finalized by 5:00 PM).

Manual usage: python scripts/run_daily.py
Cron example: 30 17 * * 1-5 cd /path/to/ndx_watchlist && python scripts/run_daily.py
"""

def main():
    date = today()
    log.info(f"Starting daily run for {date}")

    # 1. Pull all data
    pull.run(date)

    # 2. Compute features
    compute.run(date)

    # 3. Screen candidates
    candidates = screen.run(date)
    log.info(f"Stage A: {len(candidates)} candidates")

    # 4. Generate signals
    signals = model.predict(candidates, date)
    log.info(f"Stage B/C: {len([s for s in signals if s.signal != 'WATCH'])} signals")

    # 5. Write output
    output.write(date, signals)
    log.info(f"Watchlist written: watchlists/{date}.md")

    # 6. Daily health check (abbreviated monitoring)
    warnings = monitor.daily_check(date)
    if warnings:
        log.warning(f"Health warnings: {warnings}")
```

### `scripts/backfill.py`

```python
"""
One-time: pull 2 years of options OHLCV history from Polygon.
Run once after setting up the project. Expect 1-14 hours depending
on whether bulk or per-contract endpoint is used.
Logs progress so it can be interrupted and resumed.
"""
```

### `scripts/train.py`

```python
"""
Run quarterly (first trading day of Jan, Apr, Jul, Oct).
Fits Stage B and Stage C models on trailing 2 years of data.
Saves models to models/ with date stamp.
Prints comparison: new model AUC vs previous model AUC.
"""
```

---

## Dependencies — the complete list

```toml
[project]
name = "ndx-watchlist"
version = "0.1.0"
requires-python = ">=3.11"

dependencies = [
    "polygon-api-client>=1.14.0",
    "yfinance>=0.2.40",
    "fredapi>=0.5.0",
    "requests>=2.31.0",
    "beautifulsoup4>=4.12.0",
    "duckdb>=0.10.0",
    "pandas>=2.2.0",
    "numpy>=1.26.0",
    "scipy>=1.12.0",
    "py_vollib>=1.0.1",
    "lightgbm>=4.3.0",
    "scikit-learn>=1.4.0",
    "shap>=0.45.0",
    "python-dotenv>=1.0.0",
    "rich>=13.7.0",
    "exchange_calendars>=4.5.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-cov>=4.1.0",
    "ruff>=0.3.0",
]
```

Do not add any dependency without asking. Thirteen is enough.

---

## Hard constraints — never violate

1. **No async code.** Synchronous only. Silent failures in async are production killers.
2. **No ORM.** Raw DuckDB SQL only. Schema is in `schema.sql`, the single source of truth.
3. **No web framework.** No Flask, FastAPI, Streamlit. Output is files.
4. **No broker integration.** No order submission. No position tracking.
5. **No real-time data.** EOD only. This is a nightly batch system.
6. **All thresholds in `settings.py` only.** No magic numbers in source files.
7. **JSON before markdown.** `output.py` writes JSON first, renders markdown from it.
8. **Log every Polygon call** with URL, status code, and elapsed time. Rate limit debugging requires this.
9. **Fail loudly on startup** if API keys are missing. Never silently produce empty output.
10. **BSM failures are DEBUG, not WARNING.** Illiquid contract failures are expected. Only log WARNING if ATM contracts fail.

---

## What this is not

No backtesting framework. No broker integration. No portfolio construction.
No position sizing. No risk management. No real-time data. No web UI.
No cloud deployment. No database server. No authentication.
No microservices. No async. No ORM. No magic.

It is a nightly script that reads data, runs a model, and writes a markdown file.

---

## Definition of done

- [ ] Both API keys provided and stored in `.env`
- [ ] Pre-build rate limit test run and result documented in `pull.py`
- [ ] `schema.sql` applied and `db.duckdb` initialized
- [ ] `backfill.py` run successfully (2 years of options OHLCV in DB)
- [ ] All 15 features computed and stored for the full backfill period
- [ ] `train.py` run: Stage B beats IV baseline by >= 0.03 AUC
- [ ] First `run_daily.py` completes without crashing
- [ ] First watchlist written to `watchlists/`
- [ ] First `monitoring/` report generated
- [ ] All tests pass

That is the entire project. Build this. Nothing else.
