#!/usr/bin/env python3
"""
Standalone STT benchmark corpus recorder for LiveStageAssistant.

Purpose
-------
Create a reproducible speech corpus without importing or modifying LSA.

The script:
- reads ONLY the selected env file (default: .env.offline);
- reuses BACKEND_AUDIO_INPUT_DEVICE for capture;
- reuses BACKEND_AUDIO_OUTPUT_DEVICE for recording preview;
- uses the corresponding system default device when either variable is empty;
- records 3 takes per sentence by default;
- writes mono PCM WAV files;
- writes manifest.json and manifest.csv with reference text and audio metadata;
- does NOT import voice_assistant, start the agent, call MCPs, or alter any LSA file.

Typical usage on the Raspberry Pi:
    .venv/bin/python scripts/stt_benchmark_capture.py

List available PyAudio input devices:
    .venv/bin/python scripts/stt_benchmark_capture.py --list-devices

Change the number of takes:
    .venv/bin/python scripts/stt_benchmark_capture.py --takes 3

Use another env profile without changing LSA:
    .venv/bin/python scripts/stt_benchmark_capture.py --env-file .env.offline
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import shlex
import sys
import threading
import wave
from array import array
from dataclasses import dataclass
from datetime import datetime, timezone
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


DEFAULT_SAMPLE_RATE = 16000
CHANNELS = 1
FORMAT = pyaudio.paInt16
SAMPLE_WIDTH_BYTES = 2
CHUNK_FRAMES = 1024
DEFAULT_TAKES = 3

# Corpus volontairement orienté vers les difficultés STT LSA:
# noms propres / noms de tranches, nombres, dB, routage et formulations proches.
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


@dataclass
class AudioConfig:
    requested_device_index: Optional[int]
    actual_device_index: int
    device_name: str
    sample_rate: int


@dataclass
class PlaybackConfig:
    requested_device_index: Optional[int]
    actual_device_index: int
    device_name: str


def parse_env_file(path: Path) -> dict[str, str]:
    """
    Parse the dotenv file as text.

    Important: the file is NOT sourced/executed, so no shell command from it
    can run. We only read BACKEND_AUDIO_INPUT_DEVICE and
    BACKEND_AUDIO_OUTPUT_DEVICE.
    """
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


def configured_device_index(env: dict[str, str], key: str) -> Optional[int]:
    raw = env.get(key, "").strip()
    if not raw:
        return None

    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(
            f"{key} doit être vide ou contenir un index PyAudio entier; "
            f"valeur trouvée: {raw!r}"
        ) from exc


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
            f"  [{index}] {info.get('name', '?')} | "
            f"entrées={inputs} | sorties={outputs} | "
            f"rate défaut={int(float(info.get('defaultSampleRate', 0)))} Hz"
            f"{marker}"
        )

    print()


def resolve_audio_config(
    pa: pyaudio.PyAudio,
    requested_index: Optional[int],
    preferred_rate: int,
) -> AudioConfig:
    if requested_index is None:
        try:
            info = pa.get_default_input_device_info()
        except Exception as exc:
            raise RuntimeError(
                "Aucun périphérique d'entrée système par défaut n'est disponible. "
                "Utilise --list-devices puis renseigne BACKEND_AUDIO_INPUT_DEVICE "
                "dans .env.offline si nécessaire."
            ) from exc
        actual_index = int(info["index"])
    else:
        actual_index = requested_index
        try:
            info = pa.get_device_info_by_index(actual_index)
        except Exception as exc:
            raise RuntimeError(
                f"Le périphérique PyAudio #{actual_index} indiqué dans "
                "BACKEND_AUDIO_INPUT_DEVICE est introuvable."
            ) from exc

    if int(info.get("maxInputChannels", 0)) < 1:
        raise RuntimeError(
            f"Le périphérique #{actual_index} ({info.get('name', '?')}) "
            "ne possède aucun canal d'entrée."
        )

    # 16 kHz is ideal for a portable ASR benchmark corpus. If the device
    # refuses it, keep recording possible by falling back to its native rate.
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
        requested_device_index=requested_index,
        actual_device_index=actual_index,
        device_name=str(info.get("name", "?")),
        sample_rate=sample_rate,
    )


def resolve_playback_config(
    pa: pyaudio.PyAudio,
    requested_index: Optional[int],
) -> PlaybackConfig:
    if requested_index is None:
        try:
            info = pa.get_default_output_device_info()
        except Exception as exc:
            raise RuntimeError(
                "Aucun périphérique de sortie système par défaut n'est disponible. "
                "Utilise --list-devices puis renseigne BACKEND_AUDIO_OUTPUT_DEVICE "
                "dans .env.offline si nécessaire."
            ) from exc
        actual_index = int(info["index"])
    else:
        actual_index = requested_index
        try:
            info = pa.get_device_info_by_index(actual_index)
        except Exception as exc:
            raise RuntimeError(
                f"Le périphérique PyAudio #{actual_index} indiqué dans "
                "BACKEND_AUDIO_OUTPUT_DEVICE est introuvable."
            ) from exc

    if int(info.get("maxOutputChannels", 0)) < 1:
        raise RuntimeError(
            f"Le périphérique #{actual_index} ({info.get('name', '?')}) "
            "ne possède aucun canal de sortie."
        )

    return PlaybackConfig(
        requested_device_index=requested_index,
        actual_device_index=actual_index,
        device_name=str(info.get("name", "?")),
    )


def play_wav(
    pa: pyaudio.PyAudio,
    path: Path,
    playback: PlaybackConfig,
) -> None:
    with wave.open(str(path), "rb") as wav:
        stream = pa.open(
            format=pa.get_format_from_width(wav.getsampwidth()),
            channels=wav.getnchannels(),
            rate=wav.getframerate(),
            output=True,
            output_device_index=playback.actual_device_index,
            frames_per_buffer=CHUNK_FRAMES,
        )
        try:
            print(
                f"▶ PREVIEW sur [{playback.actual_device_index}] "
                f"{playback.device_name}"
            )
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

        self._stream = self.pa.open(
            format=FORMAT,
            channels=CHANNELS,
            rate=self.config.sample_rate,
            input=True,
            input_device_index=self.config.actual_device_index,
            frames_per_buffer=CHUNK_FRAMES,
        )

        def capture() -> None:
            try:
                while not self._stop.is_set():
                    data = self._stream.read(
                        CHUNK_FRAMES,
                        exception_on_overflow=False,
                    )
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
    """
    Basic quality indicators, calculated from signed 16-bit mono PCM.
    They are not used to reject a take automatically.
    """
    if not pcm:
        return {
            "rms_dbfs": -120.0,
            "peak_dbfs": -120.0,
            "clipped_percent": 0.0,
        }

    samples = array("h")
    samples.frombytes(pcm)

    # WAV/PyAudio data on the Raspberry Pi is little-endian.
    if sys.byteorder != "little":
        samples.byteswap()

    if not samples:
        return {
            "rms_dbfs": -120.0,
            "peak_dbfs": -120.0,
            "clipped_percent": 0.0,
        }

    peak = max(abs(int(v)) for v in samples)
    sum_sq = sum(int(v) * int(v) for v in samples)
    rms = math.sqrt(sum_sq / len(samples))

    def to_dbfs(value: float) -> float:
        if value <= 0:
            return -120.0
        return 20.0 * math.log10(value / 32768.0)

    clipped = sum(1 for v in samples if abs(int(v)) >= 32760)

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
        "backend_audio_input_device": (
            "" if audio.requested_device_index is None else str(audio.requested_device_index)
        ),
        "actual_input_device_index": audio.actual_device_index,
        "input_device_name": audio.device_name,
        "backend_audio_output_device": (
            "" if playback.requested_device_index is None else str(playback.requested_device_index)
        ),
        "actual_output_device_index": playback.actual_device_index,
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
        "phrase_id",
        "take",
        "file",
        "reference",
        "duration_seconds",
        "rms_dbfs",
        "peak_dbfs",
        "clipped_percent",
        "sha256",
    ]

    with (output_dir / "manifest.csv").open(
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def print_level_advice(metrics: dict[str, float]) -> None:
    rms = metrics["rms_dbfs"]
    peak = metrics["peak_dbfs"]
    clipped = metrics["clipped_percent"]

    if clipped > 0.01 or peak > -0.5:
        print("  Attention: niveau très fort / écrêtage possible.")
    elif rms < -40.0:
        print("  Attention: signal assez faible; vérifie la distance ou le gain micro.")
    else:
        print("  Niveau audio: OK pour comparaison STT.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Enregistre de façon autonome un corpus WAV multi-prises "
            "pour benchmark STT de LiveStageAssistant."
        )
    )

    parser.add_argument(
        "--env-file",
        default=".env.offline",
        help="Profil lu uniquement pour le périphérique micro (défaut: .env.offline).",
    )
    parser.add_argument(
        "--output-dir",
        default="recordings/stt_benchmark_audio",
        help="Dossier de sortie (défaut: recordings/stt_benchmark_audio).",
    )
    parser.add_argument(
        "--takes",
        type=int,
        default=DEFAULT_TAKES,
        help="Nombre de prises par phrase (défaut: 3).",
    )
    parser.add_argument(
        "--sample-rate",
        type=int,
        default=DEFAULT_SAMPLE_RATE,
        help=(
            "Fréquence souhaitée en Hz; fallback automatique sur le rate natif "
            "du périphérique (défaut: 16000)."
        ),
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="Affiche les périphériques d'entrée/sortie PyAudio puis quitte.",
    )

    args = parser.parse_args()

    if args.takes < 1:
        parser.error("--takes doit être >= 1")

    pa = pyaudio.PyAudio()

    try:
        if args.list_devices:
            list_audio_devices(pa)
            return 0

        env_path = Path(args.env_file)
        env = parse_env_file(env_path)
        requested_device = configured_device_index(env, "BACKEND_AUDIO_INPUT_DEVICE")
        requested_output_device = configured_device_index(env, "BACKEND_AUDIO_OUTPUT_DEVICE")
        audio = resolve_audio_config(pa, requested_device, args.sample_rate)
        playback = resolve_playback_config(pa, requested_output_device)

        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        total_expected = len(PHRASES) * args.takes

        print("\n=== LSA STT benchmark - capture autonome ===")
        print(f"Profil lu             : {env_path}")
        print(
            "BACKEND_AUDIO_INPUT_DEVICE : "
            + (
                "<vide = périphérique système par défaut>"
                if requested_device is None
                else str(requested_device)
            )
        )
        print(f"Entrée réelle          : [{audio.actual_device_index}] {audio.device_name}")
        print(
            "BACKEND_AUDIO_OUTPUT_DEVICE: "
            + (
                "<vide = périphérique système par défaut>"
                if requested_output_device is None
                else str(requested_output_device)
            )
        )
        print(
            f"Sortie preview         : [{playback.actual_device_index}] "
            f"{playback.device_name}"
        )
        print(f"Format WAV             : mono PCM 16 bits, {audio.sample_rate} Hz")
        print(f"Phrases               : {len(PHRASES)}")
        print(f"Prises / phrase       : {args.takes}")
        print(f"WAV attendus          : {total_expected}")
        print(f"Sortie                 : {output_dir.resolve()}")

        if audio.sample_rate != args.sample_rate:
            print(
                f"NOTE: le micro n'accepte pas {args.sample_rate} Hz directement; "
                f"capture à {audio.sample_rate} Hz."
            )

        print("\nCommandes:")
        print("  Entrée = démarrer / arrêter l'enregistrement")
        print("  p/play = écouter le dernier enregistrement sur la sortie configurée")
        print("  r      = refaire la prise")
        print("  s      = sauter la prise")
        print("  q      = terminer proprement")
        print("\nNe prononce pas 'momo' sauf si tu veux explicitement le tester:")
        print("le wake word n'intervient pas dans ce corpus STT.\n")

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
                        print("\nCapture interrompue proprement.")
                        print(f"Manifest: {output_dir / 'manifest.json'}")
                        return 0

                    if cmd == "s":
                        print("Prise sautée.")
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
                        f"RMS {metrics['rms_dbfs']:.2f} dBFS | "
                        f"peak {metrics['peak_dbfs']:.2f} dBFS"
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

                    row = {
                        "phrase_id": phrase_idx,
                        "take": take,
                        "file": filename,
                        "reference": phrase,
                        "duration_seconds": round(duration, 3),
                        "rms_dbfs": metrics["rms_dbfs"],
                        "peak_dbfs": metrics["peak_dbfs"],
                        "clipped_percent": metrics["clipped_percent"],
                        "sha256": checksum,
                    }
                    rows.append(row)

                    write_manifests(output_dir, rows, env_path, audio, playback, args.takes)

                    if decision == "q":
                        print("\nCapture interrompue proprement.")
                        print(f"Manifest: {output_dir / 'manifest.json'}")
                        return 0

                    break

        write_manifests(output_dir, rows, env_path, audio, playback, args.takes)

        print("\n=== CORPUS TERMINÉ ===")
        print(f"Enregistrements validés : {len(rows)}/{total_expected}")
        print(f"WAV                     : {output_dir.resolve()}")
        print(f"Manifest JSON           : {(output_dir / 'manifest.json').resolve()}")
        print(f"Manifest CSV            : {(output_dir / 'manifest.csv').resolve()}")
        print("\nAucun composant de l'agent LiveStageAssistant n'a été lancé ou modifié.")
        return 0

    except KeyboardInterrupt:
        print("\nInterrompu par l'utilisateur.")
        return 130

    except Exception as exc:
        print(f"\nErreur: {exc}", file=sys.stderr)
        print(
            "Astuce: lance --list-devices pour vérifier les index PyAudio.",
            file=sys.stderr,
        )
        return 1

    finally:
        pa.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
