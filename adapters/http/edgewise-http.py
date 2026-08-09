#!/usr/bin/env python3
"""An HTTP front door for the badge, for everything that cannot speak MQTT.

    edgewise-http.py --token hunter2

Webhooks, phone shortcuts, browsers, `curl` in a Makefile, a Grafana alert, a
router's "on new device" action -- none of those can publish MQTT, and all of
them can fetch a URL. This bridges the two, on a machine that is already awake.

    curl -H "X-Edgewise-Token: hunter2" \\
         "http://desk:8420/slot/build?state=working"

    curl -H "X-Edgewise-Token: hunter2" \\
         "http://desk:8420/slot/deploy?state=needs_you&msg=ship+v2"
    curl -H "X-Edgewise-Token: hunter2" --max-time 300 \\
         "http://desk:8420/wait/deploy"        # blocks until you tap the badge

**The badge deliberately does not do this itself.** It has no HTTP client, and
non-MQTT transports are an explicit non-goal: a badge that terminates
connections is a badge with an attack surface, and this runs on hardware that
can afford one.

Needs `paho-mqtt`. Reads the same `~/.config/edgewise/env` as every other
adapter, so it inherits `EDGEWISE_ID` -- including a space-separated list of
badges -- and the broker settings.

## What the token is and is not

It stops the rest of your network from lighting your badge by accident. It is
sent in the clear over plain HTTP, so it is not protection against anyone who
can watch your LAN, and it is not a reason to expose this to the internet. The
threat model is the one in docs/security.md: this is a lamp.

`/wait` is worth a further thought. It turns a tap on a badge into an exit code,
and a script that deploys on exit 0 has made a tap into an authorisation. The
badge does not know who pressed it. Read docs/security.md before wiring it to
anything that spends money or moves a robot.
"""

import argparse
import json
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

STATES = ("working", "needs_you", "done", "error", "info", "clear")
CONDITIONS = ("clear", "part", "cloud", "rain", "snow", "storm", "fog", "wind")

DEFAULT_PORT = 8420
DEFAULT_WAIT_S = 300
MAX_WAIT_S = 900

_ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*$")
_SLOT_OK = re.compile(r"^[A-Za-z0-9._-]{1,16}$")


def load_env():
    """The same env file every other adapter reads."""
    env = {}
    path = os.environ.get(
        "EDGEWISE_ENV", os.path.expanduser("~/.config/edgewise/env"))
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if line.lstrip().startswith("#"):
                    continue
                m = _ENV_LINE.match(line)
                if not m:
                    continue
                value = m.group(2)
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                env[m.group(1)] = value
    except OSError:
        pass
    for key, value in os.environ.items():
        if key.startswith("EDGEWISE_"):
            env[key] = value
    return env


def one(query, name, default=None):
    values = query.get(name)
    return values[0] if values else default


def as_int(value, low, high):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if low <= number <= high else None


def build(path, query):
    """Turn a request into (topic-suffix, payload, retain).

    On a bad request the suffix is None and the second value is the reason.
    Always three values either way -- a function whose shape depends on whether
    it succeeded makes every caller do arity gymnastics to find out.

    Pure, so the whole routing table is testable without a socket or a broker.
    Deliberately strict: the badge ignores anything malformed, which is right
    for an untrusted radio and useless for a caller who has just typo'd a state
    name and would rather be told.
    """
    parts = [unquote(p) for p in path.strip("/").split("/") if p]
    if not parts:
        return None, "no endpoint", None

    if parts[0] == "slot":
        if len(parts) != 2:
            return None, "usage: /slot/<name>", None
        name = parts[1]
        if not _SLOT_OK.match(name):
            return None, "slot names are 1-16 of A-Za-z0-9._-", None
        state = one(query, "state", "info")
        if state not in STATES:
            return None, "state must be one of: %s" % " ".join(STATES), None
        if state == "clear":
            # Empty retained payload: clears the board and the broker's copy.
            return "slot/%s" % name, "", True
        payload = {"state": state, "label": one(query, "label", name)[:16]}
        msg = one(query, "msg")
        if msg:
            payload["msg"] = msg[:64]
        ttl = as_int(one(query, "ttl"), 1, 86400)
        payload["ttl"] = 3600 if ttl is None else ttl
        edge = as_int(one(query, "edge"), 0, 5)
        if edge is not None:
            payload["edge"] = edge
        return "slot/%s" % name, json.dumps(payload), True

    if parts[0] == "text" and len(parts) == 1:
        msg = one(query, "msg")
        if not msg:
            return None, "usage: /text?msg=...", None
        level = one(query, "level", "info")
        duration = as_int(one(query, "duration"), 1, 300)
        return "text", json.dumps({
            "msg": msg[:64],
            "level": level if level in ("info", "alert") else "info",
            "duration": 30 if duration is None else duration,
        }), False

    if parts[0] == "weather" and len(parts) == 1:
        payload = {}
        cond = one(query, "cond")
        if cond is not None:
            if cond not in CONDITIONS:
                return None, "cond must be one of: %s" % " ".join(CONDITIONS), None
            payload["cond"] = cond
        temp = as_int(one(query, "temp"), -99, 99)
        if temp is not None:
            payload["temp"] = temp
        rain = as_int(one(query, "rain"), 0, 100)
        if rain is not None:
            payload["rain"] = rain
        if not payload:
            return None, "usage: /weather?cond=rain&temp=12&rain=40", None
        unit = one(query, "unit", "C")
        payload["unit"] = unit if unit in ("C", "F") else "C"
        ttl = as_int(one(query, "ttl"), 1, 86400)
        payload["ttl"] = 10800 if ttl is None else ttl
        return "weather", json.dumps(payload), True

    return None, "unknown endpoint", None


class Waiters:
    """Callers parked on /wait, and the events that release them.

    A dict of slot name to a list of one-shot boxes. Guarded by a lock because
    the MQTT client's thread delivers events and the HTTP threads consume them.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._waiting = {}

    def park(self, slot):
        box = {"event": threading.Event(), "result": None}
        with self._lock:
            self._waiting.setdefault(slot, []).append(box)
        return box

    def leave(self, slot, box):
        with self._lock:
            boxes = self._waiting.get(slot) or []
            if box in boxes:
                boxes.remove(box)
            if not boxes:
                self._waiting.pop(slot, None)

    def deliver(self, event):
        slot = event.get("slot")
        if not slot:
            return
        with self._lock:
            boxes = self._waiting.pop(slot, [])
        for box in boxes:
            box["result"] = event
            box["event"].set()


class Bridge:
    def __init__(self, env):
        import paho.mqtt.client as mqtt

        self.ids = (env.get("EDGEWISE_ID") or "").split()
        self.host = env.get("EDGEWISE_BROKER")
        self.port = int(env.get("EDGEWISE_PORT", "1883"))
        self.prefix = env.get("EDGEWISE_PREFIX", "edgewise")
        self.waiters = Waiters()
        if not self.ids or not self.host:
            sys.exit("EDGEWISE_ID and EDGEWISE_BROKER must be set "
                     "(see ~/.config/edgewise/env)")

        try:
            self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        except AttributeError:
            self.client = mqtt.Client()
        if env.get("EDGEWISE_USER"):
            self.client.username_pw_set(env["EDGEWISE_USER"],
                                        env.get("EDGEWISE_PASS"))
        if env.get("EDGEWISE_TLS") == "1":
            self.client.tls_set()
        self.client.on_message = self._on_message
        self.client.connect(self.host, self.port, 30)
        for device in self.ids:
            self.client.subscribe("%s/%s/event" % (self.prefix, device))
        self.client.loop_start()

    def _on_message(self, _c, _u, msg):
        try:
            self.waiters.deliver(json.loads(msg.payload))
        except (ValueError, TypeError):
            pass

    def publish(self, suffix, payload, retain):
        for device in self.ids:
            self.client.publish("%s/%s/%s" % (self.prefix, device, suffix),
                                payload, qos=0, retain=retain)


def make_handler(bridge, token):
    class Handler(BaseHTTPRequestHandler):
        server_version = "edgewise"

        def _reply(self, code, body):
            raw = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _authorised(self, query):
            given = self.headers.get("X-Edgewise-Token") or one(query, "token")
            # Constant-ish time: not meaningful against a LAN attacker who can
            # read the header anyway, but free and stops the sloppiest guessing.
            if given is None or len(given) != len(token):
                return False
            return sum(a != b for a, b in zip(given, token)) == 0

        def _handle(self):
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)

            if parsed.path.rstrip("/") in ("/health", ""):
                return self._reply(200, {"ok": True, "badges": len(bridge.ids)})
            if not self._authorised(query):
                return self._reply(401, {"error": "bad or missing token"})

            parts = [p for p in parsed.path.strip("/").split("/") if p]
            if parts and parts[0] == "wait":
                return self._wait(parts, query)

            suffix, payload, retain = build(parsed.path, query)
            if suffix is None:
                return self._reply(400, {"error": payload})
            bridge.publish(suffix, payload, retain)
            return self._reply(200, {"published": suffix,
                                     "badges": len(bridge.ids)})

        def _wait(self, parts, query):
            if len(parts) != 2:
                return self._reply(400, {"error": "usage: /wait/<slot>"})
            slot = unquote(parts[1])
            seconds = as_int(one(query, "timeout"), 1, MAX_WAIT_S) or DEFAULT_WAIT_S
            box = bridge.waiters.park(slot)
            try:
                if box["event"].wait(seconds):
                    return self._reply(200, box["result"])
                # 408, not 200-with-nothing: a caller that treats a timeout as
                # approval is a caller that approves whenever the badge is off.
                return self._reply(408, {"error": "no answer", "slot": slot})
            finally:
                bridge.waiters.leave(slot, box)

        do_GET = _handle
        do_POST = _handle
        do_PUT = _handle

        def log_message(self, fmt, *args):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--listen", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--token", default=os.environ.get("EDGEWISE_HTTP_TOKEN"))
    args = ap.parse_args()

    if not args.token:
        # No default, and no "it is only your LAN". Anything on the network can
        # reach this, and a bridge with no token is a badge anyone can drive.
        sys.exit("--token is required (or set EDGEWISE_HTTP_TOKEN).")

    bridge = Bridge(load_env())
    server = ThreadingHTTPServer((args.listen, args.port),
                                 make_handler(bridge, args.token))
    print("edgewise-http on %s:%d -> %d badge(s) via %s"
          % (args.listen, args.port, len(bridge.ids), bridge.host))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("stopping")


if __name__ == "__main__":
    main()
