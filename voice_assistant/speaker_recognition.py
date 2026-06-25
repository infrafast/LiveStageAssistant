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


@dataclass
class SpeakerProfile:
    name: str
    wav_paths: list[Path]
    enabled: bool = True
    slug: str | None = None
    embedding_path: Path | None = None


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
        self._profile_embeddings: list[tuple[SpeakerProfile, np.ndarray]] | None = None

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

    def _normalized_embedding_mean(self, embeddings: list[np.ndarray]) -> np.ndarray:
        mean_embedding = np.mean([embedding.astype(np.float32) for embedding in embeddings], axis=0)
        norm = float(np.linalg.norm(mean_embedding))
        if norm > 0:
            mean_embedding = mean_embedding / norm
        return mean_embedding.astype(np.float32)

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
            if self._load_cached_profile_embedding(profile) is not None:
                continue
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

    def _profile_embedding_cache_path(self, profile: SpeakerProfile) -> Path | None:
        if profile.embedding_path:
            return profile.embedding_path
        first_path = next((path for path in profile.wav_paths if path), None)
        if first_path is None:
            return None
        match = re.match(r"^(profil\d+)_\d+$", first_path.stem)
        if match:
            return first_path.with_name(f"{match.group(1)}{SPEAKER_EMBEDDING_SUFFIX}")
        return first_path.with_suffix(SPEAKER_EMBEDDING_SUFFIX)

    def _load_cached_profile_embedding(self, profile: SpeakerProfile) -> np.ndarray | None:
        cache_path = self._profile_embedding_cache_path(profile)
        if cache_path is None or not cache_path.exists() or not cache_path.is_file():
            return None
        existing_paths = [path for path in profile.wav_paths if path.exists() and path.is_file()]
        if len(existing_paths) != len(profile.wav_paths):
            return None
        try:
            cache_mtime = cache_path.stat().st_mtime
            if any(cache_mtime < wav_path.stat().st_mtime for wav_path in existing_paths):
                return None
            embedding = np.load(cache_path)
            if embedding.ndim != 1:
                return None
            return embedding.astype(np.float32)
        except Exception:
            return None

    def _load_profile_embeddings(self) -> list[tuple[SpeakerProfile, np.ndarray]]:
        if self._profile_embeddings:
            return self._profile_embeddings

        embeddings: list[tuple[SpeakerProfile, np.ndarray]] = []
        for profile in self.profiles:
            cached_profile_embedding = self._load_cached_profile_embedding(profile)
            if cached_profile_embedding is not None:
                embeddings.append((profile, cached_profile_embedding))
                continue
            profile_vectors = []
            for wav_path in profile.wav_paths:
                if wav_path.exists() and wav_path.is_file():
                    profile_vectors.append(self._embedding_for_profile_path(wav_path))
            if len(profile_vectors) == len(profile.wav_paths):
                embedding = self._normalized_embedding_mean(profile_vectors)
                cache_path = self._profile_embedding_cache_path(profile)
                if cache_path is not None:
                    try:
                        cache_path.parent.mkdir(parents=True, exist_ok=True)
                        np.save(cache_path, embedding)
                    except OSError:
                        pass
                embeddings.append((profile, embedding))
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
            ((profile, float(np.inner(embedding, profile_embedding))) for profile, profile_embedding in profiles),
            key=lambda item: item[1],
            reverse=True,
        )
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


def compute_resemblyzer_mean_embedding_file(
    wav_paths: list[str | Path],
    embedding_path: str | Path,
) -> Path:
    """Compute and persist a normalized mean Resemblyzer embedding for a profile."""
    cleaned_paths = [Path(path) for path in wav_paths if Path(path).exists() and Path(path).is_file()]
    if len(cleaned_paths) != len(wav_paths):
        raise ValueError("all speaker profile samples must exist before computing the mean embedding")
    recognizer = ResemblyzerSpeakerRecognizer([])
    embeddings = [recognizer._embedding_for_path(path) for path in cleaned_paths]
    embedding = recognizer._normalized_embedding_mean(embeddings)
    embedding_path = Path(embedding_path)
    embedding_path.parent.mkdir(parents=True, exist_ok=True)
    np.save(embedding_path, embedding)
    return embedding_path


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
