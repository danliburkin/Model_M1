-- data/schema.sql
-- Source of truth. Apply once with: duckdb data/db.duckdb < data/schema.sql

CREATE TABLE IF NOT EXISTS options_daily (
    date            DATE        NOT NULL,
    ticker          VARCHAR     NOT NULL,
    underlying      VARCHAR     NOT NULL,
    strike          DOUBLE      NOT NULL,
    expiry          DATE        NOT NULL,
    contract_type   VARCHAR     NOT NULL,
    open            DOUBLE,
    high            DOUBLE,
    low             DOUBLE,
    close           DOUBLE,
    volume          DOUBLE,
    vwap            DOUBLE,
    transactions    INTEGER,
    mid_price       DOUBLE,
    iv_computed     DOUBLE,
    delta           DOUBLE,
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
    realized_vol_20d    DOUBLE,
    ewma_var            DOUBLE,
    PRIMARY KEY (date, ticker)
);

CREATE TABLE IF NOT EXISTS market_daily (
    date            DATE        NOT NULL PRIMARY KEY,
    vix             DOUBLE,
    vix3m           DOUBLE,
    hy_oas          DOUBLE,
    t_bill_3m       DOUBLE,
    dspx            DOUBLE,
    cor1m           DOUBLE
);

CREATE TABLE IF NOT EXISTS features_daily (
    date            DATE        NOT NULL,
    ticker          VARCHAR     NOT NULL,
    iv_atm_30dte            DOUBLE,
    iv_pct_rank_252d        DOUBLE,
    iv_minus_ewma_rv        DOUBLE,
    z_iv_minus_ewma_rv      DOUBLE,
    skew_pct_rank_252d      DOUBLE,
    term_structure_60_30    DOUBLE,
    z_call_put_volume_ratio DOUBLE,
    z_5d_return             DOUBLE,
    vix_level               DOUBLE,
    z_vix_3d_change         DOUBLE,
    vix3m_level             DOUBLE,
    z_hy_oas_5d_change      DOUBLE,
    dspx_level              DOUBLE,
    cor1m_level             DOUBLE,
    z_realized_vol_20d      DOUBLE,
    cw_iv_spread            DOUBLE,
    call_put_volume_ratio   DOUBLE,
    PRIMARY KEY (date, ticker)
);

CREATE TABLE IF NOT EXISTS signals (
    date            DATE        NOT NULL,
    ticker          VARCHAR     NOT NULL,
    activity_score  DOUBLE,
    p_magnitude     DOUBLE,
    p_up            DOUBLE,
    signal          VARCHAR,
    confidence      VARCHAR,
    threshold_pct   DOUBLE,
    top_features    VARCHAR,
    leakage_caveats VARCHAR,
    PRIMARY KEY (date, ticker)
);

CREATE TABLE IF NOT EXISTS backfill_progress (
    ticker          VARCHAR     NOT NULL,
    contract_ticker VARCHAR     NOT NULL,
    last_date       DATE,
    PRIMARY KEY (ticker, contract_ticker)
);

CREATE TABLE IF NOT EXISTS pull_log (
    pulled_at       TIMESTAMP   NOT NULL,
    url             VARCHAR     NOT NULL,
    status_code     INTEGER,
    elapsed_ms      DOUBLE
);
