#!/usr/bin/env python3
"""Isolated sherpa-onnx runner used only by scripts/stt_benchmark.py."""

from __future__ import annotations

import argparse
import json
import time
import wave
from pathlib import Path

import numpy as np
import sherpa_onnx


SAMPLE_RATE = 16000



def format_eta(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{sec:02d}s"
    if minutes:
        return f"{minutes}m{sec:02d}s"
    return f"{sec}s"


def read_wav(path: Path) -> tuple[np.ndarray, int]:
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
        rate = SAMPLE_RATE
    return samples, rate


def model_files(model_dir: Path) -> dict[str, Path]:
    files = {
        "tokens": model_dir / "tokens.txt",
        "encoder": model_dir / "encoder-epoch-29-avg-9-with-averaged-model.int8.onnx",
        "decoder": model_dir / "decoder-epoch-29-avg-9-with-averaged-model.onnx",
        "joiner": model_dir / "joiner-epoch-29-avg-9-with-averaged-model.onnx",
    }
    for path in files.values():
        if not path.exists():
            raise FileNotFoundError(path)
    return files


def create_recognizer(
    model_dir: Path,
    threads: int,
    decoding_method: str,
    hotwords_file: Path | None,
    hotwords_score: float,
    bpe_vocab: Path | None,
    max_active_paths: int,
):
    files = model_files(model_dir)
    kwargs = {
        "tokens": str(files["tokens"]),
        "encoder": str(files["encoder"]),
        "decoder": str(files["decoder"]),
        "joiner": str(files["joiner"]),
        "num_threads": threads,
        "provider": "cpu",
        "sample_rate": SAMPLE_RATE,
        "feature_dim": 80,
        "decoding_method": decoding_method,
        "max_active_paths": max_active_paths,
    }
    if hotwords_file is not None:
        if decoding_method != "modified_beam_search":
            raise ValueError("Sherpa hotwords require modified_beam_search")
        if bpe_vocab is None or not bpe_vocab.exists():
            raise FileNotFoundError(
                "French BPE vocabulary required for Sherpa hotwords: "
                f"{bpe_vocab}"
            )
        kwargs.update({
            "hotwords_file": str(hotwords_file),
            "hotwords_score": hotwords_score,
            "modeling_unit": "bpe",
            "bpe_vocab": str(bpe_vocab),
        })
    return sherpa_onnx.OnlineRecognizer.from_transducer(**kwargs)


def transcribe(recognizer, path: Path) -> str:
    samples, rate = read_wav(path)
    stream = recognizer.create_stream()
    stream.accept_waveform(rate, samples)
    stream.accept_waveform(rate, np.zeros(int(0.66 * rate), dtype=np.float32))
    stream.input_finished()
    while recognizer.is_ready(stream):
        recognizer.decode_stream(stream)
    result = recognizer.get_result(stream)
    return str(getattr(result, "text", result)).strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--corpus-dir", required=True)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--engine-name", default="sherpa-onnx-zipformer-fr-int8")
    parser.add_argument(
        "--decoding-method",
        choices=["greedy_search", "modified_beam_search"],
        default="greedy_search",
    )
    parser.add_argument("--max-active-paths", type=int, default=4)
    parser.add_argument("--hotwords-file")
    parser.add_argument("--hotwords-score", type=float, default=1.5)
    parser.add_argument("--bpe-vocab")
    args = parser.parse_args()

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    rows = list(manifest.get("recordings") or [])
    corpus_dir = Path(args.corpus_dir)
    model_dir = Path(args.model_dir)
    hotwords_file = Path(args.hotwords_file) if args.hotwords_file else None
    bpe_vocab = Path(args.bpe_vocab) if args.bpe_vocab else None
    if hotwords_file is not None and not hotwords_file.exists():
        raise FileNotFoundError(hotwords_file)

    print(
        "Chargement du modèle sherpa-onnx Zipformer FR "
        f"({args.decoding_method}"
        + (f", hotwords score={args.hotwords_score}" if hotwords_file else "")
        + ")...",
        flush=True,
    )
    started = time.perf_counter()
    recognizer = create_recognizer(
        model_dir,
        args.threads,
        args.decoding_method,
        hotwords_file,
        args.hotwords_score,
        bpe_vocab,
        args.max_active_paths,
    )
    load_ms = (time.perf_counter() - started) * 1000.0
    print(f"Modèle chargé en {load_ms:.0f} ms.", flush=True)

    if rows:
        print(f'Warm-up (non compté) : {rows[0]["file"]}', flush=True)
        transcribe(recognizer, corpus_dir / str(rows[0]["file"]))
        print("Warm-up terminé.", flush=True)

    results = []
    decode_times: list[float] = []
    total = len(rows)
    for index, row in enumerate(rows, 1):
        path = corpus_dir / str(row["file"])
        engine = args.engine_name
        print(f'[{engine}] [{index:02d}/{total:02d}] À décoder : "{row["reference"]}"', flush=True)
        print(f'{" " * (len(engine) + 5)}fichier    : {row["file"]}', flush=True)
        started = time.perf_counter()
        try:
            text = transcribe(recognizer, path)
            error = None
        except Exception as exc:
            text = ""
            error = f"{type(exc).__name__}: {exc}"
        decode_ms = round((time.perf_counter() - started) * 1000.0, 2)
        duration = float(row.get("duration_seconds") or 0.0)
        rtf = round((decode_ms / 1000.0) / duration, 4) if duration else None
        print(f'{" " * (len(engine) + 5)}décodé     : "{text or "<vide>"}"', flush=True)
        if error:
            print(f'{" " * (len(engine) + 5)}ERREUR     : {error}', flush=True)
        decode_times.append(decode_ms)
        remaining = max(0, total - index)
        eta = (sum(decode_times) / len(decode_times) / 1000.0) * remaining
        print(
            f'{" " * (len(engine) + 5)}temps      : {decode_ms} ms | '
            f'RTF={rtf} | ETA ≈ {format_eta(eta)}',
            flush=True,
        )
        results.append({
            "file": str(row["file"]),
            "transcription": text,
            "decode_ms": decode_ms,
            "error": error,
        })

    payload = {
        "sherpa_version": getattr(sherpa_onnx, "__version__", "unknown"),
        "load_ms": round(load_ms, 2),
        "threads": args.threads,
        "engine": args.engine_name,
        "decoding_method": args.decoding_method,
        "max_active_paths": args.max_active_paths,
        "hotwords_file": str(hotwords_file) if hotwords_file else "",
        "hotwords_score": args.hotwords_score if hotwords_file else None,
        "bpe_vocab": str(bpe_vocab) if bpe_vocab else "",
        "results": results,
    }
    Path(args.output).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
