"""
Tool-level tests. These exercise the real system calls against a throwaway
directory tree and need no API key, so they are the fast way to verify the
agent's filesystem layer before spending tokens on the LLM loop.

    python tests/test_tools.py        # standalone
    pytest tests/                     # or under pytest
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from semantic_search.registry import dispatch  # noqa: E402
from semantic_search.safety import ToolError, resolve_path  # noqa: E402
from semantic_search.timespec import parse_size, parse_time  # noqa: E402

DAY = 86_400


def build_tree(root: Path) -> None:
    """A miniature home directory covering every filter the agent exposes."""
    (root / "projects" / "webapp").mkdir(parents=True)
    (root / "projects" / "webapp" / "node_modules" / "junk").mkdir(parents=True)
    (root / "Documents").mkdir()
    (root / ".config" / "myapp").mkdir(parents=True)

    # The target of the headline example: a hidden config, edited "yesterday",
    # containing a database password.
    target = root / ".config" / "myapp" / "settings.conf"
    target.write_text(
        "[database]\n"
        "host = db.internal\n"
        "port = 5432\n"
        "DB_PASSWORD = hunter2-not-real\n"
    )
    yesterday = time.time() - DAY
    os.utime(target, (yesterday, yesterday))

    # A decoy: same kind of file, right contents, but edited long ago.
    old = root / "projects" / "webapp" / "old.conf"
    old.write_text("DB_PASSWORD = ancient\n")
    long_ago = time.time() - 400 * DAY
    os.utime(old, (long_ago, long_ago))

    # A decoy: edited yesterday, but no credential in it.
    notes = root / "Documents" / "notes.md"
    notes.write_text("# Meeting notes\nNothing sensitive here.\n")
    os.utime(notes, (yesterday, yesterday))

    # Size filtering.
    (root / "Documents" / "big.log").write_bytes(b"x" * 3_000_000)

    # Binary content must be skipped by the content search.
    (root / "Documents" / "blob.bin").write_bytes(b"\x00\x01DB_PASSWORD\x00")

    # Pruned directory: a match in here must never be reported.
    (root / "projects" / "webapp" / "node_modules" / "junk" / "pkg.conf").write_text(
        "DB_PASSWORD = should-never-surface\n"
    )

    # Symlink handling.
    (root / "link-to-config").symlink_to(root / ".config" / "myapp" / "settings.conf")


def call(name: str, **kwargs):
    payload, is_error = dispatch(name, kwargs)
    data = json.loads(payload)
    assert not is_error, f"{name} returned an error: {data}"
    return data


def call_expect_error(name: str, **kwargs) -> str:
    payload, is_error = dispatch(name, kwargs)
    assert is_error, f"{name} unexpectedly succeeded: {payload}"
    return json.loads(payload)["error"]


# --------------------------------------------------------------------------- #

def test_timespec() -> None:
    now = time.time()
    assert abs(parse_time("3 days ago") - (now - 3 * DAY)) < 5
    assert abs(parse_time("2h") - (now - 7200)) < 5
    assert parse_time("2026-01-15") < now
    assert parse_size("10M") == 10 * 1024**2
    assert parse_size("512k") == 512 * 1024
    assert parse_size(4096) == 4096
    try:
        parse_time("sometime last Michaelmas")
    except ToolError:
        pass
    else:
        raise AssertionError("expected ToolError for an unparseable time")


def test_sandbox(root: Path) -> None:
    assert resolve_path(str(root / "Documents")).path == (root / "Documents").resolve()
    err = call_expect_error("list_directory", path="/etc")
    assert "outside the searchable roots" in err
    err = call_expect_error("search_by_metadata", root="/proc")
    assert "outside the searchable roots" in err or "pseudo-filesystem" in err


def test_metadata_by_name(root: Path) -> None:
    out = call("search_by_metadata", root=str(root), name_pattern="*.conf",
               include_hidden=True)
    names = {Path(e["path"]).name for e in out["results"]}
    assert "settings.conf" in names, names
    assert "old.conf" in names, names
    assert "pkg.conf" not in names, "node_modules should have been pruned"


def test_metadata_hidden_is_opt_in(root: Path) -> None:
    visible = call("search_by_metadata", root=str(root), name_pattern="*.conf",
                   include_hidden=False)
    assert all(".config" not in e["path"] for e in visible["results"])


def test_metadata_time_filter(root: Path) -> None:
    recent = call("search_by_metadata", root=str(root), extensions=["conf", "md"],
                  modified_after="2 days ago", include_hidden=True)
    paths = {e["path"] for e in recent["results"]}
    assert str(root / ".config" / "myapp" / "settings.conf") in paths
    assert str(root / "projects" / "webapp" / "old.conf") not in paths, \
        "the 400-day-old decoy should be filtered out"


def test_metadata_size_filter(root: Path) -> None:
    big = call("search_by_metadata", root=str(root), min_size="1M")
    assert [Path(e["path"]).name for e in big["results"]] == ["big.log"]
    assert big["results"][0]["size_bytes"] == 3_000_000


def test_metadata_type_and_depth(root: Path) -> None:
    dirs = call("search_by_metadata", root=str(root), file_type="dir", max_depth=1)
    assert {Path(e["path"]).name for e in dirs["results"]} >= {"projects", "Documents"}

    links = call("search_by_metadata", root=str(root), file_type="symlink")
    assert [Path(e["path"]).name for e in links["results"]] == ["link-to-config"]


def test_content_search_over_shortlist(root: Path) -> None:
    shortlist = call("search_by_metadata", root=str(root), extensions=["conf", "md"],
                     modified_after="2 days ago", include_hidden=True)
    hits = call("search_file_content",
                pattern=r"password|passwd|secret|DB_PASS",
                paths=[e["path"] for e in shortlist["results"]])
    assert hits["files_matched"] == 1, hits
    match = hits["matches"][0]
    assert match["path"].endswith("settings.conf")
    assert "DB_PASSWORD" in match["line"]
    assert match["line_number"] == 4


def test_content_search_skips_binaries_and_caches(root: Path) -> None:
    hits = call("search_file_content", pattern="DB_PASSWORD", root=str(root))
    paths = {h["path"] for h in hits["matches"]}
    assert not any("node_modules" in p for p in paths), "pruned dir leaked into results"
    assert not any(p.endswith(".bin") for p in paths), "binary file was not skipped"
    assert any(p.endswith("settings.conf") for p in paths)


def test_content_search_rejects_bad_regex(root: Path) -> None:
    err = call_expect_error("search_file_content", pattern="unclosed(", root=str(root))
    assert "invalid regular expression" in err


def test_preview_and_stat(root: Path) -> None:
    target = root / ".config" / "myapp" / "settings.conf"
    preview = call("preview_file", path=str(target), max_lines=2)
    assert preview["binary"] is False
    assert preview["lines_returned"] == 2
    assert preview["content"].startswith("[database]")

    blob = call("preview_file", path=str(root / "Documents" / "blob.bin"))
    assert blob["binary"] is True
    assert "content" not in blob

    meta = call("stat_file", path=str(target))
    assert meta["kind"] == "file"
    assert meta["mode"].startswith("-rw")
    assert meta["inode"] > 0


def test_list_directory(root: Path) -> None:
    out = call("list_directory", path=str(root), include_hidden=True)
    names = [e["name"] for e in out["entries"]]
    # Directories sort before files.
    assert names.index("Documents") < names.index("link-to-config")
    assert ".config" in names


def test_unknown_tool() -> None:
    err = call_expect_error("delete_everything", path="/")
    assert "unknown tool" in err


def test_bad_arguments(root: Path) -> None:
    err = call_expect_error("search_by_metadata", root=str(root), nonsense=True)
    assert "bad arguments" in err


# --------------------------------------------------------------------------- #

def main() -> int:
    with tempfile.TemporaryDirectory(prefix="sfs-test-") as tmp:
        root = Path(tmp).resolve()
        build_tree(root)
        os.environ["SFS_ALLOWED_ROOTS"] = str(root)

        tests = [
            (test_timespec, ()),
            (test_sandbox, (root,)),
            (test_metadata_by_name, (root,)),
            (test_metadata_hidden_is_opt_in, (root,)),
            (test_metadata_time_filter, (root,)),
            (test_metadata_size_filter, (root,)),
            (test_metadata_type_and_depth, (root,)),
            (test_content_search_over_shortlist, (root,)),
            (test_content_search_skips_binaries_and_caches, (root,)),
            (test_content_search_rejects_bad_regex, (root,)),
            (test_preview_and_stat, (root,)),
            (test_list_directory, (root,)),
            (test_unknown_tool, ()),
            (test_bad_arguments, (root,)),
        ]

        failures = 0
        for fn, args in tests:
            try:
                fn(*args)
            except Exception as exc:
                failures += 1
                print(f"FAIL  {fn.__name__}: {type(exc).__name__}: {exc}")
            else:
                print(f"ok    {fn.__name__}")

        print(f"\n{len(tests) - failures}/{len(tests)} passed")
        return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
