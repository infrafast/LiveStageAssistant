from pathlib import Path

path = Path('docs/ARCHITECTURE.md')
text = path.read_text(encoding='utf-8')
needle = "- Complete hardware recette on Raspberry Pi with real input/output devices, backend TTS, browser TTS, browser STT, backend diagnostic, speaker recognition, MCP routing, env reload, and stop/interruption behavior.\n"
insert = needle + "- Restore the intended `BACKEND_AUDIO_MONITOR_MODE=rejected` behavior under the strict `WAIT_WAKE` state machine. The original specification is that speech captured without a valid wake word is replayed through the configured backend audio output, but the current strict wake-first loop does not build a complete rejected utterance, making the existing replay helper effectively unreachable for normal ambient speech. Proposed implementation: only when `BACKEND_AUDIO_MONITOR_MODE=rejected`, run Silero VAD in parallel with openWakeWord during `WAIT_WAKE` solely to delimit and buffer rejected speech for monitoring. openWakeWord must remain the only authorization path into `CAPTURE_COMMAND`; the parallel VAD must never trigger STT, speaker recognition, LLM, or MCP processing. Keep this path disabled in `off` and `passthrough` modes so there is no extra VAD cost in normal operation, and validate Raspberry Pi CPU/latency impact before considering it complete.\n"
if needle not in text:
    raise SystemExit('roadmap insertion point not found')
if 'Restore the intended `BACKEND_AUDIO_MONITOR_MODE=rejected` behavior' in text:
    raise SystemExit('roadmap item already present')
path.write_text(text.replace(needle, insert, 1), encoding='utf-8')
