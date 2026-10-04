#!/usr/bin/env python3
"""
Bake the day's Wire digest on the home server.

This is where the work lives -- the phone is dumb (exactly like the Reader's
desktop converter turning EPUB/PDF into clean .txt). For each shipped headline
this fetches the article and extracts the READABLE FULL TEXT, so on the phone
you read the piece, not its blurb.

What picks the ten: a local decision model. Candidates are scored by `laya:en`
via Ollaya (see scorer.py) on taste, topic, hype and depth; the best that clear
a threshold ship. No cloud, no keys. If Ollaya is down or you pass --no-model
it falls back to the old newest-first curation. The brief is tech / retro
gaming / interesting new tech concepts; by design there is NO current-events
section, and the classifier rejects news/politics that leaks in via aggregators.

Extraction quality: best with `pip install trafilatura`. Falls back to
readability-lxml, then a stdlib <p>-scraper, then the feed summary.

Output is the WIRE1 line-format the phone parses; up to 10 items, hard-capped.

Everyday use:
    bake.py -o /srv/wire/wire.txt         # the cron bake
    bake.py --like 3 7                     # teach it (item numbers from last run)
    bake.py --dislike 5
    bake.py --eval                         # how well is it ranking your labels?
    bake.py --dry-run                      # ranked table, writes nothing
    bake.py --no-model                     # force the legacy path
See the README "Taste ranking" section for the full setup.
"""

import argparse
import datetime
import html
import json
import os
import re
import ssl
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter

import scorer
from scorer import (CATEGORIES, MIN_SCORE_DEFAULT, OllayaClient, ScoringAborted,
                    Cache, build_questions, interests_hash, questions_hash,
                    score_candidates)

# --- curation: feeds as dicts. "weight" nudges the final score within +/-1
# (scorer.WEIGHT_CLAMP). "full_content" just records whether the feed carries
# the whole article (the extractor auto-detects anyway). Validated 2026-10-04:
# each one fetches, parses, has recent dated items, and extracts >=300 chars.
# No current-events feeds by design; news that leaks in is rejected by topic.
FEEDS = [
    # ---- TECH ----
    {"tag": "TECH", "name": "Hackaday",        "url": "https://hackaday.com/feed/",                       "full_content": True,  "weight": 0.3},
    {"tag": "TECH", "name": "The Register",    "url": "https://www.theregister.com/headlines.atom",       "full_content": True,  "weight": -0.4},
    {"tag": "TECH", "name": "Ars Technica",    "url": "https://feeds.arstechnica.com/arstechnica/technology-lab", "full_content": True, "weight": -0.1},
    {"tag": "TECH", "name": "Ars Gadgets",     "url": "https://feeds.arstechnica.com/arstechnica/gadgets", "full_content": True,  "weight": -0.2},
    {"tag": "TECH", "name": "Adafruit",        "url": "https://blog.adafruit.com/feed/",                  "full_content": True,  "weight": 0.2},
    {"tag": "TECH", "name": "Raspberry Pi",    "url": "https://www.raspberrypi.com/news/feed/",           "full_content": True,  "weight": 0.2},
    {"tag": "TECH", "name": "Phoronix",        "url": "https://www.phoronix.com/rss.php",                 "full_content": False, "weight": 0.0},
    {"tag": "TECH", "name": "Lobsters",        "url": "https://lobste.rs/rss",                            "full_content": False, "weight": 0.1},
    {"tag": "TECH", "name": "Ken Shirriff",    "url": "https://www.righto.com/feeds/posts/default?alt=rss", "full_content": False, "weight": 0.5},
    {"tag": "TECH", "name": "Simon Willison",  "url": "https://simonwillison.net/atom/everything/",       "full_content": False, "weight": 0.3},
    {"tag": "TECH", "name": "LWN",             "url": "https://lwn.net/headlines/newrss",                 "full_content": False, "weight": 0.3},
    {"tag": "TECH", "name": "HN Best",         "url": "https://hnrss.org/best",                           "full_content": False, "weight": -0.2},
    {"tag": "TECH", "name": "HN 150pts",       "url": "https://hnrss.org/newest?points=150",              "full_content": False, "weight": -0.2},
    {"tag": "TECH", "name": "Jeff Geerling",   "url": "https://www.jeffgeerling.com/blog.xml",            "full_content": False, "weight": 0.4},
    # ---- RETRO ----
    {"tag": "RETRO", "name": "Retro Dodo",     "url": "https://retrododo.com/feed/",                      "full_content": True,  "weight": 0.0},
    {"tag": "RETRO", "name": "Time Extension", "url": "https://www.timeextension.com/feeds/latest",       "full_content": False, "weight": 0.0},
    {"tag": "RETRO", "name": "Hardcore Gaming 101", "url": "http://www.hardcoregaming101.net/feed/",      "full_content": False, "weight": 0.2},
    {"tag": "RETRO", "name": "Digital Antiquarian", "url": "https://www.filfre.net/feed/",                "full_content": True,  "weight": 0.5},
    {"tag": "RETRO", "name": "VG History Foundation", "url": "https://gamehistory.org/feed/",             "full_content": True,  "weight": 0.4},
    {"tag": "RETRO", "name": "Hackaday Retro", "url": "https://hackaday.com/tag/retrocomputing/feed/",    "full_content": True,  "weight": 0.3},
    {"tag": "RETRO", "name": "The 8-Bit Guy",  "url": "https://www.the8bitguy.com/feed/",                 "full_content": True,  "weight": 0.2},
    {"tag": "RETRO", "name": "Nicole Express",  "url": "https://nicole.express/feed.xml",                 "full_content": True,  "weight": 0.5},
    {"tag": "RETRO", "name": "Big Mess o' Wires", "url": "https://www.bigmessowires.com/feed/",           "full_content": True,  "weight": 0.5},
    {"tag": "RETRO", "name": "Old Vintage Computing", "url": "https://oldvcr.blogspot.com/feeds/posts/default?alt=rss", "full_content": True, "weight": 0.5},
    # ---- CONCEPT ----
    {"tag": "CONCEPT", "name": "IEEE Spectrum", "url": "https://spectrum.ieee.org/feeds/topic/computing.rss", "full_content": False, "weight": 0.1},
    {"tag": "CONCEPT", "name": "IEEE Semiconductors", "url": "https://spectrum.ieee.org/feeds/topic/semiconductors.rss", "full_content": False, "weight": 0.1},
    {"tag": "CONCEPT", "name": "Quanta",       "url": "https://www.quantamagazine.org/feed/",             "full_content": False, "weight": 0.2},
    {"tag": "CONCEPT", "name": "Works in Progress", "url": "https://www.worksinprogress.news/feed",       "full_content": True,  "weight": 0.3},
    {"tag": "CONCEPT", "name": "Construction Physics", "url": "https://www.construction-physics.com/feed", "full_content": True,  "weight": 0.3},
    {"tag": "CONCEPT", "name": "MIT News Engineering", "url": "https://news.mit.edu/rss/topic/engineering", "full_content": True, "weight": 0.1},
    {"tag": "CONCEPT", "name": "Computer History Museum", "url": "https://computerhistory.org/feed/",     "full_content": True,  "weight": 0.3},
]

MAX_ITEMS = 10               # the sacred cap -- matches Digest.MAX_ITEMS on the phone
POOL_PER_FEED = int(os.environ.get("WIRE_POOL_PER_FEED", "8"))    # newest N per feed before scoring
# Hard ceiling on how many candidates get scored, fairly interleaved across
# feeds. laya:en is ~20s/candidate on a Pi 4B CPU, so this bounds the cold run;
# the cache makes later runs cheap (only new items score). Lower it if the first
# bake is too slow (WIRE_MAX_CANDIDATES=80 ~= 25 min cold).
MAX_CANDIDATES = int(os.environ.get("WIRE_MAX_CANDIDATES", "120"))
ARTICLE_CHARS = 6000         # per-article cap: a solid long read, keeps RMS happy
MAX_PER_DOMAIN = 2           # at most this many shipped items from one source domain
CAT_TARGET = {"TECH": 4, "CONCEPT": 3, "RETRO": 3}   # soft balance (scores still win)
SEEN_DAYS = int(os.environ.get("WIRE_SEEN_DAYS", "14"))   # never reship within this window
HARD_CEILING_SECONDS = 10800  # 3h: the only deadline -- a stuck run must not block tomorrow
HOST_DELAY = 1.0             # polite gap between requests to the same host, seconds
TIMEOUT = 20

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(HERE, ".wirecache.db")
LOCK_PATH = os.path.join(HERE, ".bake.lock")
LOG_PATH = os.path.join(HERE, "bake.log")
LAST_BAKE_PATH = os.path.join(HERE, "last_bake.json")
LABELS_PATH = os.path.join(HERE, "labels.tsv")
INTERESTS_PATH = os.path.join(HERE, "interests.md")
LOG_KEEP = 14
LOG_DELIM = "\n----8<---- bake ----8<----\n"

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
CONTENT_ENCODED = "{http://purl.org/rss/1.0/modules/content/}encoded"

_SSL_CTX = None   # set by --insecure


class BakeError(Exception):
    pass


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
        if len(t) >= 40:
            out.append(t)
    return "\n\n".join(out)


def extract(html_str, url):
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
        print("  ! fetch %s: %s" % (link, e), file=sys.stderr)
        return ""


def article_text(summary, link, full_html):
    """Full readable text for one item, with fallbacks. Returns
    (text, source_domain, full_ok) where full_ok is False when we had to fall
    back to the feed blurb."""
    src = domain(link)
    text = ""
    if full_html and len(full_html) > 800:
        text = extract(full_html, link)
    if (not text or len(text) < 300) and link:
        got = fetch_and_extract(link)
        if got and len(got) > len(text):
            text = got
    full_ok = bool(text and len(text) >= 200)
    if not full_ok:
        text = strip_html(summary)
        if link:
            text += "\n\n(full text unavailable — summary only)"
    text = re.sub(r"\n[ \t]*\n(\s*\n)+", "\n\n", text).strip()
    if len(text) > ARTICLE_CHARS:
        text = text[:ARTICLE_CHARS].rstrip() + "…"
    return text, src, full_ok


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
            full = it.findtext(CONTENT_ENCODED) or ""
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


def norm_title(t):
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def norm_url(u):
    u = (u or "").strip()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www\.", "", u)
    u = u.split("#", 1)[0].split("?", 1)[0]
    return u.rstrip("/")


def _host_delay(last_host, url):
    h = domain(url)
    now = time.time()
    if h in last_host:
        wait = HOST_DELAY - (now - last_host[h])
        if wait > 0:
            time.sleep(wait)
    last_host[h] = time.time()


def collect(feeds, cache, drop_seen=True, errors=None):
    """Up to POOL_PER_FEED newest per feed as candidate dicts; deduped by
    normalized title and URL; SEEN urls dropped. Per-feed failures are isolated
    and logged once each. Sequential, one-second gap per host."""
    cands = []
    last_host = {}
    for f in feeds:
        _host_delay(last_host, f["url"])
        try:
            items = parse_feed(fetch(f["url"]))
        except Exception as e:
            msg = "feed %s: %s" % (f["name"], e)
            print("  ! " + msg, file=sys.stderr)
            if errors is not None:
                errors.append(msg)
            continue
        taken = 0
        for (title, summary, link, full) in items:
            if not title:
                continue
            cands.append({
                "tag": f["tag"], "title": title, "summary": strip_html(summary),
                "link": link, "full_html": full,
                "source": domain(link) or domain(f["url"]),
                "feed_name": f["name"], "weight": f.get("weight", 0.0),
                "full_content": f.get("full_content", False),
            })
            taken += 1
            if taken >= POOL_PER_FEED:
                break
    seen_t, seen_u, uniq = set(), set(), []
    for c in cands:
        nt, nu = norm_title(c["title"]), norm_url(c["link"])
        if (nt and nt in seen_t) or (nu and nu in seen_u):
            continue
        if nt:
            seen_t.add(nt)
        if nu:
            seen_u.add(nu)
        uniq.append(c)
    out = []
    for c in uniq:
        if drop_seen and c["link"] and cache.is_seen(c["link"], SEEN_DAYS):
            continue
        out.append(c)
    return _interleave_cap(out, MAX_CANDIDATES)


def _interleave_cap(cands, cap):
    """Bound the candidate pool to `cap`, round-robin across feeds so the cut is
    fair (one busy feed can't crowd out the quiet blogs). Order within a feed is
    preserved (newest first). Under the cap, returns the list unchanged."""
    if cap is None or len(cands) <= cap:
        return cands
    groups = {}
    order = []
    for c in cands:
        if c["feed_name"] not in groups:
            groups[c["feed_name"]] = []
            order.append(c["feed_name"])
        groups[c["feed_name"]].append(c)
    out = []
    while len(out) < cap:
        progressed = False
        for name in order:
            lst = groups[name]
            if lst:
                out.append(lst.pop(0))
                progressed = True
                if len(out) >= cap:
                    break
        if not progressed:
            break
    return out


# ---------------------------------------------------------------- selection

def select(scored, min_score):
    """Pick <=MAX_ITEMS by score, honouring: >= min_score, not OFFTOPIC-rejected,
    max MAX_PER_DOMAIN per source, min 1 per category (if any clears), soft
    per-category targets that scores can override to fill the ten."""
    elig = [c for c in scored
            if c.get("scored") and not c.get("reject") and c.get("final", 0) >= min_score]
    elig.sort(key=lambda c: c.get("final", 0), reverse=True)
    chosen, chosen_ids = [], set()
    dom, cat = Counter(), Counter()

    def can(c, cap=None):
        if len(chosen) >= MAX_ITEMS or id(c) in chosen_ids:
            return False
        if dom[c["source"]] >= MAX_PER_DOMAIN:
            return False
        if cap is not None and cat[c["tag"]] >= cap:
            return False
        return True

    def take(c):
        chosen.append(c); chosen_ids.add(id(c))
        dom[c["source"]] += 1; cat[c["tag"]] += 1

    for k in CATEGORIES:                       # 1. guarantee at least one per category
        if cat[k] == 0:
            for c in elig:
                if c["tag"] == k and can(c):
                    take(c); break
    for c in elig:                             # 2. greedy within soft targets
        if can(c, CAT_TARGET.get(c["tag"])):
            take(c)
    for c in elig:                             # 3. relax targets; scores win
        if can(c):
            take(c)
    return chosen


def extract_winners(winners, elig):
    """Download + extract full text for winners only. If a winner can only yield
    its summary, try the next-ranked still-unused item in the same category
    first (respecting the per-domain cap). Sets c['text'] and c['src']."""
    chosen_ids = {id(c) for c in winners}
    final, dom = [], Counter()
    for w in winners:
        text, src, ok = article_text(w.get("summary", ""), w.get("link", ""), w.get("full_html", ""))
        if ok and dom[w["source"]] < MAX_PER_DOMAIN:
            w["text"], w["src"] = text, src or w.get("source", "")
            final.append(w); dom[w["source"]] += 1
            continue
        replaced = False
        for b in elig:
            if id(b) in chosen_ids or b["tag"] != w["tag"] or dom[b["source"]] >= MAX_PER_DOMAIN:
                continue
            t2, s2, ok2 = article_text(b.get("summary", ""), b.get("link", ""), b.get("full_html", ""))
            if ok2:
                b["text"], b["src"] = t2, s2 or b.get("source", "")
                chosen_ids.add(id(b)); final.append(b); dom[b["source"]] += 1
                replaced = True
                break
        if not replaced and dom[w["source"]] < MAX_PER_DOMAIN:
            w["text"], w["src"] = text, src or w.get("source", "")   # ship summary-only
            final.append(w); dom[w["source"]] += 1
    return final


def legacy_select(cands):
    """The pre-model curation: newest-first, round-robin across categories, with
    SEEN already dropped by collect() and the per-domain cap applied here."""
    buckets = {k: [] for k in CATEGORIES}
    for c in cands:
        if c["tag"] in buckets:
            buckets[c["tag"]].append(c)
    order, out, dom, i = list(CATEGORIES), [], Counter(), 0
    while len(out) < MAX_ITEMS and any(buckets[k] for k in order):
        k = order[i % len(order)]; i += 1
        if not buckets[k]:
            continue
        c = buckets[k].pop(0)
        if dom[c["source"]] >= MAX_PER_DOMAIN:
            continue
        out.append(c); dom[c["source"]] += 1
    for c in out:
        text, src, _ = article_text(c.get("summary", ""), c.get("link", ""), c.get("full_html", ""))
        c["text"], c["src"] = text, src or c.get("source", "")
    return out


# ---------------------------------------------------------------- render + write

def esc_body(text):
    """Body text is arbitrary (full articles): make sure no line starts with '#'
    (the item delimiter) and strip stray control chars."""
    lines = []
    for ln in text.split("\n"):
        ln = ln.replace("\x00", "")
        if ln.startswith("#"):
            ln = " " + ln
        lines.append(ln.rstrip())
    return "\n".join(lines)


def render(items):
    today = datetime.date.today().strftime("%a %d %b %Y")
    out = ["WIRE1", today]
    for c in items:
        out.append("# [%s] %s" % (c["tag"], c["title"]))
        body = esc_body(c["text"]).strip("\n")
        if c.get("src"):
            body += "\n\n— %s" % c["src"]
        out.append(body)
    return "\n".join(out) + "\n"


def write_output(path, text, item_count):
    """Atomic write: tmp in the same dir, fsync, os.replace. Refuses to clobber
    a good digest with an empty/zero-item one -- yesterday's file then stays."""
    if item_count <= 0 or not text.strip():
        raise BakeError("refusing to write an empty/zero-item digest")
    d = os.path.dirname(os.path.abspath(path)) or "."
    tmp = os.path.join(d, os.path.basename(path) + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def write_last_bake(items):
    data = [{"index": i + 1, "title": c["title"], "url": c.get("link", ""),
             "score": c.get("final"), "tag": c["tag"]} for i, c in enumerate(items)]
    with open(LAST_BAKE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return data


def append_log(lines, path=LOG_PATH, keep=LOG_KEEP):
    block = "\n".join(lines).rstrip() + "\n"
    old = ""
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                old = f.read()
        except OSError:
            old = ""
    blocks = [b for b in old.split(LOG_DELIM) if b.strip()]
    blocks.append(block)
    blocks = blocks[-keep:]
    with open(path, "w", encoding="utf-8") as f:
        f.write(LOG_DELIM.join(blocks))


# ---------------------------------------------------------------- lock

def _pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False


class Lock:
    """A tiny PID lockfile so two bakes can't overlap. A stale lock (dead PID)
    is stolen."""
    def __init__(self, path=LOCK_PATH):
        self.path = path
        self.held = False

    def acquire(self):
        if os.path.exists(self.path):
            pid = None
            try:
                with open(self.path) as f:
                    pid = int(f.read().strip())
            except Exception:
                pid = None
            if pid and _pid_alive(pid):
                raise BakeError("another bake is running (pid %s)" % pid)
        with open(self.path, "w") as f:
            f.write(str(os.getpid()))
        self.held = True

    def release(self):
        if self.held:
            try:
                os.remove(self.path)
            except OSError:
                pass
            self.held = False

    def __enter__(self):
        self.acquire(); return self

    def __exit__(self, *exc):
        self.release()


# ---------------------------------------------------------------- learning

def load_last_bake():
    try:
        with open(LAST_BAKE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def resolve_labels(tokens, last_bake):
    """Map --like/--dislike tokens (1-based index, or a title/url substring) to
    last_bake entries. Returns (matched, misses)."""
    matched, misses = [], []
    for tok in tokens:
        tok = str(tok).strip()
        hit = None
        if tok.isdigit():
            idx = int(tok)
            for e in last_bake:
                if e.get("index") == idx:
                    hit = e; break
        else:
            low = tok.lower()
            for e in last_bake:
                if low == (e.get("url") or "").lower() or low in (e.get("title") or "").lower():
                    hit = e; break
        (matched if hit else misses).append(hit or tok)
    return matched, misses


def read_labels():
    rows = []
    if os.path.exists(LABELS_PATH):
        with open(LABELS_PATH, encoding="utf-8") as f:
            for ln in f:
                parts = ln.rstrip("\n").split("\t")
                if len(parts) >= 5 and parts[0] in ("like", "dislike"):
                    rows.append(dict(zip(("label", "title", "url", "source", "date"), parts[:5])))
    return rows


def write_labels(rows):
    with open(LABELS_PATH, "w", encoding="utf-8") as f:
        for r in rows:
            f.write("\t".join([r["label"], r["title"], r["url"], r["source"], r["date"]]) + "\n")


def cmd_label(label, tokens):
    """--like / --dislike: append labels.tsv (idempotent, keyed by URL)."""
    last = load_last_bake()
    if not last:
        print("no last_bake.json -- run a bake first", file=sys.stderr)
        return 1
    matched, misses = resolve_labels(tokens, last)
    rows = read_labels()
    by_url = {r["url"]: r for r in rows}
    today = datetime.date.today().isoformat()
    for e in matched:
        url = e.get("url") or ""
        rec = {"label": label, "title": e.get("title", ""), "url": url,
               "source": domain(url), "date": today}
        by_url[url] = rec                       # overwrite -> idempotent, and like<->dislike flips
    write_labels(list(by_url.values()))
    print("%sd: %s" % (label, ", ".join(e.get("title", "?")[:50] for e in matched) or "nothing"))
    if misses:
        print("no match for: %s" % ", ".join(str(m) for m in misses), file=sys.stderr)
    return 0


def cmd_eval(model_override, insecure):
    """Score labeled items and report ranking quality. <80 lines, by design."""
    rows = read_labels()
    likes = [r for r in rows if r["label"] == "like"]
    dislikes = [r for r in rows if r["label"] == "dislike"]
    if not likes or not dislikes:
        print("need both liked and disliked labels (have %d/%d)" % (len(likes), len(dislikes)))
        return 1
    if insecure:
        global _SSL_CTX
        _SSL_CTX = ssl._create_unverified_context()
    itext = _read(INTERESTS_PATH)
    questions = build_questions(itext)
    ihash, qhash = interests_hash(itext), questions_hash(questions)
    cache = Cache(CACHE_PATH)
    models = [scorer.WIRE_MODEL]
    if model_override and model_override not in models:
        models.append(model_override)
    print("eval over %d liked / %d disliked labels\n" % (len(likes), len(dislikes)))
    for model in models:
        client = OllayaClient(model=model)
        if not client.wait_ready(10):
            print("%-22s backend down" % model); continue
        mver = client.model_version()
        scored = {"like": [], "dislike": []}
        probes = {"interest_ev": [], "p_hype": [], "p_depth": [], "p_offtopic": []}
        for r in rows:
            c = {"title": r["title"], "source": r["source"], "summary": "",
                 "link": r["url"], "tag": None, "weight": 0.0}
            try:
                score_candidates([c], client, cache, mver, questions, ihash, qhash, progress=False)
            except ScoringAborted as e:
                print("%-22s aborted: %s" % (model, e)); break
            if c.get("scored"):
                scored[r["label"]].append((c["final"], r["title"]))
                for k in probes:
                    probes[k].append(c.get(k, 0.0))
        if not scored["like"] or not scored["dislike"]:
            continue
        pairs = [(a[0] > b[0]) + 0.5 * (a[0] == b[0])
                 for a in scored["like"] for b in scored["dislike"]]
        auc = sum(pairs) / len(pairs)
        ml = sum(s for s, _ in scored["like"]) / len(scored["like"])
        md = sum(s for s, _ in scored["dislike"]) / len(scored["dislike"])
        print("%-22s AUC=%.3f  mean like=%.2f  dislike=%.2f" % (model, auc, ml, md))
        worst_d = max(scored["dislike"])        # disliked but scored high
        worst_l = min(scored["like"])           # liked but scored low
        print("   worst miss (disliked, high): %.2f  %s" % (worst_d[0], worst_d[1][:48]))
        print("   worst miss (liked, low):     %.2f  %s" % (worst_l[0], worst_l[1][:48]))
        dead = [k for k, v in probes.items() if len(v) > 1 and (max(v) - min(v)) < 0.05]
        if dead:
            print("   near-constant (no signal, consider dropping): %s" % ", ".join(dead))
        print()
    cache.close()
    return 0


# ---------------------------------------------------------------- main bake

def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def print_table(items, header):
    print(header)
    for i, c in enumerate(items, 1):
        ov = (" tag:%s->%s" % (c["override"]["from"], c["override"]["to"])) if c.get("override") else ""
        print("%2d. %5s  %-7s  %-20s  i=%.2f h=%.2f d=%.2f off=%.2f%s  %s"
              % (i, ("%.2f" % c["final"]) if c.get("final") is not None else " n/a",
                 c["tag"], (c.get("feed_name") or c.get("source", ""))[:20],
                 c.get("interest_ev", 0), c.get("p_hype", 0), c.get("p_depth", 0),
                 c.get("p_offtopic", 0), ov, c["title"][:54]))


def run_bake(out_path, use_model=True, dry_run=False, insecure=False):
    global _SSL_CTX
    if insecure:
        _SSL_CTX = ssl._create_unverified_context()
    start = time.time()
    deadline = start + HARD_CEILING_SECONDS
    log = ["bake %s" % datetime.datetime.now().isoformat(timespec="seconds")]
    errors = []
    cache = Cache(CACHE_PATH)
    backend = "legacy (--no-model)" if not use_model else "legacy"
    shipped = []

    try:
        cands = collect(FEEDS, cache, drop_seen=True, errors=errors)
        log.append("candidates: %d  feed-errors: %d" % (len(cands), len(errors)))

        picked = None
        if use_model:
            client = OllayaClient(model=scorer.WIRE_MODEL)
            if not client.wait_ready():
                log.append("FALLBACK: Ollaya unreachable at %s after %ds -- legacy newest-first"
                           % (scorer.OLLAYA_HOST, scorer.BOOT_WAIT_SECONDS))
            else:
                itext = _read(INTERESTS_PATH)
                questions = build_questions(itext)
                ihash, qhash = interests_hash(itext), questions_hash(questions)
                mver = client.model_version()
                warm = client.warmup()           # absorb the cold model load up front
                log.append("model warmup: %s" % ("%.1fs" % warm if warm is not None else "failed"))
                if sys.stderr.isatty():
                    sys.stderr.write("  model warmed in %s\n"
                                     % ("%.1fs" % warm if warm is not None else "— (load failed)"))
                try:
                    stats = score_candidates(cands, client, cache, mver, questions,
                                             ihash, qhash, deadline=deadline,
                                             log=lambda m: errors.append(m),
                                             progress=sys.stderr.isatty())
                    backend = "ollaya %s (%s)" % (client.model, mver[:12])
                    log.append("scored live:%d cache-hits:%d fails:%d  latency med:%.2fs p95:%.2fs"
                               % (stats["scored_live"], stats["cache_hits"], stats["fails"],
                                  stats["lat_median"], stats["lat_p95"]))
                    elig = [c for c in cands if c.get("scored") and not c.get("reject")
                            and c.get("final", 0) >= MIN_SCORE_DEFAULT]
                    elig.sort(key=lambda c: c.get("final", 0), reverse=True)
                    winners = select(cands, MIN_SCORE_DEFAULT)
                    for c in winners:
                        if c.get("override"):
                            log.append("override: %s %s->%s p=%.2f  %s"
                                       % (c["source"], c["override"]["from"], c["override"]["to"],
                                          c["override"]["p"], c["title"][:50]))
                    if dry_run:
                        ranked = sorted([c for c in cands if c.get("scored")],
                                        key=lambda c: c.get("final", 0), reverse=True)
                        print_table(ranked[:30], "RANKED (top 30 of %d scored) -- dry run, nothing written:" % len(ranked))
                        print("\nwould ship %d:" % len(winners))
                        print_table(winners, "")
                        cache.close()
                        return 0
                    picked = extract_winners(winners, elig)
                except ScoringAborted as e:
                    log.append("FALLBACK: %s -- legacy newest-first" % e)

        if picked is None:                       # legacy path (no-model, down, or aborted)
            if dry_run:
                picked = legacy_select(cands)
                print_table(picked, "LEGACY selection -- dry run, nothing written:")
                cache.close()
                return 0
            picked = legacy_select(cands)

        shipped = picked
        text = render(shipped)
        write_output(out_path, text, len(shipped))
        write_last_bake(shipped)
        for c in shipped:                        # remember what shipped; forget the old
            if c.get("link"):
                cache.mark_seen(c["link"], c["title"])
        cache.purge_seen(SEEN_DAYS)
    except BakeError as e:
        log.append("ERROR: %s (kept yesterday's wire.txt)" % e)
        _finish_log(log, start, backend, errors, shipped)
        cache.close()
        print("bake failed: %s" % e, file=sys.stderr)
        return 1

    _finish_log(log, start, backend, errors, shipped)
    cache.close()
    print(">> wrote %s: %d items, %d bytes (%s)"
          % (out_path, len(shipped), len(text.encode("utf-8")), backend))
    return 0


def _finish_log(log, start, backend, errors, shipped):
    log.insert(1, "backend: %s" % backend)
    for c in shipped:
        sc = ("%.2f" % c["final"]) if c.get("final") is not None else "n/a"
        log.append("  %5s [%s] %s" % (sc, c["tag"], c["title"][:66]))
    for e in errors[:20]:
        log.append("  ! %s" % e)
    log.append("elapsed: %.1fs  shipped: %d" % (time.time() - start, len(shipped)))
    try:
        append_log(log)
    except OSError:
        pass


def main(argv=None):
    ap = argparse.ArgumentParser(description="Bake the day's Wire digest.")
    ap.add_argument("-o", "--out", default="wire.txt", help="output file (default wire.txt)")
    ap.add_argument("--insecure", action="store_true",
                    help="skip TLS verification (macOS Python missing root certs)")
    ap.add_argument("--no-model", action="store_true", help="force the legacy newest-first path")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the ranked table; write nothing, mark nothing SEEN")
    ap.add_argument("--like", nargs="+", metavar="N", help="label last-bake items as liked")
    ap.add_argument("--dislike", nargs="+", metavar="N", help="label last-bake items as disliked")
    ap.add_argument("--eval", action="store_true", help="report ranking quality over labels.tsv")
    ap.add_argument("--model", help="override WIRE_MODEL (for --eval comparison)")
    args = ap.parse_args(argv)

    if args.like:
        return cmd_label("like", args.like)
    if args.dislike:
        return cmd_label("dislike", args.dislike)
    if args.eval:
        return cmd_eval(args.model, args.insecure)
    if args.model:
        scorer.WIRE_MODEL = args.model

    if args.dry_run:                             # dev tool: no lock, no writes
        return run_bake(args.out, use_model=not args.no_model, dry_run=True, insecure=args.insecure)
    try:
        with Lock():
            return run_bake(args.out, use_model=not args.no_model, insecure=args.insecure)
    except BakeError as e:
        print(str(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
