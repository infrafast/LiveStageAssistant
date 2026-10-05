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
    failure_kind: str = "unknown"
    dispatched: bool = False
    read_only: bool | None = None


def tool_read_only_from_metadata(tool: dict[str, Any] | None) -> bool | None:
    """Return MCP readOnlyHint when available, otherwise None.

    AUTO mode must not infer mutability from a tool name. Only explicit MCP
    metadata is authoritative enough to allow post-dispatch replay of reads.
    """
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
    failure_kind: str = "unknown",
) -> AutoFallbackDecision:
    """Classify whether AUTO may replay a logical request through the bridge.

    Safety rules:
    - Before native tool dispatch, fallback is always safe. This covers auth,
      discovery, connection and timeout failures before an external action is
      attempted.
    - After dispatch, explicitly read-only calls may be replayed because a
      duplicate read does not mutate stage state.
    - A write/unknown call may be replayed only if a provider/server signal
      explicitly proves that execution did not happen.
    - Any other post-dispatch write/unknown failure is ambiguous. In particular,
      a timeout or connection loss after dispatch MUST NOT trigger STDIO replay.
    """
    kind = (failure_kind or "unknown").strip().lower().replace(" ", "_")

    if not dispatched:
        return AutoFallbackDecision(
            True,
            "pre_dispatch_definite_failure",
            f"native {kind} failure occurred before any tool dispatch",
            failure_kind=kind,
            dispatched=False,
            read_only=read_only,
        )
    if read_only is True:
        return AutoFallbackDecision(
            True,
            "post_dispatch_read_only_failure",
            f"native read hit a post-dispatch {kind} failure; duplicate read is safe",
            failure_kind=kind,
            dispatched=True,
            read_only=True,
        )
    if explicit_not_executed:
        return AutoFallbackDecision(
            True,
            "post_dispatch_not_executed",
            f"native {kind} failure explicitly proves the tool did not execute",
            failure_kind=kind,
            dispatched=True,
            read_only=read_only,
        )
    return AutoFallbackDecision(
        False,
        "ambiguous_mutation_or_unknown",
        f"native tool may have executed before {kind} failure; automatic cross-transport replay suppressed",
        failure_kind=kind,
        dispatched=True,
        read_only=read_only,
    )


def fault_matrix() -> tuple[tuple[str, AutoFallbackDecision], ...]:
    """Return the canonical RV2C safety matrix used by tests/field validation."""
    cases = (
        ("auth_before_dispatch", dict(dispatched=False, read_only=None, failure_kind="auth")),
        ("timeout_before_dispatch", dict(dispatched=False, read_only=None, failure_kind="timeout")),
        ("connection_before_dispatch", dict(dispatched=False, read_only=None, failure_kind="connection")),
        ("timeout_after_read_dispatch", dict(dispatched=True, read_only=True, failure_kind="timeout")),
        ("timeout_after_write_dispatch", dict(dispatched=True, read_only=False, failure_kind="timeout")),
        ("connection_after_write_dispatch", dict(dispatched=True, read_only=False, failure_kind="connection")),
        ("timeout_after_unknown_dispatch", dict(dispatched=True, read_only=None, failure_kind="timeout")),
        (
            "explicit_not_executed_write",
            dict(dispatched=True, read_only=False, explicit_not_executed=True, failure_kind="provider_rejected"),
        ),
    )
    return tuple((name, classify_auto_fallback(**kwargs)) for name, kwargs in cases)
