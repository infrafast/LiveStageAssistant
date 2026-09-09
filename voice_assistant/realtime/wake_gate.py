"""Provider-neutral local wake gate for realtime voice engines.

The gate consumes 24 kHz mono PCM16 from the common realtime capture path,
resamples it to openWakeWord's 16 kHz input rate and authorizes cloud audio
only after a local wake detection. It deliberately contains no OpenAI- or
MCP-specific logic.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import time
from typing import Any, Callable, Mapping

import numpy as np

from ..wake_word import get_configured_wake_words
from .audio import Pcm16MonoResampler

OWW_RATE = 16000
OWW_FRAME_SAMPLES = 1280
OWW_FRAME_BYTES = OWW_FRAME_SAMPLES * 2
REALTIME_RATE = 24000
PCM16_BYTES_PER_SAMPLE = 2
DEFAULT_POST_TTS_SUPPRESSION_MS = 350
DEFAULT_PRE_ROLL_MS = 1600


def _float(values: Mapping[str, object], key: str, default: float) -> float:
    try:
        return float(values.get(key, default) or default)
    except (TypeError, ValueError):
        return default


def _int(values: Mapping[str, object], key: str, default: int) -> int:
    try:
        return int(float(values.get(key, default) or default))
    except (TypeError, ValueError):
        return default


def _csv(value: object) -> tuple[str, ...]:
    result: list[str] = []
    for item in str(value or "").replace(";", ",").split(","):
        item = item.strip()
        if item and item not in result:
            result.append(item)
    return tuple(result)


def _max_score(value: Any) -> float:
    """Return the largest numeric score from an openWakeWord prediction."""
    if isinstance(value, Mapping):
        scores = [_max_score(item) for item in value.values()]
        return max(scores, default=0.0)
    if isinstance(value, (list, tuple, np.ndarray)):
        try:
            return float(np.max(value)) if len(value) else 0.0
        except (TypeError, ValueError):
            return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _best_label_score(prediction: Any) -> tuple[str, float]:
    if not isinstance(prediction, Mapping) or not prediction:
        return "configured model", _max_score(prediction)
    best_label = "configured model"
    best_score = 0.0
    for label, value in prediction.items():
        score = _max_score(value)
        if score >= best_score:
            best_label = str(label)
            best_score = score
    return best_label, best_score


@dataclass(frozen=True)
class RealtimeWakeConfig:
    wake_word: str = ""
    model_paths: tuple[str, ...] = ()
    model_names: tuple[str, ...] = ()
    threshold: float = 0.5
    pre_roll_ms: int = DEFAULT_PRE_ROLL_MS
    cooldown_ms: int = 1200
    post_tts_suppression_ms: int = DEFAULT_POST_TTS_SUPPRESSION_MS

    @property
    def enabled(self) -> bool:
        return bool(self.wake_word.strip())

    @classmethod
    def from_env(cls, values: Mapping[str, object] | None = None) -> "RealtimeWakeConfig":
        env: Mapping[str, object] = values if values is not None else os.environ
        wake_words = get_configured_wake_words(env)
        return cls(
            wake_word=wake_words[0] if wake_words else "",
            model_paths=_csv(env.get("BACKEND_WAKE_WORD_MODEL_PATHS")),
            model_names=_csv(env.get("BACKEND_WAKE_WORD_MODEL_NAMES")),
            threshold=max(0.01, min(0.99, _float(env, "BACKEND_WAKE_WORD_THRESHOLD", 0.5))),
            pre_roll_ms=max(0, min(5000, _int(env, "BACKEND_WAKE_WORD_PRE_ROLL_MS", DEFAULT_PRE_ROLL_MS))),
            cooldown_ms=max(0, min(10000, _int(env, "BACKEND_WAKE_WORD_COOLDOWN_MS", 1200))),
            post_tts_suppression_ms=max(
                0,
                min(5000, _int(env, "WAKE_WORD_POST_TTS_SUPPRESSION_MS", DEFAULT_POST_TTS_SUPPRESSION_MS)),
            ),
        )


class RealtimeWakeGate:
    """Local authorization gate placed before realtime provider audio upload."""

    def __init__(
        self,
        config: RealtimeWakeConfig,
        *,
        predictor: Callable[[np.ndarray], Any] | None = None,
        reset_predictor: Callable[[], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self._clock = clock
        self._resampler = Pcm16MonoResampler(REALTIME_RATE, OWW_RATE)
        self._buffer = bytearray()
        self._pre_roll = bytearray()
        self._pre_roll_limit = int(REALTIME_RATE * PCM16_BYTES_PER_SAMPLE * (config.pre_roll_ms / 1000.0))
        self._last_detection = -1e9
        self._last_detection_label = ""
        self._last_detection_score = 0.0
        self._not_before = -1e9
        self._waiting = config.enabled
        self._predictor = predictor
        self._reset_predictor = reset_predictor
        self._model = None
        if config.enabled and self._predictor is None:
            self._load_openwakeword()

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    @property
    def waiting(self) -> bool:
        return self.enabled and self._waiting

    @property
    def authorized(self) -> bool:
        return not self.waiting

    @property
    def last_detection_label(self) -> str:
        return self._last_detection_label

    @property
    def last_detection_score(self) -> float:
        return self._last_detection_score

    def _load_openwakeword(self) -> None:
        try:
            from openwakeword.model import Model
        except Exception as error:  # pragma: no cover - hardware dependency
            raise RuntimeError(f"openWakeWord unavailable for realtime wake mode: {error}") from error

        models: list[str] = []
        for raw in (*self.config.model_paths, *self.config.model_names):
            path = Path(os.path.expandvars(raw)).expanduser()
            models.append(str(path) if path.exists() else raw)
        if not models:
            raise RuntimeError(
                "WAKE_WORD is enabled but no BACKEND_WAKE_WORD_MODEL_PATHS or "
                "BACKEND_WAKE_WORD_MODEL_NAMES are configured"
            )
        self._model = Model(wakeword_models=models, inference_framework="onnx")
        self._predictor = self._model.predict
        reset = getattr(self._model, "reset", None)
        self._reset_predictor = reset if callable(reset) else None

    def _remember_pre_roll(self, pcm24k: bytes) -> None:
        if self._pre_roll_limit <= 0 or not pcm24k:
            return
        self._pre_roll.extend(pcm24k)
        overflow = len(self._pre_roll) - self._pre_roll_limit
        if overflow > 0:
            del self._pre_roll[:overflow]

    def consume_pre_roll(self) -> bytes:
        """Return and clear the retained 24 kHz PCM preceding wake authorization."""
        payload = bytes(self._pre_roll)
        self._pre_roll.clear()
        return payload

    def rearm(self, *, suppress_ms: int | None = None) -> None:
        if not self.enabled:
            return
        self._waiting = True
        delay_ms = self.config.post_tts_suppression_ms if suppress_ms is None else max(0, int(suppress_ms))
        self._not_before = self._clock() + (delay_ms / 1000.0)
        self._buffer.clear()
        self._pre_roll.clear()
        self._resampler = Pcm16MonoResampler(REALTIME_RATE, OWW_RATE)
        if self._reset_predictor is not None:
            try:
                self._reset_predictor()
            except Exception:
                pass

    def authorize(self) -> None:
        self._waiting = False
        self._buffer.clear()

    def feed(self, pcm24k: bytes) -> bool:
        """Feed capture audio while waiting; return True exactly on detection."""
        if not self.waiting or not pcm24k:
            return False
        if self._clock() < self._not_before:
            return False

        self._remember_pre_roll(pcm24k)
        converted = self._resampler.process(pcm24k)
        if converted:
            self._buffer.extend(converted)

        while len(self._buffer) >= OWW_FRAME_BYTES:
            frame = bytes(self._buffer[:OWW_FRAME_BYTES])
            del self._buffer[:OWW_FRAME_BYTES]
            samples = np.frombuffer(frame, dtype=np.int16)
            prediction = self._predictor(samples) if self._predictor is not None else {}
            label, score = _best_label_score(prediction)
            now = self._clock()
            cooldown = self.config.cooldown_ms / 1000.0
            if score >= self.config.threshold and now - self._last_detection >= cooldown:
                self._last_detection = now
                self._last_detection_label = label
                self._last_detection_score = score
                self.authorize()
                return True
        return False
