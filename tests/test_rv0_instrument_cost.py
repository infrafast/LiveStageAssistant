import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "rv0_instrument_cost.py"
spec = importlib.util.spec_from_file_location("rv0_instrument_cost", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)


class Rv0CostInstrumentationTests(unittest.TestCase):
    def test_instruments_current_agent_source(self):
        source = (ROOT / "voice_assistant" / "agent.py").read_text()
        updated = module.instrument(source)
        self.assertTrue(module.is_instrumented(updated))
        self.assertEqual(module.instrument(updated), updated)

    def test_missing_anchor_fails_closed(self):
        with self.assertRaises(RuntimeError):
            module.instrument("not agent source")


if __name__ == "__main__":
    unittest.main()
