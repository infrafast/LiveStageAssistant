import sys

from voice_assistant import engine_entry


def test_engine_entry_routes_local_to_dedicated_runner(monkeypatch):
    calls = []

    monkeypatch.setattr(engine_entry, "run_local", lambda env: calls.append(("local", env)) or 17)
    monkeypatch.setattr(engine_entry, "run_classic", lambda env: calls.append(("classic", env)) or 18)
    monkeypatch.setattr(sys, "argv", [
        "engine_entry",
        "--engine",
        "local",
        "--env-file",
        "/tmp/local.env",
    ])

    assert engine_entry.main() == 17
    assert calls == [("local", "/tmp/local.env")]


def test_engine_entry_keeps_classic_route(monkeypatch):
    calls = []

    monkeypatch.setattr(engine_entry, "run_local", lambda env: calls.append(("local", env)) or 17)
    monkeypatch.setattr(engine_entry, "run_classic", lambda env: calls.append(("classic", env)) or 18)
    monkeypatch.setattr(sys, "argv", [
        "engine_entry",
        "--engine",
        "classic",
        "--env-file",
        "/tmp/online.env",
    ])

    assert engine_entry.main() == 18
    assert calls == [("classic", "/tmp/online.env")]
