#!/usr/bin/env python3
"""Hostile publisher for the M2 soak.

    python tools/chaos.py --host 127.0.0.1 --id <device-id> --duration 86400

The corpus comes from `fixtures.py`, the same one the unit tests use. That is
deliberate: anything this finds becomes a permanent regression test by adding
one line there, rather than by trying to reproduce a race against a live broker.

Reproducible by seed, so a failure at hour nineteen can be replayed.

Pass/fail for the soak is not "it did not crash". It is:

* `availability` never flipped to `offline` -- the badge never dropped off;
* a final valid update on `slot/canary` still lights an edge -- it is not
  wedged, just alive;
* no cap was bypassed, which is the one thing this cannot check by itself and
  which needs a human watching the ring.

Dev-only, `export-ignore`d.
"""

import argparse
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import paho.mqtt.client as paho
except ImportError:
    raise SystemExit("needs paho-mqtt: python -m pip install paho-mqtt")

import fixtures  # noqa: E402  - after the sys.path fix above

PROFILES = ("junk", "oversize", "flood", "strobe", "all")


class Chaos:
    def __init__(self, client, root, rng):
        self.client = client
        self.root = root
        self.rng = rng
        self.sent = 0
        self.went_offline = False

    def publish(self, suffix, payload, retain=False):
        self.client.publish("%s/%s" % (self.root, suffix), payload, retain=retain)
        self.sent += 1

    # -- profiles ------------------------------------------------------------

    def junk(self):
        name, payload = self.rng.choice(fixtures.HOSTILE_SLOT_PAYLOADS)
        self.publish("slot/junk%d" % self.rng.randint(0, 5), payload)

    def strobe(self):
        """The one that matters most: nothing here may make the ring flash."""
        name, payload = self.rng.choice(fixtures.HOSTILE_LED_PAYLOADS)
        self.publish("led", payload)

    def oversize(self):
        choice = self.rng.randint(0, 3)
        if choice == 0:
            self.publish("slot/big", b'{"state":"working","label":"' + b"A" * 4096 + b'"}')
        elif choice == 1:
            self.publish("slot/huge", b"x" * 100000)
        elif choice == 2:
            self.publish("text", b'{"msg":"' + b"B" * 8192 + b'"}')
        else:
            # Two hundred distinct retained slots, then a forced reconnect, is
            # the out-of-memory test for the retained rebuild. Only safe on a
            # broker whose retained store you can wipe -- never a public one.
            for i in range(200):
                self.publish("slot/bulk%03d" % i, b'{"state":"working"}', retain=True)

    def flood(self):
        for i in range(200):
            self.publish("slot/flood%d" % (i % 8), b'{"state":"working"}')

    def valid(self):
        self.publish("slot/canary", fixtures.VALID_SLOT)

    def clear_bulk(self):
        for i in range(200):
            self.publish("slot/bulk%03d" % i, b"", retain=True)
        self.publish("slot/canary", b"", retain=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--id", required=True, help="the badge's device id")
    ap.add_argument("--prefix", default="edgewise")
    ap.add_argument("--duration", type=int, default=60, help="seconds")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--profile", choices=PROFILES, default="all")
    ap.add_argument("--rate", type=float, default=20.0,
                help="generator calls/second, NOT messages/second -- the "
                     "burst generators send tens of messages each, so 20 "
                     "here measured ~970 msg/s against a badge")
    args = ap.parse_args()

    root = "%s/%s" % (args.prefix, args.id)
    rng = random.Random(args.seed)

    client = paho.Client(paho.CallbackAPIVersion.VERSION1, client_id="edgewise-chaos")
    state = {"offline_seen": False, "events": 0}

    def on_message(_c, _u, msg):
        if msg.topic.endswith("/availability"):
            if msg.payload == b"offline":
                state["offline_seen"] = True
                print("!! availability went offline at %.0fs" % (time.time() - start))
        elif msg.topic.endswith("/event"):
            state["events"] += 1

    client.on_message = on_message
    client.connect(args.host, args.port, keepalive=60)
    client.subscribe(root + "/availability")
    client.subscribe(root + "/event")
    client.loop_start()

    chaos = Chaos(client, root, rng)
    generators = {
        "junk": [chaos.junk],
        "oversize": [chaos.oversize],
        "flood": [chaos.flood],
        "strobe": [chaos.strobe],
    }
    if args.profile == "all":
        pool = [g for gs in generators.values() for g in gs]
    else:
        pool = generators[args.profile]

    start = time.time()
    deadline = start + args.duration
    interval = 1.0 / args.rate
    last_report = start

    print("chaos: %s -> %s for %ds (seed %d, profile %s)"
          % (args.host, root, args.duration, args.seed, args.profile))
    try:
        while time.time() < deadline:
            rng.choice(pool)()
            # A valid message every so often, so "is it still alive" stays
            # answerable throughout rather than only at the end.
            if rng.random() < 0.02:
                chaos.valid()
            time.sleep(interval)
            if time.time() - last_report >= 30:
                last_report = time.time()
                print("  %5.0fs  sent=%-8d events=%-5d offline_seen=%s"
                      % (time.time() - start, chaos.sent, state["events"],
                         state["offline_seen"]))
    except KeyboardInterrupt:
        print("interrupted")

    print("cleaning up retained test slots")
    chaos.clear_bulk()
    time.sleep(1)
    client.loop_stop()
    client.disconnect()

    print("\n--- soak result ---")
    print("messages sent      : %d" % chaos.sent)
    print("badge events seen  : %d" % state["events"])
    print("availability offline: %s" % state["offline_seen"])
    if state["offline_seen"]:
        print("FAIL: the badge dropped off the broker during the soak")
        return 1
    print("PASS (subject to a human confirming the ring never exceeded the caps)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
