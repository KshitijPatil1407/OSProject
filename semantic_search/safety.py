"""
Guardrails shared by every tool in the Semantic File Search Agent.

The LLM picks the arguments, so every path, pattern and limit it hands us is
treated as untrusted input. This module is the single place where those values
are validated before they reach a system call.
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

# Pseudo-filesystems and caches. Walking /proc or /sys blocks on kernel reads
# and returns nothing a user would call "a file", so we never descend into them.
PRUNE_ABSOLUTE = {
    "/proc",
    "/sys",
    "/dev",
    "/run",
    "/snap",
    "/var/run",
    "/var/lib/docker",
    "/lost+found",
}

# Directory names pruned anywhere in the tree.
PRUNE_NAMES = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".cache",
    ".npm",
    ".cargo",
    ".rustup",
    ".gradle",
    ".m2",
    "site-packages",
    ".next",
    "target",
    "build",
    "dist",
}

# Hard ceilings. The agent loop feeds results back into the model's context, so
# unbounded output is both slow and expensive.
MAX_RESULTS = 200
MAX_CONTENT_MATCHES = 100
MAX_FILE_BYTES_FOR_CONTENT = 32 * 1024 * 1024  # skip anything larger when grepping
MAX_PREVIEW_BYTES = 64 * 1024
WALK_TIMEOUT_SECONDS = 25.0

# Roots the agent may search. Defaults to $HOME; override with SFS_ALLOWED_ROOTS
# (colon-separated) to widen or narrow the sandbox.
def allowed_roots() -> list[Path]:
    raw = os.environ.get("SFS_ALLOWED_ROOTS")
    if raw:
        roots = [Path(p).expanduser() for p in raw.split(":") if p.strip()]
    else:
        roots = [Path.home()]
    return [r.resolve() for r in roots if r.exists()]


class ToolError(Exception):
    """Raised for input the model got wrong; surfaced back to it as is_error."""


@dataclass(frozen=True)
class SafePath:
    path: Path          # resolved, symlinks collapsed
    original: str       # what the model actually asked for


def resolve_path(raw: str, *, must_exist: bool = True) -> SafePath:
    """Expand, resolve and sandbox-check a model-supplied path.

    Resolution happens before the sandbox check so that a symlink pointing out
    of the allowed roots cannot be used to escape them.
    """
    if not raw or not raw.strip():
        raise ToolError("path must be a non-empty string")

    candidate = Path(os.path.expandvars(raw)).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate

    try:
        resolved = candidate.resolve(strict=must_exist)
    except FileNotFoundError:
        raise ToolError(f"no such file or directory: {raw}") from None
    except OSError as exc:
        raise ToolError(f"cannot resolve {raw}: {exc.strerror}") from None

    roots = allowed_roots()
    if not any(resolved == r or r in resolved.parents for r in roots):
        pretty = ", ".join(str(r) for r in roots)
        raise ToolError(
            f"{resolved} is outside the searchable roots ({pretty}). "
            "Set SFS_ALLOWED_ROOTS to widen the sandbox."
        )

    for banned in PRUNE_ABSOLUTE:
        if str(resolved) == banned or str(resolved).startswith(banned + "/"):
            raise ToolError(f"{resolved} is a pseudo-filesystem and is not searchable")

    return SafePath(path=resolved, original=raw)


def should_prune(dir_path: str, dir_name: str, *, include_hidden: bool) -> bool:
    """Decide whether a directory encountered mid-walk should be skipped."""
    if dir_name in PRUNE_NAMES:
        return True
    if not include_hidden and dir_name.startswith(".") and dir_name not in (".", ".."):
        return True
    for banned in PRUNE_ABSOLUTE:
        if dir_path == banned or dir_path.startswith(banned + "/"):
            return True
    return False


def compile_regex(pattern: str, *, ignore_case: bool) -> re.Pattern[str]:
    flags = re.MULTILINE | (re.IGNORECASE if ignore_case else 0)
    try:
        return re.compile(pattern, flags)
    except re.error as exc:
        raise ToolError(f"invalid regular expression {pattern!r}: {exc}") from None


def kind_of(st: os.stat_result) -> str:
    """Human-readable file type from the st_mode bits."""
    mode = st.st_mode
    if stat.S_ISDIR(mode):
        return "dir"
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISREG(mode):
        return "file"
    if stat.S_ISFIFO(mode):
        return "fifo"
    if stat.S_ISSOCK(mode):
        return "socket"
    if stat.S_ISBLK(mode):
        return "block"
    if stat.S_ISCHR(mode):
        return "char"
    return "other"


def looks_binary(chunk: bytes) -> bool:
    """A NUL byte in the first block is the same heuristic grep(1) uses."""
    return b"\0" in chunk


def clamp(value: int | None, default: int, maximum: int) -> int:
    if value is None:
        return default
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise ToolError(f"expected an integer, got {value!r}") from None
    return max(1, min(value, maximum))
