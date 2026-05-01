"""
GTM Intelligence Agent — Gradio query interface.

Two-tab UI for reviewers:
  TAB 1  Ask Competitive Intel — semantic Q&A over the Supabase knowledge base
                                  (HF embed -> match_competitor_intel RPC -> Sonnet answer).
  TAB 2  System Status         — last-run dates per competitor, total signal count,
                                  per-competitor confidence, manual digest trigger.

Run locally:
    pip install -r gradio_app/requirements.txt
    python gradio_app/app.py

Deploy to HuggingFace Spaces:
    1) Push this folder as a Space (Gradio template).
    2) In the Space settings -> Variables and secrets, add (as Secrets):
         ANTHROPIC_API_KEY
         HUGGINGFACE_API_KEY
         SUPABASE_URL
         SUPABASE_SERVICE_ROLE_KEY
         N8N_WEBHOOK_BASE_URL          # for the manual-digest button
    3) Optional (sane defaults applied if unset):
         EMBEDDING_MODEL    default sentence-transformers/all-MiniLM-L6-v2
         LLM_MODEL_SYNTHESIZE   default claude-sonnet-4-6
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

import gradio as gr
import httpx


# --- Config from env (with defaults) ----------------------------------------

ANTHROPIC_API_KEY        = os.environ.get("ANTHROPIC_API_KEY", "")
HUGGINGFACE_API_KEY      = os.environ.get("HUGGINGFACE_API_KEY", "")
SUPABASE_URL             = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
N8N_WEBHOOK_BASE_URL     = os.environ.get("N8N_WEBHOOK_BASE_URL", "").rstrip("/")

EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
LLM_MODEL       = os.environ.get("LLM_MODEL_SYNTHESIZE", "claude-sonnet-4-6")

COMPETITORS = ["Pigment", "Anaplan", "Planful", "Drivetrain", "Vena"]


# --- Low-level helpers ------------------------------------------------------

def _missing_env() -> Optional[str]:
    needed = {
        "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
        "HUGGINGFACE_API_KEY": HUGGINGFACE_API_KEY,
        "SUPABASE_URL": SUPABASE_URL,
        "SUPABASE_SERVICE_ROLE_KEY": SUPABASE_SERVICE_ROLE_KEY,
    }
    missing = [k for k, v in needed.items() if not v]
    if missing:
        return ("⚠️ Missing environment variables: " + ", ".join(missing)
                + ". Set them as Space Secrets or in your local .env.")
    return None


def embed_query(text: str) -> list[float]:
    """Embed a single string via the HuggingFace Inference API."""
    url = f"https://api-inference.huggingface.co/pipeline/feature-extraction/{EMBEDDING_MODEL}"
    headers = {"Authorization": f"Bearer {HUGGINGFACE_API_KEY}"}
    payload = {"inputs": text, "options": {"wait_for_model": True}}
    r = httpx.post(url, headers=headers, json=payload, timeout=30)
    r.raise_for_status()
    vec = r.json()
    if isinstance(vec, list) and vec and isinstance(vec[0], list):
        vec = vec[0]
    if not isinstance(vec, list) or len(vec) != 384:
        raise RuntimeError(f"unexpected embedding shape (len={len(vec) if hasattr(vec,'__len__') else 'n/a'})")
    return vec


def supabase_rpc(name: str, body: dict) -> dict | list:
    url = f"{SUPABASE_URL}/rest/v1/rpc/{name}"
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }
    r = httpx.post(url, headers=headers, json=body, timeout=15)
    r.raise_for_status()
    return r.json()


def supabase_select(table: str, params: dict) -> list[dict]:
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
    }
    r = httpx.get(url, headers=headers, params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def claude_answer(query: str, hits: list[dict]) -> str:
    """Call Claude Sonnet with retrieved snippets. Grounded, no hallucination."""
    snippets = [
        {
            "competitor": h.get("competitor_name"),
            "type": h.get("signal_type"),
            "content": (h.get("content") or "")[:600],
            "scraped_at": h.get("scraped_at"),
            "source_url": h.get("source_url"),
        }
        for h in hits
    ]
    body = {
        "model": LLM_MODEL,
        "max_tokens": 400,
        "temperature": 0.2,
        "system": (
            "You are a competitive intelligence analyst. Use ONLY the snippets "
            "in <retrieved_intel>. Never invent facts. If snippets don't directly "
            "answer the question, say so explicitly. British English. 3-6 short "
            "sentences. End with one '_Source(s):_' line listing competitor + scrape "
            "date for each snippet you used."
        ),
        "messages": [
            {
                "role": "user",
                "content": (
                    f"<query>\n{query}\n</query>\n\n"
                    f"<retrieved_intel>\n{snippets}\n</retrieved_intel>\n\n"
                    "Answer the query. Be specific about which competitor each fact belongs to."
                ),
            }
        ],
    }
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    r = httpx.post("https://api.anthropic.com/v1/messages", headers=headers, json=body, timeout=45)
    r.raise_for_status()
    data = r.json()
    return (data.get("content", [{}])[0] or {}).get("text", "(no answer)")


# --- Tab 1: Ask Competitive Intel -------------------------------------------

def answer_query(query: str) -> str:
    if not query or not query.strip():
        return "Type a question above. Try one of the examples below."
    err = _missing_env()
    if err:
        return err
    try:
        emb = embed_query(query.strip())
        hits = supabase_rpc("match_competitor_intel", {
            "query_embedding": emb,
            "match_count": 6,
            "competitor_filter": None,
            "days_back": 90,
        })
    except httpx.HTTPStatusError as e:
        return f"⚠️ Backend error: {e.response.status_code} {e.response.text[:200]}"
    except Exception as e:
        return f"⚠️ Backend error: {e}"

    hits = hits if isinstance(hits, list) else (hits.get("results") or [])
    hits = [h for h in hits if (h.get("content") or "").strip()]

    if not hits:
        return "**No intelligence found for this query.**\n\nTry a wider window or a different competitor name."

    try:
        answer = claude_answer(query.strip(), hits)
    except Exception as e:
        # Still useful — show raw matches so the user gets value even if the LLM call fails.
        bullets = "\n".join(
            f"- *{h['competitor_name']}* ({h['signal_type']}, {(h.get('scraped_at') or '')[:10]}): "
            f"{(h.get('content') or '')[:200]}"
            for h in hits[:5]
        )
        return f"⚠️ LLM error ({e}); showing raw matches instead:\n\n{bullets}"

    competitors = sorted({h["competitor_name"] for h in hits if h.get("competitor_name")})
    last_dates = sorted([h.get("scraped_at") for h in hits if h.get("scraped_at")], reverse=True)
    last_updated = (last_dates[0] or "")[:10] if last_dates else "—"
    return (
        f"{answer}\n\n"
        f"---\n"
        f"**Sources:** {', '.join(competitors) or '—'}  ·  "
        f"**Last updated:** {last_updated}  ·  "
        f"**Hits:** {len(hits)}"
    )


# --- Tab 2: System Status ---------------------------------------------------

def system_status() -> str:
    err = _missing_env()
    if err:
        return err
    try:
        # Last scrape per competitor
        rows = supabase_select("competitor_signals", {
            "select": "competitor_name,scraped_at,confidence_score",
            "order": "scraped_at.desc",
            "limit": "1000",
        })
    except Exception as e:
        return f"⚠️ Could not query Supabase: {e}"

    by_competitor: dict[str, dict] = {}
    for r in rows:
        c = r.get("competitor_name")
        if not c or c not in COMPETITORS:
            continue
        agg = by_competitor.setdefault(c, {"last": None, "confs": [], "count": 0})
        agg["count"] += 1
        agg["confs"].append(r.get("confidence_score") or 0)
        if not agg["last"] or (r.get("scraped_at") or "") > agg["last"]:
            agg["last"] = r.get("scraped_at")

    lines = ["## System status\n", f"**Total signals in knowledge base:** {len(rows)}\n"]
    lines.append("| Competitor | Last run | Signals | Avg confidence |")
    lines.append("|---|---|---|---|")
    for c in COMPETITORS:
        a = by_competitor.get(c)
        if not a:
            lines.append(f"| {c} | _no data_ | 0 | — |")
            continue
        last = (a["last"] or "")[:10] or "—"
        avg = round(sum(a["confs"]) / len(a["confs"])) if a["confs"] else 0
        lines.append(f"| {c} | {last} | {a['count']} | {avg}% |")

    lines.append(f"\n_As of {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_")
    return "\n".join(lines)


def trigger_digest() -> str:
    if not N8N_WEBHOOK_BASE_URL:
        return "⚠️ Set `N8N_WEBHOOK_BASE_URL` in Space secrets to enable manual digest runs."
    url = f"{N8N_WEBHOOK_BASE_URL}/scenario-a-trigger"
    try:
        r = httpx.post(url, json={"_manual_trigger_from": "gradio"}, timeout=10)
        if 200 <= r.status_code < 300:
            return f"✅ Digest run triggered ({r.status_code}). Watch #competitive-intel in Slack — usually arrives in ~60-90s."
        return f"⚠️ Webhook returned {r.status_code}: {r.text[:200]}"
    except Exception as e:
        return f"⚠️ Could not reach n8n webhook: {e}"


# --- Gradio UI --------------------------------------------------------------

EXAMPLES = [
    "What has Pigment shipped in last 30 days?",
    "What are Anaplan's weaknesses?",
    "Any pricing changes this week?",
    "How does Drivetrain position against us?",
    "Where does Vena win deals?",
]

with gr.Blocks(title="GTM Intelligence Agent", theme=gr.themes.Soft()) as app:
    gr.Markdown(
        "# GTM Intelligence Agent\n"
        "Unified competitive intelligence over Pigment · Anaplan · Planful · Drivetrain · Vena.  \n"
        "_Backed by Supabase pgvector + Claude Sonnet, with Slack and Airtable integrations._"
    )

    with gr.Tabs():
        with gr.TabItem("Ask Competitive Intel"):
            gr.Markdown("Ask a question. The system embeds it, retrieves the top matches from the knowledge base, and synthesises an answer **only from retrieved snippets**.")
            q_in = gr.Textbox(label="Ask about a competitor", placeholder="e.g. What has Pigment shipped in last 30 days?", lines=2)
            q_btn = gr.Button("Ask", variant="primary")
            q_out = gr.Markdown()
            gr.Examples(examples=EXAMPLES, inputs=q_in, label="Examples")
            q_btn.click(answer_query, inputs=q_in, outputs=q_out)
            q_in.submit(answer_query, inputs=q_in, outputs=q_out)

        with gr.TabItem("System Status"):
            gr.Markdown("Knowledge-base health — last scrape per competitor, signal counts, average confidence.")
            with gr.Row():
                refresh = gr.Button("Refresh", variant="secondary")
                trigger = gr.Button("Trigger manual digest run", variant="primary")
            status_out = gr.Markdown(value=system_status())
            trigger_out = gr.Markdown()
            refresh.click(system_status, outputs=status_out)
            trigger.click(trigger_digest, outputs=trigger_out)

    gr.Markdown(
        "---\n"
        "Source: [github.com/arjitmat/gtm-intelligence-agent](https://github.com/arjitmat/gtm-intelligence-agent)  ·  "
        "See [`MASTER.md`](https://github.com/arjitmat/gtm-intelligence-agent/blob/main/MASTER.md) for architecture."
    )


if __name__ == "__main__":
    app.launch(server_name="0.0.0.0", server_port=int(os.environ.get("PORT", 7860)))
