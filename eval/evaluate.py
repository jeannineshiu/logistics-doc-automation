"""Field-level evaluation against a ground truth.

Sends every sample through POST /extract and reports:
  - field-level accuracy
  - doc-level auto-approve precision
  - human intervention rate
  - rule-layer coverage (zero-token fields)
  - cost per document, p50/p95 latency

Two corpora, one scorer:

  python eval/evaluate.py --api http://localhost:8000
  python eval/evaluate.py --manifest data/real/sroie/manifest.json

The first is the synthetic corpus, where every field of the schema is known and
compared with string equality. The second is a real public corpus prepared by
data/prepare_real.py, which labels only part of the schema — the manifest names
which fields are scored and how each one is compared, and the rest are left out
of the numbers rather than counted as misses.

Evaluate against a fresh database: /extract is idempotent by file hash, so a
database that already holds these documents replays stored results.
"""

import argparse
import json
import mimetypes
import statistics
from collections import Counter
from pathlib import Path

import requests
from compare import compare

ROOT = Path(__file__).resolve().parents[1]


def load_manifest(path: Path):
    """Real corpus: files, per-file expected fields, and a comparator per field."""
    m = json.loads(path.read_text())
    corpus = [
        (path.parent / name, doc["fields"], doc.get("doc_type"))
        for name, doc in m["documents"].items()
    ]
    label = f"{m['dataset']} ({len(corpus)} documents, source: {m['source']})"
    return corpus, m["scored_fields"], label, m.get("notes", "")


def load_legacy(gt_path: Path, samples: Path):
    """Synthetic corpus: ground_truth.json keyed by filename, every field exact."""
    gt_all = json.loads(gt_path.read_text())
    corpus = []
    for file in sorted(samples.glob("*.pdf")):
        gt = gt_all.get(file.name)
        if gt:
            expected = {k: v for k, v in gt.items() if k != "doc_type"}
            corpus.append((file, expected, gt["doc_type"]))
    scored = {name: "exact" for _, expected, _ in corpus for name in expected}
    return corpus, scored, f"synthetic ({len(corpus)} documents)", ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--samples", default=str(ROOT / "data" / "samples"))
    ap.add_argument("--gt", default=str(ROOT / "data" / "ground_truth.json"))
    ap.add_argument("--manifest", help="a real corpus prepared by data/prepare_real.py")
    ap.add_argument("--limit", type=int, default=0, help="evaluate only the first N docs")
    args = ap.parse_args()

    if args.manifest:
        corpus, scored_fields, label, notes = load_manifest(Path(args.manifest))
    else:
        corpus, scored_fields, label, notes = load_legacy(Path(args.gt), Path(args.samples))
    if args.limit:
        corpus = corpus[: args.limit]

    field_total = field_correct = 0
    rule_fields = llm_fields = 0
    auto_docs = auto_docs_all_correct = 0
    interventions = 0
    costs, latencies = [], []
    per_doc = []
    decisions, doc_types, notes_seen = Counter(), Counter(), Counter()

    for path, expected, want_doc_type in corpus:
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        with path.open("rb") as fh:
            r = requests.post(f"{args.api}/extract", files={"file": (path.name, fh, mime)})
        r.raise_for_status()
        res = r.json()
        costs.append(res["cost_usd"])
        latencies.append(res["latency_ms"])
        decisions[res["decision"]] += 1
        doc_types[res["doc_type"]] += 1

        fields = res.get("fields") or {}
        for field in fields.values():
            if (field or {}).get("method") == "rule":
                rule_fields += 1
            elif (field or {}).get("method") == "llm":
                llm_fields += 1

        doc_correct = want_doc_type is None or res["doc_type"] == want_doc_type
        n_ok, doc_notes = 0, []
        for name, want in expected.items():
            got = (fields.get(name) or {}).get("value")
            verdict = compare(scored_fields[name], got, want)
            field_total += 1
            field_correct += verdict.ok
            n_ok += verdict.ok
            if not verdict.ok:
                doc_notes.append(f"{name}: got {got!r} want {want!r}"
                                 + (f" [{verdict.note}]" if verdict.note else ""))
                if verdict.note:
                    notes_seen[verdict.note.split()[0]] += 1

        if res["decision"] == "auto_approve":
            auto_docs += 1
            auto_docs_all_correct += n_ok == len(expected) and doc_correct
        else:
            interventions += 1
        per_doc.append((path.name, res["decision"], f"{n_ok}/{len(expected)}", doc_notes))

    n = len(per_doc)
    print(f"\n=== Evaluation over {n} documents: {label} ===")
    if notes:
        print(f"    {notes}\n")
    for name, decision, score, doc_notes in per_doc:
        print(f"  {name:<28} {decision:<14} fields correct: {score}")
        for note in doc_notes:
            print(f"      - {note}")
    print()
    print(f"Field-level accuracy        : {field_correct}/{field_total} = {field_correct/field_total:.1%}"
          + (f"  (scored fields: {', '.join(scored_fields)})" if args.manifest else ""))
    if auto_docs:
        print(f"Auto-approve precision      : {auto_docs_all_correct}/{auto_docs} = "
              f"{auto_docs_all_correct/auto_docs:.1%}")
    print(f"Human intervention rate     : {interventions}/{n} = {interventions/n:.1%}")
    covered = rule_fields + llm_fields
    if covered:
        print(f"Rule-layer coverage         : {rule_fields}/{covered} = {rule_fields/covered:.1%} "
              "of extracted fields (zero token cost)")
    print(f"Cost per document (mean)    : ${statistics.mean(costs):.5f}")
    print(f"Latency p50 / p95 (ms)      : {statistics.median(latencies):.0f} / "
          f"{sorted(latencies)[max(0, int(len(latencies)*0.95)-1)]:.0f}")
    if args.manifest:
        print(f"Decisions                   : {dict(decisions)}")
        print(f"Detected doc types          : {dict(doc_types)}")
        if notes_seen:
            print(f"Mismatch kinds              : {dict(notes_seen)}")


if __name__ == "__main__":
    main()
