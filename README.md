# logistics-doc-automation

**Supervised-autonomy document processing for logistics** — invoices and customs forms flow in, structured fields flow out. High-confidence extractions are auto-approved; anything uncertain is routed to a human. Orchestrated in **n8n**, computed by a **FastAPI** engine with a **deterministic-first** extraction strategy and **GPT-4o Vision** fallback.

> The goal is not "fully automated" — it's *supervised autonomy*: humans handle exceptions, not routine data entry.

## Architecture

```
 PDF/JPG/PNG ──► n8n Webhook ──► POST /extract ──► Switch on decision
                                     │                ├─ auto_approve  → stored (status=approved) + notify
                                     │                ├─ human_review  → n8n Form (HITL) → POST /review/{id}
                                     │                └─ reject        → alert
                                     ▼
                     ┌────────────────────────────────────┐
                     │  Extraction engine (FastAPI)       │
                     │  1. Rule layer (regex + checksum)  │  ← zero tokens, zero latency
                     │  2. GPT-4o Vision (gaps only)      │  ← token-budget hard cap
                     │  3. Confidence scorer & router     │  ← thresholds are env vars
                     └────────────────────────────────────┘
                        PostgreSQL (documents + audit log) · Streamlit dashboard · /metrics (Prometheus)
```

**Separation of concerns:** n8n owns orchestration (branching, retries, HITL forms, error workflow); Python owns computation (extraction, validation, scoring) where it is unit-testable. n8n nodes stay thin.

### Why deterministic-first?

Fields with a fixed format (IBAN, VAT ID, dates, HS codes, amounts) are extracted from the PDF text layer with regexes and *validated* — an IBAN that passes its checksum is mathematically correct and gets confidence 1.0 without spending a single token. The LLM is only called for the fields the rule layer missed, and only asked for those fields. Result on the synthetic corpus: **100% of text-layer fields resolved by rules at zero cost**; the LLM budget is reserved for the hard cases (noisy scans, handwriting).

### Cost governance in code, not prompts

- `MAX_TOKENS_PER_DOC=8000` and `MAX_LLM_CALLS_PER_DOC=2` are hard caps — exceeded means abort and route to human review with `<budget_exceeded>` flagged, written to the audit log.
- Every response reports `tokens_used` and `cost_usd`; `/metrics` exposes totals in Prometheus format.

### Confidence-based routing

| Decision | Condition |
|---|---|
| `auto_approve` | every field present with confidence ≥ 0.90 |
| `human_review` | any field missing, below 0.90, or budget exceeded |
| `reject` | document type unrecognizable (confidence < 0.6) |

Thresholds are env vars because they're a **business decision**: raising `AUTO_APPROVE_THRESHOLD` trades a higher human-intervention rate for a lower rate of wrong data entering the system.

## Quickstart

```bash
cp .env.example .env          # add your OPENAI_API_KEY (or set LLM_ENABLED=0 for rule-only mode)
docker compose up --build
```

| Service | URL |
|---|---|
| API (OpenAPI docs) | http://localhost:8000/docs |
| n8n | http://localhost:5678 |
| Streamlit dashboard | http://localhost:8501 |
| Metrics | http://localhost:8000/metrics |

Generate the synthetic test corpus (50 docs: EN/DE invoices, CN22/23-style customs forms, noisy scans, missing-field variants) and try one:

```bash
python data/generate_synthetic.py --count 50
curl -F "file=@data/samples/invoice_000.pdf" http://localhost:8000/extract | jq
```

### n8n setup

1. Open n8n → *Workflows → Import from file* → `n8n/workflows/doc_processing.json` and `error_handler.json`.
2. In the main workflow's settings, set **Error Workflow** to *Error Handler*.
3. Activate, then POST a file to the webhook URL. The `human_review` branch pauses on an **n8n Form** — the reviewer corrects the flagged fields and the workflow resumes by writing back through `POST /review/{id}`.

> **n8n Cloud:** import the same JSON, then change the two HTTP nodes' base URL from `http://api:8000` to a publicly reachable URL for the API (e.g. a `cloudflared`/`ngrok` tunnel to `localhost:8000`).

## Evaluation

`python eval/evaluate.py --api http://localhost:8000` replays the corpus against ground truth.

Deterministic-only baseline (`LLM_ENABLED=0`, 50 docs — 40 clean text-layer PDFs + 10 noisy scans):

| Metric | Result |
|---|---|
| Auto-approve precision | **100%** (34/34 — zero wrong documents entered the system) |
| Field-level accuracy | 80.2% overall (**~99% on text-layer docs**; scans need the LLM) |
| Human intervention rate | 32% (all scans + missing-field docs correctly routed to review) |
| Rule-layer coverage | **100% of extracted fields at zero token cost** |
| Latency p50 / p95 | 30 ms / 162 ms |
| Cost per document | $0.00 |

With `LLM_ENABLED=1` the 10 scanned documents route through GPT-4o Vision instead of being rejected — re-run the eval with your API key to fill in the hybrid numbers.

## Idempotency & audit

- Re-sending the same file (SHA-256 match) returns the stored result — no duplicate rows, no duplicate spend.
- Every state change is in `audit_log`: extraction, decision, reviewer corrections (with field-level diffs). Human corrections are stored with `method=human` — a ready-made dataset for future few-shot examples or fine-tuning.

## Tests

65 pytest tests, no API key needed (LLM mocked / disabled):

```bash
pip install -r api/requirements.txt -r requirements-dev.txt
pytest api/tests
```

## Repo layout

```
api/            FastAPI app: routers/, engine/ (rules, llm_extractor, confidence, budget), models/, tests/
n8n/workflows/  doc_processing.json (main pipeline), error_handler.json
data/           generate_synthetic.py, samples/, ground_truth.json
eval/           evaluate.py — field accuracy, precision, intervention rate, cost, latency
dashboard/      Streamlit ops view (volume, decisions, cost, latency)
```
