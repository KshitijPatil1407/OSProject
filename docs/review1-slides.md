# Review 1 — Slide Outline

**Project:** Agentic Shell for Linux — Agent #7, Semantic File Search
**Team:** Satvik Sinha + partner
**Course:** Operating Systems

Build these in PowerPoint / Google Slides. Each section below is one slide:
the heading is the slide title, bullets are the slide body, and *Say:* is what
to cover verbally without putting on the slide.

---

## Slide 1 — Title

- Agentic Shell for Linux
- **Agent #7: Semantic File Search**
- Team members, registration numbers, course code, guide name
- Review 1

---

## Slide 2 — The problem

- A Linux shell is powerful but demands exact syntax from memory
- Finding one file means composing a pipeline:
  `find ~ -name "*.conf" -mtime -1 -type f -exec grep -l "password" {} \;`
- Four flags, two commands, one `-exec` idiom — any of which is easy to get wrong
- The user already knows *what* they want: "the config I edited yesterday with the DB password"
- **The gap is translation, not capability**

*Say:* the shell isn't missing features; the interface assumes you've memorised
them. That's the gap the project closes.

---

## Slide 3 — The system we're part of

- Class-wide project: a shell that accepts natural language
- A master loop reads user text, an LLM **routes intent** to one of 35 specialised agents
- Each agent owns one OS domain and exposes its capabilities as callable **tools**
- Diagram: user text → router → [Agent 1 … Agent 35] → **our agent highlighted**
- Ours: domain 7, filesystem search

*Say:* one sentence on how routing works, then move on — the review cares about
our agent, not the whole class's.

---

## Slide 4 — Our objective

- Replace hand-written `find` / `grep` pipelines with described intent
- Search on **metadata** (time, size, type, name) and **content** (regex) together
- Resolve vague human phrasing into precise filters:
  - "config" → `conf, cfg, ini, yaml, toml, env, json` + hidden dotfiles
  - "yesterday" → an mtime window, computed from the live clock
  - "the password" → `password|passwd|secret|token|DB_PASS`
- Interface the real OS through system calls, not a canned database

---

## Slide 5 — How function calling works

- The LLM does **not** run commands; it selects a tool and fills its arguments
- We publish each capability as a JSON schema: name, description, typed parameters
- Loop: send schemas → model returns a `tool_use` request → **our Python code executes it** → return the result → repeat until the model answers
- The model is the planner; our code is the only thing that touches the kernel

*Say:* this is the slide that shows you understand the architecture rather than
just calling an API. Emphasise the separation: the model never gets to run code.

---

## Slide 6 — Architecture

```
          user text
              │
    ┌─────────▼──────────────────────┐
    │ agent.py — tool-use loop       │
    │ system prompt + 6 tool schemas │
    └─────────┬──────────────────────┘
              │ tool calls
    ┌─────────▼──────────────────────┐
    │ registry.py — schemas/dispatch │
    ├────────────────────────────────┤
    │ safety.py    sandbox + limits  │
    │ metadata.py  scandir / stat    │
    │ content.py   ripgrep / mmap    │
    │ timespec.py  "yesterday"→epoch │
    └─────────┬──────────────────────┘
              │ system calls
        live filesystem (read-only)
```

- Redraw this cleanly in the slide tool — don't paste ASCII

---

## Slide 7 — The six tools

| Tool | System calls | Purpose |
|---|---|---|
| `search_by_metadata` | `getdents64`, `lstat` | Name, extension, mtime/atime, size, type, depth |
| `search_file_content` | `ripgrep(1)` / `read` | Regex over contents, returns matching lines |
| `stat_file` | `lstat`, `readlink` | Timestamps, mode, owner, inode, link count |
| `preview_file` | `open`, `read` | Bounded slice to confirm a hit |
| `list_directory` | `getdents64` | One level, to orient when input is vague |
| `identify_file` | `file(1)` | True type from magic bytes |

*Say:* `os.scandir` wraps `getdents64`; `os.stat` wraps `stat(2)`. Name the
syscall if asked — this is an OS course.

---

## Slide 8 — Key design decision: metadata before content

- Naive approach: grep all of `$HOME` for "password" → slow, hundreds of irrelevant lines
- Ours: filter on metadata first (`stat` only, very cheap), then grep **only the survivors**
- The system prompt instructs the model to chain the two tools in that order
- Measured on our test tree: **12 entries scanned → 2 candidates → 1 correct answer**

*Say:* this is the slide to spend time on. It's the actual engineering
contribution, not just "we called an API."

---

## Slide 9 — Why we wrote our own traversal

- We use `os.scandir` directly instead of shelling out to `find(1)`
- Owning the walk gives us:
  - **Pruning** — `/proc`, `/sys`, `/dev` block on kernel reads; `.git`, `node_modules`, `__pycache__` are noise
  - **Cycle detection** — track `(st_dev, st_ino)` so bind mounts and symlink loops can't trap us
  - **Bounded cost** — depth cap and a 25-second wall-clock timeout
  - **Fault tolerance** — an unreadable directory is skipped and counted, not fatal

*Say:* this is the OS-concepts slide. Device+inode identity, pseudo-filesystems,
permission handling.

---

## Slide 10 — Safety model

The LLM chooses every argument, so every argument is untrusted:

- **Sandbox** — paths resolved *before* the permission check, so a symlink can't escape; root defaults to `$HOME`
- **Hard caps** — 200 results, 100 content matches, 200 preview lines, 12 tool rounds
- **Honest truncation** — every response reports whether a cap was hit, so the agent says "partial" instead of implying it searched everything
- **Read-only** — no tool writes, moves, or deletes
- **Errors as data** — a bad regex returns `is_error: true` so the model self-corrects instead of crashing the turn

---

## Slide 11 — Current status

**Working and verified**
- All 6 tools implemented against live system calls
- 14/14 tool tests passing, no API key required
- Both content backends (ripgrep and pure-Python fallback) return identical results
- All tool schemas validated against their handler signatures
- End-to-end chain resolves the target query and rejects three deliberate decoys

**Pending**
- Live LLM loop verification (awaiting API key)
- Integration with the class base shell router

*Say:* state the pending items plainly. Examiners trust a status slide that
admits what isn't done.

---

## Slide 12 — Demo evidence

- Show the actual terminal output of `python demo.py --tools-only`
- Screenshot it; don't retype it
- Walk the three steps: metadata shortlist → content grep → preview confirmation
- Call out the decoys and why each was rejected:
  - `old.conf` — right contents, 400 days old → rejected by the time filter
  - `notes.md` — right date, no credential → rejected by the content filter
  - `node_modules/pkg.conf` — both right → never scanned, directory pruned

---

## Slide 13 — Tech stack

- **Python 3.11** — `os`, `stat`, `pwd`, `subprocess`, `re`, `mmap`
- **Claude Opus 5** via the Anthropic SDK, function calling with adaptive thinking
- **ripgrep** as the fast content backend, with a pure-Python fallback
- Prompt caching on the tool schemas to cut repeat-query cost
- ~900 lines across 6 modules plus tests

---

## Slide 14 — Plan for Reviews 2 and 3

**Review 2**
- Live LLM loop verified across a query suite
- Integrated with the base shell router
- Multi-turn follow-ups ("no, the other one", "show me more of that file")
- Measured latency and token cost per query

**Review 3**
- Ranking when several files match, instead of listing them flat
- `locate`/`mlocate` database as a fast path for pure name lookups
- Fuzzy name matching for misremembered filenames
- Final evaluation: accuracy on a labelled query set vs. equivalent `find` commands

---

## Slide 15 — Thank you / Questions

Have ready:

- **"Which syscall does `os.scandir` use?"** → `getdents64`; `os.stat` → `stat(2)`, `os.lstat` → `lstat(2)`
- **"What if the LLM asks for `/etc/shadow`?"** → outside the sandbox root, rejected before any syscall runs
- **"Why not just use `find`?"** → we need to prune pseudo-filesystems, detect cycles, bound by time, and survive permission errors mid-walk; `find` gives us none of that control
- **"Does the model run shell commands?"** → no, it selects a tool and arguments; our Python code is the only thing that executes
