"""
search_file_content — the replacement for hand-written `grep -r` pipelines.

Uses ripgrep when it is installed (it is dramatically faster than anything we
can write in Python, because it memory-maps and parallelises), and falls back to
a pure-Python scanner using mmap(2) so the agent still works on a bare system.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time

from .safety import (
    MAX_CONTENT_MATCHES,
    MAX_FILE_BYTES_FOR_CONTENT,
    MAX_PREVIEW_BYTES,
    PRUNE_NAMES,
    WALK_TIMEOUT_SECONDS,
    ToolError,
    clamp,
    compile_regex,
    looks_binary,
    resolve_path,
    should_prune,
)
from .timespec import human_size

MAX_LINE_CHARS = 300
RIPGREP = shutil.which("rg")


def _trim(line: str) -> str:
    line = line.rstrip("\n\r")
    if len(line) > MAX_LINE_CHARS:
        return line[:MAX_LINE_CHARS] + f" …(+{len(line) - MAX_LINE_CHARS} chars)"
    return line


def search_file_content(
    pattern: str,
    *,
    root: str = "~",
    paths: list[str] | None = None,
    file_glob: str | None = None,
    extensions: list[str] | None = None,
    ignore_case: bool = True,
    include_hidden: bool = True,
    max_matches: int | None = None,
    max_matches_per_file: int = 3,
    context_lines: int = 0,
) -> dict:
    """Search file *contents* for a regular expression.

    Pass `paths` to search a specific shortlist (the normal flow: run
    search_by_metadata first, then grep only the candidates it returned), or
    `root` to search a whole subtree.
    """
    if not pattern or not pattern.strip():
        raise ToolError("pattern must be a non-empty regular expression")

    # Validate the regex up front so a bad pattern is reported as such rather
    # than as a ripgrep exit code.
    compile_regex(pattern, ignore_case=ignore_case)

    cap = clamp(max_matches, default=40, maximum=MAX_CONTENT_MATCHES)
    per_file = clamp(max_matches_per_file, default=3, maximum=20)
    context = clamp(context_lines, default=1, maximum=10) if context_lines else 0

    targets: list[str] = []
    if paths:
        for raw in paths:
            safe = resolve_path(raw)
            targets.append(str(safe.path))
    else:
        targets.append(str(resolve_path(root).path))

    if RIPGREP:
        return _search_with_ripgrep(
            pattern, targets,
            file_glob=file_glob, extensions=extensions,
            ignore_case=ignore_case, include_hidden=include_hidden,
            cap=cap, per_file=per_file, context=context,
        )
    return _search_in_python(
        pattern, targets,
        file_glob=file_glob, extensions=extensions,
        ignore_case=ignore_case, include_hidden=include_hidden,
        cap=cap, per_file=per_file,
    )


# --------------------------------------------------------------------------- #
# ripgrep backend
# --------------------------------------------------------------------------- #

def _search_with_ripgrep(
    pattern: str,
    targets: list[str],
    *,
    file_glob: str | None,
    extensions: list[str] | None,
    ignore_case: bool,
    include_hidden: bool,
    cap: int,
    per_file: int,
    context: int,
) -> dict:
    cmd = [
        RIPGREP,
        "--json",
        "--no-messages",       # permission errors go to /dev/null, not stderr
        "--no-ignore",         # a .gitignore must not hide the file we want
        "--max-filesize", str(MAX_FILE_BYTES_FOR_CONTENT),
        "--max-count", str(per_file),
    ]
    if ignore_case:
        cmd.append("--ignore-case")
    if include_hidden:
        cmd.append("--hidden")
    if context:
        cmd += ["--context", str(context)]
    if file_glob:
        cmd += ["--glob", file_glob]
    for ext in extensions or []:
        cmd += ["--glob", f"*.{ext.lstrip('.')}"]
    for pruned in PRUNE_NAMES:
        cmd += ["--glob", f"!**/{pruned}/**"]

    cmd += ["--regexp", pattern, "--"]
    cmd += targets

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=WALK_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "backend": "ripgrep",
            "pattern": pattern,
            "files_matched": 0,
            "matches": [],
            "truncated": f"search exceeded {WALK_TIMEOUT_SECONDS:.0f}s",
        }

    # rg exits 1 for "no matches" and 2 for real errors.
    if proc.returncode >= 2 and not proc.stdout:
        detail = (proc.stderr or "").strip().splitlines()
        raise ToolError(f"ripgrep failed: {detail[0] if detail else 'unknown error'}")

    matches: list[dict] = []
    files: set[str] = set()
    truncated: str | None = None

    for raw in proc.stdout.splitlines():
        if not raw.strip():
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "match":
            continue

        data = event["data"]
        path = data["path"].get("text")
        if path is None:  # non-UTF-8 filename; rg reports it as bytes
            continue

        files.add(path)
        matches.append({
            "path": path,
            "line_number": data["line_number"],
            "line": _trim(data["lines"].get("text", "")),
        })

        if len(matches) >= cap:
            truncated = f"stopped at {cap} matches"
            break

    return {
        "backend": "ripgrep",
        "pattern": pattern,
        "files_matched": len(files),
        "match_count": len(matches),
        "matches": matches,
        "truncated": truncated,
    }


# --------------------------------------------------------------------------- #
# pure-Python fallback
# --------------------------------------------------------------------------- #

def _candidate_files(targets: list[str], *, include_hidden: bool,
                     file_glob: str | None, extensions: list[str] | None):
    import fnmatch

    ext_set = {("." + e.lstrip(".")).lower() for e in extensions} if extensions else None
    deadline = time.monotonic() + WALK_TIMEOUT_SECONDS

    for target in targets:
        if os.path.isfile(target):
            yield target
            continue

        stack = [target]
        while stack:
            if time.monotonic() > deadline:
                return
            current = stack.pop()
            try:
                with os.scandir(current) as it:
                    entries = list(it)
            except OSError:
                continue

            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if not should_prune(entry.path, entry.name,
                                            include_hidden=include_hidden):
                            stack.append(entry.path)
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                except OSError:
                    continue

                if not include_hidden and entry.name.startswith("."):
                    continue
                if file_glob and not fnmatch.fnmatch(entry.name, file_glob):
                    continue
                if ext_set and os.path.splitext(entry.name)[1].lower() not in ext_set:
                    continue
                yield entry.path


def _search_in_python(
    pattern: str,
    targets: list[str],
    *,
    file_glob: str | None,
    extensions: list[str] | None,
    ignore_case: bool,
    include_hidden: bool,
    cap: int,
    per_file: int,
) -> dict:
    flags = re.IGNORECASE if ignore_case else 0
    regex = re.compile(pattern.encode("utf-8", "surrogateescape"), flags)

    matches: list[dict] = []
    files: set[str] = set()
    truncated: str | None = None
    deadline = time.monotonic() + WALK_TIMEOUT_SECONDS

    for path in _candidate_files(targets, include_hidden=include_hidden,
                                 file_glob=file_glob, extensions=extensions):
        if time.monotonic() > deadline:
            truncated = f"search exceeded {WALK_TIMEOUT_SECONDS:.0f}s"
            break
        if len(matches) >= cap:
            truncated = f"stopped at {cap} matches"
            break

        try:
            size = os.path.getsize(path)
            if size == 0 or size > MAX_FILE_BYTES_FOR_CONTENT:
                continue
            with open(path, "rb") as handle:
                head = handle.read(8192)
                if looks_binary(head):
                    continue
                handle.seek(0)
                found_here = 0
                for lineno, raw_line in enumerate(handle, start=1):
                    if regex.search(raw_line):
                        files.add(path)
                        matches.append({
                            "path": path,
                            "line_number": lineno,
                            "line": _trim(raw_line.decode("utf-8", "replace")),
                        })
                        found_here += 1
                        if found_here >= per_file or len(matches) >= cap:
                            break
        except OSError:
            continue

    return {
        "backend": "python",
        "pattern": pattern,
        "files_matched": len(files),
        "match_count": len(matches),
        "matches": matches,
        "truncated": truncated,
    }


# --------------------------------------------------------------------------- #

def preview_file(path: str, *, max_lines: int = 40,
                 start_line: int = 1) -> dict:
    """Read a bounded slice of a text file so the model can confirm a hit."""
    safe = resolve_path(path)
    target = safe.path

    if not target.is_file():
        raise ToolError(f"{target} is not a regular file")

    lines_cap = clamp(max_lines, default=40, maximum=200)
    start = max(1, int(start_line))

    try:
        size = target.stat().st_size
        with open(target, "rb") as handle:
            head = handle.read(8192)
            if looks_binary(head):
                return {
                    "path": str(target),
                    "size": human_size(size),
                    "binary": True,
                    "note": "binary file; contents not shown",
                }
            handle.seek(0)
            collected: list[str] = []
            consumed = 0
            for lineno, raw in enumerate(handle, start=1):
                if lineno < start:
                    continue
                consumed += len(raw)
                collected.append(_trim(raw.decode("utf-8", "replace")))
                if len(collected) >= lines_cap or consumed > MAX_PREVIEW_BYTES:
                    break
    except OSError as exc:
        raise ToolError(f"cannot read {target}: {exc.strerror}") from None

    return {
        "path": str(target),
        "size": human_size(size),
        "binary": False,
        "start_line": start,
        "lines_returned": len(collected),
        "content": "\n".join(collected),
    }


def identify_file(path: str) -> dict:
    """Ask file(1) what a path actually is — useful when the extension lies."""
    safe = resolve_path(path)
    binary = shutil.which("file")
    if not binary:
        return {"path": str(safe.path), "description": None,
                "note": "file(1) is not installed"}
    try:
        proc = subprocess.run(
            [binary, "--brief", "--", str(safe.path)],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except subprocess.TimeoutExpired:
        raise ToolError("file(1) timed out") from None
    return {"path": str(safe.path), "description": proc.stdout.strip() or None}
