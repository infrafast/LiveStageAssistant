import importlib.util
import time
from pathlib import Path

import pytest

STAGE_TIMEOUT_PATH = Path(__file__).parents[1] / "voice_assistant" / "stage_timeout.py"
STAGE_TIMEOUT_SPEC = importlib.util.spec_from_file_location("stage_timeout_under_test", STAGE_TIMEOUT_PATH)
assert STAGE_TIMEOUT_SPEC and STAGE_TIMEOUT_SPEC.loader
STAGE_TIMEOUT_MODULE = importlib.util.module_from_spec(STAGE_TIMEOUT_SPEC)
STAGE_TIMEOUT_SPEC.loader.exec_module(STAGE_TIMEOUT_MODULE)
TimedStageRunner = STAGE_TIMEOUT_MODULE.TimedStageRunner


def test_timed_stage_returns_completed_result() -> None:
    runner = TimedStageRunner()

    result = runner.run("test-fast", 0.2, lambda: "ready")

    assert result == "ready"
    assert runner.active_worker("test-fast") is None


def test_timed_stage_raises_without_waiting_for_stuck_worker() -> None:
    runner = TimedStageRunner()
    started_at = time.perf_counter()

    with pytest.raises(TimeoutError, match="exceeded"):
        runner.run("test-slow", 0.02, lambda: time.sleep(0.2))

    assert time.perf_counter() - started_at < 0.15
    assert runner.active_worker("test-slow").is_alive()


def test_timed_stage_rejects_duplicate_worker_while_previous_one_is_stuck() -> None:
    runner = TimedStageRunner()

    with pytest.raises(TimeoutError):
        runner.run("test-duplicate", 0.02, lambda: time.sleep(0.2))

    with pytest.raises(TimeoutError, match="previous test-duplicate worker is still running"):
        runner.run("test-duplicate", 0.02, lambda: "second")
