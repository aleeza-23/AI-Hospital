from typing import Any

from medsim.memory.buffer import MemoryBuffer, MemoryRecord, NullMemory

__all__ = ["MemoryBuffer", "MemoryRecord", "NullMemory", "build_memory"]


def build_memory(name: str, **kwargs: Any) -> MemoryBuffer:
    """Factory used by the pipeline from config `features.memory`.

    none    -> NullMemory (Phase 1 baseline, E1)
    ungated -> MedAgentSim-style memory, stores every correct case (E2, G3 baseline)
    gated   -> SafeWard verified memory gate (G3)

    `k` comes from `features.memory_k`; `auto_retract_after` and `min_score` are
    available for dev tuning.
    """
    if name in (None, "none"):
        return NullMemory()
    # Imported lazily so `memory: none` runs do not build the index.
    from medsim.memory.stores import GatedMemory, UngatedMemory

    if name == "ungated":
        return UngatedMemory(**kwargs)
    if name == "gated":
        return GatedMemory(**kwargs)
    raise ValueError(f"Unknown memory {name!r} (expected none | ungated | gated)")
