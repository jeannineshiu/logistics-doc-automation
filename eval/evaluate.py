"""Field-level evaluation against ground_truth.json.

Sends every sample through POST /extract and reports:
  - field-level accuracy
  - doc-level auto-approve precision
  - human intervention rate
  - rule-layer coverage (zero-token fields)
  - cost per document, p50/p95 latency

Usage:
  python eval/evaluate.py --api http://localhost:8000 [--samples data/samples] [--limit 15]
"""

import argparse
import json
import statistics
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]


def normalize(v):
    return str(v).strip().lower() if v is not None else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--samples", default=str(ROOT / "data" / "samples"))
    ap.add_argument("--gt", default=str(ROOT / "data" / "ground_truth.json"))
    ap.add_argument("--limit", type=int, default=0, help="evaluate only the first N docs")
    args = ap.parse_args()

    gt_all = json.loads(Path(args.gt).read_text())
    files = sorted(Path(args.samples).glob("*.pdf"))
    if args.limit:
        files = files[: args.limit]

    field_total = field_correct = 0
    rule_fields = llm_fields = 0
    auto_docs = auto_docs_all_correct = 0
    interventions = 0
    costs, latencies = [], []
    per_doc = []

    for path in files:
        gt = gt_all.get(path.name)
        if not gt:
            continue
        with path.open("rb") as fh:
            r = requests.post(f"{args.api}/extract", files={"file": (path.name, fh, "application/pdf")})
        r.raise_for_status()
        res = r.json()
        costs.append(res["cost_usd"])
        latencies.append(res["latency_ms"])

        expected = {k: v for k, v in gt.items() if k != "doc_type"}
        fields = res.get("fields") or {}
        doc_correct = res["doc_type"] == gt["doc_type"]
        n_ok = 0
        for name, want in expected.items():
            got = (fields.get(name) or {}).get("value")
            method = (fields.get(name) or {}).get("method")
            if method == "rule":
                rule_fields += 1
            elif method == "llm":
                llm_fields += 1
            field_total += 1
            ok = normalize(got) == normalize(want)
            field_correct += ok
            n_ok += ok

        if res["decision"] == "auto_approve":
            auto_docs += 1
            auto_docs_all_correct += (n_ok == len(expected) and doc_correct)
        else:
            interventions += 1
        per_doc.append((path.name, res["decision"], f"{n_ok}/{len(expected)}"))

    n = len(per_doc)
    print(f"\n=== Evaluation over {n} documents ===")
    for name, decision, score in per_doc:
        print(f"  {name:<28} {decision:<14} fields correct: {score}")
    print()
    print(f"Field-level accuracy        : {field_correct}/{field_total} = {field_correct/field_total:.1%}")
    if auto_docs:
        print(f"Auto-approve precision      : {auto_docs_all_correct}/{auto_docs} = {auto_docs_all_correct/auto_docs:.1%}")
    print(f"Human intervention rate     : {interventions}/{n} = {interventions/n:.1%}")
    covered = rule_fields + llm_fields
    if covered:
        print(f"Rule-layer coverage         : {rule_fields}/{covered} = {rule_fields/covered:.1%} of extracted fields (zero token cost)")
    print(f"Cost per document (mean)    : ${statistics.mean(costs):.5f}")
    print(f"Latency p50 / p95 (ms)      : {statistics.median(latencies):.0f} / "
          f"{sorted(latencies)[max(0, int(len(latencies)*0.95)-1)]:.0f}")


if __name__ == "__main__":
    main()
