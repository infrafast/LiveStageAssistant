import unittest

from voice_assistant.realtime.mcp_auto import classify_auto_fallback, tool_read_only_from_metadata


class RealtimeMCPAutoPolicyTests(unittest.TestCase):
    def test_pre_dispatch_failure_falls_back(self):
        decision = classify_auto_fallback(dispatched=False, read_only=None)
        self.assertTrue(decision.fallback)
        self.assertEqual(decision.classification, "pre_dispatch_definite_failure")

    def test_post_dispatch_read_only_failure_falls_back(self):
        decision = classify_auto_fallback(dispatched=True, read_only=True)
        self.assertTrue(decision.fallback)
        self.assertEqual(decision.classification, "post_dispatch_read_only_failure")

    def test_post_dispatch_write_failure_is_not_replayed(self):
        decision = classify_auto_fallback(dispatched=True, read_only=False)
        self.assertFalse(decision.fallback)
        self.assertEqual(decision.classification, "ambiguous_mutation_or_unknown")

    def test_post_dispatch_unknown_failure_is_not_replayed(self):
        decision = classify_auto_fallback(dispatched=True, read_only=None)
        self.assertFalse(decision.fallback)
        self.assertEqual(decision.classification, "ambiguous_mutation_or_unknown")

    def test_explicit_non_execution_allows_write_fallback(self):
        decision = classify_auto_fallback(
            dispatched=True,
            read_only=False,
            explicit_not_executed=True,
        )
        self.assertTrue(decision.fallback)
        self.assertEqual(decision.classification, "post_dispatch_not_executed")

    def test_read_only_hint_is_strict_metadata_only(self):
        self.assertTrue(tool_read_only_from_metadata({"annotations": {"readOnlyHint": True}}))
        self.assertFalse(tool_read_only_from_metadata({"annotations": {"readOnlyHint": False}}))
        self.assertIsNone(tool_read_only_from_metadata({"annotations": {}}))
        self.assertIsNone(tool_read_only_from_metadata({"name": "get_status"}))


if __name__ == "__main__":
    unittest.main()
