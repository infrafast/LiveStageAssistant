from pathlib import Path
import json
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

    def test_apply_policy_is_restart_safe_by_default(self):
        policy = json.loads((WEB / "config-apply-policy.json").read_text(encoding="utf-8"))
        self.assertEqual(policy["default_mode"], "engine-restart")
        self.assertIn("#web-tts-volume", policy["hot"])
        self.assertIn("#browser-audio-input", policy["ignore"])

    def test_multiple_changed_fields_use_strictest_mode(self):
        script = (WEB / "config-unified.js").read_text(encoding="utf-8")
        self.assertIn("function globalMode(entries)", script)
        self.assertIn('entry.mode === "engine-restart"', script)
        self.assertIn('return "hot"', script)

    def test_extended_endpoints_are_only_called_for_dirty_groups(self):
        script = (WEB / "config-unified.js").read_text(encoding="utf-8")
        self.assertIn('keys.has("voice-engine")', script)
        self.assertIn('keys.has("voice-output-gains")', script)

    def test_restart_pending_survives_additional_unsaved_changes(self):
        script = (WEB / "config-unified.js").read_text(encoding="utf-8")
        self.assertIn("let restartPending = false", script)
        self.assertIn("if (dirty.size > 0)", script)
        self.assertIn("if (restartPending)", script)
        self.assertNotIn("setRestartRequired(false)", script)

    def test_save_is_disabled_when_clean(self):
        script = (WEB / "config-unified.js").read_text(encoding="utf-8")
        self.assertIn('saveButton.disabled = true', script)
        self.assertIn('saveButton.textContent = "Save"', script)


if __name__ == "__main__":
    unittest.main()
