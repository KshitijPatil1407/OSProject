"""
Tool registry for the Semantic File Search Agent.

`TOOLS` is the list of Anthropic tool definitions; `dispatch` executes one by
name. The base shell can import both to merge this agent's capabilities into a
larger tool set, or it can call `semantic_search.agent.handle()` and let this
agent run its own loop.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from .content import identify_file, preview_file, search_file_content
from .metadata import list_directory, search_by_metadata, stat_file
from .safety import ToolError

TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_by_metadata",
        "description": (
            "Find files and directories by their metadata: name pattern, extension, "
            "modification/access time, size, type, and depth. This is the structural "
            "search — use it first to build a shortlist, then pass that shortlist to "
            "search_file_content if the user also cares about what is inside the file. "
            "Every filter is optional and they are ANDed together. Returns paths with "
            "size, timestamps, permissions and owner."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "root": {
                    "type": "string",
                    "description": "Directory to search under. Defaults to the user's home.",
                },
                "name_pattern": {
                    "type": "string",
                    "description": "Shell glob matched against the file name only, e.g. '*.conf' or 'settings*'.",
                },
                "name_regex": {
                    "type": "string",
                    "description": "Regular expression matched against the file name, when a glob is not expressive enough.",
                },
                "extensions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Extensions to accept, with or without the dot, e.g. ['conf','yaml','env'].",
                },
                "file_type": {
                    "type": "string",
                    "enum": ["file", "dir", "symlink", "any"],
                    "description": "Restrict by entry type. Defaults to 'file'.",
                },
                "modified_after": {
                    "type": "string",
                    "description": "Only entries modified at or after this time. ISO timestamp, 'yesterday', 'today', or a relative form like '3 days ago'.",
                },
                "modified_before": {
                    "type": "string",
                    "description": "Only entries modified at or before this time. Same formats as modified_after.",
                },
                "accessed_after": {
                    "type": "string",
                    "description": "Only entries read at or after this time. Same formats as modified_after.",
                },
                "min_size": {
                    "type": "string",
                    "description": "Minimum size, e.g. '10M', '512k', or a byte count.",
                },
                "max_size": {
                    "type": "string",
                    "description": "Maximum size, e.g. '1M'.",
                },
                "max_depth": {
                    "type": "integer",
                    "description": "How many directory levels below root to descend. Omit for unlimited (capped at 64).",
                },
                "include_hidden": {
                    "type": "boolean",
                    "description": "Include dotfiles and dot-directories. Set true when looking for config files such as .env or .bashrc.",
                },
                "sort_by": {
                    "type": "string",
                    "enum": ["mtime", "atime", "ctime", "size", "name", "path"],
                    "description": "Result ordering. Time and size sorts are newest/largest first. Defaults to 'mtime'.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum results to return (capped at 200). Defaults to 50.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "search_file_content",
        "description": (
            "Search inside files for a regular expression and return the matching "
            "lines with their line numbers. Pass 'paths' with a shortlist from "
            "search_by_metadata to grep only the likely candidates — that is much "
            "faster and far more precise than scanning a whole home directory. Pass "
            "'root' only when you genuinely need a broad content sweep. Binary files "
            "and caches are skipped automatically."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regular expression to search for. Alternate spellings with '|', e.g. 'password|passwd|secret|DB_PASS'.",
                },
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Specific files or directories to search. Prefer this over 'root' when you already have candidates.",
                },
                "root": {
                    "type": "string",
                    "description": "Directory to search under when no shortlist is available. Defaults to the user's home.",
                },
                "file_glob": {
                    "type": "string",
                    "description": "Only search files whose name matches this glob, e.g. '*.py'.",
                },
                "extensions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Only search files with these extensions.",
                },
                "ignore_case": {
                    "type": "boolean",
                    "description": "Case-insensitive matching. Defaults to true.",
                },
                "include_hidden": {
                    "type": "boolean",
                    "description": "Search dotfiles too. Defaults to true, since config files are usually hidden.",
                },
                "max_matches": {
                    "type": "integer",
                    "description": "Total match lines to return (capped at 100). Defaults to 40.",
                },
                "max_matches_per_file": {
                    "type": "integer",
                    "description": "Match lines per file, so one noisy file cannot crowd out the rest. Defaults to 3.",
                },
                "context_lines": {
                    "type": "integer",
                    "description": "Lines of surrounding context per match. Defaults to 0.",
                },
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "stat_file",
        "description": (
            "Full metadata for one path: size, all three timestamps, permission bits, "
            "owner, inode, hard-link count, and the target if it is a symlink. Use it "
            "to confirm a candidate before reporting it to the user."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The file or directory to inspect."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "preview_file",
        "description": (
            "Read a bounded slice of a text file so you can verify it is the file the "
            "user meant before answering. Returns at most 200 lines and refuses to "
            "dump binary data."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File to read."},
                "start_line": {"type": "integer", "description": "1-based line to start from. Defaults to 1."},
                "max_lines": {"type": "integer", "description": "Lines to return, capped at 200. Defaults to 40."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "list_directory",
        "description": (
            "List one directory level without recursing. Use it to orient yourself "
            "when the user's description of a location is vague, before committing to "
            "a deep recursive search."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory to list."},
                "include_hidden": {"type": "boolean", "description": "Include dotfiles."},
                "limit": {"type": "integer", "description": "Maximum entries, capped at 200."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "identify_file",
        "description": (
            "Run file(1) to determine a file's real type from its contents rather than "
            "its extension. Useful when the user describes a file by what it is rather "
            "than what it is named."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File to identify."},
            },
            "required": ["path"],
        },
    },
]

HANDLERS: dict[str, Callable[..., Any]] = {
    "search_by_metadata": search_by_metadata,
    "search_file_content": search_file_content,
    "stat_file": stat_file,
    "preview_file": preview_file,
    "list_directory": list_directory,
    "identify_file": identify_file,
}


def dispatch(name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
    """Run one tool call.

    Returns (payload, is_error). The payload is JSON text ready to drop into a
    tool_result block. Errors are returned rather than raised so the model can
    correct itself instead of the whole turn failing.
    """
    handler = HANDLERS.get(name)
    if handler is None:
        return json.dumps({"error": f"unknown tool {name!r}"}), True

    if not isinstance(arguments, dict):
        return json.dumps({"error": "tool input must be an object"}), True

    try:
        result = handler(**arguments)
    except ToolError as exc:
        return json.dumps({"error": str(exc)}), True
    except TypeError as exc:
        # Wrong or misspelled argument names — tell the model exactly what broke.
        return json.dumps({"error": f"bad arguments for {name}: {exc}"}), True
    except Exception as exc:  # last-resort guard; a crash here would kill the loop
        return json.dumps({"error": f"{name} failed: {type(exc).__name__}: {exc}"}), True

    return json.dumps(result, default=str), False
