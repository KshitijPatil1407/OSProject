#!/usr/bin/env bash
#
# Capture a real terminal screenshot of a command actually running.
#
# Opens a Konsole window of a known size, runs the command inside it, waits for
# the command to finish, then screenshots that window with Spectacle. The
# figures in the report are therefore photographs of a real session rather than
# typeset recreations of one.
#
#   ./capture.sh OUT.png COLSxROWS "shell command" [FONT_PT]
#
# Konsole takes its grid from the window geometry, not from the profile's
# TerminalColumns/TerminalRows, and KWin ignores geometry requests on Wayland.
# So we run Konsole under XWayland (QT_QPA_PLATFORM=xcb), where
# -qwindowgeometry is honoured, and size the window in pixels to give exactly
# the character grid we asked for. That matters because the agent prints
# unwrapped paragraphs: the terminal's own width is what wraps them, so the
# window width is what controls how the figure reads.
#
# Requires a running KDE session: `spectacle -a` grabs the active window, which
# is the Konsole we just opened.
#
# Run this in the foreground. If it is backgrounded the new Konsole never takes
# focus, and `spectacle -a` will faithfully photograph whatever window does.

set -uo pipefail

OUT=${1:?usage: capture.sh OUT.png COLSxROWS "command" [font_pt]}
GEOM=${2:?missing COLSxROWS}
CMD=${3:?missing command}
FONT=${4:-}

COLS=${GEOM%x*}
ROWS=${GEOM#*x}

# Measured for Noto Sans Mono in Konsole on this display: a cell is about
# 0.826*pt wide and 1.86*pt tall.
CELL_W=0.826
CELL_H=1.86
SCREEN_W=1900
SCREEN_H=1020

# Largest font at which the requested grid still fits on screen — bigger font
# means more pixels per character, so a sharper figure.
if [ -z "$FONT" ]; then
    FONT=$(python3 -c "
cols, rows = $COLS, $ROWS
by_w = $SCREEN_W / (cols * $CELL_W)
by_h = $SCREEN_H / (rows * $CELL_H)
print(max(7, min(int(by_w), int(by_h))))
")
fi

read -r WIN_W WIN_H <<<"$(python3 -c "
print(int($COLS * $CELL_W * $FONT) + 8, int($ROWS * $CELL_H * $FONT) + 8)
")"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
FLAG="$WORK/done"
LOG="$WORK/output.txt"
PROJECT=$PWD

mkdir -p "$(dirname "$OUT")"

# The command travels via a file so it needs no second layer of shell quoting.
printf '%s\n' "$CMD" > "$WORK/cmd"

PROFILE_DIR="$HOME/.local/share/konsole"
mkdir -p "$PROFILE_DIR"
cat > "$PROFILE_DIR/SfsCapture.profile" <<PROFILE
[General]
Name=SfsCapture
Parent=FALLBACK/

[Appearance]
ColorScheme=Breeze
Font=Noto Sans Mono,$FONT,-1,5,50,0,0,0,0,0

[Scrolling]
ScrollBarPosition=2
PROFILE

# `script` gives the command a real pty, so tools that colourise only when
# attached to a terminal still do, and we keep a text copy of what was shown.
cat > "$WORK/run.sh" <<RUNNER
cd "$PROJECT"
sleep 1
printf '\033[1;32m\$\033[0m %s\n' "\$(cat "$WORK/cmd")"
script -qefc "\$(cat "$WORK/cmd")" "$LOG"
touch "$FLAG"
# Hold the window open and static while the screenshot is taken.
sleep 900
RUNNER

QT_QPA_PLATFORM=xcb konsole \
  --separate \
  --profile SfsCapture \
  --hide-menubar --hide-tabbar \
  -qwindowgeometry "${WIN_W}x${WIN_H}+30+30" \
  -e bash "$WORK/run.sh" &
KONSOLE_PID=$!

for _ in $(seq 1 2400); do
    [ -f "$FLAG" ] && break
    sleep 0.5
done

if [ ! -f "$FLAG" ]; then
    echo "capture.sh: command did not finish in time" >&2
    kill "$KONSOLE_PID" 2>/dev/null
    exit 1
fi

sleep 1.5
spectacle -a -b -n -o "$OUT" >/dev/null 2>&1
sleep 1

kill "$KONSOLE_PID" 2>/dev/null
wait "$KONSOLE_PID" 2>/dev/null

if [ ! -s "$OUT" ]; then
    echo "capture.sh: spectacle produced no image" >&2
    exit 1
fi

# Keep the text next to the image: it is greppable where a PNG is not.
cp "$LOG" "${OUT%.png}.txt" 2>/dev/null

# Trim the unused terminal below the last line of output.
python3 - "$OUT" <<'PY'
import sys
from PIL import Image

path = sys.argv[1]
im = Image.open(path).convert("RGB")
w, h = im.size
px = im.load()

FRAME = 16          # window border/shadow to leave untouched
PAD = 16            # breathing room kept under the last line of output

# Sample the terminal background well inside the window, bottom-right, where
# output never reaches.
bg = px[w - FRAME - 10, h - FRAME - 10]

def row_has_content(y, tol=20):
    for x in range(FRAME, w - FRAME, 3):
        r, g, b = px[x, y]
        if abs(r - bg[0]) + abs(g - bg[1]) + abs(b - bg[2]) > tol:
            return True
    return False

last = h - FRAME - 1
while last > 60 and not row_has_content(last):
    last -= 1

cut = min(h - FRAME, last + PAD)

# Splice out the empty middle but keep the window's own bottom border, so the
# figure still looks like a complete window.
if cut < h - FRAME - 8:
    top = im.crop((0, 0, w, cut))
    tail = im.crop((0, h - FRAME, w, h))
    out = Image.new("RGB", (w, cut + FRAME))
    out.paste(top, (0, 0))
    out.paste(tail, (0, cut))
    out.save(path)
    im = out

print(f"{path}  {im.width}x{im.height}")
PY

# Warn if the output was taller than the window: the screenshot would then be
# missing the beginning of the run, which no amount of cropping can recover.
python3 - "${OUT%.png}.txt" "$COLS" "$ROWS" <<'PY'
import math, re, sys

path, cols, rows = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
ansi = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
try:
    text = open(path, encoding="utf-8", errors="replace").read()
except OSError:
    sys.exit(0)

# `script` brackets the log with its own two banner lines; they are not shown.
lines = [l for l in text.splitlines()
         if not l.startswith(("Script started on", "Script done on"))]
shown = sum(max(1, math.ceil(len(ansi.sub("", l)) / cols)) for l in lines) + 1
if shown > rows:
    print(f"  WARNING: output needs ~{shown} rows but window has {rows}; "
          f"the top has scrolled off", file=sys.stderr)
PY
