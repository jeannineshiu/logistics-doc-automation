"""Download a real document corpus and write an evaluable manifest.

The synthetic corpus is generated from the same templates the rule layer was
written against, which makes it a test of the wiring rather than of the
extraction. These are real documents nobody in this project laid out.

  python data/prepare_real.py sroie  --limit 25
  python data/prepare_real.py cord   --limit 15
  python data/prepare_real.py docile --limit 25   # needs a local copy, see adapters/docile.py
  python data/prepare_real.py all    --limit 25

Then evaluate against a fresh database (/extract is idempotent by file hash):

  python eval/evaluate.py --manifest data/real/sroie/manifest.json

Nothing downloaded here is committed: data/real/ is gitignored, and both
datasets are distributed under their own terms.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "data"))

from adapters import cord, docile, sroie  # imports need the sys.path line above

ADAPTERS = {"sroie": sroie.prepare, "cord": cord.prepare, "docile": docile.prepare}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("dataset", choices=[*ADAPTERS, "all"])
    ap.add_argument("--limit", type=int, default=25, help="documents per dataset")
    ap.add_argument("--out", default=str(ROOT / "data" / "real"))
    ap.add_argument("--docile-root", default=str(ROOT / "data" / "docile"),
                    help="where annotated-trainval was unzipped")
    args = ap.parse_args()

    names = list(ADAPTERS) if args.dataset == "all" else [args.dataset]
    for name in names:
        out_dir = Path(args.out) / name
        print(f"preparing {name} -> {out_dir}")
        kwargs = {"root": Path(args.docile_root)} if name == "docile" else {}
        manifest = ADAPTERS[name](out_dir, args.limit, **kwargs)
        n = len(json.loads(manifest.read_text())["documents"])
        print(f"  {n} documents, manifest at {manifest}")


if __name__ == "__main__":
    main()
