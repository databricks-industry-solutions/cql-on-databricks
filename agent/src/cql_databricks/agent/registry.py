"""AGENT-TOOL-REGISTRY, DEC-AGENT-DESIGNER-CYCLE — tools another feature contributes to the agent.

The agent owns the tool surface and the loop; a feature ABOVE it in the dependency order (the designer)
contributes the tools only it can answer, by registering them here. The agent never imports that
feature: the edge points one way, from the contributor to the agent (`docs/systems.md`).

A registered tool is published exactly like a built-in one — its name, signature and docstring are what
an MCP client and the in-product drawer both see — so a contributed tool cannot drift from a built-in.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

ToolFunction = Callable[..., Any]


class ToolRegistry:
    """An ordered set of tool functions, installed onto an MCP server by ``create_mcp``."""

    def __init__(self, *, instructions: str = "") -> None:
        self._tools: list[ToolFunction] = []
        #: What the contributor adds to the standing instructions (`skills.standing_instructions`).
        self.instructions = instructions

    def tool(self, fn: ToolFunction) -> ToolFunction:
        """Register ``fn`` (usable as a decorator). A second tool with the same name is refused."""
        if any(t.__name__ == fn.__name__ for t in self._tools):
            raise ValueError(f"a tool named {fn.__name__!r} is already registered")
        self._tools.append(fn)
        return fn

    def __iter__(self) -> Iterator[ToolFunction]:
        return iter(self._tools)

    def names(self) -> list[str]:
        return [t.__name__ for t in self._tools]
