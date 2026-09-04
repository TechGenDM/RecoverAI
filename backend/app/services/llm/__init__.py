from app.config import settings

from .base import BaseLLMProvider
from .gemini_provider import GeminiProvider
from .mock_provider import MockLLMProvider


def get_llm_provider() -> BaseLLMProvider:
    if settings.LLM_PROVIDER == "gemini":
        # In tests, if GEMINI_API_KEY is not set, we can fallback to mock if desired,
        # but config should dictate.
        try:
            return GeminiProvider()
        except ValueError:
            # Fallback to mock if API key is missing (useful for basic local tests without keys)
            return MockLLMProvider()
    return MockLLMProvider()
