"""
The Semantic File Search Agent.

Takes a natural-language request ("find the config file I edited yesterday that
has the database password in it"), lets Claude decide which of this agent's
tools to run against the live filesystem, and returns a plain-language answer.

Run it standalone:
    python -m semantic_search "find large videos I haven't opened in a year"
    python -m semantic_search            # interactive

Or call it from the base shell:
    from semantic_search.agent import handle
    answer = handle(user_text)
"""

from __future__ import annotations

import os
import platform
import socket
import sys
from datetime import datetime
from typing import Any

import anthropic

from .config import PROJECT_ROOT, load_env
from .registry import TOOLS, dispatch
from .safety import MAX_RESULTS, allowed_roots

# Read .env at import time so SFS_EFFORT and SFS_MAX_ROUNDS below can come
# from the file too, not just the API key.
load_env()

MODEL = "claude-opus-5"
MAX_TOKENS = 64_000
EFFORT = os.environ.get("SFS_EFFORT", "medium")
MAX_TOOL_ROUNDS = int(os.environ.get("SFS_MAX_ROUNDS", "12"))

# Stable across every request, so it sits in the cached prefix.
SYSTEM_STABLE = f"""\
You are the Semantic File Search Agent inside an agentic Linux shell. The user \
describes a file in their own words; you locate it by calling the tools below \
and then answer in plain language.

How to search:
- Start with metadata, not content. search_by_metadata is cheap and narrows the \
candidate set fast. Full-text search over a home directory is slow and noisy, so \
reach for search_file_content with a `root` only when metadata alone cannot \
identify the file.
- The high-value pattern is to chain them: run search_by_metadata to get a \
shortlist, then pass that shortlist as `paths` to search_file_content. "A config \
file I edited yesterday containing the database password" is a metadata filter \
(config extensions, modified since yesterday, hidden included) followed by a \
content grep over the handful of files that survived.
- Translate vague wording into the right filters rather than guessing a single \
exact name. "Config" means extensions like conf, cfg, ini, yaml, yml, toml, env, \
json, properties, and hidden dotfiles. "Notes" means md, txt, org. "Recently" \
means a modified_after filter, not a sort alone. Set include_hidden to true \
whenever config or dotfiles could be involved.
- Searching for a credential means searching for the alternate spellings people \
actually use: password, passwd, pwd, secret, token, DB_PASS, DATABASE_URL. Use a \
regex alternation, not one literal word.
- If a search comes back empty, widen one dimension at a time and say what you \
changed. Dropping the time filter, broadening the extension list, or moving the \
root up a level are usually more productive than re-running the same query.
- Confirm before you conclude. When you believe you have the file, preview_file \
or stat_file it so your answer rests on what is actually there.

How to answer:
- Lead with the file. Give the absolute path, then the one or two facts that make \
it the right file — when it was modified, which line matched.
- Show the matching line when the user asked about content, but never print more \
of a credential than the user needs to recognise the file. Quoting the key name \
and a truncated value is enough.
- If several files fit, list the best few with the detail that distinguishes them \
and say which one you would bet on. Do not dump every result.
- If nothing fits, say so directly and state where you looked and what you \
filtered on, so the user can correct the assumption that was wrong.
- Report tool failures honestly. A permission-denied directory or a truncated \
walk means your answer is partial; say that rather than implying the search was \
exhaustive.

Limits you are operating under: results are capped at {MAX_RESULTS} entries per \
call, walks time out after about 25 seconds, and pseudo-filesystems (/proc, /sys, \
/dev) plus build caches (node_modules, __pycache__, .git) are never searched. \
These are guardrails, not failures — work within them and note when a cap was hit.

You read the filesystem; you never modify or delete anything. If the user asks \
for a change, locate the file and tell them what you found."""


def _system_volatile() -> str:
    """Per-request context. Deliberately placed after the cache breakpoint."""
    now = datetime.now()
    roots = ", ".join(str(r) for r in allowed_roots()) or "(none)"
    return (
        f"Current local time: {now.strftime('%A %Y-%m-%d %H:%M:%S %Z')} "
        f"(epoch {int(now.timestamp())}).\n"
        f"Home directory: {os.path.expanduser('~')}\n"
        f"Current working directory: {os.getcwd()}\n"
        f"Searchable roots: {roots}\n"
        f"Host: {socket.gethostname()} ({platform.system()} {platform.release()})"
    )


def _build_system() -> list[dict[str, Any]]:
    return [
        {
            "type": "text",
            "text": SYSTEM_STABLE,
            # Tools render before system, so this breakpoint caches both.
            "cache_control": {"type": "ephemeral"},
        },
        {"type": "text", "text": _system_volatile()},
    ]


def _client() -> anthropic.Anthropic:
    env_file = load_env()
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        where = f" (read {env_file}, but it has no ANTHROPIC_API_KEY)" if env_file else ""
        raise SystemExit(
            f"No Anthropic credentials found{where}.\n"
            f"Create {PROJECT_ROOT / '.env'} containing:\n"
            "    ANTHROPIC_API_KEY=sk-ant-...\n"
            "or export ANTHROPIC_API_KEY in your shell."
        )
    return anthropic.Anthropic()


def handle(
    user_text: str,
    *,
    client: anthropic.Anthropic | None = None,
    history: list[dict[str, Any]] | None = None,
    verbose: bool = False,
) -> str:
    """Run one natural-language request to completion and return the answer.

    `history` is mutated in place when supplied, so a caller can keep a
    conversation going across turns.
    """
    client = client or _client()
    messages: list[dict[str, Any]] = history if history is not None else []
    messages.append({"role": "user", "content": user_text})

    answer_parts: list[str] = []

    for _ in range(MAX_TOOL_ROUNDS):
        with client.messages.stream(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=_build_system(),
            tools=TOOLS,
            thinking={"type": "adaptive"},
            output_config={"effort": EFFORT},
            messages=messages,
        ) as stream:
            response = stream.get_final_message()

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "refusal":
            return "The request was declined by the model's safety system."

        tool_uses = [b for b in response.content if b.type == "tool_use"]
        text_now = "\n".join(b.text for b in response.content if b.type == "text").strip()

        if not tool_uses:
            if text_now:
                answer_parts.append(text_now)
            break

        if verbose and text_now:
            print(f"  … {text_now}", file=sys.stderr)

        tool_results = []
        for call in tool_uses:
            if verbose:
                print(f"  → {call.name}({_brief(call.input)})", file=sys.stderr)
            payload, is_error = dispatch(call.name, call.input)
            if verbose:
                flag = "error" if is_error else "ok"
                print(f"    {flag}, {len(payload)} bytes", file=sys.stderr)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": call.id,
                "content": payload,
                "is_error": is_error,
            })

        # All results for one assistant turn go back in a single user message.
        messages.append({"role": "user", "content": tool_results})
    else:
        answer_parts.append(
            f"(stopped after {MAX_TOOL_ROUNDS} tool rounds without reaching a "
            "conclusion — try narrowing the request)"
        )

    return "\n".join(answer_parts).strip() or "(no answer produced)"


def _brief(args: dict[str, Any], width: int = 90) -> str:
    parts = []
    for key, value in args.items():
        text = str(value)
        if len(text) > 30:
            text = text[:30] + "…"
        parts.append(f"{key}={text}")
    joined = ", ".join(parts)
    return joined if len(joined) <= width else joined[:width] + "…"


def repl(verbose: bool = False) -> None:
    client = _client()
    history: list[dict[str, Any]] = []
    print("Semantic File Search Agent. Describe the file you are looking for.")
    print("Ctrl-D or 'exit' to quit.\n")
    while True:
        try:
            line = input("search> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not line:
            continue
        if line in ("exit", "quit"):
            return
        try:
            print("\n" + handle(line, client=client, history=history, verbose=verbose) + "\n")
        except anthropic.APIError as exc:
            print(f"\nAPI error: {exc}\n", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    verbose = False
    for flag in ("-v", "--verbose"):
        if flag in argv:
            argv.remove(flag)
            verbose = True

    if not argv:
        repl(verbose=verbose)
        return 0

    try:
        print(handle(" ".join(argv), verbose=verbose))
    except anthropic.APIError as exc:
        print(f"API error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
