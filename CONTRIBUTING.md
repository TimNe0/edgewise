# Contributing

Bug reports, board profiles and adapters are all welcome. So is telling us the
documentation is wrong — this repo treats a README that does not work as a
defect rather than a chore.

Security problems go through [SECURITY.md](SECURITY.md), not the issue tracker.

## Getting set up

```sh
git clone https://github.com/TimNe0/edgewise
cd edgewise
python -m unittest discover -t . -s tests -v
```

No dependencies. Everything except the LED hardware and the touch ring runs
under CPython, and that is not an accident — the pure-logic modules (`model`,
`layout`, `security`, `ledfx`, `gestures`) import nothing from the firmware
precisely so the hostile-input corpus and the strobe sweep can run in CI.

The simulator and dev broker are in [tools/](tools/README.md). Read the part
about what the simulator cannot tell you before you trust it.

## House style

**Comments explain why, not what.** The code says what it does. A comment earns
its place by recording a decision, a measurement, or a trap someone already fell
into — several of the longer ones exist because the obvious implementation was
tried first and did not work on the hardware.

**No new dependencies.** Not on the badge, not in the tests. MicroPython is a
small target and every import is memory.

**No per-frame allocation in `update()` or `draw()`.** Twenty times a second,
tuples add up until the garbage collector is visible in the frame time.

**Nothing blocks the render loop.** The MQTT worker owns the socket and never
shares it; the UI task never touches the client. If you find yourself wanting a
lock across a socket read, read the module docstring in `mqtt_link.py` first.

## Things that are not up for negotiation

**The 3 Hz flash cap.** It is a photosensitive-seizure precaution. It is
enforced structurally — `period_ms()` is the only way to turn a speed into a
period and it will not return anything shorter — so an effect cannot bypass it
by accident. A patch that divides by speed itself will be rejected by
`test_ledfx.py`, which greps for exactly that and separately sweeps every effect
at every speed counting luminance transitions. Treat that as the most important
test here.

**All inbound is untrusted.** Every parser returns `None` rather than raising.
Every string is length-capped and stripped to printable ASCII. Nothing received
over MQTT is ever evaluated or executed. New fields get validated in
`security.py` like all the others, and unknown fields stay ignored rather than
rejected.

**Events are never retained.** A retained `ack` would be re-delivered to every
future subscriber, which for the approve-from-badge flow means one approval
authorising an unbounded number of actions.

**Installers stay short, reviewable and offline.** Under sixty lines, no sudo,
no network fetches, a printed diff and a backup before writing.

## Adding a board profile

A new badge revision should cost one file in `boards/` and no core changes: the
LED count, the index→edge map, whether there is a touch ring and its pad
geometry, and sensible defaults. If your board needs a change outside `boards/`,
say so in the PR — that is a design bug worth fixing rather than working around.

If you cannot get the LED map from firmware source, the in-app calibrate screen
answers it by hand, and the resulting map is what a profile should contain.

## Adding an adapter

Follow what the existing ones do — the conventions are listed in
[adapters/README.md](adapters/README.md), and they are all consequences of one
rule: **a status light must never be able to break the thing it reports on.**
Publish through `shell/edgewise-pub.sh` rather than building topics yourself,
exit 0 when the broker is unreachable, bound every wait, and honour
`EDGEWISE_LABELS=hash`.

End the adapter's README with a copy-paste block you have actually run.

## Pull requests

Small and focused. Say what you tested it on — CPython only, the simulator, or a
real badge, and which board. "Not tested on hardware" is a fine thing to write
and a much better thing to write than nothing; several bugs here only appear on
congested Wi-Fi and never on a desk.

Line endings matter more than usual: `.gitattributes` pins shell scripts and
sources to LF, because a CRLF after a shebang makes Linux report `bad
interpreter: no such file or directory` and nobody guesses why. There is a test
for it.
