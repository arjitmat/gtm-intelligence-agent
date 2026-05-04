# Demo script — GTM Intelligence Agent (6 minutes)

Total budget: **6:00**. Hard cap. If anything fails live, fall back to recordings linked at the bottom.

## Pre-demo checklist (run 5 min before)

- [ ] Slack workspace open in browser, `#competitive-intel` and `#deals` both visible
- [ ] n8n editor open in second tab — both workflows imported, Config nodes filled
- [ ] Airtable base open in third tab — DEAL-EDGE-001 in `Discovery` stage (you'll move it to Qualified live)
- [ ] Langfuse dashboard open in fourth tab, filter set to "today"
- [ ] Slide 1 (the architecture diagram) ready to flip to during 4:30–5:30
- [ ] One sentence written on a sticky note: *"One knowledge layer, two interfaces."* — that's the spine.

---

## 60-second business pitch (standalone — for the cold open or recap)

> "Mid-market FP&A vendors compete in a five-vendor market: Pigment, Anaplan, Planful, Drivetrain, Vena. The GTM team has two unrelated jobs — keep on top of those competitors, and enrich every deal that lands in the CRM. Today they're done by two different people on two different cadences with two different tools.
>
> I built one system instead. Same knowledge layer powers both. Every Monday morning, a digest lands in `#competitive-intel` with priority-tiered signals. Every time an AE qualifies a deal, a card lands in `#deals` with ICP score and the relevant competitive context for *that* prospect — pulled from the same data that fed Monday's digest.
>
> Cost: under four dollars a year. Maintained by RevOps in n8n's visual editor — no engineering required."

---

## The 6-minute walkthrough

### **0:00 — 0:45  ·  The insight (slides + Slack)**

**Tab:** keep Slack open in foreground. No slide flip yet.

> "I built one system, not two. Both scenarios share a knowledge layer in Supabase. Scenario A is the proactive weekly push. Scenario B is the reactive deal pull. Same brain, two interfaces. That's the only architectural decision that matters in this submission — everything else is a consequence of it."

Hover over Slack `#competitive-intel` channel — show last week's digest already there. Don't read it.

---

### **0:45 — 2:00  ·  Live trigger Scenario A**

**Tab:** flip to **n8n editor**, Scenario A workflow open.

1. Click "Execute workflow" on the manual webhook trigger.
2. Watch nodes light up green: Schedule trigger → Config → Set run config → Split competitors → loop.
3. **Pause on Loop Over Competitors** — point: *"Five competitors, four sources each, parallel scrapes. Firecrawl returns clean markdown so the Haiku call sees 60-80% fewer tokens."*
4. Watch IF-HIGH branch fire on Pigment → Slack approval card appears in `#competitive-intel`.
5. Watch the loop close, Aggregate run, Sonnet digest synth fire.

**Tab:** flip to **Slack** at second 110.

6. Digest appears with 🔴🟡🟢 sections. Read **only** the recommended actions block — that's the AE's takeaway.

> "Three things to point out. The 🚦 approval card is human-in-the-loop — HIGH signals don't enter the knowledge base until a human OKs them. The data confidence score in the footer is real — averaged across the run. And the cost — $0.06."

---

### **2:00 — 2:30  ·  Edge case demonstration**

**Tab:** stay in n8n; Drivetrain's pricing page genuinely 404s in production.

1. Point to Drivetrain in the just-finished run — `scrape_status.pricing_ok = false`.
2. Flip to Slack digest, scroll to data-quality warnings — show ⚠️ Drivetrain.

> "Sparse-data competitor handled honestly. Confidence stays low, no fabricated signals, the digest tells the team it's a known gap. There are six other edge cases tested in `scripts/edge_cases.py` — prompt injection, malformed JSON, 429 rate limits — all handled the same way: degrade gracefully, never lie."

---

### **2:30 — 4:00  ·  Live trigger Scenario B**

**Tab:** flip to **Airtable**.

1. Point to Hexalogic People (DEAL-EDGE-001) in `Discovery` stage. Read the company name, employee count, current FP&A tool aloud — 5 seconds.
2. Drag the "Deal Stage" cell from `Discovery` → `Qualified`. The Airtable Automation fires the n8n webhook.

**Tab:** flip to **n8n editor**, Scenario B workflow.

3. Watch nodes light up: Webhook → Config → Airtable get → Validate → 3 parallel branches (Firecrawl, Hunter.io, HF embed → Supabase RAG) → Merge → ICP score (deterministic) → Sonnet enrichment → Airtable PATCH → Slack post → Langfuse.
4. Pause on **Code — ICP score + merge** — point: *"Score is deterministic, JS, the rubric from MASTER.md §5. Sonnet receives it pre-computed and is told to copy it. The model never owns the score."*

**Tab:** flip to **Slack `#deals`** at second 220.

5. Card appears: ICP 90/100 🟢 Green, Pigment competitive watch, AE next steps, Open in Airtable button.
6. Click **Open in Airtable** — show the enrichment fields populated back on the deal record.

> "Thirty-second thing to note: the competitive context here — *Pigment pitched them in Q1* — that's pulled from the SAME signals the Monday digest used. One ingestion, two consumption surfaces."

---

### **4:00 — 4:30  ·  Query the system**

**Tab:** stay in Slack, `#competitive-intel`.

1. In the message box, type: `/intel Pigment last 30 days` and send.
2. Slash command echoes; ~3-6 seconds later the bot posts a synthesised answer with source attribution and a Top sources line linking back to the actual scraped pages.

> "Same knowledge base, queried from where the GTM team already lives — no second app, no second login. The `/intel` slash command hits the same Supabase RPC the Monday digest writes to. Backed by an empty-result guard: ask about a competitor we don't track, you get *No intelligence found* — never a hallucination."

(Optional, if time allows ~10s spare: type `/intel FakeCompanyZZZ` and show the empty-result short-circuit.)

---

### **4:30 — 5:30  ·  Architecture decisions (slide flip)**

**Tab:** flip to **Slide 1 — architecture diagram**.

Five sentences, one breath each:

1. **n8n over LangChain** — RevOps modifies workflows in a visual editor, no engineering required.
2. **Haiku for classification, Sonnet for synthesis** — 80% cost reduction vs all-Sonnet, model abstraction makes both swappable.
3. **Supabase pgvector for the knowledge layer** — semantic search lives in the same Postgres as relational, one backend.
4. **Airtable as mock CRM** — what mid-market sales actually uses; webhook on stage change is realistic.
5. **Langfuse for observability** — token counts and costs only; full prompt content stays out by design.

> "Total cost: under $4 a year. That's less than one hour of SDR time."

(Quick flip to **Langfuse dashboard** for 5 seconds — show the cost/run chart.)

---

### **5:30 — 6:00  ·  What I'd improve with more time**

Three items, eight seconds each:

1. **Fine-tuned embeddings for FP&A vocabulary** — generic MiniLM gets us 90% of the way; domain-tuned would push semantic recall on terms like "rolling forecast" or "consolidation".
2. **LinkedIn Sales Navigator integration** — better decision-maker tracking than what Hunter.io gives us.
3. **Automated win/loss tagging on Airtable Closed Won / Closed Lost** — turns the system into a closed-loop learning surface, not just a delivery surface.

> "Happy to dig into any of it. The full spec is in `MASTER.md`."

---

## Backup recordings (link if live demo fails)

- Scenario A end-to-end (90 s): `https://www.loom.com/share/<id>`
- Scenario B end-to-end (90 s): `https://www.loom.com/share/<id>`
- `/intel` query (30 s): `https://www.loom.com/share/<id>`

## Anti-patterns to avoid

- **Don't read the digest aloud.** Show it, point at it, move on. Reviewers can read.
- **Don't open the JSON.** Workflow JSON is for the repo, not the demo.
- **Don't apologise for sparse data on Drivetrain.** It's the deliberate sparse-data edge case — frame it as a feature, not a bug.
- **Don't run over.** If you're at 5:55 and still on Scenario B, skip the `/intel` demo and go straight to architecture. The architecture is the differentiator.
