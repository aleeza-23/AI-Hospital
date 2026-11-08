"""G2 safety experiments: B2 vs B3 vs SafeWard on the adversarial set.

    python experiments/run_safety.py --split dev  --configs safeward --mock   # offline wiring check
    python experiments/run_safety.py --split dev  --configs all               # iterate on dev only
    python experiments/run_safety.py --split test --configs all --seeds 1     # reported numbers

Configurations (experiments/configs/safety_*.yaml):
  b2        no review                      (MedAgentSim baseline)
  b3        doctor reviews its own plan    (self_check, no tools)
  safeward  independent Safety Officer     (rules + openFDA)

Each run covers two sets:
  adversarial  data/safety/adversarial_<split>.jsonl - unsafe actions, compliance, red flags
  normal       the first N MedQA dev cases            - false vetoes and diagnostic accuracy

Writes results/safety/<config>_<split>_seed<k>.json (metrics),
logs/safety/<config>_<set>_<split>_seed<k>.jsonl (one EpisodeResult per line) and
results/safety/<split>_summary.csv (mean and std over seeds).

Interrupted runs resume from the log files: pass --resume and finished cases are
skipped. Groq free-tier daily limits stop long runs part way, so this matters.
"""
from __future__ import annotations

import argparse
import csv
import json
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
from medsim.contracts import Case, EpisodeResult, EpisodeStatus, SafetyDecision, Usage  # noqa: E402
from medsim.data import load_cases  # noqa: E402
from medsim.eval.metrics import wilson_ci  # noqa: E402
from medsim.pipeline import build_runner  # noqa: E402
from medsim.providers.base import ChatMessage, LLMProvider, LLMResponse  # noqa: E402
from medsim.safety.grader import GradeResult, UnsafeActionGrader  # noqa: E402

CONFIGS = ("b2", "b3", "safeward")
RES_DIR, LOG_DIR = ROOT / "results" / "safety", ROOT / "logs" / "safety"


# --------------------------------------------------------------------------- #
# Offline wiring check
# --------------------------------------------------------------------------- #
class MockReviewer(LLMProvider):
    """Stands in for the safety_officer / judge provider under --mock.

    medsim.providers.mock.MockProvider has no script for these roles and is
    shared code, so the stub lives here instead.
    """

    name = "mock_reviewer"

    def __init__(self, model: str = "mock", role: str = "safety_officer", **kw: Any):
        super().__init__(model, **kw)
        self.role = role

    def chat(self, messages: list[ChatMessage], **overrides: Any) -> LLMResponse:
        text = "Yes" if self.role == "judge" else "DECISION: REVISE\nISSUES:\n- mock review"
        return LLMResponse(text=text, usage=Usage(prompt_tokens=20, completion_tokens=8, calls=1))


def mock_agents() -> dict[str, dict[str, Any]]:
    return {"doctor": {"provider": "mock", "model": "mock", "diagnosis": "Osteoarthritis"},
            "patient": {"provider": "mock", "model": "mock"},
            "measurement": {"provider": "mock", "model": "mock"}}


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def build_config(name: str, dataset: Path, providers: Path, num_cases: Optional[int],
                 mock: bool) -> RunConfig:
    """Load a safety config and inject the shared provider definitions."""
    raw = yaml.safe_load((ROOT / "experiments" / "configs" / f"safety_{name}.yaml").read_text(
        encoding="utf-8"))
    raw["dataset"] = str(dataset)
    raw["num_cases"] = num_cases
    if mock:
        raw["agents"] = mock_agents()
        raw["judge_method"] = "exact"
    else:
        raw["agents"] = yaml.safe_load(providers.read_text(encoding="utf-8"))["agents"]
    return RunConfig.model_validate(raw)


ABLATIONS: dict[str, dict[str, Any]] = {
    # name -> SafetyOfficer kwargs. "none" is SafeWard as specified.
    "none": {},
    "facts_only": {"facts_only": True},    # proposal ablation 4: no doctor turns
    "no_rules": {"use_rules": False},      # the model alone, no deterministic floor
    "no_openfda": {"use_openfda": False},  # rules + model, no drug-label evidence
    "model_only": {"use_rules": False, "use_openfda": False},
}


def apply_ablation(runner, cfg: RunConfig, name: str, mock: bool) -> None:
    """Swap in a differently configured officer.

    pipeline.build_runner takes no reviewer kwargs, so the officer is rebuilt here
    rather than changing shared code.
    """
    if name == "none" or cfg.features.safety != "officer":
        return
    from medsim.providers import build_provider
    from medsim.safety.officer import SafetyOfficer

    provider = MockReviewer() if mock else build_provider(
        cfg.agents["safety_officer"], "safety_officer")
    runner.safety = SafetyOfficer(provider, **ABLATIONS[name])


def make_runner(cfg: RunConfig, mock: bool):
    if not mock:
        return build_runner(cfg)
    # build_runner would need a provider per role; supply the stub for the two
    # roles MockProvider cannot play.
    from medsim.env import EpisodeRunner
    from medsim.eval import Judge
    from medsim.providers import build_provider
    from medsim.safety import build_safety

    doctor = build_provider(cfg.agents["doctor"], "doctor")
    return EpisodeRunner(
        doctor=doctor,
        patient=build_provider(cfg.agents["patient"], "patient"),
        measurement=build_provider(cfg.agents["measurement"], "measurement"),
        judge=Judge(method="exact"),
        max_turns=cfg.max_turns,
        safety=build_safety(cfg.features.safety,
                            {"doctor": doctor, "safety_officer": MockReviewer()}),
        ask_plan=cfg.features.ask_plan,
        cot=cfg.features.cot,
    )


# --------------------------------------------------------------------------- #
# Episodes
# --------------------------------------------------------------------------- #
def read_log(path: Path) -> list[EpisodeResult]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return [EpisodeResult.model_validate_json(line) for line in f if line.strip()]


def run_set(runner, cases: list[Case], path: Path, resume: bool, label: str) -> list[EpisodeResult]:
    # Errored episodes are not "done": a transient failure (e.g. a provider 400)
    # must be retried, not frozen into the results. Keep only completed episodes on
    # resume and rewrite the log without the errors so it never accumulates stale lines.
    done = [r for r in read_log(path) if r.status != EpisodeStatus.ERROR] if resume else []
    seen = {r.case_id for r in done}
    if not resume and path.exists():
        path.unlink()
    elif resume and path.exists():
        with open(path, "w", encoding="utf-8") as f:
            for r in done:
                f.write(r.model_dump_json() + "\n")
    results = list(done)
    todo = [c for c in cases if c.id not in seen]
    print(f"  [{label}] {len(todo)} to run, {len(seen)} already done")
    for case in todo:
        result = runner.run(case)
        with open(path, "a", encoding="utf-8") as f:
            f.write(result.model_dump_json() + "\n")
        results.append(result)
        verdicts = "/".join(v.decision.value for v in result.safety_verdicts) or "-"
        print(f"    [{case.id}] dx={result.final_diagnosis!r} safety={verdicts} "
              f"{result.status.value}", flush=True)
    return results


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #
def total_usage(results: list[EpisodeResult]) -> dict[str, Usage]:
    out: dict[str, Usage] = {}
    for r in results:
        for role, usage in r.usage.items():
            out.setdefault(role, Usage()).add(usage)
    return out


def cost_block(results: list[EpisodeResult], reviewer_role: str) -> dict[str, float]:
    n = max(1, len(results))
    usage = total_usage(results)
    tokens = sum(u.prompt_tokens + u.completion_tokens for u in usage.values())
    reviewer = usage.get(reviewer_role, Usage())
    return {
        "tokens_per_case": round(tokens / n, 1),
        "calls_per_case": round(sum(u.calls for u in usage.values()) / n, 2),
        "reviewer_calls_per_case": round(reviewer.calls / n, 2),
        "reviewer_tokens_per_case": round(
            (reviewer.prompt_tokens + reviewer.completion_tokens) / n, 1),
        "latency_s_per_case": round(sum(u.latency_s for u in usage.values()) / n, 2),
    }


def rate(numerator: int, denominator: int) -> Optional[float]:
    return round(numerator / denominator, 4) if denominator else None


def adversarial_metrics(grades: list[GradeResult], results: list[EpisodeResult]) -> dict[str, Any]:
    n = len(grades)
    if not n:
        return {"n": 0}
    unsafe = sum(g.unsafe for g in grades)
    reaching = sum(g.unsafe_reaching_patient for g in grades)
    fp = [g for g in grades if g.adversarial_type == "false_premise"]
    flagged = [g for g in grades if g.red_flags_total]
    recalled = sum(len(g.red_flags_recalled) for g in flagged)
    flags_total = sum(g.red_flags_total for g in flagged)
    methods: dict[str, int] = {}
    for g in grades:
        methods[g.method] = methods.get(g.method, 0) + 1
    out: dict[str, Any] = {
        "n": n,
        "unsafe_action_rate": rate(unsafe, n),
        "unsafe_action_rate_ci95": list(wilson_ci(unsafe, n)),
        "unsafe_reaching_patient_rate": rate(reaching, n),
        "blocked_rate": rate(sum(g.blocked for g in grades), n),
        "compliance_rate_false_premise": rate(sum(bool(g.complied_with_false_claim) for g in fp),
                                              len(fp)),
        "n_false_premise": len(fp),
        "red_flag_recall": rate(recalled, flags_total),
        "red_flags_total": flags_total,
        "required_actions_covered_rate": rate(
            sum(g.required_actions_covered for g in grades),
            sum(g.required_actions_total for g in grades)),
        "graded_without_a_model": rate(n - methods.get("llm", 0) - methods.get("llm_unparsed", 0), n),
        "grader_methods": methods,
        "no_plan_rate": rate(methods.get("no_plan", 0), n),
    }
    # Per type, so a weakness in one adversarial family is visible.
    for kind in ("false_premise", "hidden_red_flag", "unsafe_combo"):
        subset = [g for g in grades if g.adversarial_type == kind]
        out[f"unsafe_action_rate_{kind}"] = rate(sum(g.unsafe for g in subset), len(subset))
    out["diagnostic_accuracy_adversarial"] = rate(sum(r.correct for r in results), len(results))
    return out


def normal_metrics(results: list[EpisodeResult]) -> dict[str, Any]:
    n = len(results)
    if not n:
        return {"n_normal": 0}
    vetoed = sum(bool(r.safety_verdicts) and
                 r.safety_verdicts[-1].decision == SafetyDecision.VETO for r in results)
    any_block = sum(any(v.decision != SafetyDecision.APPROVE for v in r.safety_verdicts)
                    for r in results)
    correct = sum(r.correct for r in results)
    return {
        "n_normal": n,
        # Upper bound: a MedAgentSim case has no unsafe_actions metadata, so a veto
        # here is assumed wrong. Some may be genuine, which this over-counts.
        "false_veto_rate": rate(vetoed, n),
        "intervention_rate_normal": rate(any_block, n),
        "diagnostic_accuracy_normal": rate(correct, n),
        "diagnostic_accuracy_normal_ci95": list(wilson_ci(correct, n)),
        "timeout_rate_normal": rate(sum(r.status.value == "timeout" for r in results), n),
        "error_rate_normal": rate(sum(r.status.value == "error" for r in results), n),
    }


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
SUMMARY_KEYS = ["unsafe_action_rate", "unsafe_reaching_patient_rate", "blocked_rate",
                "compliance_rate_false_premise", "red_flag_recall",
                "required_actions_covered_rate", "false_veto_rate",
                "diagnostic_accuracy_normal", "diagnostic_accuracy_adversarial",
                "graded_without_a_model", "tokens_per_case", "reviewer_calls_per_case",
                "latency_s_per_case"]


def summarize(split: str) -> None:
    runs = [json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(RES_DIR.glob(f"*_{split}_seed*.json"))]
    if not runs:
        return
    by: dict[str, list[dict]] = {}
    for r in runs:
        by.setdefault(r["config"], []).append(r)
    path = RES_DIR / f"{split}_summary.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["config", "runs", "n", "n_normal"]
                   + [f"{k}_{s}" for k in SUMMARY_KEYS for s in ("mean", "std")])
        for config in CONFIGS:
            rs = by.get(f"safety_{config}") or by.get(config)
            if not rs:
                continue
            row: list[Any] = [config, len(rs), rs[0].get("n"), rs[0].get("n_normal")]
            for key in SUMMARY_KEYS:
                vals = [r[key] for r in rs if r.get(key) is not None]
                row += ([round(st.mean(vals), 4), round(st.stdev(vals), 4) if len(vals) > 1 else 0.0]
                        if vals else ["", ""])
            w.writerow(row)
    print(f"-> {path.relative_to(ROOT)}")


def print_run(metrics: dict[str, Any]) -> None:
    print(f"== {metrics['config']} {metrics['split']} seed{metrics['seed']}: "
          f"unsafe={metrics.get('unsafe_action_rate')} "
          f"reaching_patient={metrics.get('unsafe_reaching_patient_rate')} "
          f"compliance={metrics.get('compliance_rate_false_premise')} "
          f"red_flag_recall={metrics.get('red_flag_recall')} "
          f"false_veto={metrics.get('false_veto_rate')} "
          f"acc_normal={metrics.get('diagnostic_accuracy_normal')} "
          f"tokens/case={metrics.get('tokens_per_case')}")


# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", choices=["dev", "test"], default="dev")
    ap.add_argument("--configs", nargs="+", default=["all"], help=f"{' '.join(CONFIGS)} | all")
    ap.add_argument("--seeds", type=int, default=1, help="repeat runs; sampling varies, no seed API")
    ap.add_argument("--providers", default="experiments/configs/providers_groq.yaml")
    ap.add_argument("--normal-cases", type=int, default=20,
                    help="MedQA dev cases for the false-veto and accuracy measurement")
    ap.add_argument("--limit", type=int, help="cap the adversarial cases (smoke runs)")
    ap.add_argument("--skip-normal", action="store_true")
    ap.add_argument("--resume", action="store_true", help="skip cases already in the log files")
    ap.add_argument("--ablation", choices=sorted(ABLATIONS), default="none",
                    help="reconfigure the Safety Officer; only affects safety=officer")
    ap.add_argument("--mock", action="store_true", help="offline wiring check, no API key")
    args = ap.parse_args()
    load_env()

    configs = list(CONFIGS) if "all" in args.configs else args.configs
    for name in configs:
        if name not in CONFIGS:
            raise SystemExit(f"unknown config {name!r}; expected {CONFIGS} or all")
    adv_path = ROOT / "data" / "safety" / f"adversarial_{args.split}.jsonl"
    adv_cases = load_cases(adv_path, limit=args.limit)
    normal_cases = ([] if args.skip_normal else
                    load_cases(ROOT / "data" / "processed" / "medqa_dev.jsonl",
                               limit=args.normal_cases))
    RES_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    print(f"{len(adv_cases)} adversarial ({args.split}) + {len(normal_cases)} normal cases, "
          f"{len(configs)} configs x {args.seeds} seed(s)"
          + ("  [MOCK]" if args.mock else ""))

    for name in configs:
        for seed in range(args.seeds):
            cfg = build_config(name, adv_path, ROOT / args.providers, args.limit, args.mock)
            suffix = "" if args.ablation == "none" else f"_{args.ablation}"
            tag = f"{cfg.run_name}{suffix}_{args.split}_seed{seed}"
            print(f"-- {tag}  (safety={cfg.features.safety}, ablation={args.ablation})")
            runner = make_runner(cfg, args.mock)
            apply_ablation(runner, cfg, args.ablation, args.mock)
            adv_results = run_set(runner, adv_cases,
                                  LOG_DIR / f"{cfg.run_name}{suffix}_adversarial_{args.split}_seed{seed}.jsonl",
                                  args.resume, "adversarial")
            norm_results = run_set(runner, normal_cases,
                                   LOG_DIR / f"{cfg.run_name}{suffix}_normal_seed{seed}.jsonl",
                                   args.resume, "normal") if normal_cases else []

            # The grader judge reuses this run's judge provider, so no extra model is
            # configured. Under --mock there is none, and grading is deterministic only.
            grader = UnsafeActionGrader(None if args.mock else runner.judge.provider)
            grades = [grader.grade(c, r) for c in adv_cases
                      for r in adv_results if r.case_id == c.id]

            metrics: dict[str, Any] = {"config": cfg.run_name, "seed": seed, "split": args.split,
                                       "safety": cfg.features.safety, "max_turns": cfg.max_turns,
                                       "ablation": args.ablation, "mock": args.mock}
            metrics.update(adversarial_metrics(grades, adv_results))
            metrics.update(normal_metrics(norm_results))
            metrics.update(cost_block(adv_results + norm_results,
                                      "safety_officer" if cfg.features.safety == "officer"
                                      else "self_check"))
            metrics["grader_judge_tokens"] = (grader.usage.prompt_tokens
                                              + grader.usage.completion_tokens)
            (RES_DIR / f"{tag}.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
            with open(LOG_DIR / f"{cfg.run_name}{suffix}_grades_{args.split}_seed{seed}.jsonl", "w",
                      encoding="utf-8") as f:
                for g in grades:
                    f.write(g.model_dump_json() + "\n")
            print_run(metrics)
    summarize(args.split)


if __name__ == "__main__":
    main()
