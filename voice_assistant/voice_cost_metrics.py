"""RV0 helpers for measuring classic cloud usage without changing control flow."""

from __future__ import annotations

from dataclasses import dataclass
import io
import json
import shutil
import subprocess
import tempfile
import threading
import wave

from langchain_core.callbacks import BaseCallbackHandler

VOICE_COST_PREFIX = "VOICE_COST "


@dataclass(frozen=True)
class TokenSnapshot:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0

    def __sub__(self, other: "TokenSnapshot") -> "TokenSnapshot":
        return TokenSnapshot(
            max(0, self.input_tokens - other.input_tokens),
            max(0, self.cached_input_tokens - other.cached_input_tokens),
            max(0, self.output_tokens - other.output_tokens),
        )


class OpenAIUsageCollector(BaseCallbackHandler):
    """Thread-safe cumulative LangChain/OpenAI token collector."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tokens = TokenSnapshot()

    def snapshot(self) -> TokenSnapshot:
        with self._lock:
            return self._tokens

    def on_llm_end(self, response, **kwargs) -> None:  # type: ignore[override]
        usage = dict((getattr(response, "llm_output", None) or {}).get("token_usage") or {})
        if usage:
            input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
            output_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
            details = usage.get("prompt_tokens_details") or usage.get("input_token_details") or {}
            cached = int(details.get("cached_tokens") or details.get("cache_read") or 0)
        else:
            input_tokens = output_tokens = cached = 0
            for generation_list in getattr(response, "generations", []) or []:
                for generation in generation_list or []:
                    metadata = getattr(getattr(generation, "message", None), "usage_metadata", None) or {}
                    input_tokens += int(metadata.get("input_tokens") or 0)
                    output_tokens += int(metadata.get("output_tokens") or 0)
                    details = metadata.get("input_token_details") or {}
                    cached += int(details.get("cache_read") or details.get("cached_tokens") or 0)
        if input_tokens or output_tokens or cached:
            with self._lock:
                current = self._tokens
                self._tokens = TokenSnapshot(
                    current.input_tokens + input_tokens,
                    current.cached_input_tokens + cached,
                    current.output_tokens + output_tokens,
                )


RV0_COST_COLLECTOR = OpenAIUsageCollector()


def wav_duration_seconds(data: bytes) -> float | None:
    try:
        with wave.open(io.BytesIO(data), "rb") as wav:
            rate = wav.getframerate()
            return wav.getnframes() / float(rate) if rate else None
    except Exception:
        return None


def mp3_duration_seconds(data: bytes) -> float | None:
    """Return encoded MP3 duration using ffprobe when available."""
    if not data or not shutil.which("ffprobe"):
        return None
    path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as handle:
            handle.write(data)
            path = handle.name
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", path],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        value = float(result.stdout.strip())
        return value if value > 0 else None
    except Exception:
        return None
    finally:
        if path:
            try:
                import os
                os.unlink(path)
            except OSError:
                pass


def cost_log(stage: str, **values) -> str:
    return VOICE_COST_PREFIX + json.dumps({"schema": 1, "stage": stage, **values}, separators=(",", ":"), sort_keys=True)


def parse_cost_line(line: str):
    marker = line.find(VOICE_COST_PREFIX)
    if marker < 0:
        return None
    try:
        value = json.loads(line[marker + len(VOICE_COST_PREFIX):].strip())
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None
