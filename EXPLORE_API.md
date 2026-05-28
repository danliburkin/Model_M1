# Massive.com API Explorer

> **Minimal one-task spec for Cursor.**
> Build a single Python script that calls the Massive.com (Polygon.io) free tier Options API
> and prints exactly what fields are returned. Nothing else.
> Read this entire file before writing any code.

---

## Mission

We do not know exactly what the Massive.com free tier returns for options data.
Before building any pipeline, we need ground truth.

This script calls the API, prints the raw response, and produces a
human-readable report of every field available. That report will drive
all future architecture decisions.

---

## Stop and ask first — one question only

Before writing any code, ask the user:

```
[QUESTION 1 of 1]
Context: I need your Massive.com API key to call the options endpoints.
Issue: Without it I cannot run any API calls.
Action needed: Please paste your Massive.com / Polygon.io API key.
It should look like: pk_abc123...
You can find it at: https://massive.com/dashboard
```

Wait for the key. Then proceed.

Store the key in a `.env` file:

```
POLYGON_API_KEY=your_key_here
```

Add `.env` to `.gitignore` immediately.

---

## What to build

One single file: `explore_api.py`

No classes. No abstraction. No pipeline. Just sequential API calls
with `requests`, printing and saving everything.

---

## Dependencies

Minimal. Only these:

```
requests
python-dotenv
rich
```

Install with:
```bash
pip install requests python-dotenv rich
```

---

## The script: `explore_api.py`

The script calls six endpoints in sequence for one test ticker (AAPL)
and one test index option ticker (SPX). For each endpoint it prints:

1. The URL called
2. The HTTP status code
3. The full raw JSON response (pretty-printed)
4. A field inventory: every key found, its type, and a sample value

At the end it writes a markdown report: `api_report.md`

### Exact endpoints to call

#### Block 1 — Reference data (what contracts exist)

```
GET https://api.polygon.io/v3/reference/options/contracts
    ?underlying_ticker=AAPL
    &as_of=TODAY          ← use today's date in YYYY-MM-DD format
    &limit=5
    &apiKey=YOUR_KEY
```

```
GET https://api.polygon.io/v3/reference/options/contracts
    ?underlying_ticker=I:SPX
    &as_of=TODAY
    &limit=5
    &apiKey=YOUR_KEY
```

**We want to know:** do SPX index options work on the free tier?
The prefix `I:` denotes an index underlying in Polygon's notation.

#### Block 2 — Daily aggregate bars for one contract

Take the first contract ticker returned from Block 1 AAPL result.
Call:

```
GET https://api.polygon.io/v2/aggs/ticker/{CONTRACT_TICKER}/range/1/day
    /START_DATE/END_DATE
    ?adjusted=true
    &sort=asc
    &limit=10
    &apiKey=YOUR_KEY
```

Where:
- `START_DATE` = 30 days ago in YYYY-MM-DD
- `END_DATE` = today in YYYY-MM-DD

**We want to know:** do we get OHLCV? Volume? Anything else?

#### Block 3 — Snapshot for one contract (the key test)

```
GET https://api.polygon.io/v3/snapshot/options/AAPL/{CONTRACT_TICKER}
    ?apiKey=YOUR_KEY
```

**We want to know:** does this work on free tier at all?
If yes: do we get IV, Greeks, OI, bid/ask, or just an error?

#### Block 4 — Full chain snapshot (the other key test)

```
GET https://api.polygon.io/v3/snapshot/options/AAPL
    ?limit=5
    &apiKey=YOUR_KEY
```

**We want to know:** does the chain snapshot endpoint work on free tier?
If yes: what fields come back for each contract?

#### Block 5 — Minute aggregates (the Technical Indicators / Minute Aggregates claim)

Take the same contract ticker from Block 1.
Call:

```
GET https://api.polygon.io/v2/aggs/ticker/{CONTRACT_TICKER}/range/1/minute
    /START_DATE/END_DATE
    ?adjusted=true
    &sort=asc
    &limit=5
    &apiKey=YOUR_KEY
```

**We want to know:** do minute bars work on free tier for options?

#### Block 6 — Technical indicators (what "Technical Indicators" means)

```
GET https://api.polygon.io/v1/indicators/sma/{CONTRACT_TICKER}
    ?timespan=day
    &adjusted=true
    &window=10
    &series_type=close
    &limit=5
    &apiKey=YOUR_KEY
```

**We want to know:** does this apply to options contracts or only stocks?
This reveals whether "Technical Indicators" in the free tier is useful
for our options pipeline.

---

## Script structure

```python
#!/usr/bin/env python3
"""
Massive.com / Polygon.io Free Tier API Explorer
Run this before building anything else.
Usage: python explore_api.py
Output: prints to console + writes api_report.md
"""

import os
import json
import datetime
import requests
from dotenv import load_dotenv
from rich import print as rprint
from rich.panel import Panel
from rich.table import Table

load_dotenv()
API_KEY = os.getenv("POLYGON_API_KEY")
BASE = "https://api.polygon.io"
TODAY = datetime.date.today().isoformat()
START = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()

results = {}  # accumulates all findings for the report

def call(label, url, params=None):
    """Call one endpoint, print everything, return parsed response."""
    params = params or {}
    params["apiKey"] = API_KEY
    
    rprint(Panel(f"[bold]{label}[/bold]\n{url}"))
    
    r = requests.get(url, params=params)
    
    rprint(f"Status: [bold {'green' if r.ok else 'red'}]{r.status_code}[/]")
    
    try:
        data = r.json()
    except Exception:
        rprint(f"[red]Response is not JSON:[/] {r.text[:500]}")
        results[label] = {"status": r.status_code, "error": "not_json"}
        return None
    
    # Pretty print the raw response
    rprint("[dim]Raw response:[/]")
    rprint(json.dumps(data, indent=2)[:3000])  # cap at 3000 chars
    
    # Field inventory
    inventory = extract_fields(data)
    if inventory:
        table = Table(title="Field Inventory", show_header=True)
        table.add_column("Field path")
        table.add_column("Type")
        table.add_column("Sample value")
        for path, typ, sample in inventory:
            table.add_row(path, typ, str(sample)[:60])
        rprint(table)
    
    results[label] = {
        "status": r.status_code,
        "ok": r.ok,
        "fields": inventory,
        "raw": data,
    }
    return data


def extract_fields(obj, prefix="", max_depth=4):
    """
    Recursively walk a JSON object and return a flat list of
    (field_path, type_name, sample_value) tuples.
    Works on dicts, lists of dicts, nested structures.
    """
    items = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else k
            if isinstance(v, (dict, list)) and max_depth > 0:
                items.extend(extract_fields(v, path, max_depth - 1))
            else:
                items.append((path, type(v).__name__, v))
    elif isinstance(obj, list) and obj:
        items.extend(extract_fields(obj[0], f"{prefix}[0]", max_depth - 1))
    return items


def write_report():
    """Write api_report.md summarising all findings."""
    lines = [
        "# Massive.com Free Tier API Report",
        f"\nGenerated: {datetime.datetime.now().isoformat()}",
        "\n---\n",
        "## Summary\n",
    ]

    for label, result in results.items():
        status = result.get("status", "?")
        ok = result.get("ok", False)
        emoji = "✅" if ok else "❌"
        lines.append(f"- {emoji} **{label}** — HTTP {status}")

    lines.append("\n---\n")

    for label, result in results.items():
        lines.append(f"## {label}\n")
        lines.append(f"**HTTP status:** {result.get('status', '?')}\n")
        
        if result.get("error"):
            lines.append(f"**Error:** {result['error']}\n")
            continue
            
        fields = result.get("fields", [])
        if fields:
            lines.append("**Fields returned:**\n")
            lines.append("| Field | Type | Sample |")
            lines.append("|---|---|---|")
            for path, typ, sample in fields:
                sample_str = str(sample)[:80].replace("|", "/")
                lines.append(f"| `{path}` | {typ} | {sample_str} |")
        else:
            lines.append("*No fields found or empty response.*")
        
        lines.append("")

    lines.append("---\n")
    lines.append("## Interpretation guide\n")
    lines.append(
        "Look for these specific fields in the report above:\n\n"
        "| What we need | Field to look for | Which block |\n"
        "|---|---|---|\n"
        "| Implied volatility | `implied_volatility` or `iv` | Block 3 or 4 |\n"
        "| Delta | `greeks.delta` or `delta` | Block 3 or 4 |\n"
        "| Gamma | `greeks.gamma` or `gamma` | Block 3 or 4 |\n"
        "| Open interest | `open_interest` or `oi` | Block 2, 3, or 4 |\n"
        "| Bid price | `last_quote.bid` or `bid` | Block 3 or 4 |\n"
        "| Ask price | `last_quote.ask` or `ask` | Block 3 or 4 |\n"
        "| Volume | `day.volume` or `v` | Block 2 or 4 |\n"
        "| SPX index options | any result in Block 1 SPX | Block 1 |\n"
        "| Minute bars | any result | Block 5 |\n"
    )

    with open("api_report.md", "w") as f:
        f.write("\n".join(lines))

    rprint(Panel("[bold green]Report written to api_report.md[/bold green]"))


# ── Main sequence ────────────────────────────────────────────────

if not API_KEY:
    rprint("[bold red]ERROR: POLYGON_API_KEY not set in .env[/bold red]")
    raise SystemExit(1)

rprint(Panel("[bold blue]Massive.com Free Tier API Explorer[/bold blue]"))

# Block 1a — AAPL reference
aapl_ref = call(
    "Block 1a — AAPL contract reference",
    f"{BASE}/v3/reference/options/contracts",
    {"underlying_ticker": "AAPL", "as_of": TODAY, "limit": 5},
)

# Extract first contract ticker for subsequent calls
contract_ticker = None
if aapl_ref and aapl_ref.get("results"):
    contract_ticker = aapl_ref["results"][0].get("ticker")
    rprint(f"[green]Using contract ticker for further tests: {contract_ticker}[/green]")

# Block 1b — SPX index options reference
call(
    "Block 1b — SPX index options reference",
    f"{BASE}/v3/reference/options/contracts",
    {"underlying_ticker": "I:SPX", "as_of": TODAY, "limit": 5},
)

# Block 2 — Daily OHLCV agg bars
if contract_ticker:
    call(
        "Block 2 — Daily OHLCV bars",
        f"{BASE}/v2/aggs/ticker/{contract_ticker}/range/1/day/{START}/{TODAY}",
        {"adjusted": "true", "sort": "asc", "limit": 10},
    )

# Block 3 — Single contract snapshot
if contract_ticker:
    call(
        "Block 3 — Single contract snapshot (Greeks/IV/OI test)",
        f"{BASE}/v3/snapshot/options/AAPL/{contract_ticker}",
    )

# Block 4 — Full chain snapshot
call(
    "Block 4 — Full chain snapshot",
    f"{BASE}/v3/snapshot/options/AAPL",
    {"limit": 5},
)

# Block 5 — Minute aggregates
if contract_ticker:
    call(
        "Block 5 — Minute aggregates",
        f"{BASE}/v2/aggs/ticker/{contract_ticker}/range/1/minute/{START}/{TODAY}",
        {"adjusted": "true", "sort": "asc", "limit": 5},
    )

# Block 6 — Technical indicators
if contract_ticker:
    call(
        "Block 6 — Technical indicators (SMA)",
        f"{BASE}/v1/indicators/sma/{contract_ticker}",
        {"timespan": "day", "adjusted": "true", "window": 10,
         "series_type": "close", "limit": 5},
    )

# Write the report
write_report()

rprint("\n[bold green]Done. Share api_report.md to determine next build steps.[/bold green]")
```

---

## What Cursor must NOT do

- Do not build any pipeline, model, or feature engineering
- Do not add any abstraction, classes, or modules
- Do not install anything beyond `requests`, `python-dotenv`, `rich`
- Do not handle errors beyond printing them clearly
- Do not retry failed requests
- Do not mock any responses

If an endpoint returns a 403, 404, or 401 — print it clearly and move on.
That IS the information we need.

---

## Expected outputs

After running `python explore_api.py` the user will have:

1. **Console output** showing every API response in real time
2. **`api_report.md`** summarising every field returned

The user will then share `api_report.md` and we will decide together
what the full PROJECT.md pipeline can actually use.

---

## Definition of done for this task

- [ ] User has provided API key
- [ ] `.env` file created with key
- [ ] `.gitignore` includes `.env`
- [ ] `explore_api.py` runs without crashing
- [ ] All 6 blocks execute (errors are fine, crashes are not)
- [ ] `api_report.md` is written
- [ ] User shares the report

That is the only deliverable. Nothing else.
