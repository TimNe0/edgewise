# Shell, make and cron

The base adapter. Every other adapter in this directory is a thin layer over
`edgewise-pub.sh`, so this is the one worth reading.

## Setup, once

Create `~/.config/edgewise/env`:

```sh
mkdir -p ~/.config/edgewise
cat > ~/.config/edgewise/env <<'EOF'
EDGEWISE_ID=PUT-YOUR-26-CHAR-DEVICE-ID-HERE
# Two badges? EDGEWISE_ID="AAAA...  BBBB..."
EDGEWISE_BROKER=192.168.1.10
# EDGEWISE_PORT=1883
# EDGEWISE_PREFIX=edgewise
# EDGEWISE_USER=badge
# EDGEWISE_PASS=hunter2
# EDGEWISE_LABELS=hash      # see "Privacy" below
EOF
chmod 600 ~/.config/edgewise/env
```

The device ID is on the badge under **Settings → Device ID** (with a QR code, if
your phone is nearer than your keyboard). Then:

```sh
./edgewise-pub.sh --check
```

That prints the resolved config and puts a five-second message on the badge's
screen. If nothing appears, the config is wrong in a way that cannot fail
loudly — the broker happily accepts a publish to a topic nobody is subscribed
to, so a mistyped device ID looks exactly like a broker outage.

Put both scripts somewhere on your `PATH` if you want them everywhere:

```sh
install -m 755 edgewise-pub.sh run-and-report ~/.local/bin/
```

## Publishing

```sh
edgewise-pub.sh kiln needs_you "door open?"
edgewise-pub.sh build working
edgewise-pub.sh build done
edgewise-pub.sh build error "3 tests failed"
edgewise-pub.sh --clear build
edgewise-pub.sh --text "bins tonight" alert
edgewise-pub.sh --weather rain 12 40      # condition, degrees, chance of rain
edgewise-pub.sh --weather clear 21
```

The weather shows in the middle of the dashboard under the clock, and steps
aside the moment a slot needs you. Conditions are `clear` `part` `cloud`
`rain` `snow` `storm` `fog` `wind`; temperature and chance of rain are both
optional. Run it from cron against whatever weather API you already use --
the badge has no HTTP client and never fetches anything itself, so the API
key stays on a machine that can keep one.

Slot names are lowercased, truncated to 16 characters, and have `/ # + space`
replaced with `-`, because a slot name is one level of an MQTT topic.

## Every variable

| | |
|---|---|
| `EDGEWISE_ID` | required. The 26-character device ID from Settings → Device ID. **Several badges: separate them with spaces** and every one gets the same board |
| `EDGEWISE_BROKER` | required. Hostname or IP |
| `EDGEWISE_PORT` | default 1883 |
| `EDGEWISE_PREFIX` | default `edgewise`. Must match Settings → Broker → Prefix |
| `EDGEWISE_USER` / `EDGEWISE_PASS` | broker credentials, if it has any |
| `EDGEWISE_TLS` | `1` to connect over TLS with the system CA path |
| `EDGEWISE_TTL` | default 3600. How long an edge survives without an update |
| `EDGEWISE_EDGE` | `0`–`5`. Pins a slot to one edge instead of letting the layout move it |
| `EDGEWISE_LABELS` | `name` (default) or `hash` — see Privacy below |
| `EDGEWISE_HMAC_KEY` | signing key, when the badge is in signed mode. Needs `openssl`; without it the publisher refuses rather than sending unsigned |
| `EDGEWISE_MOSQUITTO` | full path to `mosquitto_pub`, if it is not on `PATH`. Searched automatically in Program Files, homebrew and /usr/local/bin — a hook fired by an editor does not inherit the PATH you set it up in |
| `EDGEWISE_ENV` | path to the env file, if not `~/.config/edgewise/env` |
| `EDGEWISE_PUB` | path to the publisher, for `run-and-report` and the other adapters |
| `EDGEWISE_TEMP_UNIT` | `C` (default) or `F`, for `--weather` |
| `EDGEWISE_WEATHER_TTL` | default 10800 (three hours). Stale weather expires rather than lying |
| `EDGEWISE_RUN_TTL` | default 86400. The TTL `run-and-report` uses while a command is still running, so a nine-hour job does not expire mid-flight |

Anything set in the environment beats the env file, so a one-off override is
`EDGEWISE_EDGE=0 edgewise-pub.sh kiln working`.

The file is **parsed, not sourced** — it is data, not a script, and running it
would hand code execution to anyone who could write it. Only the names above are
recognised; anything else in the file is ignored. `chmod 600` it: it holds a
broker password, and the adapters will warn you if it is readable by others.

## Wrapping a command

```sh
run-and-report backup -- restic backup /home
run-and-report nightly -- make -C ~/src/thing test
```

`working` while it runs, `done` on exit 0, `error` with the exit status
otherwise. The command's stdout, stderr and exit status all pass straight
through, so this is safe to put in front of something already in a Makefile.

In crontab, cron's environment is nearly empty — it sets `HOME` but does not
source your profile, so `~/.config/edgewise/env` is found but `~/.local/bin` is
not on the `PATH`:

```cron
0 3 * * * /home/you/.local/bin/run-and-report backup -- /usr/local/bin/nightly-backup
```

In a Makefile:

```make
REPORT ?= run-and-report

test:
	$(REPORT) tests -- pytest -q
```

## No mosquitto-clients?

`edgewise_pub.py` is the same tool over `paho-mqtt`, with the same arguments,
the same env file and byte-identical payloads:

```sh
pip install paho-mqtt
./edgewise_pub.py kiln needs_you "door open?"
```

Point `run-and-report` at it with `EDGEWISE_PUB=/path/to/edgewise_pub.py`.

## Signed mode

If the badge has **Require signed** on, set the same key here:

```sh
EDGEWISE_HMAC_KEY=correct-horse-battery-staple
```

Slot updates are then signed automatically. `--text` and `--weather` cannot sign
from the shell yet and will refuse rather than send something the badge will
silently drop — use `edgewise_pub.py`, which signs everything.

Signing needs `openssl` for the HMAC. Without it, a configured key makes the
publisher exit 2 rather than publish unsigned: a message a badge in signed mode
throws away looks exactly like a broken badge, and finding that out at the badge
is much slower than being told here.

## Privacy

Slot names are usually project names, and on a public broker **the topic itself
leaks them** — hiding the label would not be enough. `EDGEWISE_LABELS=hash`
replaces the name with a 6-character digest everywhere, topic included, and
sends no label and no message at all. The badge shows six anonymous edges; you
know which is which, and nobody watching the broker does.

Use it for screenshots, demos, conference Wi-Fi, and any broker you do not own.
[docs/security.md](../../docs/security.md) has the rest.

## Failure behaviour, on purpose

`edgewise-pub.sh` exits 0 when the publish fails, and warns on stderr. It is
wrapped around builds, backups and git hooks; a status light that can fail your
build is worse than no status light. It exits 2 only when the arguments were
wrong, which is a bug in your script rather than a fact about the network.

Publishes are capped at five seconds where `timeout(1)` exists, so a broker
that accepts the connection and then goes quiet cannot wedge a hook.
