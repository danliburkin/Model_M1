"""All thresholds and configuration — single source of truth."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "db.duckdb"
SCHEMA_PATH = DATA_DIR / "schema.sql"
WATCHLISTS_DIR = ROOT / "watchlists"
MONITORING_DIR = ROOT / "monitoring"
MODELS_DIR = ROOT / "models"

# API keys — fail loudly if missing
POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")
FRED_API_KEY = os.getenv("FRED_API_KEY")

# Universe
UNIVERSE_TICKER = "QQQ"
NDX_MEMBERSHIP_URL = "https://en.wikipedia.org/wiki/Nasdaq-100"

# Signal
HORIZON_DAYS = 5
BASE_RATE_TARGET = 0.30
STAGE_B_THRESHOLD = 0.55
DIRECTION_HIT_RATE_TARGET = 0.55

# Stage A
MIN_OPTION_VOLUME_20D = 500
MIN_TRANSACTIONS_FOR_IV = 10
MIN_HISTORY_DAYS = 252
OPEX_BUFFER_DAYS = 3
EARNINGS_BUFFER_DAYS = 5
STAGE_A_BASE = 5
STAGE_A_SLOPE = 5
STAGE_A_MAX = 25

# Options chain scope
STRIKES_EACH_SIDE = 3
NUM_EXPIRIES = 3
MIN_DTE = 14
MAX_DTE = 60

# IV computation
EWMA_LAMBDA = 0.94

# Model
IV_BASELINE_MIN_AUC_LIFT = 0.03
TRAINING_WINDOW_YEARS = 2
CV_N_FOLDS = 5
CV_EMBARGO_DAYS = 5
REFIT_FREQUENCY_MONTHS = 3

# Monitoring thresholds
CALIBRATION_ERROR_MAX = 0.10
HIT_RATE_MIN = 0.50
MARGINAL_AUC_MIN = 0.02
KS_DRIFT_ALERT = 0.25
KS_DRIFT_PAUSE = 0.30

# Data
POLYGON_BASE = "https://api.polygon.io"
RATE_LIMIT_CALLS_PER_MIN = 5
RATE_LIMIT_SLEEP = 12.5
FRED_SERIES = {
    "hy_oas": "BAMLH0A0HYM2",
    "t_bill_3m": "DGS3MO",
}
CBOE_DSPX_URL = "https://www.cboe.com/us/options/market_statistics/daily/"

# Feature names (15 model features)
FEATURE_NAMES = [
    "iv_atm_30dte",
    "iv_pct_rank_252d",
    "z_iv_minus_ewma_rv",
    "skew_pct_rank_252d",
    "term_structure_60_30",
    "z_call_put_volume_ratio",
    "z_5d_return",
    "vix_level",
    "z_vix_3d_change",
    "vix3m_level",
    "z_hy_oas_5d_change",
    "dspx_level",
    "cor1m_level",
    "z_realized_vol_20d",
    "cw_iv_spread",
]

STAGE_C_FEATURES = FEATURE_NAMES  # includes cw_iv_spread
STAGE_B_FEATURES = [f for f in FEATURE_NAMES if f != "cw_iv_spread"]

# Cache paths
NDX_CACHE_PATH = DATA_DIR / "ndx_members.json"
NDX_CACHE_DAYS = 7
