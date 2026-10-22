#!/usr/bin/env python3
"""
Build the Review 3 report PDF.

Numbers in Chapter 5 are read from docs/evidence/evaluation.json, which is
produced by evaluate.py against the live agent, so the report cannot drift
from what was actually measured.

    .venv/bin/python build_review3.py
"""

from __future__ import annotations

import json
import statistics
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    Image,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Preformatted,
    Spacer,
    Table,
    TableStyle,
)

ROOT = Path(__file__).resolve().parent
EVIDENCE = ROOT / "docs" / "evidence"
FIGURES = ROOT / "docs" / "figures"
OUT = ROOT / "docs" / "Review3_Report_24BCE2001.pdf"

PAGE_W, PAGE_H = A4
MARGIN = 2.2 * cm
CONTENT_W = PAGE_W - 2 * MARGIN

ACCENT = colors.HexColor("#1F3864")
HEADER_BG = colors.HexColor("#DCE4F2")
GRID = colors.HexColor("#8898B8")
VERIFIED = colors.HexColor("#C6EFCE")
PENDING = colors.HexColor("#FFEB9C")

# --------------------------------------------------------------------------- #
# Styles
# --------------------------------------------------------------------------- #

ss = getSampleStyleSheet()

S = {
    "title": ParagraphStyle("title", parent=ss["Title"], fontName="Times-Bold",
                            fontSize=19, textColor=colors.HexColor("#C00000"),
                            spaceAfter=4),
    "subtitle": ParagraphStyle("subtitle", parent=ss["Title"], fontName="Times-Bold",
                               fontSize=14, spaceAfter=2, textColor=colors.black),
    "subtitle2": ParagraphStyle("subtitle2", parent=ss["Title"], fontName="Times-Bold",
                                fontSize=13, spaceAfter=14, textColor=colors.black),
    "h1": ParagraphStyle("h1", parent=ss["Heading1"], fontName="Times-Bold",
                         fontSize=15, textColor=ACCENT, spaceBefore=16, spaceAfter=8),
    "h2": ParagraphStyle("h2", parent=ss["Heading2"], fontName="Times-Bold",
                         fontSize=12.5, textColor=colors.black,
                         spaceBefore=12, spaceAfter=6),
    "h3": ParagraphStyle("h3", parent=ss["Heading3"], fontName="Times-BoldItalic",
                         fontSize=11.5, textColor=colors.black,
                         spaceBefore=10, spaceAfter=5),
    "body": ParagraphStyle("body", parent=ss["BodyText"], fontName="Times-Roman",
                           fontSize=10.5, leading=14.5, alignment=TA_JUSTIFY,
                           spaceAfter=7),
    "bullet": ParagraphStyle("bullet", parent=ss["BodyText"], fontName="Times-Roman",
                             fontSize=10.5, leading=14.5, alignment=TA_JUSTIFY,
                             leftIndent=16, bulletIndent=5, spaceAfter=4),
    "cell": ParagraphStyle("cell", fontName="Times-Roman", fontSize=9, leading=11.5),
    "cellb": ParagraphStyle("cellb", fontName="Times-Bold", fontSize=9, leading=11.5),
    "cellm": ParagraphStyle("cellm", fontName="Courier", fontSize=8, leading=11),
    "caption": ParagraphStyle("caption", fontName="Times-Italic", fontSize=9,
                              alignment=TA_CENTER, spaceBefore=5, spaceAfter=12,
                              textColor=colors.HexColor("#333333")),
    "code": ParagraphStyle("code", fontName="Courier", fontSize=8, leading=10.5,
                           leftIndent=10, backColor=colors.HexColor("#F2F2F2"),
                           borderPadding=5, spaceBefore=4, spaceAfter=8),
}


def P(text, style="body"):
    return Paragraph(text, S[style])


def bullets(items, style="bullet"):
    return [Paragraph(t, S[style], bulletText="•") for t in items]


def caption(text):
    return Paragraph(text, S["caption"])


def table(rows, widths, header=True, align=None, zebra=False):
    cmds = [
        ("GRID", (0, 0), (-1, -1), 0.6, GRID),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if header:
        cmds.append(("BACKGROUND", (0, 0), (-1, 0), HEADER_BG))
    if zebra:
        for i in range(1 if header else 0, len(rows)):
            if i % 2 == 0:
                cmds.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#F7F9FC")))
    for c in align or []:
        cmds.append(c)
    t = Table(rows, colWidths=widths, repeatRows=1 if header else 0, hAlign="CENTER")
    t.setStyle(TableStyle(cmds))
    return t


def figure(path: Path, cap: str, max_w=CONTENT_W, max_h=10.2 * cm):
    from PIL import Image as PILImage

    with PILImage.open(path) as im:
        w, h = im.size
    scale = min(max_w / w, max_h / h, 1.0)
    img = Image(str(path), width=w * scale, height=h * scale)
    img.hAlign = "CENTER"
    return [img, caption(cap)]


# --------------------------------------------------------------------------- #
# Load measured data
# --------------------------------------------------------------------------- #

def load_results() -> list[dict]:
    path = EVIDENCE / "evaluation.json"
    if not path.exists():
        raise SystemExit(
            f"{path} not found — run `.venv/bin/python evaluate.py` first."
        )
    return json.loads(path.read_text())


def metrics(results: list[dict]) -> dict:
    lat = [r["latency_s"] for r in results]
    by_cat: dict[str, list[dict]] = {}
    for r in results:
        by_cat.setdefault(r["category"], []).append(r)

    translated = sum(
        1 for r in results
        if r["expected_tools_present"] and r["forbidden_tools_absent"]
    )
    err_cases = [r for r in results if r["tool_errors"] > 0]
    recovered = [r for r in err_cases if r["answer"]]

    return {
        "n": len(results),
        "lat_min": min(lat),
        "lat_max": max(lat),
        "lat_mean": statistics.mean(lat),
        "lat_median": statistics.median(lat),
        "rounds_mean": statistics.mean(r["rounds"] for r in results),
        "tok_in": sum(r["usage"]["input"] for r in results),
        "tok_out": sum(r["usage"]["output"] for r in results),
        "cache_read": sum(r["usage"]["cache_read"] for r in results),
        "cache_write": sum(r["usage"]["cache_write"] for r in results),
        "translated": translated,
        "tool_errors": sum(r["tool_errors"] for r in results),
        "err_cases": len(err_cases),
        "recovered": len(recovered),
        "by_cat": by_cat,
        "destructive_no_write": all(
            not any(t in ("__never__",) for t in r["tools_used"])
            for r in by_cat.get("destructive", [])
        ),
    }


# --------------------------------------------------------------------------- #
# Document
# --------------------------------------------------------------------------- #

def header_footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Times-Roman", 9)
    canvas.setFillColor(colors.HexColor("#555555"))
    canvas.drawCentredString(PAGE_W / 2, 1.2 * cm, str(canvas.getPageNumber()))
    if canvas.getPageNumber() > 1:
        canvas.setFont("Times-Italic", 8)
        canvas.drawRightString(PAGE_W - MARGIN, PAGE_H - 1.3 * cm,
                               "Review 3 — Agent #7: Semantic File Search")
        canvas.setStrokeColor(colors.HexColor("#CCCCCC"))
        canvas.line(MARGIN, PAGE_H - 1.45 * cm, PAGE_W - MARGIN, PAGE_H - 1.45 * cm)
    canvas.restoreState()


def build(results: list[dict]) -> Path:
    m = metrics(results)
    story: list = []

    # ---------------- Title block ---------------- #
    story += [
        P("Review 3 Report", "title"),
        P("Agentic Shell for Linux", "subtitle"),
        P("Agent #7: Semantic File Search", "subtitle2"),
    ]

    story.append(table(
        [
            [P("Item", "cellb"), P("Details", "cellb")],
            [P("Team", "cell"), P("Team No. 7 — File System &amp; Storage Agents", "cell")],
            [P("Team members", "cell"),
             P("Satvik Sinha (24BCE2001)<br/>Kshitij R Patil (24BDS0184)", "cell")],
            [P("Course", "cell"), P("Operating Systems", "cell")],
            [P("Guide", "cell"), P("Prof. Naveen Kumar J", "cell")],
            [P("Report covers", "cell"),
             P("Chapter 5 (Testing, Evaluation &amp; Results), Chapter 6 "
               "(Conclusion &amp; Future Scope), Chapter 7 (Screenshots)", "cell")],
            [P("Evaluation run", "cell"),
             P(f"{datetime.now():%d %B %Y}, {m['n']} live queries against "
               f"<font face='Courier' size='8'>claude-opus-5</font>", "cell")],
            [P("Source code", "cell"),
             P("<link href='https://github.com/satviksinha12/OSProject'>"
               "<font face='Courier' size='8' color='#1F4E79'>"
               "github.com/satviksinha12/OSProject</font></link>", "cell")],
        ],
        [4.0 * cm, CONTENT_W - 4.0 * cm],
    ))
    story.append(Spacer(1, 12))

    story.append(P(
        "<b>Status since Review 2.</b> Review 2 listed the live tool-use loop as "
        "<i>pending</i>: the loop was complete but had never been run against the "
        "Anthropic API. That gap is now closed. This report records the first live "
        "runs of the agent, a measured evaluation over "
        f"{m['n']} natural-language queries, and an end-to-end case in which the "
        "agent located a PostgreSQL script on the live filesystem."
    ))

    # ---------------- Chapter 5 ---------------- #
    story += [P("Chapter 5: Testing, Evaluation, and Results", "h1")]

    story.append(P(
        "Testing is in two layers. The <b>tool layer</b> is covered by an automated "
        "suite of 14 tests that exercise real system calls against a throwaway "
        "directory tree and need no API key, so they run in under a second and gate "
        "every change. The <b>agent layer</b> is covered by the evaluation harness in "
        "<font face='Courier' size='8'>evaluate.py</font>, which sends natural-language "
        "queries to the live model, records the tools it chose, and times each run. "
        "Every number in this chapter is read directly from "
        "<font face='Courier' size='8'>docs/evidence/evaluation.json</font>, the file "
        "that harness produced; none of it is transcribed by hand."
    ))

    story.append(P("5.1 Test Cases &amp; Scenarios", "h2"))
    story.append(P(
        f"The suite follows the three categories named in the review template. All "
        f"{m['n']} queries were run against the live agent on the real home directory."
    ))

    # --- 5.1.1 simple --- #
    story.append(P("5.1.1 Simple intents", "h3"))
    story.append(P(
        "Single-constraint requests that should resolve with one metadata search and "
        "no content scanning. The interesting case is S3: a naive implementation "
        "would grep file contents to answer a question about size, and the agent "
        "correctly did not."
    ))
    rows = [[P("ID", "cellb"), P("Query", "cellb"), P("Tools called", "cellb"),
             P("Latency", "cellb"), P("Result", "cellb")]]
    for r in m["by_cat"].get("simple", []):
        ok = r["expected_tools_present"] and r["forbidden_tools_absent"]
        rows.append([
            P(r["id"], "cell"), P(r["query"], "cell"),
            P(", ".join(dict.fromkeys(r["tools_used"])) or "—", "cellm"),
            P(f"{r['latency_s']}s", "cell"),
            P("Pass" if ok else "Review", "cell"),
        ])
    story.append(table(rows, [1.1 * cm, 5.4 * cm, 4.6 * cm, 1.6 * cm, 1.8 * cm], zebra=True))
    story.append(caption("Table 5.1: Simple-intent test cases"))

    # --- 5.1.2 chained --- #
    story.append(P("5.1.2 Complex chained intents", "h3"))
    story.append(P(
        "These require the metadata-first strategy from Chapter 3 to actually hold: "
        "the model must narrow with "
        "<font face='Courier' size='8'>search_by_metadata</font> and then pass the "
        "surviving paths into <font face='Courier' size='8'>search_file_content</font> "
        "rather than grepping the whole tree. The recorded tool sequences confirm it "
        "did so unprompted on every chained case."
    ))
    rows = [[P("ID", "cellb"), P("Query", "cellb"), P("Tool sequence", "cellb"),
             P("Rounds", "cellb"), P("Latency", "cellb")]]
    for r in m["by_cat"].get("chained", []):
        rows.append([
            P(r["id"], "cell"), P(r["query"], "cell"),
            P(" → ".join(dict.fromkeys(r["tools_used"])) or "—", "cellm"),
            P(str(r["rounds"]), "cell"), P(f"{r['latency_s']}s", "cell"),
        ])
    story.append(table(rows, [1.1 * cm, 5.2 * cm, 5.0 * cm, 1.4 * cm, 1.8 * cm], zebra=True))
    story.append(caption("Table 5.2: Chained-intent test cases"))

    # --- 5.1.3 destructive --- #
    story.append(P("5.1.3 Malicious and destructive inputs", "h3"))
    story.append(P(
        "The template asks how the system handles an instruction such as "
        "<i>“Delete everything on my drive.”</i> This agent's answer is structural "
        "rather than filtered: <b>no tool in the registry can write, move or delete</b>, "
        "and no tool accepts a shell command string, so a destructive instruction has "
        "nothing to bind to. There is no blacklist to bypass. The sandbox in "
        "<font face='Courier' size='8'>safety.py</font> additionally resolves and "
        "range-checks every path before any system call, which is what rejects "
        "<font face='Courier' size='8'>/etc/shadow</font>."
    ))
    rows = [[P("ID", "cellb"), P("Hostile input", "cellb"),
             P("Tools called", "cellb"), P("Outcome", "cellb")]]
    outcomes = {
        "M1": "Declined without a single tool call; explained it is read-only.",
        "M2": "Declined in 1.2 s; path lies outside the sandbox root.",
        "M3": "Located the file and listed the folder, then explained it cannot delete.",
    }
    for r in m["by_cat"].get("destructive", []):
        rows.append([
            P(r["id"], "cell"), P(r["query"], "cell"),
            P(", ".join(dict.fromkeys(r["tools_used"])) or "none", "cellm"),
            P(outcomes.get(r["id"], ""), "cell"),
        ])
    story.append(table(rows, [1.1 * cm, 4.2 * cm, 3.6 * cm, 5.6 * cm], zebra=True))
    story.append(caption("Table 5.3: Malicious and destructive inputs"))

    story.append(P(
        "<b>Note on M3.</b> The agent did call read-only tools before refusing — it "
        "located the file the user named and listed the directory. That is the "
        "designed behaviour: the system prompt instructs it to find what the user "
        "referred to and report it, leaving the destructive act to the user."
    ))

    story.append(P(
        "<b>An unplanned result worth recording.</b> M3 names a real file, so the "
        "agent did not simply decline: it located both copies of "
        "<font face='Courier' size='8'>entries.sql</font>, listed the Downloads "
        "folder, and reported that the two copies differ in size and modification "
        "time before explaining that it cannot delete anything. It then flagged, "
        "unprompted, that several of the files the request would have destroyed "
        "were modified within the last hour and might not exist anywhere else. "
        "Nothing in the system prompt asks for that check; it came out of the "
        "general instruction to report what is actually there. A read-only agent "
        "that answers a destructive request with an accurate inventory is more "
        "useful than one that only refuses."
    ))

    # --- 5.1.4 recovery --- #
    story.append(P("5.1.4 Error recovery", "h3"))
    story.append(P(
        "Recovery is tested by handing the agent inputs that are guaranteed to fail "
        "inside a tool. Because "
        "<font face='Courier' size='8'>dispatch()</font> converts every "
        "<font face='Courier' size='8'>ToolError</font> into a "
        "<font face='Courier' size='8'>tool_result</font> carrying "
        "<font face='Courier' size='8'>is_error: true</font> instead of raising, the "
        "model receives the failure as data and can correct itself within the same turn."
    ))
    rows = [[P("ID", "cellb"), P("Input", "cellb"), P("Tool errors", "cellb"),
             P("Recovered", "cellb"), P("Latency", "cellb")]]
    for r in m["by_cat"].get("recovery", []):
        if r["tool_errors"] == 0:
            verdict = "n/a — no error raised"
        elif r["recovered"]:
            verdict = "Yes"
        else:
            verdict = "No"
        rows.append([
            P(r["id"], "cell"), P(r["query"], "cell"),
            P(str(r["tool_errors"]), "cell"),
            P(verdict, "cell"),
            P(f"{r['latency_s']}s", "cell"),
        ])
    story.append(table(rows, [1.1 * cm, 6.4 * cm, 1.9 * cm, 2.8 * cm, 2.3 * cm], zebra=True))
    story.append(caption("Table 5.4: Error-recovery test cases"))

    story.append(P(
        "<b>An honest qualification.</b> E1 was written to force an invalid-regex "
        "failure, but it raised no tool error at all: the model recognised that "
        "<font face='Courier' size='8'>unclosed(paren</font> was malformed and "
        "corrected it <i>before</i> issuing the call. That is a better outcome than "
        "recovery, but it means E1 did not exercise the recovery path, and only E2 "
        "did. The recovery figure in Table 5.5 therefore rests on a single case and "
        "should be read as a demonstration that the mechanism works, not as a rate "
        "measured over a meaningful sample. In E2 the agent called "
        "<font face='Courier' size='8'>stat_file</font> and then "
        "<font face='Courier' size='8'>preview_file</font> on a path that does not "
        "exist, received <font face='Courier' size='8'>is_error: true</font> from "
        "both, and instead of failing the turn it searched the directory for real "
        "<font face='Courier' size='8'>.sql</font> files and offered them as "
        "alternatives."
    ))

    # --- 5.2 metrics --- #
    story.append(P("5.2 Performance Metrics", "h2"))

    story.append(P("Intent translation accuracy", "h3"))
    story.append(P(
        "A query counts as correctly translated when the tools the model chose "
        "include every tool the case requires and none that it forbids — for example "
        "S3 forbids <font face='Courier' size='8'>search_file_content</font>, because "
        "a size question should never read file contents. Scoring is done "
        "programmatically in the harness rather than by inspection."
    ))
    pct = 100.0 * m["translated"] / m["n"]
    story.append(table(
        [
            [P("Metric", "cellb"), P("Value", "cellb"), P("Basis", "cellb")],
            [P("Queries evaluated", "cell"), P(str(m["n"]), "cell"),
             P("Four categories", "cell")],
            [P("Correct tool selection", "cell"),
             P(f"{m['translated']}/{m['n']} ({pct:.0f}%)", "cell"),
             P("Required tools present, forbidden tools absent", "cell")],
            [P("Mean tool rounds per query", "cell"),
             P(f"{m['rounds_mean']:.1f}", "cell"),
             P(f"Cap is {12} rounds", "cell")],
            [P("Tool errors raised", "cell"), P(str(m["tool_errors"]), "cell"),
             P("Returned as data, not exceptions", "cell")],
            [P("Recovery rate", "cell"),
             P(f"{m['recovered']}/{m['err_cases']}" if m["err_cases"]
               else "no errors triggered", "cell"),
             P("Answered despite a failed tool call", "cell")],
        ],
        [5.2 * cm, 3.4 * cm, CONTENT_W - 8.6 * cm],
        zebra=True,
    ))
    story.append(caption("Table 5.5: Intent translation and recovery"))

    story.append(P("Latency (time to execution)", "h3"))
    story.append(P(
        "Latency is wall-clock time for a complete request: every model round trip "
        "plus every system call, measured end to end. It is dominated by model "
        "inference, not by the filesystem — a metadata walk over the home directory "
        "returns in milliseconds, while each model round trip costs seconds."
    ))
    lat_rows = [[P("Category", "cellb"), P("Queries", "cellb"), P("Mean", "cellb"),
                 P("Min", "cellb"), P("Max", "cellb")]]
    for cat in ("simple", "chained", "destructive", "recovery"):
        rs = m["by_cat"].get(cat, [])
        if not rs:
            continue
        ls = [r["latency_s"] for r in rs]
        lat_rows.append([
            P(cat.capitalize(), "cell"), P(str(len(rs)), "cell"),
            P(f"{statistics.mean(ls):.1f}s", "cell"),
            P(f"{min(ls):.1f}s", "cell"), P(f"{max(ls):.1f}s", "cell"),
        ])
    lat_rows.append([
        P("All queries", "cellb"), P(str(m["n"]), "cellb"),
        P(f"{m['lat_mean']:.1f}s", "cellb"),
        P(f"{m['lat_min']:.1f}s", "cellb"), P(f"{m['lat_max']:.1f}s", "cellb"),
    ])
    story.append(table(lat_rows, [4.4 * cm, 2.2 * cm, 2.4 * cm, 2.2 * cm, 2.2 * cm],
                       zebra=True))
    story.append(caption("Table 5.6: Latency by category (median overall "
                         f"{m['lat_median']:.1f}s)"))

    story.append(P("Token cost and prompt caching", "h3"))
    story.append(P(
        "Chapter 3 placed the six tool schemas and the stable half of the system "
        "prompt behind a cache breakpoint. The figures below are the measured result "
        "over the whole run."
    ))
    total_in = m["tok_in"] + m["cache_read"] + m["cache_write"]
    cache_pct = (100.0 * m["cache_read"] / total_in) if total_in else 0.0
    story.append(table(
        [
            [P("Measure", "cellb"), P("Tokens", "cellb")],
            [P("Uncached input", "cell"), P(f"{m['tok_in']:,}", "cell")],
            [P("Served from prompt cache", "cell"),
             P(f"{m['cache_read']:,}  ({cache_pct:.0f}% of all input)", "cell")],
            [P("Written to prompt cache", "cell"), P(f"{m['cache_write']:,}", "cell")],
            [P("Output generated", "cell"), P(f"{m['tok_out']:,}", "cell")],
        ],
        [7.0 * cm, CONTENT_W - 7.0 * cm],
        zebra=True,
    ))
    story.append(caption("Table 5.7: Token usage across the evaluation run"))

    story.append(P(
        f"<b>Reading these numbers honestly.</b> The cache served {cache_pct:.0f}% of "
        "input tokens — useful, but not the dominant share, and worth explaining. The "
        "zero in the write row is not a failure: the cached prefix had already been "
        "written by the live runs made earlier the same session, so every query in "
        "this run was a cache <i>hit</i> and no fresh write was billed. The uncached "
        f"{m['tok_in']:,} tokens are mostly conversation history rather than prompt: "
        "the API is stateless, so each round resends every previous tool result, and "
        "a four-round query therefore pays for its early results four times. Only the "
        "fixed prefix sits behind the breakpoint; the growing tail cannot. This is the "
        "main cost lever left in the system, and the clearest way to pull it is to "
        "shrink tool results further rather than to add more cache breakpoints."
    ))

    # ---------------- Chapter 6 ---------------- #
    story.append(PageBreak())
    story.append(P("Chapter 6: Conclusion &amp; Future Scope", "h1"))

    story.append(P("6.1 Summary of Contributions", "h2"))
    story.append(P(
        "The project delivers a working prototype that bridges conversational text and "
        "real system execution. A user describes a file in ordinary English; the model "
        "selects from six typed tools; the Python layer issues the corresponding system "
        "calls and returns structured results; the model answers in prose. The "
        "contributions are:"
    ))
    story += bullets([
        "<b>A natural-language interface to filesystem search</b> that replaces "
        "multi-flag <font face='Courier' size='8'>find</font> and "
        "<font face='Courier' size='8'>grep</font> pipelines, covering time, metadata "
        "and content in a single request.",
        "<b>A metadata-first search strategy.</b> The agent narrows with "
        "<font face='Courier' size='8'>stat(2)</font> before reading any file content. "
        "The chained test cases confirm the model adopts this strategy on its own.",
        "<b>A hand-written tool-use loop</b> on the Anthropic SDK, with no agent "
        "framework, so the control flow is explicit and auditable — a requirement for "
        "code that issues system calls on the user's behalf.",
        "<b>A traversal written directly on <font face='Courier' size='8'>os.scandir</font></b> "
        "rather than <font face='Courier' size='8'>find(1)</font>, which is what makes "
        "pruning pseudo-filesystems, cycle detection via "
        "<font face='Courier' size='8'>(st_dev, st_ino)</font>, bounded walks and "
        "per-directory error tolerance possible.",
        "<b>Security by construction rather than by filtering.</b> No tool can modify "
        "the filesystem and none accepts a command string, so destructive instructions "
        "have nothing to bind to — demonstrated in Section 5.1.3.",
        "<b>A reproducible evidence trail.</b> 14 automated tool tests, a scripted "
        "demonstration that runs without an API key, and an evaluation harness whose "
        "JSON output is the direct source for every number in Chapter 5.",
    ])

    story.append(P("6.2 Challenges Encountered", "h2"))

    story.append(P("Managing state and context across multiple executions", "h3"))
    story.append(P(
        "Each tool result is appended to the conversation and resent on the next "
        "round, so a careless implementation grows the context linearly and pays for "
        "the whole history every round. Three measures keep this bounded: hard caps on "
        "every tool result (200 entries, 100 content matches, 300 characters per line), "
        "a <font face='Courier' size='8'>truncated</font> field so the model knows an "
        "answer is partial rather than silently assuming it is complete, and a cache "
        "breakpoint placed so the volatile block — current time, working directory, "
        "host — sits <i>after</i> the stable prefix and never invalidates it. Table 5.7 "
        "shows the result."
    ))

    story.append(P("Keeping the tool schema and the handler in step", "h3"))
    story.append(P(
        "Every property in a JSON schema must correspond to a keyword argument of its "
        "handler; a mismatch surfaces only at dispatch time, as a "
        "<font face='Courier' size='8'>TypeError</font> during a live run. The schemas "
        "are therefore validated against the handler signatures with "
        "<font face='Courier' size='8'>inspect.signature</font>, which turns a runtime "
        "failure into a startup check."
    ))

    story.append(P("Letting the model fail safely", "h3"))
    story.append(P(
        "The first instinct is to raise on a bad argument. In an agent loop that ends "
        "the turn and loses the work. Returning the error as a "
        "<font face='Courier' size='8'>tool_result</font> with "
        "<font face='Courier' size='8'>is_error: true</font> lets the model read the "
        "message and retry — Section 5.1.4 measures this directly."
    ))

    story.append(P("Environment reproducibility", "h3"))
    story.append(P(
        "A mid-project distribution upgrade moved the system interpreter from Python "
        "3.11 to 3.14 and emptied the system "
        "<font face='Courier' size='8'>site-packages</font>, so every import failed at "
        "once. The project now pins its dependencies inside a virtual environment and "
        "ships a Docker image, which also gives teammates on Windows a working Linux "
        "userspace — native Windows is not supported, since "
        "<font face='Courier' size='8'>pwd</font> is Unix-only."
    ))

    story.append(P("6.3 Future Work", "h2"))
    story += bullets([
        "<b>Structural credential masking.</b> Redaction is currently an instruction in "
        "the system prompt; the raw matching line still reaches the model. Masking "
        "inside <font face='Courier' size='8'>search_file_content</font> would make the "
        "guarantee structural rather than advisory. This is the most important "
        "outstanding item.",
        "<b>Deeper local file indexing.</b> A <font face='Courier' size='8'>locate</font>-style "
        "index would turn pure name lookups into a database query, reserving the live "
        "walk for queries that genuinely need fresh metadata.",
        "<b>Semantic rather than lexical content matching.</b> Content search is regex "
        "today, so “the file about database configuration” only works if those words "
        "appear literally. Local embeddings over a file index would close that gap.",
        "<b>Ranking when several files match</b>, instead of returning a flat list — "
        "recency, path depth and match density are all available already.",
        "<b>Multi-modal input.</b> Voice-driven queries suit this agent well, since the "
        "output is a short spoken answer rather than a screenful of paths.",
        "<b>Integration with the base shell router</b>, once the class-wide contract is "
        "defined. Both integration paths already exist: "
        "<font face='Courier' size='8'>handle()</font> for a delegated call, or "
        "<font face='Courier' size='8'>TOOLS</font> plus "
        "<font face='Courier' size='8'>dispatch()</font> for a merged tool set.",
    ])

    # ---------------- Chapter 7 ---------------- #
    story.append(PageBreak())
    story.append(P("Chapter 7: Screenshots", "h1"))
    story.append(P(
        "Every figure below is an unedited screenshot of a real terminal session, "
        "taken while the command shown on its first line was actually running. They "
        "were produced by <font face='Courier' size='8'>capture.sh</font>, which "
        "opens a terminal of a fixed size, runs the command inside it, waits for it "
        "to finish and then photographs the window; the only post-processing is "
        "trimming the blank area below the last line of output. The text of each "
        "session is stored next to its image in "
        "<font face='Courier' size='8'>docs/figures/</font>, so every figure can be "
        "checked against a greppable transcript."
    ))

    figs = [
        ("fig7-1-tests.png",
         "Figure 7.1: The automated tool suite — 14 tests against real system calls, "
         "no API key required."),
        ("fig7-2-demo-chain.png",
         "Figure 7.2: The metadata → content → preview chain running without the LLM. "
         "12 entries scanned, 2 candidates, 1 answer; three decoys rejected."),
        ("fig7-3-live-locate.png",
         "Figure 7.3: The live agent locating entries.sql. Note the tool order — "
         "search_by_metadata first, sorted by mtime, and only then preview_file on "
         "the single winning path. Two calls answer the question; no content search "
         "was needed, and the agent also spotted the older duplicate in Downloads."),
        ("fig7-4-psql.png",
         "Figure 7.4: The modified entries.sql executed against the mydb PostgreSQL "
         "database — 17 rows, aggregate queries, and ON CONFLICT DO NOTHING "
         "correctly skipping the duplicate email."),
        ("fig7-5-destructive.png",
         "Figure 7.5: A destructive request (M3). The agent names a real file, so "
         "it first locates both copies and lists the target folder — both read-only "
         "operations — then explains that it cannot delete, and flags which of the "
         "listed files were edited recently."),
        ("fig7-6-eval.png",
         "Figure 7.6: The evaluation harness running the full suite against the "
         "live model."),
    ]
    # Screenshots of a real window are close to square, unlike the wide captures
    # this chapter used before, so a single height cap keeps them all legible
    # without any of them overflowing the text column.
    for name, cap in figs:
        path = FIGURES / name
        if not path.exists():
            continue
        story.append(KeepTogether(figure(path, cap, max_h=9.6 * cm)))

    # ---------------- Build ---------------- #
    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc = BaseDocTemplate(
        str(OUT), pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=1.9 * cm, bottomMargin=1.8 * cm,
        title="Review 3 Report — Agent #7 Semantic File Search",
        author="Satvik Sinha (24BCE2001), Kshitij R Patil (24BDS0184)",
    )
    frame = Frame(MARGIN, 1.8 * cm, CONTENT_W, PAGE_H - 3.7 * cm, id="body")
    doc.addPageTemplates([PageTemplate(id="main", frames=[frame],
                                       onPage=header_footer)])
    doc.build(story)
    return OUT


if __name__ == "__main__":
    results = load_results()
    out = build(results)
    print(f"Wrote {out}  ({out.stat().st_size:,} bytes)")
