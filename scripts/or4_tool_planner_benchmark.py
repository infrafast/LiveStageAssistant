#!/usr/bin/env python3
"""Standalone Ollama tool-calling benchmark for OR4.

This script deliberately does not import or start LiveStageAssistant, mcp_use,
audio, Whisper, Piper or real MCP servers. It measures only local Ollama tool
planning so candidate models can be accepted/rejected before integration.

The benchmark mirrors LSA's server-level routing contract: each routed case sees
only the tools and prompt for that MCP server. A one-tool baseline isolates raw
Ollama tool-call capability from multi-tool selection.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_TIMEOUT = 12.0


TOOLS = {
    "resolve_target": {
        "type": "function",
        "function": {
            "name": "resolve_target",
            "description": "Resolve a user-provided mixer target name before changing it.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    },
    "mute_target": {
        "type": "function",
        "function": {
            "name": "mute_target",
            "description": "Set mute state on an already resolved mixer target.",
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["channel", "bus"]},
                    "index": {"type": "integer"},
                    "mute": {"type": "boolean"},
                },
                "required": ["kind", "index", "mute"],
            },
        },
    },
    "qlc_get_state": {
        "type": "function",
        "function": {
            "name": "qlc_get_state",
            "description": "Get QLC connection state.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    "qlc_list_widgets": {
        "type": "function",
        "function": {
            "name": "qlc_list_widgets",
            "description": "List QLC widget captions, optionally filtered by query.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
            },
        },
    },
    "qlc_button_press": {
        "type": "function",
        "function": {
            "name": "qlc_button_press",
            "description": "Press one exact QLC widget caption.",
            "parameters": {
                "type": "object",
                "properties": {"caption": {"type": "string"}},
                "required": ["caption"],
            },
        },
    },
}


BASELINE_PROMPT = (
    "You are a strict tool planner. Use the provided tool silently. "
    "Resolve the user-provided target name exactly; do not answer with JSON text."
)

MIXER_PROMPT = (
    "You are a strict mixer tool planner. Use tools silently. "
    "Never invent target indexes. Resolve a named target before acting on it. "
    "French 'coupe' means mute=true and 'remets' means mute=false. "
    "After a resolver result gives kind/index, use those exact values."
)

QLC_PROMPT = (
    "You are a strict QLC tool planner. Use tools silently. "
    "Call qlc_get_state first. If state is ready and the user asks only to list "
    "controls, call qlc_list_widgets. Before pressing a control, list/verify its "
    "exact caption first."
)


@dataclass
class Case:
    name: str
    user: str
    system_prompt: str
    tool_names: list[str]
    expected_sequence: list[str]


CASES = [
    Case(
        "single-resolve",
        "résous Claude",
        BASELINE_PROMPT,
        ["resolve_target"],
        ["resolve_target"],
    ),
    Case(
        "mixer-mute",
        "coupe Claude",
        MIXER_PROMPT,
        ["resolve_target", "mute_target"],
        ["resolve_target", "mute_target"],
    ),
    Case(
        "qlc-list",
        "qlc liste tous les contrôles",
        QLC_PROMPT,
        ["qlc_get_state", "qlc_list_widgets", "qlc_button_press"],
        ["qlc_get_state", "qlc_list_widgets"],
    ),
]


def http_json(url: str, payload: dict[str, Any] | None, timeout: float) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="GET" if payload is None else "POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Ollama unavailable: {exc.reason}") from exc
    result = json.loads(raw.decode("utf-8"))
    if not isinstance(result, dict):
        raise RuntimeError("Ollama returned non-object JSON")
    return result


def installed_models(base_url: str) -> set[str]:
    result = http_json(f"{base_url.rstrip('/')}/api/tags", None, 5.0)
    names: set[str] = set()
    for model in result.get("models") or []:
        if isinstance(model, dict):
            name = str(model.get("name") or "").strip()
            if name:
                names.add(name)
    return names


def duration_s(response: dict[str, Any], key: str) -> float:
    value = response.get(key)
    return float(value) / 1_000_000_000.0 if isinstance(value, (int, float)) else 0.0


def normalize_arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def synthetic_tool_result(name: str, arguments: dict[str, Any]) -> str:
    if name == "resolve_target":
        return json.dumps({"name": "Claude", "kind": "bus", "index": 3, "safeToWrite": True})
    if name == "mute_target":
        return json.dumps(
            {
                "ok": True,
                "kind": arguments.get("kind"),
                "index": arguments.get("index"),
                "mute": arguments.get("mute"),
            }
        )
    if name == "qlc_get_state":
        return json.dumps({"state": "ready"})
    if name == "qlc_list_widgets":
        return json.dumps(["Blue Speed", "Red Flash", "Dimmer Up"])
    if name == "qlc_button_press":
        return json.dumps({"ok": True, "caption": arguments.get("caption")})
    return json.dumps({"ok": False, "error": "unknown synthetic tool"})


def validate_call(case: Case, step: int, name: str, arguments: dict[str, Any]) -> tuple[bool, str]:
    expected = case.expected_sequence[step]
    if name != expected:
        return False, f"expected tool {expected}, got {name or '<none>'}"
    if name == "resolve_target" and str(arguments.get("name", "")).lower() != "claude":
        return False, f"resolve_target expected name=Claude, got {arguments}"
    if name == "mute_target":
        if (
            arguments.get("kind") != "bus"
            or arguments.get("index") != 3
            or arguments.get("mute") is not True
        ):
            return False, f"mute_target expected bus/3/true, got {arguments}"
    return True, "ok"


def describe_calls(calls: list[Any]) -> list[tuple[str, dict[str, Any]]]:
    described: list[tuple[str, dict[str, Any]]] = []
    for raw_call in calls:
        call = raw_call if isinstance(raw_call, dict) else {}
        function = call.get("function") if isinstance(call.get("function"), dict) else {}
        described.append(
            (
                str(function.get("name") or ""),
                normalize_arguments(function.get("arguments")),
            )
        )
    return described


def run_case(
    base_url: str,
    model: str,
    case: Case,
    timeout: float,
    max_step_s: float,
) -> tuple[bool, float]:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": case.system_prompt},
        {"role": "user", "content": case.user},
    ]
    case_tools = [TOOLS[name] for name in case.tool_names]
    total_started = time.perf_counter()
    request_timeout = min(timeout, max_step_s + 2.0)

    print(f"  tools={','.join(case.tool_names)} request_timeout={request_timeout:.1f}s")

    for step, expected in enumerate(case.expected_sequence):
        payload = {
            "model": model,
            "messages": messages,
            "tools": case_tools,
            "stream": False,
            "think": False,
            "keep_alive": "10m",
            "options": {"temperature": 0, "num_ctx": 2048, "num_predict": 48},
        }
        started = time.perf_counter()
        try:
            response = http_json(
                f"{base_url.rstrip('/')}/api/chat",
                payload,
                request_timeout,
            )
        except Exception as exc:
            elapsed = time.perf_counter() - started
            print(f"  step {step + 1}: ERROR after {elapsed:.2f}s: {exc}")
            return False, time.perf_counter() - total_started

        elapsed = time.perf_counter() - started
        message = response.get("message") or {}
        calls = message.get("tool_calls") or []
        described = describe_calls(calls)
        print(
            f"  step {step + 1}: {elapsed:.2f}s "
            f"load={duration_s(response, 'load_duration'):.2f}s "
            f"prompt_eval={duration_s(response, 'prompt_eval_duration'):.2f}s/"
            f"{int(response.get('prompt_eval_count') or 0)}tok "
            f"eval={duration_s(response, 'eval_duration'):.2f}s/"
            f"{int(response.get('eval_count') or 0)}tok "
            f"tool_calls={len(calls)}"
        )
        for idx, (name, arguments) in enumerate(described, start=1):
            print(
                f"    call[{idx}]={name or '<none>'} "
                f"args={json.dumps(arguments, ensure_ascii=False, sort_keys=True)}"
            )

        content = str(message.get("content") or "")
        if content:
            print(f"    content={content[:200]!r}")

        if elapsed > max_step_s:
            print(f"    FAIL: step exceeded {max_step_s:.1f}s target")
            return False, time.perf_counter() - total_started

        if len(calls) != 1:
            print(
                f"    FAIL: expected exactly one sequential tool call ({expected}); "
                f"got {len(calls)}"
            )
            return False, time.perf_counter() - total_started

        name, arguments = described[0]
        valid, reason = validate_call(case, step, name, arguments)
        if not valid:
            print(f"    FAIL: {reason}")
            return False, time.perf_counter() - total_started

        messages.append(
            {
                "role": "assistant",
                "content": content,
                "tool_calls": calls,
            }
        )
        messages.append(
            {
                "role": "tool",
                "tool_name": name,
                "content": synthetic_tool_result(name, arguments),
            }
        )

    total = time.perf_counter() - total_started
    print(f"  PASS: {case.name} total={total:.2f}s")
    return True, total


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--models",
        default="llama3.2:3b",
        help="Comma-separated Ollama model names",
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--max-step-seconds", type=float, default=10.0)
    args = parser.parse_args()

    models = [item.strip() for item in args.models.split(",") if item.strip()]
    available = installed_models(args.base_url)
    print(f"Ollama: {args.base_url}")
    print(
        f"Acceptance: every required sequential tool call "
        f"<= {args.max_step_seconds:.1f}s and correct"
    )
    print()

    overall = True
    for model in models:
        print(f"MODEL {model}")
        if model not in available:
            print(f"  SKIP: model is not installed (run: ollama pull {model})")
            overall = False
            print()
            continue

        model_ok = True
        for case in CASES:
            print(f" CASE {case.name}: {case.user}")
            ok, _ = run_case(
                args.base_url,
                model,
                case,
                args.timeout,
                args.max_step_seconds,
            )
            model_ok = model_ok and ok

        print(f" RESULT {model}: {'PASS' if model_ok else 'FAIL'}")
        print()
        overall = overall and model_ok

    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
