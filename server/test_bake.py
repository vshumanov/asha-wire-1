#!/usr/bin/env python3
"""
Stdlib-unittest coverage for the Wire bake. No network, no real Ollaya: the
decision model is a FakeClient and article extraction is monkeypatched.

    python3 -m unittest -v test_bake        (from server/)
"""

import json
import os
import socket
import tempfile
import unittest

import bake
import scorer


# --------------------------------------------------------------- helpers

def answers(ev_probs=None, choice="TECH", choice_probs=None, hype=0.0, depth=0.0):
    """Build a laya-style answers block. ev_probs: 5 floats over levels 0..4."""
    ev_probs = ev_probs or [0, 0, 0, 1.0, 0]
    choice_probs = choice_probs or {"TECH": 0.9, "RETRO": 0.03, "CONCEPT": 0.03, "OFFTOPIC": 0.04}
    return {
        "interest": {"type": "score", "probabilities": {str(i): p for i, p in enumerate(ev_probs)},
                     "score": sum(i * p for i, p in enumerate(ev_probs))},
        "topic": {"type": "choice", "choice": choice, "probabilities": choice_probs},
        "hype": {"type": "noul", "noul": hype},
        "depth": {"type": "noul", "noul": depth},
    }


class FakeClient:
    def __init__(self, reply=None, fail=False, code=None, model="laya:en"):
        self.reply = reply if reply is not None else {"answers": answers(), "state_truncated": False}
        self.fail = fail
        self.code = code
        self.model = model
        self.calls = 0

    def model_version(self):
        return "deadbeefcafe"

    def wait_ready(self, *a, **k):
        return True

    def decide(self, state, questions):
        self.calls += 1
        if self.fail:
            raise scorer.OllayaError("boom", code=self.code)
        return self.reply


def scored(tag, source, final, reject=False, title=None, override=None):
    return {"tag": tag, "source": source, "final": final, "reject": reject,
            "scored": True, "title": title or ("%s-%s-%s" % (tag, source, final)),
            "link": "https://%s/%s" % (source, final), "summary": "s",
            "full_html": "", "weight": 0.0, "feed_name": source, "override": override}


def mini_parse_wire1(text):
    """Faithful reimplementation of Digest.parse's invariants, to confirm the
    rendered digest is parseable by the phone (mirrors DigestTest)."""
    raw = [ln[:-1] if ln.endswith("\r") else ln for ln in text.split("\n")]
    p = 0
    while p < len(raw) and raw[p].strip() == "":
        p += 1
    if p >= len(raw) or raw[p].strip() != "WIRE1":
        return None
    p += 1
    date = raw[p].strip() if p < len(raw) else ""
    p += 1
    items, tag, head, body, in_item = [], None, None, [], False
    for line in raw[p:]:
        if line[:1] == "#":
            if in_item:
                items.append((tag, head, "\n".join(body).rstrip("\n")))
                in_item = False
            if len(items) >= bake.MAX_ITEMS:
                break
            h = line[1:].strip()
            tag = ""
            if h.startswith("["):
                e = h.find("]")
                if e > 0:
                    tag, h = h[1:e].strip(), h[e + 1:].strip()
            head, body, in_item = h, [], True
        elif in_item:
            body.append(line)
    if in_item and len(items) < bake.MAX_ITEMS:
        items.append((tag, head, "\n".join(body).rstrip("\n")))
    return {"date": date, "items": items}


# --------------------------------------------------------------- score math

class TestScoreMath(unittest.TestCase):
    def test_expected_interest(self):
        self.assertAlmostEqual(scorer.expected_interest({"0": 0.0, "1": 0.0, "2": 0.0, "3": 1.0, "4": 0.0}), 3.0)
        self.assertAlmostEqual(scorer.expected_interest({"0": 0.5, "4": 0.5}), 2.0)

    def test_final_formula(self):
        r = scorer.score_answers(answers(ev_probs=[0, 0, 0, 1.0, 0], hype=0.0, depth=1.0), weight=0.5, feed_tag="TECH")
        # 3.0*2.0 + 1.5*1.0 - 2.0*0 + 0.5 = 8.0
        self.assertAlmostEqual(r["final"], 8.0, places=3)
        self.assertFalse(r["reject"])

    def test_hype_penalty_and_clamp(self):
        r = scorer.score_answers(answers(ev_probs=[1.0, 0, 0, 0, 0], hype=1.0), weight=-1.0, feed_tag="TECH")
        self.assertEqual(r["final"], 0.0)   # clamped at 0

    def test_weight_clamped(self):
        r = scorer.score_answers(answers(ev_probs=[0, 0, 0, 0, 1.0]), weight=5.0, feed_tag="TECH")
        # ev=4 -> 8.0 base + weight clamped to +1.0 = 9.0
        self.assertAlmostEqual(r["final"], 9.0, places=3)

    def test_offtopic_reject(self):
        cp = {"TECH": 0.2, "RETRO": 0.1, "CONCEPT": 0.1, "OFFTOPIC": 0.6}
        r = scorer.score_answers(answers(choice="OFFTOPIC", choice_probs=cp), weight=0.0, feed_tag="TECH")
        self.assertTrue(r["reject"])

    def test_topic_override(self):
        cp = {"TECH": 0.1, "RETRO": 0.8, "CONCEPT": 0.05, "OFFTOPIC": 0.05}
        r = scorer.score_answers(answers(choice="RETRO", choice_probs=cp), weight=0.0, feed_tag="TECH")
        self.assertEqual(r["tag"], "RETRO")
        self.assertIsNotNone(r["override"])

    def test_no_override_below_threshold(self):
        cp = {"TECH": 0.45, "RETRO": 0.5, "CONCEPT": 0.03, "OFFTOPIC": 0.02}  # 0.5 < 0.6
        r = scorer.score_answers(answers(choice="RETRO", choice_probs=cp), weight=0.0, feed_tag="TECH")
        self.assertEqual(r["tag"], "TECH")
        self.assertIsNone(r["override"])

    def test_malformed_answers_default_zero(self):
        r = scorer.score_answers({}, weight=0.0, feed_tag="TECH")
        self.assertEqual(r["final"], 0.0)
        self.assertEqual(r["tag"], "TECH")


# --------------------------------------------------------------- selection

class TestSelection(unittest.TestCase):
    def test_threshold(self):
        cands = [scored("TECH", "a.com", 7.0), scored("TECH", "b.com", 5.9),
                 scored("CONCEPT", "c.com", 6.1), scored("RETRO", "d.com", 2.0)]
        picked = bake.select(cands, 6.0)
        finals = sorted(c["final"] for c in picked)
        self.assertNotIn(5.9, finals)
        self.assertNotIn(2.0, finals)
        self.assertIn(7.0, finals)

    def test_per_domain_cap(self):
        cands = [scored("TECH", "same.com", 9.0), scored("TECH", "same.com", 8.5),
                 scored("TECH", "same.com", 8.0), scored("CONCEPT", "x.com", 7.0),
                 scored("RETRO", "y.com", 7.0)]
        picked = bake.select(cands, 6.0)
        from_same = [c for c in picked if c["source"] == "same.com"]
        self.assertEqual(len(from_same), bake.MAX_PER_DOMAIN)

    def test_min_one_per_category(self):
        cands = ([scored("TECH", "t%d.com" % i, 9.0 - i * 0.1) for i in range(8)]
                 + [scored("CONCEPT", "c.com", 6.2), scored("RETRO", "r.com", 6.1)])
        picked = bake.select(cands, 6.0)
        tags = {c["tag"] for c in picked}
        self.assertEqual(tags, {"TECH", "CONCEPT", "RETRO"})

    def test_reject_excluded(self):
        cands = [scored("TECH", "a.com", 9.0, reject=True), scored("CONCEPT", "b.com", 7.0)]
        picked = bake.select(cands, 6.0)
        self.assertTrue(all(not c["reject"] for c in picked))
        self.assertEqual(len(picked), 1)

    def test_short_digest_no_padding(self):
        cands = [scored("TECH", "a.com", 7.0)]   # only one clears
        picked = bake.select(cands, 6.0)
        self.assertEqual(len(picked), 1)

    def test_cap_at_max_items(self):
        cands = [scored(("TECH", "CONCEPT", "RETRO")[i % 3], "d%d.com" % i, 9.0 - i * 0.01)
                 for i in range(40)]
        picked = bake.select(cands, 6.0)
        self.assertLessEqual(len(picked), bake.MAX_ITEMS)


# --------------------------------------------------------------- extraction / replacement

class TestExtractReplacement(unittest.TestCase):
    def setUp(self):
        self._orig = bake.article_text

    def tearDown(self):
        bake.article_text = self._orig

    def test_summary_only_winner_replaced_by_next_ranked(self):
        good = scored("TECH", "good.com", 8.0, title="good")
        bad = scored("TECH", "bad.com", 9.0, title="bad")       # higher, but summary-only
        backup = scored("TECH", "backup.com", 7.0, title="backup")

        def fake(summary, link, full):
            if "bad.com" in link:
                return ("blurb", "bad.com", False)              # only summary
            return ("Full article text here.", bake.domain(link), True)

        bake.article_text = fake
        final = bake.extract_winners([bad, good], [bad, good, backup])
        titles = [c["title"] for c in final]
        self.assertIn("backup", titles)       # replacement pulled in
        self.assertIn("good", titles)
        self.assertNotIn("bad", titles)       # summary-only winner dropped in favour of backup


# --------------------------------------------------------------- cache / invalidation

class TestCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.cache = scorer.Cache(self.tmp.name)

    def tearDown(self):
        self.cache.close()
        os.unlink(self.tmp.name)

    def test_key_components_change_key(self):
        k0 = self.cache.key("u", "m", "i", "q")
        self.assertNotEqual(k0, self.cache.key("u", "m2", "i", "q"))   # model version
        self.assertNotEqual(k0, self.cache.key("u", "m", "i2", "q"))   # interests
        self.assertNotEqual(k0, self.cache.key("u", "m", "i", "q2"))   # question set
        self.assertNotEqual(k0, self.cache.key("u2", "m", "i", "q"))   # url

    def test_second_score_is_cache_hit(self):
        q = scorer.build_questions("Likes: x Dislikes: y")
        client = FakeClient()
        c1 = scored("TECH", "a.com", 0)
        bake_cand = {"title": "t", "source": "a.com", "summary": "s", "link": "https://a.com/1",
                     "tag": "TECH", "weight": 0.0}
        s1 = scorer.score_candidates([dict(bake_cand)], client, self.cache, "mv", q, "ih", "qh", progress=False)
        self.assertEqual(s1["scored_live"], 1)
        self.assertEqual(client.calls, 1)
        s2 = scorer.score_candidates([dict(bake_cand)], client, self.cache, "mv", q, "ih", "qh", progress=False)
        self.assertEqual(s2["cache_hits"], 1)
        self.assertEqual(client.calls, 1)      # not called again
        # changing the question-set hash invalidates -> rescored
        s3 = scorer.score_candidates([dict(bake_cand)], client, self.cache, "mv", q, "ih", "qh-NEW", progress=False)
        self.assertEqual(s3["scored_live"], 1)
        self.assertEqual(client.calls, 2)

    def test_seen_window(self):
        self.cache.mark_seen("https://a.com/x", "t")
        self.assertTrue(self.cache.is_seen("https://a.com/x", 14))
        self.assertFalse(self.cache.is_seen("https://a.com/other", 14))


# --------------------------------------------------------------- fallback / abort / ceiling

class TestScoringControl(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.cache = scorer.Cache(self.tmp.name)
        self.q = scorer.build_questions("Likes: x")

    def tearDown(self):
        self.cache.close()
        os.unlink(self.tmp.name)

    def _cands(self, n):
        return [{"title": "t%d" % i, "source": "s%d.com" % i, "summary": "s",
                 "link": "https://s%d.com/%d" % (i, i), "tag": "TECH", "weight": 0.0}
                for i in range(n)]

    def test_abort_after_five_failures(self):
        client = FakeClient(fail=True)
        with self.assertRaises(scorer.ScoringAborted):
            scorer.score_candidates(self._cands(10), client, self.cache, "mv", self.q, "i", "q", progress=False)
        self.assertEqual(client.calls, scorer.FIRST_FAIL_ABORT)

    def test_model_not_found_aborts_immediately(self):
        client = FakeClient(fail=True, code="MODEL_NOT_FOUND")
        with self.assertRaises(scorer.ScoringAborted):
            scorer.score_candidates(self._cands(10), client, self.cache, "mv", self.q, "i", "q", progress=False)
        self.assertEqual(client.calls, 1)

    def test_hung_process_ceiling(self):
        client = FakeClient()
        clock = {"t": 1000.0}

        def now():
            clock["t"] += 10.0           # each check advances 10s
            return clock["t"]

        # deadline only 25s out -> stops after ~2 candidates
        stats = scorer.score_candidates(self._cands(50), client, self.cache, "mv", self.q,
                                        "i", "q", now=now, deadline=1025.0, progress=False)
        self.assertLess(stats["scored_live"], 50)
        self.assertGreaterEqual(stats["scored_live"], 1)

    def test_truncated_flag_recorded(self):
        client = FakeClient(reply={"answers": answers(), "state_truncated": True})
        cands = self._cands(1)
        scorer.score_candidates(cands, client, self.cache, "mv", self.q, "i", "q", progress=False)
        self.assertTrue(cands[0]["truncated"])


# --------------------------------------------------------------- atomic write

class TestAtomicWrite(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.path = os.path.join(self.d, "wire.txt")

    def test_refuses_zero_items(self):
        with self.assertRaises(bake.BakeError):
            bake.write_output(self.path, "WIRE1\ndate\n", 0)
        self.assertFalse(os.path.exists(self.path))

    def test_crash_leaves_old_file(self):
        bake.write_output(self.path, "WIRE1\nday one\n# [TECH] a\nbody\n", 1)
        with open(self.path) as f:
            orig = f.read()
        real_replace = os.replace
        os.replace = lambda *a, **k: (_ for _ in ()).throw(OSError("crash during replace"))
        try:
            with self.assertRaises(OSError):
                bake.write_output(self.path, "WIRE1\nday two\n# [TECH] b\nnew\n", 1)
        finally:
            os.replace = real_replace
        with open(self.path) as f:
            self.assertEqual(f.read(), orig)   # old digest intact


# --------------------------------------------------------------- lock

class TestLock(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.path = os.path.join(self.d, ".bake.lock")

    def test_exclusion_and_release(self):
        a = bake.Lock(self.path)
        a.acquire()
        b = bake.Lock(self.path)
        with self.assertRaises(bake.BakeError):
            b.acquire()
        a.release()
        b.acquire()      # now free
        b.release()

    def test_stale_lock_stolen(self):
        with open(self.path, "w") as f:
            f.write("999999")      # almost certainly dead PID
        a = bake.Lock(self.path)
        a.acquire()                # should steal, not raise
        a.release()


# --------------------------------------------------------------- learning

class TestLabels(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self._last, self._labels = bake.LAST_BAKE_PATH, bake.LABELS_PATH
        bake.LAST_BAKE_PATH = os.path.join(self.d, "last_bake.json")
        bake.LABELS_PATH = os.path.join(self.d, "labels.tsv")
        with open(bake.LAST_BAKE_PATH, "w") as f:
            json.dump([
                {"index": 1, "title": "DOOM on a toaster", "url": "https://a.com/doom", "score": 8.1, "tag": "RETRO"},
                {"index": 2, "title": "RISC-V board", "url": "https://b.com/riscv", "score": 7.5, "tag": "TECH"},
                {"index": 3, "title": "Lattice crypto", "url": "https://c.com/pq", "score": 6.9, "tag": "CONCEPT"},
            ], f)

    def tearDown(self):
        bake.LAST_BAKE_PATH, bake.LABELS_PATH = self._last, self._labels

    def test_resolve_by_index_title_url(self):
        last = bake.load_last_bake()
        matched, misses = bake.resolve_labels(["1", "RISC-V", "https://c.com/pq"], last)
        self.assertEqual(len(matched), 3)
        self.assertEqual(misses, [])
        self.assertEqual(matched[0]["url"], "https://a.com/doom")

    def test_resolve_miss(self):
        last = bake.load_last_bake()
        matched, misses = bake.resolve_labels(["99", "nonexistent"], last)
        self.assertEqual(len(matched), 0)
        self.assertEqual(len(misses), 2)

    def test_label_idempotent_and_flip(self):
        bake.cmd_label("like", ["1"])
        bake.cmd_label("like", ["1"])      # again -> still one row
        rows = bake.read_labels()
        self.assertEqual(len([r for r in rows if r["url"] == "https://a.com/doom"]), 1)
        self.assertEqual(rows[0]["label"], "like")
        bake.cmd_label("dislike", ["1"])   # flip
        rows = bake.read_labels()
        self.assertEqual([r for r in rows if r["url"] == "https://a.com/doom"][0]["label"], "dislike")


# --------------------------------------------------------------- WIRE1 output

class TestWire1Output(unittest.TestCase):
    def test_render_parseable_and_capped(self):
        items = [{"tag": ("TECH", "RETRO", "CONCEPT")[i % 3], "title": "Headline %d" % i,
                  "text": "Para one about #hashtags should be escaped.\n\nPara two.", "src": "ex%d.com" % i}
                 for i in range(13)]
        text = bake.render(items)
        parsed = mini_parse_wire1(text)
        self.assertIsNotNone(parsed)
        self.assertEqual(len(parsed["items"]), bake.MAX_ITEMS)   # hard cap holds
        self.assertEqual(parsed["items"][0][0], "TECH")
        self.assertEqual(parsed["items"][0][1], "Headline 0")
        for _, _, body in parsed["items"]:
            for line in body.split("\n"):
                self.assertFalse(line.startswith("#"), "body line must not start with #")

    def test_source_footer_present(self):
        items = [{"tag": "TECH", "title": "H", "text": "Body text.", "src": "hackaday.com"}]
        text = bake.render(items)
        self.assertIn("— hackaday.com", text)


class TestInterleaveCap(unittest.TestCase):
    def _cand(self, feed, i):
        return {"feed_name": feed, "title": "%s-%d" % (feed, i), "tag": "TECH",
                "source": feed, "link": "https://%s/%d" % (feed, i)}

    def test_under_cap_unchanged(self):
        cands = [self._cand("a", i) for i in range(3)]
        self.assertEqual(bake._interleave_cap(cands, 10), cands)

    def test_cap_is_fair_across_feeds(self):
        # one busy feed (20) + two quiet (2 each); cap 9 must not be all-busy
        cands = ([self._cand("busy", i) for i in range(20)]
                 + [self._cand("q1", i) for i in range(2)]
                 + [self._cand("q2", i) for i in range(2)])
        out = bake._interleave_cap(cands, 9)
        self.assertEqual(len(out), 9)
        feeds = {c["feed_name"] for c in out}
        self.assertEqual(feeds, {"busy", "q1", "q2"})           # every feed represented
        self.assertLessEqual(sum(c["feed_name"] == "busy" for c in out), 7)
        # order within a feed preserved (newest first)
        busy = [c["title"] for c in out if c["feed_name"] == "busy"]
        self.assertEqual(busy, sorted(busy, key=lambda t: int(t.split("-")[1])))


class TestClientTimeout(unittest.TestCase):
    def test_timeout_not_retried(self):
        client = scorer.OllayaClient(model="laya:en")
        calls = {"n": 0}

        def boom(method, path, payload=None):
            calls["n"] += 1
            raise socket.timeout("read timed out")

        client._request = boom
        with self.assertRaises(scorer.OllayaError) as ctx:
            client.decide("state", {"d": {"type": "noul", "instructions": "?"}})
        self.assertIn("timeout", str(ctx.exception))
        self.assertEqual(calls["n"], 1)      # NOT retried (would pile load on a slow box)

    def test_connection_error_retried_once(self):
        client = scorer.OllayaClient(model="laya:en")
        calls = {"n": 0}

        def boom(method, path, payload=None):
            calls["n"] += 1
            raise __import__("urllib").error.URLError("connection refused")

        client._request = boom
        with self.assertRaises(scorer.OllayaError):
            client.decide("state", {"d": {"type": "noul", "instructions": "?"}})
        self.assertEqual(calls["n"], 2)      # original + one retry


if __name__ == "__main__":
    unittest.main()
