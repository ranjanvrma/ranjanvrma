#!/usr/bin/env python3
"""Render the profile README cards as self-contained SVGs.

    python render_cards.py            animated cards  -> cards/
    STATIC=1 python render_cards.py   final frame only -> cards-static/ + cards-static/preview.html

Every card is shown through an <img> tag on GitHub, so each file carries its own
fonts (pyftsubset -> WOFF -> base64), its own CSS keyframes and its own SMIL.
Nothing is fetched at view time.

Contribution data is read from data/contributions.json, which the scheduled
workflow (.github/workflows/profile-cards.yml) refreshes before running this.
"""
from __future__ import annotations

import base64
import calendar
import datetime as dt
import html
import json
import mimetypes
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATIC = os.environ.get("STATIC") == "1"
OUT = ROOT / ("cards-static" if STATIC else "cards")
DATA = ROOT / "data" / "contributions.json"
FONT_DIR = ROOT / "fonts"

# --------------------------------------------------------------------------- content

PROFILE = {
    "login": "ranjanvrma",
    "name": "Ranjan Verma",
    "line": "AI & ML student at Symbiosis Institute of Technology, Pune. "
            "I build agentic systems and RAG apps, and take them past the notebook.",
    "status": "Open to ML, AI and data science internships",
    # Optional. Any jpg/png/webp; it is embedded, so keep it small (~300px square).
    "portrait": "assets/portrait.jpg",
}

# Three tiles in a row. File names are cards/tile-<key>.svg.
TILES = [
    {"key": "linkedin", "title": "LinkedIn", "detail": "in/ranjan-vrma"},
    {"key": "email", "title": "Email", "detail": "ranjanverma2310@gmail.com"},
    {"key": "repos", "title": "Repositories", "detail": "github.com/ranjanvrma"},
]

# One card per featured repo. File names are cards/repo-<slug>.svg.
# "stat" is optional: a real number from the project, shown on the right.
# "demo" is optional: a live URL. The repo then also gets cards/repo-<slug>-demo.svg,
# a button panel shown beside the card in the README.
REPOS = [
    {
        "slug": "rig-sight",
        "name": "Rig Sight",
        "tagline": "Agentic AI for drilling operations",
        "text": "A LangGraph multi-agent system that reads Daily Drilling Reports and tracks "
                "non-productive time across six categories. Planner, Research, Executor and "
                "Critic agents share state and check each other's work; all maths stays in "
                "deterministic Python.",
        "tags": ["LangGraph", "LangChain", "FastAPI", "Groq", "Pytest"],
        "stat": ("0.067 h", "NPT error on 15 synthetic reports, every planted mistake caught"),
        "demo": "https://rig-sight.onrender.com/",
    },
    {
        "slug": "docmind",
        "name": "DocMind",
        "tagline": "Document Q&A with grounded answers",
        "text": "A RAG app that chunks PDFs and indexes them for semantic search. Answers come "
                "with citations, and when the documents don't hold the answer it says so. "
                "Session isolation, rate limits and server-side API keys.",
        "tags": ["FastAPI", "React", "FAISS", "ONNX", "OpenRouter"],
        "demo": "https://docmind-gmjl.onrender.com",
    },
    {
        "slug": "fraud-detection",
        "name": "Fraud Risk Simulator",
        "tagline": "End-to-end fraud detection",
        "text": "Behavioural features and a Random Forest trained with SMOTE for class "
                "imbalance. Predictions run through a FastAPI service and a Streamlit app "
                "that gives a live fraud-risk score.",
        "tags": ["Scikit-learn", "Pandas", "FastAPI", "Streamlit"],
        "stat": ("6M+", "transactions analysed"),
        "demo": "https://fraud-risk-simulator.streamlit.app/",
    },
    {
        "slug": "retail-sales-analytics",
        "name": "Retail Sales Analytics",
        "tagline": "From messy data to business insights",
        "text": "What drives sales and profit across products, customers and regions. SQL with "
                "CTEs, joins and window functions, with an interactive Power BI dashboard on top.",
        "tags": ["SQL", "Power BI", "Power Query", "Python"],
    },
]

# --------------------------------------------------------------------------- theme
# Taken from the current README: JetBrains Mono, and #38BDF8 from the typing banner.
# Swap these for your website's values; nothing else in the file hard-codes colour.

BRAND = "#38BDF8"
THEMES = {
    "light": {"bg": "#F3F8FB", "line": "#D3E2EB", "ink": "#0B2131", "mut": "#4A6475",
              "acc": "#0369A1", "bar": "#0EA5E9"},
    "dark": {"bg": "#0F1D28", "line": "#223747", "ink": "#E4EEF4", "mut": "#8FA8B8",
             "acc": "#7DD3FC", "bar": BRAND},
}
FONT_FILES = {400: "JetBrainsMono-Regular.ttf", 700: "JetBrainsMono-Bold.ttf",
              800: "JetBrainsMono-ExtraBold.ttf"}
FONT_URL = "https://raw.githubusercontent.com/JetBrains/JetBrainsMono/master/fonts/ttf/"
FONT_STACK = "JBM, 'JetBrains Mono', ui-monospace, monospace"
ADVANCE = 0.6  # JetBrains Mono advance width in em; every glyph is the same width
W = 840        # full-width card; GitHub's README column is about 830px
# Repos with a demo are two images side by side: the card (links to the code) and a
# panel with the demo button (links to the demo). viewBox widths keep the README's
# width ratio, so both images scale to exactly the same height.
SPLIT = (74, 25)  # % widths in the README; the space between the tags is the gap
BODY_W = W * SPLIT[0] / sum(SPLIT)
PANEL_W = W * SPLIT[1] / sum(SPLIT)

# Timeline in seconds. All cards load together, so delays are global and the page
# plays from the hero down to the contribution graph.
T_HERO = 0.15
T_TILES = 1.45
T_ACTIVITY = 2.0
T_REPOS = 3.0
T_REPO_STEP = 0.28
T_GRAPH = T_REPOS + len(REPOS) * T_REPO_STEP + 0.1

# --------------------------------------------------------------------------- helpers


def esc(s: str) -> str:
    return html.escape(s, quote=True)


def width_of(s: str, size: float) -> float:
    return len(s) * size * ADVANCE


def wrap(s: str, max_px: float, size: float) -> list[str]:
    return textwrap.wrap(s, max(8, int(max_px // (size * ADVANCE))))


def ensure_fonts() -> None:
    FONT_DIR.mkdir(exist_ok=True)
    for name in FONT_FILES.values():
        path = FONT_DIR / name
        if not path.exists():
            import urllib.request
            print(f"downloading {name}")
            urllib.request.urlretrieve(FONT_URL + name, path)


_subset_cache: dict[tuple[int, str], str] = {}


def font_face(weight: int, chars: set[str]) -> str:
    text = "".join(sorted(chars | {" "}))
    key = (weight, text)
    if key not in _subset_cache:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "sub.woff"
            subprocess.run(
                ["pyftsubset", str(FONT_DIR / FONT_FILES[weight]), f"--text={text}",
                 "--flavor=woff", "--no-hinting", "--desubroutinize",
                 "--layout-features=kern", f"--output-file={out}"],
                check=True,
            )
            _subset_cache[key] = base64.b64encode(out.read_bytes()).decode()
    return (f"@font-face{{font-family:JBM;font-weight:{weight};"
            f"src:url(data:font/woff;base64,{_subset_cache[key]}) format('woff')}}")


def theme_vars(t: dict) -> str:
    return ";".join(f"--{k}:{v}" for k, v in t.items())


BASE_CSS = f"""
svg{{{theme_vars(THEMES['light'])}}}
@media (prefers-color-scheme:dark){{svg{{{theme_vars(THEMES['dark'])}}}}}
text{{font-family:{FONT_STACK}}}
.card{{fill:var(--bg);stroke:var(--line)}}
.ink{{fill:var(--ink)}}.mut{{fill:var(--mut)}}.acc{{fill:var(--acc)}}
.bar{{fill:var(--bar)}}.rule{{stroke:var(--line);fill:none}}.pill{{fill:none;stroke:var(--line)}}
.btn{{fill:var(--bar)}}.btnt{{fill:#0B2131}}.brand{{fill:{BRAND}}}.g0{{fill:var(--line)}}
.g1{{fill:{BRAND};fill-opacity:.32}}.g2{{fill:{BRAND};fill-opacity:.55}}
.g3{{fill:{BRAND};fill-opacity:.78}}.g4{{fill:{BRAND}}}
"""

MOTION_CSS = """
.rise{animation:rise .7s cubic-bezier(.2,.7,.2,1) both}
.fade{animation:fade .5s ease-out both}
.pop{animation:fade .3s ease-out both}
.blink{animation:blink 1.06s steps(1,end) 4 both}
@keyframes rise{from{opacity:0;transform:translateY(8px)}}
@keyframes fade{from{opacity:0}}
@keyframes blink{50%{opacity:0}}
.s{display:none}
@media (prefers-reduced-motion:reduce){
*{animation:none!important}
.m{display:none}.s{display:inline}
}
"""


class Card:
    """One SVG. Final-frame geometry is the base state; motion is layered on top,
    so dropping the motion (STATIC=1 or reduced motion) leaves the finished card."""

    def __init__(self, name: str, w: float, h: float, title: str, radius: float = 14):
        self.name, self.w, self.h, self.title, self.radius = name, w, h, title, radius
        self.body: list[str] = []
        self.defs: list[str] = []
        self.chars: dict[int, set[str]] = defaultdict(set)

    def text(self, x, y, s, size, weight=400, cls="ink", anchor="start") -> str:
        self.chars[weight].update(s)
        a = f' text-anchor="{anchor}"' if anchor != "start" else ""
        return (f'<text x="{x:g}" y="{y:g}" font-size="{size}" font-weight="{weight}" '
                f'class="{cls}"{a}>{esc(s)}</text>')

    def add(self, *parts: str) -> None:
        self.body.extend(parts)

    @staticmethod
    def anim(inner: str, delay: float, kind: str = "rise") -> str:
        """CSS keyframe entrance. Removed entirely in STATIC mode."""
        if STATIC:
            return inner
        return f'<g class="{kind}" style="animation-delay:{delay:.2f}s">{inner}</g>'

    @staticmethod
    def smil(moving: str, still: str) -> str:
        """SMIL version plus a still twin. CSS can't pause SMIL, so reduced motion
        hides the moving copy and shows the twin instead."""
        if STATIC:
            return still
        return f'<g class="m">{moving}</g><g class="s">{still}</g>'

    def render(self) -> str:
        faces = "".join(font_face(w, c) for w, c in sorted(self.chars.items()))
        css = faces + BASE_CSS + ("" if STATIC else MOTION_CSS)
        defs = f"<defs>{''.join(self.defs)}</defs>" if self.defs else ""
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
            f'width="{self.w:g}" height="{self.h:g}" viewBox="0 0 {self.w:g} {self.h:g}" '
            f'role="img" aria-label="{esc(self.title)}">'
            f"<title>{esc(self.title)}</title><style>{css}</style>{defs}"
            f'<rect class="card" x=".5" y=".5" width="{self.w - 1:g}" height="{self.h - 1:g}" '
            f'rx="{self.radius}"/>'
            + "".join(self.body) + "</svg>"
        )

    def save(self) -> None:
        (OUT / f"{self.name}.svg").write_text(self.render(), encoding="utf-8")
        print(f"  {self.name}.svg")


# --------------------------------------------------------------------------- data


def load_days() -> list[tuple[dt.date, int, int | None]]:
    if not DATA.exists():
        sys.exit(
            f"{DATA.relative_to(ROOT)} is missing. The workflow writes it; to fetch it "
            "locally, run the 'Refresh contribution data' commands from "
            ".github/workflows/profile-cards.yml with the gh CLI signed in."
        )
    raw = json.loads(DATA.read_text())
    days = [(dt.date.fromisoformat(d["date"]), int(d["count"]), d.get("level"))
            for d in raw["days"]]
    days.sort()
    if not days:
        sys.exit("contributions.json has no days in it")
    return days


def levels(days) -> list[int]:
    if all(lv is not None for _, _, lv in days):
        return [int(lv) for _, _, lv in days]
    nz = sorted(c for _, c, _ in days if c)
    if not nz:
        return [0] * len(days)
    q = [nz[int(len(nz) * f) - 1 if int(len(nz) * f) else 0] for f in (.25, .5, .75)]
    return [0 if c == 0 else 1 + sum(c > t for t in q) for _, c, _ in days]


def streaks(days) -> tuple[int, int]:
    best = run = 0
    for _, c, _ in days:
        run = run + 1 if c else 0
        best = max(best, run)
    counts = [c for _, c, _ in days]
    if counts and counts[-1] == 0:  # today isn't over yet; don't break the streak on it
        counts = counts[:-1]
    cur = 0
    for c in reversed(counts):
        if not c:
            break
        cur += 1
    return cur, best


def monthly(days) -> list[tuple[dt.date, int]]:
    last = days[-1][0].replace(day=1)
    months = []
    y, m = last.year, last.month
    for _ in range(12):
        months.append(dt.date(y, m, 1))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    months.reverse()
    totals = defaultdict(int)
    for d, c, _ in days:
        totals[d.replace(day=1)] += c
    return [(m, totals[m]) for m in months]


# --------------------------------------------------------------------------- cards


def hero() -> Card:
    pad, h = 44, 250
    portrait = ROOT / PROFILE["portrait"] if PROFILE.get("portrait") else None
    has_portrait = bool(portrait and portrait.exists())
    text_w = W - 2 * pad - (200 if has_portrait else 0)
    c = Card("hero", W, h, f"{PROFILE['name']}. {PROFILE['line']}", radius=20)

    # Name, typed in one character at a time: a nod to the old typing banner.
    name, size, x0, base = PROFILE["name"], 56, pad, 112
    cw = size * ADVANCE
    n = len(name)
    full = n * cw
    t0, per = T_HERO + 0.25, 0.07
    steps = ";".join(f"{i * cw:g}" for i in range(n + 1))
    caret_xs = ";".join(f"{x0 + i * cw + 3:g}" for i in range(n + 1))
    times = ";".join(f"{i / n:.4f}" for i in range(n + 1))
    typing = f'begin="{t0}s" dur="{n * per:.2f}s" calcMode="discrete" keyTimes="{times}" fill="freeze"'
    c.defs.append(
        f'<clipPath id="type"><rect x="{x0 - 2}" y="{base - size}" width="0" height="{size * 1.4:g}">'
        f'<animate attributeName="width" values="{steps}" {typing}/></rect></clipPath>'
    )
    name_svg = c.text(x0, base, name, size, 800)
    caret_still = f'<rect class="brand" x="{x0 + full + 3:g}" y="{base - size * .74:g}" width="6" height="{size * .8:g}"/>'
    caret_moving = (
        f'<g class="blink" style="animation-delay:{t0 + n * per:.2f}s">'
        f'<rect class="brand" x="{x0 + 3}" y="{base - size * .74:g}" width="6" height="{size * .8:g}">'
        f'<animate attributeName="x" values="{caret_xs}" {typing}/></rect></g>'
    )
    c.add(c.smil(f'<g clip-path="url(#type)">{name_svg}</g>{caret_moving}', name_svg + caret_still))

    y = base + 46
    lines = wrap(PROFILE["line"], text_w, 17)
    c.add(c.anim("".join(c.text(pad, y + i * 27, ln, 17, 400, "mut") for i, ln in enumerate(lines)),
                 t0 + n * per - 0.1))

    sy = y + len(lines) * 27 + 22
    ring = (f'<circle cx="{pad + 6}" cy="{sy - 5}" r="6" fill="none" stroke="{BRAND}" stroke-width="2" opacity="0">'
            f'<animate attributeName="r" values="6;16" begin="{t0 + n * per + 0.6:.2f}s" dur="1.6s" repeatCount="3"/>'
            f'<animate attributeName="opacity" values=".7;0" begin="{t0 + n * per + 0.6:.2f}s" dur="1.6s" repeatCount="3"/>'
            f"</circle>")
    status = (c.smil(ring, "") + f'<circle class="brand" cx="{pad + 6}" cy="{sy - 5}" r="6"/>'
              + c.text(pad + 22, sy, PROFILE["status"], 14, 700, "acc"))
    c.add(c.anim(status, t0 + n * per + 0.25))

    c.h = h = max(h, sy + 46)
    if has_portrait:
        r = 82
        cx, cy = W - pad - r, h / 2
        mime = mimetypes.guess_type(portrait.name)[0] or "image/jpeg"
        data = base64.b64encode(portrait.read_bytes()).decode()
        c.defs.append(f'<clipPath id="face"><circle cx="{cx}" cy="{cy}" r="{r}"/></clipPath>')
        img = (f'<image href="data:{mime};base64,{data}" x="{cx - r}" y="{cy - r}" width="{2 * r}" '
               f'height="{2 * r}" preserveAspectRatio="xMidYMid slice" clip-path="url(#face)"/>'
               f'<circle cx="{cx}" cy="{cy}" r="{r + 5}" fill="none" stroke="{BRAND}" stroke-width="2"/>')
        c.add(c.anim(img, T_HERO, "fade"))
    return c


def tile(i: int, t: dict) -> Card:
    w, h = 272, 112
    c = Card(f"tile-{t['key']}", w, h, f"{t['title']}: {t['detail']}", radius=12)
    inner = (c.text(24, 50, t["title"], 20, 800)
             + c.text(24, 80, t["detail"], 13, 400, "acc")
             + f'<rect class="bar" x="0" y="26" width="4" height="{h - 52}"/>')
    c.add(c.anim(inner, T_TILES + i * 0.15))
    return c


def activity(days) -> Card:
    h, pad = 270, 44
    months = monthly(days)
    total = sum(v for _, v in months)
    cur, best = streaks(days)
    span = [d for d in days if d[0] >= months[0][0]]
    active = sum(1 for _, cnt, _ in span if cnt)
    busy_d, busy_c, _ = max(days, key=lambda d: (d[1], d[0]))
    c = Card("activity", W, h, f"{total} contributions in the last 12 months; current streak "
             f"{cur} days; {active} active days; busiest day {busy_d:%d %b %Y} with {busy_c}.")

    def stat(y, value, rest):
        return c.text(pad, y, value, 15, 700) + c.text(pad + width_of(value, 15) + 9, y, rest, 13, 400, "mut")

    left = (c.text(pad, 86, f"{total:,}", 44, 800)
            + c.text(pad, 114, "contributions in the last 12 months", 13, 400, "mut")
            + (stat(164, f"{cur} day{'s' if cur != 1 else ''}", f"current streak, best {best}") if cur
               else stat(164, f"{best} day{'s' if best != 1 else ''}", "longest streak this year"))
            + stat(198, f"{active}", f"active days of {len(span)}")
            + stat(232, f"{busy_c}", f"on {busy_d:%a %d %b}, the busiest day"))
    c.add(c.anim(left, T_ACTIVITY))

    x0, x1, base, top = 340, W - pad, 214, 70
    slot = (x1 - x0) / len(months)
    bw = slot * 0.58
    peak = max(1, max(v for _, v in months))
    c.add(f'<line class="rule" x1="{x0}" y1="{base + .5}" x2="{x1}" y2="{base + .5}"/>')
    for i, (m, v) in enumerate(months):
        bh = max(2.0, (base - top) * v / peak) if v else 0
        x = x0 + i * slot + (slot - bw) / 2
        cx = x + bw / 2
        d = T_ACTIVITY + 0.3 + i * 0.06
        still = f'<rect class="bar" x="{x:.1f}" y="{base - bh:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="2"/>'
        spline = 'calcMode="spline" keyTimes="0;1" keySplines=".2 .7 .2 1" fill="freeze"'
        moving = (f'<rect class="bar" x="{x:.1f}" y="{base}" width="{bw:.1f}" height="0" rx="2">'
                  f'<animate attributeName="height" from="0" to="{bh:.1f}" begin="{d:.2f}s" dur=".7s" {spline}/>'
                  f'<animate attributeName="y" from="{base}" to="{base - bh:.1f}" begin="{d:.2f}s" dur=".7s" {spline}/>'
                  f"</rect>")
        c.add(c.smil(moving, still))
        c.add(c.anim(c.text(cx, base - bh - 8, str(v), 11, 700 if v == peak else 400,
                            "ink" if v == peak else "mut", "middle"), d + 0.45, "fade"))
        c.add(c.text(cx, base + 22, calendar.month_abbr[m.month], 11, 400, "mut", "middle"))
    return c


def repo(i: int, r: dict) -> list[Card]:
    pad = 40
    demo = r.get("demo")
    w = BODY_W if demo else W
    stat_w = 230 if r.get("stat") and not demo else 0  # with a demo, the stat moves to the panel
    text_w = w - 2 * pad - stat_w
    lines = wrap(r["text"], text_w, 14)

    # Lay the tags out first, wrapping to a second row if the narrower card needs it.
    tags_y = 128 + len(lines) * 22 + 8
    pills, x, y = [], pad, tags_y
    for tag in r["tags"]:
        pw = width_of(tag, 12) + 22
        if x + pw > w - pad - stat_w and x > pad:
            x, y = pad, y + 32
        pills.append((tag, x, y, pw))
        x += pw + 8
    h = y + 24 + 34
    c = Card(f"repo-{r['slug']}", w, h, f"{r['name']}: {r['tagline']}. {r['text']}")

    body = c.text(pad, 62, r["name"], 24, 800) + c.text(pad, 90, r["tagline"], 15, 700, "acc")
    body += "".join(c.text(pad, 124 + j * 22, ln, 14, 400, "mut") for j, ln in enumerate(lines))
    for tag, x, y, pw in pills:
        body += (f'<rect class="pill" x="{x + .5:g}" y="{y + .5}" width="{pw:g}" height="24" rx="12"/>'
                 + c.text(x + pw / 2, y + 16.5, tag, 12, 400, "ink", "middle"))
    if r.get("stat") and not demo:
        value, label = r["stat"]
        sx = W - pad
        body += f'<line class="rule" x1="{sx - stat_w + 20}" y1="42" x2="{sx - stat_w + 20}" y2="{h - 42}"/>'
        body += c.text(sx, 92, value, 36, 800, "acc", "end")
        body += "".join(c.text(sx, 120 + j * 18, ln, 12, 400, "mut", "end")
                        for j, ln in enumerate(wrap(label, stat_w - 44, 12)))
    c.add(c.anim(body, T_REPOS + i * T_REPO_STEP))
    return [c, demo_panel(i, r, h)] if demo else [c]


def demo_panel(i: int, r: dict, h: float) -> Card:
    """The narrow right-hand image. The whole panel is the link, styled as a button."""
    pad, bh = 24, 40
    bw, cx = PANEL_W - 2 * pad, PANEL_W / 2
    c = Card(f"repo-{r['slug']}-demo", PANEL_W, h, f"Open the {r['name']} live demo")
    body = ""
    if r.get("stat"):
        value, label = r["stat"]
        body += c.text(cx, 88, value, 34, 800, "acc", "middle")
        body += "".join(c.text(cx, 114 + j * 18, ln, 12, 400, "mut", "middle")
                        for j, ln in enumerate(wrap(label, bw, 12)))
        by = h - 40 - bh
    else:
        by = (h - bh) / 2
    body += (f'<rect class="btn" x="{pad}" y="{by:g}" width="{bw:g}" height="{bh}" rx="{bh / 2:g}"/>'
             + c.text(cx, by + 25, "Open live demo", 13, 800, "btnt", "middle"))
    c.add(c.anim(body, T_REPOS + i * T_REPO_STEP + 0.12))
    return c


def graph(days) -> Card:
    lv = levels(days)
    first = days[0][0]
    offset = (first.weekday() + 1) % 7  # GitHub's weeks start on Sunday
    cols = (offset + len(days) + 6) // 7
    step = min(15.0, (W - 44) / cols)
    cell = step - 3
    x0 = (W - cols * step + 3) / 2
    top, h = 78, 78 + 7 * step + 26
    total = sum(cnt for _, cnt, _ in days)
    c = Card("contributions", W, h, f"Contribution graph: {total} contributions from "
             f"{first:%d %b %Y} to {days[-1][0]:%d %b %Y}.")

    head = c.text(x0, 42, "Day by day", 15, 700)
    lx = W - x0
    head += c.text(lx, 42, "More", 11, 400, "mut", "end")
    lx -= width_of("More", 11) + 6 + 4 * (cell + 3) + cell
    for k in range(5):
        head += f'<rect class="g{k}" x="{lx + k * (cell + 3):.1f}" y="{32}" width="{cell:.1f}" height="{cell:.1f}" rx="2.5"/>'
    head += c.text(lx - 6, 42, "Less", 11, 400, "mut", "end")
    c.add(c.anim(head, T_GRAPH, "fade"))

    columns: dict[int, list[str]] = defaultdict(list)
    last_label = -9
    for idx, ((d, cnt, _), level) in enumerate(zip(days, lv)):
        col, row = divmod(idx + offset, 7)
        x, y = x0 + col * step, top + row * step
        columns[col].append(f'<rect class="g{level}" x="{x:.1f}" y="{y:.1f}" width="{cell:.1f}" '
                            f'height="{cell:.1f}" rx="2.5"/>')
        if d.day <= 7 and row == 0 and col - last_label >= 3:
            c.add(c.text(x, top - 10, calendar.month_abbr[d.month], 11, 400, "mut"))
            last_label = col
    for col in sorted(columns):
        c.add(c.anim("".join(columns[col]), T_GRAPH + 0.15 + col * 0.012, "pop"))
    return c


# --------------------------------------------------------------------------- main


def preview(cards: list[Card]) -> None:
    by = {c.name: c for c in cards}
    tiles = "".join(f'<img src="{t}.svg" width="32.5%"> ' for t in by if t.startswith("tile-"))
    repos = "".join(
        f'<p style="margin:0"><img src="repo-{r["slug"]}.svg" width="{SPLIT[0]}%"> '
        f'<img src="repo-{r["slug"]}-demo.svg" width="{SPLIT[1]}%"></p>' if r.get("demo")
        else f'<img src="repo-{r["slug"]}.svg" width="100%">' for r in REPOS)
    (OUT / "preview.html").write_text(
        "<!doctype html><meta charset=utf-8><title>Profile cards preview</title>"
        "<style>body{margin:0;padding:32px 0;background:#fff}main{max-width:830px;margin:auto}"
        "img{display:inline-block;vertical-align:top;margin-bottom:8px}"
        "@media (prefers-color-scheme:dark){body{background:#0d1117}}</style><main>"
        f'<img src="hero.svg" width="100%"><p style="margin:0">{tiles}</p>'
        f'<img src="activity.svg" width="100%">{repos}<img src="contributions.svg" width="100%"></main>',
        encoding="utf-8",
    )


def main() -> None:
    if shutil.which("pyftsubset") is None:
        sys.exit("pyftsubset not found: pip install fonttools")
    ensure_fonts()
    days = load_days()
    OUT.mkdir(exist_ok=True)
    for old in OUT.glob("*.svg"):
        old.unlink()
    print(f"rendering {'static' if STATIC else 'animated'} cards into {OUT.relative_to(ROOT)}/")
    cards = [hero(), *(tile(i, t) for i, t in enumerate(TILES)), activity(days),
             *(card for i, r in enumerate(REPOS) for card in repo(i, r)), graph(days)]
    for card in cards:
        card.save()
    if STATIC:
        preview(cards)
        print(f"open {(OUT / 'preview.html').relative_to(ROOT)} to screenshot the final frame")


if __name__ == "__main__":
    main()
