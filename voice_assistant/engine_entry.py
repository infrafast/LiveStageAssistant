#!/usr/bin/env python3
"""Engine entry adapter for the common LSA runtime lifecycle.

This module contains no connectivity detection or startup policy. It prevents
legacy per-engine loader ownership when launched by `voice_assistant.runtime`
and provides the small engine-specific speech adapter needed to deliver a
pending online-connectivity announcement with the selected engine voice.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
from pathlib import Path
import sys
import tempfile

from dotenv import dotenv_values

CLASSIC_READY_MARKER = "LSA Classic ready:"


def _install_offline_piper_adapter(agent, values: dict[str, object]) -> None:
    """Route the historical Classic local-TTS hook through Piper offline.

    Offline configuration is owned solely by LOCAL_TTS_PROVIDER. The temporary
    internal normalization below only activates the legacy Classic local-output
    code path; it is not a user-facing pyttsx3 configuration or fallback.
    """
    provider = str(values.get("LOCAL_TTS_PROVIDER") or "piper").strip().lower()
    if provider != "piper":
        return

    from voice_assistant.local_tts import piper_ready, piper_voice_name, render_piper_wav

    original_resolve_tts = agent.resolve_tts_config_from_values

    def resolve_tts_with_piper(env_values: dict):
        # Classic still identifies its historical local-output hook with the
        # internal provider token "pyttsx3". Keep that implementation detail out
        # of env profiles; the hook itself is replaced by Piper below.
        normalized = dict(env_values)
        normalized["TTS_PROVIDER"] = "pyttsx3"
        return original_resolve_tts(normalized)

    def piper_available() -> bool:
        return piper_ready(values)

    def text_to_speech_piper(self, text: str) -> bool:
        if not piper_ready(values):
            self.stop_thinking_sound()
            print("Piper unavailable in offline mode; local speech is unavailable.", flush=True)
            return False

        if not self._backend_output_ready():
            self.stop_thinking_sound()
            print(f"Local Piper TTS skipped: backend audio output is {self.audio_output_device_status}.", flush=True)
            return False

        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
                temp_path = temp_file.name
            spoken_text = agent.prepare_text_for_tts(text)
            render_piper_wav(spoken_text, temp_path, values)
            print(f"Local Piper TTS: voice={piper_voice_name(values)}", flush=True)
            self.stop_thinking_sound()
            self.play_local_tts_file(temp_path, stop_event=agent.TTS_STOP_EVENT)
            return True
        except Exception as exc:
            self.stop_thinking_sound()
            print(f"Local Piper TTS failed: {exc}", flush=True)
            return False
        finally:
            if temp_path:
                with contextlib.suppress(OSError):
                    Path(temp_path).unlink()

    agent.resolve_tts_config_from_values = resolve_tts_with_piper
    agent.local_tts_playback_available = piper_available
    agent.VoiceAssistant.text_to_speech_pyttsx3 = text_to_speech_piper
    print(
        f"LSA local TTS adapter: provider=piper voice={piper_voice_name(values)} ready={piper_ready(values)}",
        flush=True,
    )


def run_classic(env_file: str) -> int:
    from voice_assistant import agent

    values = dict(dotenv_values(env_file)) if str(env_file).lower() != "auto" else {}
    online = str(values.get("CONNECTIVITY_MODE") or "online").strip().lower() != "offline"

    if not online:
        _install_offline_piper_adapter(agent, values)

    if os.getenv("LSA_COMMON_STARTUP_LIFECYCLE") == "1":
        agent.VoiceAssistant.start_startup_loader_sound = lambda self: None
        agent.VoiceAssistant.stop_startup_loader_sound = lambda self: None

        original_announce_ready = agent.VoiceAssistant.announce_startup_ready

        async def announce_ready_with_connectivity(self, loaded_servers):
            print(f"{CLASSIC_READY_MARKER} connectivity={'online' if online else 'offline'}", flush=True)

            if online and os.getenv("LSA_ANNOUNCE_ONLINE_WITH_ENGINE", "1") == "1":
                message = "Assistant connecté à internet."
                print(f"LSA connectivity announcement via classic: {message}", flush=True)
                if getattr(self, "tts_provider", "none") != "none":
                    try:
                        await asyncio.wait_for(
                            asyncio.to_thread(lambda: asyncio.run(self.text_to_speech(message))),
                            timeout=8.0,
                        )
                    except Exception as exc:
                        print(f"Classic connectivity announcement failed: {exc}", flush=True)
                await asyncio.sleep(0.45)
            await original_announce_ready(self, loaded_servers)

        agent.VoiceAssistant.announce_startup_ready = announce_ready_with_connectivity

    sys.argv = [sys.argv[0], "--env-file", env_file]
    asyncio.run(agent.main())
    return 0


def run_openai_realtime(env_file: str) -> int:
    from voice_assistant.realtime import service

    if os.getenv("LSA_COMMON_STARTUP_LIFECYCLE") == "1":
        service.play_startup_sound = lambda _env_file: None

    sys.argv = [sys.argv[0], "--env-file", env_file]
    return service.main()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", required=True, choices=("classic", "local", "openai-realtime"))
    parser.add_argument("--env-file", required=True)
    args = parser.parse_args()

    if args.engine == "openai-realtime":
        return run_openai_realtime(args.env_file)
    return run_classic(args.env_file)


if __name__ == "__main__":
    raise SystemExit(main())
