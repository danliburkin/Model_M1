# Massive.com Free Tier API Report

Generated: 2026-05-24T12:46:52.448442

---

## Summary

- ✅ **Block 1a — AAPL contract reference** — HTTP 200
- ✅ **Block 1b — SPX index options reference** — HTTP 200
- ✅ **Block 2 — Daily OHLCV bars** — HTTP 200
- ❌ **Block 3 — Single contract snapshot (Greeks/IV/OI test)** — HTTP 403
- ❌ **Block 4 — Full chain snapshot** — HTTP 403
- ✅ **Block 5 — Minute aggregates** — HTTP 200
- ✅ **Block 6 — Technical indicators (SMA)** — HTTP 200

---

## Block 1a — AAPL contract reference

**HTTP status:** 200

**Fields returned:**

| Field | Type | Sample |
|---|---|---|
| `results[0].cfi` | str | OCASPS |
| `results[0].contract_type` | str | call |
| `results[0].exercise_style` | str | american |
| `results[0].expiration_date` | str | 2026-05-26 |
| `results[0].primary_exchange` | str | BATO |
| `results[0].shares_per_contract` | int | 100 |
| `results[0].strike_price` | int | 220 |
| `results[0].ticker` | str | O:AAPL260526C00220000 |
| `results[0].underlying_ticker` | str | AAPL |
| `status` | str | OK |
| `request_id` | str | f608fae547f03aaae2bf5567995d6633 |
| `next_url` | str | https://api.polygon.io/v3/reference/options/contracts?cursor=YXA9JTdCJTIySUQlMjI |

## Block 1b — SPX index options reference

**HTTP status:** 200

**Fields returned:**

| Field | Type | Sample |
|---|---|---|
| `status` | str | OK |
| `request_id` | str | 6b32b2ba3c5f4ad73b739028c2b88bec |

## Block 2 — Daily OHLCV bars

**HTTP status:** 200

**Fields returned:**

| Field | Type | Sample |
|---|---|---|
| `ticker` | str | O:AAPL260526C00220000 |
| `queryCount` | int | 3 |
| `resultsCount` | int | 3 |
| `adjusted` | bool | True |
| `results[0].v` | int | 6 |
| `results[0].vw` | float | 72.8583 |
| `results[0].o` | float | 73.85 |
| `results[0].c` | int | 72 |
| `results[0].h` | float | 73.85 |
| `results[0].l` | float | 71.95 |
| `results[0].t` | int | 1778472000000 |
| `results[0].n` | int | 6 |
| `status` | str | DELAYED |
| `request_id` | str | 8ef4f63b089d03ce6388ae59fdd41daf |
| `count` | int | 3 |

## Block 3 — Single contract snapshot (Greeks/IV/OI test)

**HTTP status:** 403

**Fields returned:**

| Field | Type | Sample |
|---|---|---|
| `status` | str | NOT_AUTHORIZED |
| `request_id` | str | 71217d62a694e07b0ec5507c3324a95e |
| `message` | str | You are not entitled to this data. Please upgrade your plan at https://massive.c |

## Block 4 — Full chain snapshot

**HTTP status:** 403

**Fields returned:**

| Field | Type | Sample |
|---|---|---|
| `status` | str | NOT_AUTHORIZED |
| `request_id` | str | c56cc259bb7e296d033e9c4f43944ece |
| `message` | str | You are not entitled to this data. Please upgrade your plan at https://massive.c |

## Block 5 — Minute aggregates

**HTTP status:** 200

**Fields returned:**

| Field | Type | Sample |
|---|---|---|
| `ticker` | str | O:AAPL260526C00220000 |
| `queryCount` | int | 6 |
| `resultsCount` | int | 6 |
| `adjusted` | bool | True |
| `results[0].v` | int | 2 |
| `results[0].vw` | float | 73.825 |
| `results[0].o` | float | 73.85 |
| `results[0].c` | float | 73.8 |
| `results[0].h` | float | 73.85 |
| `results[0].l` | float | 73.8 |
| `results[0].t` | int | 1778513460000 |
| `results[0].n` | int | 2 |
| `status` | str | DELAYED |
| `request_id` | str | 1d75f66bb878d8c89abb935c38395378 |
| `count` | int | 6 |
| `next_url` | str | https://api.polygon.io/v2/aggs/ticker/O:AAPL260526C00220000/range/1/minute/17794 |

## Block 6 — Technical indicators (SMA)

**HTTP status:** 200

**Fields returned:**

| Field | Type | Sample |
|---|---|---|
| `results.underlying.url` | str | https://api.polygon.io/v2/aggs/ticker/O:AAPL260526C00220000/range/1/day/14016816 |
| `results.values[0].timestamp` | int | 1779422400000 |
| `results.values[0].value` | float | 75.758 |
| `status` | str | OK |
| `request_id` | str | 0e23671c360675622d48bf917916abd4 |

---

## Interpretation guide

Look for these specific fields in the report above:

| What we need | Field to look for | Which block |
|---|---|---|
| Implied volatility | `implied_volatility` or `iv` | Block 3 or 4 |
| Delta | `greeks.delta` or `delta` | Block 3 or 4 |
| Gamma | `greeks.gamma` or `gamma` | Block 3 or 4 |
| Open interest | `open_interest` or `oi` | Block 2, 3, or 4 |
| Bid price | `last_quote.bid` or `bid` | Block 3 or 4 |
| Ask price | `last_quote.ask` or `ask` | Block 3 or 4 |
| Volume | `day.volume` or `v` | Block 2 or 4 |
| SPX index options | any result in Block 1 SPX | Block 1 |
| Minute bars | any result | Block 5 |
