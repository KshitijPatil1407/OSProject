"""
Minimal .env loader.

Deliberately dependency-free: the project already asks a teammate to install
one package, and a ~40-line parser is cheaper than a second install step.

Lookup order for the file, first hit wins:
  1. $SFS_ENV_FILE, if set
  2. <project root>/.env   (the directory containing this package)
  3. ./.env                (current working directory)

Variables already present in the real environment are never overwritten, so an
exported ANTHROPIC_API_KEY still beats whatever is in the file.
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_loaded = False


def _candidates() -> list[Path]:
    paths: list[Path] = []
    override = os.environ.get("SFS_ENV_FILE")
    if override:
        paths.append(Path(override).expanduser())
    paths.append(PROJECT_ROOT / ".env")
    paths.append(Path.cwd() / ".env")
    return paths


def _parse_line(line: str) -> tuple[str, str] | None:
    line = line.strip()
    if not line or line.startswith("#"):
        return None

    # Tolerate "export KEY=value", which people paste in from their shell.
    if line.startswith("export "):
        line = line[len("export "):].lstrip()

    key, sep, value = line.partition("=")
    if not sep:
        return None

    key = key.strip()
    if not key:
        return None

    value = value.strip()

    # A quoted value is taken literally; an unquoted one has trailing comments
    # stripped, since "KEY=abc  # note" is a common mistake.
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    else:
        hash_at = value.find(" #")
        if hash_at != -1:
            value = value[:hash_at].rstrip()

    return key, value


def load_env(*, force: bool = False) -> Path | None:
    """Populate os.environ from the first .env file found. Returns its path."""
    global _loaded
    if _loaded and not force:
        return None

    for path in _candidates():
        try:
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue

        for raw in text.splitlines():
            parsed = _parse_line(raw)
            if parsed is None:
                continue
            key, value = parsed
            # Real environment variables win over the file.
            if key not in os.environ:
                os.environ[key] = value

        _loaded = True
        return path

    _loaded = True
    return None
