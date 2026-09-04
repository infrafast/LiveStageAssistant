import importlib.util
from pathlib import Path
import unittest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "rv0_instrument_classic.py"
SPEC = importlib.util.spec_from_file_location("rv0_instrument_classic", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class Rv0InstrumentationTests(unittest.TestCase):
    def sample_agent(self):
        return (
            "try:\n"
            + MODULE.PACKAGE_IMPORT
            + "except ImportError:\n"
            + MODULE.FALLBACK_IMPORT
            + "\nasync def run(self):\n"
            + MODULE.PROCESS_ANCHOR
            + MODULE.RESPONSE_ANCHOR
            + MODULE.TTS_START_ANCHOR
            + MODULE.TTS_END_ANCHOR
        )

    def test_instrumentation_is_idempotent(self):
        original = self.sample_agent()
        updated = MODULE.instrument(original)
        self.assertTrue(MODULE.is_instrumented(updated))
        self.assertEqual(MODULE.instrument(updated), updated)

    def test_missing_anchor_fails_loudly(self):
        with self.assertRaises(RuntimeError):
            MODULE.instrument(self.sample_agent().replace(MODULE.RESPONSE_ANCHOR, ""))


if __name__ == "__main__":
    unittest.main()
