"""hello-reviewer tools entry point: get_tools() -> list[Tool]."""

from pyrrhon.core.tools.base import Tool

CHECKLIST = """\
Code review checklist (hello-reviewer):
1. Correctness — does the change do what it claims? Which edge case breaks it?
2. Tests — is the new behavior covered, and do the tests fail without the change?
3. Naming — do names say what things are, not how they are built?
4. Error handling — are failures surfaced to the caller, not swallowed?
5. Scope — is anything in the diff unrelated to the stated goal?
6. Docs — do comments and README still tell the truth after this change?
"""


class ChecklistTool(Tool):
    name = "checklist"
    description = "Return hello-reviewer's static code-review checklist."
    parameters = {"type": "object", "properties": {}, "required": []}

    async def run(self, **kwargs) -> str:
        return CHECKLIST


def get_tools() -> list[Tool]:
    return [ChecklistTool()]
