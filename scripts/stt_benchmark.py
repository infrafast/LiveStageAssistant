#!/usr/bin/env python3
"""
Standalone STT benchmark for LiveStageAssistant.

Non-intrusive by design:
- reads the selected offline env file as text only;
- reuses BACKEND_AUDIO_INPUT_DEVICE and BACKEND_AUDIO_OUTPUT_DEVICE;
- records/replays reusable WAV files, three takes per phrase by default;
- never starts LiveStageAssistant, MCP, OSC, mixer, QLC+, wake word or TTS;
- experimental runtimes live under .stt-benchmark;
- audio/results live under recordings/stt_benchmark_audio.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import hashlib
import http.client
import json
import math
import shlex
import shutil
import socket
import statistics
import subprocess
import sys
import threading
import time
import uuid
import wave
from array import array
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = PROJECT_ROOT / "raspi_service_pack_stdio/.env.offline"
DEFAULT_CORPUS_DIR = PROJECT_ROOT / "recordings/stt_benchmark_audio"
BENCH_ROOT = PROJECT_ROOT / ".stt-benchmark"
SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH_BYTES = 2
CHUNK_FRAMES = 1024
DEFAULT_TAKES = 3

PHRASES = [
    {"id": 1, "domain": "mixer", "text": "mets guitar-clode à moins cinq dB"},
    {"id": 2, "domain": "mixer", "text": "mets guitar-loran à moins dix dB"},
    {"id": 3, "domain": "mixer", "text": "mets guitar-anto à moins cinq dB"},
    {"id": 4, "domain": "mixer", "text": "mets basse-mike à moins huit dB"},
    {"id": 5, "domain": "mixer", "text": "monte guitar-anto de trois dB"},
    {"id": 6, "domain": "mixer", "text": "baisse basse-mike de quatre dB"},
    {"id": 7, "domain": "mixer", "text": "mets la guitare de anto sur Claude à moins cinq dB"},
    {"id": 8, "domain": "mixer", "text": "baisse progressivement guitar-clode à moins trente dB en deux secondes"},
    {"id": 9, "domain": "mixer", "text": "mute guitar-loran"},
    {"id": 10, "domain": "mixer", "text": "démute basse-mike"},
    {"id": 11, "domain": "mixer", "text": "mets guitar-anto sur le retour Claude à moins douze dB"},
    {"id": 12, "domain": "mixer", "text": "monte le son de la batterie de deux dB"},
    {"id": 13, "domain": "mixer", "text": "mets anto à moins cinq dB"},
    {"id": 14, "domain": "mixer", "text": "baisse un peu anto"},
    {"id": 15, "domain": "qlc", "text": "qlc rouge"},
    {"id": 16, "domain": "qlc", "text": "qlc blanc"},
]

GENERIC_HOTWORDS = (
    "mets, monte, baisse, mute, démute, coupe, rallume, active, réactive, "
    "niveau, volume, statut, mixeur, retour, façade, main, progressivement, dB"
)


def parse_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        raise FileNotFoundError(f"Profil env introuvable: {path}")
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        if raw_value:
            try:
                parts = shlex.split(raw_value, comments=False, posix=True)
                value = parts[0] if len(parts) == 1 else raw_value.strip("\"'")
            except ValueError:
                value = raw_value.strip("\"'")
        else:
            value = ""
        values[key] = value
    return values


def prompt_from_env(values: dict[str, str], env_file: Path) -> str:
    configured = str(values.get("STT_PROMPT") or "").strip()
    if not configured:
        return ""
    candidate = Path(configured)
    paths = [candidate] if candidate.is_absolute() else [env_file.parent / candidate, PROJECT_ROOT / candidate]
    for path in paths:
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    return ""


def endpoint(raw: str, pipewire_kind: str) -> tuple[str, str | int | None]:
    value = str(raw or "").strip()
    prefix = f"pipewire:{pipewire_kind}:"
    if value.startswith(prefix):
        target = value[len(prefix):].strip()
        if not target:
            raise ValueError(f"Endpoint PipeWire incomplet: {value}")
        return "pipewire", target
    if not value:
        return "default", None
    try:
        return "pyaudio", int(value.split(":", 1)[0])
    except ValueError as exc:
        raise ValueError(f"Endpoint audio non supporté: {value}") from exc


def audio_config(values: dict[str, str]) -> dict[str, Any]:
    raw_in = str(values.get("BACKEND_AUDIO_INPUT_DEVICE") or "").strip()
    raw_out = str(values.get("BACKEND_AUDIO_OUTPUT_DEVICE") or "").strip()
    in_kind, in_value = endpoint(raw_in, "source")
    out_kind, out_value = endpoint(raw_out, "sink")
    return {
        "raw_input": raw_in,
        "raw_output": raw_out,
        "input_kind": in_kind,
        "input_value": in_value,
        "output_kind": out_kind,
        "output_value": out_value,
    }


def import_pyaudio():
    try:
        import pyaudio
        return pyaudio
    except ImportError as exc:
        raise RuntimeError("PyAudio absent pour un endpoint audio PyAudio/default.") from exc


def pipewire_record_command(target: str) -> list[str]:
    if shutil.which("pw-record"):
        return [
            "pw-record", "--raw", "--target", target, "--format", "s16",
            "--rate", str(SAMPLE_RATE), "--channels", str(CHANNELS), "-"
        ]
    if shutil.which("pw-cat"):
        return [
            "pw-cat", "--record", "--raw", "--target", target, "--format", "s16",
            "--rate", str(SAMPLE_RATE), "--channels", str(CHANNELS), "-"
        ]
    raise RuntimeError("pw-record ou pw-cat est requis pour la capture PipeWire.")


class Recorder:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.frames: list[bytes] = []
        self.error: BaseException | None = None
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.process = None
        self.pa = None
        self.stream = None
        self.rate = SAMPLE_RATE

    def start(self) -> None:
        self.frames = []
        self.error = None
        self.stop_event.clear()

        if self.config["input_kind"] == "pipewire":
            target = str(self.config["input_value"])
            self.process = subprocess.Popen(
                pipewire_record_command(target),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )

            def read_pipewire() -> None:
                assert self.process and self.process.stdout
                try:
                    while not self.stop_event.is_set():
                        data = self.process.stdout.read(CHUNK_FRAMES * SAMPLE_WIDTH_BYTES)
                        if data:
                            self.frames.append(data)
                        elif self.process.poll() is not None:
                            break
                except BaseException as exc:
                    self.error = exc

            self.thread = threading.Thread(target=read_pipewire, daemon=True)
            self.thread.start()
            return

        pyaudio = import_pyaudio()
        self.pa = pyaudio.PyAudio()
        device = self.config["input_value"] if self.config["input_kind"] == "pyaudio" else None
        info = self.pa.get_device_info_by_index(device) if device is not None else self.pa.get_default_input_device_info()
        candidates = [SAMPLE_RATE, int(round(float(info.get("defaultSampleRate", SAMPLE_RATE))))]
        last_error = None
        for rate in dict.fromkeys(candidates):
            try:
                self.stream = self.pa.open(
                    format=pyaudio.paInt16,
                    channels=CHANNELS,
                    rate=rate,
                    input=True,
                    input_device_index=device,
                    frames_per_buffer=CHUNK_FRAMES,
                )
                self.rate = rate
                break
            except BaseException as exc:
                last_error = exc
        if self.stream is None:
            raise RuntimeError(f"Impossible d'ouvrir l'entrée audio: {last_error}")

        def read_pyaudio() -> None:
            try:
                while not self.stop_event.is_set():
                    self.frames.append(self.stream.read(CHUNK_FRAMES, exception_on_overflow=False))
            except BaseException as exc:
                self.error = exc

        self.thread = threading.Thread(target=read_pyaudio, daemon=True)
        self.thread.start()

    def stop(self) -> tuple[bytes, int]:
        self.stop_event.set()
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                self.process.wait(timeout=1)
            if self.process.poll() is None:
                self.process.kill()
                self.process.wait(timeout=1)
        if self.thread is not None:
            self.thread.join(timeout=2)
        if self.stream is not None:
            self.stream.stop_stream()
            self.stream.close()
        if self.pa is not None:
            self.pa.terminate()
        if self.error is not None:
            raise RuntimeError(f"Erreur de capture: {self.error}")
        return b"".join(self.frames), self.rate


def play_wav(path: Path, config: dict[str, Any]) -> None:
    if config["output_kind"] == "pipewire":
        if not shutil.which("pw-play"):
            raise RuntimeError("pw-play est requis pour la relecture PipeWire.")
        subprocess.run(["pw-play", "--target", str(config["output_value"]), str(path)], check=True)
        return

    pyaudio = import_pyaudio()
    pa = pyaudio.PyAudio()
    stream = None
    try:
        with wave.open(str(path), "rb") as wav:
            device = config["output_value"] if config["output_kind"] == "pyaudio" else None
            stream = pa.open(
                format=pa.get_format_from_width(wav.getsampwidth()),
                channels=wav.getnchannels(),
                rate=wav.getframerate(),
                output=True,
                output_device_index=device,
                frames_per_buffer=CHUNK_FRAMES,
            )
            while True:
                data = wav.readframes(CHUNK_FRAMES)
                if not data:
                    break
                stream.write(data)
    finally:
        if stream is not None:
            stream.stop_stream()
            stream.close()
        pa.terminate()


def write_wav(path: Path, pcm: bytes, rate: int) -> float:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(CHANNELS)
        wav.setsampwidth(SAMPLE_WIDTH_BYTES)
        wav.setframerate(rate)
        wav.writeframes(pcm)
    return len(pcm) / float(SAMPLE_WIDTH_BYTES * CHANNELS * rate)


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as wav:
        return wav.getnframes() / float(wav.getframerate() or 1)


def audio_metrics(pcm: bytes) -> dict[str, float]:
    if not pcm:
        return {"rms_dbfs": -120.0, "peak_dbfs": -120.0, "clipped_percent": 0.0}
    samples = array("h")
    samples.frombytes(pcm)
    if sys.byteorder != "little":
        samples.byteswap()
    peak = max(abs(int(v)) for v in samples)
    rms = math.sqrt(sum(int(v) * int(v) for v in samples) / len(samples))
    clipped = sum(1 for v in samples if abs(int(v)) >= 32760)
    def dbfs(value: float) -> float:
        return -120.0 if value <= 0 else 20.0 * math.log10(value / 32768.0)
    return {
        "rms_dbfs": round(dbfs(rms), 2),
        "peak_dbfs": round(dbfs(peak), 2),
        "clipped_percent": round(clipped * 100.0 / len(samples), 4),
    }


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(corpus_dir: Path) -> dict[str, Any]:
    path = corpus_dir / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"recordings": []}


def save_manifest(
    corpus_dir: Path,
    rows: list[dict[str, Any]],
    env_file: Path,
    config: dict[str, Any],
    takes: int,
) -> None:
    corpus_dir.mkdir(parents=True, exist_ok=True)
    rows = sorted(rows, key=lambda item: (int(item["phrase_id"]), int(item["take"])))
    payload = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "LiveStageAssistant isolated STT benchmark corpus",
        "benchmark_generation": 2,
        "env_file_read_only": str(env_file),
        "backend_audio_input_device": config["raw_input"],
        "backend_audio_output_device": config["raw_output"],
        "sample_rate_target_hz": SAMPLE_RATE,
        "channels": CHANNELS,
        "sample_width_bits": 16,
        "takes_per_phrase_requested": takes,
        "phrase_count": len(PHRASES),
        "phrases": PHRASES,
        "recordings": rows,
    }
    (corpus_dir / "manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    fields = [
        "phrase_id", "domain", "take", "file", "reference", "duration_seconds",
        "sample_rate_hz", "rms_dbfs", "peak_dbfs", "clipped_percent", "sha256"
    ]
    with (corpus_dir / "manifest.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def upsert(rows: list[dict[str, Any]], row: dict[str, Any]) -> None:
    key = (int(row["phrase_id"]), int(row["take"]))
    rows[:] = [
        item for item in rows
        if (int(item["phrase_id"]), int(item["take"])) != key
    ]
    rows.append(row)


def record_corpus(env_file: Path, corpus_dir: Path, takes: int) -> None:
    values = parse_env_file(env_file)
    config = audio_config(values)
    rows = list(load_manifest(corpus_dir).get("recordings") or [])

    print("\n=== CAPTURE STT ISOLÉE ===")
    print(f"Profil lu           : {env_file}")
    print(f"Entrée              : {config['raw_input'] or '<défaut système>'}")
    print(f"Sortie replay       : {config['raw_output'] or '<défaut système>'}")
    print(f"Corpus              : {corpus_dir}")
    print(f"Phrases             : {len(PHRASES)}")
    print(f"Prises par phrase   : {takes}")
    print("Aucun wake word, LSA runtime, MCP, mixer, OSC ou QLC n'est lancé.")

    for phrase in PHRASES:
        phrase_id = int(phrase["id"])
        reference = str(phrase["text"])
        domain = str(phrase["domain"])
        print("\n" + "=" * 76)
        print(f"PHRASE {phrase_id:02d}/{len(PHRASES):02d} [{domain}]")
        print(f'À prononcer: "{reference}"')

        for take in range(1, takes + 1):
            filename = f"{phrase_id:02d}_take{take}.wav"
            wav_path = corpus_dir / filename
            existing = next(
                (
                    item for item in rows
                    if int(item["phrase_id"]) == phrase_id
                    and int(item["take"]) == take
                    and wav_path.exists()
                ),
                None,
            )
            if existing is not None:
                while True:
                    cmd = input(
                        f"Prise {take}/{takes} existante: "
                        "[Entrée/a]=garder, p=replay, r=refaire, q=quitter : "
                    ).strip().lower()
                    if cmd in {"", "a"}:
                        break
                    if cmd == "p":
                        play_wav(wav_path, config)
                        continue
                    if cmd == "r":
                        existing = None
                        break
                    if cmd == "q":
                        save_manifest(corpus_dir, rows, env_file, config, takes)
                        return
                    print("Commande inconnue.")
                if existing is not None:
                    continue

            while True:
                cmd = input(
                    f"Prise {take}/{takes} -> {filename} | "
                    "Entrée=enregistrer, s=sauter, q=quitter : "
                ).strip().lower()
                if cmd == "q":
                    save_manifest(corpus_dir, rows, env_file, config, takes)
                    return
                if cmd == "s":
                    break
                if cmd:
                    print("Commande inconnue.")
                    continue

                recorder = Recorder(config)
                print("● ENREGISTREMENT — parle puis Entrée pour arrêter.")
                recorder.start()
                input()
                pcm, rate = recorder.stop()
                if not pcm:
                    print("Aucun audio capturé.")
                    continue

                duration = write_wav(wav_path, pcm, rate)
                metrics = audio_metrics(pcm)
                print(
                    f"{duration:.2f}s | {rate} Hz | "
                    f"RMS {metrics['rms_dbfs']:.1f} dBFS | "
                    f"peak {metrics['peak_dbfs']:.1f} dBFS"
                )

                accepted = False
                while True:
                    decision = input(
                        "[Entrée/a]=accepter, p=replay, r=recommencer, "
                        "s=sauter, q=quitter : "
                    ).strip().lower()
                    if decision == "p":
                        play_wav(wav_path, config)
                        continue
                    if decision == "r":
                        with contextlib.suppress(FileNotFoundError):
                            wav_path.unlink()
                        break
                    if decision == "s":
                        with contextlib.suppress(FileNotFoundError):
                            wav_path.unlink()
                        accepted = True
                        break
                    if decision == "q":
                        with contextlib.suppress(FileNotFoundError):
                            wav_path.unlink()
                        save_manifest(corpus_dir, rows, env_file, config, takes)
                        return
                    if decision in {"", "a"}:
                        upsert(rows, {
                            "phrase_id": phrase_id,
                            "domain": domain,
                            "take": take,
                            "file": filename,
                            "reference": reference,
                            "duration_seconds": round(duration, 3),
                            "sample_rate_hz": rate,
                            **metrics,
                            "sha256": sha256(wav_path),
                        })
                        save_manifest(corpus_dir, rows, env_file, config, takes)
                        print("✓ prise acceptée")
                        accepted = True
                        break
                    print("Commande inconnue.")

                if decision == "r":
                    continue
                if accepted:
                    break

    save_manifest(corpus_dir, rows, env_file, config, takes)
    print("\nCorpus terminé.")
    print(f"Prises validées: {len(rows)}/{len(PHRASES) * takes}")


def verify_corpus(env_file: Path, corpus_dir: Path, replay: bool) -> bool:
    values = parse_env_file(env_file)
    config = audio_config(values)
    manifest = load_manifest(corpus_dir)
    rows = list(manifest.get("recordings") or [])
    takes = int(manifest.get("takes_per_phrase_requested") or DEFAULT_TAKES)
    expected = len(PHRASES) * takes
    valid = 0
    issues: list[str] = []
    for row in rows:
        path = corpus_dir / str(row["file"])
        if not path.exists():
            issues.append(f"manquant: {path.name}")
            continue
        if row.get("sha256") and sha256(path) != row["sha256"]:
            issues.append(f"SHA différent: {path.name}")
            continue
        valid += 1

    print("\n=== CORPUS ===")
    print(f"Valides: {valid}/{expected}")
    for issue in issues:
        print(f"⚠ {issue}")

    if replay and rows:
        while True:
            name = input("Replay fichier (ex 03_take2), Entrée=retour : ").strip()
            if not name:
                break
            if not name.endswith(".wav"):
                name += ".wav"
            path = corpus_dir / name
            if path.exists():
                play_wav(path, config)
            else:
                print("Fichier introuvable.")
    return valid == expected and not issues


def engine_paths() -> dict[str, Path]:
    return {
        "whisper_server": BENCH_ROOT / "whisper.cpp/build/bin/whisper-server",
        "whisper_base": BENCH_ROOT / "whisper.cpp/models/ggml-base-q5_0.bin",
        "whisper_small": BENCH_ROOT / "whisper.cpp/models/ggml-small-q5_0.bin",
        "sherpa_python": BENCH_ROOT / "sherpa-venv/bin/python",
        "sherpa_model": BENCH_ROOT / "models/sherpa-onnx-streaming-zipformer-fr-2023-04-14",
    }


def sherpa_model_ready(model_dir: Path) -> bool:
    required = [
        "tokens.txt",
        "encoder-epoch-29-avg-9-with-averaged-model.int8.onnx",
        "decoder-epoch-29-avg-9-with-averaged-model.onnx",
        "joiner-epoch-29-avg-9-with-averaged-model.onnx",
    ]
    return all((model_dir / name).exists() for name in required)


def engine_status() -> dict[str, dict[str, Any]]:
    paths = engine_paths()
    try:
        import faster_whisper
        faster = {"available": True, "detail": getattr(faster_whisper, "__version__", "installed")}
    except Exception as exc:
        faster = {"available": False, "detail": str(exc)}
    return {
        "faster-whisper-base": faster,
        "whisper.cpp-base-q5_0": {
            "available": paths["whisper_server"].exists() and paths["whisper_base"].exists(),
            "detail": str(paths["whisper_base"]),
        },
        "whisper.cpp-small-q5_0": {
            "available": paths["whisper_server"].exists() and paths["whisper_small"].exists(),
            "detail": str(paths["whisper_small"]),
        },
        "sherpa-onnx-zipformer-fr-int8": {
            "available": paths["sherpa_python"].exists() and sherpa_model_ready(paths["sherpa_model"]),
            "detail": str(paths["sherpa_model"]),
        },
    }


def print_engine_status() -> dict[str, dict[str, Any]]:
    status = engine_status()
    print("\n=== MOTEURS ===")
    for name, item in status.items():
        print(f"{'✓' if item['available'] else '✗'} {name}")
        print(f"  {item['detail']}")
    if not all(item["available"] for item in status.values()):
        print("\nPréparation isolée:")
        print("  bash scripts/stt_benchmark_setup.sh")
    return status


def read_wav_float(path: Path):
    import numpy as np
    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        width = wav.getsampwidth()
        rate = wav.getframerate()
        raw = wav.readframes(wav.getnframes())
    if width != 2:
        raise RuntimeError(f"{path.name}: PCM16 requis")
    samples = np.frombuffer(raw, dtype="<i2").astype(np.float32)
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    samples *= 1.0 / 32768.0
    if rate != SAMPLE_RATE:
        old_x = np.linspace(0.0, 1.0, num=len(samples), endpoint=False)
        new_len = max(1, round(len(samples) * SAMPLE_RATE / rate))
        new_x = np.linspace(0.0, 1.0, num=new_len, endpoint=False)
        samples = np.interp(new_x, old_x, samples).astype(np.float32)
    return samples


def normalize_text(text: str) -> str:
    import unicodedata
    value = unicodedata.normalize("NFD", str(text or "")).lower()
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    value = "".join(ch if ch.isalnum() else " " for ch in value)
    return " ".join(value.split())


def word_distance(reference: str, hypothesis: str) -> tuple[int, int]:
    ref = normalize_text(reference).split()
    hyp = normalize_text(hypothesis).split()
    previous = list(range(len(hyp) + 1))
    for i, rw in enumerate(ref, 1):
        current = [i]
        for j, hw in enumerate(hyp, 1):
            current.append(min(
                previous[j] + 1,
                current[j - 1] + 1,
                previous[j - 1] + (rw != hw),
            ))
        previous = current
    return previous[-1], len(ref)


def format_eta(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{sec:02d}s"
    if minutes:
        return f"{minutes}m{sec:02d}s"
    return f"{sec}s"


def print_decode_start(engine: str, index: int, total: int, row: dict[str, Any]) -> None:
    print(f'[{engine}] [{index:02d}/{total:02d}] À décoder : "{row["reference"]}"', flush=True)
    print(f'{" " * (len(engine) + 5)}fichier    : {row["file"]}', flush=True)


def print_decode_done(
    engine: str,
    index: int,
    total: int,
    row: dict[str, Any],
    result: dict[str, Any],
    completed_decode_ms: list[float],
) -> None:
    decoded = result.get("transcription") or "<vide>"
    print(f'{" " * (len(engine) + 5)}décodé     : "{decoded}"', flush=True)
    if result.get("error"):
        print(f'{" " * (len(engine) + 5)}ERREUR     : {result["error"]}', flush=True)
    completed_decode_ms.append(float(result.get("decode_ms") or 0.0))
    remaining = max(0, total - index)
    avg_seconds = (sum(completed_decode_ms) / len(completed_decode_ms)) / 1000.0
    eta = avg_seconds * remaining
    print(
        f'{" " * (len(engine) + 5)}temps      : {result.get("decode_ms")} ms | '
        f'RTF={result.get("rtf")} | ETA ≈ {format_eta(eta)}',
        flush=True,
    )


def result_row(source: dict[str, Any], engine: str, text: str, decode_ms: float, error: str | None) -> dict[str, Any]:
    duration = float(source.get("duration_seconds") or 0.0)
    edits, words = word_distance(str(source["reference"]), text)
    return {
        "engine": engine,
        "phrase_id": int(source["phrase_id"]),
        "domain": str(source.get("domain") or "mixer"),
        "take": int(source["take"]),
        "file": str(source["file"]),
        "reference": str(source["reference"]),
        "transcription": text,
        "audio_duration_seconds": duration,
        "decode_ms": round(decode_ms, 2),
        "rtf": round((decode_ms / 1000.0) / duration, 4) if duration else None,
        "exact_text": normalize_text(str(source["reference"])) == normalize_text(text),
        "word_edits": edits,
        "reference_words": words,
        "error": error,
    }


def benchmark_faster(rows: list[dict[str, Any]], corpus_dir: Path, prompt: str):
    from faster_whisper import WhisperModel
    engine = "faster-whisper-base"
    print("Chargement du modèle Faster-Whisper base...", flush=True)
    started = time.perf_counter()
    model = WhisperModel("base", device="cpu", compute_type="int8", cpu_threads=4, num_workers=1)
    load_ms = (time.perf_counter() - started) * 1000
    print(f"Modèle chargé en {load_ms:.0f} ms.", flush=True)

    def decode(path: Path) -> str:
        segments, _ = model.transcribe(
            read_wav_float(path),
            language="fr",
            initial_prompt=prompt or None,
            hotwords=GENERIC_HOTWORDS,
            beam_size=1,
            best_of=1,
            temperature=0.0,
            condition_on_previous_text=False,
            word_timestamps=False,
            vad_filter=False,
            max_new_tokens=48,
        )
        return "".join(segment.text for segment in segments).strip()

    if rows:
        print(f'Warm-up (non compté) : {rows[0]["file"]}', flush=True)
        decode(corpus_dir / str(rows[0]["file"]))
        print("Warm-up terminé.", flush=True)

    output = []
    decode_times: list[float] = []
    total = len(rows)
    for index, row in enumerate(rows, 1):
        print_decode_start(engine, index, total, row)
        started = time.perf_counter()
        try:
            text = decode(corpus_dir / str(row["file"]))
            error = None
        except Exception as exc:
            text = ""
            error = f"{type(exc).__name__}: {exc}"
        current = result_row(row, engine, text, (time.perf_counter() - started) * 1000, error)
        output.append(current)
        print_decode_done(engine, index, total, row, current, decode_times)
    return output, {
        "engine": "faster-whisper-base",
        "runtime": "faster-whisper",
        "model": "base",
        "compute": "CPU int8, threads=4, workers=1, beam=1",
        "load_ms": round(load_ms, 2),
        "warmup": "first WAV discarded",
        "prompt": prompt,
        "hotwords": GENERIC_HOTWORDS,
    }


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def health(port: int) -> bool:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=0.5)
    try:
        conn.request("GET", "/health")
        response = conn.getresponse()
        payload = response.read()
        return response.status == 200 and json.loads(payload.decode()).get("status") == "ok"
    finally:
        conn.close()


def multipart(path: Path, prompt: str) -> tuple[bytes, str]:
    boundary = "lsa-" + uuid.uuid4().hex
    fields = {
        "temperature": "0.0",
        "temperature_inc": "0.0",
        "prompt": prompt,
        "response_format": "json",
    }
    parts: list[bytes] = []
    for name, value in fields.items():
        parts += [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
            value.encode(),
            b"\r\n",
        ]
    parts += [
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'.encode(),
        b"Content-Type: audio/wav\r\n\r\n",
        path.read_bytes(),
        b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    return b"".join(parts), boundary


def whisper_infer(port: int, path: Path, prompt: str) -> str:
    body, boundary = multipart(path, prompt)
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=120)
    try:
        conn.request(
            "POST", "/inference", body=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}
        )
        response = conn.getresponse()
        payload = response.read()
        if response.status >= 400:
            raise RuntimeError(f"HTTP {response.status}: {payload[:200]!r}")
        return str(json.loads(payload.decode()).get("text") or "").strip()
    finally:
        conn.close()


def git_head(path: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return "unknown"


def benchmark_whisper_cpp(
    rows: list[dict[str, Any]],
    corpus_dir: Path,
    prompt: str,
    model_path: Path,
    engine: str,
    results_dir: Path,
):
    paths = engine_paths()
    port = free_port()
    log_path = results_dir / (engine.replace("/", "_") + ".server.log")
    log = log_path.open("wb")
    cmd = [
        str(paths["whisper_server"]), "-m", str(model_path), "-l", "fr",
        "-t", "4", "-bo", "1", "-bs", "1", "-nf", "-nt", "-ng",
        "--host", "127.0.0.1", "--port", str(port)
    ]
    if prompt:
        cmd += ["--prompt", prompt]
    started = time.perf_counter()
    process = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 120
        while True:
            if process.poll() is not None:
                raise RuntimeError(f"whisper-server arrêté; voir {log_path}")
            try:
                if health(port):
                    break
            except Exception:
                pass
            if time.monotonic() > deadline:
                raise TimeoutError(f"whisper-server non prêt; voir {log_path}")
            time.sleep(0.2)
        load_ms = (time.perf_counter() - started) * 1000

        print(f"Serveur {engine} prêt en {load_ms:.0f} ms.", flush=True)
        if rows:
            print(f'Warm-up (non compté) : {rows[0]["file"]}', flush=True)
            whisper_infer(port, corpus_dir / str(rows[0]["file"]), prompt)
            print("Warm-up terminé.", flush=True)

        output = []
        decode_times: list[float] = []
        total = len(rows)
        for index, row in enumerate(rows, 1):
            print_decode_start(engine, index, total, row)
            started_one = time.perf_counter()
            try:
                text = whisper_infer(port, corpus_dir / str(row["file"]), prompt)
                error = None
            except Exception as exc:
                text = ""
                error = f"{type(exc).__name__}: {exc}"
            current = result_row(row, engine, text, (time.perf_counter() - started_one) * 1000, error)
            output.append(current)
            print_decode_done(engine, index, total, row, current, decode_times)
        return output, {
            "engine": engine,
            "runtime": "whisper.cpp persistent local server",
            "model": model_path.name,
            "compute": "CPU, threads=4, beam=1, best_of=1, no-fallback, no-gpu",
            "load_ms": round(load_ms, 2),
            "warmup": "first WAV discarded",
            "prompt": prompt,
            "whisper_cpp_commit": git_head(BENCH_ROOT / "whisper.cpp"),
            "server_log": str(log_path),
        }
    finally:
        if process.poll() is None:
            process.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=5)
            if process.poll() is None:
                process.kill()
        log.close()


def benchmark_sherpa(rows: list[dict[str, Any]], corpus_dir: Path, results_dir: Path):
    paths = engine_paths()
    raw_output = results_dir / "sherpa_raw_results.json"
    log_path = results_dir / "sherpa.log"
    cmd = [
        str(paths["sherpa_python"]),
        str(PROJECT_ROOT / "scripts/stt_benchmark_sherpa.py"),
        "--manifest", str(corpus_dir / "manifest.json"),
        "--corpus-dir", str(corpus_dir),
        "--model-dir", str(paths["sherpa_model"]),
        "--output", str(raw_output),
        "--threads", "4",
    ]
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="", flush=True)
            log.write(line)
            log.flush()
        returncode = process.wait()
    if returncode != 0:
        raise RuntimeError(f"sherpa a échoué; voir {log_path}")
    raw = json.loads(raw_output.read_text(encoding="utf-8"))
    by_file = {item["file"]: item for item in raw["results"]}
    output = []
    for row in rows:
        item = by_file.get(row["file"], {})
        output.append(result_row(
            row,
            "sherpa-onnx-zipformer-fr-int8",
            str(item.get("transcription") or ""),
            float(item.get("decode_ms") or 0),
            item.get("error") or ("missing result" if not item else None),
        ))
    return output, {
        "engine": "sherpa-onnx-zipformer-fr-int8",
        "runtime": f"sherpa-onnx {raw.get('sherpa_version', 'unknown')}",
        "model": "sherpa-onnx-streaming-zipformer-fr-2023-04-14",
        "compute": "CPU, int8 encoder, threads=4, greedy_search, online transducer",
        "load_ms": raw.get("load_ms"),
        "warmup": "first WAV discarded",
        "prompt": "",
        "hotwords": "",
    }


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    if len(values) == 1:
        return values[0]
    rank = (len(values) - 1) * fraction
    low = int(math.floor(rank))
    high = int(math.ceil(rank))
    if low == high:
        return values[low]
    return values[low] + (values[high] - values[low]) * (rank - low)


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [row for row in rows if not row.get("error")]
    decode = [float(row["decode_ms"]) for row in valid]
    rtfs = [float(row["rtf"]) for row in valid if row.get("rtf") is not None]
    edits = sum(int(row["word_edits"]) for row in valid)
    words = sum(int(row["reference_words"]) for row in valid)
    exact = sum(1 for row in valid if row["exact_text"])
    return {
        "samples": len(rows),
        "errors": len(rows) - len(valid),
        "decode_p50_ms": round(percentile(decode, 0.5), 2),
        "decode_p95_ms": round(percentile(decode, 0.95), 2),
        "decode_mean_ms": round(statistics.mean(decode), 2) if decode else None,
        "rtf_mean": round(statistics.mean(rtfs), 4) if rtfs else None,
        "exact_percent": round(100 * exact / len(valid), 2) if valid else 0.0,
        "wer_percent": round(100 * edits / words, 2) if words else None,
    }


def write_results(corpus_dir: Path, results_dir: Path, rows: list[dict[str, Any]], metadata: list[dict[str, Any]]) -> Path:
    summaries = []
    for engine in sorted({row["engine"] for row in rows}):
        summary = summarize([row for row in rows if row["engine"] == engine])
        meta = next(item for item in metadata if item["engine"] == engine)
        summaries.append({"engine": engine, **summary, "load_ms": meta.get("load_ms")})

    payload = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "production_runtime_changed": False,
        "corpus_manifest": str(corpus_dir / "manifest.json"),
        "engine_metadata": metadata,
        "summaries": summaries,
        "results": rows,
    }
    json_path = results_dir / "benchmark_results.json"
    csv_path = results_dir / "benchmark_results.csv"
    report_path = results_dir / "benchmark_report.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    fields = [
        "engine", "phrase_id", "domain", "take", "file", "reference", "transcription",
        "audio_duration_seconds", "decode_ms", "rtf", "exact_text", "word_edits",
        "reference_words", "error"
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    lines = [
        "# LSA STT benchmark",
        "",
        "Benchmark isolé: aucun runtime LSA, MCP, OSC, mixer ou QLC n'est démarré.",
        "",
        "## Résumé STT brut",
        "",
        "| Engine | Samples | Errors | Load ms | Decode p50 ms | Decode p95 ms | RTF moyen | Exact | WER |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in summaries:
        lines.append(
            f"| {item['engine']} | {item['samples']} | {item['errors']} | "
            f"{item.get('load_ms', '-')} | {item['decode_p50_ms']} | {item['decode_p95_ms']} | "
            f"{item['rtf_mean']} | {item['exact_percent']}% | {item['wer_percent']}% |"
        )
    lines += [
        "",
        "WER et exact match sont secondaires. La décision live doit utiliser le replay",
        "parser/resolver XMSeries et conserver wrong_accepted à zéro.",
        "",
        "## Transcriptions brutes",
        "",
    ]
    for row in rows:
        lines.append(
            f"- {row['engine']} / {row['file']} | ref={row['reference']} | "
            f"stt={row['transcription']} | {row['decode_ms']} ms | RTF={row['rtf']}"
            + (f" | ERROR={row['error']}" if row.get("error") else "")
        )
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    shutil.copy2(json_path, corpus_dir / "benchmark_results.json")
    shutil.copy2(csv_path, corpus_dir / "benchmark_results.csv")
    shutil.copy2(report_path, corpus_dir / "benchmark_report.md")
    return json_path


def choose_engines(status: dict[str, dict[str, Any]]) -> list[str]:
    available = [name for name, item in status.items() if item["available"]]
    if not available:
        return []
    print("\nDisponibles:")
    for index, name in enumerate(available, 1):
        print(f" {index}) {name}")
    print(" a) tous")
    raw = input("Choix [a] : ").strip().lower() or "a"
    if raw == "a":
        return available
    selected = []
    for token in raw.replace(",", " ").split():
        try:
            selected.append(available[int(token) - 1])
        except (ValueError, IndexError):
            print(f"Choix ignoré: {token}")
    return list(dict.fromkeys(selected))


def run_xmseries_scorer(results_json: Path, results_dir: Path) -> None:
    xm_root = PROJECT_ROOT.parent / "XMSeries-MCP"
    scorer = xm_root / "scripts/stt_resolver_benchmark.mjs"
    if not scorer.exists():
        print("Scorer XMSeries absent: résultat STT brut conservé.")
        return
    print("\nReplay parser/resolver XMSeries, sans mixer ni OSC...")
    process = subprocess.run(
        ["node", str(scorer), "--input", str(results_json), "--output-dir", str(results_dir)],
        cwd=str(xm_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    print(process.stdout)
    if process.returncode:
        print("⚠ Le scorer XMSeries a échoué; le résultat STT brut reste valide.")


def run_benchmark(env_file: Path, corpus_dir: Path) -> None:
    if not verify_corpus(env_file, corpus_dir, replay=False):
        print("Corpus incomplet: utilise le menu 1.")
        return
    rows = list(load_manifest(corpus_dir)["recordings"])
    status = print_engine_status()
    selected = choose_engines(status)
    if not selected:
        print("Aucun moteur sélectionné.")
        return

    values = parse_env_file(env_file)
    prompt = prompt_from_env(values, env_file)
    results_dir = corpus_dir / "results" / datetime.now().strftime("%Y%m%d-%H%M%S")
    results_dir.mkdir(parents=True, exist_ok=True)
    paths = engine_paths()

    all_rows = []
    metadata = []
    for engine in selected:
        print(f"\n=== {engine} ===")
        try:
            if engine == "faster-whisper-base":
                current, meta = benchmark_faster(rows, corpus_dir, prompt)
            elif engine == "whisper.cpp-base-q5_0":
                current, meta = benchmark_whisper_cpp(
                    rows, corpus_dir, prompt, paths["whisper_base"], engine, results_dir
                )
            elif engine == "whisper.cpp-small-q5_0":
                current, meta = benchmark_whisper_cpp(
                    rows, corpus_dir, prompt, paths["whisper_small"], engine, results_dir
                )
            elif engine == "sherpa-onnx-zipformer-fr-int8":
                current, meta = benchmark_sherpa(rows, corpus_dir, results_dir)
            else:
                continue
            all_rows += current
            metadata.append(meta)
            summary = summarize(current)
            print(
                f"p50={summary['decode_p50_ms']} ms | p95={summary['decode_p95_ms']} ms | "
                f"RTF={summary['rtf_mean']} | exact={summary['exact_percent']}% | "
                f"WER={summary['wer_percent']}%"
            )
        except Exception as exc:
            print(f"ERREUR {engine}: {type(exc).__name__}: {exc}")

    if not all_rows:
        print("Aucun résultat.")
        return
    results_json = write_results(corpus_dir, results_dir, all_rows, metadata)
    print(f"\nJSON   : {results_json}")
    print(f"Rapport: {results_dir / 'benchmark_report.md'}")
    run_xmseries_scorer(results_json, results_dir)


def menu(env_file: Path, corpus_dir: Path, takes: int) -> int:
    while True:
        print("\n" + "=" * 72)
        print("LSA STT BENCHMARK - ISOLÉ / NON INTRUSIF")
        print("=" * 72)
        print(f"Profil: {env_file}")
        print(f"Corpus: {corpus_dir}")
        print("1) Enregistrer / reprendre / refaire le corpus (3 prises par phrase)")
        print("2) Vérifier / écouter le corpus")
        print("3) État des moteurs benchmark")
        print("4) Lancer le benchmark des WAV existants")
        print("q) Quitter")
        choice = input("Choix : ").strip().lower()
        if choice == "1":
            record_corpus(env_file, corpus_dir, takes)
        elif choice == "2":
            verify_corpus(env_file, corpus_dir, replay=True)
        elif choice == "3":
            print_engine_status()
        elif choice == "4":
            run_benchmark(env_file, corpus_dir)
        elif choice in {"q", "quit", "exit"}:
            return 0
        else:
            print("Choix inconnu.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Standalone LSA STT benchmark")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--corpus-dir", default=str(DEFAULT_CORPUS_DIR))
    parser.add_argument("--takes", type=int, default=DEFAULT_TAKES)
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    if args.takes < 1:
        parser.error("--takes doit être >= 1")
    env_file = Path(args.env_file).expanduser().resolve()
    corpus_dir = Path(args.corpus_dir).expanduser().resolve()

    try:
        if args.record:
            record_corpus(env_file, corpus_dir, args.takes)
            return 0
        if args.verify:
            return 0 if verify_corpus(env_file, corpus_dir, replay=False) else 1
        if args.status:
            print_engine_status()
            return 0
        if args.run:
            run_benchmark(env_file, corpus_dir)
            return 0
        return menu(env_file, corpus_dir, args.takes)
    except KeyboardInterrupt:
        print("\nInterrompu proprement.")
        return 130
    except Exception as exc:
        print(f"\nErreur: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
