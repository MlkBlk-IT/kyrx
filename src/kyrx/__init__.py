"""KYRX — content-addressed coordination plane for LLM agent fleets.

Design rule: compute where it is deterministic, infer where it is ambiguous.
Artifacts are addressed by content, agents exchange handles instead of prose,
and state is produced by pure reducers rather than summarised by a model.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.3.0"
