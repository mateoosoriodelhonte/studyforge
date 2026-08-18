"""Build the configured provider."""

from __future__ import annotations

from studyforge.ai.base import AIProvider
from studyforge.ai.none import NoAIProvider
from studyforge.ai.ollama import OllamaProvider
from studyforge.config import AIProvider as ProviderChoice
from studyforge.config import Settings


def build_provider(settings: Settings) -> AIProvider:
    """Return the provider named by configuration.

    Falls through to :class:`NoAIProvider` for anything unrecognised, so a typo
    in ``AI_PROVIDER`` degrades to the working default rather than preventing
    the application from starting.
    """
    if settings.ai_provider is ProviderChoice.OLLAMA:
        return OllamaProvider(
            base_url=settings.ollama_base_url,
            model=settings.ai_model,
            timeout_seconds=settings.ai_timeout_seconds,
        )
    return NoAIProvider()
