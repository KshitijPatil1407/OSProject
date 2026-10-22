#!/usr/bin/env python3
"""
Render captured terminal output into a PNG for the report.

The text is real output captured from the commands that were actually run
(docs/evidence/*.txt); this script only typesets it, so the figures in the
report are legible at print size instead of being phone photographs of a
screen. It understands the small subset of ANSI colour that our tools emit.

    .venv/bin/python render_terminal.py INPUT.txt OUTPUT.png --title "..."
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# One Dark-ish palette: dark enough to read on paper, high contrast.
BG = (30, 33, 39)
TITLEBAR = (45, 49, 57)
FG = (205, 211, 222)
DIM = (120, 128, 142)
GREEN = (142, 192, 124)
YELLOW = (229, 192, 123)
BLUE = (97, 175, 239)
RED = (224, 108, 117)
MAGENTA = (198, 120, 221)
CYAN = (86, 182, 194)

DOTS = [(237, 106, 94), (244, 191, 79), (97, 190, 80)]

REGULAR = "/usr/share/fonts/noto/NotoSansMono-Regular.ttf"
BOLD = "/usr/share/fonts/TTF/DejaVuSansMono-Bold.ttf"

ANSI = re.compile(r"\x1b\[[0-9;]*m")

PAD = 22
LINE_SPACING = 6
TITLEBAR_H = 38


def colour_for(line: str) -> tuple[int, int, int]:
    """Cheap syntax highlighting so the figures read well in print."""
    s = line.strip()
    if not s:
        return FG
    if s.startswith("==="):
        return MAGENTA
    if s.startswith("$") or s.startswith("search>"):
        return GREEN
    if s.startswith("→") or s.startswith("->"):
        return BLUE
    if s.startswith("…") or s.startswith("..."):
        return DIM
    if re.match(r"^\s*(ok|PASS)\b", s) or "passed" in s:
        return GREEN
    if re.match(r"^\s*(FAIL|ERROR|error)\b", s) or "Error" in s:
        return RED
    if s.startswith("#") or s.startswith("--"):
        return DIM
    if re.match(r"^\s*(NOTICE|WARNING)", s):
        return YELLOW
    if re.match(r"^\s*(DROP|CREATE|INSERT|SELECT|UPDATE|DELETE)\b", s):
        return CYAN
    if s.startswith("|") or s.startswith("+--") or re.match(r"^\s*-+\+", s):
        return DIM
    return FG


def render(text: str, out_path: Path, title: str, font_size: int = 15,
           max_width: int = 150) -> Path:
    text = ANSI.sub("", text).replace("\t", "    ")

    lines: list[str] = []
    for raw in text.splitlines():
        if len(raw) <= max_width:
            lines.append(raw)
        else:  # soft-wrap, preserving indentation on continuations
            indent = len(raw) - len(raw.lstrip())
            prefix = " " * (indent + 2)
            lines.append(raw[:max_width])
            rest = raw[max_width:]
            while rest:
                lines.append(prefix + rest[: max_width - len(prefix)])
                rest = rest[max_width - len(prefix):]

    font = ImageFont.truetype(REGULAR, font_size)
    title_font = ImageFont.truetype(BOLD, font_size - 1)

    probe = Image.new("RGB", (10, 10))
    pd = ImageDraw.Draw(probe)
    char_w = pd.textlength("M", font=font)
    line_h = font_size + LINE_SPACING

    width = int(PAD * 2 + char_w * max(max_width, len(title) + 8))
    height = int(TITLEBAR_H + PAD * 2 + line_h * max(len(lines), 1))

    img = Image.new("RGB", (width, height), BG)
    d = ImageDraw.Draw(img)

    # Title bar with the usual three dots.
    d.rectangle([0, 0, width, TITLEBAR_H], fill=TITLEBAR)
    for i, colour in enumerate(DOTS):
        cx = 18 + i * 18
        d.ellipse([cx - 6, TITLEBAR_H // 2 - 6, cx + 6, TITLEBAR_H // 2 + 6], fill=colour)
    tw = d.textlength(title, font=title_font)
    d.text(((width - tw) / 2, TITLEBAR_H / 2 - font_size / 2), title,
           font=title_font, fill=DIM)

    y = TITLEBAR_H + PAD
    for line in lines:
        d.text((PAD, y), line, font=font, fill=colour_for(line))
        y += line_h

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG")
    return out_path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--title", default="satvik@arch: ~/Documents/Code2/OSProject")
    ap.add_argument("--font-size", type=int, default=15)
    ap.add_argument("--max-width", type=int, default=150)
    ap.add_argument("--head", type=int, help="only the first N lines")
    args = ap.parse_args()

    text = Path(args.input).read_text(encoding="utf-8", errors="replace")
    if args.head:
        text = "\n".join(text.splitlines()[: args.head])

    out = render(text, Path(args.output), args.title,
                 font_size=args.font_size, max_width=args.max_width)
    img = Image.open(out)
    print(f"{out}  ({img.width}x{img.height})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
