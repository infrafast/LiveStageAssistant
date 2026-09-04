from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"RV0 patch anchor not found: {label}")
    if text.count(old) != 1:
        raise SystemExit(f"RV0 patch anchor is not unique: {label} ({text.count(old)} matches)")
    return text.replace(old, new, 1)


agent_path = ROOT / "voice_assistant" / "agent.py"
agent = agent_path.read_text()

agent = replace_once(
    agent,
    "    from .wake_word import apply_wake_word, parse_wake_words\n",
    "    from .wake_word import apply_wake_word, parse_wake_words\n    from .voice_metrics import VoiceTurnMetrics\n",
    "package voice_metrics import",
)
agent = replace_once(
    agent,
    "    from wake_word import apply_wake_word, parse_wake_words\n",
    "    from wake_word import apply_wake_word, parse_wake_words\n    from voice_metrics import VoiceTurnMetrics\n",
    "fallback voice_metrics import",
)

agent = replace_once(
    agent,
    "                # Process command\n                self._set_backend_audio_state(BackendAudioState.PROCESSING, \"command accepted\")\n                process_task = asyncio.create_task(self.process_command(text, speaker_result=speaker_result))\n",
    "                # Process command\n                turn_metrics = VoiceTurnMetrics(pipeline=\"classic\")\n                turn_metrics.mark(\"command_accepted\")\n                self._set_backend_audio_state(BackendAudioState.PROCESSING, \"command accepted\")\n                process_task = asyncio.create_task(self.process_command(text, speaker_result=speaker_result))\n",
    "classic turn start",
)
agent = replace_once(
    agent,
    "                response = await process_task\n                if self.reload_event and self.reload_event.is_set():\n",
    "                response = await process_task\n                turn_metrics.mark(\"agent_response_ready\")\n                if self.reload_event and self.reload_event.is_set():\n",
    "agent response timing",
)
agent = replace_once(
    agent,
    "                # Try to speak the response\n                self._set_backend_audio_state(BackendAudioState.TTS, \"speaking response\")\n",
    "                # Try to speak the response\n                turn_metrics.mark(\"tts_start\")\n                self._set_backend_audio_state(BackendAudioState.TTS, \"speaking response\")\n",
    "tts start timing",
)
agent = replace_once(
    agent,
    "                else:\n                    await self.text_to_speech(response)\n\n                if self._backend_streaming_wake_active():\n",
    "                else:\n                    await self.text_to_speech(response)\n\n                turn_metrics.mark(\"tts_end\")\n                print(turn_metrics.to_log_line(), flush=True)\n\n                if self._backend_streaming_wake_active():\n",
    "tts end timing",
)
agent_path.write_text(agent)

metrics_path = ROOT / "voice_assistant" / "voice_metrics.py"
metrics_path.write_text('''"""Provider-neutral voice-turn timing primitives used by classic and realtime benchmarks."""\n\nfrom __future__ import annotations\n\nfrom dataclasses import dataclass, field\nimport json\nimport time\nfrom typing import Callable\n\nVOICE_METRICS_PREFIX = "VOICE_METRICS "\n\n\n@dataclass\nclass VoiceTurnMetrics:\n    """Capture monotonic event timestamps without changing runtime control flow."""\n\n    pipeline: str\n    clock: Callable[[], float] = time.perf_counter\n    started_at: float = field(init=False)\n    events: dict[str, float] = field(default_factory=dict)\n\n    def __post_init__(self) -> None:\n        self.started_at = self.clock()\n\n    def mark(self, event: str) -> float:\n        if not event:\n            raise ValueError("event name is required")\n        elapsed_ms = (self.clock() - self.started_at) * 1000.0\n        self.events[event] = elapsed_ms\n        return elapsed_ms\n\n    def duration_ms(self, start_event: str, end_event: str) -> float | None:\n        start = self.events.get(start_event)\n        end = self.events.get(end_event)\n        if start is None or end is None or end < start:\n            return None\n        return end - start\n\n    def record(self) -> dict[str, object]:\n        durations = {\n            "agent_ms": self.duration_ms("command_accepted", "agent_response_ready"),\n            "tts_ms": self.duration_ms("tts_start", "tts_end"),\n            "turn_ms": self.duration_ms("command_accepted", "tts_end"),\n        }\n        return {\n            "schema": 1,\n            "pipeline": self.pipeline,\n            "events_ms": {key: round(value, 3) for key, value in self.events.items()},\n            "durations_ms": {\n                key: (round(value, 3) if value is not None else None)\n                for key, value in durations.items()\n            },\n        }\n\n    def to_log_line(self) -> str:\n        return VOICE_METRICS_PREFIX + json.dumps(self.record(), separators=(",", ":"), sort_keys=True)\n\n\ndef parse_voice_metrics_line(line: str) -> dict[str, object] | None:\n    marker = line.find(VOICE_METRICS_PREFIX)\n    if marker < 0:\n        return None\n    payload = line[marker + len(VOICE_METRICS_PREFIX) :].strip()\n    if not payload:\n        return None\n    try:\n        value = json.loads(payload)\n    except json.JSONDecodeError:\n        return None\n    return value if isinstance(value, dict) else None\n''')

realtime_dir = ROOT / "voice_assistant" / "realtime"
realtime_dir.mkdir(parents=True, exist_ok=True)
(realtime_dir / "__init__.py").write_text('''"""Provider-neutral realtime voice boundary. Live transports begin in RV1."""\n\nfrom .engine import RealtimeEngine, RealtimeEngineConfig, RealtimeEngineState\n\n__all__ = ["RealtimeEngine", "RealtimeEngineConfig", "RealtimeEngineState"]\n''')
(realtime_dir / "engine.py").write_text('''"""Provider-neutral realtime engine contract introduced by RV0.\n\nRV0 deliberately contains no OpenAI, Gemini, WebSocket or WebRTC implementation.\nProvider transports start in RV1 and must implement this boundary without altering\nthe existing classic or MCP execution paths.\n"""\n\nfrom __future__ import annotations\n\nfrom abc import ABC, abstractmethod\nfrom dataclasses import dataclass\nfrom enum import Enum\nfrom typing import Any\n\n\nclass RealtimeEngineState(str, Enum):\n    STOPPED = "stopped"\n    READY = "ready"\n    ACTIVE = "active"\n    ERROR = "error"\n\n\n@dataclass(frozen=True)\nclass RealtimeEngineConfig:\n    provider: str\n    model: str\n\n    def __post_init__(self) -> None:\n        if not self.provider.strip():\n            raise ValueError("realtime provider is required")\n        if not self.model.strip():\n            raise ValueError("realtime model is required")\n\n\nclass RealtimeEngine(ABC):\n    """Minimal lifecycle contract shared by future realtime providers."""\n\n    def __init__(self, config: RealtimeEngineConfig) -> None:\n        self.config = config\n        self.state = RealtimeEngineState.STOPPED\n\n    @abstractmethod\n    async def start(self) -> None:\n        """Prepare the provider session and move to READY."""\n\n    @abstractmethod\n    async def stop(self) -> None:\n        """Close provider/audio resources and move to STOPPED."""\n\n    @abstractmethod\n    async def send_audio(self, pcm: bytes) -> None:\n        """Send one backend audio chunk to the active realtime provider."""\n\n    @abstractmethod\n    async def cancel_response(self) -> None:\n        """Cancel the current provider response for barge-in/cancellation."""\n\n    @abstractmethod\n    async def submit_tool_result(self, call_id: str, result: Any) -> None:\n        """Return an existing LSA tool-path result to the provider session."""\n''')

script_path = ROOT / "scripts" / "summarize_voice_metrics.py"
script_path.write_text('''#!/usr/bin/env python3\n"""Summarize classic/realtime VOICE_METRICS log lines for RV benchmarks."""\n\nfrom __future__ import annotations\n\nimport argparse\nfrom pathlib import Path\nimport re\nimport statistics\nimport sys\n\nfrom voice_assistant.voice_metrics import parse_voice_metrics_line\n\nSTT_RE = re.compile(r"STT finished in ([0-9.]+)s\\.")\n\n\ndef percentile(values: list[float], p: float) -> float | None:\n    if not values:\n        return None\n    ordered = sorted(values)\n    if len(ordered) == 1:\n        return ordered[0]\n    position = (len(ordered) - 1) * p\n    low = int(position)\n    high = min(low + 1, len(ordered) - 1)\n    fraction = position - low\n    return ordered[low] + (ordered[high] - ordered[low]) * fraction\n\n\ndef summarize(values: list[float]) -> str:\n    if not values:\n        return "n=0"\n    return (\n        f"n={len(values)} mean={statistics.fmean(values):.0f}ms "\n        f"p50={percentile(values, 0.50):.0f}ms "\n        f"p90={percentile(values, 0.90):.0f}ms "\n        f"p95={percentile(values, 0.95):.0f}ms"\n    )\n\n\ndef collect(lines: list[str]) -> dict[str, list[float]]:\n    samples = {"stt_ms": [], "agent_ms": [], "tts_ms": [], "turn_ms": []}\n    for line in lines:\n        stt_match = STT_RE.search(line)\n        if stt_match:\n            samples["stt_ms"].append(float(stt_match.group(1)) * 1000.0)\n        record = parse_voice_metrics_line(line)\n        if not record:\n            continue\n        durations = record.get("durations_ms")\n        if not isinstance(durations, dict):\n            continue\n        for key in ("agent_ms", "tts_ms", "turn_ms"):\n            value = durations.get(key)\n            if isinstance(value, (int, float)):\n                samples[key].append(float(value))\n    return samples\n\n\ndef main() -> int:\n    parser = argparse.ArgumentParser()\n    parser.add_argument("logfile", nargs="?", help="journal/log file; stdin when omitted")\n    args = parser.parse_args()\n    lines = Path(args.logfile).read_text(errors="replace").splitlines() if args.logfile else sys.stdin.read().splitlines()\n    samples = collect(lines)\n    for key in ("stt_ms", "agent_ms", "tts_ms", "turn_ms"):\n        print(f"{key}: {summarize(samples[key])}")\n    return 0\n\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n''')

tests_dir = ROOT / "tests"
tests_dir.mkdir(exist_ok=True)
(tests_dir / "test_voice_metrics.py").write_text('''import json\nimport unittest\n\nfrom voice_assistant.voice_metrics import VOICE_METRICS_PREFIX, VoiceTurnMetrics, parse_voice_metrics_line\n\n\nclass FakeClock:\n    def __init__(self):\n        self.value = 10.0\n\n    def __call__(self):\n        return self.value\n\n    def advance(self, seconds):\n        self.value += seconds\n\n\nclass VoiceMetricsTests(unittest.TestCase):\n    def test_records_classic_durations(self):\n        clock = FakeClock()\n        metrics = VoiceTurnMetrics("classic", clock=clock)\n        metrics.mark("command_accepted")\n        clock.advance(0.4)\n        metrics.mark("agent_response_ready")\n        clock.advance(0.1)\n        metrics.mark("tts_start")\n        clock.advance(0.3)\n        metrics.mark("tts_end")\n        record = metrics.record()\n        self.assertEqual(record["pipeline"], "classic")\n        self.assertAlmostEqual(record["durations_ms"]["agent_ms"], 400.0)\n        self.assertAlmostEqual(record["durations_ms"]["tts_ms"], 300.0)\n        self.assertAlmostEqual(record["durations_ms"]["turn_ms"], 800.0)\n\n    def test_log_line_round_trip(self):\n        metrics = VoiceTurnMetrics("classic")\n        metrics.mark("command_accepted")\n        line = metrics.to_log_line()\n        self.assertTrue(line.startswith(VOICE_METRICS_PREFIX))\n        parsed = parse_voice_metrics_line("prefix " + line)\n        self.assertEqual(parsed["pipeline"], "classic")\n\n    def test_invalid_line_is_ignored(self):\n        self.assertIsNone(parse_voice_metrics_line("VOICE_METRICS {broken"))\n\n\nif __name__ == "__main__":\n    unittest.main()\n''')
(tests_dir / "test_realtime_engine.py").write_text('''import unittest\n\nfrom voice_assistant.realtime import RealtimeEngine, RealtimeEngineConfig, RealtimeEngineState\n\n\nclass DummyEngine(RealtimeEngine):\n    async def start(self):\n        self.state = RealtimeEngineState.READY\n\n    async def stop(self):\n        self.state = RealtimeEngineState.STOPPED\n\n    async def send_audio(self, pcm: bytes):\n        return None\n\n    async def cancel_response(self):\n        return None\n\n    async def submit_tool_result(self, call_id: str, result):\n        return None\n\n\nclass RealtimeEngineTests(unittest.IsolatedAsyncioTestCase):\n    async def test_provider_neutral_lifecycle_contract(self):\n        engine = DummyEngine(RealtimeEngineConfig(provider="test", model="test-model"))\n        self.assertEqual(engine.state, RealtimeEngineState.STOPPED)\n        await engine.start()\n        self.assertEqual(engine.state, RealtimeEngineState.READY)\n        await engine.stop()\n        self.assertEqual(engine.state, RealtimeEngineState.STOPPED)\n\n    def test_config_requires_provider_and_model(self):\n        with self.assertRaises(ValueError):\n            RealtimeEngineConfig(provider="", model="x")\n        with self.assertRaises(ValueError):\n            RealtimeEngineConfig(provider="x", model="")\n\n\nif __name__ == "__main__":\n    unittest.main()\n''')

doc_path = ROOT / "docs" / "ARCHITECTURE_AND_ROADMAP.md"
doc = doc_path.read_text()
doc = replace_once(doc, "- [ ] create/refresh `realtime-voice-architecture` from the current `main`;", "- [x] create/refresh `realtime-voice-architecture` from the current `main`;", "RV0 branch status")
doc = replace_once(doc, "- [ ] formalize the classic timing events needed for comparison;", "- [x] formalize the classic timing events needed for comparison;", "RV0 timing contract")
doc = replace_once(doc, "- [ ] add only missing lightweight classic-path instrumentation;", "- [x] add only missing lightweight classic-path instrumentation;", "RV0 instrumentation status")
doc = replace_once(doc, "- [ ] create an isolated `RealtimeEngine` interface/package skeleton only, with no live realtime audio transport yet.", "- [x] create an isolated `RealtimeEngine` interface/package skeleton only, with no live realtime audio transport yet.", "RV0 skeleton status")
anchor = "Exit: dedicated branch is current with `main`, classic latency/cost baseline is recorded, and the realtime package boundary exists without changing production classic behavior."
note = "Implementation note: RV0 now emits structured `VOICE_METRICS` lines for classic command-processing/TTS timing, retains the existing STT/capture timing logs, and includes `scripts/summarize_voice_metrics.py` for p50/p90/p95 summaries. The Raspberry Pi latency/cost baseline remains intentionally unchecked until measured on real hardware; no synthetic value is treated as a baseline."
doc = replace_once(doc, anchor, anchor + "\n\n" + note, "RV0 implementation note")
doc_path.write_text(doc)

# Temporary staging files remove themselves from the final RV0 commit.
for relative in ("scripts/_apply_rv0.py", ".github/workflows/apply-rv0.yml"):
    path = ROOT / relative
    if path.exists():
        path.unlink()
