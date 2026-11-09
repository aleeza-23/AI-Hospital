"""G3: the consultation experiments E1 / E2 / E3 on a fixed MedQA test subset.

    python experiments/run_consultation.py --configs all --mock      # offline wiring check
    python experiments/run_consultation.py --configs e1 e2 --n 40    # the reported numbers

Configurations (experiments/configs/consultation_*.yaml):
  e1       all features off. The Phase 1 system, and the reference for the rest.
  e2       MedAgentSim reproduction: ungated memory + chain of thought.
           The paper's multi-doctor majority-vote ensembling is skipped for cost.
  e3       SafeWard's verified memory gate + chain of thought, no safety review.
           Isolates the gate against e2.
  e3_full  the complete SafeWard system: gated memory + CoT + the G2 Safety
           Officer. Runnable because GatedMemory refuses to store an outcome the
           officer vetoed.

The subset is drawn once with --subset-seed (default 0) and the case ids are
recorded in every result file, so all configurations see the same cases in the
same order. The subset is never selected or tuned on results.

Writes results/consultation/<config>_seed<k>.json, results/consultation/summary.csv
and logs/consultation/<config>_seed<k>.jsonl. Resume with --resume.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import statistics as st
import sys
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# Windows consoles default to cp1252; model output can contain non-ASCII
# characters (e.g. U+2011 non-breaking hyphen), which crash progress prints
# mid-run. Force UTF-8 on the streams so a run never dies on a diagnosis string.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # non-reconfigurable stream (e.g. piped)
        pass

import yaml  # noqa: E402

from medsim.config import RunConfig, load_env  # noqa: E402
from medsim.contracts import Case, EpisodeResult, EpisodeStatus  # noqa: E402
from medsim.data import load_cases  # noqa: E402
from medsim.eval import compute_metrics  # noqa: E402
from medsim.pipeline import build_runner  # noqa: E402

CONFIGS = ("e1", "e2", "e3", "e3_full")
RES_DIR, LOG_DIR = ROOT / "results" / "consultation", ROOT / "logs" / "consultation"


class MockReviewer:
    """Stub for the safety_officer / judge roles under --mock.

    medsim.providers.mock.MockProvider has no script for them and is shared code.
    """

    name = "mock_reviewer"
    model = "mock"

    def __init__(self, role: str = "safety_officer"):
        self.role = role

    def chat(self, messages: list[dict[str, Any]], **overrides: Any):
        from medsim.contracts import Usage
        from medsim.providers.base import LLMResponse
        text = "Yes" if self.role == "judge" else "DECISION: APPROVE\nISSUES:\n- none"
        return LLMResponse(text=text, usage=Usage(prompt_tokens=20, completion_tokens=6, calls=1))


def mock_agents() -> dict[str, dict[str, Any]]:
    return {"doctor": {"provider": "mock", "model": "mock", "diagnosis": "Myasthenia gravis"},
            "patient": {"provider": "mock", "model": "mock"},
            "measurement": {"provider": "mock", "model": "mock"}}


def build_config(name: str, providers: Path, mock: bool) -> RunConfig:
    raw = yaml.safe_load((ROOT / "experiments" / "configs" / f"consultation_{name}.yaml").read_text(
        encoding="utf-8"))
    raw["workers"] = 1
    if mock:
        raw["agents"] = mock_agents()
        raw["judge_method"] = "exact"
    else:
        raw["agents"] = yaml.safe_load(providers.read_text(encoding="utf-8"))["agents"]
    return RunConfig.model_validate(raw)


def make_runner(cfg: RunConfig, mock: bool):
    if not mock or cfg.features.safety == "none":
        return build_runner(cfg)
    # e3_full under --mock needs a reviewer MockProvider cannot play.
    from medsim.env import EpisodeRunner
    from medsim.eval import Judge
    from medsim.memory import build_memory
    from medsim.providers import build_provider
    from medsim.safety import build_safety

    doctor = build_provider(cfg.agents["doctor"], "doctor")
    return EpisodeRunner(
        doctor=doctor,
        patient=build_provider(cfg.agents["patient"], "patient"),
        measurement=build_provider(cfg.agents["measurement"], "measurement"),
        judge=Judge(method="exact"),
        max_turns=cfg.max_turns,
        memory=build_memory(cfg.features.memory, k=cfg.features.memory_k),
        safety=build_safety(cfg.features.safety,
                            {"doctor": doctor, "safety_officer": MockReviewer()}),
        ask_plan=cfg.features.ask_plan,
        cot=cfg.features.cot,
    )


def subset(dataset: Path, n: int, seed: int) -> list[Case]:
    """A fixed, seeded subset, identical for every configuration."""
    cases = load_cases(dataset)
    if n >= len(cases):
        return cases
    picked = random.Random(seed).sample(range(len(cases)), n)
    return [cases[i] for i in sorted(picked)]


def read_log(path: Path) -> list[EpisodeResult]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return [EpisodeResult.model_validate_json(line) for line in f if line.strip()]


def run_one(name: str, seed: int, cases: list[Case], providers: Path, mock: bool,
            resume: bool, subset_seed: int) -> dict[str, Any]:
    cfg = build_config(name, providers, mock)
    runner = make_runner(cfg, mock)
    tag = f"{cfg.run_name}_seed{seed}"
    log_path = LOG_DIR / f"{tag}.jsonl"
    # An errored episode is not "done": a transient failure (a provider 400, a
    # dropped connection) must be retried on resume, not frozen into the results.
    # The log is rewritten without the error lines so it never accumulates stale ones.
    done = [r for r in read_log(log_path) if r.status != EpisodeStatus.ERROR] if resume else []
    seen = {r.case_id for r in done}
    if not resume and log_path.exists():
        log_path.unlink()
    elif resume and log_path.exists():
        with open(log_path, "w", encoding="utf-8") as f:
            for r in done:
                f.write(r.model_dump_json() + "\n")

    print(f"-- {tag}: memory={cfg.features.memory} safety={cfg.features.safety} "
          f"cot={cfg.features.cot} | {len(cases) - len(seen)} to run, {len(seen)} done")
    results = list(done)
    for case in cases:
        if case.id in seen:
            continue
        result = runner.run(case)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(result.model_dump_json() + "\n")
        results.append(result)
        print(f"   [{case.id}] {'CORRECT' if result.correct else 'WRONG  '} "
              f"pred={result.final_diagnosis!r} turns={result.num_turns} "
              f"tests={result.num_tests} {result.status.value}", flush=True)

    base = compute_metrics(cfg.run_name, results).model_dump()
    memory_stats = runner.memory.stats() if hasattr(runner.memory, "stats") else {}
    metrics: dict[str, Any] = {
        "config": name, "run_name": cfg.run_name, "seed": seed, "mock": mock,
        "memory": cfg.features.memory, "safety": cfg.features.safety,
        "cot": cfg.features.cot, "max_turns": cfg.max_turns,
        "subset_seed": subset_seed, "case_ids": [c.id for c in cases],
        **base,
        "tokens_per_case": round(
            (base["total_prompt_tokens"] + base["total_completion_tokens"]) / max(1, base["num_cases"]), 1),
        "latency_s_per_case": round(base["total_latency_s"] / max(1, base["num_cases"]), 2),
        "failure_rate": round(base["timeout_rate"] + base["error_rate"], 4),
        "memory_entries_active": memory_stats.get("entries_active", 0),
        "memory_entries_retracted": memory_stats.get("retracted", 0),
        "cases_with_memory_context": sum(1 for r in results if r.memory_context),
        "safety_interventions": sum(
            1 for r in results
            if any(v.decision.value != "approve" for v in r.safety_verdicts)),
    }
    RES_DIR.mkdir(parents=True, exist_ok=True)
    (RES_DIR / f"{tag}.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    print(f"== {tag}: accuracy={metrics['accuracy']} "
          f"ci95={[round(x, 3) for x in metrics['accuracy_ci95']]} "
          f"turns={metrics['avg_turns']} tests={metrics['avg_tests']} "
          f"tokens/case={metrics['tokens_per_case']} failures={metrics['failure_rate']}")
    return metrics


KEYS = ["accuracy", "avg_turns", "avg_tests", "tokens_per_case", "latency_s_per_case",
        "failure_rate", "timeout_rate", "error_rate", "cases_with_memory_context",
        "memory_entries_active", "memory_entries_retracted", "safety_interventions"]


def summarize() -> None:
    runs = [json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(RES_DIR.glob("*_seed*.json"))]
    if not runs:
        return
    by: dict[str, list[dict]] = {}
    for r in runs:
        by.setdefault(r["config"], []).append(r)
    path = RES_DIR / "summary.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["config", "memory", "safety", "cot", "runs", "n"]
                   + [f"{k}_{s}" for k in KEYS for s in ("mean", "std")])
        for name in CONFIGS:
            rs = by.get(name)
            if not rs:
                continue
            row: list[Any] = [name, rs[0]["memory"], rs[0]["safety"], rs[0]["cot"],
                              len(rs), rs[0]["num_cases"]]
            for key in KEYS:
                vals = [r[key] for r in rs if r.get(key) is not None]
                row += ([round(st.mean(vals), 4),
                         round(st.stdev(vals), 4) if len(vals) > 1 else 0.0]
                        if vals else ["", ""])
            w.writerow(row)
    print(f"-> {path.relative_to(ROOT)}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--configs", nargs="+", default=["all"], help=f"{' '.join(CONFIGS)} | all")
    ap.add_argument("--n", type=int, default=40, help="cases in the fixed subset (brief: 30-50)")
    ap.add_argument("--subset-seed", type=int, default=0, help="recorded in every result file")
    ap.add_argument("--seeds", type=int, default=1, help="repeat runs; sampling varies, no seed API")
    ap.add_argument("--providers", default="experiments/configs/providers_groq.yaml")
    ap.add_argument("--dataset", default="data/processed/medqa_test.jsonl")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--mock", action="store_true", help="offline wiring check, no API key")
    args = ap.parse_args()
    load_env()

    names = list(CONFIGS) if "all" in args.configs else args.configs
    for name in names:
        if name not in CONFIGS:
            raise SystemExit(f"unknown config {name!r}; expected {CONFIGS} or all")
    cases = subset(ROOT / args.dataset, args.n, args.subset_seed)
    RES_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    print(f"{len(cases)} cases from {args.dataset} (subset seed {args.subset_seed}), "
          f"{len(names)} configs x {args.seeds} seed(s)" + ("  [MOCK]" if args.mock else ""))

    for name in names:
        for seed in range(args.seeds):
            run_one(name, seed, cases, ROOT / args.providers, args.mock, args.resume,
                    args.subset_seed)
    summarize()


if __name__ == "__main__":
    main()
