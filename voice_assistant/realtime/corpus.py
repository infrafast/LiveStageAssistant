"""Provider-neutral helpers for Realtime MCP command-corpus validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CorpusExpect:
    min_tool_calls: int = 1
    max_tool_calls: int | None = None
    required_tools: tuple[str, ...] = ()
    forbidden_tools: tuple[str, ...] = ()
    require_answer: bool = True
    max_latency_ms: float | None = None
    max_cost_usd: float | None = None


@dataclass(frozen=True)
class CorpusCase:
    case_id: str
    text: str
    expect: CorpusExpect = field(default_factory=CorpusExpect)


@dataclass(frozen=True)
class CorpusAssertion:
    ok: bool
    failures: tuple[str, ...] = ()


def parse_case(raw: dict[str, Any]) -> CorpusCase:
    if not isinstance(raw, dict):
        raise ValueError("each corpus case must be an object")
    case_id = str(raw.get("id") or "").strip()
    text = str(raw.get("text") or "").strip()
    if not case_id:
        raise ValueError("corpus case id is required")
    if not text:
        raise ValueError(f"corpus case {case_id!r} text is required")

    expect_raw = raw.get("expect") or {}
    if not isinstance(expect_raw, dict):
        raise ValueError(f"corpus case {case_id!r} expect must be an object")

    min_tool_calls = int(expect_raw.get("min_tool_calls", 1))
    max_raw = expect_raw.get("max_tool_calls")
    max_tool_calls = None if max_raw is None else int(max_raw)
    if min_tool_calls < 0:
        raise ValueError(f"corpus case {case_id!r} min_tool_calls must be >= 0")
    if max_tool_calls is not None and max_tool_calls < min_tool_calls:
        raise ValueError(f"corpus case {case_id!r} max_tool_calls must be >= min_tool_calls")

    required_tools = tuple(str(v).strip() for v in (expect_raw.get("required_tools") or []) if str(v).strip())
    forbidden_tools = tuple(str(v).strip() for v in (expect_raw.get("forbidden_tools") or []) if str(v).strip())

    latency_raw = expect_raw.get("max_latency_ms")
    cost_raw = expect_raw.get("max_cost_usd")
    return CorpusCase(
        case_id=case_id,
        text=text,
        expect=CorpusExpect(
            min_tool_calls=min_tool_calls,
            max_tool_calls=max_tool_calls,
            required_tools=required_tools,
            forbidden_tools=forbidden_tools,
            require_answer=bool(expect_raw.get("require_answer", True)),
            max_latency_ms=None if latency_raw is None else float(latency_raw),
            max_cost_usd=None if cost_raw is None else float(cost_raw),
        ),
    )


def evaluate_case(case: CorpusCase, result: dict[str, Any]) -> CorpusAssertion:
    failures: list[str] = []
    usage = result.get("usage") or {}
    tool_entries = usage.get("tools") or []
    tool_names = [str(item.get("name") or "") for item in tool_entries if isinstance(item, dict)]
    count = int(usage.get("tool_calls") or len(tool_names))

    if count < case.expect.min_tool_calls:
        failures.append(f"tool_calls {count} < minimum {case.expect.min_tool_calls}")
    if case.expect.max_tool_calls is not None and count > case.expect.max_tool_calls:
        failures.append(f"tool_calls {count} > maximum {case.expect.max_tool_calls}")

    for name in case.expect.required_tools:
        if name not in tool_names:
            failures.append(f"required tool not called: {name}")
    for name in case.expect.forbidden_tools:
        if name in tool_names:
            failures.append(f"forbidden tool called: {name}")

    answer = str(result.get("answer") or "").strip()
    if case.expect.require_answer and not answer:
        failures.append("missing final answer")

    latency = (result.get("latency_ms") or {}).get("turn_to_final_response")
    if case.expect.max_latency_ms is not None:
        if latency is None:
            failures.append("missing turn latency")
        elif float(latency) > case.expect.max_latency_ms:
            failures.append(f"latency {float(latency):.3f} ms > maximum {case.expect.max_latency_ms:.3f} ms")

    cost = (result.get("cost_usd") or {}).get("measured_provider_usage")
    if case.expect.max_cost_usd is not None:
        if cost is None:
            failures.append("missing provider cost")
        elif float(cost) > case.expect.max_cost_usd:
            failures.append(f"cost {float(cost):.8f} > maximum {case.expect.max_cost_usd:.8f}")

    return CorpusAssertion(ok=not failures, failures=tuple(failures))
