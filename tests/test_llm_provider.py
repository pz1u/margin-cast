import unittest

from src.agent_schemas import LLMResponse, Message, MessageRole
from src.llm_provider import LLMProvider


class RecordingProvider:
    def __init__(self):
        self.messages = ()
        self.tools = ()

    def generate(self, messages, tools):
        self.messages = tuple(messages)
        self.tools = tuple(tools)
        return LLMResponse(text="필요한 가격을 알려주세요.")


class LLMProviderTests(unittest.TestCase):
    def test_structural_provider_conforms_without_inheritance(self):
        provider = RecordingProvider()
        self.assertIsInstance(provider, LLMProvider)

        message = Message(role=MessageRole.USER, content="가격 전략을 비교해줘")
        result = provider.generate([message], [{"name": "compare_price_strategies"}])

        self.assertEqual(result.text, "필요한 가격을 알려주세요.")
        self.assertEqual(provider.messages, (message,))
        self.assertEqual(provider.tools[0]["name"], "compare_price_strategies")


if __name__ == "__main__":
    unittest.main()
