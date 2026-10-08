"""
Time parsing for metadata filters.

The model is told the current local time in the system prompt, so it can send an
absolute ISO timestamp. It will also sometimes send what the user said verbatim
("yesterday", "2 days ago"), so both forms are accepted.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from .safety import ToolError

_RELATIVE = re.compile(
    r"^(?:(?P<count>\d+)\s*)?"
    # Longest alternatives first so "months" is not matched as "month" + "s",
    # and single-letter forms ("2h", "30d") come last.
    r"(?P<unit>seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?|months?|years?"
    r"|s|m|h|d|w|y)"
    r"(?:\s+ago)?$",
    re.IGNORECASE,
)

# Keys are the normalised stem; lookup strips a trailing "s".
_UNIT_SECONDS = {
    "second": 1, "sec": 1, "s": 1,
    "minute": 60, "min": 60, "m": 60,
    "hour": 3600, "hr": 3600, "h": 3600,
    "day": 86_400, "d": 86_400,
    "week": 604_800, "w": 604_800,
    "month": 2_592_000,   # 30 days
    "year": 31_536_000,   # 365 days
    "y": 31_536_000,
}


def parse_time(spec: str, *, now: datetime | None = None) -> float:
    """Return a POSIX timestamp for an absolute or relative time expression.

    Accepts ISO 8601 ("2026-10-07", "2026-10-07T14:30"), bare epoch seconds,
    "today"/"yesterday", and relative forms like "3 days ago" or "2h".
    """
    if spec is None:
        raise ToolError("time expression must not be null")

    text = str(spec).strip()
    if not text:
        raise ToolError("time expression must not be empty")

    now = now or datetime.now()
    low = text.lower()

    if low == "now":
        return now.timestamp()
    if low == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    if low == "yesterday":
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return (midnight - timedelta(days=1)).timestamp()

    # Bare epoch seconds.
    if re.fullmatch(r"\d{9,11}", text):
        return float(text)

    # ISO 8601, with or without a time component and trailing Z.
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        pass

    match = _RELATIVE.match(low)
    if match:
        count = int(match.group("count") or 1)
        unit = match.group("unit").lower()
        if unit not in _UNIT_SECONDS and unit.endswith("s"):
            unit = unit[:-1]
        seconds = _UNIT_SECONDS[unit] * count
        return (now - timedelta(seconds=seconds)).timestamp()

    raise ToolError(
        f"could not understand the time {spec!r}. Use an ISO timestamp "
        "(2026-10-07T14:30), 'yesterday', or a relative form like '3 days ago'."
    )


def format_time(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def parse_size(spec: str | int) -> int:
    """Parse '10M', '512k', '2GB' or a plain byte count into bytes."""
    if isinstance(spec, (int, float)):
        return int(spec)

    text = str(spec).strip().lower().replace(" ", "")
    if not text:
        raise ToolError("size must not be empty")

    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([kmgt]?)(?:i?b)?", text)
    if not match:
        raise ToolError(f"could not understand the size {spec!r}; try '10M' or '512k'")

    value = float(match.group(1))
    scale = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4}[match.group(2)]
    return int(value * scale)


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "K", "M", "G", "T"):
        if size < 1024 or unit == "T":
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}T"
