#!/usr/bin/env python3
"""
Standalone STT corpus recorder + benchmark for LiveStageAssistant.

The script never imports or starts LiveStageAssistant and never calls MCPs.

Interactive modes:
  1) record/replace the benchmark corpus
  2) benchmark the WAV files already recorded

Raspberry defaults are read from:
  raspi_service_pack_stdio/.env.offline

Capture reuses BACKEND_AUDIO_INPUT_DEVICE / BACKEND_AUDIO_OUTPUT_DEVICE,
including PipeWire source/sink selectors used by the Raspberry service pack.

Benchmark compares Faster-Whisper configurations on exactly the same WAV files
and writes CSV, JSON and Markdown reports. Deliberately weak/clipped recordings
are kept in the benchmark and are also reported separately from clean samples.
"""

from __future__ import annotations

import argparse
import csv
import gc
import hashlib
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
import wave
from array import array
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

try:
    import pyaudio
except ImportError:
    print(
        "Erreur: PyAudio n'est pas installé dans cet environnement Python.\n"
        "Utilise l'environnement Python de LSA, par exemple:\n"
        "  .venv/bin/python scripts/stt_benchmark_capture.py",
        file=sys.stderr,
    )
    raise SystemExit(2)


DEFAULT_ENV_FILE = "raspi_service_pack_stdio/.env.offline"
DEFAULT_OUTPUT_DIR = "recordings/stt_benchmark_audio"
DEFAULT_SAMPLE_RATE = 16000
DEFAULT_TAKES = 3
CHANNELS = 1
FORMAT = pyaudio.paInt16
SAMPLE_WIDTH_BYTES = 2
CHUNK_FRAMES = 1024

PHRASES = [
    "mets guitar-clode à moins cinq dB",
    "mets guitar-loran à moins dix dB",
    "mets guitar-anto à moins cinq dB",
    "mets basse-mike à moins huit dB",
    "monte guitar-anto de trois dB",
    "baisse basse-mike de quatre dB",
    "mets la guitare de anto sur Claude à moins cinq dB",
    "baisse progressivement guitar-clode à moins trente dB en deux secondes",
    "mute guitar-loran",
    "démute basse-mike",
    "mets guitar-anto sur le retour Claude à moins douze dB",
    "monte le son de la batterie de deux dB",
]

# Same compact vocabulary family as the LSA local Whisper runtime, completed
# with the current benchmark's configured mixer names.
HOTWORD_CORE = [
    "mets", "monte", "baisse", "mute", "unmute", "démute", "coupe", "rallume",
    "niveau", "volume", "fader", "bus", "retour", "façade", "main",
    "moins", "plus", "dB", "décibel", "décibels",
    "zéro", "un", "deux", "trois", "quatre", "cinq", "six", "sept", "huit", "neuf",
    "dix", "onze", "douze", "treize", "quatorze", "quinze", "seize",
    "vingt", "trente", "quarante", "cinquante", "soixante", "quatre-vingt",
    "guitar-clode", "guitar-loran", "guitar-anto", "basse-mike", "Claude", "batterie",
]

# Canonical entities and aliases used only for benchmark scoring/repair.
# This is intentionally small and domain-constrained; it is not a free-form NLP rewrite.
ENTITY_ALIASES = {
    "guitar-clode": ["guitar-clode", "guitar clode"],
    "guitar-loran": ["guitar-loran", "guitar loran"],
    "guitar-anto": [
        "guitar-anto", "guitar anto", "guitare de anto", "guitar de anto",
        "guitare anto",
    ],
    "basse-mike": ["basse-mike", "basse mike"],
    "claude": ["claude"],
    "batterie": ["batterie"],
}


@dataclass
class AudioConfig:
    requested_device: Optional[str]
    pyaudio_device_index: Optional[int]
    pipewire_target: Optional[str]
    device_name: str
    sample_rate: int


@dataclass
class PlaybackConfig:
    requested_device: Optional[str]
    pyaudio_device_index: Optional[int]
    pipewire_target: Optional[str]
    device_name: str


@dataclass
class BenchmarkVariant:
    name: str
    model_name: str
    use_prompt: bool
    use_hotwords: bool
    apply_repair: bool = False
    derived_from: Optional[str] = None


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse a dotenv file as text without executing/sourcing it."""
    values: dict[str, str] = {}
    if not path.exists():
        raise FileNotFoundError(f"Fichier env introuvable: {path}")

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


def resolve_text_setting(value: str, repo_root: Path) -> str:
    """Resolve an env setting that can contain either literal text or a file path."""
    raw = str(value or "").strip()
    if not raw:
        return ""

    candidates = [Path(raw)]
    if not Path(raw).is_absolute():
        candidates.insert(0, repo_root / raw)

    for candidate in candidates:
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8").strip()
    return raw


def configured_device_selector(env: dict[str, str], key: str) -> Optional[str]:
    raw = env.get(key, "").strip()
    return raw or None


def parse_pipewire_selector(selected: Optional[str], kind: str) -> Optional[str]:
    prefix = f"pipewire:{kind}:"
    value = str(selected or "").strip()
    if value.startswith(prefix):
        target = value[len(prefix):].strip()
        return target or None
    return None


def pipewire_record_command() -> Optional[str]:
    for command in ("pw-cat", "pw-record"):
        if shutil.which(command):
            return command
    return None


def list_audio_devices(pa: pyaudio.PyAudio) -> None:
    print("\nPériphériques audio PyAudio disponibles:\n")
    default_input: Optional[int] = None
    default_output: Optional[int] = None
    try:
        default_input = int(pa.get_default_input_device_info()["index"])
    except Exception:
        pass
    try:
        default_output = int(pa.get_default_output_device_info()["index"])
    except Exception:
        pass

    for index in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(index)
        inputs = int(info.get("maxInputChannels", 0))
        outputs = int(info.get("maxOutputChannels", 0))
        if inputs <= 0 and outputs <= 0:
            continue
        markers = []
        if index == default_input:
            markers.append("entrée défaut")
        if index == default_output:
            markers.append("sortie défaut")
        marker = f"  <= {', '.join(markers)}" if markers else ""
        print(
            f"  [{index}] {info.get('name', '?')} | entrées={inputs} | "
            f"sorties={outputs} | rate défaut={int(float(info.get('defaultSampleRate', 0)))} Hz"
            f"{marker}"
        )

    print("\nNœuds PipeWire disponibles:")
    if shutil.which("pw-dump") is None:
        print("  pw-dump indisponible")
        return

    try:
        process = subprocess.run(
            ["pw-dump"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
            timeout=2.0,
        )
        objects = json.loads(process.stdout) if process.returncode == 0 else []
    except Exception as exc:
        print(f"  impossible de lire PipeWire: {exc}")
        return

    found = False
    for item in objects if isinstance(objects, list) else []:
        if not isinstance(item, dict):
            continue
        info = item.get("info") if isinstance(item.get("info"), dict) else {}
        props = info.get("props") if isinstance(info.get("props"), dict) else {}
        media_class = str(props.get("media.class") or "").strip()
        if media_class not in {"Audio/Source", "Audio/Sink"}:
            continue
        node_name = str(props.get("node.name") or "").strip()
        if not node_name:
            continue
        description = str(
            props.get("node.description")
            or props.get("node.nick")
            or props.get("device.description")
            or node_name
        ).strip()
        kind = "source" if media_class == "Audio/Source" else "sink"
        print(f"  pipewire:{kind}:{node_name} | {description}")
        found = True
    if not found:
        print("  aucun nœud Audio/Source ou Audio/Sink trouvé")


def resolve_audio_config(
    pa: pyaudio.PyAudio,
    requested_device: Optional[str],
    preferred_rate: int,
) -> AudioConfig:
    pipewire_target = parse_pipewire_selector(requested_device, "source")
    if pipewire_target:
        if not pipewire_record_command():
            raise RuntimeError(
                "BACKEND_AUDIO_INPUT_DEVICE désigne une source PipeWire mais "
                "pw-cat/pw-record est indisponible."
            )
        return AudioConfig(
            requested_device=requested_device,
            pyaudio_device_index=None,
            pipewire_target=pipewire_target,
            device_name=f"PipeWire source: {pipewire_target}",
            sample_rate=preferred_rate,
        )

    if requested_device is None:
        try:
            info = pa.get_default_input_device_info()
        except Exception as exc:
            raise RuntimeError("Aucun périphérique d'entrée système par défaut.") from exc
        actual_index = int(info["index"])
    else:
        try:
            actual_index = int(requested_device.split(":", 1)[0])
            info = pa.get_device_info_by_index(actual_index)
        except Exception as exc:
            raise RuntimeError(
                "BACKEND_AUDIO_INPUT_DEVICE doit être un index PyAudio valide "
                "ou un sélecteur pipewire:source:..."
            ) from exc

    if int(info.get("maxInputChannels", 0)) < 1:
        raise RuntimeError(f"Le périphérique #{actual_index} n'a aucun canal d'entrée.")

    sample_rate = preferred_rate
    try:
        pa.is_format_supported(
            sample_rate,
            input_device=actual_index,
            input_channels=CHANNELS,
            input_format=FORMAT,
        )
    except Exception:
        sample_rate = int(round(float(info.get("defaultSampleRate", preferred_rate))))

    return AudioConfig(
        requested_device=requested_device,
        pyaudio_device_index=actual_index,
        pipewire_target=None,
        device_name=str(info.get("name", "?")),
        sample_rate=sample_rate,
    )


def resolve_playback_config(
    pa: pyaudio.PyAudio,
    requested_device: Optional[str],
) -> PlaybackConfig:
    pipewire_target = parse_pipewire_selector(requested_device, "sink")
    if pipewire_target:
        if shutil.which("pw-play") is None:
            raise RuntimeError(
                "BACKEND_AUDIO_OUTPUT_DEVICE désigne une sortie PipeWire mais pw-play est indisponible."
            )
        return PlaybackConfig(
            requested_device=requested_device,
            pyaudio_device_index=None,
            pipewire_target=pipewire_target,
            device_name=f"PipeWire sink: {pipewire_target}",
        )

    if requested_device is None:
        try:
            info = pa.get_default_output_device_info()
        except Exception as exc:
            raise RuntimeError("Aucun périphérique de sortie système par défaut.") from exc
        actual_index = int(info["index"])
    else:
        try:
            actual_index = int(requested_device.split(":", 1)[0])
            info = pa.get_device_info_by_index(actual_index)
        except Exception as exc:
            raise RuntimeError(
                "BACKEND_AUDIO_OUTPUT_DEVICE doit être un index PyAudio valide "
                "ou un sélecteur pipewire:sink:..."
            ) from exc

    if int(info.get("maxOutputChannels", 0)) < 1:
        raise RuntimeError(f"Le périphérique #{actual_index} n'a aucun canal de sortie.")

    return PlaybackConfig(
        requested_device=requested_device,
        pyaudio_device_index=actual_index,
        pipewire_target=None,
        device_name=str(info.get("name", "?")),
    )


def play_wav(pa: pyaudio.PyAudio, path: Path, playback: PlaybackConfig) -> None:
    if playback.pipewire_target:
        print(f"▶ PREVIEW sur {playback.device_name}")
        process = subprocess.run(
            ["pw-play", "--target", playback.pipewire_target, str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if process.returncode != 0:
            detail = (process.stderr or "").strip()
            raise RuntimeError(f"pw-play a échoué ({process.returncode}): {detail or 'erreur inconnue'}")
        return

    with wave.open(str(path), "rb") as wav:
        stream = pa.open(
            format=pa.get_format_from_width(wav.getsampwidth()),
            channels=wav.getnchannels(),
            rate=wav.getframerate(),
            output=True,
            output_device_index=playback.pyaudio_device_index,
            frames_per_buffer=CHUNK_FRAMES,
        )
        try:
            print(f"▶ PREVIEW sur [{playback.pyaudio_device_index}] {playback.device_name}")
            while True:
                data = wav.readframes(CHUNK_FRAMES)
                if not data:
                    break
                stream.write(data)
        finally:
            try:
                stream.stop_stream()
            finally:
                stream.close()


class PipeWireRecorderStream:
    def __init__(self, target: str, sample_rate: int):
        commands = [command for command in ("pw-cat", "pw-record") if shutil.which(command)]
        if not commands:
            raise RuntimeError("pw-cat ou pw-record est requis pour l'entrée PipeWire")

        base_args = [
            "--raw", "--target", target, "--format", "s16",
            "--rate", str(sample_rate), "--channels", str(CHANNELS), "-",
        ]
        self.process = None
        self.bytes_per_frame = CHANNELS * SAMPLE_WIDTH_BYTES

        for command in commands:
            args = [command, "--record", *base_args] if Path(command).name == "pw-cat" else [command, *base_args]
            process = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
            time.sleep(0.05)
            if process.poll() is None:
                self.process = process
                break
            try:
                process.kill()
                process.wait(timeout=1.0)
            except Exception:
                pass

        if self.process is None:
            raise RuntimeError(f"Capture PipeWire impossible pour '{target}'")

    def read(self, chunk: int, exception_on_overflow: bool = False) -> bytes:
        del exception_on_overflow
        if not self.process.stdout:
            raise RuntimeError("La capture PipeWire n'a pas de flux stdout")
        expected = max(1, int(chunk)) * self.bytes_per_frame
        parts: list[bytes] = []
        remaining = expected
        while remaining > 0:
            data = self.process.stdout.read(remaining)
            if not data:
                raise RuntimeError("La capture PipeWire s'est arrêtée")
            parts.append(data)
            remaining -= len(data)
        return b"".join(parts)

    def stop_stream(self) -> None:
        self.close()

    def close(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=1.0)
        if self.process and self.process.stdout:
            try:
                self.process.stdout.close()
            except Exception:
                pass


class Recorder:
    def __init__(self, pa: pyaudio.PyAudio, config: AudioConfig):
        self.pa = pa
        self.config = config
        self.frames: list[bytes] = []
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._stream = None
        self.error: Optional[BaseException] = None

    def start(self) -> None:
        self.frames = []
        self.error = None
        self._stop.clear()

        if self.config.pipewire_target:
            self._stream = PipeWireRecorderStream(self.config.pipewire_target, self.config.sample_rate)
        else:
            self._stream = self.pa.open(
                format=FORMAT,
                channels=CHANNELS,
                rate=self.config.sample_rate,
                input=True,
                input_device_index=self.config.pyaudio_device_index,
                frames_per_buffer=CHUNK_FRAMES,
            )

        def capture() -> None:
            try:
                while not self._stop.is_set():
                    data = self._stream.read(CHUNK_FRAMES, exception_on_overflow=False)
                    self.frames.append(data)
            except BaseException as exc:
                self.error = exc

        self._thread = threading.Thread(target=capture, daemon=True)
        self._thread.start()

    def stop(self) -> bytes:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        if self._stream is not None:
            try:
                self._stream.stop_stream()
            finally:
                self._stream.close()
            self._stream = None
        if self.error is not None:
            raise RuntimeError(f"Erreur pendant l'enregistrement audio: {self.error}")
        return b"".join(self.frames)


def write_wav(path: Path, pcm: bytes, sample_rate: int) -> float:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(CHANNELS)
        wav.setsampwidth(SAMPLE_WIDTH_BYTES)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    frame_count = len(pcm) // (CHANNELS * SAMPLE_WIDTH_BYTES)
    return frame_count / sample_rate if sample_rate else 0.0


def audio_metrics(pcm: bytes) -> dict[str, float]:
    if not pcm:
        return {"rms_dbfs": -120.0, "peak_dbfs": -120.0, "clipped_percent": 0.0}
    samples = array("h")
    samples.frombytes(pcm)
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        return {"rms_dbfs": -120.0, "peak_dbfs": -120.0, "clipped_percent": 0.0}

    peak = max(abs(int(v)) for v in samples)
    sum_sq = sum(int(v) * int(v) for v in samples)
    rms = math.sqrt(sum_sq / len(samples))
    clipped = sum(1 for v in samples if abs(int(v)) >= 32760)

    def to_dbfs(value: float) -> float:
        return -120.0 if value <= 0 else 20.0 * math.log10(value / 32768.0)

    return {
        "rms_dbfs": round(to_dbfs(rms), 2),
        "peak_dbfs": round(to_dbfs(peak), 2),
        "clipped_percent": round((clipped / len(samples)) * 100.0, 4),
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_audio_outlier(row: dict) -> bool:
    try:
        return (
            float(row.get("rms_dbfs", -120.0)) < -40.0
            or float(row.get("peak_dbfs", -120.0)) > -0.5
            or float(row.get("clipped_percent", 0.0)) > 0.01
        )
    except (TypeError, ValueError):
        return False


def write_manifests(
    output_dir: Path,
    rows: list[dict],
    env_path: Path,
    audio: AudioConfig,
    playback: PlaybackConfig,
    takes: int,
) -> None:
    metadata = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "LiveStageAssistant standalone STT benchmark corpus",
        "env_file_read_only": str(env_path),
        "backend_audio_input_device": audio.requested_device or "",
        "actual_input_device_index": audio.pyaudio_device_index,
        "input_pipewire_target": audio.pipewire_target,
        "input_device_name": audio.device_name,
        "backend_audio_output_device": playback.requested_device or "",
        "actual_output_device_index": playback.pyaudio_device_index,
        "output_pipewire_target": playback.pipewire_target,
        "output_device_name": playback.device_name,
        "sample_rate_hz": audio.sample_rate,
        "channels": CHANNELS,
        "sample_width_bits": SAMPLE_WIDTH_BYTES * 8,
        "audio_format": "WAV PCM signed 16-bit",
        "takes_per_phrase_requested": takes,
        "phrase_count": len(PHRASES),
        "recordings": rows,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    fields = [
        "phrase_id", "take", "file", "reference", "duration_seconds",
        "rms_dbfs", "peak_dbfs", "clipped_percent", "sha256",
    ]
    with (output_dir / "manifest.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def print_level_advice(metrics: dict[str, float]) -> None:
    if metrics["clipped_percent"] > 0.01 or metrics["peak_dbfs"] > -0.5:
        print("  Attention: niveau très fort / écrêtage possible.")
    elif metrics["rms_dbfs"] < -40.0:
        print("  Attention: signal assez faible; vérifie la distance ou le gain micro.")
    else:
        print("  Niveau audio: OK pour comparaison STT.")


def run_capture(args: argparse.Namespace, env_path: Path, env: dict[str, str]) -> int:
    pa = pyaudio.PyAudio()
    try:
        requested_device = configured_device_selector(env, "BACKEND_AUDIO_INPUT_DEVICE")
        requested_output_device = configured_device_selector(env, "BACKEND_AUDIO_OUTPUT_DEVICE")
        audio = resolve_audio_config(pa, requested_device, args.sample_rate)
        playback = resolve_playback_config(pa, requested_output_device)
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        total_expected = len(PHRASES) * args.takes

        print("\n=== LSA STT benchmark - capture autonome ===")
        print(f"Profil lu              : {env_path}")
        print(f"BACKEND_AUDIO_INPUT_DEVICE : {requested_device or '<défaut système>'}")
        print(f"Entrée réelle          : {audio.device_name}")
        print(f"BACKEND_AUDIO_OUTPUT_DEVICE: {requested_output_device or '<défaut système>'}")
        print(f"Sortie preview         : {playback.device_name}")
        print(f"Format WAV             : mono PCM 16 bits, {audio.sample_rate} Hz")
        print(f"Phrases                : {len(PHRASES)}")
        print(f"Prises / phrase        : {args.takes}")
        print(f"WAV attendus           : {total_expected}")
        print(f"Sortie                 : {output_dir.resolve()}")
        print("\nCommandes:")
        print("  Entrée = démarrer / arrêter l'enregistrement")
        print("  p/play = écouter le dernier enregistrement sur la sortie configurée")
        print("  r      = refaire la prise")
        print("  s      = sauter la prise")
        print("  q      = terminer proprement")
        print("\nLes anciennes prises portant le même nom seront remplacées.")
        print("Ne prononce pas 'momo': le wake word n'intervient pas dans ce corpus STT.\n")

        rows: list[dict] = []
        recorder = Recorder(pa, audio)

        for phrase_idx, phrase in enumerate(PHRASES, start=1):
            print("\n" + "=" * 78)
            print(f"PHRASE {phrase_idx:02d}/{len(PHRASES):02d}")
            print(f'Référence: "{phrase}"')

            for take in range(1, args.takes + 1):
                filename = f"{phrase_idx:02d}_take{take}.wav"
                wav_path = output_dir / filename

                while True:
                    print(f"\nPrise {take}/{args.takes} -> {filename}")
                    cmd = input("Entrée=enregistrer, s=sauter, q=quitter : ").strip().lower()
                    if cmd == "q":
                        write_manifests(output_dir, rows, env_path, audio, playback, args.takes)
                        return 0
                    if cmd == "s":
                        break
                    if cmd:
                        print("Commande inconnue.")
                        continue

                    print("● ENREGISTREMENT — parle, puis Entrée pour arrêter.")
                    recorder.start()
                    input()
                    pcm = recorder.stop()
                    if not pcm:
                        print("Aucun audio capturé; nouvelle tentative.")
                        continue

                    duration = write_wav(wav_path, pcm, audio.sample_rate)
                    metrics = audio_metrics(pcm)
                    checksum = sha256_file(wav_path)
                    print(
                        f"  Sauvegardé: {wav_path} | {duration:.2f} s | "
                        f"RMS {metrics['rms_dbfs']:.2f} dBFS | peak {metrics['peak_dbfs']:.2f} dBFS"
                    )
                    print_level_advice(metrics)

                    while True:
                        decision = input(
                            "Entrée=valider, p/play=écouter, r=refaire, q=quitter : "
                        ).strip().lower()
                        if decision in {"p", "play"}:
                            try:
                                play_wav(pa, wav_path, playback)
                            except Exception as exc:
                                print(f"Erreur preview audio: {exc}")
                            continue
                        if decision == "r":
                            try:
                                wav_path.unlink()
                            except FileNotFoundError:
                                pass
                            break
                        if decision in {"", "q"}:
                            break
                        print("Commande inconnue.")

                    if decision == "r":
                        continue

                    rows.append({
                        "phrase_id": phrase_idx,
                        "take": take,
                        "file": filename,
                        "reference": phrase,
                        "duration_seconds": round(duration, 3),
                        "rms_dbfs": metrics["rms_dbfs"],
                        "peak_dbfs": metrics["peak_dbfs"],
                        "clipped_percent": metrics["clipped_percent"],
                        "sha256": checksum,
                    })
                    write_manifests(output_dir, rows, env_path, audio, playback, args.takes)
                    if decision == "q":
                        return 0
                    break

        write_manifests(output_dir, rows, env_path, audio, playback, args.takes)
        print("\n=== CORPUS TERMINÉ ===")
        print(f"Enregistrements validés : {len(rows)}/{total_expected}")
        print(f"WAV                     : {output_dir.resolve()}")
        print(f"Manifest JSON           : {(output_dir / 'manifest.json').resolve()}")
        print(f"Manifest CSV            : {(output_dir / 'manifest.csv').resolve()}")
        return 0
    finally:
        pa.terminate()


def strip_accents(text: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(char)
    )


def normalize_text(text: str) -> str:
    value = strip_accents(str(text or "").casefold())
    value = value.replace("’", "'").replace("-", " ")
    value = re.sub(r"[^a-z0-9' ]+", " ", value)
    value = value.replace("'", " ")
    return " ".join(value.split())


def token_edit_distance(reference: list[str], hypothesis: list[str]) -> int:
    previous = list(range(len(hypothesis) + 1))
    for i, ref_token in enumerate(reference, start=1):
        current = [i]
        for j, hyp_token in enumerate(hypothesis, start=1):
            substitution = previous[j - 1] + (0 if ref_token == hyp_token else 1)
            insertion = current[j - 1] + 1
            deletion = previous[j] + 1
            current.append(min(substitution, insertion, deletion))
        previous = current
    return previous[-1]


def char_edit_distance(reference: str, hypothesis: str) -> int:
    return token_edit_distance(list(reference), list(hypothesis))


def aliases_normalized() -> dict[str, list[str]]:
    return {
        canonical: sorted({normalize_text(alias) for alias in aliases}, key=len, reverse=True)
        for canonical, aliases in ENTITY_ALIASES.items()
    }


NORMALIZED_ENTITY_ALIASES = aliases_normalized()


def expected_entities(reference: str) -> list[str]:
    normalized = normalize_text(reference)
    found: list[str] = []
    for canonical, aliases in NORMALIZED_ENTITY_ALIASES.items():
        if any(re.search(rf"\b{re.escape(alias)}\b", normalized) for alias in aliases):
            found.append(canonical)
    return found


def entity_recall(reference: str, hypothesis: str) -> tuple[int, int]:
    expected = expected_entities(reference)
    if not expected:
        return 0, 0
    normalized_hypothesis = normalize_text(hypothesis)
    matched = 0
    for canonical in expected:
        aliases = NORMALIZED_ENTITY_ALIASES[canonical]
        if any(re.search(rf"\b{re.escape(alias)}\b", normalized_hypothesis) for alias in aliases):
            matched += 1
    return matched, len(expected)


def phonetic_key(text: str) -> str:
    value = normalize_text(text)
    replacements = [
        ("eau", "o"), ("au", "o"), ("ph", "f"), ("qu", "k"),
        ("gu", "g"), ("ck", "k"), ("ch", "sh"), ("ou", "u"),
        ("ai", "e"), ("ei", "e"), ("er", "e"), ("ez", "e"),
    ]
    for source, target in replacements:
        value = value.replace(source, target)
    value = re.sub(r"([a-z])\1+", r"\1", value)
    return value


def similarity(left: str, right: str) -> float:
    lexical = SequenceMatcher(None, normalize_text(left), normalize_text(right)).ratio()
    phonetic = SequenceMatcher(None, phonetic_key(left), phonetic_key(right)).ratio()
    return max(lexical, phonetic)


def deterministic_entity_repair(text: str, threshold: float = 0.82) -> tuple[str, list[str]]:
    """
    Conservative benchmark-only repair.

    Exact known aliases are never rewritten. Otherwise a nearby 1..4-token
    window can be replaced only when one canonical entity is uniquely best,
    above threshold, and separated from the runner-up by >= 0.08.
    """
    normalized = normalize_text(text)
    repairs: list[str] = []

    tokens = normalized.split()

    # Protect the exact token spans of already-recognized aliases. The previous
    # implementation only protected the canonical candidate itself, which still
    # allowed "clode" inside the valid entity "guitar-clode" to be rewritten as
    # the distinct entity "Claude".
    protected_spans: list[tuple[int, int]] = []
    for aliases in NORMALIZED_ENTITY_ALIASES.values():
        for alias in aliases:
            alias_tokens = alias.split()
            width = len(alias_tokens)
            if not width:
                continue
            for start in range(0, len(tokens) - width + 1):
                if tokens[start:start + width] == alias_tokens:
                    protected_spans.append((start, start + width))

    def overlaps_protected(start: int, end: int) -> bool:
        return any(start < protected_end and end > protected_start
                   for protected_start, protected_end in protected_spans)

    i = 0
    while i < len(tokens):
        best = None
        second_score = 0.0
        max_window = min(4, len(tokens) - i)

        for width in range(1, max_window + 1):
            if overlaps_protected(i, i + width):
                continue
            candidate = " ".join(tokens[i:i + width])
            scored = []
            for canonical, aliases in NORMALIZED_ENTITY_ALIASES.items():
                score = max(similarity(candidate, alias) for alias in aliases)
                scored.append((score, canonical, width, candidate))
            scored.sort(reverse=True)
            if scored:
                top = scored[0]
                runner = scored[1][0] if len(scored) > 1 else 0.0
                if best is None or top[0] > best[0]:
                    best = top
                    second_score = runner

        if best and best[0] >= threshold and best[0] - second_score >= 0.08:
            score, canonical, width, candidate = best
            replacement = normalize_text(canonical)
            tokens[i:i + width] = replacement.split()
            repairs.append(f"{candidate}->{canonical} ({score:.2f})")
            i += len(replacement.split())
        else:
            i += 1

    return " ".join(tokens), repairs


def load_manifest(output_dir: Path) -> tuple[dict, list[dict]]:
    manifest_path = output_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(
            f"Manifest introuvable: {manifest_path}. Lance d'abord le mode enregistrement."
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows = manifest.get("recordings") or []
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("Le manifest ne contient aucun enregistrement.")

    missing = [
        str(output_dir / str(row.get("file", "")))
        for row in rows
        if not (output_dir / str(row.get("file", ""))).is_file()
    ]
    if missing:
        raise RuntimeError("WAV manquants:\n  " + "\n  ".join(missing))
    return manifest, rows


def hotwords_string() -> str:
    seen = set()
    words = []
    for item in HOTWORD_CORE:
        key = item.casefold().strip()
        if key and key not in seen:
            seen.add(key)
            words.append(item.strip())
    return ", ".join(words)


def benchmark_variants(env: dict[str, str]) -> list[BenchmarkVariant]:
    # Model names in variant labels are intentional and must never inherit the
    # profile's LOCAL_WHISPER_MODEL. The first benchmark accidentally did so,
    # causing every "base_*" variant to run with "small" when the Raspberry
    # profile selected small.
    return [
        BenchmarkVariant("tiny_current", "tiny", True, True),
        BenchmarkVariant("base_plain", "base", False, False),
        BenchmarkVariant("base_prompt", "base", True, False),
        BenchmarkVariant("base_hotwords", "base", False, True),
        BenchmarkVariant("base_current", "base", True, True),
        BenchmarkVariant("base_current_repair", "base", True, True, True, "base_current"),
        BenchmarkVariant("small_current", "small", True, True),
        BenchmarkVariant("small_current_repair", "small", True, True, True, "small_current"),
    ]


def percentile95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1))
    return ordered[index]


def score_transcription(
    variant: BenchmarkVariant,
    row: dict,
    transcription: str,
    latency_seconds: float,
    repairs: Optional[list[str]] = None,
) -> dict:
    reference = str(row.get("reference") or "")
    ref_norm = normalize_text(reference)
    hyp_norm = normalize_text(transcription)
    ref_tokens = ref_norm.split()
    hyp_tokens = hyp_norm.split()
    edits = token_edit_distance(ref_tokens, hyp_tokens)
    char_edits = char_edit_distance(ref_norm, hyp_norm)
    entity_ok, entity_total = entity_recall(reference, transcription)
    duration = float(row.get("duration_seconds") or 0.0)
    return {
        "variant": variant.name,
        "model": variant.model_name,
        "prompt": variant.use_prompt,
        "hotwords": variant.use_hotwords,
        "repair": variant.apply_repair,
        "phrase_id": row.get("phrase_id"),
        "take": row.get("take"),
        "file": row.get("file"),
        "reference": reference,
        "transcription": transcription,
        "normalized_reference": ref_norm,
        "normalized_transcription": hyp_norm,
        "exact": ref_norm == hyp_norm,
        "word_edits": edits,
        "reference_words": len(ref_tokens),
        "wer": edits / max(1, len(ref_tokens)),
        "char_edits": char_edits,
        "reference_chars": len(ref_norm),
        "cer": char_edits / max(1, len(ref_norm)),
        "entity_matched": entity_ok,
        "entity_expected": entity_total,
        "entity_recall": (entity_ok / entity_total) if entity_total else 1.0,
        "latency_seconds": round(latency_seconds, 4),
        "audio_seconds": duration,
        "rtf": round(latency_seconds / duration, 4) if duration > 0 else None,
        "audio_outlier": is_audio_outlier(row),
        "repairs": "; ".join(repairs or []),
        "error": "",
    }


def error_result(variant: BenchmarkVariant, row: dict, error: Exception | str) -> dict:
    result = score_transcription(variant, row, "", 0.0)
    result["error"] = str(error)
    return result


def summarize_results(results: list[dict], variant_name: str, clean_only: bool = False) -> dict:
    subset = [
        row for row in results
        if row["variant"] == variant_name
        and not row.get("error")
        and (not clean_only or not row.get("audio_outlier"))
    ]
    if not subset:
        return {
            "variant": variant_name,
            "scope": "clean" if clean_only else "all",
            "samples": 0,
            "exact_percent": 0.0,
            "wer_percent": 0.0,
            "cer_percent": 0.0,
            "entity_recall_percent": 0.0,
            "mean_latency_seconds": 0.0,
            "p95_latency_seconds": 0.0,
            "mean_rtf": 0.0,
        }

    total_words = sum(int(row["reference_words"]) for row in subset)
    total_edits = sum(int(row["word_edits"]) for row in subset)
    total_chars = sum(int(row["reference_chars"]) for row in subset)
    total_char_edits = sum(int(row["char_edits"]) for row in subset)
    total_entities = sum(int(row["entity_expected"]) for row in subset)
    matched_entities = sum(int(row["entity_matched"]) for row in subset)
    latencies = [float(row["latency_seconds"]) for row in subset]
    rtfs = [float(row["rtf"]) for row in subset if row["rtf"] is not None]

    return {
        "variant": variant_name,
        "scope": "clean" if clean_only else "all",
        "samples": len(subset),
        "exact_percent": round(100.0 * sum(bool(row["exact"]) for row in subset) / len(subset), 2),
        "wer_percent": round(100.0 * total_edits / max(1, total_words), 2),
        "cer_percent": round(100.0 * total_char_edits / max(1, total_chars), 2),
        "entity_recall_percent": round(
            100.0 * matched_entities / max(1, total_entities), 2
        ) if total_entities else 100.0,
        "mean_latency_seconds": round(sum(latencies) / len(latencies), 3),
        "p95_latency_seconds": round(percentile95(latencies), 3),
        "mean_rtf": round(sum(rtfs) / len(rtfs), 3) if rtfs else 0.0,
    }


def write_benchmark_reports(
    output_dir: Path,
    env_path: Path,
    manifest: dict,
    variants: list[BenchmarkVariant],
    results: list[dict],
    model_load_seconds: dict[str, float],
    variant_errors: dict[str, str],
) -> None:
    timestamp = datetime.now(timezone.utc).isoformat()
    variant_names = [variant.name for variant in variants]
    summaries = []
    for name in variant_names:
        summaries.append(summarize_results(results, name, clean_only=False))
        summaries.append(summarize_results(results, name, clean_only=True))

    json_payload = {
        "created_at_utc": timestamp,
        "env_file": str(env_path),
        "corpus_manifest_created_at_utc": manifest.get("created_at_utc"),
        "sample_count": len(manifest.get("recordings") or []),
        "outliers_kept": [
            row.get("file") for row in (manifest.get("recordings") or []) if is_audio_outlier(row)
        ],
        "model_load_seconds": model_load_seconds,
        "variant_errors": variant_errors,
        "variants": [variant.__dict__ for variant in variants],
        "summaries": summaries,
        "results": results,
    }

    json_path = output_dir / "benchmark_results.json"
    csv_path = output_dir / "benchmark_results.csv"
    md_path = output_dir / "benchmark_report.md"

    json_path.write_text(json.dumps(json_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    fields = [
        "variant", "model", "prompt", "hotwords", "repair", "phrase_id", "take", "file",
        "reference", "transcription", "exact", "wer", "cer", "entity_recall",
        "latency_seconds", "audio_seconds", "rtf", "audio_outlier", "repairs", "error",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)

    outliers = json_payload["outliers_kept"]
    lines = [
        "# Rapport benchmark STT LiveStageAssistant",
        "",
        f"- Date UTC: {timestamp}",
        f"- Profil: `{env_path}`",
        f"- Échantillons: **{json_payload['sample_count']}**",
        f"- Échantillons atypiques conservés: **{len(outliers)}**"
        + (f" ({', '.join(outliers)})" if outliers else ""),
        "- Mesures: exact normalisé, WER, CER, rappel des entités, latence et RTF.",
        "- Le benchmark est autonome: aucun agent LSA ni MCP n'est lancé.",
        "",
        "## Résumé — tous les échantillons",
        "",
        "| Variante | N | Exact | WER | CER | Entités | Latence moy. | P95 | RTF moy. |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]

    all_summaries = {row["variant"]: row for row in summaries if row["scope"] == "all"}
    clean_summaries = {row["variant"]: row for row in summaries if row["scope"] == "clean"}

    for name in variant_names:
        row = all_summaries[name]
        if row["samples"] == 0:
            error = variant_errors.get(name, "non exécuté")
            lines.append(f"| {name} | 0 | — | — | — | — | — | — | — |")
            if error:
                lines.append(f"\n> {name}: {error}\n")
            continue
        lines.append(
            f"| {name} | {row['samples']} | {row['exact_percent']:.2f}% | "
            f"{row['wer_percent']:.2f}% | {row['cer_percent']:.2f}% | "
            f"{row['entity_recall_percent']:.2f}% | {row['mean_latency_seconds']:.3f}s | "
            f"{row['p95_latency_seconds']:.3f}s | {row['mean_rtf']:.3f} |"
        )

    lines += [
        "",
        "## Résumé — hors échantillons audio atypiques",
        "",
        "| Variante | N | Exact | WER | CER | Entités | Latence moy. | P95 | RTF moy. |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name in variant_names:
        row = clean_summaries[name]
        if row["samples"] == 0:
            lines.append(f"| {name} | 0 | — | — | — | — | — | — | — |")
            continue
        lines.append(
            f"| {name} | {row['samples']} | {row['exact_percent']:.2f}% | "
            f"{row['wer_percent']:.2f}% | {row['cer_percent']:.2f}% | "
            f"{row['entity_recall_percent']:.2f}% | {row['mean_latency_seconds']:.3f}s | "
            f"{row['p95_latency_seconds']:.3f}s | {row['mean_rtf']:.3f} |"
        )

    lines += ["", "## Temps de chargement des modèles", ""]
    for model_name, seconds in model_load_seconds.items():
        lines.append(f"- `{model_name}`: {seconds:.3f} s")

    successful = [row for row in all_summaries.values() if row["samples"] > 0]
    if successful:
        highest_exact = max(successful, key=lambda row: (row["exact_percent"], -row["wer_percent"]))
        lowest_wer = min(successful, key=lambda row: (row["wer_percent"], -row["exact_percent"]))
        fastest = min(successful, key=lambda row: row["mean_latency_seconds"])
        lines += [
            "",
            "## Repères factuels",
            "",
            f"- Exact normalisé le plus élevé: **{highest_exact['variant']}** "
            f"({highest_exact['exact_percent']:.2f}%).",
            f"- WER le plus faible: **{lowest_wer['variant']}** ({lowest_wer['wer_percent']:.2f}%).",
            f"- Latence moyenne la plus faible: **{fastest['variant']}** "
            f"({fastest['mean_latency_seconds']:.3f} s).",
        ]

    lines += [
        "",
        "## Détail des erreurs",
        "",
        "| Variante | Fichier | Référence | Transcription | WER | Outlier | Réparations |",
        "|---|---|---|---|---:|---|---|",
    ]
    for row in results:
        if row.get("error") or not row.get("exact"):
            transcription = str(row.get("transcription") or row.get("error") or "").replace("|", "\\|")
            reference = str(row.get("reference") or "").replace("|", "\\|")
            repairs = str(row.get("repairs") or "").replace("|", "\\|")
            lines.append(
                f"| {row['variant']} | {row['file']} | {reference} | {transcription} | "
                f"{float(row['wer']) * 100:.1f}% | {'oui' if row['audio_outlier'] else 'non'} | {repairs} |"
            )

    lines += [
        "",
        "## Limites",
        "",
        "- Le succès du parseur MCP n'est pas mesuré ici: ce benchmark isole volontairement le STT.",
        "- `base_current` signifie modèle Faster-Whisper base + réglages runtime "
        "(int8 CPU, beam=1, prompt + hotwords); `small_current` applique les mêmes "
        "réglages au modèle small. Le modèle réellement configuré dans le profil est "
        "affiché séparément au lancement.",
        "- Les hotwords MCP dynamiques du runtime ne sont pas interrogés.",
        "- La réparation déterministe est évaluée séparément et ne modifie jamais les WAV.",
        "- Canary/Zipformer/whisper.cpp ne sont pas installés automatiquement: le script ne modifie pas "
        "les dépendances du Raspberry pendant une mesure.",
        "",
    ]
    md_path.write_text("\n".join(lines), encoding="utf-8")


def run_benchmark(args: argparse.Namespace, env_path: Path, env: dict[str, str]) -> int:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "faster-whisper n'est pas installé dans ce venv; impossible de lancer le benchmark."
        ) from exc

    output_dir = Path(args.output_dir)
    manifest, rows = load_manifest(output_dir)
    repo_root = Path.cwd()
    stt_prompt = resolve_text_setting(env.get("STT_PROMPT", ""), repo_root)
    language = (env.get("STT_LANGUAGE") or "fr").strip() or "fr"
    hotwords = hotwords_string()
    variants = benchmark_variants(env)

    print("\n=== LSA STT BENCHMARK ===")
    print(f"Profil                 : {env_path}")
    print(f"Corpus                 : {output_dir.resolve()}")
    print(f"Échantillons           : {len(rows)}")
    outliers = [row.get("file") for row in rows if is_audio_outlier(row)]
    print(f"Outliers conservés     : {len(outliers)}" + (f" ({', '.join(outliers)})" if outliers else ""))
    print(f"Langue                 : {language}")
    print(f"Prompt STT             : {'oui' if stt_prompt else 'non'}")
    print(f"Hotwords               : {hotwords}")
    print(f"Modèle configuré LSA   : {(env.get('LOCAL_WHISPER_MODEL') or 'base').strip() or 'base'}")
    print("\nVariantes:")
    for variant in variants:
        source = f" (dérivée de {variant.derived_from})" if variant.derived_from else ""
        print(
            f"  - {variant.name}: model={variant.model_name}, prompt={variant.use_prompt}, "
            f"hotwords={variant.use_hotwords}, repair={variant.apply_repair}{source}"
        )
    print("\nLes 36 WAV restent inchangés. Le benchmark peut télécharger les modèles "
          "'tiny', 'base' ou 'small' absents du cache Faster-Whisper.\n")

    results: list[dict] = []
    model_load_seconds: dict[str, float] = {}
    variant_errors: dict[str, str] = {}
    raw_cache: dict[tuple[str, str], dict] = {}

    direct_variants = [variant for variant in variants if not variant.apply_repair]
    model_names = []
    for variant in direct_variants:
        if variant.model_name not in model_names:
            model_names.append(variant.model_name)

    cpu_threads = max(1, min(4, os.cpu_count() or 1))

    for model_name in model_names:
        print(f"\n--- Chargement Faster-Whisper {model_name} (CPU int8, threads={cpu_threads}) ---")
        try:
            started = time.perf_counter()
            model = WhisperModel(
                model_name,
                device="cpu",
                compute_type="int8",
                cpu_threads=cpu_threads,
                num_workers=1,
            )
            model_load_seconds[model_name] = round(time.perf_counter() - started, 3)
        except Exception as exc:
            message = f"chargement modèle impossible: {exc}"
            for variant in direct_variants:
                if variant.model_name == model_name:
                    variant_errors[variant.name] = message
                    for row in rows:
                        results.append(error_result(variant, row, message))
            print(f"ERREUR: {message}")
            continue

        for variant in [v for v in direct_variants if v.model_name == model_name]:
            print(f"\n[{variant.name}]")
            for index, row in enumerate(rows, start=1):
                wav_path = output_dir / str(row["file"])
                kwargs = {
                    "language": language,
                    "beam_size": 1,
                    "best_of": 1,
                    "temperature": 0.0,
                    "condition_on_previous_text": False,
                    "without_timestamps": True,
                    "vad_filter": False,
                }
                if variant.use_prompt and stt_prompt:
                    kwargs["initial_prompt"] = stt_prompt
                if variant.use_hotwords:
                    kwargs["hotwords"] = hotwords

                try:
                    started = time.perf_counter()
                    segments, _info = model.transcribe(str(wav_path), **kwargs)
                    transcription = "".join(segment.text for segment in segments).strip()
                    latency = time.perf_counter() - started
                    result = score_transcription(variant, row, transcription, latency)
                    results.append(result)
                    raw_cache[(variant.name, str(row["file"]))] = result
                    marker = "✓" if result["exact"] else "·"
                    print(
                        f"  {index:02d}/{len(rows)} {marker} {row['file']} "
                        f"{latency:.2f}s WER={result['wer'] * 100:.1f}% -> {transcription!r}"
                    )
                except Exception as exc:
                    result = error_result(variant, row, exc)
                    results.append(result)
                    raw_cache[(variant.name, str(row["file"]))] = result
                    print(f"  {index:02d}/{len(rows)} ERREUR {row['file']}: {exc}")

        del model
        gc.collect()

    # Derived repair variants reuse exactly the raw transcription from their source
    # variant, so they do not spend a second inference pass.
    for variant in [v for v in variants if v.apply_repair]:
        source_name = str(variant.derived_from or "")
        print(f"\n[{variant.name}] réparation déterministe de {source_name}")
        for row in rows:
            source = raw_cache.get((source_name, str(row["file"])))
            if not source or source.get("error"):
                message = source.get("error") if source else f"résultat source absent: {source_name}"
                results.append(error_result(variant, row, message))
                continue
            repair_started = time.perf_counter()
            repaired, repairs = deterministic_entity_repair(str(source["transcription"]))
            repair_latency = time.perf_counter() - repair_started
            result = score_transcription(
                variant,
                row,
                repaired,
                float(source["latency_seconds"]) + repair_latency,
                repairs,
            )
            results.append(result)

    write_benchmark_reports(
        output_dir,
        env_path,
        manifest,
        variants,
        results,
        model_load_seconds,
        variant_errors,
    )

    print("\n=== BENCHMARK TERMINÉ ===")
    print(f"Rapport Markdown : {(output_dir / 'benchmark_report.md').resolve()}")
    print(f"Résultats CSV     : {(output_dir / 'benchmark_results.csv').resolve()}")
    print(f"Résultats JSON    : {(output_dir / 'benchmark_results.json').resolve()}")

    for variant in variants:
        summary = summarize_results(results, variant.name, clean_only=False)
        if summary["samples"]:
            print(
                f"{variant.name:24s} exact={summary['exact_percent']:6.2f}% "
                f"WER={summary['wer_percent']:6.2f}% "
                f"entities={summary['entity_recall_percent']:6.2f}% "
                f"lat={summary['mean_latency_seconds']:.3f}s "
                f"RTF={summary['mean_rtf']:.3f}"
            )
        else:
            print(f"{variant.name:24s} NON EXÉCUTÉ: {variant_errors.get(variant.name, 'source indisponible')}")
    return 0


def choose_mode(output_dir: Path) -> str:
    manifest_exists = (output_dir / "manifest.json").is_file()
    wav_count = len(list(output_dir.glob("*.wav"))) if output_dir.is_dir() else 0

    print("\n=== LSA STT corpus / benchmark ===")
    if manifest_exists:
        print(f"Corpus existant détecté: {wav_count} WAV dans {output_dir}")
    else:
        print(f"Aucun corpus complet détecté dans {output_dir}")

    print("\nQue veux-tu faire ?")
    print("  1 = Enregistrer / remplacer les échantillons")
    print("  2 = Benchmarker les échantillons actuellement enregistrés")
    print("  q = Quitter")

    while True:
        choice = input("Choix [1/2/q] : ").strip().lower()
        if choice in {"1", "record", "capture"}:
            return "capture"
        if choice in {"2", "bench", "benchmark"}:
            return "benchmark"
        if choice in {"q", "quit"}:
            return "quit"
        print("Choix invalide.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Enregistre ou benchmarke le corpus STT autonome de LiveStageAssistant."
    )
    parser.add_argument(
        "--env-file",
        default=DEFAULT_ENV_FILE,
        help=f"Profil Raspberry lu en lecture seule (défaut: {DEFAULT_ENV_FILE}).",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"Dossier corpus/rapports (défaut: {DEFAULT_OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--takes",
        type=int,
        default=DEFAULT_TAKES,
        help="Nombre de prises par phrase en mode capture (défaut: 3).",
    )
    parser.add_argument(
        "--sample-rate",
        type=int,
        default=DEFAULT_SAMPLE_RATE,
        help="Fréquence de capture souhaitée en Hz (défaut: 16000).",
    )
    parser.add_argument(
        "--mode",
        choices=["capture", "benchmark"],
        help="Évite le menu interactif et lance directement le mode demandé.",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="Affiche les périphériques PyAudio/PipeWire puis quitte.",
    )
    args = parser.parse_args()

    if args.takes < 1:
        parser.error("--takes doit être >= 1")

    if args.list_devices:
        pa = pyaudio.PyAudio()
        try:
            list_audio_devices(pa)
            return 0
        finally:
            pa.terminate()

    try:
        env_path = Path(args.env_file)
        env = parse_env_file(env_path)
        mode = args.mode or choose_mode(Path(args.output_dir))
        if mode == "quit":
            return 0
        if mode == "capture":
            return run_capture(args, env_path, env)
        return run_benchmark(args, env_path, env)

    except KeyboardInterrupt:
        print("\nInterrompu par l'utilisateur.")
        return 130
    except Exception as exc:
        print(f"\nErreur: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
