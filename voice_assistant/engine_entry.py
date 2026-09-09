#!/usr/bin/env python3
"""Engine entry adapter for the common LSA runtime lifecycle.

This module contains no connectivity detection, WebMonitor ownership or startup
policy. It provides only the small engine-specific speech adapter needed to
deliver startup announcements with the selected engine voice.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from dotenv import dotenv_values

from voice_assistant.startup_messages import startup_connectivity_message

CLASSIC_READY_MARKER = "LSA Classic ready:"


def run_classic(env_file: str) -> int:
    from voice_assistant import agent
    from voice_assistant import classic_engine

    values = dict(dotenv_values(env_file)) if str(env_file).lower() != "auto" else {}
    connectivity = str(values.get("CONNECTIVITY_MODE") or "online").strip().lower()
    online = connectivity != "offline"

    if os.getenv("LSA_COMMON_STARTUP_LIFECYCLE") == "1":
        agent.VoiceAssistant.start_startup_loader_sound = lambda self: None
        agent.VoiceAssistant.stop_startup_loader_sound = lambda self: None

        original_announce_ready = agent.VoiceAssistant.announce_startup_ready

        async def announce_ready_with_connectivity(self, loaded_servers):
            print(f"{CLASSIC_READY_MARKER} connectivity={'online' if online else 'offline'}", flush=True)

            message = startup_connectivity_message(
                stt_language=getattr(self, "stt_language", str(values.get("STT_LANGUAGE") or "fr")),
                connectivity=connectivity,
            )
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

    return classic_engine.run(env_file)


def run_realtime(env_file: str, provider: str) -> int:
    from voice_assistant.realtime import service
    from voice_assistant.realtime import wake_runtime

    os.environ["LSA_REALTIME_PROVIDER"] = provider
    if os.getenv("LSA_COMMON_STARTUP_LIFECYCLE") == "1":
        service.play_startup_sound = lambda _env_file: None

    # Wake authorization remains local and provider-neutral. With WAKE_WORD
    # empty this is a no-op and the direct-listening flow is preserved.
    wake_runtime.install(service, env_file)

    sys.argv = [sys.argv[0], "--env-file", env_file]
    return service.main()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", required=True, choices=("classic", "local", "openai-realtime", "gemini-live"))
    parser.add_argument("--env-file", required=True)
    args = parser.parse_args()

    if args.engine == "openai-realtime":
        return run_realtime(args.env_file, "openai")
    if args.engine == "gemini-live":
        return run_realtime(args.env_file, "gemini")
    return run_classic(args.env_file)


if __name__ == "__main__":
    raise SystemExit(main())
