"""
Seed the Supabase knowledge layer with the 5 competitor battlecards.

What this does:
  1. Reads mock_data/competitors.json for competitor metadata.
  2. Reads mock_data/battlecards/*.md as the source of truth for each battlecard.
  3. Splits each battlecard into the 5 fields we store
     (positioning, strengths, weaknesses, objection_responses, win_stories).
  4. Embeds the concatenated battlecard text via the HuggingFace Inference API
     using sentence-transformers/all-MiniLM-L6-v2 (384 dims).
  5. Upserts one row per competitor into competitor_battlecards.

Run:
    python scripts/seed_supabase.py
    python scripts/seed_supabase.py --dry-run      # print what would be written
    python scripts/seed_supabase.py --competitor Pigment   # seed one only

Env vars required (see .env.example):
    SUPABASE_URL
    SUPABASE_SERVICE_ROLE_KEY
    HUGGINGFACE_API_KEY
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv


# --- Constants ---------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPETITORS_JSON = REPO_ROOT / "mock_data" / "competitors.json"
BATTLECARDS_DIR = REPO_ROOT / "mock_data" / "battlecards"

EMBEDDING_MODEL = os.environ.get(
    "EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
)
HF_FEATURE_EXTRACTION_URL = (
    f"https://api-inference.huggingface.co/pipeline/feature-extraction/{EMBEDDING_MODEL}"
)

EXPECTED_DIMS = 384

# Battlecard markdown filename per competitor.
BATTLECARD_FILES = {
    "Pigment": "pigment.md",
    "Anaplan": "anaplan.md",
    "Planful": "planful.md",
    "Drivetrain": "drivetrain.md",
    "Vena": "vena.md",
}


@dataclass
class Battlecard:
    competitor_name: str
    positioning: str
    strengths: str
    weaknesses: str
    objection_responses: str
    win_stories: str
    embedding_input: str   # concatenated text we actually embed

    def to_row(self, embedding: list[float]) -> dict:
        return {
            "competitor_name": self.competitor_name,
            "positioning": self.positioning,
            "strengths": self.strengths,
            "weaknesses": self.weaknesses,
            "objection_responses": self.objection_responses,
            "win_stories": self.win_stories,
            "embedding": embedding,
        }


# --- Markdown parsing --------------------------------------------------------

# Maps the section headings in our battlecard template to the DB column.
# Order matters only for diagnostics.
SECTION_MAP = {
    "positioning": ["## Positioning (How They Pitch)", "## Positioning"],
    "strengths": ["## Strengths (Be Honest — Don't Underestimate)", "## Strengths"],
    "weaknesses": [
        "## Weaknesses (Where We Win)",
        "## Weaknesses (Where We Win — Especially in Displacement)",
        "## Weaknesses",
    ],
    "objection_responses": [
        "## Typical Objections & Responses",
        "## Objections & Responses",
    ],
    "win_stories": ["## Win Stories (Anonymised)", "## Win Stories"],
}


def _extract_section(md: str, heading_variants: list[str]) -> Optional[str]:
    """Return the body of the first matching heading, until the next ## or ---."""
    for heading in heading_variants:
        # Build a regex matching the heading on its own line.
        pattern = re.compile(
            rf"^{re.escape(heading)}\s*$(?P<body>.*?)(?=^##\s|\n---\s*$|\Z)",
            re.MULTILINE | re.DOTALL,
        )
        m = pattern.search(md)
        if m:
            return m.group("body").strip()
    return None


def parse_battlecard(competitor_name: str, md_path: Path) -> Battlecard:
    md = md_path.read_text(encoding="utf-8")

    sections: dict[str, Optional[str]] = {
        key: _extract_section(md, variants) for key, variants in SECTION_MAP.items()
    }

    missing = [k for k, v in sections.items() if not v]
    if missing:
        # Don't hard-fail — battlecards may evolve. Warn and continue with empty strings.
        print(
            f"  warn: {competitor_name} battlecard missing sections: {missing}",
            file=sys.stderr,
        )

    embedding_input = "\n\n".join(
        f"{key.upper()}:\n{sections[key]}" for key in SECTION_MAP if sections[key]
    )

    return Battlecard(
        competitor_name=competitor_name,
        positioning=sections["positioning"] or "",
        strengths=sections["strengths"] or "",
        weaknesses=sections["weaknesses"] or "",
        objection_responses=sections["objection_responses"] or "",
        win_stories=sections["win_stories"] or "",
        embedding_input=embedding_input,
    )


# --- Embeddings (HuggingFace Inference API) ----------------------------------

def embed(text: str, *, hf_token: str, max_retries: int = 4) -> list[float]:
    """Return a 384-dim embedding via the HF Inference API. Cold-start retries included."""
    headers = {"Authorization": f"Bearer {hf_token}"}
    payload = {"inputs": text, "options": {"wait_for_model": True}}

    for attempt in range(max_retries):
        try:
            r = httpx.post(
                HF_FEATURE_EXTRACTION_URL, headers=headers, json=payload, timeout=60
            )
        except httpx.RequestError as e:
            if attempt == max_retries - 1:
                raise
            backoff = 2 ** attempt
            print(f"  network error ({e}); retrying in {backoff}s", file=sys.stderr)
            time.sleep(backoff)
            continue

        if r.status_code == 503:
            # Model loading. Back off and retry.
            backoff = 2 ** attempt * 2
            print(f"  HF model warming up; retrying in {backoff}s", file=sys.stderr)
            time.sleep(backoff)
            continue

        r.raise_for_status()
        vec = r.json()

        # The pipeline returns either a flat list of floats (single input)
        # or a nested list. Normalise.
        if isinstance(vec, list) and vec and isinstance(vec[0], list):
            vec = vec[0]

        if not isinstance(vec, list) or len(vec) != EXPECTED_DIMS:
            raise RuntimeError(
                f"unexpected embedding shape: type={type(vec).__name__}, "
                f"len={len(vec) if hasattr(vec, '__len__') else 'n/a'}"
            )
        return vec

    raise RuntimeError("embedding failed after retries")


# --- Supabase upsert ---------------------------------------------------------

def upsert_battlecard(row: dict, *, supabase_url: str, service_key: str) -> dict:
    """Upsert one row into competitor_battlecards on competitor_name conflict."""
    url = f"{supabase_url.rstrip('/')}/rest/v1/competitor_battlecards"
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
        # on_conflict + Prefer=resolution=merge-duplicates → upsert by unique key
        "Prefer": "resolution=merge-duplicates,return=representation",
    }
    params = {"on_conflict": "competitor_name"}
    r = httpx.post(url, headers=headers, params=params, json=row, timeout=30)
    if r.status_code >= 300:
        raise RuntimeError(
            f"supabase upsert failed: {r.status_code} {r.text[:400]}"
        )
    data = r.json()
    return data[0] if isinstance(data, list) else data


# --- Main --------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="parse + embed but do not write to Supabase")
    parser.add_argument("--competitor", type=str, default=None,
                        help="seed only the named competitor (e.g. 'Pigment')")
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")

    hf_token = os.environ.get("HUGGINGFACE_API_KEY")
    supabase_url = os.environ.get("SUPABASE_URL")
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

    if not hf_token:
        print("error: HUGGINGFACE_API_KEY missing in .env", file=sys.stderr)
        return 2
    if not args.dry_run and not (supabase_url and service_key):
        print("error: SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY missing in .env",
              file=sys.stderr)
        return 2

    if not COMPETITORS_JSON.exists():
        print(f"error: {COMPETITORS_JSON} not found", file=sys.stderr)
        return 2
    competitors = json.loads(COMPETITORS_JSON.read_text(encoding="utf-8"))["competitors"]
    competitor_names = [c["name"] for c in competitors]

    targets = [args.competitor] if args.competitor else competitor_names
    for name in targets:
        if name not in BATTLECARD_FILES:
            print(f"error: no battlecard file mapped for '{name}'", file=sys.stderr)
            return 2

    print(f"seeding {len(targets)} battlecard(s): {targets}")
    print(f"  embedding model: {EMBEDDING_MODEL} ({EXPECTED_DIMS} dims)")
    print(f"  dry run: {args.dry_run}")

    failures: list[str] = []
    for name in targets:
        md_path = BATTLECARDS_DIR / BATTLECARD_FILES[name]
        if not md_path.exists():
            print(f"  skip {name}: file not found at {md_path}")
            failures.append(name)
            continue

        print(f"  → {name}: parsing {md_path.name}")
        card = parse_battlecard(name, md_path)

        print(f"    embedding {len(card.embedding_input)} chars …")
        try:
            vec = embed(card.embedding_input, hf_token=hf_token)
        except Exception as e:
            print(f"    embed failed: {e}", file=sys.stderr)
            failures.append(name)
            continue
        print(f"    embedding ok (dim {len(vec)}, head {vec[:4]!r})")

        if args.dry_run:
            print(f"    [dry-run] would upsert row for {name}")
            continue

        try:
            written = upsert_battlecard(
                card.to_row(vec),
                supabase_url=supabase_url,
                service_key=service_key,
            )
            print(f"    upsert ok: id={written.get('id')}")
        except Exception as e:
            print(f"    upsert failed: {e}", file=sys.stderr)
            failures.append(name)
            continue

    if failures:
        print(f"\nDone with errors. Failed: {failures}", file=sys.stderr)
        return 1
    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
