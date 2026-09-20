"""환경변수로 LLM Provider 구현을 선택한다."""

from __future__ import annotations

import os

from .llm_provider import LLMProvider


class LLMProviderConfigurationError(ValueError):
    """지원하지 않거나 비어 있는 Provider 설정."""

    code = "LLM_PROVIDER_CONFIGURATION_ERROR"


def create_llm_provider() -> LLMProvider:
    configured = os.getenv("LLM_PROVIDER")
    provider_name = (
        configured.strip().lower()
        if configured and configured.strip()
        else "ollama"
    )
    if provider_name == "ollama":
        from .ollama_provider import OllamaProvider

        return OllamaProvider()
    if provider_name == "openai":
        from .openai_provider import OpenAIProvider

        return OpenAIProvider()
    raise LLMProviderConfigurationError(
        "LLM_PROVIDER는 ollama 또는 openai여야 합니다."
    )