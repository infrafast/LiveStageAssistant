from pathlib import Path

agent = Path("voice_assistant/agent.py")
text = agent.read_text()

replacements = [
    (
        "DEFAULT_BACKEND_WAKE_WORD_COMMAND_TIMEOUT_MS = 2500\n",
        "DEFAULT_BACKEND_WAKE_WORD_COMMAND_TIMEOUT_MS = 2500\nDEFAULT_BACKEND_WAKE_WORD_POST_TTS_SUPPRESS_MS = 350\n",
    ),
    (
        "        self.backend_wake_word_unavailable_reason = \"\"\n        self.last_backend_streaming_wake_detected = False\n",
        "        self.backend_wake_word_unavailable_reason = \"\"\n        self.last_backend_streaming_wake_detected = False\n        self.backend_wake_word_suppress_until = 0.0\n",
    ),
    (
        "                vad_data = pcm_to_vad_16k_mono(data, source_rate=self.rate, channels=self.channels)\n                streaming_pre_wake_frame = streaming_wake_active and not wake_detected\n",
        "                vad_data = pcm_to_vad_16k_mono(data, source_rate=self.rate, channels=self.channels)\n                if (\n                    streaming_wake_active\n                    and not wake_detected\n                    and time.monotonic() < self.backend_wake_word_suppress_until\n                ):\n                    # Keep draining the microphone immediately after backend TTS, but do\n                    # not let the acoustic tail or loopback audio reach openWakeWord/VAD.\n                    pre_roll = []\n                    frames = []\n                    speech_candidate = []\n                    speech_candidate_ms = 0.0\n                    silence_ms = 0.0\n                    recorded_speech_ms = 0.0\n                    self.backend_wake_word_detector.reset(clear_cooldown=True)\n                    self.vad.reset()\n                    continue\n                streaming_pre_wake_frame = streaming_wake_active and not wake_detected\n",
    ),
    (
        "                else:\n                    await self.text_to_speech(response)\n\n                if self.mcp_reconnect_after_response:\n",
        "                else:\n                    await self.text_to_speech(response)\n\n                if self._backend_streaming_wake_active():\n                    self.backend_wake_word_suppress_until = (\n                        time.monotonic() + DEFAULT_BACKEND_WAKE_WORD_POST_TTS_SUPPRESS_MS / 1000.0\n                    )\n                    self.backend_wake_word_detector.reset(clear_cooldown=True)\n                    self.vad.reset()\n                    LOGGER.debug(\n                        \"Suppressing backend openWakeWord input for %d ms after TTS\",\n                        DEFAULT_BACKEND_WAKE_WORD_POST_TTS_SUPPRESS_MS,\n                    )\n\n                if self.mcp_reconnect_after_response:\n",
    ),
]

for old, new in replacements:
    if old not in text:
        raise SystemExit(f"Expected agent.py block not found:\n{old}")
    text = text.replace(old, new, 1)

agent.write_text(text)

architecture = Path("docs/ARCHITECTURE.md")
doc = architecture.read_text()
needle = (
    "At that point all pre-trigger audio is discarded and a fresh VAD segment starts immediately for the command, "
    "so the wake phrase itself cannot reach STT or the LLM."
)
replacement = (
    "At that point all pre-trigger audio is discarded and a fresh VAD segment starts immediately for the command, "
    "so the wake phrase itself cannot reach STT or the LLM. After backend TTS finishes, the microphone continues "
    "to drain normally but openWakeWord/VAD input is suppressed for 350 ms and their state is reset, preventing "
    "the assistant's own acoustic/loopback tail from retriggering the wake word without adding a blocking sleep."
)
if needle not in doc:
    raise SystemExit("Expected architecture wake-word paragraph not found")
architecture.write_text(doc.replace(needle, replacement, 1))

print("Applied openWakeWord post-TTS suppression guard.")
