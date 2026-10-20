"""Run configuration (YAML -> validated RunConfig)."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field


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
