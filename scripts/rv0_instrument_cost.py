#!/usr/bin/env python3
"""Apply idempotent RV0 cloud-cost instrumentation to a local agent.py checkout."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
AGENT_PATH = ROOT / "voice_assistant" / "agent.py"

PACKAGE_IMPORT = "    from .wake_word import apply_wake_word, parse_wake_words\n"
PACKAGE_COST_IMPORT = PACKAGE_IMPORT + "    from .voice_cost_metrics import RV0_COST_COLLECTOR, cost_log, mp3_duration_seconds, wav_duration_seconds\n"
FALLBACK_IMPORT = "    from wake_word import apply_wake_word, parse_wake_words\n"
FALLBACK_COST_IMPORT = FALLBACK_IMPORT + "    from voice_cost_metrics import RV0_COST_COLLECTOR, cost_log, mp3_duration_seconds, wav_duration_seconds\n"
LLM_ANCHOR = "        return ChatOpenAI(model=self.model, api_key=self.openai_api_key)\n"
LLM_REPLACEMENT = "        return ChatOpenAI(model=self.model, api_key=self.openai_api_key, callbacks=[RV0_COST_COLLECTOR])\n"
PROCESS_ANCHOR = "                process_task = asyncio.create_task(self.process_command(text, speaker_result=speaker_result))\n"
PROCESS_REPLACEMENT = "                rv0_cost_before = RV0_COST_COLLECTOR.snapshot()\n" + PROCESS_ANCHOR
RESPONSE_ANCHOR = "                response = await process_task\n"
RESPONSE_REPLACEMENT = RESPONSE_ANCHOR + "                rv0_cost_delta = RV0_COST_COLLECTOR.snapshot() - rv0_cost_before\n                print(cost_log(\"llm\", model=self.model, input_tokens=rv0_cost_delta.input_tokens, cached_input_tokens=rv0_cost_delta.cached_input_tokens, output_tokens=rv0_cost_delta.output_tokens), flush=True)\n"
STT_ANCHOR = "            response = self.openai_stt_client.audio.transcriptions.create(**kwargs)\n"
STT_REPLACEMENT = "            print(cost_log(\"stt\", model=\"whisper-1\", audio_seconds=wav_duration_seconds(wav_buffer.getvalue())), flush=True)\n" + STT_ANCHOR
TTS_ANCHOR = "        return response.read()\n\n    def generate_elevenlabs_tts_audio(\n"
TTS_REPLACEMENT = "        audio_bytes = response.read()\n        print(cost_log(\"tts\", model=model, audio_seconds=mp3_duration_seconds(audio_bytes), text_chars=len(cleaned_text)), flush=True)\n        return audio_bytes\n\n    def generate_elevenlabs_tts_audio(\n"
MARKERS = ("callbacks=[RV0_COST_COLLECTOR]", "rv0_cost_before = RV0_COST_COLLECTOR.snapshot()", "cost_log(\"llm\"", "cost_log(\"stt\"", "cost_log(\"tts\"")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"RV0 cost anchor {label!r} expected once, found {count}")
    return text.replace(old, new, 1)


def is_instrumented(text: str) -> bool:
    return all(marker in text for marker in MARKERS) and "voice_cost_metrics import RV0_COST_COLLECTOR" in text


def instrument(text: str) -> str:
    if is_instrumented(text):
        return text
    if "voice_cost_metrics import RV0_COST_COLLECTOR" not in text:
        text = replace_once(text, PACKAGE_IMPORT, PACKAGE_COST_IMPORT, "package import")
        text = replace_once(text, FALLBACK_IMPORT, FALLBACK_COST_IMPORT, "fallback import")
    text = replace_once(text, LLM_ANCHOR, LLM_REPLACEMENT, "ChatOpenAI callback")
    text = replace_once(text, PROCESS_ANCHOR, PROCESS_REPLACEMENT, "turn token snapshot")
    text = replace_once(text, RESPONSE_ANCHOR, RESPONSE_REPLACEMENT, "turn token delta")
    text = replace_once(text, STT_ANCHOR, STT_REPLACEMENT, "backend STT duration")
    text = replace_once(text, TTS_ANCHOR, TTS_REPLACEMENT, "OpenAI TTS duration")
    if not is_instrumented(text):
        raise RuntimeError("RV0 cost instrumentation did not reach expected final state")
    return text


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.check == args.apply:
        parser.error("choose exactly one of --check or --apply")
    original = AGENT_PATH.read_text()
    if args.check:
        print("RV0 cost instrumentation: present" if is_instrumented(original) else "RV0 cost instrumentation: missing")
        return 0 if is_instrumented(original) else 1
    updated = instrument(original)
    if updated == original:
        print("RV0 cost instrumentation already present; no change")
        return 0
    AGENT_PATH.write_text(updated)
    print("RV0 cost instrumentation applied to voice_assistant/agent.py")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        print(f"RV0 cost instrumentation failed: {error}", file=sys.stderr)
        raise SystemExit(2)
