"""
Semantic File Search Agent — agent #7 of the Agentic Shell.

Public surface for the base shell:

    from semantic_search import TOOLS, dispatch, handle

    TOOLS     — Anthropic tool definitions for this agent's capabilities
    dispatch  — execute one tool call by name, returns (json_payload, is_error)
    handle    — run a full natural-language request and return the answer
"""

from .registry import TOOLS, dispatch
from .safety import ToolError

__all__ = ["TOOLS", "dispatch", "ToolError", "handle", "AGENT_NAME", "AGENT_DESCRIPTION"]

AGENT_NAME = "semantic_file_search"
AGENT_DESCRIPTION = (
    "Finds files by describing them instead of by exact name: searches on "
    "modification time, size, type, name patterns and file contents, replacing "
    "hand-written find and grep pipelines. Route here for 'where is the file "
    "that...', 'find the config with...', 'what did I edit yesterday'."
)


def handle(*args, **kwargs):
    """Lazy re-export so importing TOOLS does not require the anthropic SDK."""
    from .agent import handle as _handle

    return _handle(*args, **kwargs)
