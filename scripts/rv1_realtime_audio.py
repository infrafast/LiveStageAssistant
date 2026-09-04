#!/usr/bin/env python3
"""Isolated RV1 OpenAI Realtime audio spike.

No MCP tools are loaded or exposed. The script streams the selected backend mic
to Realtime and plays returned PCM on the selected backend output while logging
latency and native response.done usage.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import signal
import sys
import time

import pyaudio
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from voice_assistant.realtime.audio import Pcm16MonoResampler, downmix_pcm16, expand_pcm16_channels
from voice_assistant.realtime.audio_devices import PipeWireInputStream, PipeWireOutputStream, parse_pipewire_selector
from voice_assistant.realtime.engine import RealtimeEngineConfig
from voice_assistant.realtime.metrics import realtime_usage_cost_usd
from voice_assistant.realtime.openai_realtime import OpenAIRealtimeEngine


REALTIME_RATE = 24000
RV1_METRICS_PREFIX = "RV1_METRICS "


def read_secret(name: str, env_file: Path) -> str:
    direct = (os.getenv(name) or "").strip()
    if direct:
        return direct
    filename = (os.getenv(f"{name}_FILE") or "").strip()
    if not filename:
        return ""
    path = Path(filename)
    if not path.is_absolute():
        candidates = [env_file.parent / path, ROOT / path]
        path = next((candidate for candidate in candidates if candidate.is_file()), candidates[0])
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def resolve_device_index(pa: pyaudio.PyAudio, value: str, *, input_device: bool) -> int | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return int(value.split(":", 1)[0])
    except ValueError:
        pass
    key = "maxInputChannels" if input_device else "maxOutputChannels"
    matches = []
    for index in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(index)
        if int(info.get(key) or 0) <= 0:
            continue
        name = str(info.get("name") or "")
        if value.casefold() == name.casefold():
            return index
        if value.casefold() in name.casefold() or name.casefold() in value.casefold():
            matches.append(index)
    if len(matches) == 1:
        return matches[0]
    raise RuntimeError(f"could not map configured audio device {value!r} to one PyAudio device")


def device_info(pa: pyaudio.PyAudio, index: int | None, *, input_device: bool) -> dict:
    if index is not None:
        return pa.get_device_info_by_index(index)
    return pa.get_default_input_device_info() if input_device else pa.get_default_output_device_info()


def unique_ints(values) -> list[int]:
    result = []
    for value in values:
        try:
            item = int(round(float(value)))
        except (TypeError, ValueError):
            continue
        if item > 0 and item not in result:
            result.append(item)
    return result


def open_input_stream(pa: pyaudio.PyAudio, index: int | None):
    info = device_info(pa, index, input_device=True)
    max_channels = max(1, int(info.get("maxInputChannels") or 1))
    rates = unique_ints([REALTIME_RATE, info.get("defaultSampleRate"), 48000, 44100, 16000])
    channels_options = [1] + ([2] if max_channels >= 2 else [])
    errors = []
    for rate in rates:
        frames = max(160, int(rate * 0.02))
        for channels in channels_options:
            try:
                stream = pa.open(
                    format=pyaudio.paInt16,
                    channels=channels,
                    rate=rate,
                    input=True,
                    input_device_index=index,
                    frames_per_buffer=frames,
                )
                return stream, rate, channels, frames, str(info.get("name") or index or "default")
            except Exception as exc:
                errors.append(f"{channels}ch/{rate}: {exc}")
    raise RuntimeError("could not open realtime input: " + "; ".join(errors[-4:]))


def open_output_stream(pa: pyaudio.PyAudio, index: int | None):
    info = device_info(pa, index, input_device=False)
    max_channels = max(1, int(info.get("maxOutputChannels") or 1))
    rates = unique_ints([REALTIME_RATE, info.get("defaultSampleRate"), 48000, 44100])
    channels_options = [1] + ([2] if max_channels >= 2 else [])
    errors = []
    for rate in rates:
        for channels in channels_options:
            try:
                stream = pa.open(
                    format=pyaudio.paInt16,
                    channels=channels,
                    rate=rate,
                    output=True,
                    output_device_index=index,
                    frames_per_buffer=max(160, int(rate * 0.02)),
                )
                return stream, rate, channels, str(info.get("name") or index or "default")
            except Exception as exc:
                errors.append(f"{channels}ch/{rate}: {exc}")
    raise RuntimeError("could not open realtime output: " + "; ".join(errors[-4:]))


def open_configured_input(pa: pyaudio.PyAudio, selected: str):
    target = parse_pipewire_selector(selected, kind="source")
    if target:
        stream = PipeWireInputStream(target, rate=REALTIME_RATE, channels=1, chunk=480)
        return stream, REALTIME_RATE, 1, 480, f"PipeWire source {target}"
    index = resolve_device_index(pa, selected, input_device=True)
    return open_input_stream(pa, index)


def open_configured_output(pa: pyaudio.PyAudio, selected: str):
    target = parse_pipewire_selector(selected, kind="sink")
    if target:
        stream = PipeWireOutputStream(target, rate=REALTIME_RATE, channels=1)
        return stream, REALTIME_RATE, 1, f"PipeWire sink {target}"
    index = resolve_device_index(pa, selected, input_device=False)
    return open_output_stream(pa, index)


async def wait_until_ready(engine: OpenAIRealtimeEngine) -> None:
    while True:
        event = await asyncio.wait_for(engine.next_event(), timeout=15.0)
        if event.type == "ready":
            return
        if event.type in {"provider_error", "connection_error", "connection_closed"}:
            raise RuntimeError(f"Realtime session failed before ready: {event.data}")


async def capture_loop(engine, stream, source_rate: int, channels: int, frames: int, stop_event: asyncio.Event) -> None:
    resampler = Pcm16MonoResampler(source_rate, REALTIME_RATE)
    while not stop_event.is_set():
        try:
            pcm = await asyncio.to_thread(stream.read, frames, False)
        except Exception as exc:
            print(f"RV1 input error: {exc}", flush=True)
            stop_event.set()
            return
        mono = downmix_pcm16(pcm, channels)
        realtime_pcm = resampler.process(mono)
        if realtime_pcm:
            try:
                await engine.send_audio(realtime_pcm)
            except Exception as exc:
                print(f"RV1 send error: {exc}", flush=True)
                stop_event.set()
                return


async def playback_loop(
    playback_queue: asyncio.Queue,
    output_stream,
    output_rate: int,
    output_channels: int,
    interrupted_responses: set[str],
    first_audio_played: dict[str, float],
    stop_event: asyncio.Event,
) -> None:
    current_response_id = ""
    resampler = Pcm16MonoResampler(REALTIME_RATE, output_rate)
    while not stop_event.is_set():
        response_id, audio = await playback_queue.get()
        if response_id in interrupted_responses:
            continue
        if response_id != current_response_id:
            current_response_id = response_id
            resampler = Pcm16MonoResampler(REALTIME_RATE, output_rate)
        converted = resampler.process(audio)
        if not converted:
            continue
        converted = expand_pcm16_channels(converted, output_channels)
        if response_id not in first_audio_played:
            first_audio_played[response_id] = time.perf_counter()
        try:
            await asyncio.to_thread(output_stream.write, converted)
        except Exception as exc:
            print(f"RV1 output error: {exc}", flush=True)
            stop_event.set()
            return


def clear_playback_queue(playback_queue: asyncio.Queue) -> None:
    while True:
        try:
            playback_queue.get_nowait()
        except asyncio.QueueEmpty:
            return


async def event_loop(
    engine,
    playback_queue: asyncio.Queue,
    interrupted_responses: set[str],
    first_audio_played: dict[str, float],
    stop_event: asyncio.Event,
) -> None:
    current_response_id = ""
    speech_stopped_at: float | None = None
    response_started_at: dict[str, float] = {}
    speech_stop_by_response: dict[str, float] = {}
    first_audio_received: dict[str, float] = {}
    completed_response_ids: set[str] = set()
    turn_index = 0

    while not stop_event.is_set():
        event = await engine.next_event()
        now = time.perf_counter()
        if event.type == "speech_started":
            print("RV1 speech started", flush=True)
            if current_response_id:
                interrupted_responses.add(current_response_id)
                clear_playback_queue(playback_queue)
        elif event.type == "speech_stopped":
            speech_stopped_at = now
            print("RV1 speech stopped", flush=True)
        elif event.type == "response_started":
            response = event.data.get("response") or {}
            current_response_id = str(response.get("id") or "")
            response_started_at[current_response_id] = now
            if speech_stopped_at is not None:
                speech_stop_by_response[current_response_id] = speech_stopped_at
        elif event.type == "audio_delta":
            response_id = str(event.data.get("response_id") or current_response_id)
            if response_id in interrupted_responses:
                continue
            audio = event.data.get("audio") or b""
            if not audio:
                continue
            if response_id not in first_audio_received:
                first_audio_received[response_id] = now
            await playback_queue.put((response_id, audio))
        elif event.type == "transcript_done":
            text = str(event.data.get("text") or "").strip()
            if text:
                print(f"Assistant: {text}", flush=True)
        elif event.type == "response_done":
            response_id = str(event.data.get("response_id") or current_response_id)
            if response_id and response_id in completed_response_ids:
                continue
            if response_id:
                completed_response_ids.add(response_id)
            usage = event.data.get("usage") or {}
            turn_index += 1
            speech_end = speech_stop_by_response.get(response_id)
            first_received = first_audio_received.get(response_id)
            first_played = first_audio_played.get(response_id)
            metrics = {
                "schema": 1,
                "pipeline": "realtime",
                "provider": "openai",
                "model": engine.config.model,
                "turn": turn_index,
                "response_id": response_id,
                "status": event.data.get("status"),
                "interrupted": response_id in interrupted_responses,
                "speech_end_to_first_audio_ms": (
                    round((first_received - speech_end) * 1000.0, 3)
                    if speech_end is not None and first_received is not None
                    else None
                ),
                "speech_end_to_first_playback_ms": (
                    round((first_played - speech_end) * 1000.0, 3)
                    if speech_end is not None and first_played is not None
                    else None
                ),
                "speech_end_to_response_done_ms": (
                    round((now - speech_end) * 1000.0, 3) if speech_end is not None else None
                ),
                "response_start_to_done_ms": (
                    round((now - response_started_at[response_id]) * 1000.0, 3)
                    if response_id in response_started_at
                    else None
                ),
                "usage": usage,
                "cost_usd": realtime_usage_cost_usd(engine.config.model, usage),
            }
            print(RV1_METRICS_PREFIX + json.dumps(metrics, separators=(",", ":"), ensure_ascii=False), flush=True)
            if current_response_id == response_id:
                current_response_id = ""
        elif event.type in {"provider_error", "connection_error"}:
            print(f"RV1 provider error: {event.data}", flush=True)
        elif event.type == "connection_closed":
            print("RV1 connection closed", flush=True)
            stop_event.set()
            return


async def run(args) -> int:
    env_file = Path(args.env_file).resolve()
    load_dotenv(env_file, override=True)
    api_key = read_secret("OPENAI_API_KEY", env_file)
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY / OPENAI_API_KEY_FILE is not configured")

    pa = pyaudio.PyAudio()
    input_stream = output_stream = None
    engine = None
    stop_event = asyncio.Event()
    tasks: list[asyncio.Task] = []
    playback_queue: asyncio.Queue = asyncio.Queue()
    interrupted_responses: set[str] = set()
    first_audio_played: dict[str, float] = {}
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    try:
        input_selected = args.input_device if args.input_device is not None else os.getenv("BACKEND_AUDIO_INPUT_DEVICE", "")
        output_selected = args.output_device if args.output_device is not None else os.getenv("BACKEND_AUDIO_OUTPUT_DEVICE", "")
        input_stream, input_rate, input_channels, input_frames, input_name = open_configured_input(pa, input_selected)
        output_stream, output_rate, output_channels, output_name = open_configured_output(pa, output_selected)
        print(f"RV1 input selector: {input_selected or '<system default>'}", flush=True)
        print(f"RV1 output selector: {output_selected or '<system default>'}", flush=True)
        print(f"RV1 input: {input_name} {input_channels}ch/{input_rate}Hz -> PCM16 mono/24000Hz", flush=True)
        print(f"RV1 output: PCM16 mono/24000Hz -> {output_name} {output_channels}ch/{output_rate}Hz", flush=True)

        config = RealtimeEngineConfig(
            provider="openai",
            model=args.model,
            voice=args.voice,
            instructions=args.instructions,
            server_vad=True,
        )
        engine = OpenAIRealtimeEngine(config, api_key=api_key)
        await engine.start()
        await wait_until_ready(engine)
        print(f"RV1 connected: model={args.model} voice={args.voice}; no MCP tools loaded.", flush=True)
        print("Parle naturellement. Le serveur gère les tours et le barge-in. Ctrl+C pour arrêter.", flush=True)

        tasks = [
            asyncio.create_task(
                capture_loop(engine, input_stream, input_rate, input_channels, input_frames, stop_event),
                name="rv1-capture",
            ),
            asyncio.create_task(
                event_loop(engine, playback_queue, interrupted_responses, first_audio_played, stop_event),
                name="rv1-events",
            ),
            asyncio.create_task(
                playback_loop(
                    playback_queue,
                    output_stream,
                    output_rate,
                    output_channels,
                    interrupted_responses,
                    first_audio_played,
                    stop_event,
                ),
                name="rv1-playback",
            ),
        ]
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=args.duration)
        except asyncio.TimeoutError:
            stop_event.set()
        return 0
    finally:
        stop_event.set()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if engine is not None:
            await engine.stop()
        for stream in (input_stream, output_stream):
            if stream is not None:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
        pa.terminate()


def main() -> int:
    parser = argparse.ArgumentParser(description="RV1 isolated OpenAI Realtime audio spike")
    parser.add_argument("--env-file", default=".env.online")
    parser.add_argument("--model", default="gpt-realtime-2.1-mini")
    parser.add_argument("--voice", default="marin")
    parser.add_argument("--duration", type=float, default=600.0, help="maximum run duration in seconds")
    parser.add_argument("--input-device", default=None, help="diagnostic override; normally use BACKEND_AUDIO_INPUT_DEVICE")
    parser.add_argument("--output-device", default=None, help="diagnostic override; normally use BACKEND_AUDIO_OUTPUT_DEVICE")
    parser.add_argument(
        "--instructions",
        default="Tu es Live Stage Assistant. Réponds en français, brièvement et naturellement. Pour ce test RV1, tu n'as aucun outil et tu ne dois prétendre exécuter aucune action sur un équipement.",
    )
    args = parser.parse_args()
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"RV1 failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())