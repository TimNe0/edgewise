# Development tools

Dev-only. `export-ignore`d, so none of this reaches a badge.

## The broker

Edgewise is developed against a private local Mosquitto, not a public broker.
Public brokers are for one scripted conformance run before release: their
documented restarts are indistinguishable from our own bugs during a soak, and
slot labels are project names.

```
winget install --id EclipseFoundation.Mosquitto -e
mosquitto -v -c tools/mosquitto-dev.conf
```

### Disable the Windows service first

The installer registers Mosquitto as an auto-starting **Windows service**, and
that service listens on `127.0.0.1:1883` using its own config under
`C:\Program Files\mosquitto`. The dev broker in this repo listens on
`0.0.0.0:1883`.

Windows lets both bind at once, because `127.0.0.1` and `0.0.0.0` are different
addresses — and the more specific one wins. So you get a split brain:

| Client | Reaches |
|---|---|
| `mosquitto_pub -h 127.0.0.1` | the **service**, with its own retained store |
| the badge, over the LAN | the **dev broker**, with a different retained store |

Two brokers, no shared state, and every symptom looks like an application bug.
Retained messages "vanish"; a slot set from the desktop never lights an edge.

Stop and disable it once, from an **elevated** shell:

```powershell
Stop-Service mosquitto
Set-Service mosquitto -StartupType Disabled
```

Then confirm exactly one listener, on `0.0.0.0`:

```
netstat -ano | findstr :1883
```

### Firewall

The badge reaches the broker over the LAN, so allow it inbound once:

```powershell
New-NetFirewallRule -DisplayName "mosquitto dev 1883" -Direction Inbound `
  -LocalPort 1883 -Protocol TCP -Action Allow -Profile Private
```

### Smoke test

Retained delivery is the mechanism the whole protocol rests on — the badge
keeps no state that matters and rebuilds the board from retained messages on
reconnect. Check it works before trusting anything else:

```
mosquitto_pub -h <host> -t 'edgewise/TESTID/slot/kiln' -r \
  -m '{"state":"needs_you","label":"kiln","msg":"door open?"}'
mosquitto_sub -h <host> -t 'edgewise/TESTID/slot/+' -v -W 2   # replays it
mosquitto_pub -h <host> -t 'edgewise/TESTID/slot/kiln' -r -n  # empty = delete
mosquitto_sub -h <host> -t 'edgewise/TESTID/slot/+' -v -W 2   # silent
```

Restart the broker between the first two lines to prove `persistence true` is
actually working. Do that against the broker you think you are using — see the
split-brain note above.

## The simulator

Expects `badge-2024-software` cloned as a sibling of this repo:

```
git clone https://github.com/emfcamp/badge-2024-software ../badge-2024-software
powershell -File tools\sim.ps1      # Windows
./tools/sim.sh                      # everything else
```

The simulator is worth using for screen layout, menus and demo timing. It
cannot tell you anything about three things, so do not let it reassure you:

- **the 2026 touch ring** — the pads are stubbed and never fire;
- **blocking cost** — a desktop badly understates it; mbedTLS and ctx
  rasterisation are far slower on an ESP32;
- **the LED ring** — including the index-to-edge mapping, which is what the
  in-app calibrate screen exists to answer.

## Tests

```
python -m unittest discover -t . -s tests -v
```

`make test` does the same, but `make` is not installed on this machine.
