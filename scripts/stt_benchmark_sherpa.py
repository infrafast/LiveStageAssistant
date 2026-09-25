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


def create_recognizer(model_dir: Path, threads: int):
    files = model_files(model_dir)
    return sherpa_onnx.OnlineRecognizer.from_transducer(
        tokens=str(files["tokens"]),
        encoder=str(files["encoder"]),
        decoder=str(files["decoder"]),
        joiner=str(files["joiner"]),
        num_threads=threads,
        provider="cpu",
        sample_rate=SAMPLE_RATE,
        feature_dim=80,
        decoding_method="greedy_search",
    )


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
    args = parser.parse_args()

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    rows = list(manifest.get("recordings") or [])
    corpus_dir = Path(args.corpus_dir)
    model_dir = Path(args.model_dir)

    started = time.perf_counter()
    recognizer = create_recognizer(model_dir, args.threads)
    load_ms = (time.perf_counter() - started) * 1000.0

    if rows:
        transcribe(recognizer, corpus_dir / str(rows[0]["file"]))

    results = []
    for row in rows:
        path = corpus_dir / str(row["file"])
        started = time.perf_counter()
        try:
            text = transcribe(recognizer, path)
            error = None
        except Exception as exc:
            text = ""
            error = f"{type(exc).__name__}: {exc}"
        results.append({
            "file": str(row["file"]),
            "transcription": text,
            "decode_ms": round((time.perf_counter() - started) * 1000.0, 2),
            "error": error,
        })

    payload = {
        "sherpa_version": getattr(sherpa_onnx, "__version__", "unknown"),
        "load_ms": round(load_ms, 2),
        "threads": args.threads,
        "results": results,
    }
    Path(args.output).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
