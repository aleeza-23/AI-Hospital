"""G1 triage experiments on the fixed KTAS split.

    python experiments/run_triage.py --split dev  --methods nurse          # iterate on dev only
    python experiments/run_triage.py --split test --methods all --seeds 3  # reported numbers

Methods:
  human        real triage nurse's KTAS_RN label (free, from the dataset)
  rules        B1 deterministic rules (free)
  single_llm   B0 one prompt with the full record
  nurse        Triage Nurse agent (interview + GET_VITALS)
  nurse_floor  the same nurse trajectories + rule-floor guardrail
               (final = rules level if rules say 1-2 and the nurse chose less urgent)

Writes results/triage/<split>_<method>_seed<k>.json (metrics),
logs/triage/<split>_<method>_seed<k>.jsonl (one trajectory per case) and
results/triage/<split>_summary.csv (mean and std over seeds).
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import yaml  # noqa: E402

from medsim.config import load_env  # noqa: E402
from medsim.providers import build_provider  # noqa: E402
from medsim.triage import (  # noqa: E402
    TriageDecision, TriageNurse, load_ktas_csv, rule_based_ktas, simulate_ward,
    single_llm_triage, stratified_split, triage_metrics,
)

LLM_METHODS = ("single_llm", "nurse", "nurse_floor")
ALL = ("human", "rules", *LLM_METHODS)
N_WARDS, WARD_SIZE = 5, 50


def apply_rule_floor(d: TriageDecision, case) -> TriageDecision:
    """Guardrail: if the deterministic rules see a level 1-2 danger sign, never let the
    agent assign a less urgent level. Added after dev-set under-triage (docs/devlog.md)."""
    rule_level, rule_flags = rule_based_ktas(case)
    if d.level is not None and rule_level <= 2 and rule_level < d.level:
        d.reason = f"[rule floor {d.level}->{rule_level}: {', '.join(rule_flags)}] " + d.reason
        d.level = rule_level
        d.red_flags = list(dict.fromkeys(d.red_flags + rule_flags))
    return d


def run_method(method, cases, providers) -> list[TriageDecision]:
    out = []
    for c in cases:
        if method == "human":
            d = TriageDecision(case_id=c.id, method="human", level=c.ktas_nurse)
        elif method == "rules":
            lv, flags = rule_based_ktas(c)
            d = TriageDecision(case_id=c.id, method="rules", level=lv, red_flags=flags)
        elif method == "single_llm":
            d = single_llm_triage(providers["triage_nurse"], c)
        else:
            d = TriageNurse(providers["triage_nurse"], providers["patient"]).triage(c)
        out.append(d)
        print(f"  [{method}] {c.id} expert={c.ktas_expert} pred={d.level} {d.error or ''}", flush=True)
    return out


def ward_samples(n_cases: int) -> list[list[int]]:
    return [random.Random(100 + w).sample(range(n_cases), min(WARD_SIZE, n_cases)) for w in range(N_WARDS)]


def write_run(method, seed, decisions, cases, split, res_dir, log_dir) -> dict:
    y_true = [c.ktas_expert for c in cases]
    preds = [d.level or 3 for d in decisions]  # failed triage defaults to level 3
    m = triage_metrics(y_true, preds)
    usages = [u for d in decisions for u in d.usage.values()]
    m.update({
        "method": method, "seed": seed, "split": split,
        "failures": sum(d.level is None for d in decisions),
        "avg_actions": round(st.mean(d.num_actions for d in decisions), 2),
        "vitals_used_rate": round(st.mean(d.used_vitals for d in decisions), 3),
        "tokens_per_case": round(sum(u.prompt_tokens + u.completion_tokens for u in usages) / len(cases), 1),
        "calls_per_case": round(sum(u.calls for u in usages) / len(cases), 2),
        "latency_s_per_case": round(sum(u.latency_s for u in usages) / len(cases), 2),
    })
    # Ward Manager: seeded wards of 50 patients ordered by the assigned acuity.
    wards = [simulate_ward([y_true[i] for i in idx], [preds[i] for i in idx],
                           [bool(decisions[i].red_flags) for i in idx], policy="acuity", seed=w)
             for w, idx in enumerate(ward_samples(len(cases)))]
    for k in wards[0]:
        m[f"ward_{k}"] = round(st.mean(x[k] for x in wards), 3)

    tag = f"{split}_{method}_seed{seed}"
    (res_dir / f"{tag}.json").write_text(json.dumps(m, indent=2), encoding="utf-8")
    with open(log_dir / f"{tag}.jsonl", "w", encoding="utf-8") as f:
        for c, d in zip(cases, decisions):
            rec = d.model_dump(mode="json")
            rec.update(ktas_expert=c.ktas_expert, ktas_nurse=c.ktas_nurse, ed_diagnosis=c.ed_diagnosis)
            f.write(json.dumps(rec) + "\n")
    print(f"== {tag}: acc={m['accuracy']} qwk={m['qwk']} under={m['under_triage']} "
          f"under_high={m['under_triage_high_acuity']} tokens/case={m['tokens_per_case']}")
    return m


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", choices=["dev", "test"], default="dev")
    ap.add_argument("--methods", nargs="+", default=["all"])
    ap.add_argument("--seeds", type=int, default=1)
    ap.add_argument("--providers", default="experiments/configs/providers_groq.yaml")
    ap.add_argument("--data", default="data/ktas/ktas_moon2019.csv")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()
    load_env()

    methods = list(ALL) if "all" in args.methods else args.methods
    dev, test = stratified_split(load_ktas_csv(ROOT / args.data), dev_per_level=5, test_per_level=30, seed=0)
    cases = (dev if args.split == "dev" else test)[: args.limit]
    pcfg = yaml.safe_load(open(ROOT / args.providers, encoding="utf-8"))["agents"]
    providers = {}
    if any(m in LLM_METHODS for m in methods):
        providers = {r: build_provider(pcfg[r], r) for r in ("triage_nurse", "patient")}

    res_dir, log_dir = ROOT / "results" / "triage", ROOT / "logs" / "triage"
    res_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    # nurse_floor reuses each nurse run's trajectories, so it costs no extra LLM calls.
    want_floor = "nurse_floor" in methods
    run_list = [m for m in methods if m != "nurse_floor"]
    if want_floor and "nurse" not in run_list:
        run_list.append("nurse")
    for method in run_list:
        for seed in range(args.seeds if method in LLM_METHODS else 1):
            print(f"-- {args.split} {method} seed{seed} ({len(cases)} cases)")
            decisions = run_method(method, cases, providers)
            write_run(method, seed, decisions, cases, args.split, res_dir, log_dir)
            if method == "nurse" and want_floor:
                floored = [apply_rule_floor(d.model_copy(deep=True), c) for d, c in zip(decisions, cases)]
                for d in floored:
                    d.method = "nurse_floor"
                write_run("nurse_floor", seed, floored, cases, args.split, res_dir, log_dir)

    # FIFO reference: MedAgentSim has no triage, so patients are seen in arrival order.
    y_true = [c.ktas_expert for c in cases]
    fifo = [simulate_ward([y_true[i] for i in idx], [3] * len(idx), policy="fifo", seed=w)
            for w, idx in enumerate(ward_samples(len(cases)))]
    (res_dir / f"{args.split}_ward_fifo.json").write_text(
        json.dumps({k: round(st.mean(x[k] for x in fifo), 3) for k in fifo[0]}, indent=2), encoding="utf-8")
    summarize(res_dir, args.split)


def summarize(res_dir: Path, split: str) -> None:
    runs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(res_dir.glob(f"{split}_*_seed*.json"))]
    keys = ["accuracy", "qwk", "under_triage", "under_triage_high_acuity", "over_triage", "failures",
            "tokens_per_case", "calls_per_case", "latency_s_per_case",
            "ward_mean_wait_high_acuity_min", "ward_target_violation_rate_high_acuity"]
    by: dict[str, list[dict]] = {}
    for r in runs:
        by.setdefault(r["method"], []).append(r)
    with open(res_dir / f"{split}_summary.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["method", "runs", "n"] + [f"{k}_{s}" for k in keys for s in ("mean", "std")])
        for method, rs in by.items():
            row = [method, len(rs), rs[0]["n"]]
            for k in keys:
                vals = [r.get(k, 0) for r in rs]
                row += [round(st.mean(vals), 4), round(st.stdev(vals), 4) if len(vals) > 1 else 0.0]
            w.writerow(row)
    print(f"-> {res_dir / f'{split}_summary.csv'}")


if __name__ == "__main__":
    main()
