"""Tool ABC: every agent capability (built-in or MCP-bridged later) exposes this shape.

`run_tool` lives here rather than beside either agent loop because it is a fact
about the tool contract — what a call may pass, and what to say when it did
not — and because both loops need it: `loop.py` already imports the deep
subagent from `escalate.py`, so a dispatcher owned by either one of them would
close an import cycle.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class Tool(ABC):
    name: str
    description: str
    parameters: dict  # JSON schema for the arguments object

    @abstractmethod
    async def run(self, **kwargs) -> str:
        """Execute and return text for the LLM. Failures return 'ERROR: ...' strings."""

    def accepted_parameters(self) -> list[str]:
        """The parameter names this tool actually takes.

        Read off the same schema the model was shown, so the two can never
        disagree — a hand-written list would be a second copy to drift.
        """
        return sorted(self.parameters.get("properties", {}))

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


async def run_tool(tools: dict[str, Tool], name: str, args: dict) -> str:
    """Run one tool call, turning a rejected one into a correctable one.

    A raw TypeError tells the model that `line_start` was unexpected and
    nothing about what it should have sent, so the next attempt is another
    guess — which is exactly what a real session showed: read_file called
    twice with line_start/line_end against a schema that says
    start_line/end_line. Naming the accepted parameters is what makes the
    retry informed rather than a second guess.
    """
    tool = tools.get(name)
    if tool is None:
        return f"ERROR: no tool named '{name}'."
    try:
        return await tool.run(**args)
    except TypeError as exc:
        accepted = ", ".join(tool.accepted_parameters())
        return f"ERROR: bad arguments for {name}: {exc}. Accepted parameters: {accepted}."
