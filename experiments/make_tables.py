"""Build the paper's LaTeX tables from results/ (no API calls).

    python experiments/make_tables.py

Writes results/tables/<name>.tex and paper/tables/<name>.tex for every result set that exists; missing
experiments (e.g. G2/G3 not run yet) are skipped with a message.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RES, OUT = ROOT / "results", ROOT / "results" / "tables"

TRIAGE_ROWS = [
    ("human", "Human nurse (KTAS\\_RN)"),
    ("rules", "B1 Rules"),
    ("single_llm", "B0 Single LLM"),
    ("nurse", "Triage Nurse (no floor)"),
    ("nurse_floor", "\\textbf{SafeWard G1} (nurse + floor)"),
]


def read_csv(p: Path) -> dict[str, dict]:
    with open(p, encoding="utf-8") as f:
        return {r.get("method") or r.get("config"): r for r in csv.DictReader(f)}


def cell(r: dict, k: str, pct=False, nd=3) -> str:
    v = float(r[f"{k}_mean"])
    s = float(r.get(f"{k}_std", 0) or 0)
    txt = f"{100*v:.1f}" if pct else f"{v:.{nd}f}"
    if s > 0:
        txt += f"$\\pm${100*s:.1f}" if pct else f"$\\pm${s:.{nd}f}"
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
        f"\\caption{{G1 triage on the KTAS test split ($n={n}$, {seeds} seed{'s' if seeds > 1 else ''}). "
        "UT = under-triage rate (\\%); UT$_{1\\text{-}2}$ restricted to KTAS 1--2. "
        "Wait = mean simulated wait of KTAS 1--2 patients (min).}",
        "\\label{tab:triage}",
        "\\setlength{\\tabcolsep}{3pt}",
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
        f"FIFO queue (MedAgentSim, no triage) & -- & -- & -- & -- & -- & {fifo['mean_wait_high_acuity_min']:.1f} \\\\",
        "\\bottomrule\\end{tabular}\\end{table}",
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


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    jobs = {
        "triage": triage_table,
        "safety": lambda: generic_table("safety", "safety", "G2 safety on the adversarial test set."),
        "memory": lambda: generic_table("memory", "memory", "G3 memory poisoning (clean vs poisoned)."),
        "consultation": lambda: generic_table("consultation", "consultation", "Consultation accuracy on MedQA."),
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
