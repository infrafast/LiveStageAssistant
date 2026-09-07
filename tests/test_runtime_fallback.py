from voice_assistant.runtime import fallback_engine_candidates, select_fallback_engine


def test_default_online_fallback_is_classic():
    assert fallback_engine_candidates({}, online=True) == ("classic",)


def test_offline_never_uses_online_engine_fallback():
    assert fallback_engine_candidates({"VOICE_ENGINE_FALLBACK": "classic"}, online=False) == ()


def test_disabled_fallback_is_empty():
    assert fallback_engine_candidates({"VOICE_ENGINE_FALLBACK": "off"}, online=True) == ()


def test_select_fallback_skips_failed_engine():
    values = {"VOICE_ENGINE_FALLBACK": "classic,openai-realtime"}
    assert select_fallback_engine(
        values,
        online=True,
        failed_engine="openai-realtime",
        failed_engines={"openai-realtime"},
    ) == "classic"


def test_select_fallback_does_not_loop_back_to_previous_failure():
    values = {"VOICE_ENGINE_FALLBACK": "classic,openai-realtime"}
    assert select_fallback_engine(
        values,
        online=True,
        failed_engine="classic",
        failed_engines={"classic", "openai-realtime"},
    ) == ""
