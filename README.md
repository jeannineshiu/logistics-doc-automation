# logistics-doc-automation

**Supervised-autonomy document processing for logistics** — invoices and customs forms flow in, structured fields flow out. High-confidence extractions are auto-approved; anything uncertain is routed to a human. Orchestrated in **n8n**, computed by a **FastAPI** engine with a **deterministic-first** extraction strategy and **GPT-4o Vision** fallback.

> The goal is not "fully automated" — it's *supervised autonomy*: humans handle exceptions, not routine data entry.

## Architecture

![Architecture: documents enter through an n8n webhook, are extracted by a three-layer FastAPI engine, and are routed to auto-approval, human review, or rejection](docs/architecture.svg)

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

Open http://localhost:5678 once to create the local owner account (stored in the `n8n_data` volume — this is the self-hosted community edition, no n8n.io account or licence needed). Then import both workflows with one command:

```bash
docker compose cp n8n/workflows/. n8n:/tmp/workflows/
docker compose exec n8n n8n import:workflow --separate --input=/tmp/workflows
docker compose exec n8n n8n publish:workflow --id=LogisticsDocProc
docker compose exec n8n n8n publish:workflow --id=LogisticsErrorWf
docker compose restart n8n            # publishing via CLI needs a restart to take effect
```

Both workflows carry fixed IDs, so the import is idempotent (re-running updates in place) and `doc_processing` already points its **Error Workflow** at `Error Handler` — no manual wiring. The error handler has to be published too: n8n skips an unpublished error workflow and only logs *"is not active and cannot be executed"*.

> **n8n 2.x UI notes.** Activation was renamed — the top-right toggle is **Publish**, not *Active*. The GUI importer moved inside the editor: open a workflow, then **⋯ (top right) → Import from File**; pasting the JSON onto the canvas with `Cmd+V` also works.

Send a document to the webhook:

```bash
# text-layer invoice → rule layer only → auto_approve, zero tokens
curl -F "file=@data/samples/invoice_000.pdf" http://localhost:5678/webhook/doc-upload

# noisy scan → no text layer → GPT-4o Vision fallback → human_review branch
curl -F "file=@data/samples/invoice_004_scan.pdf" http://localhost:5678/webhook/doc-upload
```

The `human_review` branch pauses on an **n8n Form** — the reviewer corrects the flagged fields and the workflow resumes by writing back through `POST /review/{id}`.

> **n8n Cloud:** import the same JSON, then change the two HTTP nodes' base URL from `http://api:8000` to a publicly reachable URL for the API (e.g. a `cloudflared`/`ngrok` tunnel to `localhost:8000`).

## Evaluation

`python eval/evaluate.py --api http://localhost:8000` replays the corpus against ground truth (50 documents — 40 text-layer PDFs in English and German, 10 JPEG-compressed noisy scans with no text layer, 7 with a field genuinely absent).

### Deterministic-first vs. a pure-LLM baseline

The control group runs the identical pipeline with the rule layer switched off (`RULE_LAYER_ENABLED=0`), so every field goes to GPT-4o Vision. Same corpus, same thresholds, same model:

| Metric | **Deterministic-first** | Pure-LLM baseline |
|---|---|---|
| Field-level accuracy | 99.7% (333/334) | 99.7% (333/334) |
| Auto-approve precision | **100%** (43/43) | 100% (43/43) |
| Human intervention rate | 14% | 14% |
| Rule-layer coverage | **79.5%** of fields at zero token cost | 0% |
| Cost per document | **$0.00195** | $0.00589 |
| Latency p50 | **65 ms** | 3 768 ms |
| Latency p95 | 5 440 ms | 5 526 ms |

**Resolving 79.5% of fields deterministically cuts cost per document by 67% and median latency by 58×, at identical accuracy.** Nothing is traded away: both runs miss the same single field, and both auto-approve the same 43 documents. p95 is a wash because it is set by the scanned documents, which need the model either way — the rule layer removes the model from the *common* path, not the hard one.

The number that matters most is **auto-approve precision: 100%**. Nothing incorrect was written to the database unattended.

### The one field both runs got wrong — and why it never reached the database

`invoice_048.pdf` is a hard sample whose total-amount line was deliberately removed. GPT-4o Vision reported `4720.55` anyway: a hallucinated total, invented for a field that does not exist in the document. Both runs made this error, because the rule layer also finds nothing to extract there and defers to the model.

The pipeline still did its job. The same deletion removed the currency, the missing field dropped the document below the auto-approve floor, and it was routed to **human review** rather than written to the database. This is the case the architecture exists for: the defence against a confident wrong answer is not a better prompt, it is refusing to auto-approve anything with a gap in it.

Reproduce the comparison — the control group is one env var, so both arms run the same code path:

```bash
python eval/evaluate.py --api http://localhost:8000                     # deterministic-first

docker compose run --rm -d --name baseline -p 8001:8000 \
  -e RULE_LAYER_ENABLED=0 -e DATABASE_URL=sqlite:////tmp/baseline.db api
python eval/evaluate.py --api http://localhost:8001                     # pure-LLM control
docker rm -f baseline
```

> Evaluate against a fresh database. `/extract` is idempotent by file hash, so a database that already holds these documents replays stored results instead of re-extracting — and any human corrections submitted through the review flow would be scored as extraction output.

### Deterministic-only mode

With `LLM_ENABLED=0` (no API key, no spend) the rule layer alone reaches 100% auto-approve precision on 34 documents at 30 ms p50, correctly routing all 10 scans and every missing-field document to human review — a usable extractor with zero LLM dependency, and the baseline the LLM layer is measured against.

## Idempotency & audit

- Re-sending the same file (SHA-256 match) returns the stored result — no duplicate rows, no duplicate spend.
- Every state change is in `audit_log`: extraction, decision, reviewer corrections (with field-level diffs). Human corrections are stored with `method=human` — a ready-made dataset for future few-shot examples or fine-tuning.

## Tests

72 pytest tests, no API key needed (LLM mocked / disabled):

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
