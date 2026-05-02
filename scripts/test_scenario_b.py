"""
End-to-end test harness for Scenario B (deal enrichment).

Posts three Airtable-shaped webhook payloads to the n8n Scenario B webhook and
prints the parsed result of each. The three payloads exercise the diversity
buckets defined in MASTER.md §6:

  A) Hexalogic People (DEAL-0001) — Green ICP, every field populated
  B) Stratacore Capital (DEAL-0009) — Amber ICP, missing website (graceful degradation)
  C) Brightspark Toys (DEAL-0007) — Red ICP, B2C consumer toys (filtering test)

Run:
    python scripts/test_scenario_b.py
    python scripts/test_scenario_b.py --case A          # one only
    python scripts/test_scenario_b.py --base https://your-n8n.app.n8n.cloud/webhook
    python scripts/test_scenario_b.py --dry-run         # print payload, do not POST

Env vars (read from .env at repo root):
    N8N_WEBHOOK_BASE_URL   e.g. https://your-instance.app.n8n.cloud/webhook
    (alias N8N_WEBHOOK_BASE also accepted for backwards compatibility)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parent.parent
SCENARIO_B_PATH = "/scenario-b-deal"


@dataclass
class TestCase:
    label: str
    expected_band: str
    payload: dict


# --- Mock payloads -----------------------------------------------------------
# Match the shape n8n will see: { record_id, deal_id, fields: { ... } }.
# Field keys mirror Airtable column names exactly.

CASES: list[TestCase] = [
    TestCase(
        label="A · Green ICP (Hexalogic People)",
        expected_band="Green",
        payload={
            "record_id": "recHEXALOGIC0001",
            "deal_id": "DEAL-0001",
            "fields": {
                "Deal ID": "DEAL-0001",
                "Company Name": "Hexalogic People",
                "Website": "https://hexalogic.io",
                "Industry": "HR SaaS",
                "Employee Count": 280,
                "Funding Stage": "Series B",
                "Funding Amount": 18000000,
                "Deal Stage": "Qualified",
                "AE Owner": "Elena Martínez",
                "Deal Value (EUR)": 42000,
                "Created Date": "2026-04-12",
                "Current FP&A Tool": "Google Sheets + Excel",
                "Notes": (
                    "Inbound from Q1 webinar. CFO Marc Puig owns evaluation. "
                    "Monthly close currently 8+ days. Board pack still manual."
                ),
            },
        },
    ),
    TestCase(
        label="B · Amber ICP, missing website (Stratacore Capital)",
        expected_band="Amber",
        payload={
            "record_id": "recSTRATACORE009",
            "deal_id": "DEAL-0009",
            "fields": {
                "Deal ID": "DEAL-0009",
                "Company Name": "Stratacore Capital",
                # Website intentionally omitted — tests graceful degradation
                "Industry": "Asset Management",
                "Employee Count": 90,
                "Funding Stage": "Series A",
                "Funding Amount": 8000000,
                "Deal Stage": "Qualified",
                "AE Owner": "Marc Puig",
                "Deal Value (EUR)": 45000,
                "Created Date": "2026-04-21",
                "Current FP&A Tool": "Excel",
                "Notes": (
                    "No website on record — sales rep didn't capture it. "
                    "Need to discover via Clearbit or LinkedIn."
                ),
            },
        },
    ),
    TestCase(
        label="C · Red ICP, B2C toys (Brightspark Toys)",
        expected_band="Red",
        payload={
            "record_id": "recBRIGHTSPARK07",
            "deal_id": "DEAL-0007",
            "fields": {
                "Deal ID": "DEAL-0007",
                "Company Name": "Brightspark Toys",
                "Website": "https://brightsparktoys.es",
                "Industry": "Consumer E-commerce (Toys)",
                "Employee Count": 35,
                "Funding Stage": "Bootstrapped",
                "Funding Amount": 0,
                "Deal Stage": "Qualified",
                "AE Owner": "Elena Martínez",
                "Deal Value (EUR)": 12000,
                "Created Date": "2026-04-15",
                "Current FP&A Tool": "QuickBooks",
                "Notes": (
                    "Came in via website form. B2C, sub-50 employees. "
                    "Likely Red ICP — qualify out unless budget surprises."
                ),
            },
        },
    ),
]


# --- Helpers -----------------------------------------------------------------

def resolve_base_url(cli_base: Optional[str]) -> Optional[str]:
    if cli_base:
        return cli_base.rstrip("/")
    base = os.environ.get("N8N_WEBHOOK_BASE_URL") or os.environ.get("N8N_WEBHOOK_BASE")
    return base.rstrip("/") if base else None


def resolve_airtable_record_ids(deal_ids: list[str]) -> dict[str, str]:
    """Map DEAL-XXXX -> real Airtable record_id (recXXXX). Returns empty dict if
    Airtable isn't configured or unreachable; callers should fall back gracefully."""
    api_key = os.environ.get("AIRTABLE_API_KEY")
    base = os.environ.get("AIRTABLE_BASE_ID")
    table = os.environ.get("AIRTABLE_DEALS_TABLE", "Deals")
    if not (api_key and base):
        return {}
    # filterByFormula: OR({Deal ID}='DEAL-0001', {Deal ID}='DEAL-0009', ...)
    clauses = ",".join(f"{{Deal ID}}='{d}'" for d in deal_ids)
    formula = f"OR({clauses})" if len(deal_ids) > 1 else clauses
    try:
        r = httpx.get(
            f"https://api.airtable.com/v0/{base}/{table}",
            params={"filterByFormula": formula, "maxRecords": 50},
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15,
        )
        r.raise_for_status()
    except httpx.HTTPError as e:
        print(f"  warn: could not resolve Airtable record ids ({e}); using payload IDs as-is",
              file=sys.stderr)
        return {}
    out: dict[str, str] = {}
    for rec in r.json().get("records", []):
        deal_id = rec.get("fields", {}).get("Deal ID")
        if deal_id:
            out[deal_id] = rec["id"]
    return out


def post_case(base: str, case: TestCase, *, timeout: float, dry_run: bool) -> dict:
    url = f"{base}{SCENARIO_B_PATH}"
    if dry_run:
        return {"_dry_run": True, "url": url, "payload": case.payload}

    started = time.perf_counter()
    try:
        r = httpx.post(url, json=case.payload, timeout=timeout)
    except httpx.RequestError as e:
        return {"_network_error": str(e), "url": url}
    elapsed_ms = int((time.perf_counter() - started) * 1000)

    out: dict = {"_status": r.status_code, "_elapsed_ms": elapsed_ms, "url": url}
    try:
        out["body"] = r.json()
    except Exception:
        out["body_text"] = r.text[:1000]
    return out


def extract_summary(resp: dict) -> dict:
    """Pull the key result fields out of the workflow's last-node JSON."""
    if resp.get("_dry_run") or resp.get("_network_error"):
        return resp
    body = resp.get("body") or {}
    # n8n's lastNode response with our render-deal-Block-Kit code returns:
    #   { enrichment: {...}, deal: {...}, slack_payload: {...} }
    # but Slack/Langfuse downstream nodes wrap further. We dig through a few
    # likely shapes.
    candidates = [body, body.get("data"), body.get("json"), body.get("enrichment")]
    enrich = next((c for c in candidates if isinstance(c, dict) and "icp_score" in c), None)
    if not enrich and isinstance(body.get("enrichment"), dict):
        enrich = body["enrichment"]

    if enrich:
        return {
            "http_status": resp["_status"],
            "elapsed_ms": resp["_elapsed_ms"],
            "enrichment_status": enrich.get("enrichment_status"),
            "icp_score": enrich.get("icp_score"),
            "icp_band": enrich.get("icp_band"),
            "data_confidence": enrich.get("data_confidence"),
            "missing_signals": [k for k in ("company_overview", "headcount", "funding_stage", "current_fpa_stack", "why_they_might_buy") if not enrich.get(k)],
        }
    return {
        "http_status": resp["_status"],
        "elapsed_ms": resp["_elapsed_ms"],
        "note": "no enrichment object in response — workflow ack-only or webhook returned early",
        "raw_keys": list(body.keys()) if isinstance(body, dict) else None,
    }


# --- CLI ---------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", help="n8n webhook base URL (overrides env)")
    parser.add_argument("--case", choices=["A", "B", "C"], help="run one case only")
    parser.add_argument("--timeout", type=float, default=120.0, help="per-request timeout (s)")
    parser.add_argument("--dry-run", action="store_true", help="print payload, do not POST")
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    base = resolve_base_url(args.base)
    if not base and not args.dry_run:
        print(
            "error: set N8N_WEBHOOK_BASE_URL in .env (or pass --base, or use --dry-run)",
            file=sys.stderr,
        )
        return 2

    targets = [c for c in CASES if not args.case or c.label.startswith(args.case)]

    # Resolve real Airtable record_ids from Deal IDs so the workflow's
    # 'Airtable — get deal record' GET hits the actual record. Without this,
    # the test posts placeholder ids like recHEXALOGIC0001 that 404 against
    # Airtable, and continueOnFail masks the failure.
    if not args.dry_run:
        deal_ids = [c.payload["deal_id"] for c in targets]
        id_map = resolve_airtable_record_ids(deal_ids)
        for c in targets:
            real = id_map.get(c.payload["deal_id"])
            if real:
                c.payload["record_id"] = real
            else:
                print(f"  warn: no Airtable record found for {c.payload['deal_id']}; "
                      f"keeping placeholder {c.payload['record_id']!r}", file=sys.stderr)

    print(f"Posting {len(targets)} case(s) to {base or '(dry-run)'}{SCENARIO_B_PATH}\n")

    overall_ok = True
    for i, case in enumerate(targets, 1):
        print(f"── [{i}/{len(targets)}] {case.label}  (expected band: {case.expected_band}) ──")
        resp = post_case(base or "https://dry.run", case, timeout=args.timeout, dry_run=args.dry_run)
        summary = extract_summary(resp)
        print(json.dumps(summary, indent=2, ensure_ascii=False))

        # Quick assertion — warn (don't fail) on mismatch since real network is involved.
        if not args.dry_run and isinstance(summary, dict) and summary.get("icp_band"):
            actual = summary["icp_band"]
            if actual != case.expected_band:
                overall_ok = False
                print(f"  ⚠️ band mismatch: got {actual!r}, expected {case.expected_band!r}")
            else:
                print(f"  ✅ band matches expectation: {actual}")
        elif not args.dry_run:
            overall_ok = False
            print("  ⚠️ no icp_band in response — workflow may have errored or returned early ack")
        print()

    if not overall_ok:
        return 1
    print("All cases ran cleanly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
