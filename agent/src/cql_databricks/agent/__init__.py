"""SVC-MCP-TOOLS, SVC-MCP-SKILLS — the assistant's tool surface (`agent/README.md`).

The five tools the Designer's own assistant and an external Genie Agent both call, and the guidance both
answer from (`skills.py`). This package holds the TOOLS; the transport that publishes them (MCP) is a
separate concern and a separate module, so the tools are testable without one and cannot acquire a
shape that only one client could use.

The guidance is split the same way and for the same reason: one implementation with two clients is
worth nothing if the two are told different things about how to use it.
"""
from __future__ import annotations

from .model import Column, Freshness, NodeAddress, Output, OutputShape
from .skills import ANSWERING_INSTRUCTIONS, Skill, UnknownSkillError, get_skill, list_skills
from .tools import (
    DataSourceLister, ReadOnlyViolation, Warehouse, get_freshness, get_output,
    list_data_sources, list_tables, query_data)
from .registry import ToolRegistry

__all__ = [
    "ANSWERING_INSTRUCTIONS", "Column", "Freshness", "NodeAddress", "Output", "OutputShape",
    "DataSourceLister", "ReadOnlyViolation", "Skill", "UnknownSkillError", "Warehouse",
    "ToolRegistry", "get_freshness", "get_output", "get_skill", "list_data_sources",
    "list_skills", "list_tables", "query_data",
]
