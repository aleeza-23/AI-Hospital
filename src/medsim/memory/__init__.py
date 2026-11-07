from typing import Any

from medsim.memory.buffer import MemoryBuffer, MemoryRecord, NullMemory

__all__ = ["MemoryBuffer", "MemoryRecord", "NullMemory", "build_memory"]


def build_memory(name: str, **kwargs: Any) -> MemoryBuffer:
    """Factory used by the pipeline from config `features.memory`.

    none    -> NullMemory (Phase 1 baseline, E1)
    ungated -> MedAgentSim-style memory, stores every correct case (E2, G3 baseline)
    gated   -> SafeWard verified memory gate (G3)
    """
    if name in (None, "none"):
        return NullMemory()
    raise NotImplementedError(f"memory={name!r} is not implemented yet (G3: src/medsim/memory/)")
