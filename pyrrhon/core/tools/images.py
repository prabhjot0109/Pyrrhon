"""read_image: let the agent look at a diagram, screenshot, or mockup.

Design note. Tool.run() returns str (tools/base.py), so this tool makes the
vision call ITSELF and returns prose rather than handing an image back to the
main model. That keeps the Tool ABC, the agent loop, and the LLM client
unchanged; it also works when the conversational model is text-only. Asking a
different question about the same image is a second call, which is the "look
again" affordance.

Grounding: an image has no line numbers, so a claim sourced from one cites the
PATH only. EvidenceLedger.record_file expresses exactly that.
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path

from pyrrhon.core.tools.base import Tool
from pyrrhon.core.tools.repo import _resolve_inside

# Formats every vision endpoint on the provider table accepts.
_SUFFIXES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

# Providers reject oversized payloads with an opaque 400; refuse earlier with a
# message the user can act on. 8 MiB of raw bytes is ~11 MB base64-encoded.
MAX_IMAGE_BYTES = 8 * 1024 * 1024

_PROMPT = (
    "You are looking at an image from a software repository — usually an "
    "architecture diagram, a screenshot, or a UI mockup. Answer the question "
    "precisely and concretely: name the boxes, arrows, labels, and any text you "
    "can read. If the image does not contain enough information to answer, say "
    "so plainly rather than guessing."
)

_NO_VISION = (
    "ERROR: no vision-capable model is configured. Set one with "
    "/settings llm vision <provider> <model>, or point your fast slot at a "
    "model that can see."
)


class ReadImageTool(Tool):
    name = "read_image"
    description = (
        "Look at an image in the repo (architecture diagram, screenshot, mockup) "
        "and answer one question about it. For .png/.jpg/.gif/.webp only — use "
        "read_file for text. Call again with a different question to look closer."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Repo-relative path, e.g. docs/architecture.png",
            },
            "question": {
                "type": "string",
                "description": "What you want to know about this image.",
            },
        },
        "required": ["path", "question"],
    }

    def __init__(self, repo_root: Path, llm) -> None:
        self.repo_root = Path(repo_root)
        self.llm = llm  # a vision-capable LLM, or None when none is configured
        self._max_bytes = MAX_IMAGE_BYTES

    def _load(self, path: str) -> tuple[str | None, str]:
        """(data-url, "") on success, or (None, "ERROR: ...").

        Runs off the event loop — see repo.py's real-time discipline. The size
        is checked with stat() BEFORE reading, so a huge file is refused
        without ever being pulled into memory, and base64 (real CPU work at
        several MB) is encoded here rather than on the loop.
        """
        target = _resolve_inside(self.repo_root, path)
        if target is None:
            return None, f"ERROR: '{path}' is outside the repo."
        mime = _SUFFIXES.get(target.suffix.lower())
        if mime is None:
            return None, (
                f"ERROR: '{path}' is not an image I can read "
                f"({', '.join(sorted(_SUFFIXES))}). Use read_file for text files."
            )
        if not target.is_file():
            return None, f"ERROR: '{path}' does not exist."
        size = target.stat().st_size
        if size > self._max_bytes:
            return None, (
                f"ERROR: '{path}' is too large ({size // 1024} KB; the limit is "
                f"{self._max_bytes // 1024} KB). Downscale it first."
            )
        encoded = base64.b64encode(target.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{encoded}", ""

    async def run(self, path: str = "", question: str = "") -> str:
        if self.llm is None:
            return _NO_VISION
        data_url, error = await asyncio.to_thread(self._load, path)
        if data_url is None:
            return error
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"{_PROMPT}\n\nQuestion: {question}"},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ]
        try:
            # No tools: this is a one-shot question, not a turn. Handing it the
            # belt would invite a tool loop nested inside a tool call.
            reply = await self.llm.chat(messages)
        except Exception as exc:  # provider refused, model cannot see, network
            return f"ERROR: could not read '{path}' ({exc})."
        answer = (reply.text or "").strip()
        if not answer:
            return f"ERROR: the vision model returned nothing for '{path}'."
        return f"{path}:\n{answer}"
