"""Run configuration (YAML -> validated RunConfig)."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field


class Features(BaseModel):
    """Each gap-specific component can be switched on and off for ablation (E5).
    All defaults = the Phase 1 baseline (E1)."""

    memory: str = "none"      # none | ungated (MedAgentSim) | gated (G3)
    safety: str = "none"      # none | self_check (B3) | officer (G2)
    cot: bool = False         # chain-of-thought doctor prompt (MedAgentSim)
    ask_plan: bool = False    # doctor writes a management plan (needed to measure unsafe actions)
    memory_k: int = 3


class RunConfig(BaseModel):
    run_name: str
    dataset: str = Field(description="Path to a unified Case JSONL file.")
    num_cases: Optional[int] = None
    max_turns: int = 20
    workers: int = 1
    output_dir: str = "runs"
    judge_method: str = "llm"  # llm | exact
    # role -> provider config, e.g. {provider: openai_compat, model: ..., base_url: ...}
    agents: dict[str, dict[str, Any]]
    features: Features = Field(default_factory=Features)

    def provider_cfg(self, role: str) -> Optional[dict[str, Any]]:
        return self.agents.get(role)


def load_config(path: str | Path) -> RunConfig:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return RunConfig.model_validate(raw)


def load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()
