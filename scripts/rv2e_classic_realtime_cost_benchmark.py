#!/usr/bin/env python3
"""RV2E functional Classic-vs-Realtime cost/latency benchmark.

One microphone recording is reused for both pipelines. Both paths use the same
configured local MCP server. The script performs read-oriented voice queries;
do not use it with a mutation request.

Classic path:
  recorded PCM -> OpenAI Whisper -> gpt-4.1-mini + MCPAgent/STDIO -> OpenAI TTS
Realtime path:
  same recorded PCM -> gpt-realtime-2.1 + Realtime STDIO bridge -> audio output

Realtime costs use provider response usage. Classic LLM costs use actual
LangChain/OpenAI token usage, Whisper uses measured input duration, and Classic
TTS is explicitly estimated from measured generated WAV duration because the
speech endpoint used here does not expose per-request token usage.
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
from voice_assistant.cost_metrics import (
    estimate_openai_tts_cost_usd,
    text_usage_cost_usd,
    transcription_cost_usd,
)
from voice_assistant.realtime.audio import Pcm16MonoResampler, downmix_pcm16
from voice_assistant.realtime.engine import RealtimeEngineConfig
from voice_assistant.realtime.mcp_bridge import RealtimeMCPBridge
from voice_assistant.realtime.metrics import realtime_usage_cost_usd
from voice_assistant.realtime.openai_realtime import OpenAIRealtimeEngine
from voice_assistant.realtime.service import open_configured_input, read_secret

RATE = 24000
DEFAULT_QUERY_HINT = "Quel est le volume de Claude ?"
CLASSIC_SYSTEM_PROMPT = """You are Live Stage Assistant. Reply in French. Keep the answer short.
Current external mixer state must be read through the available MCP tools before answering.
For this benchmark perform read-only inspection only. Never change a level, mute, routing, scene,
or any other external state. Do not invent a tool result.
"""
REALTIME_SYSTEM_PROMPT = """You are Live Stage Assistant in a read-only cost benchmark.
Reply in French and keep the spoken answer short. Use the available function tools to read the
current mixer state before answering. Never perform a write, level change, mute, routing change,
or any other mutation. Do not invent a tool result. Call tools silently, then answer once.
"""


class UsageCallback(BaseCallbackHandler):
    """Accumulate token usage reported by ChatOpenAI across agent/tool turns."""

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


def wav_duration_seconds(data: bytes) -> float:
    with wave.open(io.BytesIO(data), "rb") as handle:
        return handle.getnframes() / float(handle.getframerate())


def approx_text_tokens(text: str) -> int:
    # Used only for the tiny TTS text-input estimate; LLM tokens are measured.
    return max(1, round(len(text) / 4.0)) if text else 0


async def capture_once(seconds: float) -> tuple[bytes, dict[str, Any]]:
    pa = pyaudio.PyAudio()
    stream = None
    try:
        selected = str(os.getenv("BACKEND_AUDIO_INPUT_DEVICE") or "")
        stream, source_rate, channels, frames, name = open_configured_input(pa, selected)
        resampler = Pcm16MonoResampler(source_rate, RATE)
        chunks: list[bytes] = []
        deadline = time.monotonic() + seconds
        print(f"RV2E_RECORD Speak now for {seconds:.1f}s: {DEFAULT_QUERY_HINT}", flush=True)
        while time.monotonic() < deadline:
            raw = await asyncio.to_thread(stream.read, frames, False)
            converted = resampler.process(downmix_pcm16(raw, channels))
            if converted:
                chunks.append(converted)
        pcm = b"".join(chunks)
        duration = len(pcm) / 2.0 / RATE
        print(f"RV2E_RECORD done duration_s={duration:.3f} device={name}", flush=True)
        return pcm, {"duration_s": duration, "device": name, "rate": RATE}
    finally:
        if stream is not None:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass
        pa.terminate()


def filtered_mcp_config(config: dict, server: str) -> dict:
    servers = config.get("mcpServers") or {}
    if server not in servers:
        raise RuntimeError(f"MCP server {server!r} not found")
    entry = dict(servers[server])
    # mcp-use/local Classic must not consume realtime/native policy blocks.
    entry.pop("native", None)
    entry.pop("realtime", None)
    return {"mcpServers": {server: entry}}


async def run_classic(api_key: str, env_file: Path, raw_config: dict, server: str, pcm: bytes, audio_seconds: float) -> dict:
    started = time.perf_counter()
    client = openai.OpenAI(api_key=api_key)
    wav = wav_bytes(pcm)
    stt_started = time.perf_counter()
    stt_response = await asyncio.to_thread(
        client.audio.transcriptions.create,
        model="whisper-1",
        file=("rv2e.wav", wav, "audio/wav"),
        language="fr",
    )
    transcript = str(stt_response.text or "").strip()
    stt_ms = (time.perf_counter() - stt_started) * 1000.0
    if not transcript:
        raise RuntimeError("Classic STT returned an empty transcript")
    print(f"RV2E_CLASSIC transcript={transcript!r}", flush=True)

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
        response_format="wav",
    )
    if hasattr(speech, "read"):
        generated_wav = speech.read()
    else:
        generated_wav = bytes(getattr(speech, "content", b""))
    tts_ms = (time.perf_counter() - tts_started) * 1000.0
    generated_seconds = wav_duration_seconds(generated_wav)

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
    known_total = stt_cost + (llm_cost or 0.0) + float(tts_cost["cost_usd"])
    return {
        "pipeline": "classic",
        "transcript": transcript,
        "answer": answer,
        "models": {"stt": "whisper-1", "llm": model, "tts": tts_model},
        "latency_ms": {
            "stt": round(stt_ms, 3),
            "llm_mcp": round(llm_mcp_ms, 3),
            "tts": round(tts_ms, 3),
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
            "tts_estimated": tts_cost,
            "total_with_tts_estimate": known_total,
        },
    }


async def wait_ready(engine: OpenAIRealtimeEngine, timeout: float = 20.0) -> None:
    while True:
        event = await asyncio.wait_for(engine.next_event(), timeout=timeout)
        if event.type == "ready":
            return
        if event.type in {"provider_error", "connection_error", "connection_closed"}:
            raise RuntimeError(f"Realtime failed before ready: {event.type} {event.data}")


async def run_realtime(api_key: str, raw_config: dict, server: str, pcm: bytes) -> dict:
    bridge = RealtimeMCPBridge(raw_config, server_names=(server,))
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

        # Feed the exact same recorded PCM at natural timing, then enough silence
        # for provider VAD to close the turn.
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
        final_response_seen = False
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            event = await asyncio.wait_for(engine.next_event(), timeout=max(0.1, deadline - time.monotonic()))
            if event.type == "user_transcript_done":
                transcript = str(event.data.get("text") or "").strip()
                print(f"RV2E_REALTIME transcript={transcript!r}", flush=True)
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
                audio_output_bytes += len(event.data.get("audio") or b"")
            elif event.type == "transcript_done":
                text = str(event.data.get("text") or "").strip()
                if text:
                    answer = text
                    print(f"RV2E_REALTIME answer={answer!r}", flush=True)
            elif event.type == "response_done":
                usage = event.data.get("usage") or {}
                usages.append(usage)
                cost = realtime_usage_cost_usd(model, usage)
                if cost is not None:
                    response_costs.append(cost)
                if answer and tool_calls:
                    final_response_seen = True
                    break
            elif event.type in {"provider_error", "connection_error", "connection_closed"}:
                raise RuntimeError(f"Realtime session failed: {event.type} {event.data}")

        if not final_response_seen:
            raise RuntimeError("Realtime benchmark did not reach a final tool-backed spoken response")
        post_audio_ms = (time.perf_counter() - post_audio_started) * 1000.0
        audio_seconds = audio_output_bytes / 2.0 / RATE

        aggregate = {
            "input_tokens": sum(int(item.get("input_tokens") or 0) for item in usages),
            "output_tokens": sum(int(item.get("output_tokens") or 0) for item in usages),
        }
        return {
            "pipeline": "realtime",
            "transcript": transcript,
            "answer": answer,
            "models": {"realtime": model},
            "latency_ms": {
                "post_audio_to_final_response": round(post_audio_ms, 3),
                "session_work_total": round((time.perf_counter() - started) * 1000.0, 3),
            },
            "usage": {
                "responses": len(usages),
                "tool_calls": tool_calls,
                "input_tokens": aggregate["input_tokens"],
                "output_tokens": aggregate["output_tokens"],
                "audio_output_seconds": round(audio_seconds, 3),
                "responses_detail": usages,
            },
            "cost_usd": {
                "measured_provider_usage": sum(response_costs),
                "response_costs": response_costs,
            },
        }
    finally:
        if engine is not None:
            await engine.stop()
        await bridge.close()


def comparison(classic: dict, realtime: dict) -> dict:
    classic_total = float(classic["cost_usd"]["total_with_tts_estimate"])
    realtime_total = float(realtime["cost_usd"]["measured_provider_usage"])
    return {
        "classic_cost_usd": classic_total,
        "realtime_cost_usd": realtime_total,
        "realtime_minus_classic_usd": realtime_total - classic_total,
        "realtime_over_classic_ratio": (realtime_total / classic_total) if classic_total > 0 else None,
        "per_100_requests_usd": {"classic": classic_total * 100.0, "realtime": realtime_total * 100.0},
        "per_1000_requests_usd": {"classic": classic_total * 1000.0, "realtime": realtime_total * 1000.0},
        "classic_post_capture_ms": classic["latency_ms"]["post_capture_total"],
        "realtime_post_audio_ms": realtime["latency_ms"]["post_audio_to_final_response"],
        "note": "Classic TTS component is estimated from generated audio duration; all Realtime cost uses provider usage.",
    }


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
    print(
        "RV2E_CONFIG "
        + json.dumps(
            {"server": args.server, "mcp_config": str(config_path), "record_seconds": args.record_seconds},
            separators=(",", ":"),
        ),
        flush=True,
    )

    pcm, recording = await capture_once(args.record_seconds)
    classic = await run_classic(api_key, env_file, raw_config, args.server, pcm, recording["duration_s"])
    print("RV2E_CLASSIC_RESULT " + json.dumps(classic, ensure_ascii=False, separators=(",", ":")), flush=True)
    realtime = await run_realtime(api_key, raw_config, args.server, pcm)
    print("RV2E_REALTIME_RESULT " + json.dumps(realtime, ensure_ascii=False, separators=(",", ":")), flush=True)
    report = {"recording": recording, "classic": classic, "realtime": realtime, "comparison": comparison(classic, realtime)}
    print("RV2E_COST_COMPARISON " + json.dumps(report, ensure_ascii=False, separators=(",", ":")), flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env.online")
    parser.add_argument("--mcp-config", default=None)
    parser.add_argument("--server", default="mixer")
    parser.add_argument("--record-seconds", type=float, default=3.5)
    args = parser.parse_args()
    if args.record_seconds < 1.0:
        parser.error("--record-seconds must be >= 1")
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"RV2E failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
