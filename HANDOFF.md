# Handoff brief

Context for an assistant picking this project up cold. Read this before
changing anything.

## What this is

Agent #7 of a class project: an "Agentic Shell" for Linux. A master shell (built
by other teams) accepts natural language and routes intent to one of 35
domain-specific agents. This repo is **agent #7, Semantic File Search** — it
replaces hand-written `find` / `grep` pipelines with described intent:

> "Find the config file I edited yesterday that has the database password in it."

The LLM never executes commands. It selects a tool and fills its arguments; the
Python code in this repo is the only thing that issues system calls.

## Layout

```
semantic_search/
  agent.py      tool-use loop, system prompt, CLI + REPL
  config.py     .env loader (hand-rolled, no dependency)
  registry.py   TOOLS schemas + dispatch()
  metadata.py   search_by_metadata, stat_file, list_directory
  content.py    search_file_content, preview_file, identify_file
  safety.py     sandbox, prune lists, caps, regex/type helpers
  timespec.py   time + size expression parsing
tests/test_tools.py   14 tests, real syscalls, no API key needed
demo.py               scripted demo on a throwaway tree
docs/review1-slides.md   slide outline for the course review
```

## Current state

**Verified working:**
- All 6 tools implemented on live system calls (`os.scandir`, `os.lstat`, `ripgrep`)
- `python tests/test_tools.py` → 14/14 passing
- `python demo.py --tools-only` → full metadata→content→preview chain, no API needed
- Both content backends (ripgrep and pure-Python fallback) return identical results
- All tool schemas validated against their handler signatures

**Not yet verified:**
- **The live LLM loop in `agent.py` has never been run.** No API key was available
  in the environment where it was written. The loop logic is complete but untested
  end to end. Running `python demo.py` with `ANTHROPIC_API_KEY` set is the single
  highest-value thing anyone can do next.
- Integration with the base shell's router (the contract isn't defined yet)

## Deliberate decisions that look like bugs but aren't

Please don't "fix" these without asking:

1. **Metadata before content.** The system prompt in `agent.py` pushes the model
   to run `search_by_metadata` first and pass the resulting shortlist to
   `search_file_content` as `paths`. Grepping a whole home directory is slow and
   noisy. This chaining is the project's main engineering contribution — don't
   simplify it into a single search tool.

2. **Hand-written traversal instead of `find(1)`.** `metadata.py` walks with
   `os.scandir` on purpose, so it can prune pseudo-filesystems, detect directory
   cycles via `(st_dev, st_ino)`, bound the walk by depth and wall clock, and skip
   unreadable directories instead of aborting. Shelling out to `find` would lose
   all of that.

3. **`.gitignore` is deliberately not respected** in content search
   (`--no-ignore` is passed to ripgrep). An ignored file is still a file the user
   may be looking for.

4. **The caps in `safety.py` are load-bearing**, not arbitrary. Tool results are
   fed back into the model's context, so unbounded output is slow and expensive.
   Every response reports whether a cap was hit so the agent can tell the user its
   answer is partial rather than implying the search was exhaustive.

5. **Paths are resolved before the sandbox check**, not after. Reversing that order
   would let a symlink escape the allowed roots. See `resolve_path` in `safety.py`.

6. **Tool errors are returned as data** (`is_error: true`), never raised. A bad
   regex or missing path lets the model correct itself instead of killing the turn.
   See `dispatch` in `registry.py`.

## API constraints — these cause hard failures

The code targets `claude-opus-5` via the Anthropic Python SDK. On this model:

- `temperature`, `top_p`, `top_k` are **rejected with a 400** — do not add them
- `thinking: {"type": "enabled", "budget_tokens": N}` is **rejected with a 400** —
  the code correctly uses `{"type": "adaptive"}` instead
- Assistant-turn prefills are **rejected with a 400**
- `max_tokens` is 64000, which **requires streaming** (`client.messages.stream`),
  otherwise the request risks an HTTP timeout. The code already streams.
- Effort goes inside `output_config`, not top level: `output_config={"effort": ...}`

If you port this to a different model or provider, check that provider's
equivalents rather than assuming these shapes transfer.

## Useful next tasks, roughly in order of value

1. **Run the live loop.** `export ANTHROPIC_API_KEY=... && python demo.py`.
   Expect to find prompt-tuning issues — e.g. the model reaching for
   `search_file_content` with a broad `root` instead of chaining from metadata.
   Fix those in the system prompt in `agent.py`, not by changing the tools.
2. **Build the actual slide deck** from `docs/review1-slides.md` (PowerPoint or
   Google Slides). The outline has slide-by-slide content and speaker notes.
3. **Multi-turn follow-ups** — "no, the other one", "show me more of that file".
   `handle()` already accepts and mutates a `history` list; the REPL uses it.
4. **Ranking when several files match**, instead of returning a flat list.
5. **`locate`/`mlocate` fast path** for pure name lookups, falling back to the
   live walk when the database is stale.

## Setup notes

- **Use the venv.** `python3 -m venv .venv && .venv/bin/pip install -r
  requirements.txt`. This project was originally developed against Python 3.11; a
  distro upgrade to 3.14 emptied the system `site-packages` mid-session, so the
  venv is not optional hygiene here — it is the thing that keeps imports working.
- **The API key comes from `.env`** at the project root, loaded by
  `semantic_search/config.py`. An exported `ANTHROPIC_API_KEY` overrides the file.
  `.env` is gitignored; `.env.example` documents the format.

## Rules for changes

- **Run `.venv/bin/python tests/test_tools.py` before and after any edit.** It
  needs no API key and takes under a second. If a change breaks a test, the
  change is wrong until proven otherwise.
- **If you add or change a tool parameter, update both** the JSON schema in
  `registry.py` and the handler signature. They must match — a schema property
  with no corresponding keyword argument causes a `TypeError` at dispatch time.
- **Keep the system prompt and the tool descriptions in sync.** The descriptions
  in `registry.py` are what the model actually reads when deciding which tool to
  call; vague descriptions are the most common cause of wrong tool selection.
- This agent is **read-only by design.** No tool should write, move, or delete.
  If a feature seems to need that, it belongs in a different agent.
