import json
import tempfile
import unittest
from pathlib import Path

from voice_assistant.realtime.mcp_config import (
    load_mcp_inventory,
    normalize_mcp_inventory,
    normalize_mcp_server,
    server_summary,
)


class RealtimeMCPConfigTests(unittest.TestCase):
    def test_legacy_stdio_entry_defaults_to_stdio_open(self):
        server = normalize_mcp_server(
            "service-a",
            {
                "command": "node",
                "args": ["server.js"],
                "env": {"SECRET": "must-not-leak"},
                "assistantOptions": {"routing": "alpha,beta"},
            },
        )
        self.assertEqual(server.realtime.transport, "stdio")
        self.assertEqual(server.realtime.permissions.mode, "open")
        self.assertEqual(server.native.url, "")
        self.assertEqual(server.local_entry["command"], "node")
        self.assertEqual(server.assistant_options["routing"], "alpha,beta")
        summary = server_summary(server)
        self.assertEqual(summary["localTransport"], "stdio")
        self.assertFalse(summary["hasNativeUrl"])
        self.assertNotIn("SECRET", json.dumps(summary))

    def test_additive_canonical_shape_preserves_legacy_local_fields(self):
        server = normalize_mcp_server(
            "service-a",
            {
                "command": "node",
                "args": ["server.js"],
                "env": {"MODE": "local"},
                "native": {
                    "url": "https://example.test/mcp",
                    "headers": {"Authorization": "Bearer secret"},
                },
                "realtime": {
                    "transport": "auto",
                    "permissions": {"mode": "restricted", "allowedTools": ["read", "write", "read"]},
                },
            },
        )
        self.assertEqual(server.local_entry["command"], "node")
        self.assertNotIn("native", server.local_entry)
        self.assertNotIn("realtime", server.local_entry)
        self.assertEqual(server.native.url, "https://example.test/mcp")
        self.assertEqual(server.realtime.transport, "auto")
        self.assertEqual(server.realtime.permissions.mode, "restricted")
        self.assertEqual(server.realtime.permissions.allowed_tools, ("read", "write"))

    def test_legacy_https_url_is_accepted_as_native_compatibility(self):
        server = normalize_mcp_server(
            "service-a",
            {"url": "https://example.test/mcp", "headers": {"X-Test": "1"}},
        )
        self.assertEqual(server.native.url, "https://example.test/mcp")
        self.assertEqual(server.native.headers, {"X-Test": "1"})
        self.assertEqual(server.realtime.transport, "stdio")

    def test_private_http_url_is_not_promoted_to_native(self):
        server = normalize_mcp_server("service-a", {"url": "http://127.0.0.1:8788/mcp"})
        self.assertEqual(server.native.url, "")
        self.assertEqual(server_summary(server)["localTransport"], "http")

    def test_permission_string_compatibility(self):
        server = normalize_mcp_server(
            "service-a",
            {"realtime": {"transport": "native", "permission": "approval"}},
        )
        self.assertEqual(server.realtime.permissions.mode, "approval")

    def test_invalid_transport_is_rejected(self):
        with self.assertRaises(ValueError):
            normalize_mcp_server("service-a", {"realtime": {"transport": "magic"}})

    def test_restricted_requires_allowed_tools(self):
        with self.assertRaises(ValueError):
            normalize_mcp_server(
                "service-a",
                {"realtime": {"permissions": {"mode": "restricted"}}},
            )

    def test_inventory_loader_reads_file_without_mutation(self):
        payload = {
            "mcpServers": {
                "a": {"command": "node", "args": ["a.js"]},
                "b": {"command": "node", "args": ["b.js"], "realtime": {"transport": "auto"}},
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mcp.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            inventory = load_mcp_inventory(path)
            self.assertEqual(set(inventory), {"a", "b"})
            self.assertEqual(inventory["a"].realtime.transport, "stdio")
            self.assertEqual(inventory["b"].realtime.transport, "auto")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), payload)

    def test_inventory_requires_object_servers(self):
        with self.assertRaises(ValueError):
            normalize_mcp_inventory({"mcpServers": []})


if __name__ == "__main__":
    unittest.main()
