from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "or4c_acceptance_harness",
    ROOT / "scripts" / "or4c_local_gateway_acceptance.py",
)
assert SPEC is not None and SPEC.loader is not None
harness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(harness)


def test_corpus_loader_accepts_wrapped_cases_and_rejects_missing_text(tmp_path: Path):
    path = tmp_path / "corpus.json"
    path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "text": "status",
                        "expected": "ready",
                        "effect": "read",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    cases = harness._load_corpus(path)
    assert cases[0]["text"] == "status"
    assert cases[0]["expected"] == "ready"

    path.write_text(json.dumps([{"expected": "ready"}]), encoding="utf-8")
    with pytest.raises(ValueError, match="has no text"):
        harness._load_corpus(path)


def test_percentile_is_stable_for_small_live_corpora():
    assert harness._percentile([], 0.95) is None
    assert harness._percentile([12.0], 0.95) == 12.0
    assert harness._percentile([10.0, 20.0], 0.50) == 15.0
    assert harness._percentile([10.0, 20.0], 0.95) == pytest.approx(19.5)


def test_llm_process_matcher_is_narrow():
    assert harness._LLM_PROCESS_RE.search("/usr/bin/ollama serve")
    assert harness._LLM_PROCESS_RE.search("/opt/bin/llama-server -m model.gguf")
    assert harness._LLM_PROCESS_RE.search("/usr/local/bin/localai run")
    assert not harness._LLM_PROCESS_RE.search("python voice_assistant/runtime.py")
    assert not harness._LLM_PROCESS_RE.search("node XMSeries-MCP/dist/index.js")


class FakeOrchestrator:
    def __init__(self, *, effect="read"):
        self.effect = effect
        self.executions = []
        self.contexts = []

    def _routed_servers(self, _text):
        return ("gateway",)

    async def _analyze(self, server, text, *, context=None):
        self.contexts.append(context)
        return {
            "protocol": "lsa-command-gateway/v1",
            "recognized": True,
            "status": "ready",
            "effect": self.effect,
            "planToken": f"{server}:{text}",
        }

    async def _execute(self, server, token):
        self.executions.append((server, token))
        return {
            "protocol": "lsa-command-gateway/v1",
            "ok": True,
            "responseText": "ok",
        }


@pytest.mark.asyncio
async def test_read_executes_but_write_requires_explicit_allow_flag():
    read = FakeOrchestrator(effect="read")
    result = await harness._run_case(
        read,
        {"text": "safe read", "expected": "ready", "effect": "read"},
        allow_writes=False,
    )
    assert result["passed"] is True
    assert result["executed"] is True
    assert len(read.executions) == 1

    write = FakeOrchestrator(effect="write")
    result = await harness._run_case(
        write,
        {
            "text": "live write",
            "expected": "ready",
            "effect": "write",
            "execute": True,
        },
        allow_writes=False,
    )
    assert result["passed"] is True
    assert result["executed"] is False
    assert result["skipped_write"] is True
    assert write.executions == []

    allowed = FakeOrchestrator(effect="write")
    result = await harness._run_case(
        allowed,
        {
            "text": "live write",
            "expected": "ready",
            "effect": "write",
            "execute": True,
        },
        allow_writes=True,
    )
    assert result["passed"] is True
    assert result["executed"] is True
    assert len(allowed.executions) == 1


@pytest.mark.asyncio
async def test_case_context_is_forwarded_to_gateway_analysis():
    orchestrator = FakeOrchestrator(effect="read")
    context = {
        "speaker": {
            "name": "Laurent",
            "confidence": 0.91,
            "backend": "resemblyzer",
        }
    }
    result = await harness._run_case(
        orchestrator,
        {
            "text": "monte mon retour",
            "expected": "ready",
            "effect": "read",
            "context": context,
        },
        allow_writes=False,
    )
    assert result["passed"] is True
    assert orchestrator.contexts == [context]
    assert result["context"] == context


def test_harness_is_directly_runnable_from_repository_root():
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "or4c_local_gateway_acceptance.py"),
            "--help",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "--allow-writes" in completed.stdout
