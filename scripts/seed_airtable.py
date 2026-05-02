"""
Seed the Airtable mock CRM with the 12 deals from mock_data/airtable_deals.csv.

What this does:
  1. Reads mock_data/airtable_deals.csv (12 deals across 16 columns).
  2. Creates the 'Deals' table in your Airtable base if missing,
     with field types that match what scenario_b_workflow.json expects.
  3. Bulk-upserts the 12 records, matching on 'Deal ID' so re-runs are idempotent.

Run:
    python scripts/seed_airtable.py
    python scripts/seed_airtable.py --dry-run     # parse + validate, no write
    python scripts/seed_airtable.py --probe       # only inspect base & exit

Env vars (read from .env at repo root):
    AIRTABLE_API_KEY        Personal Access Token. Required scopes:
                              data.records:read, data.records:write,
                              schema.bases:read, schema.bases:write
    AIRTABLE_BASE_ID        Base id (starts with 'app...').

Exit codes: 0 success, 1 partial/failure, 2 configuration error.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = REPO_ROOT / "mock_data" / "airtable_deals.csv"
TABLE_NAME = "Deals"
META_API = "https://api.airtable.com/v0/meta/bases"
DATA_API = "https://api.airtable.com/v0"

# Field schema for table creation. The first field becomes the primary field.
# Types kept conservative (text/number/date/url) to avoid singleSelect option
# enumeration that would need pre-registration of every possible value.
FIELDS: list[dict] = [
    {"name": "Deal ID",           "type": "singleLineText"},
    {"name": "Company Name",      "type": "singleLineText"},
    {"name": "Website",           "type": "url"},
    {"name": "Industry",          "type": "singleLineText"},
    {"name": "Employee Count",    "type": "number",        "options": {"precision": 0}},
    {"name": "Funding Stage",     "type": "singleLineText"},
    {"name": "Funding Amount",    "type": "number",        "options": {"precision": 0}},
    {"name": "Deal Stage",        "type": "singleLineText"},
    {"name": "AE Owner",          "type": "singleLineText"},
    {"name": "Deal Value (EUR)",  "type": "number",        "options": {"precision": 0}},
    {"name": "Created Date",      "type": "date",          "options": {"dateFormat": {"name": "iso"}}},
    {"name": "Current FP&A Tool", "type": "singleLineText"},
    {"name": "Notes",             "type": "multilineText"},
    {"name": "Enrichment Status", "type": "singleLineText"},
    {"name": "ICP Score",         "type": "number",        "options": {"precision": 0}},
    {"name": "Last Enriched",     "type": "date",          "options": {"dateFormat": {"name": "iso"}}},
]
NUMERIC_FIELDS = {"Employee Count", "Funding Amount", "Deal Value (EUR)", "ICP Score"}
DATE_FIELDS = {"Created Date", "Last Enriched"}
ALL_FIELD_NAMES = [f["name"] for f in FIELDS]


# --- CSV parsing -------------------------------------------------------------

def parse_csv(path: Path) -> list[dict]:
    """Read the CSV and coerce numeric fields to int. Drop empty values entirely
    (Airtable rejects empty strings on number/date fields)."""
    deals: list[dict] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        # Sanity-check headers match our expected schema.
        unknown = [h for h in reader.fieldnames or [] if h not in ALL_FIELD_NAMES]
        if unknown:
            print(f"  warn: CSV has columns not in target schema: {unknown}", file=sys.stderr)
        missing = [n for n in ALL_FIELD_NAMES if n not in (reader.fieldnames or [])]
        if missing:
            print(f"  warn: CSV missing expected columns: {missing}", file=sys.stderr)

        for row_idx, row in enumerate(reader, start=2):  # start=2 because header is row 1
            fields: dict = {}
            for k, v in row.items():
                if v is None:
                    continue
                v = v.strip()
                if v == "":
                    continue
                if k in NUMERIC_FIELDS:
                    try:
                        fields[k] = int(v)
                    except ValueError:
                        try:
                            fields[k] = float(v)
                        except ValueError:
                            print(f"  warn: row {row_idx} non-numeric {k}={v!r}; dropping",
                                  file=sys.stderr)
                            continue
                else:
                    fields[k] = v
            deals.append(fields)
    return deals


# --- Airtable schema ops -----------------------------------------------------

def list_tables(api_key: str, base: str) -> list[dict]:
    r = httpx.get(
        f"{META_API}/{base}/tables",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=20,
    )
    r.raise_for_status()
    return r.json().get("tables", [])


def find_table(tables: list[dict], name: str) -> Optional[dict]:
    return next((t for t in tables if t["name"] == name), None)


def create_table(api_key: str, base: str) -> dict:
    body = {
        "name": TABLE_NAME,
        "description": "Mock CRM deals — seeded by scripts/seed_airtable.py",
        "fields": FIELDS,
    }
    r = httpx.post(
        f"{META_API}/{base}/tables",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=body,
        timeout=30,
    )
    if r.status_code >= 300:
        raise RuntimeError(f"create table failed: {r.status_code} {r.text[:600]}")
    return r.json()


def add_missing_fields(api_key: str, base: str, table: dict) -> int:
    """If the table already exists with a partial schema, add the fields it's missing."""
    existing = {f["name"] for f in table.get("fields", [])}
    added = 0
    for spec in FIELDS:
        if spec["name"] in existing:
            continue
        r = httpx.post(
            f"{META_API}/{base}/tables/{table['id']}/fields",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=spec,
            timeout=20,
        )
        if r.status_code >= 300:
            raise RuntimeError(
                f"add field {spec['name']!r} failed: {r.status_code} {r.text[:400]}"
            )
        added += 1
        print(f"    + added field: {spec['name']}")
    return added


# --- Records -----------------------------------------------------------------

def upsert_records(api_key: str, base: str, deals: list[dict]) -> list[dict]:
    """Bulk-upsert with Deal ID as the merge key. Airtable batch limit is 10."""
    written: list[dict] = []
    BATCH = 10
    for i in range(0, len(deals), BATCH):
        batch = deals[i : i + BATCH]
        body = {
            "performUpsert": {"fieldsToMergeOn": ["Deal ID"]},
            "records": [{"fields": d} for d in batch],
            "typecast": True,
        }
        r = httpx.patch(
            f"{DATA_API}/{base}/{TABLE_NAME}",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=body,
            timeout=30,
        )
        if r.status_code >= 300:
            raise RuntimeError(
                f"upsert batch {i//BATCH} failed: {r.status_code} {r.text[:600]}"
            )
        data = r.json()
        written.extend(data.get("records", []))
    return written


# --- Main --------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="parse + validate the CSV; do not contact Airtable")
    parser.add_argument("--probe", action="store_true",
                        help="list tables in the base and exit (read-only diagnostic)")
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    api_key = os.environ.get("AIRTABLE_API_KEY")
    base = os.environ.get("AIRTABLE_BASE_ID")
    if not (api_key and base):
        print("error: AIRTABLE_API_KEY or AIRTABLE_BASE_ID missing in .env", file=sys.stderr)
        return 2

    if args.probe:
        try:
            tables = list_tables(api_key, base)
        except httpx.HTTPStatusError as e:
            print(f"error: {e.response.status_code} {e.response.text[:300]}", file=sys.stderr)
            return 1
        print(f"Base {base} has {len(tables)} table(s):")
        for t in tables:
            field_names = [f["name"] for f in t.get("fields", [])]
            print(f"  - {t['name']} (id={t['id']})")
            print(f"      fields: {field_names}")
        return 0

    if not CSV_PATH.exists():
        print(f"error: {CSV_PATH} not found", file=sys.stderr)
        return 2

    deals = parse_csv(CSV_PATH)
    print(f"Parsed {len(deals)} deals from {CSV_PATH.name}")
    if args.dry_run:
        print("First record:")
        print(json.dumps(deals[0], indent=2, ensure_ascii=False))
        return 0

    print(f"Inspecting base {base} ...")
    tables = list_tables(api_key, base)
    table = find_table(tables, TABLE_NAME)

    if table is None:
        print(f"  '{TABLE_NAME}' does not exist; creating with {len(FIELDS)} fields ...")
        try:
            table = create_table(api_key, base)
        except RuntimeError as e:
            print(f"error: {e}", file=sys.stderr)
            print("\nIf this says 403 / INVALID_PERMISSIONS_OR_MODEL_NOT_FOUND, your token is\n"
                  "missing the schema.bases:write scope. Open airtable.com/create/tokens →\n"
                  "edit token → add scope → regenerate.", file=sys.stderr)
            return 1
        print(f"  ok: created table id={table['id']}")
    else:
        print(f"  '{TABLE_NAME}' exists (id={table['id']}); checking schema ...")
        try:
            added = add_missing_fields(api_key, base, table)
        except RuntimeError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        if added == 0:
            print(f"  schema already complete ({len(FIELDS)} fields)")
        else:
            print(f"  added {added} missing field(s)")

    print(f"Upserting {len(deals)} records (matching on 'Deal ID') ...")
    try:
        records = upsert_records(api_key, base, deals)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(f"  ok: {len(records)} record(s) present in Airtable")
    for rec in records[:3]:
        f = rec.get("fields", {})
        print(f"    {rec['id']}  {f.get('Deal ID')}  {f.get('Company Name')}  ICP={f.get('ICP Score','-')}")
    if len(records) > 3:
        print(f"    ... ({len(records) - 3} more)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
