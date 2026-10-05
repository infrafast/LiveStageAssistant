import sys
import time
import types

import numpy as np

from voice_assistant.agent import (
    _append_capped_audio_frames,
    BackendWakeWordDetector,
    VoiceAssistant,
    classify_cloud_api_error,
    format_backend_listening_message,
    parse_env_list,
    SpeakerRecognitionResult,
)


def install_fake_openwakeword(monkeypatch, scores):
    class FakeModel:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def predict(self, _samples):
            score = scores.pop(0) if scores else 0.0
            return {"regie": score}

    package = types.ModuleType("openwakeword")
    model_module = types.ModuleType("openwakeword.model")
    model_module.Model = FakeModel
    monkeypatch.setitem(sys.modules, "openwakeword", package)
    monkeypatch.setitem(sys.modules, "openwakeword.model", model_module)


def test_short_pre_vad_speech_candidate_is_preserved_in_preroll():
    pre_roll = [b"silence"]
    short_first_word = [b"cou", b"pe"]
    _append_capped_audio_frames(pre_roll, [*short_first_word, b"micro-pause"], 6)

    confirmed_target = [b"clau", b"de"]
    utterance = pre_roll + confirmed_target

    assert utterance == [
        b"silence",
        b"cou",
        b"pe",
        b"micro-pause",
        b"clau",
        b"de",
    ]


def test_preroll_preservation_stays_capped_to_recent_audio():
    pre_roll = [b"old-1", b"old-2"]
    _append_capped_audio_frames(pre_roll, [b"cou", b"pe", b"pause"], 4)

    assert pre_roll == [b"old-2", b"cou", b"pe", b"pause"]


def test_backend_wake_word_detector_buffers_80ms_frames(monkeypatch):
    install_fake_openwakeword(monkeypatch, [0.8])
    detector = BackendWakeWordDetector(
        model_paths=["regie.onnx"],
        model_names=[],
        threshold=0.5,
        cooldown_ms=0,
    )

    partial_frame = np.zeros(640, dtype=np.int16).tobytes()
    full_frame_tail = np.zeros(640, dtype=np.int16).tobytes()

    assert detector.process_pcm16_16k(partial_frame) is None
    assert detector.process_pcm16_16k(full_frame_tail) == ("regie", 0.8)
    assert detector.model.kwargs["inference_framework"] == "onnx"
    assert detector.model.kwargs["wakeword_models"] == ["regie.onnx"]


def test_backend_wake_word_detector_uses_threshold(monkeypatch):
    install_fake_openwakeword(monkeypatch, [0.49, 0.51])
    detector = BackendWakeWordDetector(
        model_paths=["regie.onnx"],
        model_names=[],
        threshold=0.5,
        cooldown_ms=0,
    )
    frame = np.zeros(1280, dtype=np.int16).tobytes()

    assert detector.process_pcm16_16k(frame) is None
    assert detector.process_pcm16_16k(frame) == ("regie", 0.51)


def test_backend_wake_word_config_helpers():
    assert parse_env_list("a.onnx, b.onnx;c.onnx| d.onnx") == [
        "a.onnx",
        "b.onnx",
        "c.onnx",
        "d.onnx",
    ]
    assert format_backend_listening_message(["momo"], "openwakeword") == 'Listening for "momo" using openwakeword...'
    assert format_backend_listening_message([], None) == "Listening (no wake word)"


def test_backend_wake_word_tracks_scores_and_reports_waiting(monkeypatch, capsys):
    install_fake_openwakeword(monkeypatch, [0.2, 0.8])
    detector = BackendWakeWordDetector(
        model_paths=["regie.onnx"],
        model_names=[],
        threshold=0.5,
        cooldown_ms=0,
    )
    frame = np.zeros(1280, dtype=np.int16).tobytes()

    assert detector.process_pcm16_16k(frame) is None
    assert detector._debug_last_score == 0.2
    assert detector.process_pcm16_16k(frame) == ("regie", 0.8)
    assert detector._debug_max_score == 0.8

    detector.report_waiting(["momo"])
    output = capsys.readouterr().out
    assert "momo" in output
    assert "0.50" in output


def test_transcribe_and_recognize_audio_runs_stt_only_when_speaker_disabled():
    assistant = VoiceAssistant.__new__(VoiceAssistant)
    assistant.speaker_recognition_enabled = False
    assistant.speaker_recognizer = object()
    called = {"speaker": False}

    def transcribe():
        return "monte guitare"

    def speaker():
        called["speaker"] = True
        return SpeakerRecognitionResult(speaker="laurent", confidence=0.9, backend="resemblyzer")

    text, speaker_result = assistant.transcribe_and_recognize_audio(
        transcribe,
        b"audio",
        speaker_operation=speaker,
    )

    assert text == "monte guitare"
    assert speaker_result.speaker == "unknown"
    assert called["speaker"] is False


def test_transcribe_and_recognize_audio_parallelizes_stt_and_speaker():
    assistant = VoiceAssistant.__new__(VoiceAssistant)
    assistant.speaker_recognition_enabled = True
    assistant.speaker_recognizer = object()

    def transcribe():
        time.sleep(0.12)
        return "monte guitare"

    def speaker():
        time.sleep(0.12)
        return SpeakerRecognitionResult(speaker="laurent", confidence=0.9, backend="resemblyzer")

    started_at = time.perf_counter()
    text, speaker_result = assistant.transcribe_and_recognize_audio(
        transcribe,
        b"audio",
        speaker_operation=speaker,
    )

    assert time.perf_counter() - started_at < 0.20
    assert text == "monte guitare"
    assert speaker_result.speaker == "laurent"


def test_transcribe_and_recognize_audio_does_not_wait_for_speaker_when_stt_is_empty():
    assistant = VoiceAssistant.__new__(VoiceAssistant)
    assistant.speaker_recognition_enabled = True
    assistant.speaker_recognizer = object()

    def transcribe():
        return ""

    def speaker():
        time.sleep(0.25)
        return SpeakerRecognitionResult(speaker="laurent", confidence=0.9, backend="resemblyzer")

    started_at = time.perf_counter()
    text, speaker_result = assistant.transcribe_and_recognize_audio(
        transcribe,
        b"audio",
        speaker_operation=speaker,
    )

    assert time.perf_counter() - started_at < 0.12
    assert text == ""
    assert speaker_result.speaker == "unknown"


def test_cloud_api_error_classification_is_user_facing():
    quota = classify_cloud_api_error(
        "Error code: 429 - {'error': {'code': 'credit_balance_exhausted', 'message': 'no credits remaining'}}",
        provider="OpenAI",
        stage="llm",
    )
    assert quota is not None
    assert quota.kind == "quota"
    assert "Plus de crédit API OpenAI" in quota.message

    auth = classify_cloud_api_error("status code: 401 invalid_api_key", provider="ElevenLabs", stage="tts")
    assert auth is not None
    assert auth.kind == "auth"

    rate = classify_cloud_api_error("rate_limit_exceeded", provider="OpenAI", stage="stt")
    assert rate is not None
    assert rate.kind == "rate_limit"


def test_startup_ready_message_reports_tool_count_and_failed_servers():
    assistant = VoiceAssistant.__new__(VoiceAssistant)
    assistant.stt_language = "fr"
    assistant.mcp_all_tools = [object()] * 95

    assert assistant._startup_ready_message(["mixer"], {}) == (
        "Assistant vocal prêt à exécuter des commandes, 95 outils disponibles !"
    )

    assert assistant._startup_ready_message(["mixer"], {"qlcplus": "timeout"}) == (
        "Assistant vocal prêt à exécuter des commandes, seulement 95 outils disponibles "
        "car qlcplus est injoignable."
    )

    assistant.mcp_all_tools = []
    assert assistant._startup_ready_message([], {"mixer": "timeout", "qlcplus": "timeout"}) == (
        "Assistant vocal prêt à exécuter des commandes, aucun MCP connecté."
    )

    assistant.wake_words = ["momo"]
    assistant.backend_wake_word_detector = object()
    assert assistant._startup_ready_message(["mixer"], {}) == (
        "Assistant vocal prêt à exécuter des commandes, aucun MCP connecté. "
        "Wake word actif, prononcez momo pour me réveiller."
    )

