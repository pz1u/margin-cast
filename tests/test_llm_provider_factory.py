import os
from pathlib import Path
import tempfile
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
    MISSING_ENV_PATH = Path(__file__).with_name("missing-provider.env")

    def test_project_dotenv_selects_openai_without_mutating_process_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text(
                "LLM_PROVIDER=openai\n"
                "OPENAI_API_KEY=test-dotenv-key\n"
                "OPENAI_MODEL=test-dotenv-model\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                provider = create_llm_provider(env_path=env_path)
                self.assertNotIn("OPENAI_API_KEY", os.environ)

        self.assertIsInstance(provider, OpenAIProvider)
        self.assertEqual(provider.model, "test-dotenv-model")

    def test_default_provider_remains_ollama(self):
        environment = {
            "OLLAMA_BASE_URL": "http://localhost:11434",
            "OLLAMA_MODEL": "qwen-test",
        }
        with patch.dict(os.environ, environment, clear=True):
            provider = create_llm_provider(env_path=self.MISSING_ENV_PATH)
        self.assertIsInstance(provider, OllamaProvider)
        self.assertIsInstance(provider, LLMProvider)

    def test_blank_provider_keeps_ollama_default(self):
        environment = {
            "LLM_PROVIDER": "  ",
            "OLLAMA_BASE_URL": "http://localhost:11434",
            "OLLAMA_MODEL": "qwen-test",
        }
        with patch.dict(os.environ, environment, clear=True):
            provider = create_llm_provider(env_path=self.MISSING_ENV_PATH)
        self.assertIsInstance(provider, OllamaProvider)
    def test_openai_provider_is_selected_from_environment(self):
        environment = {
            "LLM_PROVIDER": "openai",
            "OPENAI_API_KEY": "test-key",
            "OPENAI_MODEL": "test-model",
        }
        with patch.dict(os.environ, environment, clear=True):
            provider = create_llm_provider(env_path=self.MISSING_ENV_PATH)
        self.assertIsInstance(provider, OpenAIProvider)
        self.assertIsInstance(provider, LLMProvider)

    def test_unknown_provider_is_rejected(self):
        with patch.dict(os.environ, {"LLM_PROVIDER": "unknown"}, clear=True):
            with self.assertRaises(LLMProviderConfigurationError):
                create_llm_provider(env_path=self.MISSING_ENV_PATH)

    def test_selected_provider_requires_its_own_configuration(self):
        with patch.dict(os.environ, {"LLM_PROVIDER": "openai"}, clear=True):
            with self.assertRaises(Exception) as context:
                create_llm_provider(env_path=self.MISSING_ENV_PATH)
        self.assertEqual(context.exception.code, "OPENAI_CONFIGURATION_ERROR")


if __name__ == "__main__":
    unittest.main()
