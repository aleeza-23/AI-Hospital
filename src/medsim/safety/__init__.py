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

    Extra kwargs reach SafetyOfficer, which is how the ablations are selected:
    `facts_only=True` hides the doctor's turns, and `use_rules` / `use_openfda`
    switch off a layer to measure its contribution.
    """
    if name in (None, "none"):
        return None
    # Imported lazily so `safety: none` runs need neither the rules nor the tool.
    from medsim.safety.officer import SafetyOfficer, SelfCheck

    if name == "self_check":
        return SelfCheck(providers["doctor"])
    if name == "officer":
        provider = providers.get("safety_officer")
        if provider is None:
            raise ValueError(
                "safety=officer needs a 'safety_officer' provider in the config's `agents` "
                "block: the independence of the reviewer is what G2 measures, so falling "
                "back to the doctor's own provider would invalidate the comparison. "
                "Add it (see experiments/configs/providers_groq.yaml) or use safety=self_check."
            )
        return SafetyOfficer(provider, **kwargs)
    raise ValueError(f"Unknown safety reviewer {name!r} (expected none | self_check | officer)")
