# GTM Intelligence Agent

A unified competitive intelligence system for a B2B FP&A SaaS company.
**One knowledge layer, two consumption interfaces:**

- **Scenario A — Proactive:** weekly Slack digest monitoring 5 FP&A competitors (Pigment, Anaplan, Planful, Drivetrain, Vena).
- **Scenario B — Reactive:** deal enrichment triggered when an AE moves an Airtable opportunity to *Qualified*.

Both share the same Supabase pgvector knowledge base. Scrape once, serve everywhere.

> See [`MASTER.md`](./MASTER.md) for the full architectural spec — the single source of truth for every Claude Code session on this repo.

---

## Repo layout

```
.
├── MASTER.md                  # full spec — read first
├── README.md                  # this file
├── .env.example               # 14 required env vars + optional toggles
├── .gitignore
├── supabase/
│   └── schema.sql             # pgvector extension + 3 tables + match_competitor_intel RPC
├── prompts/
│   ├── signal_classification.md   # Scenario A — per-source Haiku prompt
│   ├── digest_synthesis.md        # Scenario A — weekly Sonnet aggregator
│   └── deal_enrichment.md         # Scenario B — Sonnet deal-intel synthesiser
├── mock_data/
│   ├── airtable_deals.csv         # 12 mock CRM deals across 6 ICP buckets
│   ├── competitors.json           # 5 competitor profiles + scrape targets
│   └── battlecards/
│       ├── pigment.md
│       ├── anaplan.md
│       ├── planful.md
│       ├── drivetrain.md
│       └── vena.md
├── scripts/
│   ├── seed_supabase.py            # embeds battlecards via HF + upserts to Supabase
│   ├── seed_competitor_signals.py  # back-fills competitor_signals from demo signals
│   ├── seed_airtable.py            # creates Deals table + bulk-upserts 12 mock records
│   ├── test_scenario_b.py          # posts 3 deal payloads (Green/Amber/Red) to Scenario B
│   ├── edge_cases.py               # graceful-degradation test harness
│   ├── audit_data_quality.py       # read-only pre-demo verification (Airtable + Supabase + mock_data)
│   └── requirements.txt
└── n8n/
    ├── scenario_a_workflow.json    # weekly digest (Cron + manual demo trigger)
    ├── scenario_b_workflow.json    # deal enrichment (webhook from Airtable)
    └── intel_query_workflow.json   # /intel Slack slash-command RAG handler
```

---

## Quick start

### 1. Clone & install Python deps

```bash
git clone https://github.com/arjitmat/gtm-intelligence-agent.git
cd gtm-intelligence-agent
python -m venv .venv && source .venv/bin/activate
pip install -r scripts/requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# edit .env and fill in real keys (14 of them — see comments in .env.example)
```

### 3. Provision Supabase

In your Supabase project's SQL editor, run `supabase/schema.sql`. This:

- enables `pgvector` and `uuid-ossp`
- creates `competitor_signals`, `competitor_battlecards`, `deal_enrichments`
- creates the `match_competitor_intel(...)` RPC used by `/intel`

### 4. Seed battlecards

```bash
python scripts/seed_supabase.py --dry-run     # sanity check
python scripts/seed_supabase.py               # writes to Supabase
```

### 5. Build n8n workflows

Workflows are built in n8n Cloud. See MASTER.md §4 (Scenario A) and §5 (Scenario B) for node-by-node specs. Exported `.json` files will live in `n8n/`.

---

## Sharing API keys safely

**The repo never contains real credentials.** Workflow:

1. **`.env.example`** is committed — it lists all 14 required keys with comments and signup links, but only placeholder values.
2. **`.env`** is gitignored — every collaborator (or the reviewer) keeps their own.
3. To hand keys to a reviewer / teammate, use **one** of:

   | Method | When to use |
   |---|---|
   | **1Password / Bitwarden shared vault** | Best for ongoing collaboration; revocable, auditable. |
   | **GitHub Actions / Codespaces secrets** | If running in CI or a remote dev container. |
   | **Vercel / n8n Cloud env-var UI** | For deployed workflows — paste keys directly into the platform's env settings, never into the repo. |
   | **One-time encrypted message** (e.g., onetimesecret.com, 1Password "send") | One-shot reviewer access; link self-destructs. |

4. **Never** paste keys into Slack DMs, email, or PR descriptions. **Never** commit `local.env`, `.env`, `.env.local`, or files matching `*.key` / `*.pem` (already in `.gitignore`).

5. If a key is ever exposed in a commit:
   - **Rotate it immediately** at the provider's dashboard.
   - Force-purge from history (`git filter-repo` or BFG); a `git rm` alone does not erase it.
   - GitHub's secret scanning will sometimes auto-revoke (Anthropic, Stripe, AWS) — don't rely on it.

---

## Mock data design (Section 6 of MASTER.md)

`mock_data/airtable_deals.csv` — 12 deals, intentionally diverse to exercise the system:

| Bucket | Count | Purpose |
|---|---|---|
| Green ICP (Barcelona SaaS, Series B/C, 50–500 emp) | 3 | Hexalogic People, Quizora Forms, Vertiluz Energy |
| Amber ICP (partial data, larger or earlier-stage) | 3 | Norvik Logistics Cloud, Cobblemark, Praxiom Cyber |
| Red ICP (wrong size or industry) | 2 | Brightspark Toys (B2C, 35 emp), Polaris Health Foundation (NGO, 1500 emp) |
| Missing website (graceful degradation test) | 2 | Stratacore Capital, Lumenflow Robotics |
| Already on competitor FP&A | 1 | Mertonbridge Logistics (Anaplan) |
| Non-European | 1 | Cascadia Foods (Portland, OR) |

Names are inventions — patterns are inspired by real Barcelona / European / North American mid-market profiles.

---

## Cost expectation

Per the MASTER.md §10 budget model:

- Weekly Scenario A run: ~$0.06 (5 competitors × 4 sources Haiku + 1 Sonnet digest)
- Per Scenario B enrichment: ~$0.003 (1 Sonnet call)
- Annual run-rate (1 weekly digest + 5 deal enrichments/week): **under $4/year**

All LLM calls instrumented in Langfuse for live cost tracking.

---

## Status

| Session | Scope | Status |
|---|---|---|
| 1 | Folder structure, `.env.example`, Supabase schema, mock CRM, competitor profiles | ✅ |
| 2 | Battlecards × 5, prompt files × 3, `seed_supabase.py` | ✅ |
| 3 | Scenario A weekly digest workflow (n8n + Firecrawl + Haiku classify + Sonnet synth + Slack Block Kit) | ✅ |
| 4 | Scenario B deal enrichment workflow (Airtable webhook + Hunter.io + Sonnet enrich + Airtable PATCH + Slack) | ✅ |
| 5 | `/intel` Slack slash-command RAG (HF embed → pgvector RPC → Sonnet grounded answer) | ✅ |
| 6 | Langfuse traces on every LLM call · Audit + edge-case test harnesses | ✅ |

### Known limitations

- **Scenario A live multi-competitor scrape is constrained on n8n Cloud free tier.** The `SplitInBatches v3` node's lane 0 doesn't reliably fire downstream on the free plan, so the per-competitor live-scrape loop only completes for a single competitor at a time. **Workaround for the demo:** trigger the manual webhook with `{"signals_override": true}` to use `mock_data/scenario_a_demo_signals.json` as the synthesis input — produces a representative weekly-format digest deterministically. Production deployment on the paid tier removes this constraint.
- **`/intel` slash command requires the Slack app to register `/intel` pointing at `{n8n_base}/webhook/intel`** (Slash Commands → Create New Command in your Slack app config). Bot needs `commands` scope and the channel needs `/invite @GTM Intel Bot`.
- **`deal_enrichments` and `digest_runs` Supabase tables are schema-only** — Scenario B currently writes enrichment back to Airtable (the source-of-truth CRM); the Supabase tables are forward-compatible for snapshot history but not yet wired up.

---

## License

MIT — case study artefact for evaluation.
