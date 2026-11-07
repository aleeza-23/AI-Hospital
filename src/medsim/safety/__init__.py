"""G2 Safety: Safety Officer agent, openFDA tool, adversarial scenarios."""
from __future__ import annotations

from typing import Any, Optional

from medsim.safety.base import SafetyReviewer

__all__ = ["SafetyReviewer", "build_safety"]


def build_safety(name: str, providers: dict[str, Any], **kwargs: Any) -> Optional[SafetyReviewer]:
    """Factory used by the pipeline from config `features.safety`.

    none        -> no review (MedAgentSim, B2)
    self_check  -> the doctor model reviews its own plan (B3)
    officer     -> independent Safety Officer agent + red-flag rules + openFDA (SafeWard, G2)
    """
    if name in (None, "none"):
        return None
    raise NotImplementedError(f"safety={name!r} is not implemented yet (G2: src/medsim/safety/)")
