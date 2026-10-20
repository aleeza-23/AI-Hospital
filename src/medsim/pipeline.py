"""Experiment orchestration: config -> runner -> episodes.jsonl + metrics.json."""
from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

from medsim.config import RunConfig
from medsim.contracts import EpisodeResult, Role
from medsim.data import load_cases
from medsim.env import EpisodeRunner
from medsim.eval import Judge, compute_metrics
from medsim.providers import build_provider
from medsim.tools import ImageReader

log = logging.getLogger(__name__)

ROLE_COLORS = {Role.DOCTOR: "\033[94m", Role.PATIENT: "\033[92m", Role.MEASUREMENT: "\033[93m"}


def print_message(role: Role, text: str) -> None:
    print(f"{ROLE_COLORS.get(role, '')}{role.value.upper():>11}\033[0m: {text}")


def build_runner(cfg: RunConfig, human_role: Optional[str] = None, verbose: bool = False) -> EpisodeRunner:
    def provider(role: str):
        if role == human_role:
            return build_provider({"provider": "human"}, role)
        pcfg = cfg.provider_cfg(role)
        if pcfg is None:
            raise ValueError(f"Config has no provider for role {role!r}")
        return build_provider(pcfg, role)

    judge = (
        Judge(method="exact") if cfg.judge_method == "exact" else Judge(provider("judge"), method="llm")
    )
    img_cfg = cfg.provider_cfg("image_reader")
    return EpisodeRunner(
        doctor=provider("doctor"),
        patient=provider("patient"),
        measurement=provider("measurement"),
        judge=judge,
        image_reader=ImageReader(build_provider(img_cfg, "image_reader") if img_cfg else None),
        max_turns=cfg.max_turns,
        on_message=print_message if (verbose or human_role) else None,
    )


def run_experiment(
    cfg: RunConfig,
    human_role: Optional[str] = None,
    verbose: bool = False,
    resume_dir: Optional[str] = None,
) -> Path:
    cases = load_cases(cfg.dataset, limit=cfg.num_cases)
    if resume_dir:
        out = Path(resume_dir)
    else:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        out = Path(cfg.output_dir) / f"{cfg.run_name}-{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "config.yaml").write_text(yaml.safe_dump(cfg.model_dump(), sort_keys=False), encoding="utf-8")

    episodes_path = out / "episodes.jsonl"
    done = {r.case_id for r in read_results(episodes_path)} if episodes_path.exists() else set()
    todo = [c for c in cases if c.id not in done]
    log.info("Run %s: %d cases (%d already done) -> %s", cfg.run_name, len(cases), len(done), out)

    runner = build_runner(cfg, human_role, verbose)
    lock = threading.Lock()

    def record(res: EpisodeResult) -> None:
        with lock, open(episodes_path, "a", encoding="utf-8") as f:
            f.write(res.model_dump_json() + "\n")
        mark = "CORRECT" if res.correct else "WRONG  "
        print(f"[{res.case_id}] {mark} pred={res.final_diagnosis!r} | truth={res.correct_diagnosis!r} "
              f"| turns={res.num_turns} tests={res.num_tests} {res.status.value}")

    workers = 1 if (human_role or verbose) else max(1, cfg.workers)
    if workers == 1:
        for case in todo:
            if verbose or human_role:
                print(f"\n===== {case.id} | objective: {case.objective}")
            record(runner.run(case))
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for fut in as_completed([pool.submit(runner.run, c) for c in todo]):
                record(fut.result())

    write_metrics(cfg.run_name, out)
    return out


def read_results(path: str | Path) -> list[EpisodeResult]:
    with open(path, encoding="utf-8") as f:
        return [EpisodeResult.model_validate_json(line) for line in f if line.strip()]


def write_metrics(run_name: str, run_dir: Path) -> dict:
    results = read_results(run_dir / "episodes.jsonl")
    metrics = compute_metrics(run_name, results).model_dump()
    (run_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    return metrics
