"""
Voice-First AI Personal Assistant with MCP Integration (Improved Version)

This example demonstrates a voice-enabled personal assistant that uses:
- Speech-to-text for voice input (OpenAI Whisper API or local Whisper)
- MCPAgent with multiple MCP servers (Linear, filesystem)
- Text-to-speech for voice output (ElevenLabs speak, system TTS, or none)

This version includes better error handling and fallback options.
"""

import asyncio
import base64
from contextlib import contextmanager
import io
import json
import logging
import os
from pathlib import Path
import re
import shutil
import socket
import signal
import subprocess
import sys
import tempfile
import threading
from typing import Any
import urllib.error
import urllib.request
import wave

import numpy as np
import openai
import pyaudio
import pygame
import pyttsx3
from elevenlabs.client import ElevenLabs
from elevenlabs.play import play
from elevenlabs.types.voice_settings import VoiceSettings
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from mcp_use import MCPAgent, MCPClient
from pydantic import AnyUrl

try:
    from .web_monitor import WebMonitor, build_service_state
    from .wake_word import apply_wake_word, parse_wake_words
except ImportError:
    from web_monitor import WebMonitor, build_service_state
    from wake_word import apply_wake_word, parse_wake_words

TTS_ENGINE = pyttsx3.init()
TTS_LOCK = threading.Lock()
FORCE_EXIT_REQUESTED = threading.Event()
DEFAULT_ELEVENLABS_VOICE_ID = "1EmYoP3UnnnwhlJKovEy"  # french male; ZF6FPAbjXT4488VcRRnw = english female
DEFAULT_OPENAI_TTS_MODEL = "gpt-4o-mini-tts"
DEFAULT_OPENAI_TTS_VOICE = "alloy"
DEFAULT_MCP_AGENT_TIMEOUT_SECONDS = 45.0
LOGGER = logging.getLogger(__name__)
AUTO_ENV_ONLINE = Path(".env.online")
AUTO_ENV_OFFLINE = Path(".env.offline")
AUTO_CONNECTIVITY_HOST = "api.openai.com"
AUTO_CONNECTIVITY_PORT = 443
AUTO_CONNECTIVITY_TIMEOUT = 2.0
AUTO_CHECK_INTERVAL = 10.0
EXTERNAL_STATE_FRESHNESS_RULE = (
    "Use conversation memory for context, preferences, and follow-up references, but not as the source of truth "
    "for live external state. When the user asks about the current state of anything outside this conversation, "
    "treat the answer as time-sensitive. Use the relevant MCP read tool before answering. Do not answer current "
    "external state from memory, previous tool results, or assumptions. If no suitable read tool is available, "
    "say that you cannot verify the current state."
)
TOOL_ACTION_FRESHNESS_RULE = (
    "Internal tool freshness rule: previous tool results and previous tool errors are not proof of the current "
    "state for a new user request. If the new request asks you to perform an external action or check an external "
    "state through tools, call the relevant tool again. Do not refuse a new action solely because a previous turn's "
    "tool call failed, timed out, or reported a disconnected service. Do not mention this internal rule."
)
MIXER_TARGET_RESOLUTION_RULE = (
    "Internal mixer safety rule: if this request reads or changes a named mixer object, call "
    "osc_find_named_target for that name before any get/set/mute/automation tool. Do not use remembered "
    "channel, bus, FX, aux, DCA, or matrix indexes from prior turns. For a bare label without explicit family, "
    "resolve globally across all families. If the resolution is fuzzy, stop and ask the user to confirm the "
    "resolved target before reading or writing. Do not mention this internal rule."
)
DEFAULT_VOICE_CANCEL_WORDS = (
    "stop",
    "stoppe",
    "stoppé",
    "annule",
    "annuler",
    "annulation",
    "arrete",
    "arrête",
    "arreter",
    "arrêter",
    "cancel",
)
OPENAI_TTS_VOICE_OPTIONS = [
    {"id": "alloy", "label": "Alloy"},
    {"id": "echo", "label": "Echo (masculine)"},
    {"id": "onyx", "label": "Onyx (masculine)"},
    {"id": "nova", "label": "Nova (feminine)"},
    {"id": "shimmer", "label": "Shimmer (feminine)"},
]
CLOUD_TTS_PROVIDER_OPTIONS = [
    {"id": "none", "label": "None"},
    {"id": "openai", "label": "OpenAI"},
    {"id": "elevenlabs", "label": "ElevenLabs"},
]
TTS_OUTPUT_OPTIONS = [
    {"id": "browser", "label": "Browser"},
    {"id": "backend", "label": "Backend"},
    {"id": "silent", "label": "Silent"},
]


@contextmanager
def suppress_native_stderr():
    """Temporarily silence native libraries that write directly to stderr."""
    try:
        sys.stderr.flush()
        stderr_fd = sys.stderr.fileno()
    except (AttributeError, OSError, ValueError):
        yield
        return

    saved_stderr_fd = None
    try:
        saved_stderr_fd = os.dup(stderr_fd)
        with open(os.devnull, "w") as devnull:
            os.dup2(devnull.fileno(), stderr_fd)
            yield
    finally:
        if saved_stderr_fd is not None:
            try:
                os.dup2(saved_stderr_fd, stderr_fd)
            finally:
                os.close(saved_stderr_fd)
CURRENT_STATE_QUERY_MARKERS = (
    "current",
    "currently",
    "now",
    "right now",
    "status",
    "state",
    "value",
    "level",
    "position",
    "configuration",
    "how much",
    "what is",
    "what's",
    "etat",
    "état",
    "actuel",
    "actuelle",
    "maintenant",
    "en ce moment",
    "statut",
    "connection",
    "connexion",
    "connected",
    "connecte",
    "connecté",
    "connectee",
    "connectée",
    "valeur",
    "niveau",
    "combien",
    "which mixer",
    "quel mixeur",
    "quel est",
    "quelle est",
    "a combien",
    "à combien",
)
DEFAULT_STT_PROMPT = (
    "Commandes courtes en français pour une console de mixage. "
    "Les commandes commencent souvent par: mets, met, règle, baisse, monte, coupe, mute, active, réactive. "
    "Ne colle pas le verbe 'mets' au nom qui suit: écris 'mets Claude', 'mets Voc-Claude', 'mets snare'."
)
FUSED_SET_COMMAND_RE = re.compile(r"^\s*(mets|met|me)([a-zà-ÿ][a-zà-ÿ0-9_-]{3,})(\b|$)", re.IGNORECASE)


def check_internet_connection(
    host: str = AUTO_CONNECTIVITY_HOST,
    port: int = AUTO_CONNECTIVITY_PORT,
    timeout: float = AUTO_CONNECTIVITY_TIMEOUT,
) -> bool:
    """Return whether a short TCP connection to a known internet host succeeds."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def request_force_exit(_signum=None, _frame=None) -> None:
    """Let the first Ctrl+C unwind normally and make repeated Ctrl+C decisive."""
    if FORCE_EXIT_REQUESTED.is_set():
        os._exit(130)
    FORCE_EXIT_REQUESTED.set()
    raise KeyboardInterrupt


def read_secret_from_env_values(values: dict, name: str) -> str | None:
    """Read a secret from a *_FILE entry in a parsed env profile."""
    file_path = (values.get(f"{name}_FILE") or "").strip()
    if not file_path:
        return None

    try:
        secret = Path(file_path).read_text().strip()
    except OSError as e:
        print(f"Auto monitor could not read {name}_FILE '{file_path}': {e}")
        return None

    return secret or None


def env_float_from_mapping(values: dict, name: str, default: float) -> float:
    value = values.get(name)
    if value is None or str(value).strip() == "":
        return default
    try:
        return float(str(value).strip())
    except ValueError:
        return default


def elevenlabs_playback_available() -> bool:
    """Return whether elevenlabs.play can play generated audio locally."""
    return shutil.which("ffplay") is not None


def local_tts_playback_available() -> bool:
    """Return whether pyttsx3 is likely to have a local audio player."""
    if sys.platform.startswith("linux"):
        return shutil.which("aplay") is not None
    return True


def cloud_tts_provider_from_values(values: dict) -> str:
    """Return the shared cloud TTS provider, preserving legacy env profiles."""
    configured = (values.get("CLOUD_TTS_PROVIDER") or "").strip().lower()
    if configured in {"none", "openai", "elevenlabs"}:
        return configured

    backend_provider = (values.get("TTS_PROVIDER") or "").strip().lower()
    if backend_provider in {"openai", "elevenlabs"}:
        return backend_provider

    web_provider = (values.get("WEB_TTS_PROVIDER") or "").strip().lower()
    if web_provider in {"openai", "elevenlabs"}:
        return web_provider

    if backend_provider == "none" and web_provider == "none":
        return "none"

    return "openai"


def tts_output_from_values(values: dict) -> str:
    """Return whether speech is played by the browser, backend, or nowhere."""
    backend_provider = (values.get("TTS_PROVIDER") or "").strip().lower()
    web_provider = (values.get("WEB_TTS_PROVIDER") or "").strip().lower()
    if backend_provider in {"openai", "elevenlabs", "pyttsx3"}:
        return "backend"
    if web_provider in {"openai", "elevenlabs"}:
        return "browser"
    return "silent"


def play_mp3_bytes(audio_bytes: bytes) -> None:
    """Play MP3 bytes locally through ffplay."""
    if not elevenlabs_playback_available():
        raise RuntimeError("ffplay is not available")

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as temp_file:
            temp_file.write(audio_bytes)
            temp_path = temp_file.name
        subprocess.run(
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", temp_path],
            check=True,
        )
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def speak_auto_network_status(text: str, env_file: Path, dotenv_values_func) -> None:
    """Speak a network status message with the TTS configured by the detected env file."""
    values = dotenv_values_func(env_file)
    tts_provider = (values.get("TTS_PROVIDER") or "elevenlabs").strip().lower()
    voice_id = (values.get("ELEVENLABS_VOICE_ID") or DEFAULT_ELEVENLABS_VOICE_ID).strip()

    with TTS_LOCK:
        if tts_provider == "none":
            print(f"Auto network status: {text}")
            return

        if tts_provider == "elevenlabs":
            elevenlabs_api_key = read_secret_from_env_values(values, "ELEVENLABS_API_KEY")
            if elevenlabs_api_key:
                try:
                    if not elevenlabs_playback_available():
                        return
                    client = ElevenLabs(api_key=elevenlabs_api_key)
                    audio = client.text_to_speech.convert(
                        text=text,
                        voice_id=voice_id,
                        model_id="eleven_multilingual_v2",
                        output_format="mp3_44100_128",
                        optimize_streaming_latency="2",
                        voice_settings=VoiceSettings(
                            speed=env_float_from_mapping(values, "WEB_TTS_SPEED", 1.0)
                        ),
                    )
                    play(audio)
                    return
                except Exception as e:
                    if local_tts_playback_available():
                        print(f"Auto network status ElevenLabs TTS failed: {e}")
                    else:
                        return

        if tts_provider == "openai":
            openai_api_key = read_secret_from_env_values(values, "OPENAI_API_KEY")
            if openai_api_key:
                try:
                    if not elevenlabs_playback_available():
                        return
                    client = openai.OpenAI(api_key=openai_api_key)
                    response = client.audio.speech.create(
                        model=(values.get("WEB_TTS_MODEL") or DEFAULT_OPENAI_TTS_MODEL).strip(),
                        voice=(values.get("WEB_TTS_VOICE") or DEFAULT_OPENAI_TTS_VOICE).strip(),
                        input=text.strip(),
                        response_format="mp3",
                        speed=env_float_from_mapping(values, "WEB_TTS_SPEED", 1.0),
                    )
                    play_mp3_bytes(response.read())
                    return
                except Exception as e:
                    if local_tts_playback_available():
                        print(f"Auto network status OpenAI TTS failed: {e}")
                    else:
                        return

        if not local_tts_playback_available():
            return
        try:
            TTS_ENGINE.say(text)
            TTS_ENGINE.runAndWait()
        except Exception as e:
            print(f"Auto network status local TTS failed: {e}")


class AutoNetworkMonitor:
    """Auto monitor: announce internet status changes and request runtime reloads."""

    def __init__(
        self,
        initial_online: bool,
        dotenv_values_func,
        reload_event: threading.Event | None = None,
        web_monitor: WebMonitor | None = None,
        interval: float = AUTO_CHECK_INTERVAL,
    ):
        self.current_online = initial_online
        self.dotenv_values_func = dotenv_values_func
        self.reload_event = reload_event
        self.web_monitor = web_monitor
        self.interval = interval
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="auto-network-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)

    def announce_initial_status(self) -> None:
        self._announce(self.current_online)

    def _run(self) -> None:
        while not self._stop_event.wait(self.interval):
            online = check_internet_connection()
            if online == self.current_online:
                continue

            self.current_online = online
            self._announce(online)
            if self.reload_event:
                self.reload_event.set()

    def _announce(self, online: bool) -> None:
        status_text = "Internet est en ligne" if online else "Connexion internet coupée"
        detected_env = AUTO_ENV_ONLINE if online else AUTO_ENV_OFFLINE
        print(f"Auto network status changed: {status_text}. Detected profile: {detected_env}")
        if self.web_monitor:
            self.web_monitor.update(internet=online, env_file=detected_env, mode="auto")
        speak_auto_network_status(status_text, detected_env, self.dotenv_values_func)

    @property
    def detected_env_file(self) -> Path:
        return AUTO_ENV_ONLINE if self.current_online else AUTO_ENV_OFFLINE


class VoiceAssistant:
    """Improved voice-enabled AI assistant with better error handling."""

    def __init__(
        self,
        openai_api_key: str | None = None,
        elevenlabs_api_key: str | None = None,
        model: str = "gpt-4o-mini",
        llm_provider: str = "openai",
        ollama_base_url: str = "http://localhost:11434",
        stt_provider: str = "openai-whisper",
        local_whisper_model: str = "base",
        stt_language: str | None = None,
        stt_prompt: str | None = None,
        tts_provider: str = "elevenlabs",
        elevenlabs_voice_id: str = DEFAULT_ELEVENLABS_VOICE_ID,
        thinking_sound_file: str = "thinking.wav",
        silence_threshold: int = 500,
        silence_duration: float = 1.5,
        tts_speed: float = 1.0,
        wake_words: list[str] | None = None,
        mcp_config: dict | None = None,
        mcp_load_server_prompt: bool = False,
        mcp_prompt_server: str | None = None,
        mcp_prompt_name: str | None = None,
        mcp_prompt_resource_uri: str | None = None,
        mcp_prompt_tool: str | None = None,
        mcp_prompt_sources: list[dict] | None = None,
        mcp_prompt_merge_mode: str = "append",
        mcp_agent_memory_enabled: bool = True,
        mcp_agent_timeout_seconds: float = DEFAULT_MCP_AGENT_TIMEOUT_SECONDS,
        voice_cancel_during_thinking: bool = False,
        notes_dir: str | None = None,
        system_prompt: str | None = None,
        reload_event: threading.Event | None = None,
        web_monitor: WebMonitor | None = None,
    ):
        """Initialize the voice assistant.

        Args:
            openai_api_key: OpenAI API key for Whisper API and GPT models
            elevenlabs_api_key: Optional ElevenLabs API key for TTS
            model: LLM model name to use (default: gpt-4o-mini)
            llm_provider: LLM provider (openai or ollama)
            ollama_base_url: Base URL for local Ollama server
            stt_provider: Speech-to-text provider (openai-whisper or local-whisper)
            local_whisper_model: Local faster-whisper model size or path
            stt_language: Transcription language code, or None for auto-detect
            stt_prompt: Optional STT context prompt to bias short command transcription
            tts_provider: Text-to-speech provider (elevenlabs, pyttsx3, or none)
            elevenlabs_voice_id: ElevenLabs voice ID (default: Rachel)
            thinking_sound_file: WAV file to loop while the LLM/MCP agent is processing a command
            silence_threshold: Audio silence detection threshold
            silence_duration: How long to wait after speech stops
            tts_speed: Cloud TTS speed for backend/non-web speech
            wake_words: Optional global wake word variants used to gate command processing
            mcp_config: Optional MCP server configuration dict
            mcp_load_server_prompt: Whether to load extra system instructions from an MCP server
            mcp_prompt_server: Logical MCP server name to query for instructions
            mcp_prompt_name: Optional MCP prompt name to fetch
            mcp_prompt_resource_uri: Optional MCP resource URI to read
            mcp_prompt_tool: Optional fallback MCP tool name to call for prompt text
            mcp_prompt_sources: Optional ordered list of MCP prompt sources
            mcp_prompt_merge_mode: How to merge remote instructions: append or replace
            mcp_agent_memory_enabled: Whether MCPAgent should keep conversation memory
            mcp_agent_timeout_seconds: Maximum seconds to wait for one LLM/MCP agent response
            voice_cancel_during_thinking: Listen for short spoken cancel words while the LLM/MCP agent is processing
            notes_dir: Directory for storing notes (default: temp dir)
            system_prompt: Optional custom system prompt for the assistant
            reload_event: Optional event used by auto mode to interrupt and reload the assistant
            web_monitor: Optional read-only web monitor for runtime state
        """
        # Audio configuration
        self.audio_format = pyaudio.paInt16
        self.channels = 1
        self.rate = 16000
        self.chunk = 1024
        self.silence_threshold = silence_threshold
        self.silence_duration = silence_duration
        self.tts_speed = max(0.6, min(1.8, float(tts_speed or 1.0)))
        self.wake_words = wake_words or []
        self.voice_cancel_during_thinking = voice_cancel_during_thinking
        self.voice_cancel_words = DEFAULT_VOICE_CANCEL_WORDS

        # Initialize audio components
        with suppress_native_stderr():
            self.audio = pyaudio.PyAudio()
        self.pygame_mixer_available = False
        try:
            with suppress_native_stderr():
                pygame.mixer.init()
            self.pygame_mixer_available = True
        except pygame.error as e:
            print(f"Audio output unavailable for thinking sound; continuing without it: {e}")

        # Speech-to-text configuration
        self.openai_api_key = openai_api_key
        self.stt_provider = stt_provider.lower()
        self.tts_provider = tts_provider.lower()
        self.local_whisper_model_name = local_whisper_model
        self.stt_language = stt_language or None
        self.stt_prompt = stt_prompt or DEFAULT_STT_PROMPT
        self.openai_client = None
        self.local_whisper_model = None
        if self.stt_provider == "openai-whisper" or self.tts_provider == "openai":
            self.openai_client = openai.OpenAI(api_key=openai_api_key)

        self.model = model
        self.llm_provider = llm_provider.lower()
        self.ollama_base_url = ollama_base_url

        # ElevenLabs client for text-to-speech
        self.elevenlabs_client = None
        self.elevenlabs_voice_id = elevenlabs_voice_id
        if self.tts_provider == "elevenlabs" and elevenlabs_api_key:
            self.elevenlabs_client = ElevenLabs(api_key=elevenlabs_api_key)

        # Short audio feedback while the agent is processing the user's command.
        self.thinking_sound_file = thinking_sound_file or "thinking.wav"
        self.thinking_sound_path = self._resolve_asset_path(self.thinking_sound_file)
        self.thinking_sound = None
        self.thinking_sound_channel = None
        self.thinking_sound_warning_shown = False
        self.thinking_sound_lock = threading.Lock()
        if self.pygame_mixer_available and self.thinking_sound_path:
            try:
                self.thinking_sound = pygame.mixer.Sound(str(self.thinking_sound_path))
            except pygame.error as e:
                print(f"Could not load thinking sound '{self.thinking_sound_path}': {e}")
                self.thinking_sound_warning_shown = True

        # MCP configuration
        self.mcp_config = mcp_config
        self.mcp_load_server_prompt = mcp_load_server_prompt
        self.mcp_prompt_server = mcp_prompt_server
        self.mcp_prompt_name = mcp_prompt_name
        self.mcp_prompt_resource_uri = mcp_prompt_resource_uri
        self.mcp_prompt_tool = mcp_prompt_tool
        self.mcp_prompt_sources = mcp_prompt_sources or []
        self.mcp_prompt_merge_mode = (mcp_prompt_merge_mode or "append").lower()
        self.mcp_agent_memory_enabled = mcp_agent_memory_enabled
        self.mcp_agent_timeout_seconds = max(1.0, float(mcp_agent_timeout_seconds or DEFAULT_MCP_AGENT_TIMEOUT_SECONDS))
        self.mcp_client = None
        self.agent = None
        self.reload_event = reload_event
        self.web_monitor = web_monitor
        self.pending_injected_command: str | None = None
        self.microphone_available = True
        self.microphone_warning_shown = False
        
        #self.system_prompt = system_prompt or (
        #    "You are a helpful voice assistant with access to various tools. Your name is mcp-use "
        #    "Be concise in your responses since they will be spoken aloud. Summarize your results. "
        #    "Reply in the same language as the user's latest request whenever possible. "
        #    "Behave like a great motivational speaker, and motivate me throughout the conversation."
        #)

        base_system_prompt = system_prompt or (
            "You are a helpful voice assistant with access to various tools. Your name is Live Stage Assistant. "
            "Be concise in your responses since they will be spoken aloud and have to be suitable for text-to-speech and API calls.. Summarize your results. "
            "Reply in the same language as the user's latest request whenever possible. "
            "Use plain text only. Do not use emojis, emoticons, markdown, bullets, symbols, or decorative characters. "
            "For spoken numeric values, write negative numbers with words: say 'moins 11 dB' in French "
            "and 'minus 11 dB' in English instead of '-11 dB'. "
            "Write measurement units in words for text-to-speech: say 'décibels' in French or 'decibels' "
            "in English instead of 'dB', and 'volts' instead of 'V'. "
            "Behave like a friendly calm and motivating assistant."
        )
        self.system_prompt = f"{base_system_prompt.rstrip()} {EXTERNAL_STATE_FRESHNESS_RULE}"
        if self.web_monitor:
            self.web_monitor.update(prompt=self.system_prompt)

        # Create a proper notes directory
        if notes_dir:
            self.notes_dir = notes_dir
        else:
            self.notes_dir = os.path.join(tempfile.gettempdir(), "voice_assistant_notes")
        os.makedirs(self.notes_dir, exist_ok=True)

        self._log_configured_mcp_prompt_sources()

    def _resolve_asset_path(self, value: str) -> Path | None:
        """Resolve a configured asset path, falling back to ./assets for bare filenames."""
        configured_path = Path(value).expanduser()
        if configured_path.is_absolute() and configured_path.exists():
            return configured_path

        if configured_path.exists():
            return configured_path

        assets_path = Path("assets") / configured_path
        if assets_path.exists():
            return assets_path

        return None

    def start_thinking_sound(self) -> None:
        """Loop the configured thinking sound until stop_thinking_sound is called."""
        if not self.pygame_mixer_available:
            return

        if not self.thinking_sound:
            if not self.thinking_sound_warning_shown:
                print(
                    f"Thinking sound '{self.thinking_sound_file}' not found. "
                    "Set THINKING_SOUND_FILE to a WAV file or place it in assets/."
                )
                self.thinking_sound_warning_shown = True
            return

        with self.thinking_sound_lock:
            if self.thinking_sound_channel and self.thinking_sound_channel.get_busy():
                return
            self.thinking_sound_channel = self.thinking_sound.play(loops=-1)

    def stop_thinking_sound(self) -> None:
        """Stop the thinking sound if it is currently playing."""
        if not self.pygame_mixer_available:
            return

        with self.thinking_sound_lock:
            if self.thinking_sound_channel:
                self.thinking_sound_channel.stop()
                self.thinking_sound_channel = None

    def _substitute_env_vars(self, config):
        """Recursively substitute environment variable placeholders in config."""
        if isinstance(config, dict):
            result = {}
            for key, value in config.items():
                result[key] = self._substitute_env_vars(value)
            return result

        if isinstance(config, list):
            return [self._substitute_env_vars(item) for item in config]

        if isinstance(config, str):
            # Support env placeholders anywhere in the string, e.g. "${ROOT}/dist/index.js".
            return re.sub(r"\$\{([^}]+)\}", lambda match: os.getenv(match.group(1), ""), config)

        return config

    def _filter_unavailable_mcp_servers(self, config: dict) -> dict:
        """Drop MCP servers that cannot be started locally."""
        server_configs = config.get("mcpServers")
        if not isinstance(server_configs, dict):
            return config

        filtered_config = dict(config)
        filtered_servers = {}

        for server_name, server_config in server_configs.items():
            command = server_config.get("command") if isinstance(server_config, dict) else None
            args = server_config.get("args", []) if isinstance(server_config, dict) else []

            if command and shutil.which(command) is None:
                print(
                    f"Could not start MCP server instance '{server_name}': "
                    f"command '{command}' was not found."
                )
                continue

            if command == "node":
                script_arg = args[0].strip() if args and isinstance(args[0], str) else ""
                if not script_arg:
                    print(
                        f"Could not start MCP server instance '{server_name}': "
                        "node script path is empty."
                    )
                    continue
                script_path = Path(script_arg).expanduser()
                if not script_path.exists():
                    print(
                        f"Could not start MCP server instance '{server_name}': "
                        f"node script was not found: {script_path}"
                    )
                    continue

            filtered_servers[server_name] = server_config

        filtered_config["mcpServers"] = filtered_servers
        return filtered_config

    def _build_llm(self):
        """Build the configured LLM."""
        if self.llm_provider == "ollama":
            print(f"Using Ollama model: {self.model} ({self.ollama_base_url})")
            return ChatOllama(model=self.model, base_url=self.ollama_base_url)

        print(f"Using OpenAI model: {self.model}")
        return ChatOpenAI(model=self.model, api_key=self.openai_api_key)

    def _text_from_mcp_content(self, content) -> str | None:
        """Extract text from common MCP prompt/resource/tool content objects."""
        if content is None:
            return None

        if isinstance(content, str):
            return content

        text = getattr(content, "text", None)
        if isinstance(text, str):
            return text

        resource = getattr(content, "resource", None)
        if resource is not None:
            return self._text_from_mcp_content(resource)

        return None

    def _join_mcp_texts(self, values) -> str | None:
        texts = []
        for value in values or []:
            text = self._text_from_mcp_content(value)
            if text:
                texts.append(text.strip())
        joined = "\n\n".join(text for text in texts if text)
        return joined or None

    def _mcp_capability_enabled(self, session, capability_name: str) -> bool | None:
        capabilities = getattr(getattr(session, "connector", None), "capabilities", None)
        if capabilities is None:
            return None
        return bool(getattr(capabilities, capability_name, None))

    def _build_mcp_prompt_sources(self, config: dict) -> list[dict]:
        sources = []
        for server_name, server_config in config.get("mcpServers", {}).items():
            prompt_config = server_config.get("assistantPrompt") or server_config.get("agentPrompt")
            if isinstance(prompt_config, dict):
                source = dict(prompt_config)
                source["server"] = server_name
                sources.append(source)

        if sources:
            return sources

        if self.mcp_prompt_sources:
            return self.mcp_prompt_sources

        if not any([self.mcp_prompt_server, self.mcp_prompt_name, self.mcp_prompt_resource_uri, self.mcp_prompt_tool]):
            return []

        return [
            {
                "server": self.mcp_prompt_server,
                "prompt_name": self.mcp_prompt_name,
                "resource_uri": self.mcp_prompt_resource_uri,
                "tool": self.mcp_prompt_tool,
            }
        ]

    def _source_value(self, source: dict, *keys: str) -> str | None:
        for key in keys:
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _log_configured_mcp_prompt_sources(self) -> None:
        if not self.mcp_config:
            return

        sources = self._build_mcp_prompt_sources(self.mcp_config)
        if not sources:
            return

        if self.mcp_load_server_prompt:
            self._log_mcp_prompt_info(
                "MCP startup prompt loading is enabled; configured source(s): "
                f"{self._describe_mcp_prompt_sources(sources)}"
            )
            return

        self._log_mcp_prompt_warning(
            "MCP startup prompt loading is disabled; configured source(s) will not be loaded: "
            f"{self._describe_mcp_prompt_sources(sources)}"
        )

    def _describe_mcp_prompt_source(self, source: dict) -> str:
        server_name = self._source_value(source, "server", "server_name") or "unspecified"
        prompt_name = self._source_value(source, "promptName", "prompt_name", "prompt", "name")
        resource_uri = self._source_value(source, "resourceUri", "resource_uri", "resource")
        tool_name = self._source_value(source, "tool", "toolName", "tool_name")
        parts = [f"server='{server_name}'"]
        if prompt_name:
            parts.append(f"prompt='{prompt_name}'")
        if resource_uri:
            parts.append(f"resource='{resource_uri}'")
        if tool_name:
            parts.append(f"tool='{tool_name}'")
        return " ".join(parts)

    def _describe_mcp_prompt_sources(self, sources: list[dict]) -> str:
        return "; ".join(self._describe_mcp_prompt_source(source) for source in sources)

    def _log_mcp_prompt_info(self, message: str) -> None:
        print(message)
        LOGGER.info(message)

    def _log_mcp_prompt_warning(self, message: str) -> None:
        print(f"⚠️ Warning: {message}")
        LOGGER.warning(message)

    def _format_loaded_mcp_prompts(self, loaded_prompts: list[dict]) -> str:
        sections = []
        for item in loaded_prompts:
            server_name = item["server"]
            text = item["text"].strip()
            sections.append(f'Instructions loaded from MCP server "{server_name}":\n{text}')
        return "\n\n".join(sections)

    def _describe_loaded_mcp_prompts(self, loaded_prompts: list[dict]) -> str:
        descriptions = []
        for item in loaded_prompts:
            source_type = item.get("source_type") or "unknown"
            source_id = item.get("source_id") or "unspecified"
            descriptions.append(f"{item['server']} via {source_type} '{source_id}'")
        return "; ".join(descriptions)

    def _merge_system_prompt(self, loaded_prompts: list[dict]) -> str:
        remote_prompt = self._format_loaded_mcp_prompts(loaded_prompts)
        if self.mcp_prompt_merge_mode == "replace":
            return remote_prompt

        if self.mcp_prompt_merge_mode != "append":
            self._log_mcp_prompt_warning(
                f"Unsupported MCP_PROMPT_MERGE_MODE '{self.mcp_prompt_merge_mode}'; using append mode."
            )

        return (
            f"{self.system_prompt.rstrip()}\n\n"
            "Additional instructions loaded from MCP servers:\n"
            f"{remote_prompt}"
        )

    async def _get_mcp_prompt_text(self, session, prompt_name: str, server_name: str) -> str | None:
        if not hasattr(session, "get_prompt"):
            self._log_mcp_prompt_warning(f"MCP server '{server_name}' cannot fetch prompts with this mcp-use session.")
            return None
        if self._mcp_capability_enabled(session, "prompts") is False:
            self._log_mcp_prompt_warning(f"MCP server '{server_name}' does not advertise prompt support.")
            return None

        try:
            prompts = await session.list_prompts() if hasattr(session, "list_prompts") else []
            if prompts and prompt_name not in {getattr(prompt, "name", None) for prompt in prompts}:
                self._log_mcp_prompt_warning(f"MCP prompt '{prompt_name}' was not found on server '{server_name}'.")
                return None

            result = await session.get_prompt(prompt_name)
            return self._join_mcp_texts(
                getattr(message, "content", None) for message in getattr(result, "messages", [])
            )
        except Exception as e:
            self._log_mcp_prompt_warning(
                f"Failed to load MCP prompt '{prompt_name}' from server '{server_name}': {e}"
            )
            return None

    async def _get_mcp_resource_text(self, session, resource_uri: str, server_name: str) -> str | None:
        if not hasattr(session, "read_resource"):
            self._log_mcp_prompt_warning(f"MCP server '{server_name}' cannot read resources with this mcp-use session.")
            return None
        if self._mcp_capability_enabled(session, "resources") is False:
            self._log_mcp_prompt_warning(f"MCP server '{server_name}' does not advertise resource support.")
            return None

        try:
            resources = await session.list_resources() if hasattr(session, "list_resources") else []
            if resources and resource_uri not in {str(getattr(resource, "uri", "")) for resource in resources}:
                self._log_mcp_prompt_warning(
                    f"MCP resource '{resource_uri}' was not found on server '{server_name}'."
                )
                return None

            result = await session.read_resource(AnyUrl(resource_uri))
            return self._join_mcp_texts(getattr(result, "contents", []))
        except Exception as e:
            self._log_mcp_prompt_warning(
                f"Failed to read MCP resource '{resource_uri}' from server '{server_name}': {e}"
            )
            return None

    async def _get_mcp_tool_prompt_text(self, session, tool_name: str, server_name: str) -> str | None:
        if not hasattr(session, "call_tool"):
            self._log_mcp_prompt_warning(f"MCP server '{server_name}' cannot call tools with this mcp-use session.")
            return None
        if self._mcp_capability_enabled(session, "tools") is False:
            self._log_mcp_prompt_warning(
                f"MCP server '{server_name}' does not advertise tool support for fallback prompt loading."
            )
            return None

        try:
            tools = await session.list_tools() if hasattr(session, "list_tools") else []
            if tools and tool_name not in {getattr(tool, "name", None) for tool in tools}:
                self._log_mcp_prompt_warning(
                    f"MCP prompt fallback tool '{tool_name}' was not found on server '{server_name}'."
                )
                return None

            result = await session.call_tool(tool_name, {})
            if getattr(result, "isError", False):
                self._log_mcp_prompt_warning(
                    f"MCP prompt fallback tool '{tool_name}' returned an error on server '{server_name}'."
                )
                return None

            text = self._join_mcp_texts(getattr(result, "content", []))
            if text:
                return text

            structured_content = getattr(result, "structuredContent", None)
            if isinstance(structured_content, dict):
                for key in ("prompt", "system_prompt", "instructions", "text"):
                    value = structured_content.get(key)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
        except Exception as e:
            self._log_mcp_prompt_warning(
                f"Failed to call MCP prompt fallback tool '{tool_name}' on server '{server_name}': {e}"
            )
            return None

        return None

    async def _load_prompt_from_mcp_source(self, source: dict, config: dict) -> dict | None:
        server_name = self._source_value(source, "server", "server_name")
        prompt_name = self._source_value(source, "promptName", "prompt_name", "prompt", "name")
        resource_uri = self._source_value(source, "resourceUri", "resource_uri", "resource")
        tool_name = self._source_value(source, "tool", "toolName", "tool_name")

        if not server_name:
            self._log_mcp_prompt_warning("Skipping MCP prompt source without a server name.")
            return None

        if server_name not in config.get("mcpServers", {}):
            self._log_mcp_prompt_warning(
                f"MCP prompt server '{server_name}' is not configured; skipping this prompt source."
            )
            return None

        if not any([prompt_name, resource_uri, tool_name]):
            self._log_mcp_prompt_warning(
                f"MCP prompt source for server '{server_name}' has no prompt name, resource URI, "
                "or fallback tool configured."
            )
            return None

        try:
            session = self.mcp_client.get_session(server_name)
        except ValueError:
            try:
                session = await self.mcp_client.create_session(server_name)
            except Exception as e:
                self._log_mcp_prompt_warning(f"Failed to create MCP session for prompt server '{server_name}': {e}")
                return None

        if session is None:
            self._log_mcp_prompt_warning(f"MCP prompt server '{server_name}' did not provide a usable session.")
            return None

        remote_prompt = None
        source_type = None
        source_id = None
        if prompt_name:
            remote_prompt = await self._get_mcp_prompt_text(session, prompt_name, server_name)
            source_type = "prompt"
            source_id = prompt_name

        if not remote_prompt and resource_uri:
            remote_prompt = await self._get_mcp_resource_text(session, resource_uri, server_name)
            source_type = "resource"
            source_id = resource_uri

        if not remote_prompt and tool_name:
            remote_prompt = await self._get_mcp_tool_prompt_text(session, tool_name, server_name)
            source_type = "tool"
            source_id = tool_name

        if not remote_prompt:
            self._log_mcp_prompt_warning(f"No MCP server instructions were loaded from server '{server_name}'.")
            return None

        return {
            "server": server_name,
            "source_type": source_type,
            "source_id": source_id,
            "text": remote_prompt,
        }

    async def _load_mcp_server_prompt(self, config: dict) -> str | None:
        sources = self._build_mcp_prompt_sources(config)
        if not self.mcp_load_server_prompt:
            return None

        if not sources:
            self._log_mcp_prompt_warning("MCP_LOAD_SERVER_PROMPT is true but no MCP prompt sources are configured.")
            return None

        self._log_mcp_prompt_info(
            "MCP startup prompt loading enabled. Requested source(s): "
            f"{self._describe_mcp_prompt_sources(sources)}"
        )

        loaded_prompts = []
        for source in sources:
            loaded_prompt = await self._load_prompt_from_mcp_source(source, config)
            if loaded_prompt:
                loaded_prompts.append(loaded_prompt)

        if not loaded_prompts:
            self._log_mcp_prompt_warning("No MCP server instructions were loaded; keeping the local system prompt.")
            return None

        loaded_summary = self._describe_loaded_mcp_prompts(loaded_prompts)
        log_message = (
            f"Loaded and merged {len(loaded_prompts)} MCP prompt source(s) "
            f"with merge mode '{self.mcp_prompt_merge_mode}': {loaded_summary}"
        )
        self._log_mcp_prompt_info(log_message)

        return self._merge_system_prompt(loaded_prompts)

    async def _create_missing_mcp_sessions(self) -> None:
        """Create any sessions not already opened by startup prompt loading."""
        for server_name in self.mcp_client.get_server_names():
            if server_name not in self.mcp_client.sessions:
                await self.mcp_client.create_session(server_name)

    async def initialize_mcp(self):
        """Initialize MCP client and agent with proper error handling."""
        print("Initializing MCP servers...")
        if self.web_monitor:
            self.web_monitor.update(
                services={"MCP": {"status": "initializing", "detail": "opening configured sessions"}}
            )
        config = {"mcpServers": {}}

        # Use provided config or load from file
        if self.mcp_config:
            config = self.mcp_config
        else:
            # Try to load from mcp_servers.json
            config_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "mcp_servers.json")
            if os.path.exists(config_file):
                with open(config_file) as f:
                    config = json.load(f)

        # Replace environment variable placeholders
        config = self._substitute_env_vars(config)
        config = self._filter_unavailable_mcp_servers(config)

        try:
            # Create MCP client
            self.mcp_client = MCPClient.from_dict(config)
            merged_prompt = await self._load_mcp_server_prompt(config)
            if merged_prompt:
                self.system_prompt = merged_prompt
            if self.web_monitor:
                self.web_monitor.update(prompt=self.system_prompt)
            if self.mcp_load_server_prompt and self.mcp_client.sessions:
                await self._create_missing_mcp_sessions()

            # Create LLM
            llm = self._build_llm()

            # Create agent with memory
            self.agent = MCPAgent(
                llm=llm,
                client=self.mcp_client,
                max_steps=10,
                memory_enabled=self.mcp_agent_memory_enabled,
                system_prompt=self.system_prompt,
            )
            await self.agent.initialize()

            print("✓ MCP servers initialized successfully!")
            if self.web_monitor:
                server_names = sorted(config.get("mcpServers", {}).keys())
                detail = ", ".join(server_names) if server_names else "no configured servers"
                self.web_monitor.update(services={"MCP": {"status": "initialized", "detail": detail}})
            return True

        except Exception as e:
            print(f"✗ Error initializing MCP: {e}")
            if self.web_monitor:
                self.web_monitor.update(services={"MCP": {"status": "error", "detail": str(e)}})
            return False

    def detect_silence(self, audio_data: bytes) -> bool:
        """Detect if audio contains silence."""
        audio_array = np.frombuffer(audio_data, dtype=np.int16)
        return np.max(np.abs(audio_array)) < self.silence_threshold

    def record_audio(self) -> bytes | None:
        """Record audio from microphone."""
        if not self.microphone_available:
            return None

        print("\nListening... (speak now)")

        stream = None
        try:
            with suppress_native_stderr():
                stream = self.audio.open(
                    format=self.audio_format,
                    channels=self.channels,
                    rate=self.rate,
                    input=True,
                    frames_per_buffer=self.chunk,
                )

            frames = []
            silence_frames = 0
            silence_frame_threshold = int(self.rate / self.chunk * self.silence_duration)
            has_speech = False

            while True:
                if self.web_monitor:
                    injected_command = self.web_monitor.pop_injected_command()
                    if injected_command:
                        self.pending_injected_command = injected_command
                        print("Injected command received while listening. Stopping microphone capture.")
                        break

                if self.reload_event and self.reload_event.is_set():
                    print("Auto environment reload requested while recording.")
                    break

                data = stream.read(self.chunk, exception_on_overflow=False)
                frames.append(data)

                if self.detect_silence(data):
                    silence_frames += 1
                    if has_speech and silence_frames > silence_frame_threshold:
                        break
                else:
                    silence_frames = 0
                    has_speech = True

                if len(frames) > self.rate / self.chunk * 30:
                    break

            stream.stop_stream()
            stream.close()

            if self.pending_injected_command:
                return None

            if self.reload_event and self.reload_event.is_set():
                return None

            if not has_speech:
                print("No speech detected.")
                return None

            print("Processing...")
            return b"".join(frames)

        except Exception as e:
            print(f"Error recording audio: {e}")
            self._mark_microphone_unavailable(e)
            return None
        finally:
            if stream:
                try:
                    if stream.is_active():
                        stream.stop_stream()
                    stream.close()
                except Exception:
                    pass

    def _mark_microphone_unavailable(self, error: Exception) -> None:
        """Switch to text fallback when the microphone cannot be opened."""
        error_text = str(error)
        permanent_markers = (
            "Invalid input device",
            "No Default Input Device Available",
            "no default input device",
            "Unknown PCM",
        )
        error_code = getattr(error, "errno", None)
        if error_code != -9996 and not any(marker.lower() in error_text.lower() for marker in permanent_markers):
            return

        self.microphone_available = False
        if self.web_monitor:
            self.web_monitor.update(
                services={"Audio input": {"status": "unavailable", "detail": error_text}}
            )
        if not self.microphone_warning_shown:
            print("Microphone unavailable. Falling back to text commands.")
            if self.web_monitor:
                print("Use the web monitor Inject Command field to send commands.")
            else:
                print("Type commands in the terminal prompt.")
            self.microphone_warning_shown = True

    async def wait_for_text_fallback_command(self) -> str | None:
        """Wait for a command when microphone input is unavailable."""
        if self.web_monitor:
            while True:
                if self.reload_event and self.reload_event.is_set():
                    return None
                injected_command = self.web_monitor.pop_injected_command()
                if injected_command:
                    return injected_command
                await asyncio.sleep(0.5)

        try:
            return (await asyncio.to_thread(input, "\nText command> ")).strip() or None
        except EOFError:
            return "exit"

    def _write_wav(self, audio_data: bytes, audio_file) -> None:
        """Write recorded audio bytes as a WAV file-like object."""
        with wave.open(audio_file, "wb") as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(self.audio.get_sample_size(self.audio_format))
            wf.setframerate(self.rate)
            wf.writeframes(audio_data)

    def record_voice_cancel_audio(self, stop_event: threading.Event) -> bytes | None:
        """Capture a short audio window while the assistant is thinking."""
        if not self.microphone_available:
            return None

        stream = None
        try:
            with suppress_native_stderr():
                stream = self.audio.open(
                    format=self.audio_format,
                    channels=self.channels,
                    rate=self.rate,
                    input=True,
                    frames_per_buffer=self.chunk,
                )

            frames = []
            silence_frames = 0
            silence_frame_threshold = max(1, int(self.rate / self.chunk * 0.45))
            max_frames = max(1, int(self.rate / self.chunk * 1.4))
            has_speech = False

            while len(frames) < max_frames and not stop_event.is_set():
                data = stream.read(self.chunk, exception_on_overflow=False)
                frames.append(data)

                if self.detect_silence(data):
                    silence_frames += 1
                    if has_speech and silence_frames > silence_frame_threshold:
                        break
                else:
                    silence_frames = 0
                    has_speech = True

            if stop_event.is_set() or not has_speech:
                return None
            return b"".join(frames)

        except Exception as e:
            print(f"Voice cancel listener unavailable: {e}")
            return None
        finally:
            if stream:
                try:
                    if stream.is_active():
                        stream.stop_stream()
                    stream.close()
                except Exception:
                    pass

    def is_voice_cancel_phrase(self, text: str) -> bool:
        normalized = text.strip().lower()
        normalized = re.sub(r"[^\wÀ-ÿ'-]+", " ", normalized).strip()
        if not normalized:
            return False
        words = set(normalized.split())
        return any(word in words or normalized == word for word in self.voice_cancel_words)

    async def listen_for_voice_cancel_during_thinking(
        self,
        stop_event: threading.Event,
        process_task: asyncio.Task,
    ) -> bool:
        """Return True when a spoken cancel phrase is detected during command processing."""
        if not self.voice_cancel_during_thinking or not self.microphone_available:
            return False

        print("Voice cancel listener active during thinking.")
        while not stop_event.is_set() and not process_task.done():
            audio_data = await asyncio.to_thread(self.record_voice_cancel_audio, stop_event)
            if stop_event.is_set() or process_task.done():
                return False
            if not audio_data:
                await asyncio.sleep(0.05)
                continue

            text = await asyncio.to_thread(self.audio_to_text, audio_data)
            if not text:
                continue
            print(f"Voice cancel listener heard: {text}")
            if self.is_voice_cancel_phrase(text):
                return True

        return False

    def _load_local_whisper_model(self):
        """Lazy-load faster-whisper so online-only users do not pay the import cost."""
        if self.local_whisper_model:
            return self.local_whisper_model

        try:
            from faster_whisper import WhisperModel
        except ImportError:
            print("Local Whisper requires faster-whisper. Install it with: uv pip install -e .")
            return None

        print(f"Loading local Whisper model: {self.local_whisper_model_name}")
        self.local_whisper_model = WhisperModel(self.local_whisper_model_name, device="auto", compute_type="int8")
        return self.local_whisper_model

    def audio_to_text(self, audio_data: bytes) -> str | None:
        """Convert audio to text using the configured speech-to-text provider."""
        if self.stt_provider == "local-whisper":
            return self.audio_to_text_local_whisper(audio_data)
        return self.audio_to_text_openai_whisper(audio_data)

    def normalize_stt_command_text(self, text: str) -> str:
        """Fix narrow STT artifacts that hurt short mixer commands."""
        cleaned = text.strip()

        def split_fused_set_command(match: re.Match[str]) -> str:
            verb = match.group(1)
            target = match.group(2)
            canonical_verb = "mets" if verb.lower() in {"me", "met", "mets"} else verb
            return f"{canonical_verb} {target}"

        return FUSED_SET_COMMAND_RE.sub(split_fused_set_command, cleaned, count=1)

    def audio_to_text_openai_whisper(self, audio_data: bytes) -> str | None:
        """Convert audio to text using OpenAI Whisper API."""
        if not self.openai_client:
            print("OpenAI Whisper is selected, but no OpenAI client is configured.")
            return None

        try:
            wav_buffer = io.BytesIO()
            self._write_wav(audio_data, wav_buffer)
            wav_buffer.seek(0)
            wav_buffer.name = "audio.wav"

            kwargs = {"model": "whisper-1", "file": wav_buffer}
            if self.stt_language:
                kwargs["language"] = self.stt_language
            if self.stt_prompt:
                kwargs["prompt"] = self.stt_prompt
            response = self.openai_client.audio.transcriptions.create(**kwargs)

            text = response.text.strip()
            return self.normalize_stt_command_text(text) if text else None

        except Exception as e:
            print(f"Error transcribing audio: {e}")
            return None

    def web_audio_to_text_openai(
        self,
        audio_data: bytes,
        mime_type: str,
        model: str = "whisper-1",
    ) -> str | None:
        """Convert browser-recorded audio to text using OpenAI from the backend."""
        if not self.openai_client:
            raise ValueError("OpenAI client is not configured")
        if not audio_data:
            raise ValueError("audio data is empty")

        extension = "webm"
        if "mp4" in mime_type:
            extension = "mp4"
        elif "mpeg" in mime_type or "mp3" in mime_type:
            extension = "mp3"
        elif "ogg" in mime_type:
            extension = "ogg"
        elif "wav" in mime_type:
            extension = "wav"

        audio_buffer = io.BytesIO(audio_data)
        audio_buffer.name = f"web-audio.{extension}"
        kwargs = {"model": model, "file": audio_buffer}
        if self.stt_language:
            kwargs["language"] = self.stt_language
        if self.stt_prompt:
            kwargs["prompt"] = self.stt_prompt
        response = self.openai_client.audio.transcriptions.create(**kwargs)
        text = response.text.strip()
        return self.normalize_stt_command_text(text) if text else None

    def web_audio_transcription_result(
        self,
        audio_data: bytes,
        mime_type: str,
        model: str = "whisper-1",
        apply_wake_word_gate: bool = False,
    ) -> dict[str, Any]:
        text = self.web_audio_to_text_openai(audio_data, mime_type, model=model) or ""
        if not text:
            return {"text": "", "accepted": False, "command_text": "", "message": "No speech detected."}

        if apply_wake_word_gate and self.wake_words:
            should_process, matched_wake_word, command_text = apply_wake_word(text, self.wake_words)
            if not should_process:
                return {
                    "text": text,
                    "accepted": False,
                    "command_text": "",
                    "matched_wake_word": "",
                    "message": "Wake word not detected.",
                }
            return {
                "text": text,
                "accepted": True,
                "command_text": command_text,
                "matched_wake_word": matched_wake_word or "",
            }

        return {"text": text, "accepted": True, "command_text": text, "matched_wake_word": ""}

    def audio_to_text_local_whisper(self, audio_data: bytes) -> str | None:
        """Convert audio to text using faster-whisper locally."""
        model = self._load_local_whisper_model()
        if not model:
            return None

        wav_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as wav_file:
                wav_path = wav_file.name
                self._write_wav(audio_data, wav_file)

            segments, _info = model.transcribe(wav_path, language=self.stt_language, initial_prompt=self.stt_prompt)
            text = "".join(segment.text for segment in segments).strip()
            return self.normalize_stt_command_text(text) if text else None

        except Exception as e:
            print(f"Error transcribing audio locally: {e}")
            return None

        finally:
            if wav_path and os.path.exists(wav_path):
                try:
                    os.unlink(wav_path)
                except OSError:
                    pass

    async def text_to_speech(self, text: str) -> bool:
        """Convert text to speech using the configured provider."""
        if self.tts_provider == "none":
            return False

        if self.tts_provider == "pyttsx3":
            return self.text_to_speech_pyttsx3(text)

        if self.tts_provider == "openai":
            if not self.openai_client:
                if local_tts_playback_available():
                    print("OpenAI TTS selected but OPENAI_API_KEY is missing. Falling back to pyttsx3...")
                else:
                    return False
            else:
                try:
                    with TTS_LOCK:
                        audio = self.generate_openai_tts_audio(text, speed=self.tts_speed)
                        play_mp3_bytes(audio)
                    return True
                except Exception as e:
                    if local_tts_playback_available():
                        print(f"OpenAI TTS failed: {e}")
                        print("Falling back to local pyttsx3 TTS...")
                    else:
                        return False

        elif self.tts_provider == "elevenlabs":
            if self.elevenlabs_client:
                if not elevenlabs_playback_available():
                    return self.text_to_speech_pyttsx3(text)
                try:
                    with TTS_LOCK:
                        audio = self.generate_elevenlabs_tts_audio(text, speed=self.tts_speed)
                        play(audio)
                    return True
                except Exception as e:
                    if local_tts_playback_available():
                        print(f"ElevenLabs TTS failed: {e}")
                        print("Falling back to local pyttsx3 TTS...")
                    else:
                        return False
            elif local_tts_playback_available():
                print("ElevenLabs TTS selected but ELEVENLABS_API_KEY is missing. Falling back to pyttsx3...")
            else:
                return False
        else:
            if local_tts_playback_available():
                print(f"Unknown TTS provider '{self.tts_provider}'. Falling back to pyttsx3...")
            else:
                return False

        return self.text_to_speech_pyttsx3(text)

    def text_to_speech_pyttsx3(self, text: str) -> bool:
        """Speak text through the local system TTS engine."""
        if not local_tts_playback_available():
            return False

        try:
            with TTS_LOCK:
                TTS_ENGINE.say(text)
                TTS_ENGINE.runAndWait()
            return True
        except Exception as e:
            print(f"Local pyttsx3 TTS failed: {e}")
            return False

    def generate_openai_tts_audio(
        self,
        text: str,
        model: str = DEFAULT_OPENAI_TTS_MODEL,
        voice: str = DEFAULT_OPENAI_TTS_VOICE,
        speed: float | None = None,
    ) -> bytes:
        """Generate MP3 speech with OpenAI."""
        if not self.openai_client:
            raise ValueError("OpenAI client is not configured")
        cleaned_text = text.strip()
        if not cleaned_text:
            raise ValueError("text is required")

        response = self.openai_client.audio.speech.create(
            model=model,
            voice=voice,
            input=cleaned_text,
            response_format="mp3",
            speed=max(0.6, min(1.8, float(speed or 1.0))),
        )
        return response.read()

    def generate_elevenlabs_tts_audio(self, text: str, speed: float | None = None) -> Any:
        """Generate MP3 speech with ElevenLabs."""
        if not self.elevenlabs_client:
            raise ValueError("ElevenLabs client is not configured")
        cleaned_text = text.strip()
        if not cleaned_text:
            raise ValueError("text is required")

        return self.elevenlabs_client.text_to_speech.convert(
            text=cleaned_text,
            voice_id=self.elevenlabs_voice_id,
            model_id="eleven_multilingual_v2",
            output_format="mp3_44100_128",
            optimize_streaming_latency="2",
            voice_settings=VoiceSettings(speed=max(0.6, min(1.8, float(speed or 1.0)))),
        )

    def web_text_to_speech_openai(
        self,
        text: str,
        model: str = DEFAULT_OPENAI_TTS_MODEL,
        voice: str = DEFAULT_OPENAI_TTS_VOICE,
    ) -> dict[str, Any]:
        """Generate browser-playable speech using OpenAI from the backend."""
        audio_bytes = self.generate_openai_tts_audio(text, model=model, voice=voice)
        return {
            "audio_base64": base64.b64encode(audio_bytes).decode("ascii"),
            "mime_type": "audio/mpeg",
        }

    def web_text_to_speech_elevenlabs(self, text: str) -> dict[str, Any]:
        """Generate browser-playable speech using ElevenLabs from the backend."""
        audio = self.generate_elevenlabs_tts_audio(text)
        audio_bytes = audio if isinstance(audio, bytes) else b"".join(audio)
        return {
            "audio_base64": base64.b64encode(audio_bytes).decode("ascii"),
            "mime_type": "audio/mpeg",
        }

    async def process_command(self, text: str) -> str:
        """Process user command with MCP agent."""
        print(f"\nYou said: {text}")
        if self.web_monitor:
            self.web_monitor.append_dialogue("user", text)
            self.web_monitor.set_assistant_busy(True)

        # Special commands
        if text.lower() in ["exit", "quit", "goodbye"]:
            return "Goodbye! Have a great day!"

        if text.lower() == "clear":
            if self.agent:
                self.agent.clear_conversation_history()
            return "Conversation history cleared."

        # Process with MCP agent
        if not self.agent:
            return "Sorry, the assistant is not properly initialized."

        self.start_thinking_sound()
        try:
            agent_input = self._with_runtime_instructions(text)
            response = await asyncio.wait_for(
                self.agent.run(agent_input),
                timeout=self.mcp_agent_timeout_seconds,
            )
            return response
        except asyncio.TimeoutError:
            return "La demande prend trop de temps à s'exécuter. Merci de réessayer avec une demande plus simple."
        except Exception as e:
            error_text = str(e)
            if "context_length_exceeded" in error_text or "maximum context length" in error_text:
                return (
                    "I reached the model context limit because tool definitions are too large for the current model. "
                    "Please switch to a larger-context model (for example gpt-4o-mini or gpt-4o), "
                    "or reduce enabled MCP servers/tools."
                )
            return f"Sorry, I encountered an error: {error_text}"
        finally:
            self.stop_thinking_sound()

    def _looks_like_current_external_state_query(self, text: str) -> bool:
        normalized = text.lower()
        return any(marker in normalized for marker in CURRENT_STATE_QUERY_MARKERS)

    def _with_runtime_instructions(self, text: str) -> str:
        instructions = [MIXER_TARGET_RESOLUTION_RULE, TOOL_ACTION_FRESHNESS_RULE]
        if not self._looks_like_current_external_state_query(text):
            return f"{text}\n\n" + "\n".join(instructions)

        instructions.append(
            "Internal freshness rule: this appears to ask for current external state. "
            "Use the relevant MCP read tool before answering. Do not answer from memory, "
            "previous tool results, or assumptions. If no suitable read tool is available, "
            "say that you cannot verify the current state. Do not mention this internal rule."
        )
        return f"{text}\n\n" + "\n".join(instructions)

    async def run(self):
        """Main loop for the voice assistant."""
        print("\n===== Voice-First AI Assistant (Improved) =====")
        print("\nCommands: 'help', 'clear', 'exit'")
        print("===============================================\n")

        # Initialize MCP
        if not await self.initialize_mcp():
            print("Failed to initialize MCP. Exiting.")
            return "exit"

        try:
            while True:
                if self.reload_event and self.reload_event.is_set():
                    print("Auto environment reload requested. Stopping current assistant.")
                    return "reload"

                text = self.pending_injected_command
                self.pending_injected_command = None
                if not text and self.web_monitor:
                    text = self.web_monitor.pop_injected_command()
                if text:
                    print(f"Injected command consumed: {text}")
                else:
                    text_from_fallback = False
                    if not self.microphone_available:
                        text = await self.wait_for_text_fallback_command()
                        if self.reload_event and self.reload_event.is_set():
                            print("Auto environment reload requested. Stopping current assistant.")
                            return "reload"
                        if not text:
                            continue
                        print(f"Text fallback command consumed: {text}")
                        text_from_fallback = True
                        audio_data = None
                    else:
                        audio_data = self.record_audio()

                    if self.reload_event and self.reload_event.is_set():
                        print("Auto environment reload requested. Stopping current assistant.")
                        return "reload"
                    if text_from_fallback:
                        pass
                    elif not self.microphone_available:
                        continue
                    elif not audio_data:
                        continue
                    else:
                        # Convert to text
                        text = self.audio_to_text(audio_data)
                        if self.reload_event and self.reload_event.is_set():
                            print("Auto environment reload requested. Stopping current assistant.")
                            return "reload"
                        if not text:
                            continue

                        should_process, matched_wake_word, command_text = apply_wake_word(text, self.wake_words)
                        if not should_process:
                            print("Wake word not detected. Ignoring transcription.")
                            continue
                        if matched_wake_word:
                            print(f"Wake word detected: {matched_wake_word}")
                            if command_text != text:
                                print(f"Command after wake word: {command_text}")
                        text = command_text

                # Process command
                process_task = asyncio.create_task(self.process_command(text))
                voice_cancel_stop_event = None
                voice_cancel_task = None
                if self.voice_cancel_during_thinking and self.microphone_available:
                    voice_cancel_stop_event = threading.Event()
                    voice_cancel_task = asyncio.create_task(
                        self.listen_for_voice_cancel_during_thinking(
                            voice_cancel_stop_event,
                            process_task,
                        )
                    )
                command_cancelled = False
                try:
                    while not process_task.done():
                        voice_cancel_detected = False
                        if voice_cancel_task is not None and voice_cancel_task.done() and not voice_cancel_task.cancelled():
                            voice_cancel_error = voice_cancel_task.exception()
                            if voice_cancel_error:
                                print(f"Voice cancel listener stopped: {voice_cancel_error}")
                                voice_cancel_task = None
                            else:
                                voice_cancel_detected = voice_cancel_task.result()
                        if self.web_monitor and self.web_monitor.pop_cancel_requested():
                            print("Web monitor cancel requested. Cancelling current command.")
                            process_task.cancel()
                            try:
                                await process_task
                            except asyncio.CancelledError:
                                pass
                            self.web_monitor.set_assistant_busy(False)
                            command_cancelled = True
                            break
                        if voice_cancel_detected:
                            print("Voice cancel phrase detected. Cancelling current command.")
                            process_task.cancel()
                            try:
                                await process_task
                            except asyncio.CancelledError:
                                pass
                            if self.web_monitor:
                                self.web_monitor.set_assistant_busy(False)
                            command_cancelled = True
                            break
                        if self.reload_event and self.reload_event.is_set():
                            print("Auto environment reload requested. Cancelling current command.")
                            process_task.cancel()
                            try:
                                await process_task
                            except asyncio.CancelledError:
                                pass
                            if self.web_monitor:
                                self.web_monitor.set_assistant_busy(False)
                            return "reload"
                        await asyncio.sleep(0.1)
                finally:
                    if voice_cancel_stop_event is not None:
                        voice_cancel_stop_event.set()
                    if voice_cancel_task is not None and not voice_cancel_task.done():
                        voice_cancel_task.cancel()
                        try:
                            await voice_cancel_task
                        except asyncio.CancelledError:
                            pass

                if command_cancelled:
                    continue

                response = await process_task
                if self.reload_event and self.reload_event.is_set():
                    print("Auto environment reload requested. Discarding current response.")
                    if self.web_monitor:
                        self.web_monitor.set_assistant_busy(False)
                    return "reload"

                print(f"\nAssistant: {response}")
                if self.web_monitor:
                    self.web_monitor.append_dialogue("assistant", response)
                    self.web_monitor.set_assistant_busy(False)

                # Check for exit
                if text.lower() in ["exit", "quit", "goodbye"]:
                    break

                # Try to speak the response
                await self.text_to_speech(response)

        except KeyboardInterrupt:
            print("\n\nInterrupted by user.")
            return "exit"
        finally:
            # Cleanup
            try:
                self.stop_thinking_sound()
            except Exception:
                pass
            try:
                self.audio.terminate()
            except Exception:
                pass
            if self.pygame_mixer_available:
                try:
                    pygame.mixer.quit()
                except Exception:
                    pass
            try:
                TTS_ENGINE.stop()
            except Exception:
                pass
            if self.mcp_client and self.mcp_client.sessions:
                try:
                    await asyncio.wait_for(self.mcp_client.close_all_sessions(), timeout=3.0)
                except Exception as e:
                    print(f"MCP cleanup timed out or failed: {e}")

        return "exit"


async def main():
    """Run the improved voice assistant."""
    import argparse

    from dotenv import dotenv_values, load_dotenv

    def env_bool(name: str, default: bool = False) -> bool:
        value = os.getenv(name)
        if value is None:
            return default
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}

    def env_int(name: str, default: int) -> int:
        value = os.getenv(name)
        if value is None or value.strip() == "":
            return default
        try:
            return int(value)
        except ValueError:
            print(f"Error: {name} must be an integer, got: {value}")
            sys.exit(1)

    def env_float(name: str, default: float) -> float:
        value = os.getenv(name)
        if value is None or value.strip() == "":
            return default
        try:
            return float(value)
        except ValueError:
            print(f"Error: {name} must be a number, got: {value}")
            sys.exit(1)

    def env_float_from_values(values: dict, name: str, default: float) -> float:
        value = values.get(name)
        if value is None or str(value).strip() == "":
            return default
        try:
            return float(str(value).strip())
        except ValueError:
            return default

    def env_optional(name: str) -> str | None:
        value = os.getenv(name)
        if value is None or value.strip() == "":
            return None
        return value

    def env_secret(name: str) -> str | None:
        file_path = env_optional(f"{name}_FILE")
        if not file_path:
            return None

        try:
            with open(file_path) as secret_file:
                secret = secret_file.read().strip()
        except OSError as e:
            print(f"Error: could not read {name}_FILE '{file_path}': {e}")
            sys.exit(1)

        return secret or None

    def load_mcp_config_from_values(values: dict) -> dict | None:
        mcp_config_path = (values.get("MCP_CONFIG") or "").strip()
        if not mcp_config_path:
            return None
        try:
            with open(mcp_config_path) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return None

    def format_env_value(value: str) -> str:
        """Format an env value so python-dotenv can parse it back safely."""
        value = str(value)
        if re.fullmatch(r"[A-Za-z0-9_./:@+-]*", value):
            return value
        escaped = (
            value.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\r", "\\r")
        )
        return f'"{escaped}"'

    def update_env_file_values(env_file: Path, updates: dict[str, str]) -> None:
        """Update or append KEY=value pairs in an env file while preserving other lines."""
        try:
            lines = env_file.read_text().splitlines(keepends=True)
        except OSError as e:
            raise ValueError(f"could not read env file '{env_file}': {e}") from e

        remaining = dict(updates)
        updated_lines = []
        for line in lines:
            stripped = line.lstrip()
            if not stripped or stripped.startswith("#") or "=" not in line:
                updated_lines.append(line)
                continue

            key = line.split("=", 1)[0].strip()
            if key in remaining:
                newline = "\n" if line.endswith("\n") else ""
                updated_lines.append(f"{key}={format_env_value(remaining.pop(key))}{newline}")
            else:
                updated_lines.append(line)

        if remaining:
            if updated_lines and not updated_lines[-1].endswith("\n"):
                updated_lines[-1] += "\n"
            for key, value in remaining.items():
                updated_lines.append(f"{key}={format_env_value(value)}\n")

        try:
            env_file.write_text("".join(updated_lines))
        except OSError as e:
            raise ValueError(f"could not write env file '{env_file}': {e}") from e

    def list_openai_models(values: dict) -> tuple[list[dict[str, str]], str | None]:
        if not check_internet_connection():
            return [], "internet offline"

        api_key = read_secret_from_env_values(values, "OPENAI_API_KEY")
        if not api_key:
            return [], "missing OPENAI_API_KEY_FILE"

        try:
            client = openai.OpenAI(api_key=api_key)
            response = client.models.list()
        except Exception as e:
            return [], f"OpenAI API unavailable: {e}"

        model_ids = sorted(
            {
                model.id
                for model in response.data
                if model.id.startswith(("gpt-", "o1", "o3", "o4"))
                and not any(marker in model.id for marker in ("audio", "transcribe", "tts", "image", "realtime"))
            }
        )
        return [{"id": model_id, "label": model_id} for model_id in model_ids], None

    def list_ollama_models(values: dict) -> tuple[list[dict[str, str]], str | None]:
        base_url = (values.get("OLLAMA_BASE_URL") or "http://localhost:11434").strip().rstrip("/")
        try:
            with urllib.request.urlopen(f"{base_url}/api/tags", timeout=2.0) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as e:
            return [], f"Ollama unavailable at {base_url}: {e}"

        names = sorted(
            model.get("name")
            for model in payload.get("models", [])
            if isinstance(model, dict) and model.get("name")
        )
        return [{"id": name, "label": name} for name in names], None

    def parse_elevenlabs_voice_options(value: str) -> list[dict[str, str]]:
        voices = []
        for voice_id, label in re.findall(r"([A-Za-z0-9_-]+)\s*\(([^)]+)\)", value or ""):
            voices.append({"id": voice_id, "label": label.strip()})
        return voices

    def list_elevenlabs_voice_options(values: dict) -> list[dict[str, str]]:
        return parse_elevenlabs_voice_options(values.get("ELEVENLABS_VOICE_OPTIONS") or "")

    def list_thinking_sound_options() -> list[dict[str, str]]:
        assets_dir = Path("assets")
        if not assets_dir.exists():
            return []

        return [
            {"id": wav_path.name, "label": wav_path.name}
            for wav_path in sorted(assets_dir.glob("*.wav"), key=lambda path: path.name.lower())
            if wav_path.is_file()
        ]

    def build_llm_options(env_file: Path, requested_provider: str | None = None) -> dict[str, Any]:
        values = dict(dotenv_values(env_file))
        current_provider = (values.get("LLM_PROVIDER") or "openai").strip().lower()
        provider = (requested_provider or current_provider or "openai").strip().lower()
        current_model = (values.get("OPENAI_MODEL") or "gpt-4o-mini").strip()
        current_cloud_tts_provider = cloud_tts_provider_from_values(values)
        current_tts_output = tts_output_from_values(values)
        current_wake_word = (values.get("WAKE_WORD") or "").strip()
        current_system_prompt = (values.get("ASSISTANT_SYSTEM_PROMPT") or "").strip()
        current_voice_id = (values.get("ELEVENLABS_VOICE_ID") or DEFAULT_ELEVENLABS_VOICE_ID).strip()
        current_thinking_sound_file = (values.get("THINKING_SOUND_FILE") or "thinking.wav").strip()
        current_openai_tts_voice = (values.get("WEB_TTS_VOICE") or DEFAULT_OPENAI_TTS_VOICE).strip()
        current_openai_tts_speed = env_float_from_values(values, "WEB_TTS_SPEED", 1.0)
        internet_online = check_internet_connection()

        provider_entries = [
            {
                "id": "openai",
                "label": "OpenAI",
                "available": internet_online,
                "reason": None if internet_online else "offline",
            },
            {"id": "ollama", "label": "Ollama", "available": True, "reason": None},
        ]

        if provider not in {"openai", "ollama"}:
            provider = "openai" if internet_online else "ollama"

        if provider == "openai":
            models, reason = list_openai_models(values)
        else:
            models, reason = list_ollama_models(values)

        message = ""
        if reason:
            message = reason
        elif provider == "openai":
            message = "OpenAI models loaded from API."
        elif provider == "ollama":
            message = "Ollama local models loaded."

        return {
            "provider": provider,
            "providers": provider_entries,
            "models": models,
            "selected_model": current_model if provider == current_provider else "",
            "cloud_tts_providers": CLOUD_TTS_PROVIDER_OPTIONS,
            "selected_cloud_tts_provider": current_cloud_tts_provider,
            "tts_outputs": TTS_OUTPUT_OPTIONS,
            "selected_tts_output": current_tts_output,
            "selected_wake_word": current_wake_word,
            "selected_system_prompt": current_system_prompt,
            "voices": list_elevenlabs_voice_options(values),
            "selected_voice_id": current_voice_id,
            "openai_tts_voices": OPENAI_TTS_VOICE_OPTIONS,
            "selected_openai_tts_voice": current_openai_tts_voice,
            "selected_openai_tts_speed": current_openai_tts_speed,
            "thinking_sounds": list_thinking_sound_options(),
            "selected_thinking_sound_file": current_thinking_sound_file,
            "message": message,
        }

    def save_llm_config(
        env_file: Path,
        provider: str,
        model: str,
        cloud_tts_provider: str,
        tts_output: str,
        wake_word: str,
        system_prompt: str,
        voice_id: str,
        thinking_sound_file: str,
        openai_tts_voice: str,
        openai_tts_speed: float,
        web_monitor: WebMonitor | None,
        reload_event: threading.Event | None,
    ) -> dict[str, Any]:
        provider = provider.strip().lower()
        model = model.strip()
        cloud_tts_provider = (cloud_tts_provider or "").strip().lower()
        tts_output = (tts_output or "").strip().lower()
        wake_word = (wake_word or "").strip()
        system_prompt = (system_prompt or "").strip()
        voice_id = voice_id.strip()
        thinking_sound_file = thinking_sound_file.strip()
        openai_tts_voice = (openai_tts_voice or "").strip()
        openai_tts_speed = max(0.6, min(1.8, float(openai_tts_speed or 1.0)))
        if provider not in {"openai", "ollama"}:
            raise ValueError(f"unsupported LLM provider: {provider}")
        values = dict(dotenv_values(env_file))
        current_cloud_tts_provider = cloud_tts_provider_from_values(values)
        current_tts_provider = (values.get("TTS_PROVIDER") or "elevenlabs").strip().lower()
        if not cloud_tts_provider:
            cloud_tts_provider = current_cloud_tts_provider
        if cloud_tts_provider not in {"none", "openai", "elevenlabs"}:
            raise ValueError(f"unsupported cloud TTS provider: {cloud_tts_provider}")
        if not tts_output:
            tts_output = tts_output_from_values(dict(dotenv_values(env_file)))
        if tts_output not in {"browser", "backend", "silent"}:
            raise ValueError(f"unsupported TTS output: {tts_output}")
        if cloud_tts_provider == "none":
            tts_output = "silent"
        if cloud_tts_provider in {"openai", "elevenlabs"} and tts_output == "browser":
            updated_tts_provider = "none"
            updated_web_tts_provider = cloud_tts_provider
        elif cloud_tts_provider in {"openai", "elevenlabs"} and tts_output == "backend":
            updated_tts_provider = (
                "pyttsx3"
                if current_tts_provider == "pyttsx3" and cloud_tts_provider == current_cloud_tts_provider
                else cloud_tts_provider
            )
            updated_web_tts_provider = "none"
        else:
            updated_tts_provider = "none"
            updated_web_tts_provider = "none"
        current_provider = (values.get("LLM_PROVIDER") or "openai").strip().lower()
        current_model = (values.get("OPENAI_MODEL") or "gpt-4o-mini").strip()
        if not model:
            model = current_model
        if not model:
            raise ValueError("LLM model is required")

        llm_changed = provider != current_provider or model != current_model
        if llm_changed and provider == "openai" and not check_internet_connection():
            raise ValueError("OpenAI cannot be selected while internet is offline")
        if llm_changed:
            available_models, reason = (list_openai_models(values) if provider == "openai" else list_ollama_models(values))
            if reason:
                raise ValueError(reason)
            if available_models and model not in {item["id"] for item in available_models}:
                raise ValueError(f"model '{model}' is not available for provider '{provider}'")

        voice_options = list_elevenlabs_voice_options(values)
        if not voice_id:
            voice_id = (values.get("ELEVENLABS_VOICE_ID") or DEFAULT_ELEVENLABS_VOICE_ID).strip()
        if voice_options and voice_id not in {item["id"] for item in voice_options}:
            raise ValueError(f"voice '{voice_id}' is not listed in ELEVENLABS_VOICE_OPTIONS")

        thinking_sound_options = list_thinking_sound_options()
        if not thinking_sound_file:
            thinking_sound_file = (values.get("THINKING_SOUND_FILE") or "thinking.wav").strip()
        if thinking_sound_options and thinking_sound_file not in {item["id"] for item in thinking_sound_options}:
            raise ValueError(f"thinking sound '{thinking_sound_file}' is not a WAV file in assets/")

        if not openai_tts_voice:
            openai_tts_voice = (values.get("WEB_TTS_VOICE") or DEFAULT_OPENAI_TTS_VOICE).strip()
        if openai_tts_voice not in {item["id"] for item in OPENAI_TTS_VOICE_OPTIONS}:
            raise ValueError(f"OpenAI TTS voice '{openai_tts_voice}' is not available in the web config")

        update_env_file_values(
            env_file,
            {
                "LLM_PROVIDER": provider,
                "OPENAI_MODEL": model,
                "CLOUD_TTS_PROVIDER": cloud_tts_provider,
                "TTS_PROVIDER": updated_tts_provider,
                "WEB_TTS_PROVIDER": updated_web_tts_provider,
                "WAKE_WORD": wake_word,
                "ASSISTANT_SYSTEM_PROMPT": system_prompt,
                "ELEVENLABS_VOICE_ID": voice_id,
                "THINKING_SOUND_FILE": thinking_sound_file,
                "WEB_TTS_VOICE": openai_tts_voice,
                "WEB_TTS_SPEED": f"{openai_tts_speed:.2f}",
            },
        )
        values = dict(dotenv_values(env_file))
        mcp_config = load_mcp_config_from_values(values)
        if web_monitor:
            web_monitor.update(
                env_values=values,
                mcp_config=mcp_config or {},
                services=build_service_state(
                    llm_provider=provider,
                    model=model,
                    stt_provider=(values.get("STT_PROVIDER") or "openai-whisper").strip().lower(),
                    tts_provider=(values.get("TTS_PROVIDER") or "elevenlabs").strip().lower(),
                    mcp_config=mcp_config,
                ),
            )

        if reload_event:
            reload_event.set()

        return {
            "saved": True,
            "provider": provider,
            "model": model,
            "cloud_tts_provider": cloud_tts_provider,
            "tts_output": tts_output,
            "wake_word": wake_word,
            "system_prompt": system_prompt,
            "voice_id": voice_id,
            "thinking_sound_file": thinking_sound_file,
            "openai_tts_voice": openai_tts_voice,
            "openai_tts_speed": openai_tts_speed,
            "message": "Configuration saved. Restarting assistant with the new settings.",
        }

    def clear_env_keys(env_files: list[Path]) -> None:
        """Clear keys owned by env profiles so auto reloads do not keep stale values."""
        env_keys = set()
        for profile in env_files:
            if profile.exists():
                env_keys.update(dotenv_values(profile).keys())

        for key in env_keys:
            os.environ.pop(key, None)

    def build_assistant_from_env(
        env_file: Path,
        reload_event: threading.Event | None = None,
        web_monitor: WebMonitor | None = None,
    ) -> VoiceAssistant:
        """Load one env profile and build a fresh assistant instance from it."""
        clear_profiles = [env_file]
        if auto_env_mode:
            clear_profiles.extend([AUTO_ENV_ONLINE, AUTO_ENV_OFFLINE])
        clear_env_keys(clear_profiles)
        load_dotenv(env_file, override=True)

        openai_api_key = env_secret("OPENAI_API_KEY")
        elevenlabs_api_key = env_secret("ELEVENLABS_API_KEY")
        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        llm_provider = os.getenv("LLM_PROVIDER", "openai").lower()
        ollama_base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        stt_provider = os.getenv("STT_PROVIDER", "openai-whisper").lower()
        local_whisper_model = os.getenv("LOCAL_WHISPER_MODEL", "base")
        stt_language_value = os.getenv("STT_LANGUAGE", "auto")
        stt_language = None if stt_language_value.lower() == "auto" else stt_language_value
        stt_prompt = os.getenv("STT_PROMPT", DEFAULT_STT_PROMPT)
        tts_provider = os.getenv("TTS_PROVIDER", "elevenlabs").lower()
        voice_id = os.getenv("ELEVENLABS_VOICE_ID", DEFAULT_ELEVENLABS_VOICE_ID)
        thinking_sound_file = os.getenv("THINKING_SOUND_FILE", "thinking.wav")
        silence_threshold = env_int("VOICE_SILENCE_THRESHOLD", 500)
        silence_duration = env_float("VOICE_SILENCE_DURATION", 1.5)
        voice_cancel_during_thinking = env_bool("VOICE_CANCEL_DURING_THINKING", False)
        web_audio_enabled = env_bool("WEB_AUDIO_ENABLED", False)
        web_stt_provider = os.getenv("WEB_STT_PROVIDER", "openai").strip().lower()
        raw_web_tts_provider = os.getenv("WEB_TTS_PROVIDER")
        web_tts_provider = (raw_web_tts_provider or "openai").strip().lower()
        raw_cloud_tts_provider = os.getenv("CLOUD_TTS_PROVIDER")
        cloud_tts_provider = (raw_cloud_tts_provider or "").strip().lower()
        if cloud_tts_provider not in {"none", "openai", "elevenlabs"}:
            cloud_tts_provider = cloud_tts_provider_from_values(
                {
                    "CLOUD_TTS_PROVIDER": raw_cloud_tts_provider or "",
                    "WEB_TTS_PROVIDER": raw_web_tts_provider or "",
                    "TTS_PROVIDER": tts_provider,
                }
            )
        if tts_provider in {"openai", "elevenlabs"} or cloud_tts_provider == "none":
            tts_provider = cloud_tts_provider
        if web_tts_provider in {"openai", "elevenlabs"} or cloud_tts_provider == "none":
            web_tts_provider = cloud_tts_provider
        web_tts_voice = os.getenv("WEB_TTS_VOICE", DEFAULT_OPENAI_TTS_VOICE).strip()
        web_tts_model = os.getenv("WEB_TTS_MODEL", DEFAULT_OPENAI_TTS_MODEL).strip()
        web_tts_speed = max(0.6, min(1.8, env_float("WEB_TTS_SPEED", 1.0)))
        web_stt_model = os.getenv("WEB_STT_MODEL", "whisper-1").strip()
        web_recording_max_seconds = env_float("WEB_RECORDING_MAX_SECONDS", 8.0)
        web_conversation_silence_ms = env_int("WEB_CONVERSATION_SILENCE_MS", 900)
        web_conversation_idle_seconds = env_float("WEB_CONVERSATION_IDLE_SECONDS", 25.0)
        web_conversation_threshold = env_float("WEB_CONVERSATION_THRESHOLD", 0.035)
        wake_words = parse_wake_words(env_optional("WAKE_WORD"))
        system_prompt = env_optional("ASSISTANT_SYSTEM_PROMPT")
        mcp_config_path = env_optional("MCP_CONFIG")
        mcp_prompt_merge_mode = os.getenv("MCP_PROMPT_MERGE_MODE", "append").lower()
        mcp_agent_memory_enabled = env_bool("MCP_AGENT_MEMORY_ENABLED", True)
        mcp_agent_timeout_seconds = env_float(
            "MCP_AGENT_TIMEOUT_SECONDS",
            DEFAULT_MCP_AGENT_TIMEOUT_SECONDS,
        )

        if llm_provider not in {"openai", "ollama"}:
            print(f"Error: LLM_PROVIDER must be 'openai' or 'ollama', got: {llm_provider}")
            sys.exit(1)
        if stt_provider not in {"openai-whisper", "local-whisper"}:
            print(f"Error: STT_PROVIDER must be 'openai-whisper' or 'local-whisper', got: {stt_provider}")
            sys.exit(1)
        if cloud_tts_provider not in {"none", "openai", "elevenlabs"}:
            print(f"Error: CLOUD_TTS_PROVIDER must be 'none', 'openai', or 'elevenlabs', got: {cloud_tts_provider}")
            sys.exit(1)
        if tts_provider not in {"openai", "elevenlabs", "pyttsx3", "none"}:
            print(f"Error: TTS_PROVIDER must be 'openai', 'elevenlabs', 'pyttsx3', or 'none', got: {tts_provider}")
            sys.exit(1)
        if web_stt_provider not in {"openai"}:
            print(f"Error: WEB_STT_PROVIDER must be 'openai', got: {web_stt_provider}")
            sys.exit(1)
        if web_tts_provider not in {"openai", "elevenlabs", "none"}:
            print(f"Error: WEB_TTS_PROVIDER must be 'openai', 'elevenlabs', or 'none', got: {web_tts_provider}")
            sys.exit(1)
        if mcp_prompt_merge_mode not in {"append", "replace"}:
            print(f"Error: MCP_PROMPT_MERGE_MODE must be 'append' or 'replace', got: {mcp_prompt_merge_mode}")
            sys.exit(1)

        print(f"Using env file: {env_file}")
        print(f"Using ElevenLabs voice ID: {voice_id}")
        print(f"Using LLM provider: {llm_provider}")
        print(f"Using STT provider: {stt_provider}")
        print(f"Using cloud TTS provider: {cloud_tts_provider}")
        print(f"Using TTS provider: {tts_provider}")
        print(f"Using thinking sound file: {thinking_sound_file}")
        if voice_cancel_during_thinking:
            print("Using voice cancel during thinking: enabled")
        if web_audio_enabled:
            print(f"Using web audio: STT={web_stt_provider}, TTS={web_tts_provider}")
        print(f"Using wake word: {', '.join(wake_words) if wake_words else 'disabled'}")
        print(f"Using MCP agent memory: {mcp_agent_memory_enabled}")

        web_audio_needs_openai = web_audio_enabled and (
            web_stt_provider == "openai" or web_tts_provider == "openai"
        )
        backend_tts_needs_openai = tts_provider == "openai"
        if (
            llm_provider == "openai"
            or stt_provider == "openai-whisper"
            or web_audio_needs_openai
            or backend_tts_needs_openai
        ) and not openai_api_key:
            print("Error: OpenAI API key is required")
            print(
                "Set OPENAI_API_KEY_FILE, or use an offline env file with "
                "LLM_PROVIDER=ollama and STT_PROVIDER=local-whisper"
            )
            sys.exit(1)

        backend_tts_active = tts_provider != "none"
        web_tts_has_key = (
            (web_tts_provider == "openai" and bool(openai_api_key))
            or (web_tts_provider == "elevenlabs" and bool(elevenlabs_api_key))
        )
        web_audio_state = {
            "enabled": web_audio_enabled,
            "stt_enabled": web_audio_enabled and web_stt_provider == "openai" and bool(openai_api_key),
            "tts_enabled": (
                web_audio_enabled
                and web_tts_provider in {"openai", "elevenlabs"}
                and web_tts_has_key
                and not backend_tts_active
            ),
            "tts_blocked_by_backend": web_audio_enabled and backend_tts_active,
            "stt_provider": web_stt_provider if web_audio_enabled else "none",
            "tts_provider": web_tts_provider if web_audio_enabled and not backend_tts_active else "none",
            "cloud_tts_provider": cloud_tts_provider,
            "tts_speed": web_tts_speed,
            "max_record_seconds": web_recording_max_seconds,
            "conversation_silence_ms": web_conversation_silence_ms,
            "conversation_idle_seconds": web_conversation_idle_seconds,
            "conversation_threshold": web_conversation_threshold,
        }

        mcp_config = None
        if mcp_config_path:
            try:
                with open(mcp_config_path) as f:
                    mcp_config = json.load(f)
            except OSError as e:
                print(f"Error: could not read MCP_CONFIG '{mcp_config_path}': {e}")
                sys.exit(1)
            except json.JSONDecodeError as e:
                print(f"Error: invalid JSON in MCP_CONFIG '{mcp_config_path}': {e}")
                sys.exit(1)

        if web_monitor:
            env_values = dict(dotenv_values(env_file))
            internet_status = env_file == AUTO_ENV_ONLINE if auto_env_mode else "unknown"
            web_monitor.update(
                mode="auto" if auto_env_mode else "fixed",
                env_file=env_file,
                internet=internet_status,
                env_values=env_values,
                mcp_config=mcp_config or {},
                services=build_service_state(
                    llm_provider=llm_provider,
                    model=model,
                    stt_provider=stt_provider,
                    tts_provider=tts_provider,
                    mcp_config=mcp_config,
                ),
                web_audio=web_audio_state,
                thinking_sound_file=thinking_sound_file,
            )

        assistant = VoiceAssistant(
            openai_api_key=openai_api_key,
            elevenlabs_api_key=elevenlabs_api_key,
            model=model,
            llm_provider=llm_provider,
            ollama_base_url=ollama_base_url,
            stt_provider=stt_provider,
            local_whisper_model=local_whisper_model,
            stt_language=stt_language,
            stt_prompt=stt_prompt,
            tts_provider=tts_provider,
            elevenlabs_voice_id=voice_id,
            thinking_sound_file=thinking_sound_file,
            silence_threshold=silence_threshold,
            silence_duration=silence_duration,
            tts_speed=web_tts_speed,
            wake_words=wake_words,
            mcp_config=mcp_config,
            mcp_load_server_prompt=env_bool("MCP_LOAD_SERVER_PROMPT", False),
            mcp_prompt_merge_mode=mcp_prompt_merge_mode,
            mcp_agent_memory_enabled=mcp_agent_memory_enabled,
            mcp_agent_timeout_seconds=mcp_agent_timeout_seconds,
            voice_cancel_during_thinking=voice_cancel_during_thinking,
            system_prompt=system_prompt,
            reload_event=reload_event,
            web_monitor=web_monitor,
        )

        if web_monitor:
            if web_audio_state["tts_enabled"] and web_tts_provider == "openai" and assistant.openai_client is None:
                assistant.openai_client = openai.OpenAI(api_key=openai_api_key)
            if web_audio_state["tts_enabled"] and web_tts_provider == "elevenlabs" and assistant.elevenlabs_client is None:
                assistant.elevenlabs_client = ElevenLabs(api_key=elevenlabs_api_key)

            web_tts_handler = None
            if web_audio_state["tts_enabled"] and web_tts_provider == "openai":
                web_tts_handler = lambda text, active_assistant=assistant: active_assistant.web_text_to_speech_openai(
                    text,
                    model=web_tts_model,
                    voice=web_tts_voice,
                )
            elif web_audio_state["tts_enabled"] and web_tts_provider == "elevenlabs":
                web_tts_handler = (
                    lambda text, active_assistant=assistant: active_assistant.web_text_to_speech_elevenlabs(text)
                )

            web_monitor.set_web_audio_handlers(
                transcribe_handler=(
                    lambda audio_bytes, mime_type, apply_wake_word_gate, active_assistant=assistant: active_assistant.web_audio_transcription_result(
                        audio_bytes,
                        mime_type,
                        model=web_stt_model,
                        apply_wake_word_gate=apply_wake_word_gate,
                    )
                    if web_audio_state["stt_enabled"]
                    else None
                ),
                tts_handler=web_tts_handler,
            )

        return assistant

    parser = argparse.ArgumentParser(description="Voice-enabled AI assistant")
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Environment file to load before starting the assistant (default: .env)",
    )
    args = parser.parse_args()

    auto_env_mode = args.env_file.strip().lower() == "auto"
    auto_selection_message = None
    if auto_env_mode:
        internet_online = check_internet_connection()
        env_file = AUTO_ENV_ONLINE if internet_online else AUTO_ENV_OFFLINE
        auto_selection_message = (
            "Auto env mode selected "
            f"{env_file} because internet is {'live' if internet_online else 'inactive'}."
        )
    else:
        env_file = Path(args.env_file)

    if not env_file.exists():
        print(f"Error: env file not found: {env_file}")
        print("Use one of the provided profiles, for example:")
        print("  python voice_assistant/agent.py --env-file .env.online")
        print("  python voice_assistant/agent.py --env-file .env.offline")
        print("  python voice_assistant/agent.py --env-file auto")
        sys.exit(1)

    profile_values = dotenv_values(env_file)
    web_enabled = (profile_values.get("WEB_MONITOR_ENABLED") or "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
        "on",
    }
    web_host = (profile_values.get("WEB_MONITOR_HOST") or "127.0.0.1").strip()
    try:
        web_port = int((profile_values.get("WEB_MONITOR_PORT") or "8765").strip())
    except ValueError:
        print(f"Error: WEB_MONITOR_PORT must be an integer, got: {profile_values.get('WEB_MONITOR_PORT')}")
        sys.exit(1)

    reload_event = threading.Event()
    web_monitor = None
    if web_enabled:
        web_monitor = WebMonitor()
        web_monitor.install_console_capture()
        try:
            actual_host, actual_port = web_monitor.start(web_host, web_port)
            print(f"Web monitor available at http://{actual_host}:{actual_port}")
        except OSError as e:
            web_monitor.restore_console_capture()
            web_monitor = None
            print(f"Web monitor disabled: could not bind {web_host}:{web_port}: {e}")

    if web_monitor:
        web_monitor.set_llm_config_handlers(
            options_handler=lambda provider=None: build_llm_options(env_file, provider),
            save_handler=lambda provider, model, cloud_tts_provider, tts_output, wake_word, system_prompt, voice_id, thinking_sound_file, openai_tts_voice, openai_tts_speed: save_llm_config(
                env_file,
                provider,
                model,
                cloud_tts_provider,
                tts_output,
                wake_word,
                system_prompt,
                voice_id,
                thinking_sound_file,
                openai_tts_voice,
                openai_tts_speed,
                web_monitor,
                reload_event,
            ),
        )

    if auto_selection_message:
        print(auto_selection_message)

    if not auto_env_mode:
        try:
            announce_reload_complete = False
            while True:
                reload_event.clear()
                assistant = build_assistant_from_env(env_file, reload_event=reload_event, web_monitor=web_monitor)
                if announce_reload_complete:
                    await assistant.text_to_speech("Configuration mise à jour. L'assistant redémarre avec le nouveau modèle.")
                    announce_reload_complete = False

                run_result = await assistant.run()
                if run_result != "reload":
                    break

                print(f"Configuration reload requested. Restarting assistant with {env_file}.")
                announce_reload_complete = True
        finally:
            if web_monitor:
                web_monitor.stop()
                web_monitor.restore_console_capture()
        return

    auto_monitor = AutoNetworkMonitor(
        initial_online=internet_online,
        dotenv_values_func=dotenv_values,
        reload_event=reload_event,
        web_monitor=web_monitor,
        interval=AUTO_CHECK_INTERVAL,
    )
    auto_monitor.announce_initial_status()
    auto_monitor.start()

    try:
        announce_reload_complete = False
        while True:
            env_file = auto_monitor.detected_env_file
            if not env_file.exists():
                print(f"Error: env file not found: {env_file}")
                sys.exit(1)

            reload_event.clear()
            assistant = build_assistant_from_env(env_file, reload_event=reload_event, web_monitor=web_monitor)
            if announce_reload_complete:
                await assistant.text_to_speech("Environnement mis à jour. La demande en cours a été annulée.")
                announce_reload_complete = False

            run_result = await assistant.run()
            if run_result != "reload":
                break

            next_env_file = auto_monitor.detected_env_file
            print(f"Auto env reload requested. Restarting assistant with {next_env_file}.")
            announce_reload_complete = True
    finally:
        auto_monitor.stop()
        if web_monitor:
            web_monitor.stop()
            web_monitor.restore_console_capture()


if __name__ == "__main__":
    signal.signal(signal.SIGINT, request_force_exit)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
