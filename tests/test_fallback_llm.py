import httpx
import pytest
import respx
from openai import APIStatusError

from pyrrhon.config.settings import Settings
from pyrrhon.core.providers.llm import (
    FallbackLLM,
    OpenAICompatLLM,
    create_llm_with_fallbacks,
)

B1 = "https://primary.example/v1"
B2 = "https://backup.example/v1"


def _completion(text: str) -> dict:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 0,
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": text},
            }
        ],
    }


def make_chain() -> FallbackLLM:
    return FallbackLLM(
        chain=[
            OpenAICompatLLM(model="m1", api_key="k", base_url=B1, max_retries=0),
            OpenAICompatLLM(model="m2", api_key="k", base_url=B2, max_retries=0),
        ]
    )


MESSAGES = [{"role": "user", "content": "hi"}]


@respx.mock
async def test_connect_error_falls_over_and_notifies():
    respx.post(f"{B1}/chat/completions").mock(side_effect=httpx.ConnectError("down"))
    respx.post(f"{B2}/chat/completions").mock(
        return_value=httpx.Response(200, json=_completion("from backup"))
    )
    fb = make_chain()
    switches: list[int] = []
    fb.on_switch = switches.append
    reply = await fb.chat(MESSAGES)
    assert reply.text == "from backup"
    assert switches == [1]
    assert fb.model == "m2"  # active member is now the backup


@respx.mock
async def test_5xx_falls_over_but_is_sticky_next_turn():
    r1 = respx.post(f"{B1}/chat/completions").mock(
        return_value=httpx.Response(500, json={"error": {"message": "boom"}})
    )
    respx.post(f"{B2}/chat/completions").mock(
        return_value=httpx.Response(200, json=_completion("ok"))
    )
    fb = make_chain()
    assert (await fb.chat(MESSAGES)).text == "ok"
    assert (await fb.chat(MESSAGES)).text == "ok"  # second turn skips the dead primary
    assert r1.call_count == 1


@respx.mock
async def test_4xx_does_not_fall_over():
    respx.post(f"{B1}/chat/completions").mock(
        return_value=httpx.Response(401, json={"error": {"message": "bad key"}})
    )
    r2 = respx.post(f"{B2}/chat/completions").mock(
        return_value=httpx.Response(200, json=_completion("never"))
    )
    with pytest.raises(APIStatusError):
        await make_chain().chat(MESSAGES)
    assert r2.call_count == 0


@respx.mock
async def test_all_exhausted_reraises_last():
    respx.post(f"{B1}/chat/completions").mock(side_effect=httpx.ConnectError("down"))
    respx.post(f"{B2}/chat/completions").mock(
        return_value=httpx.Response(503, json={"error": {"message": "overloaded"}})
    )
    with pytest.raises(APIStatusError) as excinfo:
        await make_chain().chat(MESSAGES)
    assert excinfo.value.status_code == 503


def test_factory_without_fallbacks_returns_plain_llm(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "sk-test")
    llm = create_llm_with_fallbacks("fast", Settings())
    assert isinstance(llm, OpenAICompatLLM)


def test_factory_builds_chain_and_skips_missing_keys(monkeypatch, caplog):
    monkeypatch.setenv("GROQ_API_KEY", "sk-g")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or")
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    settings = Settings(
        fallbacks={"fast": ["cerebras", "openrouter/meta-llama/llama-3.3-70b"]}
    )
    with caplog.at_level("WARNING", logger="pyrrhon.providers"):
        llm = create_llm_with_fallbacks("fast", settings)
    assert isinstance(llm, FallbackLLM)
    # primary (groq) + openrouter; cerebras skipped (no key)
    assert [m.model for m in llm.chain] == [
        "llama-3.3-70b-versatile",
        "meta-llama/llama-3.3-70b",
    ]
    assert "cerebras" in caplog.text


def test_factory_rejects_unknown_slot():
    with pytest.raises(KeyError):
        create_llm_with_fallbacks("medium", Settings())
