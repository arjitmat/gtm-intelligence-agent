# Gradio query interface — GTM Intelligence Agent

A two-tab UI wrapping the Supabase RAG endpoint and the Scenario A trigger.

## Run locally

```bash
pip install -r gradio_app/requirements.txt
python gradio_app/app.py
# -> open http://localhost:7860
```

The app reads its config from environment variables. For local runs, export them
in your shell or copy `.env.example` to `.env` and use `python-dotenv` (the app
itself only reads `os.environ` directly — keep it simple).

## Deploy to HuggingFace Spaces

1. Create a new Space, SDK = **Gradio**.
2. Push this folder (or the whole repo with `app.py` discovered at `gradio_app/app.py` — the simplest is to copy `gradio_app/app.py` and `gradio_app/requirements.txt` to the Space root).
3. In **Space settings → Variables and secrets**, add the following as **Secrets** (not variables — these are sensitive):

   | Secret | Required | Notes |
   |---|---|---|
   | `ANTHROPIC_API_KEY` | yes | Claude Sonnet for synthesis |
   | `HUGGINGFACE_API_KEY` | yes | sentence-transformers/all-MiniLM-L6-v2 embeddings |
   | `SUPABASE_URL` | yes | full URL incl. https:// |
   | `SUPABASE_SERVICE_ROLE_KEY` | yes | server-side; never expose to a public Space without RLS in place |
   | `N8N_WEBHOOK_BASE_URL` | optional | enables the *Trigger manual digest run* button |
   | `EMBEDDING_MODEL` | optional | default `sentence-transformers/all-MiniLM-L6-v2` |
   | `LLM_MODEL_SYNTHESIZE` | optional | default `claude-sonnet-4-6` |

4. Restart the Space. The app launches on port 7860 by default.

## Security note

If you publish this Space, set `SUPABASE_URL` to a project that has
**row-level security** enabled on `competitor_signals` and use an `anon` key
limited to read-only RPC calls — **never** ship the service-role key in a
public Space. For a private Space restricted to reviewers, the service-role
key is acceptable.
