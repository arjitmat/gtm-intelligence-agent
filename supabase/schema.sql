-- =============================================================================
-- GTM INTELLIGENCE AGENT — SUPABASE SCHEMA
-- Knowledge layer for both Scenario A (weekly digest) and Scenario B (deal enrich).
-- Embeddings: 384 dims (sentence-transformers/all-MiniLM-L6-v2 via HF Inference API)
-- =============================================================================

-- --- Extensions --------------------------------------------------------------
create extension if not exists "uuid-ossp";
create extension if not exists vector;

-- --- 1. competitor_signals ---------------------------------------------------
-- Atomic intel units: one row per scraped artefact (pricing change, blog post,
-- G2 review block, job posting). Embedded for semantic /intel queries.

create table if not exists public.competitor_signals (
    id                uuid primary key default uuid_generate_v4(),
    competitor_name   text not null
                          check (competitor_name in
                                ('Pigment','Anaplan','Planful','Drivetrain','Vena')),
    signal_type       text not null
                          check (signal_type in
                                ('pricing','feature','g2_review','job_posting','blog','news')),
    content_raw       text not null,
    content_summary   text,
    embedding         vector(384),
    priority_tier     text check (priority_tier in ('HIGH','MEDIUM','LOW')),
    confidence_score  int  check (confidence_score between 0 and 100),
    source_url        text,
    scraped_at        timestamptz not null default now(),
    ingested_at       timestamptz not null default now(),
    human_approved    boolean not null default false,
    run_id            text not null
);

create index if not exists competitor_signals_competitor_idx
    on public.competitor_signals (competitor_name);
create index if not exists competitor_signals_run_idx
    on public.competitor_signals (run_id);
create index if not exists competitor_signals_priority_idx
    on public.competitor_signals (priority_tier);
create index if not exists competitor_signals_scraped_at_idx
    on public.competitor_signals (scraped_at desc);

-- IVFFlat for cosine similarity. Lists tuned for ~10k rows; raise if you scale.
create index if not exists competitor_signals_embedding_idx
    on public.competitor_signals
    using ivfflat (embedding vector_cosine_ops) with (lists = 100);

-- --- 2. competitor_battlecards ----------------------------------------------
-- Synced from Notion. One row per competitor. Embedded so /intel can pull
-- positioning/objections alongside fresh signals.

create table if not exists public.competitor_battlecards (
    id                  uuid primary key default uuid_generate_v4(),
    competitor_name     text not null unique
                          check (competitor_name in
                                ('Pigment','Anaplan','Planful','Drivetrain','Vena')),
    strengths           text,
    weaknesses          text,
    positioning         text,
    objection_responses text,
    win_stories         text,
    last_synced         timestamptz not null default now(),
    embedding           vector(384)
);

create index if not exists competitor_battlecards_embedding_idx
    on public.competitor_battlecards
    using ivfflat (embedding vector_cosine_ops) with (lists = 10);

-- --- 3. deal_enrichments -----------------------------------------------------
-- Output of Scenario B. One row per Airtable deal that hits "Qualified".

create table if not exists public.deal_enrichments (
    id                   uuid primary key default uuid_generate_v4(),
    airtable_deal_id     text not null,
    company_name         text not null,
    enrichment_status    text not null
                            check (enrichment_status in ('complete','partial','failed')),
    company_overview     text,
    estimated_revenue    text,
    headcount            int,
    funding_stage        text,
    funding_amount       text,
    key_decision_makers  jsonb,
    current_fpa_stack    text,
    tech_stack           text[],
    why_they_might_buy   text,
    competitive_signals  text,
    data_confidence      int check (data_confidence between 0 and 100),
    enriched_at          timestamptz not null default now(),
    enrichment_version   text not null default 'v1.0'
);

create index if not exists deal_enrichments_airtable_idx
    on public.deal_enrichments (airtable_deal_id);
create index if not exists deal_enrichments_enriched_at_idx
    on public.deal_enrichments (enriched_at desc);

-- --- Helper RPC: similarity search across signals + battlecards --------------
-- Used by the /intel Slack command and the Gradio query interface.
create or replace function public.match_competitor_intel(
    query_embedding vector(384),
    match_count     int default 8,
    competitor_filter text default null,
    days_back       int default 90
)
returns table (
    source           text,
    competitor_name  text,
    signal_type      text,
    content          text,
    source_url       text,
    scraped_at       timestamptz,
    similarity       float
)
language sql stable as $$
    -- signals
    select
        'signal'::text                           as source,
        s.competitor_name,
        s.signal_type,
        coalesce(s.content_summary, s.content_raw) as content,
        s.source_url,
        s.scraped_at,
        1 - (s.embedding <=> query_embedding)    as similarity
    from public.competitor_signals s
    where s.embedding is not null
      and (competitor_filter is null or s.competitor_name = competitor_filter)
      and s.scraped_at > now() - (days_back || ' days')::interval
      and s.human_approved = true
    union all
    -- battlecards
    select
        'battlecard'::text,
        b.competitor_name,
        'battlecard'::text                       as signal_type,
        concat_ws(E'\n\n',
            'POSITIONING: ' || b.positioning,
            'STRENGTHS: '   || b.strengths,
            'WEAKNESSES: '  || b.weaknesses,
            'OBJECTIONS: '  || b.objection_responses
        ),
        null::text,
        b.last_synced,
        1 - (b.embedding <=> query_embedding)    as similarity
    from public.competitor_battlecards b
    where b.embedding is not null
      and (competitor_filter is null or b.competitor_name = competitor_filter)
    order by similarity desc
    limit match_count;
$$;
