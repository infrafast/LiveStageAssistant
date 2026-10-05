"""Engine-neutral backend audio device enumeration for the common runtime."""

from __future__ import annotations

import json
import shutil
import subprocess
from contextlib import suppress
from typing import Any

import pyaudio


def _is_default(audio: pyaudio.PyAudio, index: int, *, input_device: bool) -> bool:
    try:
        method = audio.get_default_input_device_info if input_device else audio.get_default_output_device_info
        return int(method().get("index", -1)) == index
    except Exception:
        return False


def _pipewire_record_available() -> bool:
    return shutil.which("pw-cat") is not None or shutil.which("pw-record") is not None


def _pipewire_play_available() -> bool:
    return shutil.which("pw-play") is not None


def _pipewire_devices() -> dict[str, list[dict[str, Any]]]:
    if shutil.which("pw-dump") is None:
        return {"inputs": [], "outputs": []}
    try:
        process = subprocess.run(
            ["pw-dump"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
            timeout=2.0,
        )
        if process.returncode != 0 or not process.stdout.strip():
            return {"inputs": [], "outputs": []}
        objects = json.loads(process.stdout)
    except Exception:
        return {"inputs": [], "outputs": []}

    devices: dict[str, list[dict[str, Any]]] = {"inputs": [], "outputs": []}
    seen: set[str] = set()
    for item in objects if isinstance(objects, list) else []:
        if not isinstance(item, dict):
            continue
        info = item.get("info")
        props = (info.get("props") or {}) if isinstance(info, dict) else {}
        media_class = str(props.get("media.class") or "").strip()
        if media_class not in {"Audio/Sink", "Audio/Source"}:
            continue
        node_name = str(props.get("node.name") or "").strip()
        if not node_name:
            continue
        kind = "sink" if media_class == "Audio/Sink" else "source"
        direction = "outputs" if kind == "sink" else "inputs"
        entry_id = f"pipewire:{kind}:{node_name}"
        if entry_id in seen:
            continue
        seen.add(entry_id)
        description = str(
            props.get("node.description")
            or props.get("node.nick")
            or props.get("device.description")
            or node_name
        ).strip()
        available = _pipewire_play_available() if kind == "sink" else _pipewire_record_available()
        entry: dict[str, Any] = {
            "id": entry_id,
            "label": f"PipeWire: {description}",
            "name": node_name,
            "default": False,
            "available": available,
        }
        if not available:
            entry["reason"] = "pw-play is required" if kind == "sink" else "pw-cat or pw-record is required"
        devices[direction].append(entry)
    return devices


def list_backend_audio_devices() -> dict[str, list[dict[str, Any]]]:
    """List selectable PyAudio and stable PipeWire backend devices."""
    devices: dict[str, list[dict[str, Any]]] = {"inputs": [], "outputs": []}
    audio: pyaudio.PyAudio | None = None
    try:
        with suppress(Exception):
            audio = pyaudio.PyAudio()
        if audio is not None:
            for index in range(audio.get_device_count()):
                try:
                    info = audio.get_device_info_by_index(index)
                except Exception:
                    continue
                name = str(info.get("name") or f"Device {index}").strip()
                host_api = ""
                try:
                    host = audio.get_host_api_info_by_index(int(info.get("hostApi", 0)))
                    host_api = str(host.get("name") or "").strip()
                except Exception:
                    pass
                label = f"{index}: {name}" + (f" ({host_api})" if host_api else "")
                if int(info.get("maxInputChannels", 0) or 0) > 0:
                    devices["inputs"].append({
                        "id": str(index),
                        "label": label,
                        "name": name,
                        "default": _is_default(audio, index, input_device=True),
                        "available": True,
                    })
                if int(info.get("maxOutputChannels", 0) or 0) > 0:
                    devices["outputs"].append({
                        "id": str(index),
                        "label": label,
                        "name": name,
                        "default": _is_default(audio, index, input_device=False),
                        "available": True,
                    })
    finally:
        if audio is not None:
            with suppress(Exception):
                audio.terminate()

    pipewire = _pipewire_devices()
    devices["inputs"].extend(pipewire["inputs"])
    devices["outputs"].extend(pipewire["outputs"])
    return devices
