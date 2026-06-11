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
import time
from typing import Any
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import wave

import numpy as np
import openai
import pyaudio
import pyttsx3
from elevenlabs.client import ElevenLabs
from elevenlabs.types.voice_settings import VoiceSettings
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from mcp_use import MCPAgent, MCPClient
from pydantic import AnyUrl

try:
    from .web_monitor import WebMonitor, build_service_state
    from .session_context import DEFAULT_CONTEXT_DIR, DEFAULT_SUMMARY_MAX_CHARS, SessionContextStore
    from .wake_word import apply_wake_word, parse_wake_words
except ImportError:
    from web_monitor import WebMonitor, build_service_state
    from session_context import DEFAULT_CONTEXT_DIR, DEFAULT_SUMMARY_MAX_CHARS, SessionContextStore
    from wake_word import apply_wake_word, parse_wake_words

TTS_ENGINE = pyttsx3.init()
TTS_LOCK = threading.Lock()
TTS_STOP_EVENT = threading.Event()
TTS_PLAYBACK_PROCESS: subprocess.Popen | None = None
FORCE_EXIT_REQUESTED = threading.Event()
DEFAULT_ELEVENLABS_VOICE_ID = "1EmYoP3UnnnwhlJKovEy"  # french male; ZF6FPAbjXT4488VcRRnw = english female
DEFAULT_OPENAI_TTS_MODEL = "gpt-4o-mini-tts"
DEFAULT_OPENAI_TTS_VOICE = "alloy"
DEFAULT_OLLAMA_MODEL = "qwen3.5:4b"
DEFAULT_MCP_AGENT_TIMEOUT_SECONDS = 45.0
DEFAULT_SILERO_VAD_MODEL = Path("static/vendor/silero-vad/silero_vad_v6.onnx")
OPENAI_MAX_TOOLS_PER_REQUEST = 128
DEFAULT_MCP_PROMPT_NAME = "agent_prompt"
DEFAULT_MCP_PROMPT_RESOURCE_URI = "agent://prompt/system"
DEFAULT_MCP_PROMPT_TOOL = "get_agent_prompt"
DUPLICATE_COMMAND_SUPPRESS_SECONDS = 5.0
LOGGER = logging.getLogger(__name__)
logging.getLogger("mcp_use").propagate = False
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
SESSION_LLM_SUMMARY_PROMPT = (
    "Create durable memory for this persisted assistant session.\n"
    "Keep only what should remain useful later: user preferences, future instructions, aliases, mappings, conventions, "
    "corrections, unresolved tasks, and project decisions.\n"
    "Prioritize explicit learning cues such as remember, learn, when I say X do Y, in the future, or correct this behavior.\n"
    "Ignore one-off commands, executed actions, status checks, temporary values, routine tool results, confirmations, "
    "connected device identity, and live external state unless the user explicitly says to remember them as a durable rule.\n"
    "If newer instructions correct older ones, keep only the newest rule. Do not invent facts.\n"
    "Write short bullets under 2500 characters.\n\n"
    "Transcript summary to compress:\n"
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
MCP_CONFIRMATION_WORDS = {
    "oui",
    "yes",
    "y",
    "ok",
    "okay",
    "daccord",
    "d'accord",
    "d accord",
    "vas y",
    "vas-y",
    "allez y",
    "allez-y",
    "confirme",
    "confirm",
    "execute",
    "exécute",
    "go",
}
DEFAULT_ASSISTANT_SYSTEM_PROMPT = (
    "You are a helpful voice assistant named is Live Stage Assistant with access to various tools. "
    "Be precise, conservative, and tool-driven in your responses since they will be spoken aloud and have "
    "to be suitable for text-to-speech and API calls. Don't be verbose but summarize your results. "
    "Reply in the same language as the user's latest request whenever possible. "
    "Use plain text only. Do not use emojis, emoticons, markdown, bullets, symbols, or decorative characters. "
    "Behave like a friendly calm and motivating assistant. Use conversation memory for context, preferences, "
    "and follow-up references, but not as the source of truth for live external state. When the user asks about "
    "the current state of anything outside this conversation, treat the answer as time-sensitive. Use the relevant "
    "MCP read tool before answering. Do not answer current external state from memory, previous tool results, "
    "or assumptions. If no suitable read tool is available, say that you cannot verify the current state and use "
    "only tools exposed by the MCP servers."
)
OPENAI_TTS_VOICE_OPTIONS = [
    {"id": "alloy", "label": "Alloy (masculine)"},
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
DEFAULT_BACKEND_MP3_SAMPLE_RATE = 24000
DEFAULT_BACKEND_MP3_CHANNELS = 1


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
    "Commandes courtes en français pour du mixage live. "
    "Mots fréquents: mets, met, règle, baisse, monte, coupe, mute, active, réactive, "
    "bus, retour, façade, dB, moins trois dB, Voc-Claude, snare, kick, Laurent. "
    "Ne colle pas le verbe 'mets' au nom qui suit: écris 'mets Claude', 'mets Voc-Claude', 'mets snare'. "
    "Garde les noms de pistes courts et précis."
)
FUSED_SET_COMMAND_RE = re.compile(r"^\s*(mets|met|me)([a-zà-ÿ][a-zà-ÿ0-9_-]{3,})(\b|$)", re.IGNORECASE)
STT_SILENCE_HALLUCINATION_PHRASES = (
    "Sous-titres réalisés par la communauté d'Amara.org",
    "Sous-titres réalisés para la communauté d'Amara.org",
    "Sous-titres créés par la communauté d'Amara.org",
    "Sous-titres par Amara.org",
    "Sous-titrage Société Radio-Canada",
    "Sous-titrage par Société Radio-Canada",
    "Sous-titrage fourni par la Société Radio-Canada",
    "Sous-titrage fournis par la Société Radio-Canada",
    "Sous-titrage ST' 501",
    "Sous-titrage par SousTitreur.com",
    "Subtitles created by the Amara.org community",
    "Subtitles by the Amara.org community",
    "Captioning by CBC Radio-Canada",
    "Merci d'avoir regardé cette vidéo",
    "Merci d'avoir regardé cette vidéo, n'hésitez pas à vous abonner pour ne manquer aucune de mes vidéos",
    "Merci d'avoir regardé la vidéo",
    "Merci d'avoir regardé",
    "Merci d'avoir visionné cette vidéo",
    "N'hésitez pas à vous abonner pour ne manquer aucune de mes vidéos",
    "Thanks for watching this video",
    "Thanks for watching",
    "Please subscribe so you don't miss any of my videos",
)


def normalize_stt_hallucination_candidate(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text or "")
    normalized = "".join(char for char in normalized if unicodedata.category(char) != "Mn")
    normalized = normalized.lower()
    normalized = normalized.replace("’", "'").replace("`", "'").replace("´", "'")
    normalized = re.sub(r"\b(d|l|j|m|t|s|c|n|qu)'", r"\1 ", normalized)
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


STT_SILENCE_HALLUCINATION_KEYS = {
    normalize_stt_hallucination_candidate(phrase) for phrase in STT_SILENCE_HALLUCINATION_PHRASES
}
STT_SUBTITLE_HALLUCINATION_TERMS = (
    "sous titre",
    "sous titres",
    "sous titrage",
    "subtitle",
    "subtitles",
    "caption",
    "captioning",
    "closed caption",
)
STT_SUBTITLE_CREDIT_TERMS = (
    "amara",
    "communaute",
    "community",
    "societe radio canada",
    "radio canada",
    "cbc radio canada",
    "soustitreur",
    "st 501",
)
STT_VIDEO_HALLUCINATION_STARTS = (
    "merci d avoir regarde",
    "merci d avoir visionne",
    "thanks for watching",
)
STT_VIDEO_HALLUCINATION_TERMS = (
    "video",
    "videos",
    "chaine",
    "channel",
    "abonne",
    "abonner",
    "abonnez",
    "subscribe",
    "subscribed",
    "like",
    "commentaire",
    "commentaires",
)


def is_likely_stt_silence_hallucination(text: str) -> bool:
    normalized = normalize_stt_hallucination_candidate(text)
    if not normalized:
        return False
    if normalized in STT_SILENCE_HALLUCINATION_KEYS:
        return True

    has_subtitle_term = any(term in normalized for term in STT_SUBTITLE_HALLUCINATION_TERMS)
    has_credit_term = any(term in normalized for term in STT_SUBTITLE_CREDIT_TERMS)
    if has_subtitle_term and has_credit_term:
        return True

    starts_like_video_credit = any(
        normalized.startswith(prefix) for prefix in STT_VIDEO_HALLUCINATION_STARTS
    )
    has_video_credit_term = any(term in normalized for term in STT_VIDEO_HALLUCINATION_TERMS)
    if starts_like_video_credit and has_video_credit_term:
        return True

    return False


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


def with_external_state_freshness_rule(prompt: str) -> str:
    """Append the live-state freshness rule once to the configured system prompt."""
    cleaned_prompt = prompt.rstrip()
    if EXTERNAL_STATE_FRESHNESS_RULE in cleaned_prompt:
        return cleaned_prompt
    return f"{cleaned_prompt} {EXTERNAL_STATE_FRESHNESS_RULE}"


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


def mask_secret_tail(secret: str | None, visible_tail: int = 4) -> str:
    if not secret:
        return "non configurée"
    tail = secret[-visible_tail:] if len(secret) > visible_tail else secret
    return f"{'•' * 18}{tail}"


def read_json_url(url: str, headers: dict[str, str], timeout: float = 6.0) -> dict[str, Any]:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8")
    parsed = json.loads(body or "{}")
    if not isinstance(parsed, dict):
        raise ValueError("API response is not a JSON object")
    return parsed


def env_float_from_mapping(values: dict, name: str, default: float) -> float:
    value = values.get(name)
    if value is None or str(value).strip() == "":
        return default
    try:
        return float(str(value).strip())
    except ValueError:
        return default


def elevenlabs_playback_available() -> bool:
    """Return whether generated MP3 audio can be played or decoded locally."""
    return ffmpeg_decode_available() or shutil.which("ffplay") is not None


def local_tts_playback_available() -> bool:
    """Return whether pyttsx3 is likely to have a local audio player."""
    if sys.platform.startswith("linux"):
        return shutil.which("aplay") is not None
    return True


def ffmpeg_decode_available() -> bool:
    """Return whether MP3 TTS can be decoded for backend-controlled playback."""
    return shutil.which("ffmpeg") is not None


def _pyaudio_device_is_default(audio: pyaudio.PyAudio, index: int, *, input_device: bool) -> bool:
    try:
        method = audio.get_default_input_device_info if input_device else audio.get_default_output_device_info
        return int(method().get("index", -1)) == index
    except Exception:
        return False


def list_pyaudio_devices(audio: pyaudio.PyAudio | None = None) -> dict[str, list[dict[str, Any]]]:
    """List usable PyAudio input/output devices for web config selection."""
    own_audio = audio is None
    if audio is None:
        try:
            with suppress_native_stderr():
                audio = pyaudio.PyAudio()
        except Exception:
            return {"inputs": [], "outputs": []}

    try:
        devices = {"inputs": [], "outputs": []}
        for index in range(audio.get_device_count()):
            try:
                info = audio.get_device_info_by_index(index)
            except Exception:
                continue

            name = str(info.get("name") or f"Device {index}").strip()
            host_api = ""
            try:
                host_api_info = audio.get_host_api_info_by_index(int(info.get("hostApi", 0)))
                host_api = str(host_api_info.get("name") or "").strip()
            except Exception:
                pass
            label = f"{index}: {name}" + (f" ({host_api})" if host_api else "")
            entry = {
                "id": str(index),
                "label": label,
                "name": name,
                "default": False,
            }
            if int(info.get("maxInputChannels", 0) or 0) > 0:
                input_entry = dict(entry)
                input_entry["default"] = _pyaudio_device_is_default(audio, index, input_device=True)
                devices["inputs"].append(input_entry)
            if int(info.get("maxOutputChannels", 0) or 0) > 0:
                output_entry = dict(entry)
                output_entry["default"] = _pyaudio_device_is_default(audio, index, input_device=False)
                devices["outputs"].append(output_entry)
        return devices
    finally:
        if own_audio:
            try:
                audio.terminate()
            except Exception:
                pass


def resolve_pyaudio_device_index(
    audio: pyaudio.PyAudio,
    selected_device: str | None,
    *,
    input_device: bool,
) -> tuple[int | None, str, str]:
    """Resolve a configured PyAudio device index, falling back to the system default."""
    selected = str(selected_device or "").strip()
    direction = "input" if input_device else "output"
    max_channels_key = "maxInputChannels" if input_device else "maxOutputChannels"
    default_method = audio.get_default_input_device_info if input_device else audio.get_default_output_device_info

    if selected:
        try:
            index = int(selected.split(":", 1)[0])
            info = audio.get_device_info_by_index(index)
            if int(info.get(max_channels_key, 0) or 0) > 0:
                return index, "configured", f"{index}: {info.get('name') or 'unknown'}"
            return None, "invalid", f"configured {direction} device has no {direction} channels: {selected}"
        except Exception as e:
            return None, "invalid", f"configured {direction} device unavailable ({selected}): {e}"

    try:
        info = default_method()
        return None, "default", f"default {direction}: {info.get('index')}: {info.get('name')}"
    except Exception as e:
        return None, "unavailable", f"default {direction} unavailable: {e}"


def backend_audio_service_state(
    input_status: str,
    input_detail: str,
    output_status: str,
    output_detail: str,
) -> dict[str, str]:
    """Build the single monitor tile for backend audio input/output."""
    if input_status == "unavailable" and output_status == "unavailable":
        status = "unavailable"
    elif "invalid" in {input_status, output_status}:
        status = "warn"
    elif "unavailable" in {input_status, output_status}:
        status = "warn"
    else:
        status = "configured" if "configured" in {input_status, output_status} else "default"
    return {"status": status, "detail": f"In: {input_detail}; Out: {output_detail}"}


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


def connectivity_mode_from_values(values: dict, env_file: Path | None = None) -> str:
    """Return whether this profile should use online cloud controls or offline local controls."""
    configured = (values.get("CONNECTIVITY_MODE") or "").strip().lower()
    if configured in {"online", "offline"}:
        return configured
    if env_file == AUTO_ENV_OFFLINE:
        return "offline"
    llm_provider = (values.get("LLM_PROVIDER") or "").strip().lower()
    stt_provider = (values.get("STT_PROVIDER") or "").strip().lower()
    tts_provider = (values.get("TTS_PROVIDER") or "").strip().lower()
    if llm_provider == "ollama" or stt_provider == "local-whisper" or tts_provider == "pyttsx3":
        return "offline"
    return "online"


def decode_mp3_to_pcm_bytes(
    audio_bytes: bytes,
    *,
    sample_rate: int = DEFAULT_BACKEND_MP3_SAMPLE_RATE,
    channels: int = DEFAULT_BACKEND_MP3_CHANNELS,
) -> bytes:
    """Decode MP3 bytes to signed 16-bit PCM so PyAudio owns playback/output device selection."""
    if not ffmpeg_decode_available():
        raise RuntimeError("ffmpeg is not available")

    process = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            "pipe:0",
            "-f",
            "s16le",
            "-ac",
            str(channels),
            "-ar",
            str(sample_rate),
            "pipe:1",
        ],
        input=audio_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if process.returncode != 0:
        detail = process.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"ffmpeg could not decode MP3 audio: {detail}")
    return process.stdout


def decode_audio_file_to_pcm_bytes(
    audio_path: str | Path,
    *,
    sample_rate: int = DEFAULT_BACKEND_MP3_SAMPLE_RATE,
    channels: int = DEFAULT_BACKEND_MP3_CHANNELS,
) -> bytes:
    """Decode a local audio file to signed 16-bit PCM for PyAudio playback."""
    if not ffmpeg_decode_available():
        raise RuntimeError("ffmpeg is not available")

    process = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(audio_path),
            "-f",
            "s16le",
            "-ac",
            str(channels),
            "-ar",
            str(sample_rate),
            "pipe:1",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if process.returncode != 0:
        detail = process.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"ffmpeg could not decode local TTS audio: {detail}")
    return process.stdout


def play_pcm_bytes(
    audio: pyaudio.PyAudio,
    pcm_bytes: bytes,
    *,
    sample_rate: int,
    channels: int,
    output_device_index: int | None = None,
    stop_event: threading.Event | None = None,
) -> None:
    """Play signed 16-bit PCM through PyAudio, optionally using a configured output device."""
    stream = None
    try:
        with suppress_native_stderr():
            stream = audio.open(
                format=pyaudio.paInt16,
                channels=channels,
                rate=sample_rate,
                output=True,
                output_device_index=output_device_index,
                frames_per_buffer=1024,
            )
        byte_step = 1024 * channels * 2
        for start in range(0, len(pcm_bytes), byte_step):
            if stop_event and stop_event.is_set():
                break
            stream.write(pcm_bytes[start : start + byte_step])
    finally:
        if stream:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass


def play_mp3_bytes(
    audio_bytes: bytes,
    *,
    audio: pyaudio.PyAudio | None = None,
    output_device_index: int | None = None,
) -> None:
    """Play MP3 bytes locally, preferring backend-controlled PyAudio playback."""
    if audio is not None:
        if not ffmpeg_decode_available():
            raise RuntimeError("ffmpeg is required for backend-controlled MP3 playback")
        pcm_bytes = decode_mp3_to_pcm_bytes(audio_bytes)
        play_pcm_bytes(
            audio,
            pcm_bytes,
            sample_rate=DEFAULT_BACKEND_MP3_SAMPLE_RATE,
            channels=DEFAULT_BACKEND_MP3_CHANNELS,
            output_device_index=output_device_index,
            stop_event=TTS_STOP_EVENT,
        )
        return

    if shutil.which("ffplay") is None:
        raise RuntimeError("ffplay is not available")

    global TTS_PLAYBACK_PROCESS
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as temp_file:
            temp_file.write(audio_bytes)
            temp_path = temp_file.name
        TTS_PLAYBACK_PROCESS = subprocess.Popen(
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", temp_path],
        )
        while TTS_PLAYBACK_PROCESS.poll() is None:
            if TTS_STOP_EVENT.is_set():
                TTS_PLAYBACK_PROCESS.terminate()
                try:
                    TTS_PLAYBACK_PROCESS.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    TTS_PLAYBACK_PROCESS.kill()
                break
            time.sleep(0.05)
        if TTS_PLAYBACK_PROCESS.returncode not in (0, None) and not TTS_STOP_EVENT.is_set():
            raise subprocess.CalledProcessError(TTS_PLAYBACK_PROCESS.returncode, TTS_PLAYBACK_PROCESS.args)
    finally:
        TTS_PLAYBACK_PROCESS = None
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def speak_auto_network_status(text: str, env_file: Path, dotenv_values_func) -> None:
    """Speak a network status message with the TTS configured by the detected env file."""
    values = dotenv_values_func(env_file)
    tts_provider = (values.get("TTS_PROVIDER") or "elevenlabs").strip().lower()
    web_tts_provider = (values.get("WEB_TTS_PROVIDER") or "none").strip().lower()
    web_audio_enabled = str(values.get("WEB_AUDIO_ENABLED") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
        "on",
    }
    cloud_provider = tts_provider
    if cloud_provider == "none" and web_audio_enabled and web_tts_provider in {"openai", "elevenlabs"}:
        cloud_provider = web_tts_provider
    voice_id = (values.get("ELEVENLABS_VOICE_ID") or DEFAULT_ELEVENLABS_VOICE_ID).strip()

    def play_auto_mp3(audio_bytes: bytes) -> None:
        temp_audio = None
        try:
            with suppress_native_stderr():
                temp_audio = pyaudio.PyAudio()
            output_device_index, _status, _detail = resolve_pyaudio_device_index(
                temp_audio,
                values.get("BACKEND_AUDIO_OUTPUT_DEVICE"),
                input_device=False,
            )
            play_mp3_bytes(audio_bytes, audio=temp_audio, output_device_index=output_device_index)
        finally:
            if temp_audio is not None:
                try:
                    temp_audio.terminate()
                except Exception:
                    pass

    with TTS_LOCK:
        if cloud_provider == "none":
            print(f"Auto network status: {text}")
            return

        if cloud_provider == "elevenlabs":
            elevenlabs_api_key = read_secret_from_env_values(values, "ELEVENLABS_API_KEY")
            if elevenlabs_api_key:
                try:
                    if not elevenlabs_playback_available():
                        raise RuntimeError("local MP3 playback is not available")
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
                    audio_bytes = audio if isinstance(audio, bytes) else b"".join(audio)
                    play_auto_mp3(audio_bytes)
                    return
                except Exception as e:
                    if local_tts_playback_available():
                        print(f"Auto network status ElevenLabs TTS failed: {e}")
                    else:
                        return
            elif local_tts_playback_available():
                print("Auto network status ElevenLabs TTS skipped: missing ELEVENLABS_API_KEY_FILE")
            else:
                return

        if cloud_provider == "openai":
            openai_api_key = read_secret_from_env_values(values, "OPENAI_API_KEY")
            if openai_api_key:
                try:
                    if not elevenlabs_playback_available():
                        raise RuntimeError("local MP3 playback is not available")
                    client = openai.OpenAI(api_key=openai_api_key)
                    response = client.audio.speech.create(
                        model=(values.get("WEB_TTS_MODEL") or DEFAULT_OPENAI_TTS_MODEL).strip(),
                        voice=(values.get("WEB_TTS_VOICE") or DEFAULT_OPENAI_TTS_VOICE).strip(),
                        input=text.strip(),
                        response_format="mp3",
                        speed=env_float_from_mapping(values, "WEB_TTS_SPEED", 1.0),
                    )
                    play_auto_mp3(response.read())
                    return
                except Exception as e:
                    if local_tts_playback_available():
                        print(f"Auto network status OpenAI TTS failed: {e}")
                    else:
                        return
            elif local_tts_playback_available():
                print("Auto network status OpenAI TTS skipped: missing OPENAI_API_KEY_FILE")
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
            self.web_monitor.set_environment_loading(True, "rafraichissement de l'environnement")
            self.web_monitor.update(internet=online, env_file=detected_env, mode="auto")
            self.web_monitor.append_dialogue("assistant", status_text, speak=True)
        speak_auto_network_status(status_text, detected_env, self.dotenv_values_func)

    @property
    def detected_env_file(self) -> Path:
        return AUTO_ENV_ONLINE if self.current_online else AUTO_ENV_OFFLINE


class SileroVadGate:
    """Stateful Silero VAD runner for 16 kHz mono PCM chunks."""

    def __init__(
        self,
        model_path: str | Path = DEFAULT_SILERO_VAD_MODEL,
        *,
        threshold: float = 0.5,
        neg_threshold: float | None = None,
        min_speech_ms: int = 120,
        min_silence_ms: int = 650,
        speech_pad_ms: int = 100,
        max_speech_seconds: float = 8.0,
    ) -> None:
        self.model_path = Path(model_path)
        self.threshold = max(0.05, min(0.95, float(threshold or 0.5)))
        self.neg_threshold = (
            max(0.01, min(0.95, float(neg_threshold)))
            if neg_threshold is not None
            else max(self.threshold - 0.15, 0.01)
        )
        self.min_speech_ms = max(0, int(min_speech_ms or 0))
        self.min_silence_ms = max(0, int(min_silence_ms or 0))
        self.speech_pad_ms = max(0, int(speech_pad_ms or 0))
        self.max_speech_seconds = max(1.0, float(max_speech_seconds or 8.0))
        self.window_samples = 512
        self.context_samples = 64
        self.sample_rate = 16000
        if not self.model_path.is_file():
            raise RuntimeError(f"Silero VAD model not found: {self.model_path}")
        try:
            import onnxruntime
        except ImportError as e:
            raise RuntimeError("Silero VAD requires the onnxruntime package") from e

        options = onnxruntime.SessionOptions()
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = 1
        options.enable_cpu_mem_arena = False
        options.log_severity_level = 4
        self.session = onnxruntime.InferenceSession(
            str(self.model_path),
            providers=["CPUExecutionProvider"],
            sess_options=options,
        )
        self.reset()

    @property
    def chunk_ms(self) -> float:
        return self.window_samples / self.sample_rate * 1000.0

    def reset(self) -> None:
        self.h = np.zeros((1, 1, 128), dtype=np.float32)
        self.c = np.zeros((1, 1, 128), dtype=np.float32)
        self.context = np.zeros((1, self.context_samples), dtype=np.float32)
        self.pending = np.array([], dtype=np.float32)

    def process_pcm(self, audio_data: bytes) -> list[float]:
        samples = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32) / 32768.0
        if samples.size == 0:
            return []
        self.pending = np.concatenate((self.pending, samples))
        probabilities: list[float] = []
        while self.pending.size >= self.window_samples:
            window = self.pending[: self.window_samples]
            self.pending = self.pending[self.window_samples :]
            model_input = np.concatenate((self.context.reshape(-1), window), axis=0).reshape(1, -1)
            output, self.h, self.c = self.session.run(
                None,
                {"input": model_input.astype(np.float32), "h": self.h, "c": self.c},
            )
            self.context = window[-self.context_samples :].reshape(1, -1)
            probabilities.append(float(np.ravel(output)[-1]))
        return probabilities


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
        backend_audio_input_device: str | None = None,
        backend_audio_output_device: str | None = None,
        vad_model_path: str | Path = DEFAULT_SILERO_VAD_MODEL,
        vad_speech_threshold: float = 0.5,
        vad_negative_threshold: float | None = None,
        vad_min_speech_ms: int = 120,
        vad_min_silence_ms: int = 650,
        vad_speech_pad_ms: int = 100,
        vad_max_speech_seconds: float = 8.0,
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
        mcp_tool_routing_enabled: bool = False,
        session_context_store: SessionContextStore | None = None,
        session_context_size: int = 6000,
        voice_cancel_during_thinking: bool = False,
        interrupt_conversation_enabled: bool = False,
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
            backend_audio_input_device: Optional PyAudio input device index from BACKEND_AUDIO_INPUT_DEVICE
            backend_audio_output_device: Optional PyAudio output device index from BACKEND_AUDIO_OUTPUT_DEVICE
            vad_model_path: Local Silero VAD ONNX model path
            vad_speech_threshold: Silero probability threshold that starts speech
            vad_negative_threshold: Silero probability threshold that ends speech
            vad_min_speech_ms: Minimum speech duration before backend STT is accepted
            vad_min_silence_ms: Silence duration that ends an accepted phrase
            vad_speech_pad_ms: Audio retained before detected speech
            vad_max_speech_seconds: Hard cap for one accepted utterance
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
            mcp_tool_routing_enabled: Whether assistantOptions.routing keywords restrict a turn to one MCP server
            session_context_store: Persistent chat session store
            session_context_size: Maximum active session summary characters injected into each LLM/MCP turn; 0 disables injection
            voice_cancel_during_thinking: Listen for short spoken cancel words while the LLM/MCP agent is processing
            interrupt_conversation_enabled: Allow new text/STT commands to silently cancel current work before running
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
        self.vad = SileroVadGate(
            vad_model_path,
            threshold=vad_speech_threshold,
            neg_threshold=vad_negative_threshold,
            min_speech_ms=vad_min_speech_ms,
            min_silence_ms=vad_min_silence_ms,
            speech_pad_ms=vad_speech_pad_ms,
            max_speech_seconds=vad_max_speech_seconds,
        )
        self.tts_speed = max(0.6, min(1.8, float(tts_speed or 1.0)))
        self.wake_words = wake_words or []
        self.voice_cancel_during_thinking = voice_cancel_during_thinking
        self.interrupt_conversation_enabled = interrupt_conversation_enabled
        self.voice_cancel_words = DEFAULT_VOICE_CANCEL_WORDS

        # Initialize audio components
        with suppress_native_stderr():
            self.audio = pyaudio.PyAudio()
        (
            self.audio_input_device_index,
            self.audio_input_device_status,
            self.audio_input_device_detail,
        ) = resolve_pyaudio_device_index(
            self.audio,
            backend_audio_input_device,
            input_device=True,
        )
        (
            self.audio_output_device_index,
            self.audio_output_device_status,
            self.audio_output_device_detail,
        ) = resolve_pyaudio_device_index(
            self.audio,
            backend_audio_output_device,
            input_device=False,
        )
        if self.audio_input_device_status == "invalid":
            print(f"Backend audio input fallback: {self.audio_input_device_detail}")
        if self.audio_output_device_status == "invalid":
            print(f"Backend audio output fallback: {self.audio_output_device_detail}")
        print(f"Resolved backend audio input: {self.audio_input_device_detail}")
        print(f"Resolved backend audio output: {self.audio_output_device_detail}")

        # Speech-to-text configuration
        self.openai_api_key = openai_api_key
        self.stt_provider = stt_provider.lower()
        self.tts_provider = tts_provider.lower()
        self.local_whisper_model_name = local_whisper_model
        self.stt_language = stt_language or None
        base_stt_prompt = stt_prompt or DEFAULT_STT_PROMPT
        self.stt_prompt = base_stt_prompt
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
        self.thinking_sound_warning_shown = False
        self.thinking_sound_lock = threading.Lock()
        self.thinking_sound_stop_event = threading.Event()
        self.thinking_sound_thread: threading.Thread | None = None

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
        self.mcp_tool_routing_enabled = bool(mcp_tool_routing_enabled)
        self.mcp_tool_routes: list[dict[str, Any]] = []
        self.mcp_all_tools: list[Any] = []
        self.mcp_tools_by_server: dict[str, list[Any]] = {}
        self.session_context_store = session_context_store
        self.session_context_size = max(0, int(session_context_size or 0))
        self.stt_prompt = self._with_mcp_routing_stt_keywords(base_stt_prompt)
        self.mcp_client = None
        self.agent = None
        self.mcp_initialization_error: str | None = None
        self.reload_event = reload_event
        self.web_monitor = web_monitor
        self.pending_injected_command: str | None = None
        self.last_processed_command_key: str | None = None
        self.last_processed_command_at: float = 0.0
        self.pending_mcp_confirmation_route: dict[str, Any] | None = None
        self.mcp_reconnect_after_response = False
        self.microphone_available = True
        self.microphone_warning_shown = False
        if self.audio_input_device_status == "unavailable":
            self.microphone_available = False
        if self.web_monitor:
            self.web_monitor.update(
                services={
                    "Backend audio": backend_audio_service_state(
                        self.audio_input_device_status,
                        self.audio_input_device_detail,
                        self.audio_output_device_status,
                        self.audio_output_device_detail,
                    )
                }
            )
        
        base_system_prompt = system_prompt or DEFAULT_ASSISTANT_SYSTEM_PROMPT
        self.system_prompt = with_external_state_freshness_rule(base_system_prompt)
        if self.web_monitor:
            if self.session_context_store:
                self.web_monitor.set_context_state(
                    self.session_context_store.snapshot(),
                    session_context_size=self.session_context_size,
                )
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
        if not self.thinking_sound_path:
            if not self.thinking_sound_warning_shown:
                print(
                    f"Thinking sound '{self.thinking_sound_file}' not found. "
                    "Set THINKING_SOUND_FILE to a WAV file or place it in assets/."
                )
                self.thinking_sound_warning_shown = True
            return

        with self.thinking_sound_lock:
            if self.thinking_sound_thread and self.thinking_sound_thread.is_alive():
                return
            self.thinking_sound_stop_event.clear()
            self.thinking_sound_thread = threading.Thread(
                target=self._play_thinking_sound_loop,
                name="backend-thinking-sound",
                daemon=True,
            )
            self.thinking_sound_thread.start()

    def stop_thinking_sound(self) -> None:
        """Stop the thinking sound if it is currently playing."""
        thread = None
        with self.thinking_sound_lock:
            self.thinking_sound_stop_event.set()
            thread = self.thinking_sound_thread
            self.thinking_sound_thread = None
        if thread and thread.is_alive():
            thread.join(timeout=0.5)

    def _play_thinking_sound_loop(self) -> None:
        """Loop the configured WAV through the same PyAudio output path as backend TTS."""
        if not self.thinking_sound_path:
            return
        try:
            while not self.thinking_sound_stop_event.is_set():
                with wave.open(str(self.thinking_sound_path), "rb") as wav_file:
                    sample_width = wav_file.getsampwidth()
                    if sample_width != 2:
                        if not self.thinking_sound_warning_shown:
                            print(
                                f"Thinking sound '{self.thinking_sound_file}' must be 16-bit PCM WAV for backend playback."
                            )
                            self.thinking_sound_warning_shown = True
                        return
                    stream = None
                    try:
                        with suppress_native_stderr():
                            stream = self.audio.open(
                                format=pyaudio.paInt16,
                                channels=wav_file.getnchannels(),
                                rate=wav_file.getframerate(),
                                output=True,
                                output_device_index=self.audio_output_device_index,
                                frames_per_buffer=1024,
                            )
                        while not self.thinking_sound_stop_event.is_set():
                            data = wav_file.readframes(1024)
                            if not data:
                                break
                            stream.write(data)
                    finally:
                        if stream:
                            try:
                                stream.stop_stream()
                                stream.close()
                            except Exception:
                                pass
        except Exception as e:
            if not self.thinking_sound_warning_shown:
                print(f"Could not play thinking sound '{self.thinking_sound_path}': {e}")
                self.thinking_sound_warning_shown = True

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

    async def refresh_session_llm_summary(self, *, force: bool = False) -> bool:
        """Generate the persisted LLM summary for the active session."""
        if not self.session_context_store:
            return False
        source_summary = self.session_context_store.summary_source_text()
        if not source_summary:
            if self.session_context_store.injectable_summary() and not force:
                return False
            self.session_context_store.set_llm_summary("", source_summary)
            return False
        if not force and self.session_context_store.injectable_summary() != source_summary:
            return False

        try:
            llm = self._build_llm()
            result = await asyncio.wait_for(
                llm.ainvoke(SESSION_LLM_SUMMARY_PROMPT + source_summary),
                timeout=self.mcp_agent_timeout_seconds,
            )
            content = getattr(result, "content", result)
            if isinstance(content, list):
                content = "\n".join(str(item) for item in content if item)
            llm_summary = str(content or "").strip()
            if not llm_summary:
                return False
            self.session_context_store.set_llm_summary(llm_summary, source_summary)
            if self.web_monitor:
                self.web_monitor.set_context_state(
                    self.session_context_store.snapshot(),
                    session_context_size=self.session_context_size,
                )
            print("✓ Session LLM summary refreshed.")
            return True
        except Exception as e:
            print(f"Could not refresh session LLM summary, using transcript summary fallback: {e}")
            return False

    def refresh_session_llm_summary_blocking(self, *, force: bool = False) -> bool:
        """Refresh the session LLM summary from a non-async web handler thread."""
        try:
            return asyncio.run(self.refresh_session_llm_summary(force=force))
        except RuntimeError:
            return False

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
            prompt_config = server_config.get("assistantOptions") or server_config.get("assistantPrompt") or server_config.get("agentPrompt")
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

    def _split_routing_keywords(self, value: Any) -> list[str]:
        if isinstance(value, str):
            raw_values = value.split(",")
        elif isinstance(value, list):
            raw_values = value
        else:
            raw_values = []

        keywords = []
        seen = set()
        for item in raw_values:
            keyword = str(item or "").strip().lower()
            if keyword and keyword not in seen:
                keywords.append(keyword)
                seen.add(keyword)
        return keywords

    def _build_mcp_tool_routes(self, config: dict | None) -> list[dict[str, Any]]:
        routes = []
        if not config:
            return routes

        for server_name, server_config in (config.get("mcpServers") or {}).items():
            if not isinstance(server_config, dict):
                continue
            prompt_config = server_config.get("assistantOptions") or server_config.get("assistantPrompt") or server_config.get("agentPrompt")
            if not isinstance(prompt_config, dict):
                continue
            keywords = self._split_routing_keywords(prompt_config.get("routing"))
            if keywords:
                routes.append({"server": server_name, "keywords": keywords})
        return routes

    def _validate_unique_mcp_routing_keywords(self, config: dict | None) -> None:
        if not config:
            return

        keyword_owner: dict[str, str] = {}
        for server_name, server_config in (config.get("mcpServers") or {}).items():
            if not isinstance(server_config, dict):
                continue
            prompt_config = server_config.get("assistantOptions") or server_config.get("assistantPrompt") or server_config.get("agentPrompt")
            if not isinstance(prompt_config, dict):
                continue

            keywords = self._split_routing_keywords(prompt_config.get("routing"))
            if len(keywords) > 10:
                raise ValueError(f"routing words limit exceeded: {server_name} has {len(keywords)} words, max 10")

            for keyword in keywords:
                if keyword in keyword_owner and keyword_owner[keyword] != server_name:
                    raise ValueError(f"routing word duplicate: {keyword}")
                keyword_owner[keyword] = server_name

    def _mcp_routing_keywords(self) -> list[str]:
        keywords = []
        seen = set()
        for route in self._build_mcp_tool_routes(self.mcp_config):
            for keyword in route.get("keywords") or []:
                normalized = str(keyword or "").strip()
                dedupe_key = normalized.lower()
                if normalized and dedupe_key not in seen:
                    keywords.append(normalized)
                    seen.add(dedupe_key)
        return keywords

    def _with_mcp_routing_stt_keywords(self, base_prompt: str) -> str:
        prompt = (base_prompt or DEFAULT_STT_PROMPT).strip()
        keywords = self._mcp_routing_keywords()
        if not keywords:
            return prompt

        limited_keywords = keywords[:80]
        keyword_text = ", ".join(limited_keywords)
        routing_prompt = f"Mots métier MCP possibles: {keyword_text}."
        if routing_prompt.lower() in prompt.lower():
            return prompt

        enriched = f"{prompt} {routing_prompt}".strip()
        print(f"STT prompt enriched with {len(limited_keywords)} MCP routing keyword(s).")
        return enriched

    def _refresh_mcp_tool_routing_cache(self) -> None:
        self.mcp_tool_routes = self._build_mcp_tool_routes(self.mcp_config)
        self.mcp_all_tools = list(getattr(self.agent, "_tools", []) or [])
        self.mcp_tools_by_server = {}
        if not self.mcp_client:
            return

        connector_to_server = {}
        for server_name, session in getattr(self.mcp_client, "sessions", {}).items():
            connector = getattr(session, "connector", None)
            if connector is not None:
                connector_to_server[id(connector)] = server_name

        for tool in self.mcp_all_tools:
            connector = getattr(tool, "tool_connector", None)
            server_name = connector_to_server.get(id(connector))
            if server_name:
                self.mcp_tools_by_server.setdefault(server_name, []).append(tool)

        if self.mcp_tool_routing_enabled and self.mcp_tool_routes:
            configured = ", ".join(
                f"{route['server']}({', '.join(route['keywords'])})" for route in self.mcp_tool_routes
            )
            print(f"MCP tool routing configured: {configured}")
            if len(self.mcp_all_tools) > OPENAI_MAX_TOOLS_PER_REQUEST:
                print(
                    "MCP tool routing guard enabled: "
                    f"{len(self.mcp_all_tools)} tools loaded, OpenAI request limit is {OPENAI_MAX_TOOLS_PER_REQUEST}. "
                    "Unrouted turns will use the first safe MCP server route, or no MCP tools if none is available."
                )

    def _source_value(self, source: dict, *keys: str) -> str | None:
        for key in keys:
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _command_dedupe_key(self, text: str) -> str:
        return re.sub(r"\s+", " ", (text or "").strip().lower())

    def _should_skip_duplicate_command(self, text: str) -> bool:
        key = self._command_dedupe_key(text)
        if not key:
            return False

        now = time.monotonic()
        if (
            self.last_processed_command_key == key
            and now - self.last_processed_command_at <= DUPLICATE_COMMAND_SUPPRESS_SECONDS
        ):
            return True

        self.last_processed_command_key = key
        self.last_processed_command_at = now
        return False

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
        prompt_name = self._source_value(source, "promptName", "prompt_name", "prompt", "name") or DEFAULT_MCP_PROMPT_NAME
        resource_uri = self._source_value(source, "resourceUri", "resource_uri", "resource") or DEFAULT_MCP_PROMPT_RESOURCE_URI
        tool_name = self._source_value(source, "tool", "toolName", "tool_name") or DEFAULT_MCP_PROMPT_TOOL
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
        prompt_name = self._source_value(source, "promptName", "prompt_name", "prompt", "name") or DEFAULT_MCP_PROMPT_NAME
        resource_uri = self._source_value(source, "resourceUri", "resource_uri", "resource") or DEFAULT_MCP_PROMPT_RESOURCE_URI
        tool_name = self._source_value(source, "tool", "toolName", "tool_name") or DEFAULT_MCP_PROMPT_TOOL

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

    def _mcp_config_subset(self, config: dict, server_names: list[str]) -> dict:
        """Return a shallow MCP config copy containing only selected servers."""
        server_configs = config.get("mcpServers") or {}
        subset = dict(config)
        subset["mcpServers"] = {
            name: server_configs[name]
            for name in server_names
            if name in server_configs
        }
        return subset

    async def _close_probe_mcp_client(self, client: MCPClient) -> None:
        try:
            await asyncio.wait_for(client.close_all_sessions(), timeout=3.0)
        except Exception:
            pass

    async def _filter_connectable_mcp_servers(self, config: dict) -> tuple[dict, dict[str, str]]:
        """Keep startup usable when one configured MCP server is temporarily down."""
        server_configs = config.get("mcpServers") or {}
        if len(server_configs) <= 1:
            return config, {}

        available_servers: list[str] = []
        failed_servers: dict[str, str] = {}
        for server_name in server_configs:
            probe_config = self._mcp_config_subset(config, [server_name])
            probe_client = MCPClient.from_dict(probe_config)
            try:
                await probe_client.create_session(server_name)
                available_servers.append(server_name)
            except Exception as e:
                failed_servers[server_name] = str(e)
            finally:
                await self._close_probe_mcp_client(probe_client)

        if not failed_servers:
            return config, {}

        if not available_servers:
            return config, failed_servers

        return self._mcp_config_subset(config, available_servers), failed_servers

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
        self.mcp_config = config
        if self.web_monitor:
            self.web_monitor.update(mcp_config=config)

        try:
            self.mcp_initialization_error = None
            self._validate_unique_mcp_routing_keywords(config)
            runtime_config, failed_servers = await self._filter_connectable_mcp_servers(config)
            if failed_servers:
                failed_detail = "; ".join(f"{name}: {error}" for name, error in failed_servers.items())
                if runtime_config is config:
                    self._log_mcp_prompt_warning(
                        "All configured MCP servers failed startup probe; continuing with normal initialization "
                        f"to preserve the original error. Detail: {failed_detail}"
                    )
                else:
                    available = ", ".join(sorted((runtime_config.get("mcpServers") or {}).keys()))
                    skipped = ", ".join(sorted(failed_servers.keys()))
                    self._log_mcp_prompt_warning(
                        f"Skipping unavailable MCP server(s) for this run: {skipped}. Available MCP server(s): {available}."
                    )
                    if self.web_monitor:
                        self.web_monitor.update(
                            services={
                                "MCP": {
                                    "status": "warning",
                                    "detail": f"available: {available}; skipped: {skipped}",
                                }
                            }
                        )
                    config = runtime_config
                    self.mcp_config = runtime_config

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
            self._refresh_mcp_tool_routing_cache()

            print("✓ MCP servers initialized successfully!")
            if self.web_monitor:
                server_names = sorted(config.get("mcpServers", {}).keys())
                detail = ", ".join(server_names) if server_names else "no configured servers"
                self.web_monitor.update(services={"MCP": {"status": "initialized", "detail": detail}})
            return True

        except Exception as e:
            self.mcp_initialization_error = str(e)
            print(f"✗ Error initializing MCP: {e}")
            if self.web_monitor:
                self.web_monitor.update(services={"MCP": {"status": "error", "detail": str(e)}})
            return False

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
                    input_device_index=self.audio_input_device_index,
                    frames_per_buffer=self.chunk,
                )

            self.vad.reset()
            frames: list[bytes] = []
            pre_roll: list[bytes] = []
            speech_candidate: list[bytes] = []
            speech_candidate_ms = 0.0
            silence_ms = 0.0
            recorded_speech_ms = 0.0
            audio_chunk_ms = self.chunk / self.rate * 1000.0
            pad_frames = max(1, int((self.vad.speech_pad_ms / audio_chunk_ms) + 0.999))
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
                probabilities = self.vad.process_pcm(data)
                speech_probability = max(probabilities) if probabilities else 0.0
                chunk_ms = self.vad.chunk_ms * max(1, len(probabilities))

                if has_speech:
                    frames.append(data)
                    recorded_speech_ms += chunk_ms
                    if speech_probability < self.vad.neg_threshold:
                        silence_ms += chunk_ms
                        if silence_ms >= self.vad.min_silence_ms:
                            break
                    else:
                        silence_ms = 0.0
                    if recorded_speech_ms >= self.vad.max_speech_seconds * 1000:
                        break
                elif speech_probability >= self.vad.threshold:
                    speech_candidate.append(data)
                    speech_candidate_ms += chunk_ms
                    if speech_candidate_ms >= self.vad.min_speech_ms:
                        has_speech = True
                        frames = pre_roll + speech_candidate
                        recorded_speech_ms = speech_candidate_ms
                        pre_roll = []
                        speech_candidate = []
                else:
                    speech_candidate = []
                    speech_candidate_ms = 0.0
                    pre_roll.append(data)
                    if len(pre_roll) > pad_frames:
                        pre_roll = pre_roll[-pad_frames:]

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
                services={
                    "Backend audio": backend_audio_service_state(
                        "unavailable",
                        error_text,
                        self.audio_output_device_status,
                        self.audio_output_device_detail,
                    )
                }
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
                    input_device_index=self.audio_input_device_index,
                    frames_per_buffer=self.chunk,
                )

            self.vad.reset()
            frames = []
            silence_ms = 0.0
            max_frames = max(1, int(self.rate / self.chunk * 1.4))
            has_speech = False

            while len(frames) < max_frames and not stop_event.is_set():
                data = stream.read(self.chunk, exception_on_overflow=False)
                frames.append(data)
                probabilities = self.vad.process_pcm(data)
                speech_probability = max(probabilities) if probabilities else 0.0

                if speech_probability >= self.vad.threshold:
                    silence_ms = 0.0
                    has_speech = True
                elif has_speech:
                    silence_ms += self.vad.chunk_ms * max(1, len(probabilities))
                    if silence_ms > 450:
                        break

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

    def stop_tts(self) -> None:
        """Stop any local/backend TTS playback that can be interrupted."""
        TTS_STOP_EVENT.set()
        try:
            TTS_ENGINE.stop()
        except Exception:
            pass
        process = TTS_PLAYBACK_PROCESS
        if process and process.poll() is None:
            try:
                process.terminate()
            except Exception:
                pass

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

    async def listen_for_voice_interrupt_during_activity(
        self,
        stop_event: threading.Event,
        activity_task: asyncio.Task,
    ) -> str | None:
        """Return a new spoken command or an empty string for cancel-only interruption."""
        if not self.microphone_available:
            return None

        print("Voice interrupt listener active.")
        while not stop_event.is_set() and not activity_task.done():
            audio_data = await asyncio.to_thread(self.record_voice_cancel_audio, stop_event)
            if stop_event.is_set() or activity_task.done():
                return None
            if not audio_data:
                await asyncio.sleep(0.05)
                continue

            text = await asyncio.to_thread(self.audio_to_text, audio_data)
            if not text:
                continue
            print(f"Voice interrupt listener heard: {text}")
            if self.is_voice_cancel_phrase(text):
                return ""

            should_process, matched_wake_word, command_text = apply_wake_word(text, self.wake_words)
            if not should_process:
                print("Wake word not detected for interrupt command. Ignoring transcription.")
                continue
            if matched_wake_word and command_text != text:
                print(f"Interrupt command after wake word: {command_text}")
            return command_text

        return None

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

        cleaned = FUSED_SET_COMMAND_RE.sub(split_fused_set_command, cleaned, count=1)
        if is_likely_stt_silence_hallucination(cleaned):
            print(f"Ignored likely Whisper silence hallucination: {cleaned!r}")
            return ""
        return cleaned

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

        TTS_STOP_EVENT.clear()
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
                        TTS_STOP_EVENT.clear()
                        audio = self.generate_openai_tts_audio(text, speed=self.tts_speed)
                        play_mp3_bytes(
                            audio,
                            audio=self.audio,
                            output_device_index=self.audio_output_device_index,
                        )
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
                    if local_tts_playback_available():
                        print("ElevenLabs TTS selected but local MP3 playback is unavailable. Falling back to pyttsx3...")
                    return self.text_to_speech_pyttsx3(text)
                try:
                    with TTS_LOCK:
                        TTS_STOP_EVENT.clear()
                        audio = self.generate_elevenlabs_tts_audio(text, speed=self.tts_speed)
                        audio_bytes = audio if isinstance(audio, bytes) else b"".join(audio)
                        play_mp3_bytes(
                            audio_bytes,
                            audio=self.audio,
                            output_device_index=self.audio_output_device_index,
                        )
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
        """Speak text through local TTS, preferring a file rendered into backend PyAudio output."""
        if not local_tts_playback_available():
            return False

        temp_path = None
        try:
            with TTS_LOCK:
                TTS_STOP_EVENT.clear()
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
                    temp_path = temp_file.name
                TTS_ENGINE.save_to_file(text, temp_path)
                TTS_ENGINE.runAndWait()
                file_rendered = temp_path and os.path.exists(temp_path) and os.path.getsize(temp_path) > 0
                if file_rendered:
                    try:
                        self.play_local_tts_file(temp_path, stop_event=TTS_STOP_EVENT)
                        return True
                    except Exception as e:
                        if self.audio_output_device_index is not None:
                            print(f"Local pyttsx3 backend playback failed on selected output: {e}")
                            return False
                        print(f"Local pyttsx3 backend playback failed: {e}. Falling back to direct system TTS...")
                if self.audio_output_device_index is not None:
                    print("Local pyttsx3 file rendering failed; direct system TTS skipped because a backend output device is selected.")
                    return False
                if not file_rendered:
                    print("Local pyttsx3 file rendering failed. Falling back to direct system TTS...")
                TTS_ENGINE.say(text)
                TTS_ENGINE.runAndWait()
            return True
        except Exception as e:
            print(f"Local pyttsx3 TTS failed: {e}")
            return False
        finally:
            if temp_path:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass

    def play_local_tts_file(self, audio_path: str | Path, *, stop_event: threading.Event | None = None) -> None:
        """Play a pyttsx3-rendered file through the selected backend output device."""
        try:
            self.play_wav_file(audio_path, stop_event=stop_event)
            return
        except Exception as wav_error:
            if not ffmpeg_decode_available():
                if self.audio_output_device_index is not None:
                    raise RuntimeError(
                        f"local TTS file is not directly playable and ffmpeg is unavailable: {wav_error}"
                    ) from wav_error
                raise

        pcm_bytes = decode_audio_file_to_pcm_bytes(audio_path)
        play_pcm_bytes(
            self.audio,
            pcm_bytes,
            sample_rate=DEFAULT_BACKEND_MP3_SAMPLE_RATE,
            channels=DEFAULT_BACKEND_MP3_CHANNELS,
            output_device_index=self.audio_output_device_index,
            stop_event=stop_event,
        )

    def play_wav_file(self, wav_path: str | Path, *, stop_event: threading.Event | None = None) -> None:
        """Play a WAV file through backend PyAudio output selection."""
        with wave.open(str(wav_path), "rb") as wav_file:
            sample_width = wav_file.getsampwidth()
            if sample_width != 2:
                raise RuntimeError("only 16-bit PCM WAV playback is supported")
            stream = None
            try:
                with suppress_native_stderr():
                    stream = self.audio.open(
                        format=pyaudio.paInt16,
                        channels=wav_file.getnchannels(),
                        rate=wav_file.getframerate(),
                        output=True,
                        output_device_index=self.audio_output_device_index,
                        frames_per_buffer=1024,
                    )
                while True:
                    if stop_event and stop_event.is_set():
                        break
                    data = wav_file.readframes(1024)
                    if not data:
                        break
                    stream.write(data)
            finally:
                if stream:
                    try:
                        stream.stop_stream()
                        stream.close()
                    except Exception:
                        pass

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

    def generate_elevenlabs_tts_audio(
        self,
        text: str,
        speed: float | None = None,
        voice_id: str | None = None,
    ) -> Any:
        """Generate MP3 speech with ElevenLabs."""
        if not self.elevenlabs_client:
            raise ValueError("ElevenLabs client is not configured")
        cleaned_text = text.strip()
        if not cleaned_text:
            raise ValueError("text is required")

        return self.elevenlabs_client.text_to_speech.convert(
            text=cleaned_text,
            voice_id=voice_id or self.elevenlabs_voice_id,
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
        speed: float | None = None,
    ) -> dict[str, Any]:
        """Generate browser-playable speech using OpenAI from the backend."""
        audio_bytes = self.generate_openai_tts_audio(text, model=model, voice=voice, speed=speed)
        return {
            "audio_base64": base64.b64encode(audio_bytes).decode("ascii"),
            "mime_type": "audio/mpeg",
        }

    def web_text_to_speech_elevenlabs(
        self,
        text: str,
        voice_id: str | None = None,
        speed: float | None = None,
    ) -> dict[str, Any]:
        """Generate browser-playable speech using ElevenLabs from the backend."""
        audio = self.generate_elevenlabs_tts_audio(text, speed=speed, voice_id=voice_id)
        audio_bytes = audio if isinstance(audio, bytes) else b"".join(audio)
        return {
            "audio_base64": base64.b64encode(audio_bytes).decode("ascii"),
            "mime_type": "audio/mpeg",
        }

    def _select_mcp_tool_route(self, text: str) -> dict[str, Any] | None:
        if not self.mcp_tool_routing_enabled or not self.mcp_tool_routes:
            return None

        normalized = text.lower()
        for route in self.mcp_tool_routes:
            if any(self._routing_keyword_matches(normalized, keyword) for keyword in route.get("keywords") or []):
                server_name = route.get("server")
                if server_name and self.mcp_tools_by_server.get(server_name):
                    return route
        return None

    def _routing_keyword_matches(self, normalized_text: str, keyword: str) -> bool:
        keyword = str(keyword or "").strip().lower()
        if not keyword:
            return False
        if any(char.isspace() for char in keyword):
            return keyword in normalized_text
        return re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", normalized_text, flags=re.IGNORECASE) is not None

    def _is_mcp_confirmation_reply(self, text: str) -> bool:
        normalized = re.sub(r"[^\w' -]+", " ", str(text or "").strip().lower(), flags=re.UNICODE)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return normalized in MCP_CONFIRMATION_WORDS

    def _assistant_response_requests_confirmation(self, response: str) -> bool:
        normalized = str(response or "").strip().lower()
        if "?" not in normalized:
            return False
        return any(
            marker in normalized
            for marker in (
                "confirme",
                "confirmez",
                "confirmation",
                "voulez-vous",
                "veux-tu",
                "êtes-vous sûr",
                "etes-vous sur",
                "are you sure",
                "do you confirm",
                "confirm",
            )
        )

    async def _set_agent_tool_subset(self, tools: list[Any]) -> None:
        if not self.agent:
            return

        self.agent._tools = list(tools)
        if hasattr(self.agent, "_create_system_message_from_tools"):
            await self.agent._create_system_message_from_tools(self.agent._tools)
        if hasattr(self.agent, "_create_agent"):
            self.agent._agent_executor = self.agent._create_agent()

    def _all_mcp_tools_exceed_request_limit(self) -> bool:
        return len(self.mcp_all_tools or []) > OPENAI_MAX_TOOLS_PER_REQUEST

    def _default_mcp_tool_route_for_overflow(self) -> tuple[str, list[Any]] | None:
        for route in self.mcp_tool_routes or []:
            server_name = str(route.get("server") or "")
            tools = self.mcp_tools_by_server.get(server_name) or []
            if tools and len(tools) <= OPENAI_MAX_TOOLS_PER_REQUEST:
                return server_name, tools
        return None

    async def _run_agent_with_tools(
        self,
        agent_input: str,
        tools: list[Any],
        route_for_confirmation: dict[str, Any] | None = None,
    ) -> str:
        original_tools = list(getattr(self.agent, "_tools", []) or [])
        try:
            await self._set_agent_tool_subset(tools)
            response = await asyncio.wait_for(
                self.agent.run(agent_input),
                timeout=self.mcp_agent_timeout_seconds,
            )
            response_text = response if isinstance(response, str) else str(response)
            if route_for_confirmation and self._assistant_response_requests_confirmation(response_text):
                self.pending_mcp_confirmation_route = route_for_confirmation
            elif route_for_confirmation:
                self.pending_mcp_confirmation_route = None
            return response_text
        finally:
            await self._set_agent_tool_subset(original_tools or self.mcp_all_tools)

    async def _run_agent_with_optional_tool_routing(self, text: str) -> str:
        if not self.agent:
            return "Sorry, the assistant is not properly initialized."

        agent_input = self._with_runtime_instructions(text)
        route = self._select_mcp_tool_route(text)
        confirmation_route = False
        if not route and self.pending_mcp_confirmation_route and self._is_mcp_confirmation_reply(text):
            route = self.pending_mcp_confirmation_route
            confirmation_route = True
            print(f"[MCP ROUTE: confirmation -> {route.get('server')}]")
        if not confirmation_route and not self._is_mcp_confirmation_reply(text):
            self.pending_mcp_confirmation_route = None
        if not route:
            if self.mcp_tool_routing_enabled and self._all_mcp_tools_exceed_request_limit():
                default_route = self._default_mcp_tool_route_for_overflow()
                if default_route:
                    server_name, tools = default_route
                    print(f"[MCP CALL: no route, default {server_name}]")
                    default_route_config = next(
                        (
                            route_config
                            for route_config in self.mcp_tool_routes
                            if str(route_config.get("server") or "") == server_name
                        ),
                        None,
                    )
                    return await self._run_agent_with_tools(agent_input, tools, default_route_config)
                print("[MCP CALL: no route, no tools]")
                return await self._run_agent_with_tools(agent_input, [])
            response = await asyncio.wait_for(
                self.agent.run(agent_input),
                timeout=self.mcp_agent_timeout_seconds,
            )
            return response if isinstance(response, str) else str(response)

        server_name = str(route["server"])
        routed_tools = self.mcp_tools_by_server.get(server_name) or []
        if not routed_tools:
            if self.mcp_tool_routing_enabled and self._all_mcp_tools_exceed_request_limit():
                print("[MCP CALL: route has no tools, no tools]")
                return await self._run_agent_with_tools(agent_input, [])
            response = await asyncio.wait_for(
                self.agent.run(agent_input),
                timeout=self.mcp_agent_timeout_seconds,
            )
            return response if isinstance(response, str) else str(response)

        print(f"[MCP CALL: {server_name} only]")
        original_tools = list(getattr(self.agent, "_tools", []) or [])
        original_history = list(self.agent.get_conversation_history()) if hasattr(self.agent, "get_conversation_history") else []
        routed_failed = False
        try:
            await self._set_agent_tool_subset(routed_tools)
            response = await asyncio.wait_for(
                self.agent.run(agent_input),
                timeout=self.mcp_agent_timeout_seconds,
            )
            if isinstance(response, str) and response.strip():
                if self._assistant_response_requests_confirmation(response):
                    self.pending_mcp_confirmation_route = route
                else:
                    self.pending_mcp_confirmation_route = None
                return response
            if response:
                response_text = str(response)
                if self._assistant_response_requests_confirmation(response_text):
                    self.pending_mcp_confirmation_route = route
                else:
                    self.pending_mcp_confirmation_route = None
                return response_text
            print(f"[MCP CALL: {server_name} only] empty response, falling back to all tools")
            routed_failed = True
        except Exception as e:
            print(f"[MCP CALL: {server_name} only] failed: {e}; falling back to all tools")
            routed_failed = True
        finally:
            if self.agent:
                await self._set_agent_tool_subset(original_tools or self.mcp_all_tools)

        if self.agent and routed_failed:
            self.agent._conversation_history = original_history

        if self.mcp_tool_routing_enabled and self._all_mcp_tools_exceed_request_limit():
            return "Je n'ai pas pu exécuter cette demande avec le serveur MCP routé. Merci de reformuler en précisant le domaine, par exemple lumière, QLC, mixeur, volume ou bus."

        response = await asyncio.wait_for(
            self.agent.run(agent_input),
            timeout=self.mcp_agent_timeout_seconds,
        )
        return response if isinstance(response, str) else str(response)

    async def process_command(self, text: str) -> str:
        """Process user command with MCP agent."""
        print(f"\nYou said: {text}")
        if self.session_context_store:
            self.session_context_store.append_message("user", text)
            if self.web_monitor:
                self.web_monitor.set_context_state(
                    self.session_context_store.snapshot(),
                    session_context_size=self.session_context_size,
                )
        if self.web_monitor:
            self.web_monitor.append_dialogue("user", text)
            self.web_monitor.set_assistant_busy(True)

        # Special commands
        if text.lower() in ["exit", "quit", "goodbye"]:
            return "Goodbye! Have a great day!"

        if self.is_voice_cancel_phrase(text):
            self.stop_tts()
            return "D'accord, j'arrête."

        if text.lower() == "clear":
            if self.agent:
                self.agent.clear_conversation_history()
            if self.session_context_store:
                self.session_context_store.clear_current()
                if self.web_monitor:
                    self.web_monitor.replace_dialogue(self.session_context_store.snapshot().get("messages") or [])
                    self.web_monitor.set_context_state(
                        self.session_context_store.snapshot(),
                        session_context_size=self.session_context_size,
                    )
            return "Conversation history cleared."

        # Process with MCP agent
        if not self.agent:
            detail = self.mcp_initialization_error or "MCP initialization failed"
            return (
                "L'assistant web reste disponible, mais les outils MCP ne sont pas initialisés. "
                "Corrige la configuration dans l'onglet Config puis sauvegarde pour redémarrer. "
                f"Détail: {detail}"
            )

        self.start_thinking_sound()
        try:
            return await self._run_agent_with_optional_tool_routing(text)
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
            if self._is_mcp_connection_loss_error(error_text):
                self.mcp_reconnect_after_response = True
                return (
                    "La connexion au serveur MCP a été perdue pendant l'appel outil. "
                    "Je vais redémarrer la session MCP, puis tu pourras relancer la commande."
                )
            return f"Sorry, I encountered an error: {error_text}"
        finally:
            self.stop_thinking_sound()

    def _is_mcp_connection_loss_error(self, error_text: str) -> bool:
        normalized = error_text.lower()
        return any(
            marker in normalized
            for marker in (
                "failed due to connection loss",
                "connection closed",
                "streamable http context cleanup",
                "read stream closed",
                "write stream closed",
            )
        )

    def _looks_like_current_external_state_query(self, text: str) -> bool:
        normalized = text.lower()
        return any(marker in normalized for marker in CURRENT_STATE_QUERY_MARKERS)

    def _with_runtime_instructions(self, text: str) -> str:
        instructions = [TOOL_ACTION_FRESHNESS_RULE]
        if self.session_context_size > 0 and self.session_context_store:
            session_context = self.session_context_store.context_text(
                exclude_last_user=True,
                max_chars=self.session_context_size,
            )
            if session_context:
                instructions.append(session_context)
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
            print("Failed to initialize MCP. Continuing without MCP tools; use the web config to fix and reload.")
            if self.web_monitor:
                self.web_monitor.set_environment_loading(False)
        else:
            await self.refresh_session_llm_summary()

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

                if self._should_skip_duplicate_command(text):
                    print(f"Duplicate command ignored: {text}")
                    continue

                # Process command
                process_task = asyncio.create_task(self.process_command(text))
                voice_cancel_stop_event = None
                voice_cancel_task = None
                interrupt_command: str | None = None
                if self.microphone_available and (
                    self.interrupt_conversation_enabled or self.voice_cancel_during_thinking
                ):
                    voice_cancel_stop_event = threading.Event()
                    if self.interrupt_conversation_enabled:
                        voice_cancel_task = asyncio.create_task(
                            self.listen_for_voice_interrupt_during_activity(
                                voice_cancel_stop_event,
                                process_task,
                            )
                        )
                    else:
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
                                voice_result = voice_cancel_task.result()
                                if self.interrupt_conversation_enabled:
                                    if voice_result is not None:
                                        interrupt_command = voice_result or None
                                        voice_cancel_detected = True
                                else:
                                    voice_cancel_detected = bool(voice_result)
                        if self.web_monitor and self.web_monitor.pop_cancel_requested():
                            print("Web monitor cancel requested. Cancelling current command.")
                            self.stop_tts()
                            process_task.cancel()
                            try:
                                await process_task
                            except asyncio.CancelledError:
                                pass
                            self.web_monitor.set_assistant_busy(False)
                            command_cancelled = True
                            break
                        if voice_cancel_detected:
                            if interrupt_command:
                                print(f"Voice interrupt command detected. Cancelling current command: {interrupt_command}")
                            else:
                                print("Voice cancel phrase detected. Cancelling current command.")
                            self.stop_tts()
                            process_task.cancel()
                            try:
                                await process_task
                            except asyncio.CancelledError:
                                pass
                            if self.web_monitor:
                                self.web_monitor.set_assistant_busy(False)
                            if interrupt_command:
                                self.pending_injected_command = interrupt_command
                            command_cancelled = True
                            break
                        if self.reload_event and self.reload_event.is_set():
                            print("Auto environment reload requested. Cancelling current command.")
                            self.stop_tts()
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
                if self.session_context_store:
                    self.session_context_store.append_message("assistant", response)
                    if self.web_monitor:
                        self.web_monitor.set_context_state(
                            self.session_context_store.snapshot(),
                            session_context_size=self.session_context_size,
                        )
                if self.web_monitor:
                    self.web_monitor.append_dialogue("assistant", response)
                    self.web_monitor.set_assistant_busy(False)

                # Check for exit
                if text.lower() in ["exit", "quit", "goodbye"]:
                    break

                # Try to speak the response
                if self.interrupt_conversation_enabled and self.microphone_available and self.tts_provider != "none":
                    tts_task = asyncio.create_task(
                        asyncio.to_thread(lambda: asyncio.run(self.text_to_speech(response)))
                    )
                    voice_tts_stop_event = threading.Event()
                    voice_tts_task = asyncio.create_task(
                        self.listen_for_voice_interrupt_during_activity(
                            voice_tts_stop_event,
                            tts_task,
                        )
                    )
                    try:
                        while not tts_task.done():
                            if voice_tts_task.done() and not voice_tts_task.cancelled():
                                voice_tts_error = voice_tts_task.exception()
                                if voice_tts_error:
                                    print(f"Voice interrupt listener stopped during TTS: {voice_tts_error}")
                                    break
                                voice_result = voice_tts_task.result()
                                if voice_result is not None:
                                    self.stop_tts()
                                    if voice_result:
                                        print(f"Voice interrupt command detected during TTS: {voice_result}")
                                        self.pending_injected_command = voice_result
                                    else:
                                        print("Voice cancel phrase detected during TTS.")
                                    break
                            if self.reload_event and self.reload_event.is_set():
                                self.stop_tts()
                                break
                            await asyncio.sleep(0.1)
                        await tts_task
                    finally:
                        voice_tts_stop_event.set()
                        if not voice_tts_task.done():
                            voice_tts_task.cancel()
                            try:
                                await voice_tts_task
                            except asyncio.CancelledError:
                                pass
                else:
                    await self.text_to_speech(response)

                if self.mcp_reconnect_after_response:
                    self.mcp_reconnect_after_response = False
                    print("MCP connection loss detected. Restarting assistant sessions.")
                    return "reload"

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

    def env_int_from_values(values: dict, name: str, default: int) -> int:
        value = values.get(name)
        if value is None or str(value).strip() == "":
            return default
        try:
            return int(str(value).strip())
        except ValueError:
            return default

    def env_bool_from_values(values: dict, name: str, default: bool = False) -> bool:
        value = values.get(name)
        if value is None or str(value).strip() == "":
            return default
        return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}

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

    def display_env_path(path: Path) -> str:
        try:
            return str(path.resolve().relative_to(Path.cwd().resolve()))
        except ValueError:
            return str(path)

    def list_available_env_files(current_env_file: Path, auto_env_mode: bool) -> dict[str, Any]:
        candidates: dict[str, Path] = {}
        search_dirs = [Path.cwd()]
        if current_env_file.parent not in search_dirs:
            search_dirs.append(current_env_file.parent)
        for search_dir in search_dirs:
            for candidate in search_dir.glob(".env*"):
                if candidate.is_file():
                    candidates[display_env_path(candidate)] = candidate
        if current_env_file.exists():
            candidates[display_env_path(current_env_file)] = current_env_file

        profiles = [
            {
                "id": profile_id,
                "label": profile_id,
                "selected": profile_path.resolve() == current_env_file.resolve(),
            }
            for profile_id, profile_path in sorted(candidates.items(), key=lambda item: item[0].lower())
        ]
        return {
            "current": display_env_path(current_env_file),
            "profiles": profiles,
            "switching_enabled": not auto_env_mode,
            "auto_mode": auto_env_mode,
            "connectivity_locked": current_env_file.name in {".env.online", ".env.offline"},
            "message": (
                "Manual env switching is disabled while --env-file auto controls the active profile."
                if auto_env_mode
                else ""
            ),
        }

    def remote_screen_url_from_values(values: dict) -> str:
        return (values.get("REMOTE_SCREEN_VNC_URL") or "vnc://192.168.0.160:5900?password=ronron").strip()

    def save_remote_screen_config(env_file: Path, vnc_url: str, web_monitor: WebMonitor | None) -> dict[str, Any]:
        cleaned_url = vnc_url.strip()
        if not cleaned_url:
            raise ValueError("VNC URL is required")
        parsed = urllib.parse.urlparse(cleaned_url)
        if parsed.scheme not in {"vnc", "http", "https"}:
            raise ValueError("VNC URL must start with vnc://, http://, or https://")
        if not parsed.netloc:
            raise ValueError("VNC URL must include a host")

        update_env_file_values(env_file, {"REMOTE_SCREEN_VNC_URL": cleaned_url})
        if web_monitor:
            web_monitor.update(remote_screen={"vnc_url": cleaned_url})
        return {"saved": True, "vnc_url": cleaned_url}

    def build_cloud_api_status(env_file: Path) -> dict[str, Any]:
        values = dict(dotenv_values(env_file))
        openai_api_key = read_secret_from_env_values(values, "OPENAI_API_KEY")
        elevenlabs_api_key = read_secret_from_env_values(values, "ELEVENLABS_API_KEY")
        result: dict[str, Any] = {
            "openai": {
                "status": "missing" if not openai_api_key else "unavailable",
                "masked_key": mask_secret_tail(openai_api_key),
                "lines": [
                    "Crédit restant: non exposé par l'API publique OpenAI.",
                    "Le coût récent exige une clé autorisée pour les endpoints organisation.",
                ],
            },
            "elevenlabs": {
                "status": "missing" if not elevenlabs_api_key else "unavailable",
                "masked_key": mask_secret_tail(elevenlabs_api_key),
                "lines": [],
            },
        }

        if openai_api_key:
            start_time = int(time.time()) - 7 * 24 * 60 * 60
            url = f"https://api.openai.com/v1/organization/costs?start_time={start_time}&bucket_width=1d&limit=7"
            try:
                data = read_json_url(url, {"Authorization": f"Bearer {openai_api_key}"})
                total_value = 0.0
                currency = "usd"
                for bucket in data.get("data") or []:
                    for item in bucket.get("results") or []:
                        amount = item.get("amount") or {}
                        total_value += float(amount.get("value") or 0)
                        currency = str(amount.get("currency") or currency)
                result["openai"] = {
                    "status": "ok",
                    "masked_key": mask_secret_tail(openai_api_key),
                    "cost_7d": {"value": round(total_value, 4), "currency": currency},
                    "lines": [
                        "Crédit restant: non exposé par l'API publique OpenAI.",
                        "Costs API accessible avec cette clé.",
                    ],
                }
            except urllib.error.HTTPError as e:
                detail = f"Costs API indisponible avec cette clé: HTTP {e.code}."
                if e.code in {401, 403}:
                    detail = "Costs API non autorisée avec cette clé. Utilise une clé admin/org pour afficher le coût."
                result["openai"]["lines"].append(detail)
            except Exception as e:
                result["openai"]["lines"].append(f"Costs API indisponible: {e}")

        if elevenlabs_api_key:
            try:
                data = read_json_url(
                    "https://api.elevenlabs.io/v1/user/subscription",
                    {"xi-api-key": elevenlabs_api_key},
                )
                used = int(data.get("character_count") or 0)
                limit = int(data.get("character_limit") or 0)
                remaining = max(0, limit - used) if limit else 0
                tier = str(data.get("tier") or "unknown")
                subscription_status = str(data.get("status") or "unknown")
                overage = data.get("current_overage") or {}
                lines = [f"Plan: {tier}", f"Statut: {subscription_status}"]
                if overage:
                    lines.append(f"Overage: {overage.get('amount', '0')} {overage.get('currency', 'usd')}")
                result["elevenlabs"] = {
                    "status": "ok",
                    "masked_key": mask_secret_tail(elevenlabs_api_key),
                    "characters": {"used": used, "limit": limit, "remaining": remaining},
                    "lines": lines,
                }
            except urllib.error.HTTPError as e:
                detail = f"Subscription API indisponible: HTTP {e.code}."
                if e.code in {401, 403}:
                    detail = "Clé ElevenLabs refusée pour la lecture de subscription."
                result["elevenlabs"] = {
                    "status": "error",
                    "masked_key": mask_secret_tail(elevenlabs_api_key),
                    "lines": [detail],
                }
            except Exception as e:
                result["elevenlabs"] = {
                    "status": "error",
                    "masked_key": mask_secret_tail(elevenlabs_api_key),
                    "lines": [f"Subscription API indisponible: {e}"],
                }

        return result

    def resolve_selected_env_file(selection: str, current_env_file: Path) -> Path:
        selection_path = Path(selection)
        candidate = selection_path if selection_path.is_absolute() else Path.cwd() / selection_path
        try:
            resolved_candidate = candidate.resolve(strict=True)
        except OSError as e:
            raise ValueError(f"env file not found: {selection}") from e
        if not resolved_candidate.is_file():
            raise ValueError(f"env file is not a file: {selection}")
        if not resolved_candidate.name.startswith(".env"):
            raise ValueError("only .env* files can be selected")

        search_dirs = [Path.cwd()]
        if current_env_file.parent not in search_dirs:
            search_dirs.append(current_env_file.parent)
        allowed = {
            path.resolve()
            for search_dir in search_dirs
            for path in search_dir.glob(".env*")
            if path.is_file()
        }
        if current_env_file.exists():
            allowed.add(current_env_file.resolve())
        if resolved_candidate not in allowed:
            raise ValueError("selected env file is outside the available .env profiles")
        return resolved_candidate

    def build_llm_options(env_file: Path, requested_provider: str | None = None) -> dict[str, Any]:
        values = dict(dotenv_values(env_file))
        current_connectivity_mode = connectivity_mode_from_values(values, env_file)
        current_provider = (values.get("LLM_PROVIDER") or "openai").strip().lower()
        provider = (requested_provider or current_provider or "openai").strip().lower()
        current_model = (values.get("OPENAI_MODEL") or "gpt-4o-mini").strip()
        current_cloud_tts_provider = cloud_tts_provider_from_values(values)
        current_tts_output = tts_output_from_values(values)
        current_wake_word = (values.get("WAKE_WORD") or "").strip()
        current_stt_prompt = (values.get("STT_PROMPT") or DEFAULT_STT_PROMPT).strip()
        current_system_prompt = (values.get("ASSISTANT_SYSTEM_PROMPT") or DEFAULT_ASSISTANT_SYSTEM_PROMPT).strip()
        current_session_context_size = env_int_from_values(values, "SESSION_CONTEXT_SIZE", 6000)
        current_mcp_tool_routing_enabled = env_bool_from_values(values, "MCP_TOOL_ROUTING_ENABLED", False)
        current_interrupt_conversation_enabled = env_bool_from_values(values, "INTERRUPT_CONVERSATION_ENABLED", False)
        current_voice_id = (values.get("ELEVENLABS_VOICE_ID") or DEFAULT_ELEVENLABS_VOICE_ID).strip()
        current_thinking_sound_file = (values.get("THINKING_SOUND_FILE") or "thinking.wav").strip()
        current_openai_tts_voice = (values.get("WEB_TTS_VOICE") or DEFAULT_OPENAI_TTS_VOICE).strip()
        current_openai_tts_speed = env_float_from_values(values, "WEB_TTS_SPEED", 1.0)
        current_vad_speech_threshold = env_float_from_values(values, "VAD_SPEECH_THRESHOLD", 0.5)
        current_vad_negative_threshold = env_float_from_values(values, "VAD_NEGATIVE_THRESHOLD", 0.35)
        current_vad_min_speech_ms = env_int_from_values(values, "VAD_MIN_SPEECH_MS", 120)
        current_vad_min_silence_ms = env_int_from_values(values, "VAD_MIN_SILENCE_MS", 650)
        current_vad_speech_pad_ms = env_int_from_values(values, "VAD_SPEECH_PAD_MS", 100)
        current_vad_max_speech_seconds = env_float_from_values(values, "VAD_MAX_SPEECH_SECONDS", 8.0)
        current_backend_audio_input_device = (values.get("BACKEND_AUDIO_INPUT_DEVICE") or "").strip()
        current_backend_audio_output_device = (values.get("BACKEND_AUDIO_OUTPUT_DEVICE") or "").strip()
        internet_online = check_internet_connection()
        backend_audio_devices = list_pyaudio_devices()

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
        message = f"{message} Active env: {env_file}.".strip()

        return {
            "provider": provider,
            "selected_connectivity_mode": current_connectivity_mode,
            "providers": provider_entries,
            "models": models,
            "selected_model": current_model if provider == current_provider else "",
            "cloud_tts_providers": CLOUD_TTS_PROVIDER_OPTIONS,
            "selected_cloud_tts_provider": current_cloud_tts_provider,
            "tts_outputs": TTS_OUTPUT_OPTIONS,
            "selected_tts_output": current_tts_output,
            "selected_wake_word": current_wake_word,
            "selected_stt_prompt": current_stt_prompt,
            "selected_system_prompt": current_system_prompt,
            "selected_session_context_size": current_session_context_size,
            "selected_mcp_tool_routing_enabled": current_mcp_tool_routing_enabled,
            "selected_interrupt_conversation_enabled": current_interrupt_conversation_enabled,
            "voices": list_elevenlabs_voice_options(values),
            "selected_voice_id": current_voice_id,
            "openai_tts_voices": OPENAI_TTS_VOICE_OPTIONS,
            "selected_openai_tts_voice": current_openai_tts_voice,
            "selected_openai_tts_speed": current_openai_tts_speed,
            "selected_vad_speech_threshold": current_vad_speech_threshold,
            "selected_vad_negative_threshold": current_vad_negative_threshold,
            "selected_vad_min_speech_ms": current_vad_min_speech_ms,
            "selected_vad_min_silence_ms": current_vad_min_silence_ms,
            "selected_vad_speech_pad_ms": current_vad_speech_pad_ms,
            "selected_vad_max_speech_seconds": current_vad_max_speech_seconds,
            "backend_audio_inputs": backend_audio_devices["inputs"],
            "backend_audio_outputs": backend_audio_devices["outputs"],
            "selected_backend_audio_input_device": current_backend_audio_input_device,
            "selected_backend_audio_output_device": current_backend_audio_output_device,
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
        connectivity_mode: str,
        wake_word: str,
        stt_prompt: str,
        system_prompt: str,
        session_context_size: int,
        mcp_tool_routing_enabled: bool,
        interrupt_conversation_enabled: bool,
        backend_audio_input_device: str,
        backend_audio_output_device: str,
        voice_id: str,
        thinking_sound_file: str,
        openai_tts_voice: str,
        openai_tts_speed: float,
        vad_speech_threshold: float,
        vad_negative_threshold: float,
        vad_min_speech_ms: int,
        vad_min_silence_ms: int,
        vad_speech_pad_ms: int,
        vad_max_speech_seconds: float,
        web_monitor: WebMonitor | None,
        reload_event: threading.Event | None,
        auto_env_mode: bool = False,
    ) -> dict[str, Any]:
        provider = provider.strip().lower()
        model = model.strip()
        cloud_tts_provider = (cloud_tts_provider or "").strip().lower()
        tts_output = (tts_output or "").strip().lower()
        connectivity_mode = (connectivity_mode or "").strip().lower()
        wake_word = (wake_word or "").strip()
        stt_prompt = (stt_prompt or DEFAULT_STT_PROMPT).strip()
        system_prompt = (system_prompt or "").strip()
        session_context_size = max(0, min(12000, int(session_context_size or 0)))
        mcp_tool_routing_enabled = bool(mcp_tool_routing_enabled)
        interrupt_conversation_enabled = bool(interrupt_conversation_enabled)
        backend_audio_input_device = str(backend_audio_input_device or "").strip()
        backend_audio_output_device = str(backend_audio_output_device or "").strip()
        voice_id = voice_id.strip()
        thinking_sound_file = thinking_sound_file.strip()
        openai_tts_voice = (openai_tts_voice or "").strip()
        openai_tts_speed = max(0.6, min(1.8, float(openai_tts_speed or 1.0)))
        vad_speech_threshold = max(0.05, min(0.95, float(vad_speech_threshold or 0.5)))
        vad_negative_threshold = max(0.01, min(0.95, float(vad_negative_threshold or 0.35)))
        if vad_negative_threshold >= vad_speech_threshold:
            vad_negative_threshold = max(0.01, vad_speech_threshold - 0.15)
        vad_min_speech_ms = max(0, min(2000, int(vad_min_speech_ms or 120)))
        vad_min_silence_ms = max(100, min(5000, int(vad_min_silence_ms or 650)))
        vad_speech_pad_ms = max(0, min(1000, int(vad_speech_pad_ms or 100)))
        vad_max_speech_seconds = max(1.0, min(30.0, float(vad_max_speech_seconds or 8.0)))
        if provider not in {"openai", "ollama"}:
            raise ValueError(f"unsupported LLM provider: {provider}")
        values = dict(dotenv_values(env_file))
        if not connectivity_mode:
            connectivity_mode = connectivity_mode_from_values(values, env_file)
        if connectivity_mode not in {"online", "offline"}:
            raise ValueError(f"unsupported connectivity mode: {connectivity_mode}")
        def env_secret_available(name: str) -> bool:
            file_path = (values.get(f"{name}_FILE") or "").strip()
            if not file_path:
                return False
            try:
                return bool(Path(file_path).read_text().strip())
            except OSError:
                return False

        current_cloud_tts_provider = cloud_tts_provider_from_values(values)
        if connectivity_mode == "offline":
            provider = "ollama"
            cloud_tts_provider = "none"
            tts_output = "backend"
            updated_tts_provider = "pyttsx3"
            updated_web_tts_provider = "none"
            updated_web_audio_enabled = "false"
        else:
            if provider == "ollama":
                raise ValueError("online mode cannot use Ollama; switch to offline mode for local LLM")
        if not cloud_tts_provider:
            cloud_tts_provider = current_cloud_tts_provider
        if cloud_tts_provider not in {"none", "openai", "elevenlabs"}:
            raise ValueError(f"unsupported cloud TTS provider: {cloud_tts_provider}")
        if not tts_output:
            tts_output = tts_output_from_values(dict(dotenv_values(env_file)))
        if tts_output not in {"browser", "backend", "silent"}:
            raise ValueError(f"unsupported TTS output: {tts_output}")
        if connectivity_mode != "offline" and cloud_tts_provider == "none":
            tts_output = "silent"
        if connectivity_mode == "offline":
            pass
        elif cloud_tts_provider in {"openai", "elevenlabs"} and tts_output == "browser":
            updated_tts_provider = "none"
            updated_web_tts_provider = cloud_tts_provider
            updated_web_audio_enabled = "true"
            if not env_secret_available("OPENAI_API_KEY"):
                raise ValueError("browser audio requires OPENAI_API_KEY_FILE for web speech-to-text")
            if cloud_tts_provider == "elevenlabs" and not env_secret_available("ELEVENLABS_API_KEY"):
                raise ValueError("ElevenLabs browser TTS requires ELEVENLABS_API_KEY_FILE")
        elif cloud_tts_provider in {"openai", "elevenlabs"} and tts_output == "backend":
            if auto_env_mode and env_file == AUTO_ENV_OFFLINE:
                raise ValueError(
                    "auto mode is currently using .env.offline, whose backend TTS is local pyttsx3. "
                    "Start with --env-file .env.online or wait for auto mode to switch online before saving cloud backend TTS."
                )
            updated_tts_provider = cloud_tts_provider
            updated_web_tts_provider = "none"
            updated_web_audio_enabled = (values.get("WEB_AUDIO_ENABLED") or "false").strip().lower() or "false"
            if cloud_tts_provider == "openai" and not env_secret_available("OPENAI_API_KEY"):
                raise ValueError("OpenAI backend TTS requires OPENAI_API_KEY_FILE")
            if cloud_tts_provider == "elevenlabs" and not env_secret_available("ELEVENLABS_API_KEY"):
                raise ValueError("ElevenLabs backend TTS requires ELEVENLABS_API_KEY_FILE")
        else:
            updated_tts_provider = "none"
            updated_web_tts_provider = "none"
            updated_web_audio_enabled = (values.get("WEB_AUDIO_ENABLED") or "false").strip().lower() or "false"
        current_provider = (values.get("LLM_PROVIDER") or "openai").strip().lower()
        current_model = (values.get("OPENAI_MODEL") or "gpt-4o-mini").strip()
        if connectivity_mode == "offline" and (
            not model
            or (current_provider != "ollama" and model == current_model)
            or model.startswith(("gpt-", "o1", "o3", "o4"))
        ):
            model = (values.get("OFFLINE_MODEL") or DEFAULT_OLLAMA_MODEL).strip()
        if not model:
            model = current_model
        if not model:
            raise ValueError("LLM model is required")

        llm_changed = provider != current_provider or model != current_model
        if llm_changed and provider == "openai" and not check_internet_connection():
            raise ValueError("OpenAI cannot be selected while internet is offline")
        if llm_changed:
            available_models, reason = (list_openai_models(values) if provider == "openai" else list_ollama_models(values))
            if reason and connectivity_mode != "offline":
                raise ValueError(reason)
            if available_models and model not in {item["id"] for item in available_models} and connectivity_mode == "offline":
                model = available_models[0]["id"]
            elif available_models and model not in {item["id"] for item in available_models}:
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

        backend_audio_devices = list_pyaudio_devices()
        backend_audio_input_ids = {item["id"] for item in backend_audio_devices["inputs"]}
        backend_audio_output_ids = {item["id"] for item in backend_audio_devices["outputs"]}
        if backend_audio_input_device and backend_audio_input_device not in backend_audio_input_ids:
            LOGGER.warning(
                "Backend audio input device %r is not available; clearing saved selection",
                backend_audio_input_device,
            )
            backend_audio_input_device = ""
        if backend_audio_output_device and backend_audio_output_device not in backend_audio_output_ids:
            LOGGER.warning(
                "Backend audio output device %r is not available; clearing saved selection",
                backend_audio_output_device,
            )
            backend_audio_output_device = ""

        update_env_file_values(
            env_file,
            {
                "LLM_PROVIDER": provider,
                "OPENAI_MODEL": model,
                "CONNECTIVITY_MODE": connectivity_mode,
                "STT_PROVIDER": "local-whisper" if connectivity_mode == "offline" else (values.get("STT_PROVIDER") or "openai-whisper").strip().lower(),
                "CLOUD_TTS_PROVIDER": cloud_tts_provider,
                "TTS_PROVIDER": updated_tts_provider,
                "WEB_AUDIO_ENABLED": updated_web_audio_enabled,
                "WEB_STT_PROVIDER": "openai",
                "WEB_STT_MODEL": (values.get("WEB_STT_MODEL") or "whisper-1").strip() or "whisper-1",
                "WEB_TTS_PROVIDER": updated_web_tts_provider,
                "WEB_TTS_MODEL": (values.get("WEB_TTS_MODEL") or DEFAULT_OPENAI_TTS_MODEL).strip() or DEFAULT_OPENAI_TTS_MODEL,
                "VAD_SPEECH_THRESHOLD": f"{vad_speech_threshold:.2f}".rstrip("0").rstrip("."),
                "VAD_NEGATIVE_THRESHOLD": f"{vad_negative_threshold:.2f}".rstrip("0").rstrip("."),
                "VAD_MIN_SPEECH_MS": str(vad_min_speech_ms),
                "VAD_MIN_SILENCE_MS": str(vad_min_silence_ms),
                "VAD_SPEECH_PAD_MS": str(vad_speech_pad_ms),
                "VAD_MAX_SPEECH_SECONDS": f"{vad_max_speech_seconds:.1f}".rstrip("0").rstrip("."),
                "WAKE_WORD": wake_word,
                "STT_PROMPT": stt_prompt,
                "ASSISTANT_SYSTEM_PROMPT": system_prompt,
                "SESSION_CONTEXT_SIZE": str(session_context_size),
                "MCP_TOOL_ROUTING_ENABLED": "true" if mcp_tool_routing_enabled else "false",
                "INTERRUPT_CONVERSATION_ENABLED": "true" if interrupt_conversation_enabled else "false",
                "BACKEND_AUDIO_INPUT_DEVICE": backend_audio_input_device,
                "BACKEND_AUDIO_OUTPUT_DEVICE": backend_audio_output_device,
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
            web_monitor.set_environment_loading(True, "rafraichissement de l'environnement")

        if reload_event:
            reload_event.set()

        return {
            "saved": True,
            "message": (
                "Saved. Browser TTS enabled WEB_AUDIO_ENABLED=true."
                if tts_output == "browser"
                else f"Saved. Backend TTS uses {cloud_tts_provider} in {env_file}."
                if tts_output == "backend" and cloud_tts_provider in {"openai", "elevenlabs"}
                else "Saved."
            ),
            "provider": provider,
            "model": model,
            "connectivity_mode": connectivity_mode,
            "cloud_tts_provider": cloud_tts_provider,
            "tts_output": tts_output,
            "wake_word": wake_word,
            "system_prompt": system_prompt,
            "session_context_size": session_context_size,
            "backend_audio_input_device": backend_audio_input_device,
            "backend_audio_output_device": backend_audio_output_device,
            "voice_id": voice_id,
            "thinking_sound_file": thinking_sound_file,
            "openai_tts_voice": openai_tts_voice,
            "openai_tts_speed": openai_tts_speed,
            "vad_speech_threshold": vad_speech_threshold,
            "vad_negative_threshold": vad_negative_threshold,
            "vad_min_speech_ms": vad_min_speech_ms,
            "vad_min_silence_ms": vad_min_silence_ms,
            "vad_speech_pad_ms": vad_speech_pad_ms,
            "vad_max_speech_seconds": vad_max_speech_seconds,
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
        backend_audio_input_device = os.getenv("BACKEND_AUDIO_INPUT_DEVICE", "").strip()
        backend_audio_output_device = os.getenv("BACKEND_AUDIO_OUTPUT_DEVICE", "").strip()
        vad_model_path = os.getenv("VAD_MODEL_PATH", str(DEFAULT_SILERO_VAD_MODEL)).strip() or str(DEFAULT_SILERO_VAD_MODEL)
        vad_speech_threshold = env_float("VAD_SPEECH_THRESHOLD", 0.5)
        vad_negative_threshold = env_float("VAD_NEGATIVE_THRESHOLD", 0.35)
        vad_min_speech_ms = env_int("VAD_MIN_SPEECH_MS", 120)
        vad_min_silence_ms = env_int("VAD_MIN_SILENCE_MS", 650)
        vad_speech_pad_ms = env_int("VAD_SPEECH_PAD_MS", 100)
        vad_max_speech_seconds = env_float("VAD_MAX_SPEECH_SECONDS", 8.0)
        voice_cancel_during_thinking = env_bool("VOICE_CANCEL_DURING_THINKING", False)
        interrupt_conversation_enabled = env_bool("INTERRUPT_CONVERSATION_ENABLED", False)
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
        wake_words = parse_wake_words(env_optional("WAKE_WORD"))
        system_prompt = env_optional("ASSISTANT_SYSTEM_PROMPT")
        mcp_config_path = env_optional("MCP_CONFIG")
        mcp_prompt_merge_mode = os.getenv("MCP_PROMPT_MERGE_MODE", "append").lower()
        mcp_agent_memory_enabled = env_bool("MCP_AGENT_MEMORY_ENABLED", True)
        mcp_agent_timeout_seconds = env_float(
            "MCP_AGENT_TIMEOUT_SECONDS",
            DEFAULT_MCP_AGENT_TIMEOUT_SECONDS,
        )
        mcp_tool_routing_enabled = env_bool("MCP_TOOL_ROUTING_ENABLED", False)
        session_context_dir = os.getenv("SESSION_CONTEXT_DIR", str(DEFAULT_CONTEXT_DIR)).strip() or str(DEFAULT_CONTEXT_DIR)
        session_context_size = max(0, min(12000, env_int("SESSION_CONTEXT_SIZE", 6000)))

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
        print(f"Using backend audio input: {backend_audio_input_device or 'default'}")
        print(f"Using backend audio output: {backend_audio_output_device or 'default'}")
        if voice_cancel_during_thinking:
            print("Using voice cancel during thinking: enabled")
        if interrupt_conversation_enabled:
            print("Using interrupt conversation mode: enabled")
        if web_audio_enabled:
            print(f"Using web audio: STT={web_stt_provider}, TTS={web_tts_provider}")
        print(f"Using wake word: {', '.join(wake_words) if wake_words else 'disabled'}")
        print(f"Using MCP agent memory: {mcp_agent_memory_enabled}")
        print(f"Using MCP tool routing: {'enabled' if mcp_tool_routing_enabled else 'disabled'}")
        print(f"Using session context size: {session_context_size} ({session_context_dir})")

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
            "vad_model_url": "/static/vendor/silero-vad/silero_vad_v6.onnx",
            "vad_ort_url": "/static/vendor/onnxruntime-web/ort.wasm.min.mjs",
            "vad_ort_wasm_path": "/static/vendor/onnxruntime-web/",
            "vad_speech_threshold": vad_speech_threshold,
            "vad_negative_threshold": vad_negative_threshold,
            "vad_min_speech_ms": vad_min_speech_ms,
            "vad_min_silence_ms": vad_min_silence_ms,
            "vad_speech_pad_ms": vad_speech_pad_ms,
            "vad_max_speech_seconds": vad_max_speech_seconds,
            "interrupt_conversation_enabled": interrupt_conversation_enabled,
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

        session_context_store = SessionContextStore(
            session_context_dir,
            summary_max_chars=DEFAULT_SUMMARY_MAX_CHARS,
        )

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
                remote_screen={"vnc_url": remote_screen_url_from_values(env_values)},
                thinking_sound_file=thinking_sound_file,
            )
            web_monitor.replace_dialogue(session_context_store.snapshot().get("messages") or [])
            web_monitor.set_context_state(
                session_context_store.snapshot(),
                session_context_size=session_context_size,
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
            backend_audio_input_device=backend_audio_input_device,
            backend_audio_output_device=backend_audio_output_device,
            vad_model_path=vad_model_path,
            vad_speech_threshold=vad_speech_threshold,
            vad_negative_threshold=vad_negative_threshold,
            vad_min_speech_ms=vad_min_speech_ms,
            vad_min_silence_ms=vad_min_silence_ms,
            vad_speech_pad_ms=vad_speech_pad_ms,
            vad_max_speech_seconds=vad_max_speech_seconds,
            tts_speed=web_tts_speed,
            wake_words=wake_words,
            mcp_config=mcp_config,
            mcp_load_server_prompt=env_bool("MCP_LOAD_SERVER_PROMPT", False),
            mcp_prompt_merge_mode=mcp_prompt_merge_mode,
            mcp_agent_memory_enabled=mcp_agent_memory_enabled,
            mcp_agent_timeout_seconds=mcp_agent_timeout_seconds,
            mcp_tool_routing_enabled=mcp_tool_routing_enabled,
            session_context_store=session_context_store,
            session_context_size=session_context_size,
            voice_cancel_during_thinking=voice_cancel_during_thinking,
            interrupt_conversation_enabled=interrupt_conversation_enabled,
            system_prompt=system_prompt,
            reload_event=reload_event,
            web_monitor=web_monitor,
        )

        if web_monitor:
            def session_context_response() -> dict[str, Any]:
                snapshot = session_context_store.snapshot()
                web_monitor.replace_dialogue(snapshot.get("messages") or [])
                web_monitor.set_context_state(
                    snapshot,
                    session_context_size=assistant.session_context_size,
                )
                return snapshot

            def select_session_context(session_id: str) -> dict[str, Any]:
                session_context_store.select_session(session_id)
                assistant.refresh_session_llm_summary_blocking()
                if assistant.agent:
                    assistant.agent.clear_conversation_history()
                return session_context_response()

            def new_session_context(title: str | None = None) -> dict[str, Any]:
                session_context_store.new_session(title)
                assistant.refresh_session_llm_summary_blocking()
                if assistant.agent:
                    assistant.agent.clear_conversation_history()
                return session_context_response()

            def rename_session_context(session_id: str, title: str) -> dict[str, Any]:
                session_context_store.rename_session(session_id, title)
                return session_context_response()

            def clear_session_context(session_id: str) -> dict[str, Any]:
                was_active = session_context_store.active_id == session_id
                session_context_store.clear_session_conversation(session_id, preserve_llm_summary=True)
                if was_active and assistant.agent:
                    assistant.agent.clear_conversation_history()
                return session_context_response()

            def save_session_context(session_id: str) -> dict[str, Any]:
                session_context_store.select_session(session_id)
                refreshed = assistant.refresh_session_llm_summary_blocking(force=True)
                if assistant.agent:
                    assistant.agent.clear_conversation_history()
                snapshot = session_context_response()
                snapshot["llm_summary_refreshed"] = refreshed
                return snapshot

            def delete_session_context(session_id: str) -> dict[str, Any]:
                was_active = session_context_store.active_id == session_id
                session_context_store.delete_session(session_id)
                if was_active and assistant.agent:
                    assistant.agent.clear_conversation_history()
                return session_context_response()

            web_monitor.set_session_context_handlers(
                list_handler=session_context_response,
                new_handler=new_session_context,
                select_handler=select_session_context,
                rename_handler=rename_session_context,
                clear_handler=clear_session_context,
                save_handler=save_session_context,
                delete_handler=delete_session_context,
            )

            if openai_api_key and assistant.openai_client is None:
                assistant.openai_client = openai.OpenAI(api_key=openai_api_key)
            if elevenlabs_api_key and assistant.elevenlabs_client is None:
                assistant.elevenlabs_client = ElevenLabs(api_key=elevenlabs_api_key)

            web_tts_handler = None
            if openai_api_key or elevenlabs_api_key:
                def web_tts_handler(
                    text: str,
                    options: dict[str, Any] | None = None,
                    active_assistant: VoiceAssistant = assistant,
                ) -> dict[str, Any]:
                    requested = options or {}
                    requested_provider = str(requested.get("provider") or "").strip().lower()
                    configured_provider = (
                        web_tts_provider if web_tts_provider in {"openai", "elevenlabs"} else cloud_tts_provider
                    )
                    provider = requested_provider or configured_provider
                    speed = max(0.6, min(1.8, float(requested.get("speed") or web_tts_speed or 1.0)))

                    if provider == "openai":
                        if active_assistant.openai_client is None:
                            raise ValueError("OpenAI client is not configured")
                        return active_assistant.web_text_to_speech_openai(
                            text,
                            model=str(requested.get("model") or web_tts_model or DEFAULT_OPENAI_TTS_MODEL),
                            voice=str(requested.get("voice") or web_tts_voice or DEFAULT_OPENAI_TTS_VOICE),
                            speed=speed,
                        )
                    if provider == "elevenlabs":
                        if active_assistant.elevenlabs_client is None:
                            raise ValueError("ElevenLabs client is not configured")
                        return active_assistant.web_text_to_speech_elevenlabs(
                            text,
                            voice_id=str(requested.get("voice") or active_assistant.elevenlabs_voice_id),
                            speed=speed,
                        )
                    raise ValueError("Web audio TTS is not available")

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
            web_monitor.set_cancel_handler(assistant.stop_tts)

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
    env_file_lock = threading.RLock()

    def get_active_env_file() -> Path:
        with env_file_lock:
            return env_file

    def switch_active_env_file(selection: str) -> dict[str, Any]:
        nonlocal env_file
        if auto_env_mode:
            raise ValueError("manual env switching is disabled while --env-file auto controls the active profile")
        with env_file_lock:
            previous_env_file = env_file
            selected_env_file = resolve_selected_env_file(selection, previous_env_file)
            if selected_env_file.resolve() == previous_env_file.resolve():
                return {
                    "switched": False,
                    "env_file": display_env_path(env_file),
                    "message": f"Already using {display_env_path(env_file)}.",
                }
            env_file = selected_env_file
            values = dict(dotenv_values(env_file))
            mcp_config = load_mcp_config_from_values(values)
            if web_monitor:
                web_monitor.set_environment_loading(True, "rafraichissement de l'environnement")
                web_monitor.update(
                    env_file=env_file,
                    mode="manual",
                    env_values=values,
                    mcp_config=mcp_config or {},
                    services=build_service_state(
                        llm_provider=(values.get("LLM_PROVIDER") or "openai").strip().lower(),
                        model=(values.get("OPENAI_MODEL") or "gpt-4o-mini").strip(),
                        stt_provider=(values.get("STT_PROVIDER") or "openai-whisper").strip().lower(),
                        tts_provider=(values.get("TTS_PROVIDER") or "elevenlabs").strip().lower(),
                        mcp_config=mcp_config,
                    ),
                )
            reload_event.set()
            return {
                "switched": True,
                "env_file": display_env_path(env_file),
                "message": f"Switching to {display_env_path(env_file)}.",
            }

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
        web_monitor.set_env_profile_handlers(
            list_handler=lambda: list_available_env_files(get_active_env_file(), auto_env_mode),
            switch_handler=switch_active_env_file,
        )
        web_monitor.set_remote_screen_handler(
            lambda vnc_url: save_remote_screen_config(get_active_env_file(), vnc_url, web_monitor)
        )
        web_monitor.set_cloud_api_status_handler(lambda: build_cloud_api_status(get_active_env_file()))
        web_monitor.set_llm_config_handlers(
            options_handler=lambda provider=None: build_llm_options(get_active_env_file(), provider),
            save_handler=lambda provider, model, cloud_tts_provider, tts_output, connectivity_mode, wake_word, stt_prompt, system_prompt, session_context_size, mcp_tool_routing_enabled, interrupt_conversation_enabled, backend_audio_input_device, backend_audio_output_device, voice_id, thinking_sound_file, openai_tts_voice, openai_tts_speed, vad_speech_threshold, vad_negative_threshold, vad_min_speech_ms, vad_min_silence_ms, vad_speech_pad_ms, vad_max_speech_seconds: save_llm_config(
                get_active_env_file(),
                provider,
                model,
                cloud_tts_provider,
                tts_output,
                connectivity_mode,
                wake_word,
                stt_prompt,
                system_prompt,
                session_context_size,
                mcp_tool_routing_enabled,
                interrupt_conversation_enabled,
                backend_audio_input_device,
                backend_audio_output_device,
                voice_id,
                thinking_sound_file,
                openai_tts_voice,
                openai_tts_speed,
                vad_speech_threshold,
                vad_negative_threshold,
                vad_min_speech_ms,
                vad_min_silence_ms,
                vad_speech_pad_ms,
                vad_max_speech_seconds,
                web_monitor,
                reload_event,
                auto_env_mode,
            ),
        )

    if auto_selection_message:
        print(auto_selection_message)

    if not auto_env_mode:
        try:
            announce_reload_complete = False
            while True:
                reload_event.clear()
                active_env_file = get_active_env_file()
                assistant = build_assistant_from_env(active_env_file, reload_event=reload_event, web_monitor=web_monitor)
                if announce_reload_complete:
                    await assistant.text_to_speech("Configuration mise à jour. L'assistant redémarre avec le nouveau modèle.")
                    announce_reload_complete = False
                if web_monitor:
                    web_monitor.set_environment_loading(False)

                run_result = await assistant.run()
                if run_result != "reload":
                    break

                print(f"Configuration reload requested. Restarting assistant with {get_active_env_file()}.")
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
            detected_env_file = auto_monitor.detected_env_file
            with env_file_lock:
                env_file = detected_env_file
            if not detected_env_file.exists():
                print(f"Error: env file not found: {detected_env_file}")
                sys.exit(1)

            reload_event.clear()
            assistant = build_assistant_from_env(detected_env_file, reload_event=reload_event, web_monitor=web_monitor)
            if announce_reload_complete:
                await assistant.text_to_speech("Environnement mis à jour. La demande en cours a été annulée.")
                announce_reload_complete = False
            if web_monitor:
                web_monitor.set_environment_loading(False)

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
