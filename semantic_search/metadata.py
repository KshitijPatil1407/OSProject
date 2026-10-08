"""
search_by_metadata — the replacement for hand-written `find` pipelines.

Implemented directly on top of the getdents/stat system calls that os.scandir
and os.stat wrap, rather than by shelling out to find(1), so the agent owns the
traversal: it can prune pseudo-filesystems, bound the walk by time and depth,
and survive permission errors on individual entries.
"""

from __future__ import annotations

import fnmatch
import os
import pwd
import stat
import time

from .safety import (
    MAX_RESULTS,
    WALK_TIMEOUT_SECONDS,
    ToolError,
    clamp,
    compile_regex,
    kind_of,
    resolve_path,
    should_prune,
)
from .timespec import format_time, human_size, parse_size, parse_time

_SORT_KEYS = {
    "mtime": lambda e: -e["modified_epoch"],
    "atime": lambda e: -e["accessed_epoch"],
    "ctime": lambda e: -e["changed_epoch"],
    "size": lambda e: -e["size_bytes"],
    "name": lambda e: e["name"].lower(),
    "path": lambda e: e["path"],
}


def _owner(uid: int) -> str:
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


def _describe(path: str, st: os.stat_result) -> dict:
    return {
        "path": path,
        "name": os.path.basename(path),
        "kind": kind_of(st),
        "size_bytes": st.st_size,
        "size": human_size(st.st_size),
        "modified": format_time(st.st_mtime),
        "modified_epoch": st.st_mtime,
        "accessed": format_time(st.st_atime),
        "accessed_epoch": st.st_atime,
        "changed": format_time(st.st_ctime),
        "changed_epoch": st.st_ctime,
        "mode": stat.filemode(st.st_mode),
        "owner": _owner(st.st_uid),
    }


def search_by_metadata(
    root: str = "~",
    *,
    name_pattern: str | None = None,
    name_regex: str | None = None,
    extensions: list[str] | None = None,
    file_type: str = "file",
    modified_after: str | None = None,
    modified_before: str | None = None,
    accessed_after: str | None = None,
    min_size: str | None = None,
    max_size: str | None = None,
    max_depth: int | None = None,
    include_hidden: bool = False,
    sort_by: str = "mtime",
    limit: int | None = None,
) -> dict:
    """Walk `root` and return entries matching every filter that was supplied."""
    base = resolve_path(root).path
    if not base.is_dir():
        raise ToolError(f"{base} is not a directory; use stat_file for a single path")

    if file_type not in ("file", "dir", "symlink", "any"):
        raise ToolError("file_type must be one of: file, dir, symlink, any")
    if sort_by not in _SORT_KEYS:
        raise ToolError(f"sort_by must be one of: {', '.join(_SORT_KEYS)}")

    regex = compile_regex(name_regex, ignore_case=True) if name_regex else None
    ext_set = None
    if extensions:
        ext_set = {("." + e.lstrip(".")).lower() for e in extensions}

    mtime_after = parse_time(modified_after) if modified_after else None
    mtime_before = parse_time(modified_before) if modified_before else None
    atime_after = parse_time(accessed_after) if accessed_after else None
    size_min = parse_size(min_size) if min_size is not None else None
    size_max = parse_size(max_size) if max_size is not None else None

    depth_cap = clamp(max_depth, default=64, maximum=64) if max_depth else 64
    result_cap = clamp(limit, default=50, maximum=MAX_RESULTS)

    results: list[dict] = []
    skipped_permission = 0
    seen_dirs: set[tuple[int, int]] = set()
    deadline = time.monotonic() + WALK_TIMEOUT_SECONDS
    truncated_reason: str | None = None

    stack: list[tuple[str, int]] = [(str(base), 0)]
    scanned = 0

    while stack:
        if time.monotonic() > deadline:
            truncated_reason = f"walk exceeded {WALK_TIMEOUT_SECONDS:.0f}s"
            break

        current, depth = stack.pop()
        try:
            with os.scandir(current) as it:
                entries = list(it)
        except PermissionError:
            skipped_permission += 1
            continue
        except OSError:
            continue

        for entry in entries:
            scanned += 1
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                continue

            if is_dir and depth < depth_cap:
                if not should_prune(entry.path, entry.name, include_hidden=include_hidden):
                    try:
                        dst = entry.stat(follow_symlinks=False)
                        key = (dst.st_dev, dst.st_ino)
                    except OSError:
                        key = None
                    # Guard against hardlinked/bind-mounted directory cycles.
                    if key is None or key not in seen_dirs:
                        if key is not None:
                            seen_dirs.add(key)
                        stack.append((entry.path, depth + 1))

            if not include_hidden and entry.name.startswith("."):
                continue

            # --- name filters (cheap, applied before stat) ---
            if name_pattern and not fnmatch.fnmatch(entry.name, name_pattern):
                continue
            if regex and not regex.search(entry.name):
                continue
            if ext_set and os.path.splitext(entry.name)[1].lower() not in ext_set:
                continue

            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue

            kind = kind_of(st)
            if file_type == "file" and kind != "file":
                continue
            if file_type == "dir" and kind != "dir":
                continue
            if file_type == "symlink" and kind != "symlink":
                continue

            # --- metadata filters ---
            if mtime_after is not None and st.st_mtime < mtime_after:
                continue
            if mtime_before is not None and st.st_mtime > mtime_before:
                continue
            if atime_after is not None and st.st_atime < atime_after:
                continue
            if size_min is not None and st.st_size < size_min:
                continue
            if size_max is not None and st.st_size > size_max:
                continue

            results.append(_describe(entry.path, st))

    results.sort(key=_SORT_KEYS[sort_by])
    total = len(results)
    if total > result_cap:
        truncated_reason = truncated_reason or f"more than {result_cap} matches"
        results = results[:result_cap]

    return {
        "root": str(base),
        "entries_scanned": scanned,
        "matches_found": total,
        "matches_returned": len(results),
        "truncated": truncated_reason,
        "directories_unreadable": skipped_permission,
        "results": results,
    }


def stat_file(path: str) -> dict:
    """Full metadata for one path, following the symlink target if there is one."""
    safe = resolve_path(path)
    try:
        st = os.lstat(safe.path)
    except OSError as exc:
        raise ToolError(f"cannot stat {safe.path}: {exc.strerror}") from None

    info = _describe(str(safe.path), st)
    info["inode"] = st.st_ino
    info["hard_links"] = st.st_nlink
    info["device"] = st.st_dev

    if stat.S_ISLNK(st.st_mode):
        try:
            info["symlink_target"] = os.readlink(safe.path)
        except OSError:
            info["symlink_target"] = None

    return info


def list_directory(path: str = ".", *, include_hidden: bool = False,
                   limit: int | None = None) -> dict:
    """One non-recursive level, for orienting before a deeper search."""
    base = resolve_path(path).path
    if not base.is_dir():
        raise ToolError(f"{base} is not a directory")

    cap = clamp(limit, default=100, maximum=MAX_RESULTS)
    entries: list[dict] = []
    try:
        with os.scandir(base) as it:
            for entry in it:
                if not include_hidden and entry.name.startswith("."):
                    continue
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                entries.append(_describe(entry.path, st))
    except PermissionError:
        raise ToolError(f"permission denied reading {base}") from None

    entries.sort(key=lambda e: (e["kind"] != "dir", e["name"].lower()))
    return {
        "path": str(base),
        "total": len(entries),
        "entries": entries[:cap],
        "truncated": f"more than {cap} entries" if len(entries) > cap else None,
    }
