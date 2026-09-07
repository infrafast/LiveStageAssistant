from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "assets" / "web"


class WebConfigArchitectureTests(unittest.TestCase):
    def test_bootstrap_runs_before_app_main(self):
        app = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertLess(app.index("config-bootstrap.js"), app.index("app-main.js"))

    def test_unified_controller_has_no_dual_wake_selector(self):
        script = (WEB / "config-unified.js").read_text(encoding="utf-8")
        self.assertNotIn("wake-word-select", script)
        self.assertNotIn("legacyWake", script)
        self.assertNotIn("__lsaWakeCanonicalFetchInstalled", script)

    def test_bootstrap_replaces_wake_control_with_single_canonical_select(self):
        script = (WEB / "config-bootstrap.js").read_text(encoding="utf-8")
        self.assertIn('select.id = "wake-word"', script)
        self.assertIn("current.replaceWith(select)", script)
        self.assertNotIn("wake-word-select", script)

    def test_changes_after_save_cancel_restart_state(self):
        script = (WEB / "config-unified.js").read_text(encoding="utf-8")
        self.assertIn("function markConfigDirty()", script)
        self.assertIn("setRestartRequired(false)", script)
        self.assertIn('configPanel.addEventListener("change"', script)


if __name__ == "__main__":
    unittest.main()
