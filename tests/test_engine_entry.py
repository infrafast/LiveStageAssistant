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


def test_local_child_does_not_own_loader_under_common_runtime(monkeypatch):
    from voice_assistant import agent, local_engine

    calls = []
    monkeypatch.setenv("LSA_COMMON_STARTUP_LIFECYCLE", "1")
    monkeypatch.setattr(local_engine, "run", lambda env: calls.append(env) or 0)

    original_start = agent.VoiceAssistant.start_startup_loader_sound
    original_stop = agent.VoiceAssistant.stop_startup_loader_sound
    try:
        assert engine_entry.run_local("/tmp/offline.env") == 0
        assert calls == ["/tmp/offline.env"]
        assert agent.VoiceAssistant.start_startup_loader_sound is not original_start
        assert agent.VoiceAssistant.stop_startup_loader_sound is not original_stop
    finally:
        agent.VoiceAssistant.start_startup_loader_sound = original_start
        agent.VoiceAssistant.stop_startup_loader_sound = original_stop
