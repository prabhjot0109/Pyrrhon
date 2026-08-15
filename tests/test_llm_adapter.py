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
