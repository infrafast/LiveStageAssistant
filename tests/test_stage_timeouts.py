import importlib.util
import time
from pathlib import Path
import unittest


STAGE_TIMEOUT_PATH = Path(__file__).parents[1] / "voice_assistant" / "stage_timeout.py"
STAGE_TIMEOUT_SPEC = importlib.util.spec_from_file_location("stage_timeout_under_test", STAGE_TIMEOUT_PATH)
assert STAGE_TIMEOUT_SPEC and STAGE_TIMEOUT_SPEC.loader
STAGE_TIMEOUT_MODULE = importlib.util.module_from_spec(STAGE_TIMEOUT_SPEC)
STAGE_TIMEOUT_SPEC.loader.exec_module(STAGE_TIMEOUT_MODULE)
TimedStageRunner = STAGE_TIMEOUT_MODULE.TimedStageRunner


class TimedStageRunnerTests(unittest.TestCase):
    def test_timed_stage_returns_completed_result(self) -> None:
        runner = TimedStageRunner()

        result = runner.run("test-fast", 0.2, lambda: "ready")

        self.assertEqual(result, "ready")
        self.assertIsNone(runner.active_worker("test-fast"))

    def test_timed_stage_raises_without_waiting_for_stuck_worker(self) -> None:
        runner = TimedStageRunner()
        started_at = time.perf_counter()

        with self.assertRaisesRegex(TimeoutError, "exceeded"):
            runner.run("test-slow", 0.02, lambda: time.sleep(0.2))

        self.assertLess(time.perf_counter() - started_at, 0.15)
        self.assertTrue(runner.active_worker("test-slow").is_alive())

    def test_timed_stage_rejects_duplicate_worker_while_previous_one_is_stuck(self) -> None:
        runner = TimedStageRunner()

        with self.assertRaises(TimeoutError):
            runner.run("test-duplicate", 0.02, lambda: time.sleep(0.2))

        with self.assertRaisesRegex(TimeoutError, "previous test-duplicate worker is still running"):
            runner.run("test-duplicate", 0.02, lambda: "second")


if __name__ == "__main__":
    unittest.main()
