from voice_assistant.i18n import localized_error_text, sanitize_spoken_response


RAW_OSCXR_ROUTE_MUTE = (
    "La commande mixeur a échoué : Unsupported for OSCXR: "
    "Channel-to-bus mute is not losslessly supported: OSCXR exposes "
    "/ch/13/mix/on as whole-channel mute, not bus 3 mute."
)


def test_french_oscxr_route_mute_error_is_concise_and_localized():
    result = localized_error_text("fr", domain="mixer", error=RAW_OSCXR_ROUTE_MUTE, error_code="execution_failed")
    assert result == (
        "La commande mixeur a échoué : "
        "le mute d’une voie vers un bus séparé n’est pas pris en charge avec OSCXR"
    )
    assert "/ch/13/mix/on" not in result
    assert "losslessly" not in result


def test_english_oscxr_route_mute_error_is_concise_and_localized():
    result = localized_error_text("en", domain="mixer", error=RAW_OSCXR_ROUTE_MUTE, error_code="execution_failed")
    assert result == (
        "The mixer command failed : "
        "per-bus mute for an input channel is not supported with OSCXR"
    )


def test_unknown_exception_never_leaks_raw_detail():
    raw = "RuntimeError: internal/path/to/private_impl.py:219 impossible state foo=bar"
    result = localized_error_text("fr", domain="command", error=raw)
    assert result == "La commande a échoué : une erreur technique est survenue"
    assert "private_impl" not in result
    assert "foo=bar" not in result


def test_final_spoken_guard_sanitizes_obvious_technical_errors():
    result = sanitize_spoken_response("fr", RAW_OSCXR_ROUTE_MUTE)
    assert "Unsupported for OSCXR" not in result
    assert "losslessly" not in result
    assert result.startswith("La commande mixeur a échoué :")


def test_final_spoken_guard_leaves_normal_assistant_text_unchanged():
    text = "ANTO réactivé."
    assert sanitize_spoken_response("fr", text) == text
