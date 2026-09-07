import io
import json
import unittest
from unittest.mock import patch

from voice_assistant.realtime.browser_auth import create_openai_browser_client_secret


class _Response:
    status = 200
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self):
        return json.dumps({"value":"ek_test","expires_at":123,"session":{"model":"gpt-realtime-2.1"}}).encode()


class BrowserAuthTests(unittest.TestCase):
    @patch("voice_assistant.realtime.browser_auth.urlopen", return_value=_Response())
    def test_long_lived_key_is_only_authorization_header(self, mocked):
        result = create_openai_browser_client_secret("sk-server-secret", model="gpt-realtime-2.1", voice="marin", instructions="x")
        self.assertEqual(result["value"], "ek_test")
        request = mocked.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer sk-server-secret")
        self.assertNotIn(b"sk-server-secret", request.data)


if __name__ == "__main__":
    unittest.main()
