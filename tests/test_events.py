from pyrrhon.core.events import Citation, SpeechChunk
from pyrrhon.core.providers.llm import LLMReply
from tests.helpers import FakeLLM


def test_events_are_immutable_values():
    chunk = SpeechChunk(text="hello")
    assert chunk.text == "hello"
    assert Citation(file="app.py", line=3) == Citation(file="app.py", line=3)


async def test_fake_llm_pops_replies_in_order_and_records_calls():
    fake = FakeLLM([LLMReply(text="first"), LLMReply(text="second")])
    first = await fake.chat([{"role": "user", "content": "a"}])
    second = await fake.chat([{"role": "user", "content": "b"}], tools=[{"x": 1}])
    assert (first.text, second.text) == ("first", "second")
    assert len(fake.calls) == 2
    assert fake.calls[1]["tools"] == [{"x": 1}]
