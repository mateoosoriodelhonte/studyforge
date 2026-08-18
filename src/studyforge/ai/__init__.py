"""Optional AI providers.

StudyForge is fully functional with no provider configured, and that is the
default. Everything an AI can do here is an *enhancement* of a deterministic
path that already works: concept extraction, card generation, quiz generation
and -- above all -- scheduling all run without a model.

The rules an implementation must obey are in :mod:`studyforge.ai.base`.
"""

from studyforge.ai.base import (
    AIProvider,
    AIUnavailableError,
    Explanation,
    ExtractedConcept,
    ExtractedConcepts,
    GeneratedCard,
    GeneratedCards,
    GeneratedQuestion,
    GeneratedQuestions,
    ProviderStatus,
)
from studyforge.ai.factory import build_provider
from studyforge.ai.none import NoAIProvider
from studyforge.ai.ollama import OllamaProvider

__all__ = [
    "AIProvider",
    "AIUnavailableError",
    "Explanation",
    "ExtractedConcept",
    "ExtractedConcepts",
    "GeneratedCard",
    "GeneratedCards",
    "GeneratedQuestion",
    "GeneratedQuestions",
    "NoAIProvider",
    "OllamaProvider",
    "ProviderStatus",
    "build_provider",
]
