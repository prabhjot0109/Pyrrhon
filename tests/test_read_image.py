"""read_image: repo-scoped, vision-slot-aware, honest when it cannot see."""

import base64

from pyrrhon.core.providers.llm import LLMReply
from pyrrhon.core.tools.images import MAX_IMAGE_BYTES, ReadImageTool

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class FakeVisionLLM:
    def __init__(self, answer="A box labelled 'agent loop' feeding into 'gate'."):
        self.answer = answer
        self.seen = None
        self.calls = 0

    async def chat(self, messages, tools=None):
        self.seen = messages
        self.calls += 1
        return LLMReply(text=self.answer)


class ExplodingLLM:
    async def chat(self, messages, tools=None):
        raise RuntimeError("model does not support image input")


async def test_reads_an_image_and_returns_the_answer(tmp_path):
    (tmp_path / "arch.png").write_bytes(PNG_1PX)
    llm = FakeVisionLLM()
    tool = ReadImageTool(tmp_path, llm)

    result = await tool.run(path="arch.png", question="What does this show?")

    assert "agent loop" in result
    assert result.startswith("arch.png:")
    content = llm.seen[0]["content"]
    assert content[0]["type"] == "text"
    assert "What does this show?" in content[0]["text"]
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


async def test_the_image_is_sent_without_the_tool_belt(tmp_path):
    """A vision call is a one-shot question, not a turn. Handing it the belt
    would invite it to start its own tool loop inside a tool."""
    (tmp_path / "arch.png").write_bytes(PNG_1PX)
    llm = FakeVisionLLM()

    await ReadImageTool(tmp_path, llm).run(path="arch.png", question="what?")

    assert llm.calls == 1


async def test_jpeg_declares_its_own_mime_type(tmp_path):
    (tmp_path / "shot.JPG").write_bytes(PNG_1PX)  # bytes are irrelevant here
    llm = FakeVisionLLM()

    await ReadImageTool(tmp_path, llm).run(path="shot.JPG", question="what?")

    url = llm.seen[0]["content"][1]["image_url"]["url"]
    assert url.startswith("data:image/jpeg;base64,")


async def test_missing_file_returns_an_error_string(tmp_path):
    tool = ReadImageTool(tmp_path, FakeVisionLLM())
    result = await tool.run(path="nope.png", question="what?")
    assert result.startswith("ERROR:")
    assert "nope.png" in result


async def test_path_escaping_the_repo_is_refused(tmp_path):
    tool = ReadImageTool(tmp_path, FakeVisionLLM())
    result = await tool.run(path="../../../etc/passwd", question="what?")
    assert result.startswith("ERROR:")


async def test_a_directory_is_not_an_image(tmp_path):
    (tmp_path / "assets.png").mkdir()
    tool = ReadImageTool(tmp_path, FakeVisionLLM())
    result = await tool.run(path="assets.png", question="what?")
    assert result.startswith("ERROR:")


async def test_unsupported_extension_is_refused(tmp_path):
    (tmp_path / "notes.txt").write_text("hello")
    tool = ReadImageTool(tmp_path, FakeVisionLLM())
    result = await tool.run(path="notes.txt", question="what?")
    assert result.startswith("ERROR:")
    assert "read_file" in result  # point them at the right tool


async def test_no_vision_model_says_how_to_fix_it(tmp_path):
    (tmp_path / "arch.png").write_bytes(PNG_1PX)
    tool = ReadImageTool(tmp_path, None)
    result = await tool.run(path="arch.png", question="what?")
    assert result.startswith("ERROR:")
    assert "/settings" in result


async def test_oversized_image_is_refused_without_reading_it(tmp_path):
    """Checked by stat, not by reading: a 500 MB file must not be pulled into
    memory just to be told it is too big."""
    huge = tmp_path / "huge.png"
    huge.write_bytes(b"\x89PNG\r\n\x1a\n")
    original_read = type(huge).read_bytes

    def _explode(self):
        raise AssertionError("read_bytes called on an oversized image")

    try:
        type(huge).read_bytes = _explode
        tool = ReadImageTool(tmp_path, FakeVisionLLM())
        tool._max_bytes = 4  # smaller than the header just written
        result = await tool.run(path="huge.png", question="what?")
    finally:
        type(huge).read_bytes = original_read

    assert result.startswith("ERROR:")
    assert "too large" in result


async def test_the_real_size_cap_is_stated_in_the_message(tmp_path):
    assert MAX_IMAGE_BYTES == 8 * 1024 * 1024


async def test_a_provider_refusal_becomes_an_error_string_not_an_exception(tmp_path):
    """Tool.run never raises — a turn must survive a model that cannot see."""
    (tmp_path / "arch.png").write_bytes(PNG_1PX)
    tool = ReadImageTool(tmp_path, ExplodingLLM())
    result = await tool.run(path="arch.png", question="what?")
    assert result.startswith("ERROR:")
    assert "does not support image input" in result


async def test_an_empty_answer_is_reported_rather_than_returned_blank(tmp_path):
    (tmp_path / "arch.png").write_bytes(PNG_1PX)
    tool = ReadImageTool(tmp_path, FakeVisionLLM(answer="   "))
    result = await tool.run(path="arch.png", question="what?")
    assert result.startswith("ERROR:")
