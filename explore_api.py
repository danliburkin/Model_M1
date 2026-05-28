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
