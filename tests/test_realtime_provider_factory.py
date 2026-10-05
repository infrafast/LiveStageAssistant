import unittest
from unittest.mock import patch

from voice_assistant.realtime.engine import RealtimeEngineConfig
from voice_assistant.realtime.gemini_live import GeminiLiveEngine
from voice_assistant.realtime.openai_realtime import OpenAIRealtimeEngine
from voice_assistant.realtime.provider_factory import create_realtime_engine


class RealtimeProviderFactoryTests(unittest.TestCase):
    def test_openai_provider(self):
        engine = create_realtime_engine("openai", RealtimeEngineConfig(provider="openai", model="m", voice="v"), api_key="key")
        self.assertIsInstance(engine, OpenAIRealtimeEngine)

    def test_gemini_provider(self):
        engine = create_realtime_engine("gemini", RealtimeEngineConfig(provider="gemini", model="m", voice="v"), api_key="key")
        self.assertIsInstance(engine, GeminiLiveEngine)

    def test_gemini_rejects_native_mcp(self):
        from voice_assistant.realtime.engine import RealtimeMCPServer
        config = RealtimeEngineConfig(provider="gemini", model="m", voice="v", mcp_servers=(RealtimeMCPServer(label="x", url="https://example.test/mcp"),))
        with self.assertRaises(ValueError):
            create_realtime_engine("gemini", config, api_key="key")


if __name__ == "__main__":
    unittest.main()
