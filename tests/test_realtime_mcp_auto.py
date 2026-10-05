import unittest

from voice_assistant.realtime.mcp_auto import classify_auto_fallback, fault_matrix, tool_read_only_from_metadata


class RealtimeMCPAutoPolicyTests(unittest.TestCase):
    def test_pre_dispatch_failure_falls_back(self):
        decision = classify_auto_fallback(dispatched=False, read_only=None)
        self.assertTrue(decision.fallback)
        self.assertEqual(decision.classification, "pre_dispatch_definite_failure")

    def test_auth_before_dispatch_falls_back(self):
        decision = classify_auto_fallback(dispatched=False, read_only=None, failure_kind="auth")
        self.assertTrue(decision.fallback)
        self.assertEqual(decision.failure_kind, "auth")

    def test_timeout_before_dispatch_falls_back(self):
        decision = classify_auto_fallback(dispatched=False, read_only=None, failure_kind="timeout")
        self.assertTrue(decision.fallback)
        self.assertEqual(decision.classification, "pre_dispatch_definite_failure")

    def test_post_dispatch_read_only_failure_falls_back(self):
        decision = classify_auto_fallback(dispatched=True, read_only=True, failure_kind="timeout")
        self.assertTrue(decision.fallback)
        self.assertEqual(decision.classification, "post_dispatch_read_only_failure")

    def test_post_dispatch_write_timeout_is_not_replayed(self):
        decision = classify_auto_fallback(dispatched=True, read_only=False, failure_kind="timeout")
        self.assertFalse(decision.fallback)
        self.assertEqual(decision.classification, "ambiguous_mutation_or_unknown")
        self.assertIn("replay suppressed", decision.reason)

    def test_post_dispatch_write_connection_loss_is_not_replayed(self):
        decision = classify_auto_fallback(dispatched=True, read_only=False, failure_kind="connection")
        self.assertFalse(decision.fallback)
        self.assertEqual(decision.classification, "ambiguous_mutation_or_unknown")

    def test_post_dispatch_unknown_failure_is_not_replayed(self):
        decision = classify_auto_fallback(dispatched=True, read_only=None, failure_kind="timeout")
        self.assertFalse(decision.fallback)
        self.assertEqual(decision.classification, "ambiguous_mutation_or_unknown")

    def test_explicit_non_execution_allows_write_fallback(self):
        decision = classify_auto_fallback(
            dispatched=True,
            read_only=False,
            explicit_not_executed=True,
            failure_kind="provider_rejected",
        )
        self.assertTrue(decision.fallback)
        self.assertEqual(decision.classification, "post_dispatch_not_executed")

    def test_read_only_hint_is_strict_metadata_only(self):
        self.assertTrue(tool_read_only_from_metadata({"annotations": {"readOnlyHint": True}}))
        self.assertFalse(tool_read_only_from_metadata({"annotations": {"readOnlyHint": False}}))
        self.assertIsNone(tool_read_only_from_metadata({"annotations": {}}))
        self.assertIsNone(tool_read_only_from_metadata({"name": "get_status"}))

    def test_fault_matrix_has_expected_stage_safe_outcomes(self):
        decisions = dict(fault_matrix())
        self.assertTrue(decisions["auth_before_dispatch"].fallback)
        self.assertTrue(decisions["timeout_before_dispatch"].fallback)
        self.assertTrue(decisions["timeout_after_read_dispatch"].fallback)
        self.assertFalse(decisions["timeout_after_write_dispatch"].fallback)
        self.assertFalse(decisions["connection_after_write_dispatch"].fallback)
        self.assertFalse(decisions["timeout_after_unknown_dispatch"].fallback)
        self.assertTrue(decisions["explicit_not_executed_write"].fallback)


if __name__ == "__main__":
    unittest.main()
