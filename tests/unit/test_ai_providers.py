"""AI providers: the no-AI default, and Ollama against a mocked transport.

CI never contacts a live model. Every request in these tests is answered by an
in-process mock, which is also the only way to exercise the failure modes that
matter -- timeouts, a missing model, rate limiting, and a model that returns
confident nonsense.
"""

from __future__ import annotations

import json
from typing import Any

import httpx2 as httpx
import pytest

from studyforge.ai.base import (
    MAX_ITEMS,
    AIUnavailableError,
    GeneratedQuestion,
    ProviderState,
)
from studyforge.ai.factory import build_provider
from studyforge.ai.none import NoAIProvider
from studyforge.ai.ollama import OllamaProvider
from studyforge.config import AIProvider as ProviderChoice
from studyforge.config import Settings

PASSAGE = "An AVL tree is a self-balancing binary search tree."


def transport(handler: Any) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def chat_reply(content: str) -> httpx.Response:
    return httpx.Response(200, json={"message": {"content": content}})


def ollama(handler: Any, **kwargs: Any) -> OllamaProvider:
    return OllamaProvider(model="test-model", transport=transport(handler), **kwargs)


class TestNoAIProvider:
    async def test_reports_itself_disabled(self) -> None:
        status = await NoAIProvider().status()
        assert status.state is ProviderState.DISABLED
        assert not status.is_enabled
        assert "without it" in status.detail

    @pytest.mark.parametrize(
        "call",
        [
            lambda p: p.generate_flashcards(passage=PASSAGE),
            lambda p: p.generate_quiz(passage=PASSAGE),
            lambda p: p.extract_concepts(passage=PASSAGE),
            lambda p: p.explain_answer(question="q", expected_answer="a", passages=[]),
        ],
    )
    async def test_every_capability_raises_the_one_predictable_error(self, call: Any) -> None:
        with pytest.raises(AIUnavailableError) as caught:
            await call(NoAIProvider())
        assert "No AI provider is configured" in caught.value.message


class TestFactory:
    def test_defaults_to_no_ai(self) -> None:
        assert isinstance(build_provider(Settings()), NoAIProvider)

    def test_builds_ollama_when_selected(self) -> None:
        provider = build_provider(Settings(ai_provider=ProviderChoice.OLLAMA))
        assert isinstance(provider, OllamaProvider)


class TestOllamaStatus:
    async def test_ready_when_the_model_is_installed(self) -> None:
        provider = ollama(
            lambda _request: httpx.Response(200, json={"models": [{"name": "test-model:latest"}]})
        )
        status = await provider.status()
        assert status.state is ProviderState.READY
        assert status.is_ready

    async def test_reports_a_missing_model_without_offering_to_download_it(self) -> None:
        """Pulling gigabytes because someone clicked a button would be appalling."""
        provider = ollama(
            lambda _request: httpx.Response(200, json={"models": [{"name": "something-else"}]})
        )
        status = await provider.status()
        assert status.state is ProviderState.MODEL_MISSING
        assert "ollama pull test-model" in status.detail
        assert "does not download models for you" in status.detail

    async def test_reports_unreachable_rather_than_raising(self) -> None:
        def refuse(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        status = await ollama(refuse).status()
        assert status.state is ProviderState.UNREACHABLE
        assert "ollama serve" in status.detail

    async def test_a_malformed_tags_response_is_survivable(self) -> None:
        status = await ollama(lambda _request: httpx.Response(200, text="not json")).status()
        assert status.state is ProviderState.UNREACHABLE


class TestOllamaGeneration:
    async def test_valid_output_is_parsed(self) -> None:
        payload = {"cards": [{"front": "What is an AVL tree?", "back": "A balanced BST."}]}
        provider = ollama(lambda _request: chat_reply(json.dumps(payload)))
        result = await provider.generate_flashcards(passage=PASSAGE)
        assert len(result.cards) == 1
        assert result.cards[0].front == "What is an AVL tree?"

    async def test_output_wrapped_in_a_code_fence_is_still_parsed(self) -> None:
        """Models add fences even when told not to."""
        fenced = '```json\n{"cards": [{"front": "F", "back": "B"}]}\n```'
        provider = ollama(lambda _request: chat_reply(fenced))
        assert len((await provider.generate_flashcards(passage=PASSAGE)).cards) == 1

    async def test_malformed_json_becomes_a_clean_failure(self) -> None:
        provider = ollama(lambda _request: chat_reply("this is not json at all"))
        with pytest.raises(AIUnavailableError) as caught:
            await provider.generate_flashcards(passage=PASSAGE)
        assert "valid JSON" in caught.value.message
        assert "Falling back" in caught.value.message

    async def test_hallucinated_fields_are_rejected(self) -> None:
        payload = {"cards": [{"question": "wrong key", "answer": "wrong key"}]}
        provider = ollama(lambda _request: chat_reply(json.dumps(payload)))
        with pytest.raises(AIUnavailableError):
            await provider.generate_flashcards(passage=PASSAGE)

    async def test_a_blank_card_side_is_rejected(self) -> None:
        payload = {"cards": [{"front": "   ", "back": "something"}]}
        provider = ollama(lambda _request: chat_reply(json.dumps(payload)))
        with pytest.raises(AIUnavailableError):
            await provider.generate_flashcards(passage=PASSAGE)

    async def test_an_excessive_number_of_items_is_refused(self) -> None:
        """A model asked for five cards that returns hundreds is malfunctioning."""
        payload = {"cards": [{"front": f"F{i}", "back": f"B{i}"} for i in range(MAX_ITEMS + 5)]}
        provider = ollama(lambda _request: chat_reply(json.dumps(payload)))
        with pytest.raises(AIUnavailableError):
            await provider.generate_flashcards(passage=PASSAGE)

    async def test_an_empty_result_is_accepted_as_a_real_answer(self) -> None:
        """ "I could not make a good card from this" is a legitimate response."""
        provider = ollama(lambda _request: chat_reply('{"cards": []}'))
        assert (await provider.generate_flashcards(passage=PASSAGE)).cards == []

    async def test_an_empty_model_response_is_a_failure(self) -> None:
        provider = ollama(lambda _request: httpx.Response(200, json={"message": {"content": ""}}))
        with pytest.raises(AIUnavailableError, match="empty"):
            await provider.generate_flashcards(passage=PASSAGE)


class TestOllamaFailureModes:
    async def test_a_timeout_is_reported_in_plain_language(self) -> None:
        def slow(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("too slow", request=request)

        with pytest.raises(AIUnavailableError, match="longer than"):
            await ollama(slow, timeout_seconds=5).generate_quiz(passage=PASSAGE)

    async def test_a_refused_connection_says_what_to_check(self) -> None:
        def refuse(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        with pytest.raises(AIUnavailableError, match="Is it running"):
            await ollama(refuse).generate_quiz(passage=PASSAGE)

    async def test_a_404_is_read_as_a_missing_model(self) -> None:
        provider = ollama(lambda _request: httpx.Response(404, json={"error": "not found"}))
        with pytest.raises(AIUnavailableError, match="ollama pull"):
            await provider.generate_quiz(passage=PASSAGE)

    async def test_rate_limiting_is_handled(self) -> None:
        provider = ollama(lambda _request: httpx.Response(429, json={"error": "slow down"}))
        with pytest.raises(AIUnavailableError, match="rate limiting"):
            await provider.generate_quiz(passage=PASSAGE)

    async def test_a_server_error_is_handled(self) -> None:
        provider = ollama(lambda _request: httpx.Response(500, text="boom"))
        with pytest.raises(AIUnavailableError, match="returned an error"):
            await provider.generate_quiz(passage=PASSAGE)

    async def test_failure_messages_never_leak_internals(self) -> None:
        def refuse(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        with pytest.raises(AIUnavailableError) as caught:
            await ollama(refuse).generate_quiz(passage=PASSAGE)
        for leak in ("Traceback", "site-packages", "httpx2.", "0x"):
            assert leak not in caught.value.message


class TestOllamaQuizCoherence:
    async def test_a_question_whose_answer_index_is_out_of_range_is_dropped(self) -> None:
        """Schema-valid but unusable. A model does this often."""
        payload = {
            "questions": [
                {
                    "prompt": "Which is balanced?",
                    "expected_answer": "AVL",
                    "choices": ["AVL", "List"],
                    "correct_choice_index": 7,
                },
                {
                    "prompt": "Good one?",
                    "expected_answer": "AVL",
                    "choices": ["AVL", "List"],
                    "correct_choice_index": 0,
                },
            ]
        }
        provider = ollama(lambda _request: chat_reply(json.dumps(payload)))
        result = await provider.generate_quiz(passage=PASSAGE)
        assert len(result.questions) == 1
        assert result.questions[0].prompt == "Good one?"

    async def test_duplicate_choices_are_rejected(self) -> None:
        payload = {
            "questions": [
                {
                    "prompt": "p",
                    "expected_answer": "a",
                    "choices": ["same", "same"],
                    "correct_choice_index": 0,
                }
            ]
        }
        provider = ollama(lambda _request: chat_reply(json.dumps(payload)))
        with pytest.raises(AIUnavailableError):
            await provider.generate_quiz(passage=PASSAGE)

    def test_a_single_choice_question_is_not_coherent(self) -> None:
        question = GeneratedQuestion(
            prompt="p", expected_answer="a", choices=["only"], correct_choice_index=0
        )
        assert not question.is_coherent()

    def test_a_short_answer_question_needs_no_choices(self) -> None:
        assert GeneratedQuestion(prompt="p", expected_answer="a").is_coherent()


class TestGrounding:
    async def test_a_citation_to_a_source_we_never_supplied_is_dropped(self) -> None:
        """A model citing source 9 when given two is hallucinating."""
        payload = {"explanation": "Because trees.", "used_sources": [0, 9, -1, 1]}
        provider = ollama(lambda _request: chat_reply(json.dumps(payload)))
        result = await provider.explain_answer(
            question="Why?", expected_answer="Because", passages=["one", "two"]
        )
        assert result.used_sources == [0, 1]

    async def test_only_the_supplied_passages_are_sent(self) -> None:
        """The prompt must carry the minimum, never unrelated documents."""
        captured: dict[str, Any] = {}

        def capture(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return chat_reply('{"explanation": "ok", "used_sources": [0]}')

        await ollama(capture).explain_answer(
            question="Why is it O(log n)?",
            expected_answer="Balanced height",
            passages=["the only passage provided"],
        )
        sent = json.dumps(captured["body"])
        assert "the only passage provided" in sent
        assert "SECRET" not in sent
