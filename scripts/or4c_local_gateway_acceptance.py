#!/usr/bin/env python3
"""OR4C live acceptance harness for the deterministic Local MCP gateway.

The harness is intentionally domain-neutral. Commands and expected outcomes
come from a JSON corpus. Read plans may execute automatically; write plans are
never executed unless both the corpus requests execution and --allow-writes is
provided explicitly.

Example:
    python scripts/or4c_local_gateway_acceptance.py \
      --env-file .env.offline \
      --corpus scripts/or4c_acceptance_corpus.example.json \
      --json-report /tmp/or4c-report.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import re
import statistics
import sys
import time
from typing import Any, Mapping

from dotenv import dotenv_values, load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from voice_assistant.classic_engine import _mcp_config
from voice_assistant.local_gateway_runtime import DeterministicGatewayOrchestrator


_LLM_PROCESS_RE = re.compile(
    r"(?:^|[ /])(ollama|llama-server|llama\.cpp|local-ai|localai)(?:$|[ /])",
    re.IGNORECASE,
)


def _load_corpus(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, Mapping):
        payload = payload.get("cases")
    if not isinstance(payload, list):
        raise ValueError("corpus must be a JSON array or an object containing a 'cases' array")
    cases: list[dict[str, Any]] = []
    for index, item in enumerate(payload, start=1):
        if not isinstance(item, Mapping):
            raise ValueError(f"case {index} must be an object")
        text = str(item.get("text") or "").strip()
        if not text:
            raise ValueError(f"case {index} has no text")
        expected = str(item.get("expected") or "ready").strip().lower()
        if expected not in {"ready", "clarification", "unrecognized", "multiple"}:
            raise ValueError(f"case {index} has unsupported expected status: {expected}")
        effect = item.get("effect")
        if effect is not None and str(effect) not in {"none", "read", "write"}:
            raise ValueError(f"case {index} has unsupported effect: {effect}")
        cases.append(dict(item, text=text, expected=expected))
    return cases


def _load_runtime_config(env_file: Path) -> tuple[dict[str, Any], str]:
    if not env_file.is_file():
        raise FileNotFoundError(env_file)
    load_dotenv(env_file, override=False)
    values = dict(dotenv_values(env_file))
    config = _mcp_config(values, env_file)
    if not isinstance(config, dict):
        raise RuntimeError(f"no MCP_CONFIG is configured in {env_file}")
    locale = str(values.get("STT_LANGUAGE") or "fr").strip() or "fr"
    return config, locale


def _llm_processes() -> dict[int, str]:
    result: dict[int, str] = {}
    proc = Path("/proc")
    if not proc.is_dir():
        return result
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(
                "utf-8", errors="replace"
            ).strip()
        except OSError:
            continue
        if raw and _LLM_PROCESS_RE.search(raw):
            result[int(entry.name)] = raw
    return result


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


async def _run_case(
    orchestrator: DeterministicGatewayOrchestrator,
    case: Mapping[str, Any],
    *,
    allow_writes: bool,
) -> dict[str, Any]:
    text = str(case["text"])
    started = time.perf_counter()
    candidates = orchestrator._routed_servers(text)

    context_value = case.get("context")
    context = dict(context_value) if isinstance(context_value, Mapping) else None

    results = await asyncio.gather(
        *(
            orchestrator._analyze(server, text, context=context)
            for server in candidates
        ),
        return_exceptions=True,
    )
    analysis_ms = (time.perf_counter() - started) * 1000.0

    claims: list[tuple[str, dict[str, Any]]] = []
    errors: list[str] = []
    for server, result in zip(candidates, results):
        if isinstance(result, Exception):
            errors.append(f"{server}: {result}")
            continue
        status = str(result.get("status") or "")
        if result.get("recognized") is True and status in {"ready", "clarification"}:
            claims.append((server, result))

    if not claims:
        actual = "unrecognized"
        selected_server = ""
        payload: dict[str, Any] = {}
    elif len(claims) > 1:
        actual = "multiple"
        selected_server = ""
        payload = {}
    else:
        selected_server, payload = claims[0]
        actual = str(payload.get("status") or "unrecognized")

    expected = str(case.get("expected") or "ready")
    passed = actual == expected
    reasons: list[str] = []
    if not passed:
        reasons.append(f"expected status={expected}, got {actual}")

    expected_server = str(case.get("server") or "").strip()
    if expected_server and selected_server != expected_server:
        passed = False
        reasons.append(
            f"expected server={expected_server}, got {selected_server or '<none>'}"
        )

    effect = str(payload.get("effect") or "none") if payload else "none"
    expected_effect = case.get("effect")
    if expected_effect is not None and effect != str(expected_effect):
        passed = False
        reasons.append(f"expected effect={expected_effect}, got {effect}")

    execute_requested = case.get("execute")
    if execute_requested is None:
        execute_requested = actual == "ready" and effect == "read"
    execute_requested = bool(execute_requested)

    execution_ms: float | None = None
    execute_result: dict[str, Any] | None = None
    skipped_write = False

    if actual == "ready" and execute_requested:
        token = str(payload.get("planToken") or "")
        if not token:
            passed = False
            reasons.append("ready result has no planToken")
        elif effect == "write" and not allow_writes:
            skipped_write = True
        else:
            execute_started = time.perf_counter()
            try:
                execute_result = await orchestrator._execute(selected_server, token)
            except Exception as exc:
                passed = False
                reasons.append(f"execute failed: {exc}")
            execution_ms = (time.perf_counter() - execute_started) * 1000.0
            if execute_result is not None and execute_result.get("ok") is not True:
                passed = False
                reasons.append(
                    f"execute returned ok={execute_result.get('ok')!r}: "
                    f"{execute_result.get('responseText') or execute_result.get('errorCode') or ''}"
                )

    total_ms = (time.perf_counter() - started) * 1000.0
    return {
        "text": text,
        "context": context,
        "expected": expected,
        "actual": actual,
        "server": selected_server or None,
        "effect": effect,
        "analysis_ms": round(analysis_ms, 3),
        "execution_ms": round(execution_ms, 3) if execution_ms is not None else None,
        "total_ms": round(total_ms, 3),
        "executed": execute_result is not None,
        "skipped_write": skipped_write,
        "response_text": (
            str(execute_result.get("responseText") or "")
            if execute_result is not None
            else str(payload.get("responseText") or "")
        ),
        "gateway_errors": errors,
        "passed": passed,
        "reasons": reasons,
    }


async def _run(args: argparse.Namespace) -> int:
    env_file = Path(args.env_file).expanduser().resolve()
    corpus_path = Path(args.corpus).expanduser().resolve()
    config, locale = _load_runtime_config(env_file)
    cases = _load_corpus(corpus_path)

    llm_before = _llm_processes()
    orchestrator = DeterministicGatewayOrchestrator(config, locale=locale)
    report: dict[str, Any] = {
        "protocol": "lsa-command-gateway/v1",
        "env_file": str(env_file),
        "corpus": str(corpus_path),
        "allow_writes": bool(args.allow_writes),
        "gateways": [],
        "cases": [],
        "llm_processes_before": llm_before,
    }

    try:
        gateways = await orchestrator.start()
        report["gateways"] = list(gateways)
        if not gateways:
            print("FAIL: no compatible deterministic Local gateway discovered", flush=True)
            report["passed"] = False
            return _finish(report, args, exit_code=2)

        print(
            "Gateways: " + ", ".join(gateways)
            + f" | protocol=lsa-command-gateway/v1 | locale={locale}",
            flush=True,
        )

        for index, case in enumerate(cases, start=1):
            result = await _run_case(
                orchestrator,
                case,
                allow_writes=bool(args.allow_writes),
            )
            report["cases"].append(result)
            marker = "PASS" if result["passed"] else "FAIL"
            execution = (
                " write-skipped"
                if result["skipped_write"]
                else (" executed" if result["executed"] else "")
            )
            print(
                f"[{index:02d}] {marker} {result['actual']}/{result['effect']}"
                f" server={result['server'] or '-'}"
                f" analysis={result['analysis_ms']:.1f}ms"
                f" total={result['total_ms']:.1f}ms{execution}"
                f" :: {result['text']}",
                flush=True,
            )
            for reason in result["reasons"]:
                print(f"     reason: {reason}", flush=True)
    finally:
        await orchestrator.close()

    llm_after = _llm_processes()
    new_llm = {
        pid: command for pid, command in llm_after.items() if pid not in llm_before
    }
    report["llm_processes_after"] = llm_after
    report["new_llm_processes"] = new_llm

    analysis_values = [
        float(item["analysis_ms"]) for item in report["cases"] if item.get("analysis_ms") is not None
    ]
    total_values = [
        float(item["total_ms"]) for item in report["cases"] if item.get("total_ms") is not None
    ]
    report["latency"] = {
        "analysis_p50_ms": _percentile(analysis_values, 0.50),
        "analysis_p95_ms": _percentile(analysis_values, 0.95),
        "total_p50_ms": _percentile(total_values, 0.50),
        "total_p95_ms": _percentile(total_values, 0.95),
        "analysis_mean_ms": statistics.fmean(analysis_values) if analysis_values else None,
    }

    all_cases_pass = all(bool(item.get("passed")) for item in report["cases"])
    report["passed"] = all_cases_pass and not new_llm

    if new_llm:
        print("FAIL: a new LLM/inference process appeared during the run:", flush=True)
        for pid, command in new_llm.items():
            print(f"  pid={pid} {command}", flush=True)

    latency = report["latency"]
    print(
        "Latency summary: "
        f"analysis p50={latency['analysis_p50_ms'] or 0:.1f}ms "
        f"p95={latency['analysis_p95_ms'] or 0:.1f}ms | "
        f"total p50={latency['total_p50_ms'] or 0:.1f}ms "
        f"p95={latency['total_p95_ms'] or 0:.1f}ms",
        flush=True,
    )
    print(
        "RESULT: " + ("PASS" if report["passed"] else "FAIL"),
        flush=True,
    )
    return _finish(report, args, exit_code=0 if report["passed"] else 1)


def _finish(report: Mapping[str, Any], args: argparse.Namespace, *, exit_code: int) -> int:
    if args.json_report:
        path = Path(args.json_report).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"JSON report: {path}", flush=True)
    return exit_code


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        default=".env.offline",
        help="LSA env profile containing MCP_CONFIG (default: .env.offline)",
    )
    parser.add_argument(
        "--corpus",
        required=True,
        help="JSON acceptance corpus; domain commands live here, not in LSA code",
    )
    parser.add_argument(
        "--allow-writes",
        action="store_true",
        help="Permit corpus cases with execute=true and effect=write to execute live actions",
    )
    parser.add_argument(
        "--json-report",
        default="",
        help="Optional path for the machine-readable acceptance report",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
