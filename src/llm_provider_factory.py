"""환경변수로 LLM Provider 구현을 선택한다."""

from __future__ import annotations

import os
from pathlib import Path

from .env_config import read_env_value
from .llm_provider import LLMProvider


class LLMProviderConfigurationError(ValueError):
    """지원하지 않거나 비어 있는 Provider 설정."""

    code = "LLM_PROVIDER_CONFIGURATION_ERROR"


PROJECT_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


def _setting(name: str, env_path: Path) -> str | None:
    if name in os.environ:
        return os.environ.get(name)
    return read_env_value(env_path, name)


def create_llm_provider(*, env_path: Path = PROJECT_ENV_PATH) -> LLMProvider:
    configured = _setting("LLM_PROVIDER", env_path)
    provider_name = (
        configured.strip().lower()
        if configured and configured.strip()
        else "ollama"
    )
    if provider_name == "ollama":
        from .ollama_provider import OllamaProvider

        return OllamaProvider(
            base_url=_setting("OLLAMA_BASE_URL", env_path),
            model=_setting("OLLAMA_MODEL", env_path),
        )
    if provider_name == "openai":
        from .openai_provider import OpenAIProvider

        return OpenAIProvider(
            api_key=_setting("OPENAI_API_KEY", env_path),
            model=_setting("OPENAI_MODEL", env_path),
        )
    raise LLMProviderConfigurationError(
        "LLM_PROVIDER는 ollama 또는 openai여야 합니다."
    )
