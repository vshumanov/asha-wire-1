# Build & packaging notes

Target: **Nokia Asha 210**, Series 40 — **MIDP 2.0 / CLDC 1.1**.

## Build the MIDlet (Docker — verified path)

A MIDlet needs CLDC-range bytecode (modern JDKs can't emit it) and a
preverification pass (adds the CLDC StackMap attributes the phone's VM needs).
This does both in a JDK 8 container using ProGuard's `-microedition` mode:

```bash
# one-time: fetch ProGuard next to the repo
curl -sSL -o /tmp/pg.zip \
  https://github.com/Guardsquare/proguard/releases/download/v7.4.2/proguard-7.4.2.zip
mkdir -p /tmp/pg && unzip -q /tmp/pg.zip -d /tmp/pg

# build
docker run --rm -v "$PWD":/src -v /tmp/pg:/pg \
  eclipse-temurin:8-jdk bash /src/build/docker-build.sh
```

Output: `dist/wire.jar` + `dist/wire.jad`. Copy **both** into the same folder on
the phone (or SD card) and open the `.jad` to install. The build prints the
class version (major 47 = CLDC) and confirms StackMap attributes were written;
the jar contains only `wire.*` classes.

The `stubs/` tree is a compile-only stand-in for the `javax.microedition.*` API
(lcdui, MIDlet, `io` incl. `HttpConnection`, and `rms`) with **spec-correct
constant values**, so constant-inlining is correct and no real device jars are
needed to compile. Stubs are never shipped.

## Validate the pure layer (no phone)

The parsing + wrapping core is pure, MIDP-free Java:

```bash
javac -d bin src/wire/TextMeasure.java src/wire/Utf8.java src/wire/Wrap.java \
             src/wire/Item.java src/wire/Digest.java test/DigestTest.java
java -cp bin DigestTest        # "ALL CHECKS PASS"
```

It verifies WIRE1 parsing (magic, date, tags, body joining, CRLF, the 10-item
hard cap) and word-wrap (line widths, word/blank-line preservation).

## Permissions

The JAD declares `javax.microedition.io.Connector.http`. The phone prompts once
for network access on first pull; "Always allow" stops the prompts. No file or
SD-card permission is used — the digest lives in RMS.

## On-server layout

```
/srv/wire/wire.txt     <- baked each morning by server/bake.py (cron)
                          served with: python3 -m http.server 9009
```
The phone's `Net.URL` must point at `http://<server-host>:9009/wire.txt`.
```
