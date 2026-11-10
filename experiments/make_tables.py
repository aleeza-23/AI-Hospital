"""Build the paper's LaTeX tables from results/ (no API calls).

    python experiments/make_tables.py

Writes results/tables/<name>.tex and paper/tables/<name>.tex for every result set that exists; missing
experiments (e.g. G2/G3 not run yet) are skipped with a message.
"""
from __future__ import annotations

import csv
import json
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RES, OUT = ROOT / "results", ROOT / "results" / "tables"

TRIAGE_ROWS = [
    ("human", "Human nurse"),
    ("rules", "B1 Rules"),
    ("single_llm", "B0 Single LLM"),
    ("nurse", "Nurse, no floor"),
    ("nurse_floor", "\\textbf{Nurse + floor}"),
]


def read_csv(p: Path) -> dict[str, dict]:
    with open(p, encoding="utf-8") as f:
        return {r.get("method") or r.get("config"): r for r in csv.DictReader(f)}


def pct1(v: float) -> str:
    """Percent to 1 decimal, rounding halves up (summary.csv values are already rounded to 4 places)."""
    return str((Decimal(str(v)) * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def cell(r: dict, k: str, pct=False, nd=3) -> str:
    v = float(r[f"{k}_mean"])
    s = float(r.get(f"{k}_std", 0) or 0)
    txt = pct1(v) if pct else f"{v:.{nd}f}"
    if s > 0:
        txt += f"$\\pm${pct1(s)}" if pct else f"$\\pm${s:.{nd}f}"
    return txt


def triage_table() -> str | None:
    p = RES / "triage" / "test_summary.csv"
    if not p.exists():
        return None
    rows = read_csv(p)
    fifo = json.loads((RES / "triage" / "test_ward_fifo.json").read_text(encoding="utf-8"))
    n = next(iter(rows.values()))["n"]
    seeds = max(int(r["runs"]) for r in rows.values())
    lines = [
        "\\begin{table}[t]\\centering\\small",
        f"\\caption{{G1 triage on the KTAS test split ($n={n}$). LLM methods show the mean$\\pm$SD of {seeds} runs; "
        "the human labels and B1 are deterministic. UT = under-triage rate (\\%); UT$_{1\\text{-}2}$ restricted to "
        "KTAS 1--2. Wait = mean simulated wait of KTAS 1--2 patients (min).}",
        "\\label{tab:triage}",
        "\\setlength{\\tabcolsep}{2.5pt}",
        "\\resizebox{\\columnwidth}{!}{%",
        "\\begin{tabular}{lcccccc}\\toprule",
        "Method & Acc & QWK & UT & UT$_{1\\text{-}2}$ & Tok/case & Wait \\\\ \\midrule",
    ]
    for key, label in TRIAGE_ROWS:
        if key not in rows:
            continue
        r = rows[key]
        tok = float(r["tokens_per_case_mean"])
        lines.append(" & ".join([
            label, cell(r, "accuracy", pct=True), cell(r, "qwk"), cell(r, "under_triage", pct=True),
            cell(r, "under_triage_high_acuity", pct=True), f"{tok:.0f}" if tok else "--",
            f"{float(r['ward_mean_wait_high_acuity_min_mean']):.1f}",
        ]) + " \\\\")
    lines += [
        "\\midrule",
        f"Arrival order & -- & -- & -- & -- & -- & {fifo['mean_wait_high_acuity_min']:.1f} \\\\",
        "\\bottomrule\\end{tabular}}\\end{table}",
    ]
    return "\n".join(lines) + "\n"


def generic_table(sub: str, name: str, caption: str) -> str | None:
    """Fallback for G2/G3/consultation: dump summary.csv mean columns as a table."""
    p = RES / sub / "summary.csv"
    if not p.exists():
        return None
    with open(p, encoding="utf-8") as f:
        rs = list(csv.DictReader(f))
    if not rs:
        return None
    first = next(iter(rs[0]))
    cols = [c for c in rs[0] if c.endswith("_mean")][:6]
    head = " & ".join([first] + [c[:-5].replace("_", "\\_") for c in cols])
    body = [" & ".join([r[first].replace("_", "\\_")] + [f"{float(r[c]):.3f}" for c in cols]) + " \\\\" for r in rs]
    return "\n".join([
        "\\begin{table}[t]\\centering\\small", f"\\caption{{{caption}}}", f"\\label{{tab:{name}}}",
        "\\begin{tabular}{l" + "c" * len(cols) + "}\\toprule", head + " \\\\ \\midrule", *body,
        "\\bottomrule\\end{tabular}\\end{table}",
    ]) + "\n"


def safety_table() -> str | None:
    rows = [("safety_b2", "B2 No review"), ("safety_b3", "B3 Self-review"), ("safety_safeward", "\\textbf{SafeWard} Officer")]
    paths = {k: RES / "safety" / f"{k}_test_seed0.json" for k, _ in rows}
    if not all(p.exists() for p in paths.values()):
        return None
    data = {k: json.loads(p.read_text(encoding="utf-8")) for k, p in paths.items()}
    n, n_norm = data["safety_b2"]["n"], data["safety_b2"]["n_normal"]
    lines = [
        "\\begin{table}[t]\\centering\\small",
        f"\\caption{{G2 safety review on {n} adversarial test scenarios and {n_norm} normal cases (1 seed). "
        "Rates are \\%; brackets give the 95\\% Wilson interval. FP = false-premise compliance, "
        "HRF = unsafe plans on hidden-red-flag scenarios, Dx = diagnostic accuracy on normal cases.}",
        "\\label{tab:safety}",
        "\\setlength{\\tabcolsep}{2.5pt}",
        "\\begin{tabular}{lccccc}\\toprule",
        "Config & Unsafe & FP & HRF & Dx & Tok/case \\\\ \\midrule",
    ]
    for key, label in rows:
        r = data[key]
        lo, hi = r["unsafe_action_rate_ci95"]
        lines.append(" & ".join([
            label, f"{100*r['unsafe_action_rate']:.1f} [{100*lo:.0f}, {100*hi:.0f}]",
            f"{100*r['compliance_rate_false_premise']:.0f}", f"{100*r['unsafe_action_rate_hidden_red_flag']:.0f}",
            f"{100*r['diagnostic_accuracy_normal']:.0f}", f"{r['tokens_per_case']:.0f}",
        ]) + " \\\\")
    lines.append("\\bottomrule\\end{tabular}\\end{table}")
    return "\n".join(lines) + "\n"


def memory_table() -> str | None:
    arms = [("ungated", "clean"), ("ungated", "poisoned"), ("gated", "clean"), ("gated", "poisoned")]
    paths = {a: RES / "memory" / f"{a[0]}_{a[1]}_seed0.json" for a in arms}
    if not all(p.exists() for p in paths.values()):
        return None
    d = {a: json.loads(p.read_text(encoding="utf-8")) for a, p in paths.items()}
    n = d[arms[0]]["n"]
    lines = [
        "\\begin{table}[t]\\centering\\small",
        f"\\caption{{G3 memory poisoning on {n} paired cases (1 seed). Accuracy is \\% with the 95\\% Wilson "
        "interval. Adm. = wrong lessons written to memory (of 10 submitted); Retr. = cases whose "
        "context contained a wrong lesson.}",
        "\\label{tab:memory}",
        "\\setlength{\\tabcolsep}{3pt}",
        "\\begin{tabular}{llcccc}\\toprule",
        "Memory & Run & Accuracy & Adm. & Retr. & Tok/case \\\\ \\midrule",
    ]
    for a in arms:
        r = d[a]
        lo, hi = r["accuracy_ci95"]
        admitted = str(r["lessons_admitted"]) if a[1] == "poisoned" else "--"
        lines.append(" & ".join([
            a[0].capitalize(), a[1], f"{100*r['accuracy']:.1f} [{100*lo:.0f}, {100*hi:.0f}]", admitted,
            str(r["cases_retrieving_a_wrong_lesson"]) if a[1] == "poisoned" else "--", f"{r['tokens_per_case']:.0f}",
        ]) + " \\\\")
    lines.append("\\bottomrule\\end{tabular}\\end{table}")
    return "\n".join(lines) + "\n"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    jobs = {
        "triage": triage_table,
        "safety": safety_table,
        "memory": memory_table,
        # consultation: tab:base in paper/main.tex is written by hand (it mixes reported and measured rows)
    }
    for name, fn in jobs.items():
        tex = fn()
        if tex is None:
            print(f"skip {name}: no results yet")
            continue
        for d in (OUT, ROOT / "paper" / "tables"):  # paper/ copy so Overleaf compiles standalone
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{name}.tex").write_text(tex, encoding="utf-8")
        print(f"wrote results/tables/{name}.tex (+ paper/tables/)")


if __name__ == "__main__":
    main()
