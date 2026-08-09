# Edgewise — notes for Claude

A desk status board for the EMF Tildagon badge. Each edge of the hexagon is one
job; anything that can send an MQTT message can drive it, and button presses
come back out as MQTT.

`EDGEWISE_SPEC.md` is the brief and the milestone plan. `controls.md` is the
single source of truth for input. `docs/protocol.md` is the wire format.

## Make the badge tell the user you are done

**Do this if it is not already set up.** The user is often away from the
machine; that is the entire point of the project. Hooks in
`~/.claude/settings.json` are run by the harness, so this works for every
session without anything having to remember it.

```sh
mkdir -p ~/.config/edgewise
cat > ~/.config/edgewise/env <<'EOF'
EDGEWISE_ID="<26-char device id>"     # several badges: separate with spaces
EDGEWISE_BROKER=<host or ip>
EOF
chmod 600 ~/.config/edgewise/env

adapters/shell/edgewise-pub.sh --check          # lights the screen for 5s
adapters/claude-code/install-hooks.sh --user --yes
```

The device ID is on the badge under **Settings → Device ID**. `--user` installs
for every project; drop it for this project only. `--yes` skips the
confirmation but still prints the diff and still backs up.

After that, with no further effort: `UserPromptSubmit` → amber, `Notification`
→ cyan flashing, `Stop` → green, `SessionEnd` → the edge clears. The slot is
named after the project directory, so each checkout gets its own edge.

The hooks embed an absolute path to this checkout. **If the repo moves, re-run
the installer.**

`Notification` only fires when Claude Code raises one. If you are blocked on a
decision and want the badge to say so, publish it yourself:

```sh
adapters/shell/edgewise-pub.sh "$(basename "$PWD")" needs_you "waiting on a decision"
```

**On Windows the hook command must name an interpreter** — the editor runs hooks
through `cmd`, which exits 0 on a `.sh` and does nothing, so the hook looks
installed and fires into a void. `install-hooks.sh` handles this; if you write
anything similar, test it by running the command exactly as `settings.json`
spells it from a shell the editor would use, never from Git Bash.

## Poking the badge from anything else

The badge answers HTTP itself (`httpd.py`), for callers that cannot speak MQTT.
Off by default: Settings → Device ID → HTTP access, which then shows the address
and token.

```sh
curl -H "X-Edgewise-Token: <token>" "http://<badge>:8420/slot/build?state=done"
curl "http://<badge>:8420/health"          # no token
```

`GET /wait/<slot>` holds the request open until that slot is acknowledged or
denied, so a tap becomes an exit code. Read `docs/security.md` before treating
that as an authorisation — the badge does not know who pressed it.

**The rule to keep:** `httpd.route` builds a dict and hands it to
`security.parse_*`, the same validators MQTT uses. Never add a limit, an enum or
a cap to `httpd.py` — a second transport with its own idea of the rules is a
second set of bugs, findable only on whichever door an attacker picks.

There was an HTTP-to-MQTT bridge in `adapters/http/` until v0.11.0. The badge
does the job directly now; it was deleted rather than left to rot.

## Running it

```sh
python -m unittest discover -t . -s tests      # no dependencies
```

Everything except the LED hardware and the touch ring runs under CPython, and
that is deliberate: the pure modules (`model`, `layout`, `security`, `ledfx`,
`gestures`, `prefs`, `clock`) import nothing from the firmware so the
hostile-input corpus and the strobe sweep can run in CI.

`tools/README.md` covers the simulator and the dev broker, including the
Windows split-brain Mosquitto trap. **The simulator cannot tell you anything
about the LED ring, the 2026 touch ring, or blocking cost** — it stubs all
three. Several bugs in this repo's history were invisible until real hardware.

## House rules

- **No new dependencies**, on the badge or in the tests.
- **No allocation in `update()`, `draw()` or the LED frame.** Twenty times a
  second, tuples add up until the collector shows in the frame time. This has
  bitten twice; see the v0.8.0 and v0.9.0 commits.
- **The 3 Hz flash cap is not negotiable.** `period_ms()` is the only way to
  turn a speed into a period. `test_ledfx.py` greps this module for code that
  divides by speed itself, and sweeps every effect counting luminance
  transitions.
- **All inbound MQTT is untrusted.** Parsers return `None`, never raise.
- **Events are never retained.** A retained `ack` re-approves forever.
- **Never promise something on screen that no handler implements.** This
  happened three times (settings that did not exist, a highlight nothing drew,
  a detail view offering ack and deny while handling neither).
  `tests/test_docs.py` now checks it, along with docs-vs-code drift.

## Hardware facts, learned the hard way

Each of these cost a release to find. Do not re-derive them.

- **The ring is at hardware indices 1–12** (`LED_OFFSET = 1`). The firmware's
  pattern app writes `range(12)`, which *looks* like 0–11, but it wraps the ring
  in `ComposedNeoPixel(leds, -1)`. Confirmed on a badge: all twelve light, no
  gap.
- **The badge is a hexagon standing on a point**, so twelve o'clock is a corner.
  Edge centres are at 30° + k·60° (`views.EDGE_CENTRE_OFFSET_DEG`).
- **MicroPython counts seconds from 2000-01-01**, not 1970. `clock.wall_seconds`
  normalises it by asking `gmtime(0)`. Publishing the raw value made events read
  as 1996.
- **Nothing in Tildagon OS sets the clock** except the OTA updater, so the badge
  believes it is 1970 until `timesync` asks NTP itself.
- **`PatternDisable` stops the OS pattern painting, not working.** Its task
  keeps computing frames at the pattern's fps — 30 for the default rainbow — on
  the same scheduler. Take the ring with `PatternSet(OffPattern)` too.
- **LED work belongs in `background_update()`**, which the firmware runs for
  every app, and which is where the OS drives its own ring. Inline in the
  foreground loop it becomes button latency.
- **Signed mode is real** (`signing.py`), and HMAC is written out from
  `hashlib.sha256` because MicroPython has the hash and not the `hmac` module.
  The canonical form is named fields in a fixed order, never the JSON bytes --
  three implementations have to agree (badge, `edgewise_pub.py`,
  `edgewise-pub.sh` via openssl) and a test compares them.
- **The badge reports its own timing** to `<root>/stats` every ten seconds:
  loop rate, worst iteration, free heap, which LED write path bound. Measure
  before optimising; four guesses from the desktop were wrong.

## Releasing

The app store reads the latest GitHub release and takes about fifteen minutes.

1. Bump `version` in `tildagon.toml` **and** `VERSION` in `app.py` — a test
   asserts they match.
2. `python -m unittest discover -t . -s tests`
3. Commit, push, `gh release create vX.Y.Z --title vX.Y.Z --notes "..."`

`.gitattributes` keeps `docs/`, `adapters/`, `tests/` and `tools/` out of the
tarball the badge downloads. Do not add `boards/` or `LICENSE` to it.
