"""
Seed competitor_signals from mock_data/scenario_a_demo_signals.json.

Why this exists:
  Scenario A's signals_override demo path skips per-iteration Supabase writes
  (the Sonnet synthesiser runs straight off the in-memory demo signals array).
  As a result, /intel queries find nothing in pgvector and the slash command
  returns "No intelligence found".

  This script back-fills competitor_signals with the same 7 demo signals so the
  /intel knowledge-layer demo can run after a signals_override digest.

What it does:
  1. Loads the 7 signals from mock_data/scenario_a_demo_signals.json.
  2. Embeds (headline + evidence) per signal via HuggingFace
     sentence-transformers/all-MiniLM-L6-v2 (384-dim).
  3. Upserts into public.competitor_signals, on_conflict on (competitor_name,
     signal_type, content_summary, scraped_at) using the same Supabase REST
     pattern as seed_supabase.py.
  4. Sets human_approved = true so match_competitor_intel returns these rows.

Run:
    python scripts/seed_competitor_signals.py
    python scripts/seed_competitor_signals.py --dry-run

Env (read from .env at repo root):
    SUPABASE_URL
    SUPABASE_SERVICE_ROLE_KEY
    HUGGINGFACE_API_KEY
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
SIGNALS_PATH = REPO_ROOT / "mock_data" / "scenario_a_demo_signals.json"

EMBEDDING_MODEL = os.environ.get(
    "EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
)
HF_FEATURE_EXTRACTION_URL = (
    f"https://router.huggingface.co/hf-inference/models/{EMBEDDING_MODEL}/pipeline/feature-extraction"
)
EXPECTED_DIMS = 384

RUN_ID = "demo_seed_2026_05"


def embed(text: str, *, hf_token: str) -> list[float]:
    headers = {"Authorization": f"Bearer {hf_token}"}
    payload = {"inputs": text, "options": {"wait_for_model": True}}
    r = httpx.post(HF_FEATURE_EXTRACTION_URL, headers=headers, json=payload, timeout=60)
    r.raise_for_status()
    vec = r.json()
    if isinstance(vec, list) and vec and isinstance(vec[0], list):
        vec = vec[0]
    if not isinstance(vec, list) or len(vec) != EXPECTED_DIMS:
        raise RuntimeError(f"unexpected embedding shape len={len(vec)}")
    return vec


def insert_signal(row: dict, *, supabase_url: str, service_key: str) -> dict:
    url = f"{supabase_url.rstrip('/')}/rest/v1/competitor_signals"
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    r = httpx.post(url, headers=headers, json=row, timeout=30)
    if r.status_code >= 300:
        raise RuntimeError(f"supabase insert failed: {r.status_code} {r.text[:400]}")
    data = r.json()
    return data[0] if isinstance(data, list) else data


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    supabase_url = os.environ["SUPABASE_URL"]
    service_key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    hf_token = os.environ["HUGGINGFACE_API_KEY"]

    payload = json.loads(SIGNALS_PATH.read_text())
    signals = payload["signals_this_week"]
    print(f"Loaded {len(signals)} signals from {SIGNALS_PATH.relative_to(REPO_ROOT)}")
    print(f"Run ID: {RUN_ID}")

    inserted = 0
    for s in signals:
        embedding_input = f"{s['headline']}\n\n{s['evidence']}"
        print(f"  · {s['competitor_name']} / {s['signal_type']} / {s['priority_tier']} — {s['headline'][:60]}…")
        if args.dry_run:
            continue
        vec = embed(embedding_input, hf_token=hf_token)
        row = {
            "competitor_name": s["competitor_name"],
            "signal_type": s["signal_type"],
            "content_raw": s["evidence"],
            "content_summary": s["headline"],
            "embedding": vec,
            "priority_tier": s["priority_tier"],
            "confidence_score": s["confidence"],
            "source_url": s["source_url"],
            "scraped_at": s["scraped_at"],
            "human_approved": True,
            "run_id": RUN_ID,
        }
        insert_signal(row, supabase_url=supabase_url, service_key=service_key)
        inserted += 1
        print(f"     ✓ inserted")

    if args.dry_run:
        print("\nDry run — no rows written.")
    else:
        print(f"\nDone. Inserted {inserted} signals into competitor_signals.")
        print("Now /intel <competitor> last 30 days should return real RAG hits.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
