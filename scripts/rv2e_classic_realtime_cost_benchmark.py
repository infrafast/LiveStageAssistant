#!/usr/bin/env python3
"""RV2E functional Classic-vs-Realtime cost/latency benchmark.

One microphone recording is reused for both pipelines. Both paths use the same
configured local MCP server and the same configured LSA audio output. The
benchmark is deliberately read-only and uses the fixed spoken query
"Quel est le volume de clic ?".

Classic:
  configured mic -> Whisper -> gpt-4.1-mini + MCP STDIO -> OpenAI TTS -> configured output
Realtime:
  exact same PCM -> gpt-realtime-2.1 + same MCP STDIO -> configured output

Realtime cost uses provider response usage. Classic LLM cost uses actual token
usage, Whisper uses measured recorded duration, and Classic TTS is estimated
from the exact PCM duration returned by the speech endpoint.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
from pathlib import Path
import sys
import time
import wave
from typing import Any

import openai
import pyaudio
from dotenv import load_dotenv
from langchain_core.callbacks import BaseCallbackHandler
from langchain_openai import ChatOpenAI
from mcp_use import MCPAgent, MCPClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import rv2_stdio_mcp as stdio_runner
from voice_assistant.cost_metrics import estimate_openai_tts_cost_usd, text_usage_cost_usd, transcription_cost_usd
from voice_assistant.realtime.audio import Pcm16MonoResampler, downmix_pcm16, expand_pcm16_channels
from voice_assistant.realtime.engine import RealtimeEngineConfig
from voice_assistant.realtime.mcp_bridge import RealtimeMCPBridge
from voice_assistant.realtime.metrics import realtime_usage_cost_usd
from voice_assistant.realtime.openai_realtime import OpenAIRealtimeEngine
from voice_assistant.realtime.service import open_configured_input, open_configured_output, read_secret

RATE = 24000
TTS_PCM_RATE = 24000
DEFAULT_QUERY_HINT = "Quel est le volume de clic ?"
CLASSIC_SYSTEM_PROMPT = """You are Live Stage Assistant. Reply in French and keep the answer short.
Current external mixer state must be read through the available MCP tools before answering.
This benchmark is strictly read-only. Never change a level, mute, routing, scene, effect, or any
other external state. Do not invent a tool result.
"""
REALTIME_SYSTEM_PROMPT = """You are Live Stage Assistant in a strictly read-only benchmark.
Reply in French and keep the spoken answer short. Read the current mixer state through the
available function tools before answering. Never perform a write, level change, mute, routing
change, scene/effect change, or any other mutation. Call tools silently, then answer once.
"""


class UsageCallback(BaseCallbackHandler):
    def __init__(self) -> None:
        self.input_tokens = 0
        self.cached_input_tokens = 0
        self.output_tokens = 0
        self.calls = 0

    def on_llm_end(self, response, **kwargs: Any) -> None:  # noqa: ANN001
        self.calls += 1
        usage = dict((getattr(response, "llm_output", None) or {}).get("token_usage") or {})
        if usage:
            self.input_tokens += int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
            self.output_tokens += int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
            details = usage.get("prompt_tokens_details") or usage.get("input_token_details") or {}
            self.cached_input_tokens += int(details.get("cached_tokens") or 0)
            return
        for generation_list in getattr(response, "generations", None) or []:
            for generation in generation_list:
                message = getattr(generation, "message", None)
                metadata = getattr(message, "usage_metadata", None) or {}
                self.input_tokens += int(metadata.get("input_tokens") or 0)
                self.output_tokens += int(metadata.get("output_tokens") or 0)
                details = metadata.get("input_token_details") or {}
                self.cached_input_tokens += int(details.get("cache_read") or details.get("cached_tokens") or 0)


def wav_bytes(pcm: bytes, *, rate: int = RATE) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm)
    return buffer.getvalue()


def approx_text_tokens(text: str) -> int:
    return max(1, round(len(text) / 4.0)) if text else 0


def validate_fixed_query_transcript(text: str) -> None:
    normalized = " ".join(
        text.casefold()
        .replace("-", " ")
        .replace("_", " ")
        .replace("?", " ")
        .replace(".", " ")
        .replace(",", " ")
        .split()
    )
    variants = ("clic", "click", "clique")
    if "volume" not in normalized or not any(variant in normalized.split() for variant in variants):
        raise RuntimeError(
            "recorded speech was not recognized as the benchmark query; "
            f"got {text!r}. No cost comparison produced."
        )


def filtered_mcp_config(config: dict, server: str) -> dict:
    servers = config.get("mcpServers") or {}
    if server not in servers:
        raise RuntimeError(f"MCP server {server!r} not found")
    entry = dict(servers[server])
    entry.pop("native", None)
    entry.pop("realtime", None)
    return {"mcpServers": {server: entry}}


def local_mcp_target(config: dict, server: str) -> dict[str, str]:
    entry = (config.get("mcpServers") or {}).get(server) or {}
    env = entry.get("env") or {}
    return {
        "host": str(env.get("OSC_HOST") or ""),
        "port": str(env.get("OSC_PORT") or ""),
        "protocol": str(env.get("OSC_PROTOCOL") or ""),
        "transport": "stdio/local",
    }


async def capture_once(seconds: float, selected: str) -> tuple[bytes, dict[str, Any]]:
    pa = pyaudio.PyAudio()
    stream = None
    try:
        stream, source_rate, channels, frames, name = open_configured_input(pa, selected)
        resampler = Pcm16MonoResampler(source_rate, RATE)
        chunks: list[bytes] = []
        deadline = time.monotonic() + seconds
        print(f"RV2E_AUDIO input_selector={selected or '<default>'} resolved_input={name}", flush=True)
        print(f"RV2E_RECORD Speak now for {seconds:.1f}s: {DEFAULT_QUERY_HINT}", flush=True)
        while time.monotonic() < deadline:
            raw = await asyncio.to_thread(stream.read, frames, False)
            converted = resampler.process(downmix_pcm16(raw, channels))
            if converted:
                chunks.append(converted)
        pcm = b"".join(chunks)
        duration = len(pcm) / 2.0 / RATE
        print(f"RV2E_RECORD done duration_s={duration:.3f} device={name}", flush=True)
        return pcm, {"duration_s": duration, "device": name, "selector": selected, "rate": RATE}
    finally:
        if stream is not None:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass
        pa.terminate()


async def write_pcm(stream, pcm: bytes, source_rate: int, output_rate: int, output_channels: int) -> None:
    if not pcm:
        return
    resampler = Pcm16MonoResampler(source_rate, output_rate)
    converted = resampler.process(pcm)
    converted = expand_pcm16_channels(converted, output_channels)
    if converted:
        await asyncio.to_thread(stream.write, converted)


async def run_classic(
    api_key: str,
    raw_config: dict,
    server: str,
    pcm: bytes,
    audio_seconds: float,
    output_stream,
    output_rate: int,
    output_channels: int,
) -> dict:
    started = time.perf_counter()
    client = openai.OpenAI(api_key=api_key)
    stt_kwargs: dict[str, Any] = {
        "model": "whisper-1",
        "file": ("rv2e.wav", wav_bytes(pcm), "audio/wav"),
        "language": str(os.getenv("STT_LANGUAGE") or "fr").strip(),
    }
    stt_prompt = str(os.getenv("STT_PROMPT") or "").strip()
    if stt_prompt:
        stt_kwargs["prompt"] = stt_prompt

    stt_started = time.perf_counter()
    stt_response = await asyncio.to_thread(client.audio.transcriptions.create, **stt_kwargs)
    transcript = str(stt_response.text or "").strip()
    stt_ms = (time.perf_counter() - stt_started) * 1000.0
    if not transcript:
        raise RuntimeError("Classic STT returned an empty transcript")
    print(f"RV2E_CLASSIC transcript={transcript!r}", flush=True)
    validate_fixed_query_transcript(transcript)

    usage = UsageCallback()
    model = str(os.getenv("OPENAI_MODEL") or "gpt-4.1-mini").strip()
    llm = ChatOpenAI(model=model, api_key=api_key, callbacks=[usage])
    mcp_client = MCPClient.from_dict(filtered_mcp_config(raw_config, server))
    agent = MCPAgent(llm=llm, client=mcp_client, max_steps=12, memory_enabled=False, system_prompt=CLASSIC_SYSTEM_PROMPT)
    llm_started = time.perf_counter()
    try:
        answer_obj = await agent.run(transcript, max_steps=12)
        answer = answer_obj if isinstance(answer_obj, str) else str(answer_obj)
    finally:
        try:
            await mcp_client.close_all_sessions()
        except Exception:
            pass
    llm_mcp_ms = (time.perf_counter() - llm_started) * 1000.0
    answer = answer.strip()
    if not answer:
        raise RuntimeError("Classic agent returned an empty answer")
    print(f"RV2E_CLASSIC answer={answer!r}", flush=True)

    tts_model = str(os.getenv("WEB_TTS_MODEL") or os.getenv("OPENAI_TTS_MODEL") or "gpt-4o-mini-tts").strip()
    tts_voice = str(os.getenv("WEB_TTS_VOICE") or os.getenv("OPENAI_TTS_VOICE") or "alloy").strip()
    tts_started = time.perf_counter()
    speech = await asyncio.to_thread(
        client.audio.speech.create,
        model=tts_model,
        voice=tts_voice,
        input=answer,
        response_format="pcm",
    )
    generated_pcm = speech.read() if hasattr(speech, "read") else bytes(getattr(speech, "content", b""))
    tts_api_ms = (time.perf_counter() - tts_started) * 1000.0
    generated_seconds = len(generated_pcm) / 2.0 / TTS_PCM_RATE
    playback_started = time.perf_counter()
    await write_pcm(output_stream, generated_pcm, TTS_PCM_RATE, output_rate, output_channels)
    playback_ms = (time.perf_counter() - playback_started) * 1000.0

    stt_cost = transcription_cost_usd("whisper-1", audio_seconds) or 0.0
    llm_cost = text_usage_cost_usd(
        model,
        input_tokens=usage.input_tokens,
        cached_input_tokens=usage.cached_input_tokens,
        output_tokens=usage.output_tokens,
    )
    tts_cost = estimate_openai_tts_cost_usd(
        text_input_tokens=approx_text_tokens(answer),
        generated_audio_seconds=generated_seconds,
    )
    total = stt_cost + (llm_cost or 0.0) + float(tts_cost["cost_usd"])
    return {
        "pipeline": "classic",
        "transcript": transcript,
        "answer": answer,
        "models": {"stt": "whisper-1", "llm": model, "tts": tts_model},
        "latency_ms": {
            "stt": round(stt_ms, 3),
            "llm_mcp": round(llm_mcp_ms, 3),
            "tts_api": round(tts_api_ms, 3),
            "tts_playback": round(playback_ms, 3),
            "post_capture_total": round((time.perf_counter() - started) * 1000.0, 3),
        },
        "usage": {
            "llm_calls": usage.calls,
            "llm_input_tokens": usage.input_tokens,
            "llm_cached_input_tokens": usage.cached_input_tokens,
            "llm_output_tokens": usage.output_tokens,
            "stt_audio_seconds": round(audio_seconds, 3),
            "tts_audio_seconds": round(generated_seconds, 3),
        },
        "cost_usd": {
            "stt_measured_duration": stt_cost,
            "llm_measured_tokens": llm_cost,
            "tts_estimated_from_exact_pcm_duration": tts_cost,
            "total_with_tts_estimate": total,
        },
    }


async def wait_ready(engine: OpenAIRealtimeEngine, timeout: float = 20.0) -> None:
    while True:
        event = await asyncio.wait_for(engine.next_event(), timeout=timeout)
        if event.type == "ready":
            return
        if event.type in {"provider_error", "connection_error", "connection_closed"}:
            raise RuntimeError(f"Realtime failed before ready: {event.type} {event.data}")


async def run_realtime(
    api_key: str,
    raw_config: dict,
    server: str,
    pcm: bytes,
    output_stream,
    output_rate: int,
    output_channels: int,
) -> dict:
    bridge_config = filtered_mcp_config(raw_config, server)
    bridge = RealtimeMCPBridge(bridge_config, server_names=(server,))
    engine: OpenAIRealtimeEngine | None = None
    started = time.perf_counter()
    try:
        function_tools = await bridge.start()
        if not function_tools:
            raise RuntimeError(f"Realtime bridge discovered no tools for {server!r}")
        model = str(os.getenv("OPENAI_REALTIME_MODEL") or "gpt-realtime-2.1").strip()
        voice = str(os.getenv("OPENAI_REALTIME_VOICE") or "marin").strip()
        engine = OpenAIRealtimeEngine(
            RealtimeEngineConfig(
                provider="openai",
                model=model,
                voice=voice,
                instructions=REALTIME_SYSTEM_PROMPT,
                server_vad=True,
                function_tools=tuple(function_tools),
            ),
            api_key=api_key,
        )
        await engine.start()
        await wait_ready(engine)

        chunk_bytes = int(RATE * 0.02) * 2
        for offset in range(0, len(pcm), chunk_bytes):
            await engine.send_audio(pcm[offset : offset + chunk_bytes])
            await asyncio.sleep(0.02)
        silence = b"\x00\x00" * int(RATE * 0.8)
        for offset in range(0, len(silence), chunk_bytes):
            await engine.send_audio(silence[offset : offset + chunk_bytes])
            await asyncio.sleep(0.02)
        post_audio_started = time.perf_counter()

        transcript = ""
        answer = ""
        tool_calls = 0
        response_costs: list[float] = []
        usages: list[dict[str, Any]] = []
        audio_output_bytes = 0
        first_audio_ms: float | None = None
        final_response_seen = False
        output_resampler = Pcm16MonoResampler(RATE, output_rate)
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            event = await asyncio.wait_for(engine.next_event(), timeout=max(0.1, deadline - time.monotonic()))
            if event.type == "user_transcript_done":
                transcript = str(event.data.get("text") or "").strip()
                print(f"RV2E_REALTIME transcript={transcript!r}", flush=True)
                if transcript:
                    validate_fixed_query_transcript(transcript)
            elif event.type == "tool_call":
                tool_calls += 1
                name = str(event.data.get("name") or "")
                call_id = str(event.data.get("call_id") or "")
                arguments = str(event.data.get("arguments") or "{}")
                target = bridge.tool_targets.get(name)
                if target is None:
                    raise RuntimeError(f"Realtime called unknown bridge function {name!r}")
                result = await bridge.execute(name, arguments)
                if result.get("is_error"):
                    raise RuntimeError(f"Realtime MCP tool failed: {result}")
                await engine.submit_tool_result(call_id, result)
            elif event.type == "audio_delta":
                audio = event.data.get("audio") or b""
                if audio:
                    if first_audio_ms is None:
                        first_audio_ms = (time.perf_counter() - post_audio_started) * 1000.0
                    audio_output_bytes += len(audio)
                    converted = output_resampler.process(audio)
                    converted = expand_pcm16_channels(converted, output_channels)
                    if converted:
                        await asyncio.to_thread(output_stream.write, converted)
            elif event.type == "transcript_done":
                text = str(event.data.get("text") or "").strip()
                if text:
                    answer = text
                    print(f"RV2E_REALTIME answer={answer!r}", flush=True)
            elif event.type == "response_done":
                final_response_seen = True
                usage = event.data.get("usage") or {}
                usages.append(usage)
                cost = realtime_usage_cost_usd(model, usage)
                if cost is not None:
                    response_costs.append(cost)
                if answer and tool_calls:
                    break
            elif event.type in {"provider_error", "connection_error", "connection_closed"}:
                raise RuntimeError(f"Realtime session failed: {event.type} {event.data}")
        else:
            raise RuntimeError("Realtime benchmark timed out")

        if not transcript or not answer or not tool_calls or not final_response_seen:
            raise RuntimeError(
                f"Realtime benchmark incomplete transcript={bool(transcript)} answer={bool(answer)} "
                f"tool_calls={tool_calls} final={final_response_seen}"
            )

        total_cost = sum(response_costs)
        return {
            "pipeline": "realtime",
            "transcript": transcript,
            "answer": answer,
            "models": {"realtime": model},
            "latency_ms": {
                "post_audio_to_first_audio": round(first_audio_ms, 3) if first_audio_ms is not None else None,
                "post_audio_to_final_response": round((time.perf_counter() - post_audio_started) * 1000.0, 3),
                "full_function_start_to_final_response": round((time.perf_counter() - started) * 1000.0, 3),
            },
            "usage": {
                "responses": len(usages),
                "tool_calls": tool_calls,
                "input_tokens": sum(int(item.get("input_tokens") or 0) for item in usages),
                "output_tokens": sum(int(item.get("output_tokens") or 0) for item in usages),
                "responses_detail": usages,
                "audio_output_seconds": round(audio_output_bytes / 2.0 / RATE, 3),
            },
            "cost_usd": {
                "measured_provider_usage": total_cost,
                "response_costs": response_costs,
            },
        }
    finally:
        if engine is not None:
            await engine.stop()
        await bridge.close()


async def run(args) -> int:
    env_file = Path(args.env_file).resolve()
    load_dotenv(env_file, override=True)
    api_key = read_secret("OPENAI_API_KEY", env_file)
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY / OPENAI_API_KEY_FILE is not configured")

    class BridgeArgs:
        mcp_config = args.mcp_config
        mcp_server = args.server

    config_path, raw_config = stdio_runner.load_mcp_config(BridgeArgs(), env_file)
    input_selector = str(args.input_device if args.input_device is not None else (os.getenv("BACKEND_AUDIO_INPUT_DEVICE") or ""))
    output_selector = str(args.output_device if args.output_device is not None else (os.getenv("BACKEND_AUDIO_OUTPUT_DEVICE") or ""))
    print("RV2E_CONFIG " + json.dumps({
        "server": args.server,
        "mcp_config": str(config_path),
        "mcp_target": local_mcp_target(raw_config, args.server),
        "record_seconds": args.seconds,
        "input_selector": input_selector or "<default>",
        "output_selector": output_selector or "<default>",
    }, ensure_ascii=False, separators=(",", ":")), flush=True)

    pcm, recording = await capture_once(args.seconds, input_selector)

    pa = pyaudio.PyAudio()
    output_stream = None
    try:
        output_stream, output_rate, output_channels, output_name = open_configured_output(pa, output_selector)
        print(f"RV2E_AUDIO output_selector={output_selector or '<default>'} resolved_output={output_name} channels={output_channels} rate={output_rate}", flush=True)
        classic = await run_classic(api_key, raw_config, args.server, pcm, recording["duration_s"], output_stream, output_rate, output_channels)
        realtime = await run_realtime(api_key, raw_config, args.server, pcm, output_stream, output_rate, output_channels)
        comparison = {
            "recording": recording,
            "audio_output": {"device": output_name, "selector": output_selector, "rate": output_rate, "channels": output_channels},
            "mcp_target": local_mcp_target(raw_config, args.server),
            "classic": classic,
            "realtime": realtime,
            "cost_ratio_realtime_over_classic": (
                realtime["cost_usd"]["measured_provider_usage"] / classic["cost_usd"]["total_with_tts_estimate"]
                if classic["cost_usd"]["total_with_tts_estimate"] > 0 else None
            ),
        }
        print("RV2E_CLASSIC_RESULT " + json.dumps(classic, ensure_ascii=False, separators=(",", ":")), flush=True)
        print("RV2E_REALTIME_RESULT " + json.dumps(realtime, ensure_ascii=False, separators=(",", ":")), flush=True)
        print("RV2E_COST_COMPARISON " + json.dumps(comparison, ensure_ascii=False, separators=(",", ":")), flush=True)
        return 0
    finally:
        if output_stream is not None:
            try:
                output_stream.stop_stream()
                output_stream.close()
            except Exception:
                pass
        pa.terminate()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env.online")
    parser.add_argument("--mcp-config", default=None)
    parser.add_argument("--server", default="mixer")
    parser.add_argument("--seconds", type=float, default=3.5)
    parser.add_argument("--input-device", default=None)
    parser.add_argument("--output-device", default=None)
    args = parser.parse_args()
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"RV2E failed: {type(exc).__name__}: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
