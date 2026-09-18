#!/usr/bin/env python3
"""
Bake the day's Wire digest on the home server.

This is where the work lives -- the phone is dumb (exactly like the Reader's
desktop converter turning EPUB/PDF into clean .txt). For each of the day's
headlines this fetches the article and extracts the READABLE FULL TEXT, so on
the phone you actually read the piece, not just its blurb. Edit FEEDS to taste.
The brief is tech / retro gaming / interesting new tech concepts; by design
there is NO current-events section.

Extraction quality: best with `pip install trafilatura` (recommended on the
server, like the Reader needing pypdf for PDFs). Falls back to readability-lxml,
then a stdlib <p>-scraper, then the feed summary -- so you always get something.

Output is the WIRE1 line-format the phone parses; up to 10 items, hard-capped.

Run from cron each morning, then serve the folder:
    python3 bake.py -o /srv/wire/wire.txt
    (cd /srv/wire && python3 -m http.server 9009)
The phone's Net.URL must point at http://<this-host>:9009/wire.txt
"""

import argparse
import datetime
import html
import re
import ssl
import sys
import urllib.request
import xml.etree.ElementTree as ET

# --- curation: edit these to taste. Category tag -> list of RSS/Atom feed URLs.
# Tip: feeds that ship the WHOLE article in the feed (WordPress content:encoded,
# e.g. Hackaday, Retro Dodo) need no per-article fetch -- so they can't be
# 403'd, and they bake fast. Prefer those; summary-only feeds fall back to
# fetching + extracting each linked page, which some sites (Cloudflare) block.
# No current-events feeds by design.
FEEDS = {
    "TECH": [
        "https://hackaday.com/feed/",                   # WordPress full-content: hardware/tech
        "https://www.theregister.com/headlines.atom",   # summary-only -> fetched + extracted
    ],
    "RETRO": [
        "https://retrododo.com/feed/",                  # WordPress full-content: retro gaming
    ],
    "CONCEPT": [
        "https://hnrss.org/newest?points=150",          # HN, well-upvoted = "interesting new stuff"
        "https://spectrum.ieee.org/feeds/topic/computing.rss",
    ],
}

MAX_ITEMS = 10           # the sacred cap -- matches Digest.MAX_ITEMS on the phone
PER_CATEGORY = 5         # newest N pulled per category before interleaving
ARTICLE_CHARS = 6000     # per-article cap: a solid long read, keeps RMS happy
TIMEOUT = 20
# Look like a real browser: many sites 403 anything that doesn't.
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
CONTENT_ENCODED = "{http://purl.org/rss/1.0/modules/content/}encoded"

# Set by --insecure to skip TLS verification (e.g. a macOS Python missing root
# certs). Fine for a personal tool pulling public pages; leave off on a real box.
_SSL_CTX = None


def fetch(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=_SSL_CTX) as r:
        return r.read()


def strip_html(s):
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = html.unescape(s)
    return re.sub(r"[ \t]+", " ", s).strip()


def domain(url):
    m = re.match(r"https?://([^/]+)", url or "")
    return m.group(1).replace("www.", "") if m else ""


# ---------------------------------------------------------------- extraction

def crude_extract(html_str):
    """Paragraph-aware stdlib fallback: keep <p> text, drop chrome, blank line
    between paragraphs so the phone's word-wrap reads naturally."""
    h = re.sub(r"(?is)<(script|style|nav|header|footer|aside|form|figure)[^>]*>.*?</\1>", " ", html_str)
    paras = re.findall(r"(?is)<(?:p|h[1-6]|li)[^>]*>(.*?)</(?:p|h[1-6]|li)>", h)
    out = []
    for p in paras:
        t = strip_html(p)
        if len(t) >= 40:                    # skip tiny nav/label fragments
            out.append(t)
    return "\n\n".join(out)


def extract(html_str, url):
    """Extract readable text from already-downloaded HTML; '' if all fail."""
    try:
        import trafilatura
        t = trafilatura.extract(html_str, url=url, include_comments=False,
                                include_tables=False, favor_precision=True)
        if t:
            return t
    except Exception:
        pass
    try:
        from readability import Document
        return crude_extract(Document(html_str).summary())
    except Exception:
        pass
    return crude_extract(html_str)


def fetch_and_extract(link):
    """Download + extract one article. Prefer trafilatura's own downloader (it
    negotiates many sites that 403 a plain urllib request), then fall back to
    our browser-headed fetch."""
    try:
        import trafilatura
        dl = trafilatura.fetch_url(link)
        if dl:
            t = trafilatura.extract(dl, url=link, include_comments=False,
                                    include_tables=False, favor_precision=True)
            if t:
                return t
    except Exception:
        pass
    try:
        raw = fetch(link)
        return extract(raw.decode("utf-8", "replace"), link)
    except Exception as e:
        print(f"  ! fetch {link}: {e}", file=sys.stderr)
        return ""


def article_text(summary, link, full_html):
    """Full readable text for one item, with fallbacks; plus the source domain."""
    src = domain(link)
    text = ""
    if full_html and len(full_html) > 800:          # feed already carried full content
        text = extract(full_html, link)             # clean it (trafilatura if available)
    if (not text or len(text) < 300) and link:      # otherwise download + extract
        got = fetch_and_extract(link)
        if got and len(got) > len(text):
            text = got
    if not text or len(text) < 200:                 # last resort: the blurb
        text = strip_html(summary)
        if link:
            text += "\n\n(full text unavailable — summary only)"
    text = re.sub(r"\n[ \t]*\n(\s*\n)+", "\n\n", text).strip()   # collapse blank-line runs
    if len(text) > ARTICLE_CHARS:
        text = text[:ARTICLE_CHARS].rstrip() + "…"
    return text, src


# ---------------------------------------------------------------- feeds

def parse_feed(data):
    """[(title, summary, link, full_html)] from an RSS or Atom feed, in order."""
    items = []
    root = ET.fromstring(data)
    rss = root.findall(".//item")
    if rss:
        for it in rss:
            title = strip_html(it.findtext("title") or "")
            link = (it.findtext("link") or "").strip()
            summary = it.findtext("description") or ""
            full = it.findtext(CONTENT_ENCODED) or ""    # content:encoded (often full article)
            items.append((title, summary, link, full))
        return items
    ns = {"a": "http://www.w3.org/2005/Atom"}
    for e in root.findall(".//a:entry", ns):
        title = strip_html(e.findtext("a:title", default="", namespaces=ns) or "")
        summary = e.findtext("a:summary", default="", namespaces=ns) or ""
        content = e.findtext("a:content", default="", namespaces=ns) or ""
        link = ""
        for l in e.findall("a:link", ns):
            if l.get("rel", "alternate") == "alternate" and l.get("href"):
                link = l.get("href"); break
        if not link:
            l = e.find("a:link", ns)
            if l is not None:
                link = l.get("href", "")
        items.append((title, summary or content, link, content))
    return items


def collect():
    """Per category, the newest PER_CATEGORY items, deduped by title."""
    buckets = {}
    for tag, urls in FEEDS.items():
        got = []
        for u in urls:
            try:
                got.extend(parse_feed(fetch(u)))
            except Exception as e:
                print(f"  ! {tag}: {u}: {e}", file=sys.stderr)
        seen, uniq = set(), []
        for title, summary, link, full in got:
            key = title.lower()
            if title and key not in seen:
                seen.add(key)
                uniq.append((title, summary, link, full))
        buckets[tag] = uniq[:PER_CATEGORY]
    return buckets


def interleave(buckets):
    """Round-robin across categories so the ten aren't all one topic."""
    order = list(buckets.keys())
    out, i = [], 0
    while len(out) < MAX_ITEMS and any(buckets[t] for t in order):
        tag = order[i % len(order)]
        if buckets[tag]:
            title, summary, link, full = buckets[tag].pop(0)
            out.append((tag, title, summary, link, full))
        i += 1
    return out[:MAX_ITEMS]


# ---------------------------------------------------------------- render

def esc_body(text):
    """Body text is arbitrary (full articles): make sure no line starts with '#'
    (the item delimiter) and strip stray control chars."""
    lines = []
    for ln in text.split("\n"):
        ln = ln.replace("", "")
        if ln.startswith("#"):
            ln = " " + ln
        lines.append(ln.rstrip())
    return "\n".join(lines)


def render(items):
    today = datetime.date.today().strftime("%a %d %b %Y")
    out = ["WIRE1", today]
    for tag, title, summary, link, full in items:
        text, src = article_text(summary, link, full)
        out.append(f"# [{tag}] {title}")
        body = esc_body(text).strip("\n")
        if src:
            body += f"\n\n— {src}"
        out.append(body)
        print(f"   [{tag}] {title[:60]}  ({len(text)} chars from {src or 'summary'})")
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default="wire.txt", help="output file (default wire.txt)")
    ap.add_argument("--insecure", action="store_true",
                    help="skip TLS verification (macOS Python missing root certs)")
    args = ap.parse_args()
    if args.insecure:
        global _SSL_CTX
        _SSL_CTX = ssl._create_unverified_context()
    text = render(interleave(collect()))
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(text)
    print(f">> wrote {args.out}: {len(text.encode('utf-8'))} bytes")


if __name__ == "__main__":
    main()
