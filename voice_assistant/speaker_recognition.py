from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
import re
import tempfile
import wave
from typing import Any

import numpy as np


UNKNOWN_SPEAKER = "unknown"
DEFAULT_SPEAKER_PROFILES_DIR = Path("data/speaker_profiles")
MAX_SPEAKER_PROFILE_WAV_BYTES = 10 * 1024 * 1024
SPEAKER_EMBEDDING_SUFFIX = ".npy"
SPEAKER_EMBEDDING_PREPARATION_MESSAGE = (
    "Je prépare l'empreinte vocale du profil. Cela peut prendre un moment la première fois."
)
SPEAKER_EMBEDDING_READY_MESSAGE = "Empreinte vocale calculée."


@dataclass
class SpeakerEmbeddingBatchResult:
    computed: list[Path]
    failed: list[tuple[Path, str]]

    @property
    def computed_count(self) -> int:
        return len(self.computed)

    @property
    def failed_count(self) -> int:
        return len(self.failed)


@dataclass
class SpeakerProfile:
    name: str
    wav_paths: list[Path]
    enabled: bool = True
    slug: str | None = None


@dataclass
class SpeakerRecognitionResult:
    speaker: str = UNKNOWN_SPEAKER
    confidence: float = 0.0
    backend: str = "none"
    second_confidence: float = 0.0
    reason: str = "disabled"
    candidates: list[tuple[str, float]] | None = None


class SpeakerRecognizerBase(ABC):
    backend_name = "base"

    def __init__(self, profiles: list[SpeakerProfile], threshold: float = 0.75, margin: float = 0.10) -> None:
        self.profiles = [profile for profile in profiles if profile.enabled and profile.wav_paths]
        self.threshold = float(threshold)
        self.margin = float(margin)

    @abstractmethod
    def recognize_wav_bytes(self, audio_data: bytes) -> SpeakerRecognitionResult:
        raise NotImplementedError

    def validate_runtime(self) -> None:
        """Raise when optional backend dependencies are unavailable."""
        return None

    def pending_embedding_paths(self) -> list[Path]:
        return []


class ResemblyzerSpeakerRecognizer(SpeakerRecognizerBase):
    backend_name = "resemblyzer"

    def __init__(self, profiles: list[SpeakerProfile], threshold: float = 0.75, margin: float = 0.10) -> None:
        super().__init__(profiles, threshold=threshold, margin=margin)
        self._encoder: Any | None = None
        self._profile_embeddings: list[tuple[SpeakerProfile, list[np.ndarray]]] | None = None

    def _load_resemblyzer(self) -> tuple[Any, Any]:
        try:
            from resemblyzer import VoiceEncoder, preprocess_wav
        except Exception as exc:  # pragma: no cover - depends on optional package
            raise RuntimeError(f"Resemblyzer is not installed or could not be imported: {exc}") from exc
        return VoiceEncoder, preprocess_wav

    def validate_runtime(self) -> None:
        try:
            import platformdirs
        except Exception as exc:  # pragma: no cover - depends on optional package
            raise RuntimeError("platformdirs is required by Resemblyzer but is not installed") from exc
        if not callable(getattr(platformdirs, "user_cache_dir", None)):
            location = getattr(platformdirs, "__file__", "unknown location")
            raise RuntimeError(
                "platformdirs is installed but platformdirs.user_cache_dir is unavailable; "
                f"force-reinstall platformdirs inside the assistant venv ({location})"
            )
        try:
            from scipy.special import loggamma as _loggamma  # noqa: F401
        except Exception as exc:  # pragma: no cover - depends on optional package
            raise RuntimeError(
                "SciPy is installed but scipy.special.loggamma is unavailable; "
                "force-reinstall a compatible SciPy wheel inside the assistant venv"
            ) from exc
        self._load_resemblyzer()

    def _encoder_instance(self) -> Any:
        if self._encoder is None:
            VoiceEncoder, _ = self._load_resemblyzer()
            self._encoder = VoiceEncoder()
        return self._encoder

    def _embedding_for_path(self, path: Path) -> np.ndarray:
        _, preprocess_wav = self._load_resemblyzer()
        wav = preprocess_wav(path)
        return self._encoder_instance().embed_utterance(wav)

    def _embedding_cache_path(self, wav_path: Path) -> Path:
        return wav_path.with_suffix(SPEAKER_EMBEDDING_SUFFIX)

    def _load_cached_embedding(self, wav_path: Path) -> np.ndarray | None:
        cache_path = self._embedding_cache_path(wav_path)
        if not cache_path.exists() or not cache_path.is_file():
            return None
        try:
            if cache_path.stat().st_mtime < wav_path.stat().st_mtime:
                return None
            embedding = np.load(cache_path)
            if embedding.ndim != 1:
                return None
            return embedding.astype(np.float32)
        except Exception:
            return None

    def pending_embedding_paths(self) -> list[Path]:
        pending: list[Path] = []
        for profile in self.profiles:
            for wav_path in profile.wav_paths:
                if wav_path.exists() and wav_path.is_file() and self._load_cached_embedding(wav_path) is None:
                    pending.append(wav_path)
        return pending

    def _embedding_for_profile_path(self, wav_path: Path) -> np.ndarray:
        cached = self._load_cached_embedding(wav_path)
        if cached is not None:
            return cached
        embedding = self._embedding_for_path(wav_path)
        try:
            np.save(self._embedding_cache_path(wav_path), embedding)
        except OSError:
            pass
        return embedding

    def _load_profile_embeddings(self) -> list[tuple[SpeakerProfile, list[np.ndarray]]]:
        if self._profile_embeddings:
            return self._profile_embeddings

        embeddings: list[tuple[SpeakerProfile, list[np.ndarray]]] = []
        for profile in self.profiles:
            profile_vectors = []
            for wav_path in profile.wav_paths:
                if wav_path.exists() and wav_path.is_file():
                    profile_vectors.append(self._embedding_for_profile_path(wav_path))
            if profile_vectors:
                embeddings.append((profile, profile_vectors))
        self._profile_embeddings = embeddings
        return embeddings

    def recognize_wav_bytes(self, audio_data: bytes) -> SpeakerRecognitionResult:
        profiles = self._load_profile_embeddings()
        if not profiles:
            return SpeakerRecognitionResult(backend=self.backend_name, reason="no_profiles")

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as wav_file:
            wav_file.write(audio_data)
            wav_file.flush()
            embedding = self._embedding_for_path(Path(wav_file.name))

        scored = sorted(
            (
                (profile, max(float(np.inner(embedding, profile_embedding)) for profile_embedding in profile_embeddings))
                for profile, profile_embeddings in profiles
                if profile_embeddings
            ),
            key=lambda item: item[1],
            reverse=True,
        )
        if not scored:
            return SpeakerRecognitionResult(backend=self.backend_name, reason="no_profiles")
        best_profile, best_score = scored[0]
        second_score = scored[1][1] if len(scored) > 1 else 0.0
        candidates = [(profile.name, score) for profile, score in scored]
        if best_score >= self.threshold and best_score - second_score >= self.margin:
            return SpeakerRecognitionResult(
                speaker=best_profile.name,
                confidence=best_score,
                second_confidence=second_score,
                backend=self.backend_name,
                reason="matched",
                candidates=candidates,
            )
        return SpeakerRecognitionResult(
            speaker=UNKNOWN_SPEAKER,
            confidence=best_score,
            second_confidence=second_score,
            backend=self.backend_name,
            reason="below_threshold_or_margin",
            candidates=candidates,
        )


class SpeechBrainSpeakerRecognizer(SpeakerRecognizerBase):
    backend_name = "speechbrain"

    def recognize_wav_bytes(self, audio_data: bytes) -> SpeakerRecognitionResult:
        return SpeakerRecognitionResult(backend=self.backend_name, reason="backend_not_implemented")


def safe_speaker_profile_slug(name: str) -> str:
    normalized = (name or "").strip().lower()
    normalized = re.sub(r"[^a-z0-9._-]+", "-", normalized)
    normalized = re.sub(r"-+", "-", normalized).strip("-._")
    return normalized[:64] or "speaker"


def validate_wav_bytes(audio_data: bytes, *, max_bytes: int = MAX_SPEAKER_PROFILE_WAV_BYTES) -> None:
    if not audio_data:
        raise ValueError("empty WAV file")
    if len(audio_data) > max_bytes:
        raise ValueError(f"WAV file is too large; max is {max_bytes} bytes")
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as wav_file:
        wav_file.write(audio_data)
        wav_file.flush()
        try:
            with wave.open(wav_file.name, "rb") as reader:
                if reader.getnframes() <= 0:
                    raise ValueError("WAV file contains no audio frames")
        except wave.Error as exc:
            raise ValueError(f"invalid WAV file: {exc}") from exc


def compute_resemblyzer_embedding_file(wav_path: str | Path, embedding_path: str | Path | None = None) -> Path:
    """Compute and persist a Resemblyzer embedding for a profile WAV file."""
    wav_path = Path(wav_path)
    if embedding_path is None:
        embedding_path = wav_path.with_suffix(SPEAKER_EMBEDDING_SUFFIX)
    embedding_path = Path(embedding_path)
    recognizer = ResemblyzerSpeakerRecognizer([])
    embedding = recognizer._embedding_for_path(wav_path)
    embedding_path.parent.mkdir(parents=True, exist_ok=True)
    np.save(embedding_path, embedding)
    return embedding_path


def resemblyzer_embedding_is_current(wav_path: str | Path, embedding_path: str | Path | None = None) -> bool:
    wav_path = Path(wav_path)
    if embedding_path is None:
        embedding_path = wav_path.with_suffix(SPEAKER_EMBEDDING_SUFFIX)
    embedding_path = Path(embedding_path)
    try:
        return (
            wav_path.exists()
            and wav_path.is_file()
            and embedding_path.exists()
            and embedding_path.is_file()
            and embedding_path.stat().st_mtime >= wav_path.stat().st_mtime
        )
    except OSError:
        return False


def conventional_speaker_sample_paths(profile_root: str | Path, *, max_profiles: int = 5, samples_per_profile: int = 3) -> list[Path]:
    root = Path(profile_root)
    max_profiles = max(0, int(max_profiles or 0))
    samples_per_profile = max(0, int(samples_per_profile or 0))
    return [
        root / f"profil{profile_index}_{sample_index}.wav"
        for profile_index in range(1, max_profiles + 1)
        for sample_index in range(1, samples_per_profile + 1)
    ]


def compute_missing_resemblyzer_embeddings(wav_paths: list[str | Path]) -> SpeakerEmbeddingBatchResult:
    computed: list[Path] = []
    failed: list[tuple[Path, str]] = []
    seen: set[Path] = set()
    for raw_wav_path in wav_paths:
        wav_path = Path(raw_wav_path)
        if wav_path in seen:
            continue
        seen.add(wav_path)
        if not wav_path.exists() or not wav_path.is_file():
            continue
        embedding_path = wav_path.with_suffix(SPEAKER_EMBEDDING_SUFFIX)
        if resemblyzer_embedding_is_current(wav_path, embedding_path):
            continue
        try:
            computed.append(compute_resemblyzer_embedding_file(wav_path, embedding_path))
        except Exception as exc:
            failed.append((wav_path, str(exc)))
    return SpeakerEmbeddingBatchResult(computed=computed, failed=failed)


def build_speaker_recognizer(
    *,
    enabled: bool,
    backend: str,
    threshold: float,
    margin: float,
    profiles: list[SpeakerProfile],
) -> SpeakerRecognizerBase | None:
    if not enabled:
        return None
    normalized_backend = (backend or "resemblyzer").strip().lower()
    if normalized_backend == "resemblyzer":
        return ResemblyzerSpeakerRecognizer(profiles, threshold=threshold, margin=margin)
    if normalized_backend == "speechbrain":
        return SpeechBrainSpeakerRecognizer(profiles, threshold=threshold, margin=margin)
    raise ValueError(f"Unsupported speaker recognition backend: {backend}")
