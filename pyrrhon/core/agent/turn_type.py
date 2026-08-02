"""Classify a turn before spending a model round on it.

SYSTEM_PROMPT already asks the model to work out "what kind of turn is this"
before reaching for a tool. That works, but it still burns a full round with
~1.5k tokens of tool schemas attached just to decide that "hi" needs no tools
— and it leaves the door open to a spurious tool call on a turn that plainly
had nothing to look up.

This is the cheap half of that decision, done in pure Python before the
request is built. It only ever *withholds* tools; it never chooses one, never
answers, and never changes the prompt. The worst case for a misclassification
is therefore an answer given without repo access, which the model handles by
asking a clarifying question — not a wrong claim about the code.

The bar for withholding is deliberately high. Anything that is not obviously
social or obviously an unanchored follow-up gets the full belt.
"""

from __future__ import annotations

import re

SOCIAL = "social"
AMBIGUOUS_FOLLOWUP = "ambiguous_followup"
REPO_QUESTION = "repo_question"

# Anchored and exact — the whole string must be one of these, optionally with
# trailing punctuation. "hi" matches; "hi, where is the auth middleware" does
# not, because ^...$ does not permit the tail. Substring matching here would
# be a grounding hazard, not just a latency one.
_SOCIAL_RE = re.compile(
    r"^(hi|hey|hello|yo|thanks|thank you|ta|cheers|ok|okay|k|yes|yeah|yep|"
    r"sure|no|nope|go on|keep going|carry on|continue|sounds good|got it|"
    r"nice|cool|great|perfect|awesome|right|indeed|makes sense|"
    r"good morning|good afternoon|good evening|bye|goodbye|see you)"
    r"[.!?]*$",
    re.IGNORECASE,
)

# A short reply is only ambiguous if it carries no repo-ish noun. These are
# the cheap signals that the user is pointing at code even in few words.
#
# NOT compiled with re.IGNORECASE: that flag would apply to the whole pattern
# and turn the camelCase probe [a-z][A-Z] into "any two adjacent letters",
# which matches essentially every English word. Case-insensitivity is scoped
# to the word list with (?i:...), where it is actually wanted.
_CODE_HINT_RE = re.compile(
    r"[/\\]"                 # a path separator
    r"|\.\w{1,4}\b"          # a file extension
    r"|[a-z]_[a-z]"          # snake_case
    r"|[a-z][A-Z]"           # camelCase — case-sensitive, deliberately
    r"|\(\)"                 # a call
    r"|::"
    r"|(?i:\b(file|files|function|func|method|class|module|line|lines|test|"
    r"tests|error|bug|import|imports|call|calls|caller|callers|type|types|"
    r"config|repo|code|dir|directory|package|api|schema|db|database|query|"
    r"route|endpoint|handler|loop|cache|queue|thread|async|await)\b)"
)

MAX_SOCIAL_WORDS = 4
MAX_FOLLOWUP_WORDS = 6


def classify(user_text: str, history: list[dict] | None = None) -> str:
    """Return SOCIAL, AMBIGUOUS_FOLLOWUP, or REPO_QUESTION.

    `history` is read only to see whether Pyrrhon's own last message ended in
    a question — a bare "yes" answering an offer is exactly the case where
    launching a search is the wrong move.
    """
    text = (user_text or "").strip()
    if not text:
        return SOCIAL
    words = text.split()

    if len(words) <= MAX_SOCIAL_WORDS and _SOCIAL_RE.match(text):
        return SOCIAL

    if (
        len(words) <= MAX_FOLLOWUP_WORDS
        and not _CODE_HINT_RE.search(text)
        and _last_assistant_asked(history)
    ):
        return AMBIGUOUS_FOLLOWUP

    return REPO_QUESTION


def needs_tools(turn_type: str) -> bool:
    return turn_type == REPO_QUESTION


def _last_assistant_asked(history: list[dict] | None) -> bool:
    for message in reversed(history or []):
        if message.get("role") != "assistant":
            continue
        content = message.get("content")
        if not isinstance(content, str):
            return False  # a tool_calls message: not a question to the user
        return content.strip().endswith("?")
    return False
