"""Web-free Classic/Local engine construction for the common LSA runtime.

This module deliberately owns no HTTP server and imports no WebMonitor. It
reuses ``VoiceAssistant`` as the Classic speech/LLM/MCP engine library while
``voice_assistant.runtime`` remains the sole owner of the production GUI.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
from typing import Any

from dotenv import dotenv_values, load_dotenv

from . import agent
from .session_context import DEFAULT_CONTEXT_DIR, DEFAULT_SUMMARY_MAX_CHARS, SessionContextStore
from .speaker_recognition import SpeakerProfile
from .wake_word import parse_wake_words


def _bool(values: dict[str, Any], name: str, default: bool = False) -> bool:
    raw = values.get(name)
    if raw in (None, ""):
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _int(values: dict[str, Any], name: str, default: int) -> int:
    raw = values.get(name)
    if raw in (None, ""):
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        return default


def _float(values: dict[str, Any], name: str, default: float) -> float:
    raw = values.get(name)
    if raw in (None, ""):
        return default
    try:
        return float(str(raw).strip())
    except ValueError:
        return default


def _secret(values: dict[str, Any], name: str) -> str | None:
    path = str(values.get(f"{name}_FILE") or "").strip()
    if not path:
        return None
    try:
        value = Path(path).expanduser().read_text(encoding="utf-8").strip()
    except OSError as error:
        raise RuntimeError(f"could not read {name}_FILE '{path}': {error}") from error
    return value or None


def _mcp_config(values: dict[str, Any], env_file: Path) -> dict[str, Any] | None:
    configured = str(values.get("MCP_CONFIG") or "").strip()
    if not configured:
        return None
    path = Path(os.path.expandvars(configured)).expanduser()
    if not path.is_absolute():
        candidates = (env_file.parent / path, Path.cwd() / path)
        path = next((candidate for candidate in candidates if candidate.is_file()), candidates[-1])
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise RuntimeError(f"could not read MCP_CONFIG '{path}': {error}") from error
    except json.JSONDecodeError as error:
        raise RuntimeError(f"invalid JSON in MCP_CONFIG '{path}': {error}") from error
    if not isinstance(payload, dict):
        raise RuntimeError(f"MCP_CONFIG '{path}' must contain a JSON object")
    servers = payload.get("mcpServers")
    if isinstance(servers, dict):
        payload = dict(payload)
        payload["mcpServers"] = {name: entry for name, entry in servers.items() if not isinstance(entry, dict) or entry.get("enabled", True) is not False}
    return payload


def _speaker_slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", value.strip().lower()).strip("._-")[:64] or "speaker"


def _speaker_profiles(values: dict[str, Any]) -> list[SpeakerProfile]:
    maximum = max(0, min(5, _int(values, "SPEAKER_PROFILES_MAX", 5)))
    root = Path(str(values.get("SPEAKER_PROFILES_DIR") or "data/speaker_profiles").strip())
    profiles: list[SpeakerProfile] = []
    for index in range(1, maximum + 1):
        name = str(values.get(f"SPEAKER_PROFILE_{index}_NAME") or "").strip()
        enabled = _bool(values, f"SPEAKER_PROFILE_{index}_ENABLED", False)
        if not name and not enabled:
            continue
        resolved_name = name or f"speaker_{index}"
        profiles.append(
            SpeakerProfile(
                name=resolved_name,
                wav_paths=[root / f"profil{index}_{sample}.wav" for sample in range(1, 4)],
                enabled=enabled,
                slug=_speaker_slug(resolved_name),
            )
        )
    return profiles


def build_assistant(env_file: str | Path) -> agent.VoiceAssistant:
    """Build one Classic/Local engine from a profile without constructing a GUI."""
    path = Path(env_file).expanduser().resolve()
    values: dict[str, Any] = dict(dotenv_values(path))
    load_dotenv(path, override=True)

    connectivity = str(values.get("CONNECTIVITY_MODE") or "online").strip().lower()
    offline = connectivity == "offline"
    tts_config = agent.resolve_tts_config_from_values(values)
    wake_words = parse_wake_words(str(values.get("WAKE_WORD") or "").strip() or None)
    monitor_mode = agent.normalize_backend_audio_monitor_mode(str(values.get("BACKEND_AUDIO_MONITOR_MODE") or "off"))
    if monitor_mode == "rejected" and not wake_words:
        monitor_mode = "off"

    backend_wake_vad_raw = str(values.get("BACKEND_WAKE_WORD_VAD_THRESHOLD") or "").strip()
    backend_wake_vad = _float(values, "BACKEND_WAKE_WORD_VAD_THRESHOLD", 0.0) if backend_wake_vad_raw else None
    command_ack = str(values.get("COMMAND_ACK_SOUND_FILE") or "").strip()
    if not command_ack and _bool(values, "COMMAND_ACK_SOUND_ENABLED", False):
        command_ack = "ring.wav"

    session_dir = str(values.get("SESSION_CONTEXT_DIR") or DEFAULT_CONTEXT_DIR).strip() or str(DEFAULT_CONTEXT_DIR)
    context_store = SessionContextStore(session_dir, summary_max_chars=DEFAULT_SUMMARY_MAX_CHARS)
    speaker_profiles = _speaker_profiles(values)

    cloud_gain = max(0.0, min(2.0, _float(values, "CLOUD_TTS_OUTPUT_GAIN", _float(values, "BACKEND_TTS_VOLUME", 1.0))))
    local_gain = max(0.0, min(2.0, _float(values, "LOCAL_TTS_OUTPUT_GAIN", _float(values, "BACKEND_TTS_VOLUME", 1.0))))
    speech_gain = local_gain if tts_config.backend_provider == "piper" or offline else cloud_gain

    assistant = agent.VoiceAssistant(
        openai_api_key=_secret(values, "OPENAI_API_KEY"),
        elevenlabs_api_key=_secret(values, "ELEVENLABS_API_KEY"),
        model=str(values.get("OFFLINE_MODEL") or values.get("OLLAMA_MODEL") or agent.DEFAULT_OLLAMA_MODEL).strip()
        if offline
        else str(values.get("OPENAI_MODEL") or "gpt-4.1-mini").strip(),
        llm_provider="ollama" if offline else str(values.get("LLM_PROVIDER") or "openai").strip().lower(),
        ollama_base_url=str(values.get("OLLAMA_BASE_URL") or "http://localhost:11434").strip(),
        stt_provider="local-whisper" if offline else str(values.get("STT_PROVIDER") or "openai-whisper").strip().lower(),
        local_whisper_model=str(values.get("LOCAL_WHISPER_MODEL") or "base").strip(),
        stt_language=str(values.get("STT_LANGUAGE") or "fr").strip(),
        stt_prompt=str(values.get("STT_PROMPT") or agent.DEFAULT_STT_PROMPT).strip(),
        stt_timeout_seconds=max(1.0, _float(values, "STT_TIMEOUT_SECONDS", agent.DEFAULT_STT_TIMEOUT_SECONDS)),
        tts_provider=tts_config.backend_provider,
        web_tts_enabled=False,
        elevenlabs_voice_id=str(values.get("ELEVENLABS_VOICE_ID") or agent.DEFAULT_ELEVENLABS_VOICE_ID).strip(),
        thinking_sound_file=str(values.get("THINKING_SOUND_FILE") or "thinking.wav").strip(),
        listening_sound_file=str(values.get("LISTENING_SOUND_FILE") or "").strip(),
        wake_detected_sound_file=str(values.get("WAKE_DETECTED_SOUND_FILE") or "").strip(),
        startup_loader_sound_enabled=_bool(values, "STARTUP_LOADER_SOUND_ENABLED", False),
        startup_loader_sound_file=str(values.get("STARTUP_LOADER_SOUND_FILE") or "loader.wav").strip(),
        command_ack_sound_file=command_ack,
        backend_audio_input_device=str(values.get("BACKEND_AUDIO_INPUT_DEVICE") or "").strip(),
        backend_audio_input_gain=max(0.5, min(2.0, _float(values, "BACKEND_AUDIO_INPUT_GAIN", 1.0))),
        backend_audio_output_device=str(values.get("BACKEND_AUDIO_OUTPUT_DEVICE") or "").strip(),
        vad_model_path=str(values.get("VAD_MODEL_PATH") or agent.DEFAULT_SILERO_VAD_MODEL).strip(),
        vad_speech_threshold=_float(values, "VAD_SPEECH_THRESHOLD", 0.5),
        vad_negative_threshold=_float(values, "VAD_NEGATIVE_THRESHOLD", 0.35),
        vad_min_speech_ms=_int(values, "VAD_MIN_SPEECH_MS", 250),
        vad_min_silence_ms=_int(values, "VAD_MIN_SILENCE_MS", 650),
        vad_speech_pad_ms=_int(values, "VAD_SPEECH_PAD_MS", 100),
        vad_max_speech_seconds=_float(values, "VAD_MAX_SPEECH_SECONDS", 8.0),
        backend_wake_word_model_paths=agent.parse_env_list(str(values.get("BACKEND_WAKE_WORD_MODEL_PATHS") or "")),
        backend_wake_word_model_names=agent.parse_env_list(str(values.get("BACKEND_WAKE_WORD_MODEL_NAMES") or "")),
        backend_wake_word_threshold=max(0.01, min(0.99, _float(values, "BACKEND_WAKE_WORD_THRESHOLD", agent.DEFAULT_BACKEND_WAKE_WORD_THRESHOLD))),
        backend_wake_word_pre_roll_ms=max(0, min(5000, _int(values, "BACKEND_WAKE_WORD_PRE_ROLL_MS", agent.DEFAULT_BACKEND_WAKE_WORD_PRE_ROLL_MS))),
        backend_wake_word_cooldown_ms=max(0, min(10000, _int(values, "BACKEND_WAKE_WORD_COOLDOWN_MS", agent.DEFAULT_BACKEND_WAKE_WORD_COOLDOWN_MS))),
        backend_wake_word_vad_threshold=backend_wake_vad,
        backend_stt_enabled=str(values.get("STT_INPUT") or "both").strip().lower() in {"both", "backend"},
        tts_speed=max(0.6, min(1.8, _float(values, "WEB_TTS_SPEED", 1.0))),
        backend_tts_volume=speech_gain,
        backend_audio_output_pan=agent.normalize_audio_pan(_float(values, "BACKEND_AUDIO_OUTPUT_PAN", 0.0)),
        backend_audio_monitor_mode=monitor_mode,
        backend_audio_monitor_volume=max(0.0, min(2.0, _float(values, "BACKEND_AUDIO_MONITOR_VOLUME", 1.0))),
        wake_words=wake_words,
        mcp_config=_mcp_config(values, path),
        mcp_load_server_prompt=_bool(values, "MCP_LOAD_SERVER_PROMPT", False),
        mcp_prompt_merge_mode=str(values.get("MCP_PROMPT_MERGE_MODE") or "append").strip().lower(),
        mcp_agent_memory_enabled=_bool(values, "MCP_AGENT_MEMORY_ENABLED", True),
        mcp_agent_timeout_seconds=max(1.0, _float(values, "MCP_AGENT_TIMEOUT_SECONDS", agent.DEFAULT_MCP_AGENT_TIMEOUT_SECONDS)),
        mcp_agent_max_steps=max(5, _int(values, "MCP_AGENT_MAX_STEPS", agent.DEFAULT_MCP_AGENT_MAX_STEPS)),
        mcp_tool_routing_enabled=_bool(values, "MCP_TOOL_ROUTING_ENABLED", False),
        session_context_store=context_store,
        session_context_size=max(0, min(12000, _int(values, "SESSION_CONTEXT_SIZE", 6000))),
        voice_cancel_during_thinking=_bool(values, "VOICE_CANCEL_DURING_THINKING", False),
        interrupt_conversation_enabled=_bool(values, "INTERRUPT_CONVERSATION_ENABLED", False),
        speaker_recognition_enabled=_bool(values, "SPEAKER_RECOGNITION_ENABLED", False),
        speaker_backend=str(values.get("SPEAKER_BACKEND") or "resemblyzer").strip().lower(),
        speaker_threshold=max(0.0, min(1.0, _float(values, "SPEAKER_THRESHOLD", 0.75))),
        speaker_margin=max(0.0, min(1.0, _float(values, "SPEAKER_MARGIN", 0.10))),
        speaker_recognition_timeout_seconds=max(1.0, _float(values, "SPEAKER_RECOGNITION_TIMEOUT_SECONDS", agent.DEFAULT_SPEAKER_RECOGNITION_TIMEOUT_SECONDS)),
        speaker_profiles=speaker_profiles,
        system_prompt=str(values.get("ASSISTANT_SYSTEM_PROMPT") or agent.DEFAULT_ASSISTANT_SYSTEM_PROMPT).strip(),
        reload_event=None,
        web_monitor=None,
    )
    print(
        f"Classic engine profile: connectivity={connectivity} llm={assistant.llm_provider}/{assistant.model} "
        f"tts={assistant.tts_provider} speech_gain={speech_gain:.2f}",
        flush=True,
    )
    return assistant


async def run_async(env_file: str | Path) -> int:
    assistant = build_assistant(env_file)
    result = await assistant.run()
    return 0 if result in {None, "exit", "reload"} else 1


def run(env_file: str | Path) -> int:
    return asyncio.run(run_async(env_file))
