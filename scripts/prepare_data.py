"""Download AgentClinic benchmark files and convert them to unified Case JSONL.

    python scripts/prepare_data.py                      # medqa, medqa_ext, nejm, nejm_ext
    python scripts/prepare_data.py --datasets medqa
    python scripts/prepare_data.py --datasets mimiciv --local path/to/agentclinic_mimiciv.jsonl

Outputs data/processed/<name>.jsonl plus a fixed dev/test split
(<name>_dev.jsonl for prompt iteration, <name>_test.jsonl for reported numbers).
"""
from __future__ import annotations

import argparse
import random
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from medsim.data.loaders import AGENTCLINIC_RAW, DATASETS, convert, iter_jsonl, save_cases  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--datasets", nargs="+", default=["medqa", "medqa_ext", "nejm", "nejm_ext"],
                    choices=list(DATASETS))
    ap.add_argument("--local", help="Use this local raw file instead of downloading (one dataset only).")
    ap.add_argument("--dev-size", type=int, default=20)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    raw_dir, out_dir = ROOT / "data" / "raw", ROOT / "data" / "processed"
    raw_dir.mkdir(parents=True, exist_ok=True)

    for name in args.datasets:
        remote, _ = DATASETS[name]
        if args.local:
            raw = Path(args.local)
        elif remote is None:
            print(f"[skip] {name}: credentialed dataset, pass --local <file>")
            continue
        else:
            raw = raw_dir / remote
            if not raw.exists():
                print(f"[download] {remote}")
                urllib.request.urlretrieve(AGENTCLINIC_RAW.format(remote), raw)

        cases = convert(name, iter_jsonl(raw))  # pydantic validates every case
        save_cases(cases, out_dir / f"{name}.jsonl")

        shuffled = cases[:]
        random.Random(args.seed).shuffle(shuffled)
        dev_n = min(args.dev_size, len(cases) // 5)
        dev_ids = {c.id for c in shuffled[:dev_n]}
        save_cases([c for c in cases if c.id in dev_ids], out_dir / f"{name}_dev.jsonl")
        save_cases([c for c in cases if c.id not in dev_ids], out_dir / f"{name}_test.jsonl")
        print(f"[ok] {name}: {len(cases)} cases -> dev {dev_n} / test {len(cases) - dev_n}")


if __name__ == "__main__":
    main()
