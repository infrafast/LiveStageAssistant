"""Engine-neutral services used by the runtime-owned WebMonitor.

The production HTTP server belongs to ``voice_assistant.runtime``. This module
contains profile/session/configuration services only; it does not bind sockets
and has no Classic/Realtime/Local engine dependency.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Any, Callable
from urllib.parse import urlparse

from dotenv import dotenv_values

from .i18n import available_locales, normalize_locale
from .session_context import DEFAULT_CONTEXT_DIR, DEFAULT_SUMMARY_MAX_CHARS, SessionContextStore


CLOUD_TTS_PROVIDER_OPTIONS = [
    {"id": "none", "label": "None"},
    {"id": "openai", "label": "OpenAI"},
    {"id": "elevenlabs", "label": "ElevenLabs"},
]
TTS_OUTPUT_OPTIONS = [
    {"id": "backend", "label": "Backend"},
    {"id": "browser", "label": "Browser"},
    {"id": "silent", "label": "Silent"},
]
OPENAI_TTS_VOICE_OPTIONS = [
    {"id": voice, "label": voice}
    for voice in ("alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer")
]
DEFAULT_STT_PROMPT = ""
DEFAULT_SYSTEM_PROMPT = ""
DEFAULT_MCP_AGENT_MAX_STEPS = 20


class RuntimeWebServices:
    """Common mutable services behind the single runtime WebMonitor."""

    def __init__(
        self,
        *,
        monitor: Any,
        active_profile: Callable[[], Path],
        automatic_profiles: bool = True,
        request_reload: Callable[[], None] | None = None,
    ) -> None:
        self.monitor = monitor
        self.active_profile = active_profile
        self.automatic_profiles = bool(automatic_profiles)
        self.request_reload = request_reload
        self._lock = threading.RLock()

    def bind(self) -> None:
        """Register only engine-neutral handlers on the common WebMonitor."""
        self.monitor.set_llm_config_handlers(
            options_handler=self.llm_options,
            save_handler=self.save_llm_config,
        )
        self.monitor.set_cloud_api_status_handler(self.cloud_api_status)
        self.monitor.set_env_profile_handlers(
            list_handler=self.list_env_profiles,
            switch_handler=self.switch_env_profile,
        )
        self.monitor.set_remote_screen_handler(self.save_remote_screen)
        self.monitor.set_mcp_routing_save_handler(self.save_mcp_routing)
        self.monitor.set_mcp_server_options_save_handler(self.save_mcp_server_options)
        self.monitor.set_session_context_handlers(
            list_handler=self.session_snapshot,
            new_handler=self.new_session,
            select_handler=self.select_session,
            rename_handler=self.rename_session,
            clear_handler=self.clear_session,
            save_handler=self.save_session,
            delete_handler=self.delete_session,
        )

    def _values(self, profile: Path | None = None) -> dict[str, Any]:
        return dict(dotenv_values(profile or self.active_profile()))

    @staticmethod
    def _bool(values: dict[str, Any], key: str, default: bool = False) -> bool:
        raw = values.get(key)
        if raw in (None, ""):
            return default
        return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}

    @staticmethod
    def _int(values: dict[str, Any], key: str, default: int) -> int:
        try:
            return int(str(values.get(key) if values.get(key) not in (None, "") else default))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _float(values: dict[str, Any], key: str, default: float) -> float:
        try:
            return float(str(values.get(key) if values.get(key) not in (None, "") else default))
        except (TypeError, ValueError):
            return default

    def _profile_dir(self) -> Path:
        return self.active_profile().parent

    def _write_env(self, profile: Path, updates: dict[str, str]) -> None:
        if not profile.is_file():
            raise ValueError(f"env file not found: {profile}")
        original_mode = profile.stat().st_mode & 0o777
        pending = {str(key): str(value) for key, value in updates.items()}
        output: list[str] = []
        for line in profile.read_text(encoding="utf-8").splitlines():
            key, sep, _value = line.partition("=")
            if sep and key.strip() in pending:
                clean_key = key.strip()
                output.append(f"{clean_key}={self._format_env_value(pending.pop(clean_key))}")
            else:
                output.append(line)
        output.extend(f"{key}={self._format_env_value(value)}" for key, value in pending.items())
        profile.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=profile.parent, delete=False) as handle:
            handle.write("\n".join(output) + "\n")
            temporary = Path(handle.name)
        os.chmod(temporary, original_mode)
        temporary.replace(profile)

    @staticmethod
    def _format_env_value(value: str) -> str:
        text = str(value)
        if re.fullmatch(r"[A-Za-z0-9_./:@+-]*", text):
            return text
        escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")
        return f'"{escaped}"'

    def _mcp_path(self, values: dict[str, Any] | None = None) -> Path:
        values = values or self._values()
        configured = str(values.get("MCP_CONFIG") or "mcp_servers.json").strip()
        path = Path(os.path.expandvars(configured)).expanduser()
        if path.is_absolute():
            return path
        candidates = (self.active_profile().parent / path, Path.cwd() / path)
        return next((candidate for candidate in candidates if candidate.is_file()), candidates[-1])

    def _load_mcp(self) -> tuple[Path, dict[str, Any]]:
        path = self._mcp_path()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except OSError as error:
            raise ValueError(f"could not read MCP_CONFIG '{path}': {error}") from error
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid JSON in MCP_CONFIG '{path}': {error}") from error
        if not isinstance(payload, dict) or not isinstance(payload.get("mcpServers"), dict):
            raise ValueError("active MCP config has no mcpServers object")
        return path, payload

    @staticmethod
    def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
        original_mode = (path.stat().st_mode & 0o777) if path.exists() else 0o644
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            temporary = Path(handle.name)
        os.chmod(temporary, original_mode)
        temporary.replace(path)

    def _refresh_monitor_config(self, *, mcp_config: dict[str, Any] | None = None) -> None:
        values = self._values()
        self.monitor.set_web_password(str(values.get("WEB_PASSWORD") or "").strip())
        kwargs: dict[str, Any] = {"env_file": self.active_profile(), "env_values": values}
        if mcp_config is not None:
            kwargs["mcp_config"] = mcp_config
        self.monitor.update(**kwargs)

    def _reload(self) -> None:
        if self.request_reload is not None:
            self.request_reload()

    def _secret_present(self, values: dict[str, Any], name: str) -> bool:
        path = str(values.get(f"{name}_FILE") or "").strip()
        if not path:
            return False
        try:
            return bool(Path(path).expanduser().read_text(encoding="utf-8").strip())
        except OSError:
            return False

    @staticmethod
    def _voice_options(values: dict[str, Any]) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        raw = str(values.get("ELEVENLABS_VOICE_OPTIONS") or "")
        for voice_id, label in re.findall(r"([A-Za-z0-9_-]+)\s*\(([^)]+)\)", raw):
            result.append({"id": voice_id, "label": label.strip()})
        current = str(values.get("ELEVENLABS_VOICE_ID") or "").strip()
        if current and all(entry["id"] != current for entry in result):
            result.insert(0, {"id": current, "label": current})
        return result

    @staticmethod
    def _wav_options() -> list[dict[str, str]]:
        assets = Path.cwd() / "assets"
        if not assets.is_dir():
            return []
        return [
            {"id": path.name, "label": path.name}
            for path in sorted(assets.glob("*.wav"), key=lambda item: item.name.lower())
            if path.is_file()
        ]

    @staticmethod
    def _wake_model_options() -> list[dict[str, str]]:
        data = Path.cwd() / "data"
        if not data.is_dir():
            return []
        result: list[dict[str, str]] = []
        for path in sorted(data.rglob("*.onnx"), key=lambda item: item.as_posix().lower()):
            if not path.is_file() or path.name.startswith("._"):
                continue
            try:
                model_id = path.relative_to(Path.cwd()).as_posix()
            except ValueError:
                model_id = path.as_posix()
            result.append({"id": model_id, "label": model_id})
        return result

    def _speaker_profiles(self, values: dict[str, Any]) -> list[dict[str, Any]]:
        root = Path(str(values.get("SPEAKER_PROFILES_DIR") or "data/speaker_profiles"))
        maximum = max(0, min(5, self._int(values, "SPEAKER_PROFILES_MAX", 5)))
        result: list[dict[str, Any]] = []
        for index in range(1, maximum + 1):
            name = str(values.get(f"SPEAKER_PROFILE_{index}_NAME") or "").strip()
            enabled = self._bool(values, f"SPEAKER_PROFILE_{index}_ENABLED", False)
            samples = []
            embedding_count = 0
            for sample in range(1, 4):
                wav = root / f"profil{index}_{sample}.wav"
                embedding = wav.with_suffix(".npy")
                ready = wav.is_file()
                embedding_ready = ready and embedding.is_file() and embedding.stat().st_mtime >= wav.stat().st_mtime
                if embedding_ready:
                    embedding_count += 1
                samples.append({
                    "index": sample,
                    "wav_path": wav.as_posix(),
                    "filename": wav.name,
                    "ready": ready,
                    "embedding_path": embedding.as_posix(),
                    "embedding_ready": embedding_ready,
                    "status": "ready" if ready else "missing",
                })
            result.append({
                "index": index,
                "name": name,
                "enabled": enabled,
                "wav_paths": [item["wav_path"] for item in samples],
                "samples": samples,
                "complete": all(item["ready"] for item in samples),
                "usable": embedding_count > 0,
                "embedding_count": embedding_count,
                "embedding_total": 3,
                "status": f"{embedding_count}/3 embeddings",
                "embedding_ready": embedding_count > 0,
                "embedding_path": next((item["embedding_path"] for item in samples if item["embedding_ready"]), ""),
                "slug": re.sub(r"[^a-zA-Z0-9_.-]+", "_", (name or f"speaker_{index}").lower()).strip("._-")[:64],
            })
        return result

    def llm_options(self, requested_provider: str | None = None) -> dict[str, Any]:
        """Return GUI options from the active profile without constructing an engine."""
        values = self._values()
        connectivity = str(values.get("CONNECTIVITY_MODE") or "online").strip().lower()
        current_provider = str(values.get("LLM_PROVIDER") or ("ollama" if connectivity == "offline" else "openai")).strip().lower()
        provider = str(requested_provider or current_provider).strip().lower()
        if provider not in {"openai", "ollama"}:
            provider = current_provider if current_provider in {"openai", "ollama"} else "openai"
        if connectivity == "offline":
            provider = "ollama"

        if provider == "ollama":
            selected_model = str(values.get("OLLAMA_MODEL") or values.get("OFFLINE_MODEL") or "").strip()
        else:
            selected_model = str(values.get("OPENAI_MODEL") or "gpt-4.1-mini").strip()
        models = [{"id": selected_model, "label": selected_model}] if selected_model else []

        cloud_tts = str(values.get("CLOUD_TTS_PROVIDER") or "").strip().lower()
        if not cloud_tts:
            backend_provider = str(values.get("TTS_PROVIDER") or "").strip().lower()
            browser_provider = str(values.get("WEB_TTS_PROVIDER") or "").strip().lower()
            cloud_tts = backend_provider if backend_provider in {"openai", "elevenlabs"} else browser_provider
        if cloud_tts not in {"none", "openai", "elevenlabs"}:
            cloud_tts = "none" if connectivity == "offline" else "openai"

        backend_provider = str(values.get("TTS_PROVIDER") or "none").strip().lower()
        browser_provider = str(values.get("WEB_TTS_PROVIDER") or "none").strip().lower()
        if backend_provider in {"openai", "elevenlabs", "piper"}:
            tts_output = "backend"
        elif browser_provider in {"openai", "elevenlabs"}:
            tts_output = "browser"
        else:
            tts_output = "silent"

        command_ack = str(values.get("COMMAND_ACK_SOUND_FILE") or "").strip()
        if not command_ack and self._bool(values, "COMMAND_ACK_SOUND_ENABLED", False):
            command_ack = "ring.wav"
        startup_enabled = self._bool(values, "STARTUP_LOADER_SOUND_ENABLED", False)
        startup_file = str(values.get("STARTUP_LOADER_SOUND_FILE") or "loader.wav").strip() if startup_enabled else ""
        stt_input = str(values.get("STT_INPUT") or "both").strip().lower()
        if stt_input not in {"both", "backend", "browser", "silent"}:
            stt_input = "both"

        providers = [
            {"id": "openai", "label": "OpenAI", "available": connectivity != "offline", "reason": None if connectivity != "offline" else "offline"},
            {"id": "ollama", "label": "Ollama", "available": True, "reason": None},
        ]
        backend_input = str(values.get("BACKEND_AUDIO_INPUT_DEVICE") or "").strip()
        backend_output = str(values.get("BACKEND_AUDIO_OUTPUT_DEVICE") or "").strip()
        return {
            "provider": provider,
            "selected_connectivity_mode": connectivity,
            "providers": providers,
            "models": models,
            "selected_model": selected_model,
            "cloud_tts_providers": CLOUD_TTS_PROVIDER_OPTIONS,
            "selected_cloud_tts_provider": cloud_tts,
            "selected_stt_input": stt_input,
            "selected_stt_language": normalize_locale(str(values.get("STT_LANGUAGE") or "fr")),
            "available_locales": available_locales(),
            "tts_outputs": TTS_OUTPUT_OPTIONS,
            "selected_tts_output": tts_output,
            "selected_wake_word": str(values.get("WAKE_WORD") or "").strip(),
            "selected_stt_prompt": str(values.get("STT_PROMPT") or DEFAULT_STT_PROMPT).strip(),
            "selected_system_prompt": str(values.get("ASSISTANT_SYSTEM_PROMPT") or DEFAULT_SYSTEM_PROMPT).strip(),
            "selected_session_context_size": self._int(values, "SESSION_CONTEXT_SIZE", 6000),
            "selected_mcp_agent_max_steps": self._int(values, "MCP_AGENT_MAX_STEPS", DEFAULT_MCP_AGENT_MAX_STEPS),
            "selected_mcp_tool_routing_enabled": self._bool(values, "MCP_TOOL_ROUTING_ENABLED", False),
            "selected_interrupt_conversation_enabled": self._bool(values, "INTERRUPT_CONVERSATION_ENABLED", False),
            "voices": self._voice_options(values),
            "selected_voice_id": str(values.get("ELEVENLABS_VOICE_ID") or "").strip(),
            "openai_tts_voices": OPENAI_TTS_VOICE_OPTIONS,
            "selected_openai_tts_voice": str(values.get("WEB_TTS_VOICE") or "alloy").strip(),
            "selected_openai_tts_speed": self._float(values, "WEB_TTS_SPEED", 1.0),
            "selected_web_tts_volume": min(1.0, self._float(values, "WEB_TTS_VOLUME", 1.0)),
            "selected_backend_tts_volume": self._float(values, "BACKEND_TTS_VOLUME", 1.0),
            "selected_backend_audio_output_pan": self._float(values, "BACKEND_AUDIO_OUTPUT_PAN", 0.0),
            "selected_backend_audio_monitor_mode": str(values.get("BACKEND_AUDIO_MONITOR_MODE") or "off").strip().lower(),
            "selected_backend_audio_monitor_volume": self._float(values, "BACKEND_AUDIO_MONITOR_VOLUME", 1.0),
            "selected_backend_audio_input_gain": self._float(values, "BACKEND_AUDIO_INPUT_GAIN", 1.0),
            "selected_vad_speech_threshold": self._float(values, "VAD_SPEECH_THRESHOLD", 0.5),
            "selected_vad_negative_threshold": self._float(values, "VAD_NEGATIVE_THRESHOLD", 0.35),
            "selected_vad_min_speech_ms": self._int(values, "VAD_MIN_SPEECH_MS", 250),
            "selected_vad_min_silence_ms": self._int(values, "VAD_MIN_SILENCE_MS", 650),
            "selected_vad_speech_pad_ms": self._int(values, "VAD_SPEECH_PAD_MS", 100),
            "selected_vad_max_speech_seconds": self._float(values, "VAD_MAX_SPEECH_SECONDS", 8.0),
            "selected_backend_wake_word_model_paths": str(values.get("BACKEND_WAKE_WORD_MODEL_PATHS") or "").strip(),
            "selected_backend_wake_word_model_names": str(values.get("BACKEND_WAKE_WORD_MODEL_NAMES") or "").strip(),
            "selected_backend_wake_word_threshold": self._float(values, "BACKEND_WAKE_WORD_THRESHOLD", 0.5),
            "selected_backend_wake_word_pre_roll_ms": self._int(values, "BACKEND_WAKE_WORD_PRE_ROLL_MS", 1600),
            "selected_backend_wake_word_cooldown_ms": self._int(values, "BACKEND_WAKE_WORD_COOLDOWN_MS", 1200),
            "selected_backend_wake_word_vad_threshold": self._float(values, "BACKEND_WAKE_WORD_VAD_THRESHOLD", 0.0),
            "wake_word_model_files": self._wake_model_options(),
            "selected_speaker_recognition_enabled": self._bool(values, "SPEAKER_RECOGNITION_ENABLED", False),
            "selected_speaker_backend": str(values.get("SPEAKER_BACKEND") or "resemblyzer").strip().lower(),
            "selected_speaker_threshold": self._float(values, "SPEAKER_THRESHOLD", 0.75),
            "selected_speaker_margin": self._float(values, "SPEAKER_MARGIN", 0.10),
            "speaker_recognition_runtime": {},
            "selected_speaker_profiles_max": max(0, min(5, self._int(values, "SPEAKER_PROFILES_MAX", 5))),
            "speaker_profiles": self._speaker_profiles(values),
            # Device enumeration moves to the common audio service. Preserve the
            # selected ids meanwhile so the current config remains visible.
            "backend_audio_inputs": ([{"id": backend_input, "label": backend_input, "name": backend_input, "default": False}] if backend_input else []),
            "backend_audio_outputs": ([{"id": backend_output, "label": backend_output, "name": backend_output, "default": False}] if backend_output else []),
            "selected_backend_audio_input_device": backend_input,
            "selected_backend_audio_output_device": backend_output,
            "thinking_sounds": self._wav_options(),
            "selected_thinking_sound_file": str(values.get("THINKING_SOUND_FILE") or "thinking.wav").strip(),
            "selected_listening_sound_file": str(values.get("LISTENING_SOUND_FILE") or "").strip(),
            "selected_wake_detected_sound_file": str(values.get("WAKE_DETECTED_SOUND_FILE") or "").strip(),
            "selected_startup_loader_sound_file": startup_file,
            "selected_command_ack_sound_file": command_ack,
            "selected_voice_engine": str(values.get("VOICE_ENGINE") or ("local" if connectivity == "offline" else "classic")).strip().lower(),
            "selected_realtime_model": str(values.get("OPENAI_REALTIME_MODEL") or "gpt-realtime-2.1").strip(),
            "selected_realtime_voice": str(values.get("OPENAI_REALTIME_VOICE") or "marin").strip(),
            "selected_cloud_tts_output_gain": self._float(values, "CLOUD_TTS_OUTPUT_GAIN", 1.0),
            "selected_local_tts_output_gain": self._float(values, "LOCAL_TTS_OUTPUT_GAIN", 1.0),
            "message": f"Common runtime options loaded from active profile: {self.active_profile()}",
        }

    def save_llm_config(
        self,
        provider: str,
        model: str,
        cloud_tts_provider: str,
        tts_output: str,
        stt_input: str,
        stt_language: str,
        connectivity_mode: str,
        wake_word: str,
        stt_prompt: str,
        system_prompt: str,
        session_context_size: int,
        mcp_agent_max_steps: int,
        mcp_tool_routing_enabled: bool,
        interrupt_conversation_enabled: bool,
        backend_audio_input_device: str,
        backend_audio_input_gain: float,
        backend_audio_output_device: str,
        voice_id: str,
        thinking_sound_file: str,
        listening_sound_file: str,
        wake_detected_sound_file: str,
        startup_loader_sound_file: str,
        command_ack_sound_file: str,
        openai_tts_voice: str,
        openai_tts_speed: float,
        web_tts_volume: float,
        backend_tts_volume: float,
        backend_audio_output_pan: float,
        backend_audio_monitor_mode: str,
        backend_audio_monitor_volume: float,
        vad_speech_threshold: float,
        vad_negative_threshold: float,
        vad_min_speech_ms: int,
        vad_min_silence_ms: int,
        vad_speech_pad_ms: int,
        vad_max_speech_seconds: float,
        backend_wake_word_model_paths: str,
        backend_wake_word_model_names: str,
        backend_wake_word_threshold: float,
        backend_wake_word_pre_roll_ms: int,
        backend_wake_word_cooldown_ms: int,
        backend_wake_word_vad_threshold: float,
        speaker_recognition_enabled: bool,
        speaker_backend: str,
        speaker_threshold: float,
        speaker_margin: float,
        speaker_profiles: list[dict[str, Any]],
        voice_engine: str,
        realtime_model: str,
        realtime_voice: str,
        cloud_tts_output_gain: float,
        local_tts_output_gain: float,
    ) -> dict[str, Any]:
        """Persist the legacy GUI form into the active runtime profile."""
        values = self._values()
        active_connectivity = str(values.get("CONNECTIVITY_MODE") or "online").strip().lower()
        requested_connectivity = str(connectivity_mode or active_connectivity).strip().lower()
        if requested_connectivity not in {"online", "offline"}:
            raise ValueError(f"unsupported connectivity mode: {requested_connectivity}")
        if self.automatic_profiles and requested_connectivity != active_connectivity:
            raise ValueError("connectivity is controlled automatically by the runtime; switch network state instead")

        provider = str(provider or "").strip().lower()
        if active_connectivity == "offline":
            provider = "ollama"
        elif provider != "openai":
            raise ValueError("online mode uses the cloud LLM provider; local Ollama belongs to the offline profile")
        model = str(model or "").strip()
        if not model:
            raise ValueError("Model is required")

        stt_input = str(stt_input or "both").strip().lower()
        if stt_input not in {"both", "backend", "browser", "silent"}:
            raise ValueError(f"unsupported STT input: {stt_input}")
        cloud_tts_provider = str(cloud_tts_provider or "none").strip().lower()
        tts_output = str(tts_output or "silent").strip().lower()
        if active_connectivity == "offline":
            cloud_tts_provider = "none"
            tts_output = "backend"
            tts_provider = "piper"
            web_tts_provider = "none"
            stt_input = "backend"
        else:
            if cloud_tts_provider not in {"none", "openai", "elevenlabs"}:
                raise ValueError(f"unsupported cloud TTS provider: {cloud_tts_provider}")
            if tts_output not in {"backend", "browser", "silent"}:
                raise ValueError(f"unsupported TTS output: {tts_output}")
            if cloud_tts_provider == "none" or tts_output == "silent":
                tts_provider = "none"
                web_tts_provider = "none"
                tts_output = "silent"
            elif tts_output == "backend":
                tts_provider = cloud_tts_provider
                web_tts_provider = "none"
            else:
                tts_provider = "none"
                web_tts_provider = cloud_tts_provider

        vad_speech_threshold = max(0.05, min(0.95, float(vad_speech_threshold)))
        vad_negative_threshold = max(0.01, min(0.95, float(vad_negative_threshold)))
        if vad_negative_threshold >= vad_speech_threshold:
            vad_negative_threshold = max(0.01, vad_speech_threshold - 0.15)
        backend_audio_monitor_mode = str(backend_audio_monitor_mode or "off").strip().lower()
        if backend_audio_monitor_mode not in {"off", "rejected", "passthrough"}:
            raise ValueError(f"unsupported audio monitor mode: {backend_audio_monitor_mode}")
        wake_word = str(wake_word or "").strip()
        if backend_audio_monitor_mode == "rejected" and not wake_word:
            backend_audio_monitor_mode = "off"

        requested_engine = str(voice_engine or ("local" if active_connectivity == "offline" else "classic")).strip().lower()
        allowed_engines = {"local"} if active_connectivity == "offline" else {"classic", "openai-realtime"}
        if requested_engine not in allowed_engines:
            raise ValueError(f"voice_engine must be one of: {', '.join(sorted(allowed_engines))}")
        cloud_tts_output_gain = max(0.0, min(2.0, float(cloud_tts_output_gain)))
        local_tts_output_gain = max(0.0, min(2.0, float(local_tts_output_gain)))
        if requested_engine == "openai-realtime":
            realtime_model = str(realtime_model or "gpt-realtime-2.1").strip()
            realtime_voice = str(realtime_voice or "marin").strip()
            if not realtime_model or not realtime_voice:
                raise ValueError("Realtime model and voice are required")

        updates = {
            "VOICE_ENGINE": requested_engine,
            "CLOUD_TTS_OUTPUT_GAIN": f"{cloud_tts_output_gain:.2f}",
            "LOCAL_TTS_OUTPUT_GAIN": f"{local_tts_output_gain:.2f}",
            "LLM_PROVIDER": provider,
            "STT_INPUT": stt_input,
            "STT_LANGUAGE": normalize_locale(stt_language),
            "WAKE_WORD": wake_word,
            "STT_PROMPT": str(stt_prompt or "").strip(),
            "ASSISTANT_SYSTEM_PROMPT": str(system_prompt or "").strip(),
            "SESSION_CONTEXT_SIZE": str(max(0, min(12000, int(session_context_size)))),
            "MCP_AGENT_MAX_STEPS": str(max(5, min(60, int(mcp_agent_max_steps)))),
            "MCP_TOOL_ROUTING_ENABLED": "true" if mcp_tool_routing_enabled else "false",
            "INTERRUPT_CONVERSATION_ENABLED": "true" if interrupt_conversation_enabled else "false",
            "BACKEND_AUDIO_INPUT_DEVICE": str(backend_audio_input_device or "").strip(),
            "BACKEND_AUDIO_INPUT_GAIN": f"{max(0.5, min(2.0, float(backend_audio_input_gain))):.2f}",
            "BACKEND_AUDIO_OUTPUT_DEVICE": str(backend_audio_output_device or "").strip(),
            "ELEVENLABS_VOICE_ID": str(voice_id or "").strip(),
            "THINKING_SOUND_FILE": str(thinking_sound_file or "").strip(),
            "LISTENING_SOUND_FILE": str(listening_sound_file or "").strip(),
            "WAKE_DETECTED_SOUND_FILE": str(wake_detected_sound_file or "").strip(),
            "STARTUP_LOADER_SOUND_ENABLED": "true" if str(startup_loader_sound_file or "").strip() else "false",
            "STARTUP_LOADER_SOUND_FILE": str(startup_loader_sound_file or "").strip() or "loader.wav",
            "COMMAND_ACK_SOUND_FILE": str(command_ack_sound_file or "").strip(),
            "COMMAND_ACK_SOUND_ENABLED": "true" if str(command_ack_sound_file or "").strip() else "false",
            "WEB_TTS_VOICE": str(openai_tts_voice or "alloy").strip(),
            "WEB_TTS_SPEED": f"{max(0.6, min(1.8, float(openai_tts_speed))):.2f}",
            "WEB_TTS_VOLUME": f"{max(0.0, min(1.0, float(web_tts_volume))):.2f}",
            "BACKEND_TTS_VOLUME": f"{max(0.0, min(2.0, float(backend_tts_volume))):.2f}",
            "BACKEND_AUDIO_OUTPUT_PAN": f"{max(-1.0, min(1.0, float(backend_audio_output_pan))):.2f}",
            "BACKEND_AUDIO_MONITOR_MODE": backend_audio_monitor_mode,
            "BACKEND_AUDIO_MONITOR_VOLUME": f"{max(0.0, min(2.0, float(backend_audio_monitor_volume))):.2f}",
            "VAD_SPEECH_THRESHOLD": f"{vad_speech_threshold:.3f}",
            "VAD_NEGATIVE_THRESHOLD": f"{vad_negative_threshold:.3f}",
            "VAD_MIN_SPEECH_MS": str(max(0, min(2000, int(vad_min_speech_ms)))),
            "VAD_MIN_SILENCE_MS": str(max(100, min(5000, int(vad_min_silence_ms)))),
            "VAD_SPEECH_PAD_MS": str(max(0, min(1000, int(vad_speech_pad_ms)))),
            "VAD_MAX_SPEECH_SECONDS": f"{max(1.0, min(30.0, float(vad_max_speech_seconds))):.2f}",
            "BACKEND_WAKE_WORD_MODEL_PATHS": str(backend_wake_word_model_paths or "").strip(),
            "BACKEND_WAKE_WORD_MODEL_NAMES": str(backend_wake_word_model_names or "").strip(),
            "BACKEND_WAKE_WORD_THRESHOLD": f"{max(0.05, min(0.99, float(backend_wake_word_threshold))):.3f}",
            "BACKEND_WAKE_WORD_PRE_ROLL_MS": str(max(200, min(5000, int(backend_wake_word_pre_roll_ms)))),
            "BACKEND_WAKE_WORD_COOLDOWN_MS": str(max(0, min(10000, int(backend_wake_word_cooldown_ms)))),
            "BACKEND_WAKE_WORD_VAD_THRESHOLD": f"{max(0.0, min(1.0, float(backend_wake_word_vad_threshold))):.3f}",
            "SPEAKER_RECOGNITION_ENABLED": "true" if speaker_recognition_enabled else "false",
            "SPEAKER_BACKEND": str(speaker_backend or "resemblyzer").strip().lower(),
            "SPEAKER_THRESHOLD": f"{max(0.0, min(1.0, float(speaker_threshold))):.3f}",
            "SPEAKER_MARGIN": f"{max(0.0, min(1.0, float(speaker_margin))):.3f}",
            "CLOUD_TTS_PROVIDER": cloud_tts_provider,
            "TTS_PROVIDER": tts_provider,
            "WEB_TTS_PROVIDER": web_tts_provider,
        }
        if requested_engine == "openai-realtime":
            updates["OPENAI_REALTIME_MODEL"] = realtime_model
            updates["OPENAI_REALTIME_VOICE"] = realtime_voice
        if provider == "ollama":
            updates["OLLAMA_MODEL"] = model
            updates["OFFLINE_MODEL"] = model
            updates["STT_PROVIDER"] = "local-whisper"
        else:
            updates["OPENAI_MODEL"] = model
            updates["STT_PROVIDER"] = str(values.get("STT_PROVIDER") or "openai-whisper").strip().lower()

        for index in range(1, 6):
            entry = speaker_profiles[index - 1] if index <= len(speaker_profiles) and isinstance(speaker_profiles[index - 1], dict) else {}
            updates[f"SPEAKER_PROFILE_{index}_NAME"] = str(entry.get("name") or "").strip()
            updates[f"SPEAKER_PROFILE_{index}_ENABLED"] = "true" if bool(entry.get("enabled")) else "false"

        with self._lock:
            profile = self.active_profile()
            self._write_env(profile, updates)
            self._refresh_monitor_config()
        return {
            "saved": True,
            "provider": provider,
            "model": model,
            "voice_engine": requested_engine,
            "connectivity_mode": active_connectivity,
            "profile": str(profile),
            "restart_required": True,
            "message": f"Saved to {profile} · restart required.",
        }

    def cloud_api_status(self) -> dict[str, Any]:
        """Expose key presence only; provider billing calls remain optional/non-core."""
        values = self._values()
        openai_present = self._secret_present(values, "OPENAI_API_KEY")
        eleven_present = self._secret_present(values, "ELEVENLABS_API_KEY")
        return {
            "openai": {
                "status": "configured" if openai_present else "missing",
                "masked_key": "configured" if openai_present else "",
                "lines": ["API key configured." if openai_present else "OPENAI_API_KEY_FILE is not configured."],
            },
            "elevenlabs": {
                "status": "configured" if eleven_present else "missing",
                "masked_key": "configured" if eleven_present else "",
                "lines": ["API key configured." if eleven_present else "ELEVENLABS_API_KEY_FILE is not configured."],
            },
        }

    def list_env_profiles(self) -> dict[str, Any]:
        active = self.active_profile().resolve()
        profiles = []
        for path in (self._profile_dir() / ".env.online", self._profile_dir() / ".env.offline"):
            if not path.is_file():
                continue
            profiles.append({"id": str(path), "label": path.name, "selected": path.resolve() == active})
        return {
            "current": str(active),
            "profiles": profiles,
            "switching_enabled": not self.automatic_profiles,
            "auto_mode": self.automatic_profiles,
            "connectivity_locked": self.automatic_profiles,
            "message": "Connectivity selects the active profile automatically." if self.automatic_profiles else "",
        }

    def switch_env_profile(self, selection: str) -> dict[str, Any]:
        if self.automatic_profiles:
            raise ValueError("manual env switching is disabled while the common runtime controls connectivity")
        candidate = Path(selection).expanduser().resolve()
        allowed = {path.resolve() for path in self._profile_dir().glob(".env*") if path.is_file()}
        if candidate not in allowed:
            raise ValueError("selected env file is outside the available profiles")
        raise ValueError("fixed-profile switching must be requested through the runtime supervisor")

    def save_remote_screen(self, vnc_url: str, view_only: bool) -> dict[str, Any]:
        cleaned = str(vnc_url or "").strip()
        parsed = urlparse(cleaned)
        if parsed.scheme not in {"vnc", "http", "https"} or not parsed.netloc:
            raise ValueError("VNC URL must include vnc://, http://, or https:// and a host")
        with self._lock:
            self._write_env(
                self.active_profile(),
                {
                    "REMOTE_SCREEN_VNC_URL": cleaned,
                    "REMOTE_SCREEN_VNC_VIEW_ONLY": "true" if view_only else "false",
                },
            )
            self._refresh_monitor_config()
        return {"saved": True, "vnc_url": cleaned, "view_only": bool(view_only)}

    @staticmethod
    def _routing_words(value: Any) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for item in re.split(r"[,;\n]+", str(value or "")):
            word = item.strip().lower()
            if word and word not in seen:
                result.append(word)
                seen.add(word)
        return result

    def save_mcp_routing(self, updates: dict[str, str]) -> dict[str, Any]:
        with self._lock:
            path, config = self._load_mcp()
            servers = config["mcpServers"]
            unknown = sorted(name for name in updates if name not in servers)
            if unknown:
                raise ValueError(f"unknown MCP server(s): {', '.join(unknown)}")
            owner: dict[str, str] = {}
            normalized: dict[str, str] = {}
            for server_name, server_config in servers.items():
                if not isinstance(server_config, dict):
                    continue
                options = server_config.get("assistantOptions")
                if not isinstance(options, dict):
                    options = {}
                    server_config["assistantOptions"] = options
                source = updates.get(str(server_name), options.get("routing", ""))
                words = self._routing_words(source)
                if len(words) > 10:
                    raise ValueError(f"routing words limit exceeded: {server_name} has {len(words)} words, max 10")
                for word in words:
                    previous = owner.get(word)
                    if previous and previous != server_name:
                        raise ValueError(f"routing word duplicate: {word}")
                    owner[word] = str(server_name)
                normalized[str(server_name)] = ",".join(words)
                options["routing"] = normalized[str(server_name)]
            self._write_json_atomic(path, config)
            self._refresh_monitor_config(mcp_config=config)
        return {"ok": True, "mcp_config": str(path), "routing": normalized, "restart_required": True}

    @staticmethod
    def _normalized_env_options(value: Any) -> dict[str, str]:
        if not isinstance(value, dict):
            raise ValueError("MCP server options must be JSON objects")
        result: dict[str, str] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key).strip()
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                raise ValueError(f"invalid MCP env option name: {key}")
            if isinstance(raw_value, (dict, list)):
                result[key] = json.dumps(raw_value, ensure_ascii=False, separators=(",", ":"))
            elif isinstance(raw_value, bool):
                result[key] = "true" if raw_value else "false"
            elif raw_value is None:
                result[key] = ""
            else:
                result[key] = str(raw_value)
        return result

    def save_mcp_server_options(self, updates: dict[str, dict[str, Any]]) -> dict[str, Any]:
        with self._lock:
            path, config = self._load_mcp()
            servers = config["mcpServers"]
            unknown = sorted(name for name in updates if name not in servers)
            if unknown:
                raise ValueError(f"unknown MCP server(s): {', '.join(unknown)}")
            normalized: dict[str, dict[str, str]] = {}
            for server_name, raw in updates.items():
                normalized[str(server_name)] = self._normalized_env_options(raw)
                server = servers.get(server_name)
                if isinstance(server, dict):
                    server["env"] = normalized[str(server_name)]
            self._write_json_atomic(path, config)
            self._refresh_monitor_config(mcp_config=config)
        return {"ok": True, "mcp_config": str(path), "options": normalized, "restart_required": True}

    def _session_store(self) -> SessionContextStore:
        values = self._values()
        root = str(values.get("SESSION_CONTEXT_DIR") or DEFAULT_CONTEXT_DIR).strip() or str(DEFAULT_CONTEXT_DIR)
        return SessionContextStore(root, summary_max_chars=DEFAULT_SUMMARY_MAX_CHARS)

    def _publish_session(self, store: SessionContextStore) -> dict[str, Any]:
        snapshot = store.snapshot()
        self.monitor.replace_dialogue(snapshot.get("messages") or [])
        size = int(str(self._values().get("SESSION_CONTEXT_SIZE") or "6000"))
        self.monitor.set_context_state(snapshot, session_context_size=size)
        return snapshot

    def session_snapshot(self) -> dict[str, Any]:
        return self._publish_session(self._session_store())

    def new_session(self, title: str | None = None) -> dict[str, Any]:
        store = self._session_store()
        store.new_session(title)
        return self._publish_session(store)

    def select_session(self, session_id: str) -> dict[str, Any]:
        store = self._session_store()
        store.select_session(session_id)
        return self._publish_session(store)

    def rename_session(self, session_id: str, title: str) -> dict[str, Any]:
        store = self._session_store()
        store.rename_session(session_id, title)
        return self._publish_session(store)

    def clear_session(self, session_id: str) -> dict[str, Any]:
        store = self._session_store()
        store.clear_session_conversation(session_id, preserve_llm_summary=True)
        return self._publish_session(store)

    def save_session(self, session_id: str) -> dict[str, Any]:
        # Persisted messages/summary are already written on each mutation. LLM
        # summary refresh remains an engine service and is intentionally not
        # performed by the HTTP runtime.
        store = self._session_store()
        store.select_session(session_id)
        snapshot = self._publish_session(store)
        snapshot["llm_summary_refreshed"] = False
        return snapshot

    def delete_session(self, session_id: str) -> dict[str, Any]:
        store = self._session_store()
        store.delete_session(session_id)
        return self._publish_session(store)
