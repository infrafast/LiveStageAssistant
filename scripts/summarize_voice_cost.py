#!/usr/bin/env python3
"""Summarize RV0 classic cloud usage and estimated end-to-end cost from journal logs."""

from __future__ import annotations

import argparse
from pathlib import Path
import statistics
import sys

from voice_assistant.voice_cost_metrics import parse_cost_line

# OpenAI public prices checked 2026-09-04. USD.
WHISPER_USD_PER_MINUTE = 0.006
GPT41_MINI_INPUT_PER_M = 0.40
GPT41_MINI_CACHED_INPUT_PER_M = 0.10
GPT41_MINI_OUTPUT_PER_M = 1.60
# /audio/speech does not expose per-request usage. This remains an explicit estimate.
GPT4O_MINI_TTS_ESTIMATED_USD_PER_MINUTE = 0.015


def mean(values):
    return statistics.fmean(values) if values else 0.0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("logfile", nargs="?", help="journal/log file; stdin when omitted")
    args = parser.parse_args()
    lines = Path(args.logfile).read_text(errors="replace").splitlines() if args.logfile else sys.stdin.read().splitlines()
    stt_seconds = []
    tts_seconds = []
    llm_costs = []
    llm_inputs = llm_cached = llm_outputs = 0
    for line in lines:
        record = parse_cost_line(line)
        if not record:
            continue
        stage = record.get("stage")
        if stage == "stt" and isinstance(record.get("audio_seconds"), (int, float)):
            stt_seconds.append(float(record["audio_seconds"]))
        elif stage == "tts" and isinstance(record.get("audio_seconds"), (int, float)):
            tts_seconds.append(float(record["audio_seconds"]))
        elif stage == "llm":
            inp = int(record.get("input_tokens") or 0)
            cached = min(inp, int(record.get("cached_input_tokens") or 0))
            out = int(record.get("output_tokens") or 0)
            llm_inputs += inp
            llm_cached += cached
            llm_outputs += out
            uncached = inp - cached
            llm_costs.append((uncached * GPT41_MINI_INPUT_PER_M + cached * GPT41_MINI_CACHED_INPUT_PER_M + out * GPT41_MINI_OUTPUT_PER_M) / 1_000_000.0)
    stt_costs = [(seconds / 60.0) * WHISPER_USD_PER_MINUTE for seconds in stt_seconds]
    tts_costs = [(seconds / 60.0) * GPT4O_MINI_TTS_ESTIMATED_USD_PER_MINUTE for seconds in tts_seconds]
    complete_n = min(len(stt_costs), len(llm_costs), len(tts_costs))
    avg_stt = mean(stt_costs)
    avg_llm = mean(llm_costs)
    avg_tts = mean(tts_costs)
    print(f"stt: n={len(stt_costs)} audio_mean={mean(stt_seconds):.2f}s avg_usd=${avg_stt:.6f}")
    print(f"llm: n={len(llm_costs)} input={llm_inputs} cached={llm_cached} output={llm_outputs} avg_usd=${avg_llm:.6f}")
    if tts_costs:
        print(f"tts: n={len(tts_costs)} audio_mean={mean(tts_seconds):.2f}s avg_estimated_usd=${avg_tts:.6f}")
    else:
        print("tts: n=0 audio duration unavailable (ffprobe required); TTS cost not estimated")
    if complete_n:
        total = avg_stt + avg_llm + avg_tts
        print(f"classic_total: representative_n={complete_n} avg_estimated_usd=${total:.6f} per_100≈${total * 100:.4f}")
        print("note: STT and LLM use measured request usage; TTS is an explicit duration-based estimate because /audio/speech has no per-request usage object.")
    else:
        print("classic_total: incomplete component sample")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
