"""Tool ABC: every agent capability (built-in or MCP-bridged later) exposes this shape."""

from __future__ import annotations

from abc import ABC, abstractmethod


class Tool(ABC):
    name: str
    description: str
    parameters: dict  # JSON schema for the arguments object

    @abstractmethod
    async def run(self, **kwargs) -> str:
        """Execute and return text for the LLM. Failures return 'ERROR: ...' strings."""

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
