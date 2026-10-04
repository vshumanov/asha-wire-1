# Wire — the day's tech wire for the Nokia Asha 210

A J2ME MIDlet (MIDP 2.0 / CLDC 1.1) for the **Nokia Asha 210**. Wake up, join
the home wifi, pull the day's **10** headlines — tech, retro gaming, interesting
new tech concepts — read them, and you're out. Tomorrow's pull overwrites
today's. No feed, no archive, no "load more."

## What this is (and the rule it bends)

The rest of the Asha suite is strictly offline. Wire is the one **documented
bend** of that rule (see the vault's `asha-philosophy`): a single, *deliberate*
pull from **your own home server on your own LAN** — not the cloud, not an
account, not a background feed. The network is touched once, when you press
Refresh with your coffee; everything after that reads from on-device storage
(RMS), so you can read on the couch with wifi off. The sacred rule still holds:
it is **bounded and finishable** — ten items, then nothing more.

## The split (same idea as the Reader)

The phone is dumb; the server does the messy web work — exactly like the
Reader's desktop converter.

- **On the home server**, `server/bake.py` fetches your feeds, **scores every
  candidate with a local decision model** (see *Taste ranking* below), ships the
  best that clear a bar — hard-capped at **10** — and, for each one, **fetches
  the article and extracts the readable full text**, baking it all into one
  `wire.txt` in the `WIRE1` line-format. Curation lives here (edit `FEEDS` and
  `interests.md`); extraction is best with `pip install trafilatura` (see below).
- **On the phone**, Wire does one HTTP GET of that file over the LAN, stores it
  in RMS, and shows a **headline index** you pick from. Selecting a headline
  opens the **full article** to read; back returns to the index.

## Using it

- **Index** (the home screen): up to 10 headlines, tagged `[TECH]` / `[RETRO]` /
  `[CONCEPT]`. Select one to read. Softkey: **Refresh** (pull today's),
  **Diagnostics**, **Exit**.
- **Reading a headline:** you read the **full article** here, not a blurb.
  `8`/down/`5` = read on (page down, then roll to the next piece at the end),
  `2`/up = page up, `6`/right = next item, `4`/left = previous. Past the last
  item you're returned to the index — that wall is the point. **Back** = index.
- **No links open the browser.** A source's name is shown as text, never a
  tappable URL: opening the stock browser is the one thing that would break the
  calm.

## Set up the server

```bash
# 0. (recommended) best-quality article extraction, in a venv (Debian has no
#    system pip and blocks system-wide installs; a venv sidesteps both).
sudo apt install -y python3-venv
python3 -m venv /srv/wire/venv
/srv/wire/venv/bin/pip install trafilatura   # falls back to stdlib + blurb if skipped

# 1. point the feeds at your taste
$EDITOR server/bake.py        # edit the FEEDS dict

# 2. bake this morning's digest (fetches + extracts each article's full text).
#    Use the venv python if you did step 0; plain python3 works too (cruftier).
/srv/wire/venv/bin/python3 /srv/wire/bake.py -o /srv/wire/wire.txt

# 3. serve the folder on the LAN (plain python3 -- no dependencies)
cd /srv/wire && python3 -m http.server 9009

# 4. cron it, e.g. 06:30 daily
# 30 6 * * *  /srv/wire/venv/bin/python3 /srv/wire/bake.py -o /srv/wire/wire.txt
```

A ready-made `server/wire.txt` is included so you can test the phone before
wiring up feeds.

## Taste ranking (local decision model)

By default the bake no longer ships "newest 5 per category". It **scores every
candidate with a local decision model** and ships the best that clear a bar —
so the ten reflect taste, not recency. The model is `laya:en` (Convai's 421M
ModernBERT decision head, Apache-2.0) served by **[Ollaya](https://ollaya.dev)**
("Ollama for decision models"). It runs entirely on the Pi: no cloud, no keys,
no text generation — laya scores a fixed set of typed answers in one forward
pass, so there is nothing to hallucinate. If Ollaya is down it falls back to the
old newest-first curation, so a bad model day still yields a wire.

**How it scores.** For each candidate the bake sends the title + source + feed
blurb (never the full article) and asks four batched questions: `interest`
(a 5-level score built from your `interests.md`), `topic` (TECH / RETRO /
CONCEPT / OFFTOPIC), `hype` and `depth`. The final 0–10 score is the expected
interest, plus a depth bonus, minus a hype penalty, plus the per-source weight.
Anything the model calls OFFTOPIC (news, politics, finance, crypto, …) is
hard-rejected — that is how current-events leakage from HN/aggregators is kept
out. Constants live at the top of `server/scorer.py`; the threshold is 6.0.

### Install Ollaya + laya on the Pi

Ollaya ships a prebuilt **Linux arm64** binary, but it needs **glibc ≥ 2.38** —
that means **Raspberry Pi OS *Trixie* (Debian 13) or Ubuntu 24.04+** on the Pi 4B.
On older Pi OS (Bookworm, glibc 2.36) use the CPU **Docker** image instead.

```bash
# A) native (Pi OS Trixie / Ubuntu 24.04+, arm64):
curl -fsSL https://ollaya.dev/install.sh | sh
ollaya pull laya:en            # ~850 MB; ModernBERT-large, onnxruntime, CPU-only on the Pi

# B) or Docker (any Pi OS):
docker run -d --restart=always -p 127.0.0.1:11435:11435 \
  -v ollaya:/root/.ollaya --name ollaya ghcr.io/ollaya-dev/ollaya
docker exec ollaya ollaya pull laya:en
```

laya:en needs **~0.85–1.5 GB RAM** resident while loaded (it auto-unloads after
a few idle minutes), so a 4 GB Pi 4B is fine. Expect a second or two per
candidate on the Pi's CPU — the bake is allowed to be slow.

Run Ollaya as a service so cron can rely on it (skip if you used Docker A/B with
`--restart=always`):

```ini
# /etc/systemd/system/ollaya.service
[Unit]
Description=Ollaya decision-model server
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/local/bin/ollaya serve
Restart=always
RestartSec=3
# Environment=OLLAYA_HOST=0.0.0.0:11435   # only to serve other LAN machines

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl enable --now ollaya
```

The bake talks to `http://127.0.0.1:11435` by default (override with
`OLLAYA_HOST`; set `OLLAYA_API_KEY` only if you put auth in front of it). To try
another model, `ollaya pull decider` (or `gliclass`, `laya:multilingual`) and
set `WIRE_MODEL=decider`. If Ollaya must live on another LAN box, point
`OLLAYA_HOST` at it.

### Cron

```cron
# early, under nice so it never fights the Pi; venv python; -o the served file
0 4 * * *  nice -n 10 /srv/wire/venv/bin/python3 /srv/wire/bake.py -o /srv/wire/wire.txt
```
Read the digest with your coffee; the 04:00 start leaves plenty of margin (a
cold run of 80 candidates is ~40 min at ~30 s each; cached daily runs are
minutes). It has a 3 h hard ceiling and will ship whatever it has ranked rather
than block tomorrow. The bake is quiet on stdout, writes errors to stderr, and
appends a run summary (backend, candidates, cache hits, latency, shipped
titles+scores) to **`server/bake.log`** (last ~14 runs kept).

### Edit your taste, then teach it

- **`server/interests.md`** — plain-prose Likes/Dislikes the `interest` question
  is built from. Editing it re-scores everything on the next bake (its hash is
  part of the score cache key).
- **`server/bake.py --like 3 7`** / **`--dislike 5`** — after reading, label items
  by their number from the last bake (a title or URL substring works too). This
  appends to `server/labels.tsv`. Make it a habit; it is instant.
- **`server/bake.py --eval`** — scores your labels and prints ranking accuracy
  (AUC), mean score per class, the worst misses, and flags any question with no
  signal. `--eval --model decider` compares another model on the same labels.
- **`--dry-run`** prints the ranked table and writes nothing; **`--no-model`**
  forces the legacy newest-first path.

Feeds live in the `FEEDS` list at the top of `server/bake.py` — each has a
`weight` (±1 nudge) you can lean on your favourite sources with.

### Tuning for the Pi (it's CPU-only)

`laya:en` is ModernBERT-large in **fp32 on CPU** — roughly **15–30 s per
candidate** on a Pi 4B, and it loads in ~14 s. So the *cold* first bake is slow;
after that the score cache means only new items are scored and daily runs are
quick. Knobs (all env vars, no code edits):

| var | default | what |
|-----|---------|------|
| `WIRE_MAX_CANDIDATES` | `80` | hard cap on items scored per bake (fairly spread across feeds). ~40 min cold at ~30 s/item; raise for coverage, lower for speed. |
| `WIRE_POOL_PER_FEED` | `8` | newest N pulled per feed before the cap. |
| `WIRE_KEEP_ALIVE` | `30m` | keep laya resident through the run (avoids repeated 14 s reloads). |
| `WIRE_HTTP_TIMEOUT` | `120` | per-request budget; must stay above a single inference time. |
| `WIRE_MODEL` | `laya:en` | try `laya:multilingual` (322M) for a lighter, faster model. |
| `WIRE_MIN_SCORE` | `5.0` | ship floor (0–10). laya scores interest conservatively, so ~5 fills the digest; raise it for a pickier, shorter wire. |
| `WIRE_SEEN_DAYS` | `14` | don't reship a URL within this many days. |

Run it interactively once (`bake.py --dry-run`) to see the live
`scoring N/80  last … med …` heartbeat (real per-inference latency) and the
score spread, then set the cron. The run summary in `bake.log` prints the
`threshold / eligible / score spread (max / #10 / min)` so you can pick a
`WIRE_MIN_SCORE` that ships about ten: set it near the "#10" score you see.

## Point the phone at your server

Edit one line — `Net.URL` in `src/wire/Net.java` — to your server's LAN address,
then rebuild. It's baked, not a setting (tuned-to-me infra, like the Schedule
app's hard-coded specifics):

```java
public static final String URL = "http://homeserver.lan:9009/wire.txt";
```

## Build

See `BUILD.md`. Output is `dist/wire.jar` + `dist/wire.jad`; copy **both** to the
phone and open the `.jad` to install. The phone will prompt once for network
access ("Always allow" stops the prompts) — the network equivalent of the
Reader's one-time file prompt.

## Layout

```
src/wire/      MIDlet + Canvas + RMS/HTTP glue, and the pure model
  Digest.java, Item.java, Wrap.java, Utf8.java, TextMeasure.java   <- pure, tested
  FontMeasure.java, Net.java, Store.java, WireCanvas.java, WireMIDlet.java
test/          DigestTest (desktop, MIDP-free)
server/        bake.py (curation) + a sample wire.txt
stubs/         compile-only javax.microedition.* surface (never shipped)
build/         docker-build.sh + manifest
```
