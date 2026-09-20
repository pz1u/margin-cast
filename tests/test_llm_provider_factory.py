import os
import unittest
from unittest.mock import patch

from src.llm_provider import LLMProvider
from src.llm_provider_factory import (
    LLMProviderConfigurationError,
    create_llm_provider,
)
from src.ollama_provider import OllamaProvider
from src.openai_provider import OpenAIProvider


class LLMProviderFactoryTests(unittest.TestCase):
    def test_default_provider_remains_ollama(self):
        environment = {
            "OLLAMA_BASE_URL": "http://localhost:11434",
            "OLLAMA_MODEL": "qwen-test",
        }
        with patch.dict(os.environ, environment, clear=True):
            provider = create_llm_provider()
        self.assertIsInstance(provider, OllamaProvider)
        self.assertIsInstance(provider, LLMProvider)

    def test_blank_provider_keeps_ollama_default(self):
        environment = {
            "LLM_PROVIDER": "  ",
            "OLLAMA_BASE_URL": "http://localhost:11434",
            "OLLAMA_MODEL": "qwen-test",
        }
        with patch.dict(os.environ, environment, clear=True):
            provider = create_llm_provider()
        self.assertIsInstance(provider, OllamaProvider)
    def test_openai_provider_is_selected_from_environment(self):
        environment = {
            "LLM_PROVIDER": "openai",
            "OPENAI_API_KEY": "test-key",
            "OPENAI_MODEL": "test-model",
        }
        with patch.dict(os.environ, environment, clear=True):
            provider = create_llm_provider()
        self.assertIsInstance(provider, OpenAIProvider)
        self.assertIsInstance(provider, LLMProvider)

    def test_unknown_provider_is_rejected(self):
        with patch.dict(os.environ, {"LLM_PROVIDER": "unknown"}, clear=True):
            with self.assertRaises(LLMProviderConfigurationError):
                create_llm_provider()

    def test_selected_provider_requires_its_own_configuration(self):
        with patch.dict(os.environ, {"LLM_PROVIDER": "openai"}, clear=True):
            with self.assertRaises(Exception) as context:
                create_llm_provider()
        self.assertEqual(context.exception.code, "OPENAI_CONFIGURATION_ERROR")


if __name__ == "__main__":
    unittest.main()