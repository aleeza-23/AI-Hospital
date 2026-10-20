"""Evaluate / compare finished runs.

    python scripts/evaluate.py runs/<run_dir>                       # recompute metrics.json
    python scripts/evaluate.py runs/A runs/B --table                # markdown comparison table
    python scripts/evaluate.py runs/<run_dir> --export-review 30    # CSV for manual judge audit
    python scripts/evaluate.py runs/<run_dir> --rejudge configs/judge_groq.yaml
"""
from __future__ import annotations

import argparse
import csv
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from medsim.config import load_config, load_env  # noqa: E402
from medsim.eval import Judge, compute_metrics  # noqa: E402
from medsim.pipeline import read_results, write_metrics  # noqa: E402
from medsim.providers import build_provider  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--table", action="store_true")
    ap.add_argument("--export-review", type=int, metavar="N")
    ap.add_argument("--rejudge", metavar="CONFIG", help="Re-grade with the judge from this config.")
    args = ap.parse_args()
    load_env()

    rows = []
    for run in map(Path, args.runs):
        results = read_results(run / "episodes.jsonl")
        if args.rejudge:
            judge = Judge(build_provider(load_config(args.rejudge).agents["judge"], "judge"))
            for r in results:
                r.verdict = judge.grade(r.correct_diagnosis, r.final_diagnosis) if r.final_diagnosis else None
            with open(run / "episodes_rejudged.jsonl", "w", encoding="utf-8") as f:
                f.writelines(r.model_dump_json() + "\n" for r in results)
            m = compute_metrics(run.name, results).model_dump()
        else:
            m = write_metrics(run.name, run)
        rows.append(m)
        print(f"{run.name}: acc={m['accuracy']:.1%} ({m['num_correct']}/{m['num_cases']}) "
              f"turns={m['avg_turns']} tests={m['avg_tests']}")

        if args.export_review:
            sample = random.Random(0).sample(results, min(args.export_review, len(results)))
            path = run / "judge_review.csv"
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["case_id", "correct_diagnosis", "final_diagnosis", "judge_correct", "human_correct"])
                for r in sample:
                    w.writerow([r.case_id, r.correct_diagnosis, r.final_diagnosis, r.correct, ""])
            print(f"  -> fill the human_correct column in {path} to measure judge agreement")

    if args.table:
        print("\n| Run | N | Accuracy | 95% CI | Avg turns | Avg tests | Timeouts |")
        print("|---|---|---|---|---|---|---|")
        for m in rows:
            lo, hi = m["accuracy_ci95"]
            print(f"| {m['run_name']} | {m['num_cases']} | {m['accuracy']:.1%} | {lo:.1%}-{hi:.1%} "
                  f"| {m['avg_turns']} | {m['avg_tests']} | {m['timeout_rate']:.0%} |")


if __name__ == "__main__":
    main()
