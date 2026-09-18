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

- **On the home server**, `server/bake.py` fetches your feeds, keeps the newest
  per category, interleaves them, hard-caps at **10**, and — for each one —
  **fetches the article and extracts the readable full text**, baking it all
  into one `wire.txt` in the `WIRE1` line-format. Curation lives here (edit
  `FEEDS`); extraction is best with `pip install trafilatura` (see below).
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
