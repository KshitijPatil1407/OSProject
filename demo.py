#!/usr/bin/env python3
"""
Demo harness for the Semantic File Search Agent.

Builds a throwaway directory tree that contains the file from the assignment's
example prompt — plus two decoys designed to be wrong in exactly one way — then
runs natural-language queries against it. The sandbox is confined to the temp
directory, so the demo never touches your real home.

    python demo.py                      # run the scripted prompts
    python demo.py "your own prompt"    # run one prompt of your choosing
    python demo.py --tools-only         # show raw tool output, no API calls

Needs ANTHROPIC_API_KEY unless --tools-only is passed.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tests.test_tools import build_tree  # noqa: E402

SCRIPTED_PROMPTS = [
    "Find the config file I edited yesterday that has the database password in it.",
    "What files did I touch in the last two days?",
    "Which file here is taking up the most space?",
    "Is there anything in here that's a symlink, and where does it point?",
]


def show_tree(root: Path) -> None:
    print(f"Sandbox: {root}\n")
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        depth = len(rel.parts) - 1
        marker = "/" if path.is_dir() else ""
        note = ""
        if path.is_file():
            size = path.stat().st_size
            note = f"  ({size:,} B)"
        print(f"  {'  ' * depth}{rel.name}{marker}{note}")
    print()


def tools_only(root: Path) -> None:
    """Run the metadata → content chain by hand, with no LLM in the loop."""
    from semantic_search.registry import dispatch

    print("Step 1 — search_by_metadata: config-ish files modified since yesterday\n")
    payload, err = dispatch("search_by_metadata", {
        "root": str(root),
        "extensions": ["conf", "cfg", "ini", "yaml", "yml", "toml", "env", "json", "md"],
        "modified_after": "2 days ago",
        "include_hidden": True,
    })
    shortlist = json.loads(payload)
    assert not err, shortlist
    for entry in shortlist["results"]:
        print(f"  {entry['modified']}  {entry['size']:>7}  {entry['path']}")
    print(f"\n  scanned {shortlist['entries_scanned']} entries, "
          f"{shortlist['matches_found']} matched\n")

    print("Step 2 — search_file_content: grep only those candidates for a credential\n")
    payload, err = dispatch("search_file_content", {
        "pattern": r"password|passwd|secret|token|DB_PASS",
        "paths": [e["path"] for e in shortlist["results"]],
    })
    hits = json.loads(payload)
    assert not err, hits
    for match in hits["matches"]:
        print(f"  {match['path']}:{match['line_number']}: {match['line']}")
    print(f"\n  backend={hits['backend']}, {hits['files_matched']} file(s) matched\n")

    print("Step 3 — preview_file: confirm it is really the file\n")
    if hits["matches"]:
        payload, err = dispatch("preview_file", {"path": hits["matches"][0]["path"]})
        preview = json.loads(payload)
        for line in preview["content"].splitlines():
            print(f"  | {line}")


def run_agent(root: Path, prompts: list[str]) -> None:
    from semantic_search.agent import handle

    for prompt in prompts:
        print("=" * 72)
        print(f"> {prompt}\n")
        print(handle(prompt, verbose=True))
        print()


def main() -> int:
    argv = sys.argv[1:]
    only_tools = "--tools-only" in argv
    if only_tools:
        argv.remove("--tools-only")

    with tempfile.TemporaryDirectory(prefix="sfs-demo-") as tmp:
        root = Path(tmp).resolve()
        build_tree(root)
        # Confine the agent to the demo tree for the duration of the run.
        os.environ["SFS_ALLOWED_ROOTS"] = str(root)
        os.chdir(root)

        show_tree(root)

        if only_tools:
            tools_only(root)
            return 0

        prompts = [" ".join(argv)] if argv else SCRIPTED_PROMPTS
        # The agent needs to know where to look; the sandbox root is not $HOME.
        prompts = [f"{p} (search under {root})" for p in prompts]
        run_agent(root, prompts)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
