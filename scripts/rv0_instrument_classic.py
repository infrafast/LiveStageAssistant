#!/usr/bin/env python3
"""Apply or verify RV0 classic-path latency instrumentation.

This helper is intentionally idempotent and is used for Raspberry Pi baseline runs.
It edits only voice_assistant/agent.py in the local checkout. The production branch
remains unchanged unless the resulting file is deliberately committed later.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
AGENT_PATH = ROOT / "voice_assistant" / "agent.py"

PACKAGE_IMPORT = "    from .wake_word import apply_wake_word, parse_wake_words\n"
PACKAGE_IMPORT_WITH_METRICS = PACKAGE_IMPORT + "    from .voice_metrics import VoiceTurnMetrics\n"
FALLBACK_IMPORT = "    from wake_word import apply_wake_word, parse_wake_words\n"
FALLBACK_IMPORT_WITH_METRICS = FALLBACK_IMPORT + "    from voice_metrics import VoiceTurnMetrics\n"

PROCESS_ANCHOR = (
    "                # Process command\n"
    "                self._set_backend_audio_state(BackendAudioState.PROCESSING, \"command accepted\")\n"
    "                process_task = asyncio.create_task(self.process_command(text, speaker_result=speaker_result))\n"
)
PROCESS_REPLACEMENT = (
    "                # Process command\n"
    "                turn_metrics = VoiceTurnMetrics(pipeline=\"classic\")\n"
    "                turn_metrics.mark(\"command_accepted\")\n"
    "                self._set_backend_audio_state(BackendAudioState.PROCESSING, \"command accepted\")\n"
    "                process_task = asyncio.create_task(self.process_command(text, speaker_result=speaker_result))\n"
)

RESPONSE_ANCHOR = (
    "                response = await process_task\n"
    "                if self.reload_event and self.reload_event.is_set():\n"
)
RESPONSE_REPLACEMENT = (
    "                response = await process_task\n"
    "                turn_metrics.mark(\"agent_response_ready\")\n"
    "                if self.reload_event and self.reload_event.is_set():\n"
)

TTS_START_ANCHOR = (
    "                # Try to speak the response\n"
    "                self._set_backend_audio_state(BackendAudioState.TTS, \"speaking response\")\n"
)
TTS_START_REPLACEMENT = (
    "                # Try to speak the response\n"
    "                turn_metrics.mark(\"tts_start\")\n"
    "                self._set_backend_audio_state(BackendAudioState.TTS, \"speaking response\")\n"
)

TTS_END_ANCHOR = (
    "                else:\n"
    "                    await self.text_to_speech(response)\n\n"
    "                if self._backend_streaming_wake_active():\n"
)
TTS_END_REPLACEMENT = (
    "                else:\n"
    "                    await self.text_to_speech(response)\n\n"
    "                turn_metrics.mark(\"tts_end\")\n"
    "                print(turn_metrics.to_log_line(), flush=True)\n\n"
    "                if self._backend_streaming_wake_active():\n"
)

MARKERS = (
    "VoiceTurnMetrics(pipeline=\"classic\")",
    "turn_metrics.mark(\"agent_response_ready\")",
    "turn_metrics.mark(\"tts_start\")",
    "turn_metrics.mark(\"tts_end\")",
)


def is_instrumented(text: str) -> bool:
    return all(marker in text for marker in MARKERS) and "voice_metrics import VoiceTurnMetrics" in text


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"RV0 anchor {label!r} expected once, found {count}")
    return text.replace(old, new, 1)


def instrument(text: str) -> str:
    if is_instrumented(text):
        return text
    text = replace_once(text, PACKAGE_IMPORT, PACKAGE_IMPORT_WITH_METRICS, "package import")
    text = replace_once(text, FALLBACK_IMPORT, FALLBACK_IMPORT_WITH_METRICS, "fallback import")
    text = replace_once(text, PROCESS_ANCHOR, PROCESS_REPLACEMENT, "process start")
    text = replace_once(text, RESPONSE_ANCHOR, RESPONSE_REPLACEMENT, "agent response")
    text = replace_once(text, TTS_START_ANCHOR, TTS_START_REPLACEMENT, "tts start")
    text = replace_once(text, TTS_END_ANCHOR, TTS_END_REPLACEMENT, "tts end")
    if not is_instrumented(text):
        raise RuntimeError("RV0 instrumentation did not reach the expected final state")
    return text


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="verify that instrumentation is already present")
    parser.add_argument("--apply", action="store_true", help="apply instrumentation in the local checkout")
    args = parser.parse_args()
    if args.check == args.apply:
        parser.error("choose exactly one of --check or --apply")

    original = AGENT_PATH.read_text()
    if args.check:
        if is_instrumented(original):
            print("RV0 classic instrumentation: present")
            return 0
        print("RV0 classic instrumentation: missing")
        return 1

    updated = instrument(original)
    if updated == original:
        print("RV0 classic instrumentation already present; no change")
        return 0
    AGENT_PATH.write_text(updated)
    print("RV0 classic instrumentation applied to voice_assistant/agent.py")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        print(f"RV0 instrumentation failed: {error}", file=sys.stderr)
        raise SystemExit(2)
