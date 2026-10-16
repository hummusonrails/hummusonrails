# /// script
# requires-python = ">=3.11"
# dependencies = ["fonttools>=4.50", "uharfbuzz>=0.39"]
# ///
"""Builds the profile README and every card it shows, in dark and light.

GitHub strips CSS and scripts from READMEs, so each designed element is an
SVG in assets/. Text is shaped with HarfBuzz and drawn as outlines, so the
cards look the same everywhere without loading a web font.

Live data comes from three public sources: the blog RSS feed, the YouTube
channel feed, and the public repo list. If a source fails, the last good
data in assets/state.json is reused so a hiccup never blanks a card.

Run locally:  uv run .github/scripts/build_profile.py
"""
from __future__ import annotations

import base64
import email.utils
import hashlib
import json
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from html import escape
from pathlib import Path

import uharfbuzz as hb
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "assets"
FONTS = ASSETS / "fonts"
STATE = ASSETS / "state.json"

USER = "hummusonrails"
SITE = "https://www.bengreenberg.dev"
RSS = f"{SITE}/rss.xml"
YT_CHANNEL = "UC3Ug3f0ZZEBl8RQoFI6YNNQ"
YT_URL = "https://www.youtube.com/@HummusOnRails"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) hummusonrails-profile"

# Public repos that are tooling, fixtures or asset dumps, not work to show off.
SKIP_REPOS = {USER, "devto-blog-assets", "oss-hunter-livefire", "agent-playground"}

THEMES = {
    # Neutrals are GitHub's own Primer page colors, so the cards sit flush on the profile.
    "dark": dict(bg="#0d1117", surf="#151b23", line="#3d444d", fg="#f0f6fc",
                 fg2="#9198a1", muted="#9198a1", acc="#ff9f45", acc2="#ffa85c"),
    "light": dict(bg="#ffffff", surf="#f6f8fa", line="#d1d9e0", fg="#1f2328",
                  fg2="#59636e", muted="#59636e", acc="#e85d04", acc2="#b23a00"),
}

TILES = [
    dict(label="Day job", title="DevRel at Arbitrum",
         sub="Getting more developers from first code to production on Arbitrum.",
         href="https://arbitrum.io"),
    dict(label="The book", title="Vector Search with JavaScript",
         sub="Pragmatic Bookshelf.",
         href="https://pragprog.com/titles/bgvector/vector-search-with-javascript/"),
    dict(label="Newsletter", title="Code and Conduct",
         sub="Technology, ethics, and the people between them.",
         href="https://codeplusconduct.substack.com"),
]

LANG_COLORS = {
    "TypeScript": "#3178c6", "JavaScript": "#f1e05a", "Rust": "#dea584", "Python": "#3572A5",
    "Ruby": "#701516", "Shell": "#89e051", "Solidity": "#AA6746", "Go": "#00ADD8", "Astro": "#ff5a03",
}


# ---------------------------------------------------------------- type

class Face:
    def __init__(self, file: str, wght: int | None = None):
        path = FONTS / file
        self.tt = TTFont(path)
        self.upem = self.tt["head"].unitsPerEm
        loc = {"wght": wght} if wght else None
        self.glyphs = self.tt.getGlyphSet(location=loc)
        self.order = self.tt.getGlyphOrder()
        self.hb = hb.Font(hb.Face(path.read_bytes()))
        if wght:
            self.hb.set_variations({"wght": wght})

    def shape(self, text: str):
        buf = hb.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        hb.shape(self.hb, buf)
        return [(self.order[i.codepoint], p.x_advance) for i, p in zip(buf.glyph_infos, buf.glyph_positions)]


DISPLAY = {w: None for w in (400, 600, 800, 900)}
MONO = {}


def display(w: int) -> Face:
    if DISPLAY.get(w) is None:
        DISPLAY[w] = Face("HankenGrotesk.ttf", w)
    return DISPLAY[w]


def mono(medium: bool = False) -> Face:
    key = "m" if medium else "r"
    if key not in MONO:
        MONO[key] = Face("DMMono-Medium.ttf" if medium else "DMMono-Regular.ttf")
    return MONO[key]


def width(face: Face, text: str, size: float, track: float = 0) -> float:
    glyphs = face.shape(text)
    adv = sum(a for _, a in glyphs) * size / face.upem
    return adv + track * size * max(len(glyphs) - 1, 0)


def text(face: Face, s: str, x: float, y: float, size: float, fill: str,
         track: float = 0, anchor: str = "start") -> str:
    if anchor == "end":
        x -= width(face, s, size, track)
    k = size / face.upem
    pen = SVGPathPen(face.glyphs, ntos=lambda v: f"{v:.1f}".rstrip("0").rstrip("."))
    cx = x
    for name, adv in face.shape(s):
        face.glyphs[name].draw(TransformPen(pen, (k, 0, 0, -k, cx, y)))
        cx += adv * k + track * size
    d = pen.getCommands()
    return f'<path fill="{fill}" d="{d}"/>' if d else ""


def wrap(face: Face, s: str, size: float, max_w: float, max_lines: int, track: float = 0) -> list[str]:
    words, lines, cur = s.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if width(face, trial, size, track) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and width(face, last + "…", size, track) > max_w:
            last = last.rsplit(" ", 1)[0] if " " in last else last[:-1]
        lines[-1] = last.rstrip(",.;:") + "…"
    return lines


def svg(w: float, h: float, body: str, t: dict, fill: str | None = None) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">'
            f'<rect x=".5" y=".5" width="{w - 1}" height="{h - 1}" rx="12" fill="{fill or t["bg"]}" stroke="{t["line"]}"/>'
            f"{body}</svg>\n")


def label(s: str, x: float, y: float, size: float, fill: str, anchor="start") -> str:
    return text(mono(medium=True), s.upper(), x, y, size, fill, track=0.12, anchor=anchor)


# ---------------------------------------------------------------- cards

def hero(t: dict, mode: str) -> str:
    W, H = 840, 350
    body = text(mono(), USER.upper(), 44, 52, 15, t["fg2"], track=0.12)
    greeting = "GN" if mode == "dark" else "GM"
    body += text(mono(medium=True), greeting, 796, 52, 15, t["acc2"], track=0.12, anchor="end")
    gx = 796 - width(mono(medium=True), greeting, 15, 0.12) - 12
    body += f'<circle cx="{gx:.1f}" cy="47" r="4" fill="{t["acc"]}"/>'
    big = display(900)
    body += text(big, "BEN", 40, 142, 104, t["fg"], track=-0.035)
    body += text(big, "GREENBERG", 40, 232, 104, t["fg"], track=-0.035)
    body += f'<rect x="44" y="256" width="386" height="5" rx="2.5" fill="{t["acc"]}"/>'
    body += text(mono(), "DEVREL & TECHNICAL GTM LEADER", 44, 290, 18, t["fg2"], track=0.2)
    body += text(mono(), "BUILDING TEAMS · DRIVING PRODUCT ADOPTION", 44, 318, 18, t["fg2"], track=0.2)
    return svg(W, H, body, t)


def tiles(t: dict) -> list[str]:
    W, P = 360, 28
    layouts = []
    for tile in TILES:
        titles = wrap(display(800), tile["title"], 34, W - 2 * P, 3, track=-0.01)
        subs = wrap(display(400), tile["sub"], 22, W - 2 * P, 4)
        layouts.append((tile, titles, subs))
    H = max(110 + 38 * (len(ti) - 1) + 22 + 28 * len(su) for _, ti, su in layouts) + P
    out = []
    for tile, titles, subs in layouts:
        body = label(tile["label"], P, 52, 16, t["acc2"])
        y = 110
        for line in titles:
            body += text(display(800), line, P, y, 34, t["fg"], track=-0.01)
            y += 38
        y += 22 - 38 + 28
        for line in subs:
            body += text(display(400), line, P, y, 22, t["fg2"])
            y += 28
        out.append(svg(W, H, body, t))
    return out


def fmt_date(d: datetime) -> str:
    return d.strftime("%b %-d, %Y").upper()


def feed_cards(t: dict, posts: list[dict], video: dict) -> tuple[str, str]:
    W, P = 500, 32
    # writing
    # video
    th_w = W - 2 * P
    th_h = th_w * 9 / 16
    v_lines = wrap(display(600), video["title"], 22, W - 2 * P, 2)
    v_h = 76 + th_h + 40 + 28 * len(v_lines) + P - 8
    H = round(v_h)
    items, used = [], 56
    for p in posts:
        lines = wrap(display(600), p["title"], 22, W - 2 * P, 2)
        if used + 42 + 28 * len(lines) > H - P + 8:
            break
        items.append((fmt_date(datetime.fromisoformat(p["date"])), lines))
        used += 42 + 28 * len(lines)

    body = label("Latest writing", P, 56, 16, t["acc2"])
    y = 56
    for date, lines in items:
        y += 42
        body += text(mono(), date, P, y, 14, t["muted"], track=0.12)
        for line in lines:
            y += 28
            body += text(display(600), line, P, y, 22, t["fg"])
    writing = svg(W, H, body, t)

    body = label("On YouTube", P, 56, 16, t["acc2"])
    ty = 76
    body += (f'<defs><clipPath id="c"><rect x="{P}" y="{ty}" width="{th_w}" height="{th_h:.1f}" rx="10"/></clipPath></defs>'
             f'<rect x="{P}" y="{ty}" width="{th_w}" height="{th_h:.1f}" rx="10" fill="{t["surf"]}"/>')
    if video.get("thumb"):
        body += (f'<image href="data:image/jpeg;base64,{video["thumb"]}" x="{P}" y="{ty}" width="{th_w}" '
                 f'height="{th_h:.1f}" preserveAspectRatio="xMidYMid slice" clip-path="url(#c)"/>')
    cx, cy = P + th_w / 2, ty + th_h / 2
    body += (f'<circle cx="{cx}" cy="{cy:.1f}" r="30" fill="{t["acc"]}"/>'
             f'<path d="M{cx - 9} {cy - 14:.1f} L{cx + 15} {cy:.1f} L{cx - 9} {cy + 14:.1f} Z" fill="#0d1117"/>')
    y = ty + th_h + 40
    body += text(mono(), fmt_date(datetime.fromisoformat(video["date"])), P, y, 14, t["muted"], track=0.12)
    for line in v_lines:
        y += 28
        body += text(display(600), line, P, y, 22, t["fg"])
    return writing, svg(W, H, body, t)


def repo_card(t: dict, r: dict) -> str:
    W, H, P = 500, 200, 32
    m = mono()
    name_size = min(21, (W - 2 * P) / max(width(m, "/ " + r["name"], 1), 1))
    body = text(m, "/", P, 54, name_size, t["acc"])
    body += text(m, r["name"], P + width(m, "/ ", name_size), 54, name_size, t["fg"])
    y = 94
    for line in wrap(display(400), r["description"], 21, W - 2 * P, 2):
        body += text(display(400), line, P, y, 21, t["fg2"])
        y += 28
    lang = r.get("language") or "Code"
    body += f'<circle cx="{P + 6}" cy="{H - 36}" r="6" fill="{LANG_COLORS.get(lang, t["muted"])}"/>'
    body += text(m, lang, P + 20, H - 31, 15, t["muted"])
    pushed = datetime.fromisoformat(r["pushed_at"].replace("Z", "+00:00"))
    body += label("pushed " + pushed.strftime("%b %-d"), W - P, H - 31, 14, t["muted"], anchor="end")
    return svg(W, H, body, t, fill=t["surf"])


# ---------------------------------------------------------------- data

def get(url: str, token: str | None = None, timeout: int = 20):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.read()


def fetch_posts() -> list[dict]:
    root = ET.fromstring(get(RSS))
    posts = []
    for item in root.iter("item"):
        when = email.utils.parsedate_to_datetime(item.findtext("pubDate"))
        posts.append(dict(title=item.findtext("title").strip(), url=item.findtext("link").strip(),
                          date=when.date().isoformat()))
    posts.sort(key=lambda p: p["date"], reverse=True)
    if not posts:
        raise ValueError("empty feed")
    return posts[:6]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def is_regular_video(vid: str) -> bool:
    """Shorts answer 200 at /shorts/ID; regular videos redirect to /watch.
    Anything else (a consent page, an error) counts as not proven regular."""
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(f"https://www.youtube.com/shorts/{vid}", headers={"User-Agent": UA})
    try:
        opener.open(req, timeout=10)
        return False
    except urllib.error.HTTPError as e:
        return e.code in (301, 302, 303, 307) and "/watch" in (e.headers.get("Location") or "")
    except Exception:
        return False


def fetch_video() -> dict:
    ns = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
    root = ET.fromstring(get(f"https://www.youtube.com/feeds/videos.xml?channel_id={YT_CHANNEL}"))
    for entry in root.findall("a:entry", ns):
        vid = entry.findtext("yt:videoId", namespaces=ns)
        if not is_regular_video(vid):
            continue
        thumb = base64.b64encode(get(f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg")).decode()
        return dict(id=vid, title=entry.findtext("a:title", namespaces=ns).strip(),
                    date=entry.findtext("a:published", namespaces=ns)[:10],
                    url=f"https://www.youtube.com/watch?v={vid}", thumb=thumb)
    raise ValueError("no regular video found")


def fetch_repos() -> list[dict]:
    """The /users endpoint lists public repos only, even with a token, and we
    check visibility again, so private work can never reach the profile."""
    token = os.environ.get("GITHUB_TOKEN")
    data = json.loads(get(f"https://api.github.com/users/{USER}/repos?type=owner&sort=pushed&per_page=60", token))
    keep = []
    for r in data:
        if r.get("private") or r.get("visibility", "public") != "public":
            continue
        if r["fork"] or r["archived"] or r.get("is_template") or not r.get("description") or r["name"] in SKIP_REPOS:
            continue
        keep.append({k: r[k] for k in ("name", "description", "language", "pushed_at", "html_url")})
        if len(keep) == 4:
            break
    if len(keep) < 4:
        raise ValueError("fewer than four public repos qualified")
    return keep


# ---------------------------------------------------------------- readme

def picture(name: str, alt: str, width: str, href: str, version: str = "") -> str:
    q = f"?v={version}" if version else ""
    return (f'<a href="{href}"><picture>'
            f'<source media="(prefers-color-scheme: dark)" srcset="assets/{name}-dark.svg{q}">'
            f'<img src="assets/{name}-light.svg{q}" alt="{escape(alt)}" width="{width}"></picture></a>')


def readme(posts: list[dict], video: dict, repos: list[dict], versions: dict) -> str:
    tile_alts = [f'{x["title"]}. {x["sub"]}' for x in TILES]
    tiles_row = "\n".join(picture(f"tile-{i + 1}", tile_alts[i], "32.6%", x["href"], versions[f"tile-{i + 1}"])
                          for i, x in enumerate(TILES))
    latest = "; ".join(p["title"] for p in posts[:3])
    feeds_row = "\n".join([
        picture("writing", f"Latest writing: {latest}", "49.4%", f"{SITE}/blog", versions["writing"]),
        picture("video", f"Latest video: {video['title']}", "49.4%", video["url"], versions["video"]),
    ])
    repo_rows = []
    for i in range(0, 4, 2):
        repo_rows.append("\n".join(
            picture(f"repo-{j + 1}", f'{repos[j]["name"]}: {repos[j]["description"]}', "49.4%",
                    repos[j]["html_url"], versions[f"repo-{j + 1}"]) for j in (i, i + 1)))
    return f"""<!-- Generated by .github/scripts/build_profile.py. Edit the script, not this file. -->

{picture("hero", "Ben Greenberg. DevRel and technical GTM leader: building teams, driving product adoption.", "100%", SITE, versions["hero"])}

I lead developer relations at [Arbitrum](https://arbitrum.io). I wrote [*Vector Search with JavaScript*](https://pragprog.com/titles/bgvector/vector-search-with-javascript/) for The Pragmatic Bookshelf, and I spent a decade in adult education and community organizing that I bring with me to my work in building developer communities.

<p>
{tiles_row}
</p>

<p>
{feeds_row}
</p>

## Building lately

<p>
{repo_rows[0]}
</p>
<p>
{repo_rows[1]}
</p>

<p align="center">
<a href="{SITE}">bengreenberg.dev</a> &nbsp;·&nbsp; <a href="https://x.com/{USER}">X</a> &nbsp;·&nbsp; <a href="https://www.linkedin.com/in/{USER}/">LinkedIn</a> &nbsp;·&nbsp; <a href="{YT_URL}">YouTube</a> &nbsp;·&nbsp; <a href="https://codeplusconduct.substack.com">Substack</a>
</p>
"""


# ---------------------------------------------------------------- main

def main() -> None:
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    for key, fetch in (("posts", fetch_posts), ("video", fetch_video), ("repos", fetch_repos)):
        try:
            state[key] = fetch()
        except Exception as e:  # keep the last good data rather than blanking a card
            print(f"{key}: kept previous data ({e})")
            if key not in state:
                raise SystemExit(f"{key}: no data and no previous state")
    STATE.write_text(json.dumps(state, indent=1) + "\n")

    files: dict[str, dict[str, str]] = {}
    for mode, t in THEMES.items():
        files.setdefault("hero", {})[mode] = hero(t, mode)
        for i, s in enumerate(tiles(t)):
            files.setdefault(f"tile-{i + 1}", {})[mode] = s
        w, v = feed_cards(t, state["posts"], state["video"])
        files.setdefault("writing", {})[mode] = w
        files.setdefault("video", {})[mode] = v
        for i, r in enumerate(state["repos"]):
            files.setdefault(f"repo-{i + 1}", {})[mode] = repo_card(t, r)

    versions = {}
    for name, modes in files.items():
        for mode, content in modes.items():
            (ASSETS / f"{name}-{mode}.svg").write_text(content)
        versions[name] = hashlib.sha1("".join(modes.values()).encode()).hexdigest()[:8]
    (ROOT / "README.md").write_text(readme(state["posts"], state["video"], state["repos"], versions))
    print("built", len(files) * 2, "cards")


if __name__ == "__main__":
    main()
