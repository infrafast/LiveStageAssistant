"""Safety policy for RV2C native-first MCP fallback.

This module is intentionally domain-neutral. It does not know mixer or lighting
semantics. It only reasons about whether a native MCP failure may be replayed
through the existing LSA bridge without risking duplicate external writes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AutoFallbackDecision:
    fallback: bool
    classification: str
    reason: str


def tool_read_only_from_metadata(tool: dict[str, Any] | None) -> bool | None:
    """Return MCP readOnlyHint when available, otherwise None."""
    if not isinstance(tool, dict):
        return None
    annotations = tool.get("annotations") or {}
    if not isinstance(annotations, dict):
        return None
    value = annotations.get("readOnlyHint")
    if isinstance(value, bool):
        return value
    return None


def classify_auto_fallback(
    *,
    dispatched: bool,
    read_only: bool | None,
    explicit_not_executed: bool = False,
) -> AutoFallbackDecision:
    """Classify whether auto may replay the logical request through the bridge.

    Rules:
    - Before native tool dispatch, fallback is always safe because no external
      tool action has been attempted.
    - After dispatch, read-only calls may be replayed because duplicate reads do
      not mutate stage state.
    - A write/unknown call may be replayed only when the native/provider signal
      explicitly proves it was not executed.
    - Otherwise the outcome is ambiguous and fallback is suppressed.
    """
    if not dispatched:
        return AutoFallbackDecision(
            True,
            "pre_dispatch_definite_failure",
            "native failed before any tool dispatch",
        )
    if read_only is True:
        return AutoFallbackDecision(
            True,
            "post_dispatch_read_only_failure",
            "native read failed after dispatch; duplicate read is safe",
        )
    if explicit_not_executed:
        return AutoFallbackDecision(
            True,
            "post_dispatch_not_executed",
            "native failure explicitly proves the tool did not execute",
        )
    return AutoFallbackDecision(
        False,
        "ambiguous_mutation_or_unknown",
        "native tool may have executed; automatic cross-transport replay suppressed",
    )
