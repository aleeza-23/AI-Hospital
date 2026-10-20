"""Run the Multi-Agent Clinic baseline on a dataset.

    python scripts/run_baseline.py --config configs/smoke_mock.yaml          # offline demo
    python scripts/run_baseline.py --config configs/baseline_medqa_groq.yaml --n 20 -v
    python scripts/run_baseline.py --config configs/baseline_medqa_groq.yaml --human doctor
    python scripts/run_baseline.py --config ... --resume runs/<run_dir>      # continue a run
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from medsim.config import load_config, load_env  # noqa: E402
from medsim.pipeline import run_experiment  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True)
    ap.add_argument("--dataset", help="Override dataset path.")
    ap.add_argument("--n", type=int, help="Only run the first N cases.")
    ap.add_argument("--workers", type=int)
    ap.add_argument("--max-turns", type=int)
    ap.add_argument("--human", choices=["doctor", "patient"], help="Control Mode: you play this role.")
    ap.add_argument("--resume", help="Existing run directory to continue.")
    ap.add_argument("-v", "--verbose", action="store_true", help="Print the dialogue live.")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpx2", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    load_env()

    cfg = load_config(args.config)
    overrides = {"dataset": args.dataset, "num_cases": args.n, "workers": args.workers, "max_turns": args.max_turns}
    cfg = cfg.model_copy(update={k: v for k, v in overrides.items() if v is not None})

    out = run_experiment(cfg, human_role=args.human, verbose=args.verbose, resume_dir=args.resume)
    metrics = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    lo, hi = metrics["accuracy_ci95"]
    print(f"\nRun dir : {out}")
    print(f"Accuracy: {metrics['accuracy']:.1%}  ({metrics['num_correct']}/{metrics['num_cases']}, "
          f"95% CI {lo:.1%}-{hi:.1%})")
    print(f"Avg turns {metrics['avg_turns']} | avg tests {metrics['avg_tests']} | "
          f"timeouts {metrics['timeout_rate']:.0%} | errors {metrics['error_rate']:.0%}")


if __name__ == "__main__":
    main()
