#!/usr/bin/env python3
"""
Local taste ranking for Wire, via Ollaya's `laya` decision model.

The phone stays dumb; the *curation* gets a brain here on the server. For each
candidate headline we ask one small, local, non-generative model four typed
questions (how interesting, which topic, is it hype, does it have depth) and
turn the calibrated probabilities it returns into a single 0-10 score. No cloud,
no API keys, no text generation -- laya scores a fixed set of answers in one
forward pass, so there is nothing to parse and nothing to hallucinate.

Backend: Ollaya only (https://ollaya.dev), an "Ollama for decision models".
  POST {OLLAYA_HOST}/api/decide   (default http://127.0.0.1:11435)
  body:  {"model","state","questions":{name:{"type","instructions","criteria"?}}}
  reply: {"answers":{name:{...}}, "usage":{"input_tokens"}, "state_truncated":bool}

This module owns: the Ollaya HTTP client, the SQLite cache + SEEN table, the
scoring math, and all tunable constants. bake.py owns collection, selection,
extraction, rendering and robustness. (Split out per the ~500-line rule.)

Model is pinned via WIRE_MODEL (default `laya:en`, NOT `laya`/`latest`). The
model's content digest is folded into the cache key, so pulling a new build of
the same tag invalidates old scores. Alternatives (`decider`, `gliclass`, the
multilingual `laya:multilingual`) work too -- set WIRE_MODEL; see the README.
"""

import hashlib
import json
import os
import re
import socket
import sqlite3
import statistics
import sys
import time
import urllib.error
import urllib.request

# --------------------------------------------------------------- config (env)
OLLAYA_HOST = os.environ.get("OLLAYA_HOST", "http://127.0.0.1:11435").rstrip("/")
OLLAYA_API_KEY = os.environ.get("OLLAYA_API_KEY", "")   # only sent if set (loopback needs none)
WIRE_MODEL = os.environ.get("WIRE_MODEL", "laya:en")    # pinned tag
# Keep the model resident through the whole run. laya:en is fp32-on-CPU and takes
# ~14s to (re)load on a Pi 4B; without this it unloads after a few idle minutes
# and every request after a gap pays that reload. "30m" / "0" / "-1" per Ollaya.
KEEP_ALIVE = os.environ.get("WIRE_KEEP_ALIVE", "30m")

# --------------------------------------------------------------- scoring knobs
# Final score (0-10), per candidate:
#   final = INTEREST_SCALE * E[interest]          # E over the 5 ordered levels (0..4) -> 0..8
#         + DEPTH_BONUS   * P(depth)              # rewards "explains how it works / hands-on"
#         - HYPE_PENALTY  * P(hype)               # punishes marketing / funding / listicle / opinion
#         + weight                                # per-source nudge, clamped to +/- WEIGHT_CLAMP
# then clamped to [0, 10]. Hard-rejected (never ships) if P(OFFTOPIC) > OFFTOPIC_MAX.
INTEREST_SCALE = 2.0        # 5 levels => E in [0,4] => base in [0,8]
DEPTH_BONUS = 1.5
HYPE_PENALTY = 2.0
OFFTOPIC_MAX = 0.50         # P(OFFTOPIC) above this => hard reject (news/politics/etc. leaking in)
TOPIC_OVERRIDE_MIN = 0.60   # model's topic overrides the feed's tag only above this prob
WEIGHT_CLAMP = 1.0          # a source's weight can only nudge the score by +/- this
MIN_SCORE_DEFAULT = 6.0     # below this never ships (ship fewer than 10 rather than pad)
STATE_MAX_CHARS = 600       # cap the state; shorter = faster inference (title+source+blurb is plenty)

BOOT_WAIT_SECONDS = 60      # retry the health check this long (cron vs. daemon boot race)
FIRST_FAIL_ABORT = 5        # if the first N live requests all fail, give up and fall back
# A laya:en forward pass is ~20-30s on a Pi 4B CPU (ModernBERT-large, fp32), so
# the per-request budget must sit well above that or valid inferences get clipped.
HTTP_TIMEOUT = int(os.environ.get("WIRE_HTTP_TIMEOUT", "120"))   # per-request, seconds
HTTP_RETRIES = 1            # one retry on a CONNECTION error only -- never on a timeout
WARMUP_TIMEOUT = int(os.environ.get("WIRE_WARMUP_TIMEOUT", "300"))   # absorb the cold model load

CATEGORIES = ("TECH", "RETRO", "CONCEPT")
TOPIC_OPTIONS = {
    "TECH": "hardware, software, programming, computing tools, or an engineering build",
    "RETRO": "retro computing, vintage hardware or consoles, or game preservation and history",
    "CONCEPT": "a science or engineering concept, mechanism or idea explained in depth",
    "OFFTOPIC": ("current events, news, politics, business or finance, celebrity, "
                 "culture-war, or cryptocurrency"),
}
INTEREST_LEVELS = [
    "not interesting at all",
    "slightly interesting",
    "moderately interesting",
    "very interesting",
    "must-read",
]


# --------------------------------------------------------------- questions
def _parse_interests(text):
    """Pull the Likes:/Dislikes: clauses out of interests.md; fall back to all text."""
    likes = dislikes = ""
    m = re.search(r"(?is)\blikes?\s*:\s*(.+?)(?:\n\s*\n|\bdislikes?\s*:|\Z)", text)
    if m:
        likes = re.sub(r"\s+", " ", m.group(1)).strip()
    m = re.search(r"(?is)\bdislikes?\s*:\s*(.+?)(?:\n\s*\n|\Z)", text)
    if m:
        dislikes = re.sub(r"\s+", " ", m.group(1)).strip()
    if not likes and not dislikes:
        likes = re.sub(r"\s+", " ", text).strip()
    return likes, dislikes


def build_questions(interests_text):
    """The batched question set asked about every candidate. Its hash is part of
    the cache key, so editing these (or interests.md) re-scores everything."""
    likes, dislikes = _parse_interests(interests_text)
    interest_instr = (
        "Rate how much a reader with these tastes would want to read this. "
        "They LIKE: " + (likes or "n/a") + " They DISLIKE: " + (dislikes or "n/a")
    )
    return {
        "interest": {"type": "score", "instructions": interest_instr,
                     "criteria": list(INTEREST_LEVELS)},
        "topic": {"type": "choice",
                  "instructions": "Which single category best fits this item?",
                  "criteria": dict(TOPIC_OPTIONS)},
        "hype": {"type": "noul",
                 "instructions": ("Is this mainly marketing, funding news, a product launch, "
                                  "a listicle, or opinion, rather than substantive content?")},
        "depth": {"type": "noul",
                  "instructions": ("Does this explain how something works, or show hands-on "
                                   "build or teardown detail?")},
    }


def build_state(title, source, summary):
    """The per-candidate 'state' laya reads. Feed summary only -- never the full
    article (we only download full text for the handful of winners)."""
    summary = re.sub(r"\s+", " ", summary or "").strip()
    state = "Title: %s\nSource: %s\nSummary: %s" % (title, source, summary)
    if len(state) > STATE_MAX_CHARS:
        state = state[:STATE_MAX_CHARS].rstrip() + "…"
    return state


# --------------------------------------------------------------- Ollaya client
class OllayaError(Exception):
    """A /api/decide call failed. `code` is Ollaya's error code when it sent one
    (e.g. MODEL_NOT_FOUND), else None."""
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


class ScoringAborted(Exception):
    """Scoring gave up (model missing, or the first N live requests all failed).
    bake.py catches this and takes the legacy fallback path."""


class OllayaClient:
    def __init__(self, host=OLLAYA_HOST, api_key=OLLAYA_API_KEY,
                 model=WIRE_MODEL, timeout=HTTP_TIMEOUT, retries=HTTP_RETRIES):
        self.host = host.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.retries = retries

    def _request(self, method, path, payload=None, timeout=None):
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(self.host + path, data=data, method=method)
        if payload is not None:
            req.add_header("Content-Type", "application/json")
        if self.api_key:
            req.add_header("Authorization", "Bearer " + self.api_key)
        with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def warmup(self, timeout=WARMUP_TIMEOUT):
        """Force the model to load (and run its first-pass warmup) with a
        generous timeout, so the cold load never clips the first real request.
        Returns the model-load seconds, or None if warmup failed."""
        t0 = time.time()
        try:
            self._request("POST", "/api/decide", timeout=timeout, payload={
                "model": self.model, "keep_alive": KEEP_ALIVE,
                "state": "warmup", "questions": {
                    "_w": {"type": "noul", "instructions": "Is this a warmup?"}}})
            return time.time() - t0
        except Exception:
            return None

    def version(self):
        return self._request("GET", "/api/version").get("version", "")

    def model_version(self):
        """A stable version string for the pinned model, for the cache key: the
        content digest if we can read it, else the bare tag."""
        try:
            for m in self._request("GET", "/api/tags").get("models", []):
                if self.model in (m.get("name"), m.get("model")):
                    return (m.get("digest") or self.model)[:32]
        except Exception:
            pass
        return self.model

    def wait_ready(self, boot_secs=BOOT_WAIT_SECONDS, now=time.time, sleep=time.sleep):
        """Retry the health check up to boot_secs (cron can beat the daemon up)."""
        deadline = now() + boot_secs
        while True:
            try:
                self.version()
                return True
            except Exception:
                if now() >= deadline:
                    return False
                sleep(2)

    def decide(self, state, questions):
        payload = {"model": self.model, "state": state, "questions": questions,
                   "keep_alive": KEEP_ALIVE}
        last = None
        for attempt in range(self.retries + 1):
            try:
                return self._request("POST", "/api/decide", payload)
            except urllib.error.HTTPError as e:            # deterministic 4xx/5xx: don't retry
                body = e.read().decode("utf-8", "replace")
                code = None
                try:
                    code = json.loads(body).get("code")
                except Exception:
                    pass
                raise OllayaError("HTTP %s: %s" % (e.code, body[:200]), code=code)
            except (socket.timeout, TimeoutError):          # slow-but-valid inference: do NOT
                raise OllayaError("timeout after %ss" % self.timeout)   # retry (piles on load)
            except urllib.error.URLError as e:
                if isinstance(getattr(e, "reason", None), (socket.timeout, TimeoutError)):
                    raise OllayaError("timeout after %ss" % self.timeout)
                last = e                                     # genuine connection error: one retry
                if attempt < self.retries:
                    time.sleep(1)
                    continue
                raise OllayaError(str(last))
            except Exception as e:
                last = e
                if attempt < self.retries:
                    time.sleep(1)
                    continue
                raise OllayaError(str(last))


# --------------------------------------------------------------- scoring math
def expected_interest(probabilities):
    """E[level] over the ordered 0..4 levels from laya's probability map."""
    if not probabilities:
        return 0.0
    return sum(int(k) * float(v) for k, v in probabilities.items())


def score_answers(answers, weight, feed_tag):
    """Turn one laya /api/decide `answers` block into a scored verdict dict."""
    interest = answers.get("interest", {}) or {}
    ev = expected_interest(interest.get("probabilities")) or float(interest.get("score", 0.0))

    topic = answers.get("topic", {}) or {}
    tprobs = topic.get("probabilities", {}) or {}
    choice = topic.get("choice")
    p_off = float(tprobs.get("OFFTOPIC", 0.0))

    p_hype = float((answers.get("hype", {}) or {}).get("noul", 0.0))
    p_depth = float((answers.get("depth", {}) or {}).get("noul", 0.0))

    w = max(-WEIGHT_CLAMP, min(WEIGHT_CLAMP, float(weight)))
    final = ev * INTEREST_SCALE + DEPTH_BONUS * p_depth - HYPE_PENALTY * p_hype + w
    final = max(0.0, min(10.0, final))

    # The model's topic overrides the feed's tag only when it is confident AND
    # on-topic. OFFTOPIC never becomes a tag -- it only drives the hard reject.
    tag = feed_tag
    override = None
    if (choice and choice != "OFFTOPIC" and choice in CATEGORIES
            and choice != feed_tag and tprobs.get(choice, 0.0) > TOPIC_OVERRIDE_MIN):
        tag = choice
        override = {"from": feed_tag, "to": choice, "p": round(tprobs.get(choice, 0.0), 3)}

    return {
        "final": round(final, 3),
        "interest_ev": round(ev, 3),
        "p_offtopic": round(p_off, 3),
        "p_hype": round(p_hype, 3),
        "p_depth": round(p_depth, 3),
        "tag": tag,
        "topic_choice": choice,
        "reject": p_off > OFFTOPIC_MAX,
        "override": override,
    }


# --------------------------------------------------------------- cache + SEEN
def _sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def interests_hash(text):
    return _sha(text)[:16]


def questions_hash(questions):
    return _sha(json.dumps(questions, sort_keys=True))[:16]


class Cache:
    """SQLite-backed score cache + SEEN ledger (server/.wirecache.db). The score
    cache is keyed so a key is never rescored; SEEN remembers shipped URLs."""

    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS scores("
                        "key TEXT PRIMARY KEY, payload TEXT, created REAL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS seen("
                        "url TEXT PRIMARY KEY, title TEXT, shipped REAL)")
        self.db.commit()

    def key(self, url, model_ver, ihash, qhash):
        return _sha("|".join([url or "", model_ver, ihash, qhash]))

    def get(self, key):
        row = self.db.execute("SELECT payload FROM scores WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key, payload):
        self.db.execute("INSERT OR REPLACE INTO scores(key,payload,created) VALUES(?,?,?)",
                        (key, json.dumps(payload), time.time()))
        self.db.commit()

    def is_seen(self, url, days):
        cut = time.time() - days * 86400
        return self.db.execute("SELECT 1 FROM seen WHERE url=? AND shipped>=?",
                               (url, cut)).fetchone() is not None

    def mark_seen(self, url, title):
        self.db.execute("INSERT OR REPLACE INTO seen(url,title,shipped) VALUES(?,?,?)",
                        (url, title, time.time()))
        self.db.commit()

    def purge_seen(self, days):
        self.db.execute("DELETE FROM seen WHERE shipped<?", (time.time() - days * 86400,))
        self.db.commit()

    def close(self):
        self.db.close()


# --------------------------------------------------------------- scoring driver
def _p95(xs):
    if not xs:
        return 0.0
    s = sorted(xs)
    return s[min(len(s) - 1, int(round(0.95 * (len(s) - 1))))]


def _heartbeat(done, total, live, cache_hits, fails, latencies, last_lat):
    med = statistics.median(latencies) if latencies else 0.0
    last = "%.2fs" % last_lat if last_lat is not None else "  -  "
    sys.stderr.write("\r  scoring %d/%d  live %d cached %d fail %d  "
                     "last %s  med %.2fs  p95 %.2fs      "
                     % (done, total, live, cache_hits, fails, last, med, _p95(latencies)))
    sys.stderr.flush()


def score_candidates(cands, client, cache, model_ver, questions, ihash, qhash,
                     now=time.time, deadline=None, log=lambda m: None, progress=True):
    """Score every candidate in place (adds the score_answers keys plus
    `scored`, `cached`, `truncated`). Uses the cache; a cached key is never
    re-scored. Raises ScoringAborted if the model is missing or the first
    FIRST_FAIL_ABORT live requests all fail. Returns a stats dict. With
    `progress`, writes an updating one-line heartbeat to stderr."""
    latencies = []
    last_lat = None
    total = len(cands)
    cache_hits = live = fails = done = 0
    for c in cands:
        if deadline is not None and now() >= deadline:
            log("hard ceiling reached during scoring; stopping with %d scored" % live)
            break
        url = c.get("link") or c.get("title") or ""
        ckey = cache.key(url, model_ver, ihash, qhash)
        cached = cache.get(ckey)
        if cached is not None:
            c.update(cached)
            c["scored"] = True
            c["cached"] = True
            cache_hits += 1
            done += 1
            if progress:
                _heartbeat(done, total, live, cache_hits, fails, latencies, last_lat)
            continue
        state = build_state(c.get("title", ""), c.get("source", ""), c.get("summary", ""))
        t0 = now()
        try:
            resp = client.decide(state, questions)
        except OllayaError as e:
            if e.code == "MODEL_NOT_FOUND":
                raise ScoringAborted("model %r not pulled: %s" % (client.model, e))
            fails += 1
            done += 1
            log("decide failed (%d): %s" % (fails, e))
            if progress:
                sys.stderr.write("\n  ! decide failed (%d): %s\n" % (fails, e))
            if live == 0 and fails >= FIRST_FAIL_ABORT:
                raise ScoringAborted("first %d live requests all failed" % FIRST_FAIL_ABORT)
            continue
        last_lat = now() - t0
        latencies.append(last_lat)
        res = score_answers(resp.get("answers", {}), c.get("weight", 0.0), c.get("tag"))
        res["truncated"] = bool(resp.get("state_truncated", False))
        cache.put(ckey, res)
        c.update(res)
        c["scored"] = True
        c["cached"] = False
        live += 1
        done += 1
        if progress:
            _heartbeat(done, total, live, cache_hits, fails, latencies, last_lat)
    if progress:
        _heartbeat(done, total, live, cache_hits, fails, latencies, last_lat)
        sys.stderr.write("\n")
        sys.stderr.flush()
    return {
        "cache_hits": cache_hits,
        "scored_live": live,
        "fails": fails,
        "lat_median": round(statistics.median(latencies), 3) if latencies else 0.0,
        "lat_p95": round(_p95(latencies), 3),
    }
