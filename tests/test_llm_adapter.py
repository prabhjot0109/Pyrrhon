import json

import httpx
import pytest
import respx

from pyrrhon.config.settings import ModelSlot, Settings
from pyrrhon.core.providers.llm import MissingAPIKeyError, OpenAICompatLLM, create_llm

BASE = "https://api.groq.com/openai/v1"


def _completion(message: dict) -> dict:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 0,
        "model": "test-model",
        "choices": [{"index": 0, "finish_reason": "stop", "message": message}],
    }


@respx.mock
async def test_chat_returns_text():
    respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(
            200, json=_completion({"role": "assistant", "content": "hi there"})
        )
    )
    llm = OpenAICompatLLM(model="test-model", api_key="k", base_url=BASE)
    reply = await llm.chat([{"role": "user", "content": "hello"}])
    assert reply.text == "hi there"
    assert reply.tool_calls == ()


@respx.mock
async def test_chat_parses_tool_calls():
    message = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "read_file", "arguments": '{"path": "app.py"}'},
            }
        ],
    }
    respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(200, json=_completion(message))
    )
    llm = OpenAICompatLLM(model="test-model", api_key="k", base_url=BASE)
    reply = await llm.chat([{"role": "user", "content": "read app.py"}])
    assert reply.tool_calls[0].name == "read_file"
    assert reply.tool_calls[0].arguments == {"path": "app.py"}


def test_create_llm_requires_api_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    settings = Settings()
    with pytest.raises(MissingAPIKeyError, match="GROQ_API_KEY"):
        create_llm(ModelSlot(provider="groq", model="m"), settings)


def test_create_llm_uses_provider_base_url(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "sk-test")
    settings = Settings()
    llm = create_llm(ModelSlot(provider="groq", model="m"), settings)
    assert llm.model == "m"


def test_create_llm_allows_keyless_local_provider(monkeypatch):
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    slot = ModelSlot(provider="ollama", model="qwen3:8b")
    llm = create_llm(slot, Settings())
    assert llm.model == "qwen3:8b"  # no MissingAPIKeyError for keyless providers


@respx.mock
async def test_generation_knobs_are_omitted_when_unset():
    """Default behaviour must be byte-identical to before [model] existed —
    providers differ on whether an explicit null is acceptable."""
    route = respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(
            200, json=_completion({"role": "assistant", "content": "ok"})
        )
    )
    llm = OpenAICompatLLM(model="test-model", api_key="k", base_url=BASE)
    await llm.chat([{"role": "user", "content": "hello"}])
    body = json.loads(route.calls[0].request.content)
    assert "max_tokens" not in body
    assert "temperature" not in body


@respx.mock
async def test_generation_knobs_are_sent_when_configured():
    route = respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(
            200, json=_completion({"role": "assistant", "content": "ok"})
        )
    )
    llm = OpenAICompatLLM(
        model="test-model", api_key="k", base_url=BASE,
        max_tokens=256, temperature=0.2,
    )
    await llm.chat([{"role": "user", "content": "hello"}])
    body = json.loads(route.calls[0].request.content)
    assert body["max_tokens"] == 256
    assert body["temperature"] == 0.2


def test_create_llm_plumbs_the_model_section(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    settings = Settings.model_validate({"model": {"max_tokens": 512, "temperature": 0.5}})
    llm = create_llm(ModelSlot(provider="groq", model="m"), settings)
    assert llm.max_tokens == 512
    assert llm.temperature == 0.5


# -- token usage: capture it, never require it -------------------------------


def _sse(*chunks: dict) -> str:
    """An OpenAI-style streamed body. [DONE] terminates it, as the SDK expects."""
    body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)
    return body + "data: [DONE]\n\n"


def _delta(**fields) -> dict:
    return {
        "id": "chatcmpl-1", "object": "chat.completion.chunk", "created": 0,
        "model": "test-model", **fields,
    }


@respx.mock
async def test_chat_captures_token_usage():
    payload = _completion({"role": "assistant", "content": "hi"})
    payload["usage"] = {
        "prompt_tokens": 120, "completion_tokens": 7, "total_tokens": 127
    }
    respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(200, json=payload)
    )
    llm = OpenAICompatLLM(model="test-model", api_key="k", base_url=BASE)
    reply = await llm.chat([{"role": "user", "content": "hey"}])

    assert reply.usage is not None
    assert (reply.usage.prompt, reply.usage.completion, reply.usage.total) == (
        120, 7, 127
    )


@respx.mock
async def test_missing_usage_block_is_not_an_error():
    """Local servers often omit usage entirely; that must not break a turn."""
    respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(
            200, json=_completion({"role": "assistant", "content": "hi"})
        )
    )
    llm = OpenAICompatLLM(model="test-model", api_key="k", base_url=BASE)
    reply = await llm.chat([{"role": "user", "content": "hey"}])

    assert reply.usage is None


@respx.mock
async def test_stream_asks_for_usage_and_carries_it_on_the_final_reply():
    """Providers omit usage from a stream unless stream_options asks for it.

    The usage-bearing chunk carries an empty `choices` list, which the delta
    loop skips — so the capture has to happen before that guard.
    """
    route = respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text=_sse(
                _delta(choices=[{"index": 0, "delta": {"content": "hi "}}]),
                _delta(choices=[{"index": 0, "delta": {"content": "there"}}]),
                _delta(choices=[], usage={
                    "prompt_tokens": 40, "completion_tokens": 2, "total_tokens": 42
                }),
            ),
        )
    )
    llm = OpenAICompatLLM(model="test-model", api_key="k", base_url=BASE)
    events = [
        event async for event in llm.stream([{"role": "user", "content": "hey"}])
    ]

    sent = json.loads(route.calls.last.request.content)
    assert sent["stream_options"] == {"include_usage": True}

    kind, reply = events[-1]
    assert kind == "reply"
    assert reply.text == "hi there"
    assert reply.usage is not None and reply.usage.prompt == 40


@respx.mock
async def test_stream_without_a_usage_chunk_still_yields_a_reply():
    respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text=_sse(_delta(choices=[{"index": 0, "delta": {"content": "hi"}}])),
        )
    )
    llm = OpenAICompatLLM(model="test-model", api_key="k", base_url=BASE)
    events = [
        event async for event in llm.stream([{"role": "user", "content": "hey"}])
    ]

    kind, reply = events[-1]
    assert kind == "reply" and reply.text == "hi" and reply.usage is None
