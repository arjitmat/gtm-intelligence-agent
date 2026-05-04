"""
End-to-end audit of the GTM Intelligence System's data quality.

Verifies:
  1. Airtable Deals — record count, ICP / enrichment / status distribution,
     per-record completeness, and which records lack enrichment.
  2. Supabase tables — competitor_signals, competitor_battlecards,
     deal_enrichments, digest_runs row counts and recency.
  3. Mock data quality — diversity of the mock data set per MASTER §6
     (some companies rich, some scarce), to demonstrate "real-life practical"
     mock data design (not all green-path, not all happy data).

Run:
    python scripts/audit_data_quality.py

Reads .env at repo root. No writes. No side effects.
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

import httpx
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

AIRTABLE_DEALS_TABLE_ID = "tblKML2oup6YpKHAw"

GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


def hdr(s: str) -> None:
    print(f"\n{BOLD}━━━ {s} ━━━{RESET}")


def ok(s: str) -> None:
    print(f"  {GREEN}✓{RESET} {s}")


def warn(s: str) -> None:
    print(f"  {YELLOW}⚠{RESET}  {s}")


def err(s: str) -> None:
    print(f"  {RED}✗{RESET} {s}")


def kv(k: str, v) -> None:
    print(f"    {DIM}{k:<28}{RESET} {v}")


# ─── Airtable ────────────────────────────────────────────────────────────────


def fetch_airtable_deals() -> list[dict]:
    base_id = os.environ["AIRTABLE_BASE_ID"]
    api_key = os.environ["AIRTABLE_API_KEY"]
    url = f"https://api.airtable.com/v0/{base_id}/{AIRTABLE_DEALS_TABLE_ID}"
    headers = {"Authorization": f"Bearer {api_key}"}
    rows: list[dict] = []
    offset = None
    while True:
        params = {"pageSize": 100}
        if offset:
            params["offset"] = offset
        r = httpx.get(url, headers=headers, params=params, timeout=30)
        r.raise_for_status()
        body = r.json()
        rows.extend(body.get("records", []))
        offset = body.get("offset")
        if not offset:
            break
    return rows


def audit_airtable() -> None:
    hdr("AIRTABLE · Deals table")
    try:
        records = fetch_airtable_deals()
    except Exception as e:
        err(f"airtable fetch failed: {e}")
        return

    ok(f"records found: {len(records)}")
    if not records:
        warn("No deal records — cannot audit")
        return

    enrichment_counter: Counter[str] = Counter()
    icp_scores: list[int] = []
    missing_enrichment: list[str] = []
    enriched: list[str] = []

    for r in records:
        f = r.get("fields", {})
        deal_id = f.get("Deal ID", "?")
        es = f.get("Enrichment Status") or "Pending"
        enrichment_counter[es] += 1
        icp = f.get("ICP Score")
        if isinstance(icp, (int, float)):
            icp_scores.append(int(icp))
        if es in ("Pending", "Failed", None) or icp is None:
            missing_enrichment.append(deal_id)
        else:
            enriched.append(deal_id)

    print()
    print(f"  Enrichment Status breakdown:")
    for status, cnt in enrichment_counter.most_common():
        kv(status, cnt)

    if icp_scores:
        print()
        print(f"  ICP Score distribution (across {len(icp_scores)} enriched):")
        bands = {"Green (≥75)": 0, "Amber (40–74)": 0, "Red (<40)": 0}
        for s in icp_scores:
            if s >= 75:
                bands["Green (≥75)"] += 1
            elif s >= 40:
                bands["Amber (40–74)"] += 1
            else:
                bands["Red (<40)"] += 1
        for band, cnt in bands.items():
            kv(band, cnt)
        kv("min / mean / max", f"{min(icp_scores)} / {sum(icp_scores) // len(icp_scores)} / {max(icp_scores)}")

    print()
    if enriched:
        ok(f"enriched: {', '.join(enriched)}")
    if missing_enrichment:
        warn(f"not enriched (fire scenario_b for these to demo): {', '.join(missing_enrichment)}")


# ─── Supabase ────────────────────────────────────────────────────────────────


def supabase_get(path: str, params: dict | None = None) -> tuple[int, list]:
    url = f"{os.environ['SUPABASE_URL'].rstrip('/')}/rest/v1/{path}"
    headers = {
        "apikey": os.environ["SUPABASE_SERVICE_ROLE_KEY"],
        "Authorization": f"Bearer {os.environ['SUPABASE_SERVICE_ROLE_KEY']}",
        "Prefer": "count=exact",
    }
    r = httpx.get(url, headers=headers, params=params or {}, timeout=30)
    r.raise_for_status()
    cr = r.headers.get("content-range", "0-0/0")
    total = int(cr.split("/")[-1]) if "/" in cr else len(r.json())
    return total, r.json()


def audit_supabase() -> None:
    hdr("SUPABASE · pgvector knowledge layer")

    try:
        total_signals, signals = supabase_get("competitor_signals", {"select": "competitor_name,priority_tier,signal_type,scraped_at,human_approved", "order": "scraped_at.desc", "limit": "200"})
        ok(f"competitor_signals: {total_signals} rows")
        if signals:
            by_comp = Counter(s["competitor_name"] for s in signals)
            by_prio = Counter(s["priority_tier"] for s in signals)
            by_type = Counter(s["signal_type"] for s in signals)
            approved = sum(1 for s in signals if s.get("human_approved"))
            print()
            print(f"  by competitor:")
            for c, n in by_comp.most_common():
                kv(c, n)
            print()
            print(f"  by priority:")
            for p, n in by_prio.most_common():
                kv(p, n)
            print()
            print(f"  by signal_type:")
            for t, n in by_type.most_common():
                kv(t, n)
            print()
            kv("human_approved", f"{approved}/{len(signals)} (RAG-eligible)")
            most_recent = signals[0]["scraped_at"][:10]
            kv("most recent scraped_at", most_recent)
    except Exception as e:
        err(f"competitor_signals query failed: {e}")

    print()
    try:
        total_bc, bcs = supabase_get("competitor_battlecards", {"select": "competitor_name,positioning,strengths,weaknesses,objection_responses,last_synced", "order": "competitor_name.asc"})
        ok(f"competitor_battlecards: {total_bc} rows")
        if bcs:
            for b in bcs:
                pos_len = len(b.get("positioning") or "")
                str_len = len(b.get("strengths") or "")
                wk_len = len(b.get("weaknesses") or "")
                obj_len = len(b.get("objection_responses") or "")
                total_len = pos_len + str_len + wk_len + obj_len
                kv(b["competitor_name"], f"{total_len} chars total · last_synced={b.get('last_synced','?')[:10]}")
    except Exception as e:
        err(f"competitor_battlecards query failed: {e}")

    print()
    try:
        total_de, des = supabase_get("deal_enrichments", {"select": "airtable_deal_id,company_name,enrichment_status,data_confidence,headcount,enriched_at", "order": "enriched_at.desc", "limit": "20"})
        ok(f"deal_enrichments: {total_de} rows  {DIM}(schema-only — Scenario B currently writes back to Airtable, not Supabase. Table is forward-compatible for snapshot history.){RESET}")
        for d in des:
            kv(d.get("airtable_deal_id") or "?", f"{d.get('company_name','?')} · {d.get('enrichment_status','?')} · conf {d.get('data_confidence','?')}%")
    except Exception as e:
        warn(f"deal_enrichments query failed: {e}")

    print()
    print(f"  {DIM}digest_runs table: not in schema (Scenario A persists per-signal rows in competitor_signals + posts the synthesised digest to Slack — there is no separate run-level row currently){RESET}")


# ─── Mock data quality ────────────────────────────────────────────────────────


def audit_mock_data() -> None:
    hdr("MOCK DATA · diversity / 'real-world practical' check")

    deals_csv = REPO_ROOT / "mock_data" / "airtable_deals.csv"
    if deals_csv.exists():
        import csv
        with deals_csv.open() as f:
            reader = csv.DictReader(f)
            deals = list(reader)
        ok(f"airtable_deals.csv: {len(deals)} mock deals")
        with_website = sum(1 for d in deals if (d.get("Website") or "").strip())
        with_industry = sum(1 for d in deals if (d.get("Industry") or "").strip())
        with_emp = sum(1 for d in deals if (d.get("Employees") or "").strip())
        with_funding = sum(1 for d in deals if (d.get("Funding Stage") or "").strip())
        with_fpa = sum(1 for d in deals if (d.get("Current FP&A Tool") or "").strip())
        with_notes = sum(1 for d in deals if (d.get("Notes") or "").strip())
        kv("Website",          f"{with_website}/{len(deals)}")
        kv("Industry",         f"{with_industry}/{len(deals)}")
        kv("Employees",        f"{with_emp}/{len(deals)}")
        kv("Funding Stage",    f"{with_funding}/{len(deals)}")
        kv("Current FP&A Tool", f"{with_fpa}/{len(deals)}")
        kv("Notes",            f"{with_notes}/{len(deals)}")
        print()
        print(f"  {DIM}sparse-data diversity check (real-world demo should NOT be all-rich):{RESET}")
        if with_website == len(deals):
            warn("every deal has a website — mock looks too uniformly clean for a realistic demo")
        elif with_website < len(deals) // 2:
            warn(f"only {with_website}/{len(deals)} deals have a website — too many sparse records")
        else:
            ok(f"healthy mix: {with_website}/{len(deals)} have websites; {len(deals)-with_website} deals exercise graceful-degradation paths in Scenario B")
    else:
        warn("mock_data/airtable_deals.csv not found")

    print()
    bc_dir = REPO_ROOT / "mock_data" / "battlecards"
    if bc_dir.exists():
        cards = sorted(bc_dir.glob("*.md"))
        ok(f"battlecards/: {len(cards)} markdown files")
        for c in cards:
            size = c.stat().st_size
            kv(c.stem, f"{size} bytes")
            if size < 800:
                warn(f"  ↑ {c.stem} battlecard is sparse ({size} bytes) — consider enriching")
    else:
        warn("mock_data/battlecards/ not found")

    print()
    sig_path = REPO_ROOT / "mock_data" / "scenario_a_demo_signals.json"
    if sig_path.exists():
        sig = json.loads(sig_path.read_text())
        signals = sig.get("signals_this_week", [])
        ok(f"scenario_a_demo_signals.json: {len(signals)} signals")
        prio = Counter(s.get("priority_tier") for s in signals)
        comp = Counter(s.get("competitor_name") for s in signals)
        kv("priority mix",   ", ".join(f"{p}:{n}" for p, n in prio.most_common()))
        kv("competitor mix", ", ".join(f"{c}:{n}" for c, n in comp.most_common()))
        no_signal = sum(1 for s in signals if "no significant" in (s.get("headline") or "").lower())
        kv("'no significant' signals", f"{no_signal} (good — shows realistic quiet weeks)")


# ─── Main ────────────────────────────────────────────────────────────────────


def main() -> int:
    print(f"{BOLD}GTM Intelligence Data Quality Audit{RESET}")
    print(f"{DIM}— pre-demo verification, no writes —{RESET}")
    audit_airtable()
    audit_supabase()
    audit_mock_data()
    print()
    print(f"{BOLD}Audit complete.{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
