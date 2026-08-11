"""Layer 2 — GPT-4o Vision extraction for fields the rule layer missed.

Only the missing fields are requested; output is forced to JSON and parsed
strictly. One retry on parse failure, all calls metered by TokenBudget.
"""

import json
import os

from models.schemas import DocType, ExtractionMethod, FieldResult
from openai import OpenAI

from engine.budget import TokenBudget
from engine.pdf_utils import to_data_url

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")

# Per-request ceiling. The SDK defaults to 600 s and 2 internal retries, which
# outlives the 120 s cap on the n8n HTTP node calling us: n8n would give up,
# re-send the document, and the abandoned request would keep generating
# billable tokens for an answer nobody reads.
#
# Retries are n8n's job — it already retries 3x with backoff — so the SDK does
# none. That keeps the worst case bounded and arithmetically under n8n's cap:
#   MAX_LLM_CALLS_PER_DOC (2) x LLM_TIMEOUT_SECONDS (45) = 90 s < 120 s
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "45"))

# USD per 1M tokens (gpt-4o, 2025 pricing) — used for cost reporting only
PRICE_INPUT = 2.50 / 1_000_000
PRICE_OUTPUT = 10.00 / 1_000_000

FIELD_HINTS = {
    "invoice_number": "the invoice number/identifier",
    "invoice_date": "the invoice issue date, formatted YYYY-MM-DD",
    "supplier_name": "the supplier/seller company name",
    "supplier_vat_id": "the supplier's VAT ID (e.g. DE123456789)",
    "currency": "the ISO 4217 currency code (e.g. EUR)",
    "total_amount": "the grand total amount as a plain decimal, e.g. 1234.56",
    "iban": "the bank IBAN, no spaces",
    "declaration_number": "the customs declaration number",
    "hs_code": "the HS/tariff code, 6-10 digits",
    "country_of_origin": "the country of origin as ISO 3166-1 alpha-2 (e.g. DE)",
    "gross_weight_kg": "the gross weight in kilograms as a plain decimal",
    "declared_value": "the declared value as a plain decimal",
}


def _client() -> OpenAI:
    return OpenAI(timeout=LLM_TIMEOUT_SECONDS, max_retries=0)


def build_prompt(doc_type: DocType, missing_fields: list[str]) -> str:
    lines = [f'- "{f}": {FIELD_HINTS[f]}' for f in missing_fields]
    return (
        f"You are extracting fields from a {doc_type.value.replace('_', ' ')} image.\n"
        "Extract ONLY these fields:\n" + "\n".join(lines) + "\n\n"
        "Respond with a single JSON object of the form:\n"
        '{"fields": {"<field>": {"value": "<string or null>", '
        '"confidence": <0.0-1.0>, "evidence": "<short quote/location in the image>"}}}\n'
        "Rules: if a field is not visible, use value null and confidence 0. "
        "Never guess. Confidence reflects how clearly the value is legible."
    )


def extract_missing_fields(
    page_pngs: list[bytes],
    doc_type: DocType,
    missing_fields: list[str],
    budget: TokenBudget,
    client: OpenAI | None = None,
) -> dict[str, FieldResult]:
    """Call GPT-4o Vision for the missing fields. Raises BudgetExceeded via budget."""
    if not missing_fields:
        return {}
    client = client or _client()
    prompt = build_prompt(doc_type, missing_fields)
    content: list[dict] = [{"type": "text", "text": prompt}]
    for png in page_pngs[:2]:  # cap pages sent to control image-token cost
        content.append({"type": "image_url", "image_url": {"url": to_data_url(png), "detail": "high"}})

    results: dict[str, FieldResult] = {}
    for _attempt in range(2):  # initial call + one retry on bad JSON
        budget.check_can_call()
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": content}],
            response_format={"type": "json_object"},
            max_tokens=800,
            temperature=0,
        )
        budget.record(resp.usage.total_tokens)
        try:
            payload = json.loads(resp.choices[0].message.content)
            raw_fields = payload["fields"]
            for name in missing_fields:
                item = raw_fields.get(name) or {}
                value = item.get("value")
                conf = float(item.get("confidence", 0.0))
                conf = min(max(conf, 0.0), 1.0)
                if value is not None:
                    results[name] = FieldResult(
                        value=str(value), method=ExtractionMethod.LLM, confidence=conf
                    )
                else:
                    results[name] = FieldResult()
            return results
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue  # retry once
    # both attempts failed to parse — report all as missing
    return {name: FieldResult() for name in missing_fields}


def estimate_cost(prompt_tokens: int, completion_tokens: int) -> float:
    return prompt_tokens * PRICE_INPUT + completion_tokens * PRICE_OUTPUT
