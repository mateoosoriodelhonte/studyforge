# AI providers

AI in StudyForge is **optional, off by default, and always an enhancement of a
path that already works**. This document covers what each provider does, what
it costs, and how to configure it.

## The default: no AI

```env
AI_PROVIDER=none
```

This is not a degraded mode. With no AI configured you still get concept
extraction, flashcard generation, quiz generation, the full spaced-repetition
engine, weak-concept analysis, search, and passage retrieval for *Ask my notes*.

**Cost: nothing. Data leaving your machine: none.**

## Ollama — local models

[Ollama](https://ollama.com) runs open-weight models on your own hardware.

```env
AI_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
AI_MODEL=llama3.2
AI_TIMEOUT_SECONDS=60
```

Setup:

```bash
# 1. Install Ollama, then pull a model yourself
ollama pull llama3.2

# 2. Make sure it is running
ollama serve

# 3. Start StudyForge with the provider enabled
AI_PROVIDER=ollama uv run studyforge serve
```

The Settings page reports whether Ollama is reachable and whether the model is
installed.

**Cost: nothing per request — it is your CPU or GPU.**
**Data leaving your machine: none.** Requests go to `localhost` only.

### StudyForge never downloads models

A model is several gigabytes. Pulling one because a user clicked a button would
be an appalling default, so StudyForge does not do it. If the configured model
is missing, the Settings page says so and gives you the `ollama pull` command,
and generation continues down the deterministic path.

### Choosing a model

Any chat model Ollama supports will work. Smaller models are faster and produce
weaker cards; the deterministic generator is often better than a small model,
which is worth trying before assuming AI helps.

## Why no cloud provider is bundled

StudyForge does not ship an OpenAI, Anthropic or Hugging Face integration in
v1.0. This is a deliberate scope decision rather than an oversight:

- **The zero-cost promise.** Any hosted inference either costs money or depends
  on a free allowance that can change without notice. A project that promises
  "$0 forever" should not depend on someone else's pricing page staying still.
- **The privacy promise.** Ollama lets StudyForge say "nothing leaves your
  machine" without qualification. Adding a cloud provider would replace a
  simple, checkable claim with a conditional one.
- **The interface already exists.** `AIProvider` in `ai/base.py` is a Protocol
  with four methods. Adding a provider means implementing it and adding a
  branch to `ai/factory.py` — a contained change, not a redesign.

If you add one, `docs/PRIVACY.md` must be updated to say what leaves the
machine. That is not optional politeness; it is the point of the document.

## The provider contract

```python
class AIProvider(Protocol):
    name: str

    async def status(self) -> ProviderStatus: ...
    async def generate_flashcards(self, *, passage, concept=None, count=5) -> GeneratedCards: ...
    async def generate_quiz(self, *, passage, count=5) -> GeneratedQuestions: ...
    async def explain_answer(self, *, question, expected_answer, passages) -> Explanation: ...
    async def extract_concepts(self, *, passage) -> ExtractedConcepts: ...
```

Two rules an implementation must obey:

1. **`status()` never raises.** It reports unreachability instead.
2. **Every other method raises only `AIUnavailableError`.** Turning a
   provider's idiosyncratic failures into one predictable error is the whole
   point of the boundary — every caller does the same thing with it.

## Model output is untrusted input

A language model is a remote service that returns a string. It can return
malformed JSON, invent fields, omit required ones, return nothing, return five
hundred items, hang, or be switched off. Each of those is ordinary, not
exceptional, and each is handled:

| Failure | What StudyForge does |
|---|---|
| Malformed JSON, or wrapped in a code fence | Strips fences, then falls back with a clear message |
| Hallucinated or missing fields | Rejected by Pydantic |
| Blank card side | Rejected |
| More than 25 items | Rejected — a model that ignores "five" is malfunctioning |
| Empty result | **Accepted.** "I could not make a good card from this" is a legitimate answer |
| `correct_choice_index` past the end of the choices | Question dropped — schema-valid but unusable |
| Duplicate choices | Rejected — two correct answers is not a question |
| Citation to a passage never supplied | Stripped |
| Timeout / refused / 404 / 429 / 5xx | One `AIUnavailableError` with a plain-language message |

Beyond validation, a provider may **never**: influence scheduling, construct
SQL or a filesystem path, make an authorisation decision, or have its output
rendered as HTML.

## Provenance

Material generated with AI records the provider, the model and the time it was
generated, and the UI labels it. You can always tell what wrote a card.

## Testing

Every provider path is covered against a mock HTTP transport, including all the
failure modes above. **CI never contacts a live model**, and `AI_PROVIDER` is
pinned to `none` in the workflow so an accidental real call fails loudly rather
than passing quietly.
