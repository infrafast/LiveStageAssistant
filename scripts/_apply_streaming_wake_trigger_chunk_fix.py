from pathlib import Path

path = Path("voice_assistant/agent.py")
text = path.read_text(encoding="utf-8")
old = '''                        # openWakeWord has already consumed the wake phrase. Start a fresh
                        # VAD segment immediately after the trigger so pre-trigger audio
                        # cannot be transcribed and leak the wake word into the LLM command.
                        # This also covers the case where Silero had already classified the
                        # wake phrase itself as speech before openWakeWord fired.
                        has_speech = False
                        frames = []
                        pre_roll = []
                        speech_candidate = []
                        speech_candidate_ms = 0.0
                        silence_ms = 0.0
                        recorded_speech_ms = 0.0
                        wake_audio_frames = []
                        wake_command_armed = True
                        wake_command_wait_ms = 0.0
                        self.vad.reset()
                        print(
                            f"Streaming wake word detected: {detected_label} ({detected_score:.2f})",
                            flush=True,
                        )
                        continue
'''
new = '''                        # Discard audio from before the openWakeWord trigger, but keep the
                        # trigger chunk itself as the first post-wake command candidate. A
                        # detector can fire late enough that this chunk already contains the
                        # beginning of an immediately-following command (for example the first
                        # syllable after any configured single- or multi-word wake phrase).
                        # Keeping only this chunk avoids reintroducing the full wake-word
                        # pre-roll while preserving commands spoken without a pause.
                        has_speech = False
                        frames = []
                        pre_roll = []
                        speech_candidate = []
                        speech_candidate_ms = 0.0
                        silence_ms = 0.0
                        recorded_speech_ms = 0.0
                        wake_audio_frames = []
                        wake_command_armed = True
                        wake_command_wait_ms = 0.0
                        self.vad.reset()
                        streaming_pre_wake_frame = False
                        print(
                            f"Streaming wake word detected: {detected_label} ({detected_score:.2f})",
                            flush=True,
                        )
'''
if old not in text:
    raise SystemExit("expected wake detection block not found")
text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")
