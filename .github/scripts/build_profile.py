# /// script
# requires-python = ">=3.11"
# dependencies = ["fonttools>=4.50", "uharfbuzz>=0.39"]
# ///
"""Builds the profile README and every card it shows, in dark and light.

GitHub strips CSS and scripts from READMEs, so each designed element is an
SVG in assets/. Text is shaped with HarfBuzz and drawn as outlines, so the
cards look the same everywhere without loading a web font.

Live data comes from four public sources: the blog RSS feed, the talks feed,
the YouTube Data API, and merged pull requests to other people's repos (GitHub GraphQL
search). Lists are native Markdown so they read on phones and to screen
readers; the hero and the contribution cards are SVG. If a source fails, the last good data
in assets/state.json is reused so a hiccup never blanks a card, and the
script exits 3 after building so the workflow run goes red.

Run locally:  YOUTUBE_API_KEY=... uv run .github/scripts/build_profile.py
"""
from __future__ import annotations

import email.utils
import hashlib
import json
import os
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

# Upstream contributions are merged PRs to projects other people maintain: the
# day job's org, my own orgs and bounty boards are left out.
NOT_UPSTREAM = {USER, "ArbitrumFoundation", "OffchainLabs", "EdgeFinder-Apps", "mergeos-bounties"}
CONTRIBUTIONS = 3
CARD_W = 640

THEMES = {
    # Neutrals are GitHub's own Primer page colors, so the cards sit flush on the profile.
    "dark": dict(bg="#0d1117", surf="#151b23", line="#3d444d", fg="#f0f6fc",
                 fg2="#9198a1", muted="#9198a1", acc="#ff9f45", acc2="#ffa85c"),
    "light": dict(bg="#ffffff", surf="#f6f8fa", line="#d1d9e0", fg="#1f2328",
                  fg2="#59636e", muted="#59636e", acc="#e85d04", acc2="#b23a00"),
}

TALKS_FEED = f"{SITE}/talks.json"
NEWSLETTER = "https://codeplusconduct.substack.com"


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


HERO_LINES = ("DEVELOPER RELATIONS · AUTHOR · AAIF AMBASSADOR", "TECHNOLOGY, ETHICS, AND THE PEOPLE BETWEEN THEM")


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
    body += text(mono(), HERO_LINES[0], 44, 292, 22, t["fg2"], track=0.08)
    body += text(mono(), HERO_LINES[1], 44, 324, 22, t["fg2"], track=0.08)
    return svg(W, H, body, t)


def fmt_date(d: datetime) -> str:
    return d.strftime("%b %-d, %Y").upper()


def contribution_card(t: dict, c: dict) -> str:
    """Shown at its own size (CARD_W), not stretched to the column, and shrunk to fit on a phone."""
    W, P = CARD_W, 24
    m = mono()
    titles = wrap(display(600), c["title"], 22, W - 2 * P, 2)
    H = 76 + 28 * len(titles) + 26
    body = text(m, "/", P, 42, 16, t["acc"])
    body += text(m, c["repo"], P + width(m, "/ ", 16), 42, 16, t["fg"])
    merged = datetime.fromisoformat(c["merged_at"].replace("Z", "+00:00"))
    body += label("merged " + fmt_date(merged), W - P, 42, 13, t["acc2"], anchor="end")
    y = 78
    for line in titles:
        body += text(display(600), line, P, y, 22, t["fg"])
        y += 28
    body += text(m, f"PR #{c['number']}", P, y + 4, 14, t["muted"])
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


def fetch_video() -> dict:
    """YouTube retired the channel RSS feeds (404 since October 2026) and puts
    a captcha in front of scraped pages, so this reads the Data API. The UULF
    playlist is the channel's long-form uploads: Shorts and private API
    uploads are already left out."""
    key = os.environ.get("YOUTUBE_API_KEY")
    if not key:
        raise ValueError("YOUTUBE_API_KEY is not set")
    playlist = "UULF" + YT_CHANNEL[2:]
    data = json.loads(get("https://www.googleapis.com/youtube/v3/playlistItems?part=snippet,contentDetails"
                          f"&maxResults=1&playlistId={playlist}&key={key}"))
    if not data.get("items"):
        raise ValueError("no regular video found")
    item = data["items"][0]
    vid = item["contentDetails"]["videoId"]
    return dict(id=vid, title=item["snippet"]["title"].strip(),
                date=item["contentDetails"]["videoPublishedAt"][:10],
                url=f"https://www.youtube.com/watch?v={vid}")


def fetch_talks() -> list[dict]:
    talks = json.loads(get(TALKS_FEED))["talks"]
    if not talks:
        raise ValueError("empty talks feed")
    return [{k: t[k] for k in ("title", "event", "date", "link")} for t in talks[:3]]


CONTRIB_QUERY = """query($q: String!, $after: String) {
  search(query: $q, type: ISSUE, first: 50, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes { ... on PullRequest { number title url mergedAt author { login }
      repository { nameWithOwner isPrivate isFork owner { login } } } }
  }
}"""


def upstream(repo: dict) -> bool:
    owner = (repo.get("owner") or {}).get("login", "")
    return (owner not in NOT_UPSTREAM and not repo.get("isPrivate") and not repo.get("isFork")
            and "bount" not in repo.get("nameWithOwner", "").lower())


def fetch_contributions() -> list[dict]:
    """Merged PRs I authored in public repos other people maintain, newest merge
    first. Search returns them in its own order, so every page is read and
    sorted locally."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ValueError("GITHUB_TOKEN is not set")
    q = f"is:pr is:merged is:public author:{USER} " + " ".join(f"-user:{o}" for o in sorted(NOT_UPSTREAM))
    found, after = {}, None
    for _ in range(10):
        req = urllib.request.Request("https://api.github.com/graphql", method="POST",
                                     data=json.dumps({"query": CONTRIB_QUERY, "variables": {"q": q, "after": after}}).encode(),
                                     headers={"User-Agent": UA, "Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=20) as res:
            data = json.loads(res.read())
        if data.get("errors"):
            raise ValueError(data["errors"][0].get("message", "GraphQL error"))
        search = data["data"]["search"]
        for n in search["nodes"]:
            if (n.get("mergedAt") and (n.get("author") or {}).get("login") == USER
                    and upstream(n.get("repository") or {}) and n.get("url", "").startswith("https://github.com/")):
                found[n["url"]] = dict(repo=n["repository"]["nameWithOwner"], number=n["number"],
                                       title=n["title"].strip(), url=n["url"], merged_at=n["mergedAt"])
        if not search["pageInfo"]["hasNextPage"]:
            break
        after = search["pageInfo"]["endCursor"]
    return sorted(found.values(), key=lambda c: (c["merged_at"], c["url"]), reverse=True)[:CONTRIBUTIONS]


# ---------------------------------------------------------------- readme

def picture(name: str, alt: str, width: str, href: str, version: str = "") -> str:
    q = f"?v={version}" if version else ""
    return (f'<a href="{href}"><picture>'
            f'<source media="(prefers-color-scheme: dark)" srcset="assets/{name}-dark.svg{q}">'
            f'<img src="assets/{name}-light.svg{q}" alt="{escape(alt)}" width="{width}"></picture></a>')


# Invisible images that count profile views on bengreenberg.dev. GitHub's
# image proxy hides who is looking, so each <picture> lets the browser report
# on itself: it loads the first source whose media query matches, which names
# its screen width, touch or mouse, and pixel density. view.gif is fetched on
# every view, viewer.gif and theme.gif once per browser per day; theme.gif
# follows the viewer's GitHub theme, which GitHub applies to
# prefers-color-scheme sources itself. Inside an existing link so GitHub does
# not wrap each one in a link of its own.
SCREENS = [("phone", "(max-width: 543.98px)"), ("tablet", "(max-width: 1011.98px)"),
           ("laptop", "(max-width: 1439.98px)"), ("large", "")]
INPUTS = [("touch", "(pointer: coarse)"), ("mouse", "")]
DENSITIES = [("2x", "(min-resolution: 2dppx)"), ("1x", "")]


def pixel(kind: str) -> str:
    """Every combination in order, most specific first; the last one is the fallback <img>."""
    combos = [(f"s={s}&amp;i={i}&amp;r={r}", " and ".join(q for q in (sq, iq, rq) if q))
              for s, sq in SCREENS for i, iq in INPUTS for r, rq in DENSITIES]
    *sources, (fallback, _) = combos
    return ("<picture>"
            + "".join(f'<source media="{m}" srcset="{SITE}/api/gh/{kind}.gif?{q}">' for q, m in sources)
            + f'<img src="{SITE}/api/gh/{kind}.gif?{fallback}" width="0" height="0" alt=""></picture>')


PIXELS = (pixel("view") + pixel("viewer")
          + f'<picture><source media="(prefers-color-scheme: dark)" srcset="{SITE}/api/gh/theme.gif?t=dark">'
          f'<img src="{SITE}/api/gh/theme.gif?t=light" width="0" height="0" alt=""></picture>')
# Links to the site carry this tag, so a click through is credited to the
# profile in the site's analytics; the site removes it from the address bar.
TO_SITE = f"{SITE}/?ref=github-profile"


def nice(iso: str, day: bool = True) -> str:
    d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return d.strftime("%b %-d, %Y") if day else d.strftime("%B %Y")


def readme(posts: list[dict], talks: list[dict], video: dict, contributions: list[dict], versions: dict) -> str:
    hero_alt = f"Ben Greenberg. {HERO_LINES[0].title()}. {HERO_LINES[1].capitalize()}."
    nav = " &nbsp;·&nbsp; ".join([
        f'<a href="{TO_SITE}">{PIXELS}bengreenberg.dev</a>',
        f'<a href="{NEWSLETTER}">Newsletter</a>',
        f'<a href="{YT_URL}">YouTube</a>',
        f'<a href="{SITE}/talks?ref=github-profile">Talks</a>',
        f'<a href="https://x.com/{USER}">X</a>',
        f'<a href="https://www.linkedin.com/in/{USER}/">LinkedIn</a>',
    ])
    upstream_section = ""
    if contributions:
        cards = "\n".join(
            "<p>" + picture(f"contrib-{i + 1}", f'Merged pull request to {c["repo"]}: {c["title"]}. Pull request '
                            f'#{c["number"]}, merged {nice(c["merged_at"])}.', str(CARD_W), c["url"],
                            versions[f"contrib-{i + 1}"]) + "</p>"
            for i, c in enumerate(contributions))
        everything = (f"https://github.com/pulls?q=is%3Apr+is%3Amerged+author%3A{USER}+"
                      + "+".join(f"-user%3A{o}" for o in sorted(NOT_UPSTREAM)))
        upstream_section = f"""## Open source

Recent open source work.

{cards}

[All my merged upstream pull requests]({everything})

"""
    writing = "\n".join(f'- [{escape(p["title"])}]({p["url"]}) · {nice(p["date"])}' for p in posts[:3])
    talk_list = "\n".join(f'- [{escape(x["title"])}]({x["link"]}) · {x["event"]}, {nice(x["date"], day=False)}' for x in talks)
    return f"""<!-- Generated by .github/scripts/build_profile.py. Edit the script, not this file. -->

{picture("hero", hero_alt, "100%", TO_SITE, versions["hero"])}

<p align="center">
{nav}
</p>

I lead developer relations at [Arbitrum](https://arbitrum.io) and I am an [AAIF](https://aaif.io) Ambassador. I wrote [*Vector Search with JavaScript*](https://pragprog.com/titles/bgvector/vector-search-with-javascript/) for The Pragmatic Bookshelf, and I spent a decade in adult education and community organizing that I bring with me to my work in building developer communities.

I write [Code and Conduct]({NEWSLETTER}), a newsletter about technology, ethics, and the people between them. **[Subscribe here]({NEWSLETTER}/subscribe).**

{upstream_section}## Latest writing

{writing}

[All writing]({SITE}/blog?ref=github-profile)

## Latest video

<a href="{video["url"]}"><img src="https://i.ytimg.com/vi/{video["id"]}/mqdefault.jpg" alt="Watch on YouTube: {escape(video["title"])}" width="320"></a>

[{escape(video["title"])}]({video["url"]}) · {nice(video["date"])}

## Recent talks

{talk_list}

[All talks]({SITE}/talks?ref=github-profile)
"""


# ---------------------------------------------------------------- main

def main() -> None:
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    stale = []
    for key, fetch in (("posts", fetch_posts), ("talks", fetch_talks), ("video", fetch_video),
                       ("contributions", fetch_contributions)):
        try:
            state[key] = fetch()
        except Exception as e:  # keep the last good data rather than blanking a card
            print(f"{key}: kept previous data ({e})")
            stale.append(key)
            if key not in state:
                raise SystemExit(f"{key}: no data and no previous state")
    STATE.write_text(json.dumps(state, indent=1) + "\n")

    files: dict[str, dict[str, str]] = {}
    for mode, t in THEMES.items():
        files.setdefault("hero", {})[mode] = hero(t, mode)
        for i, c in enumerate(state["contributions"]):
            files.setdefault(f"contrib-{i + 1}", {})[mode] = contribution_card(t, c)

    versions = {}
    for name, modes in files.items():
        for mode, content in modes.items():
            (ASSETS / f"{name}-{mode}.svg").write_text(content)
        versions[name] = hashlib.sha1("".join(modes.values()).encode()).hexdigest()[:8]
    (ROOT / "README.md").write_text(readme(state["posts"], state["talks"], state["video"], state["contributions"], versions))
    print("built", len(files) * 2, "cards")
    if stale:
        raise SystemExit(3)


if __name__ == "__main__":
    main()
