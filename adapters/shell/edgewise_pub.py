#!/usr/bin/env python3
"""edgewise_pub -- the same publisher as edgewise-pub.sh, for machines without
mosquitto-clients.

    edgewise_pub.py <slot> <state> [message]
    edgewise_pub.py --clear <slot>
    edgewise_pub.py --text <message> [info|alert]
    edgewise_pub.py --check

Needs `paho-mqtt` (pip install paho-mqtt). Reads the same
~/.config/edgewise/env, understands the same EDGEWISE_* variables, and produces
byte-identical payloads -- the two are kept interchangeable on purpose, so a
README can hand out one command and not care which one you have.

Same exit-status contract as the shell version: 0 whenever the arguments made
sense, including when the publish failed. Usage errors exit 2. A status board
that can break a build is worse than no status board.
"""

import hashlib
import json
import os
import re
import sys

STATES = ("working", "needs_you", "done", "error", "info")

DEFAULTS = {
    "EDGEWISE_PORT": "1883",
    "EDGEWISE_PREFIX": "edgewise",
    "EDGEWISE_TTL": "3600",
    "EDGEWISE_LABELS": "name",
    "EDGEWISE_TLS": "0",
}

# Matches the shell version's `. $ENV_FILE` closely enough for a file of
# KEY=value lines, without giving a config file the power to run commands --
# which is a property worth having even though the file is your own.
_ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*$")


def load_env():
    env = dict(DEFAULTS)
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
    # A real environment variable beats the file, so a one-off override works
    # the same way it does in the shell version.
    for key, value in os.environ.items():
        if key.startswith("EDGEWISE_"):
            env[key] = value
    env["_path"] = path
    return env


def clean(text, limit):
    """Printable ASCII, collapsed spaces, truncated -- what the badge will do
    to this anyway. Doing it here too means what you see in a log is what the
    badge will show."""
    out = []
    space = False
    for ch in str(text):
        if " " <= ch <= "~":
            if ch == " ":
                if space:
                    continue
                space = True
            else:
                space = False
            out.append(ch)
        elif out and not space:
            out.append(" ")
            space = True
        if len(out) >= limit:
            break
    return "".join(out).strip()


def slot_name(name):
    name = clean(name, 64).lower()
    for ch in "/#+ ":
        name = name.replace(ch, "-")
    return name[:16]


def digest6(name):
    return hashlib.sha256(name.encode("utf-8")).hexdigest()[:6]


def publish(env, suffix, payload, retain):
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        warn("paho-mqtt is not installed: pip install paho-mqtt")
        return

    topic = "%s/%s/%s" % (env["EDGEWISE_PREFIX"], env["EDGEWISE_ID"], suffix)
    try:
        # CallbackAPIVersion is required by paho 2.x and absent in 1.x, so it
        # is passed positionally only where it exists.
        try:
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        except AttributeError:
            client = mqtt.Client()
        if env.get("EDGEWISE_USER"):
            client.username_pw_set(env["EDGEWISE_USER"], env.get("EDGEWISE_PASS"))
        if env["EDGEWISE_TLS"] == "1":
            client.tls_set()
        client.connect(env["EDGEWISE_BROKER"], int(env["EDGEWISE_PORT"]), 10)
        client.loop_start()
        info = client.publish(topic, payload, qos=0, retain=retain)
        # Without this the loop can stop before the packet has left the socket,
        # and a fast script publishes nothing at all with no error anywhere.
        info.wait_for_publish(timeout=5)
        client.loop_stop()
        client.disconnect()
    except Exception as exc:  # noqa: BLE001 - never break the caller
        warn("publish to %s failed: %s" % (topic, exc))


def warn(message):
    sys.stderr.write("edgewise_pub: %s\n" % message)


def require(env):
    for key in ("EDGEWISE_ID", "EDGEWISE_BROKER"):
        if not env.get(key):
            warn("%s is not set (see %s)" % (key, env["_path"]))
            sys.exit(2)


def main(argv):
    env = load_env()
    if not argv or argv[0] in ("-h", "--help"):
        sys.stdout.write(__doc__)
        return 0

    if argv[0] == "--check":
        sys.stdout.write(
            "env file : %s\nbroker   : %s:%s\ntopic    : %s/%s/slot/<name>\n"
            "labels   : %s\n" % (
                env["_path"], env.get("EDGEWISE_BROKER", "<unset>"),
                env["EDGEWISE_PORT"], env["EDGEWISE_PREFIX"],
                env.get("EDGEWISE_ID", "<unset>"), env["EDGEWISE_LABELS"]))
        require(env)
        publish(env, "text",
                json.dumps({"msg": "edgewise_pub check", "duration": 5}), False)
        sys.stdout.write(
            "\nPublished a test message. The badge should show it for five "
            "seconds.\nIf it did not: same broker? Device ID identical to "
            "Settings -> Device ID?\n")
        return 0

    hashed = env["EDGEWISE_LABELS"] == "hash"

    if argv[0] == "--clear":
        if len(argv) < 2:
            warn("usage: edgewise_pub.py --clear <slot>")
            return 2
        require(env)
        name = slot_name(argv[1])
        # Empty retained payload: clears the board *and* the broker's store.
        publish(env, "slot/%s" % (digest6(name) if hashed else name), b"", True)
        return 0

    if argv[0] == "--text":
        if len(argv) < 2:
            warn("usage: edgewise_pub.py --text <message> [info|alert]")
            return 2
        require(env)
        level = argv[2] if len(argv) > 2 and argv[2] == "alert" else "info"
        publish(env, "text", json.dumps(
            {"msg": clean(argv[1], 64), "level": level}), False)
        return 0

    if len(argv) < 2:
        warn("usage: edgewise_pub.py <slot> <state> [message]")
        return 2

    name, state = slot_name(argv[0]), argv[1]
    require(env)
    if state == "clear":
        publish(env, "slot/%s" % (digest6(name) if hashed else name), b"", True)
        return 0
    if state not in STATES:
        warn("unknown state '%s' (%s clear)" % (state, " ".join(STATES)))
        return 2

    payload = {"state": state, "ttl": int(env["EDGEWISE_TTL"])}
    if hashed:
        # The message goes with the label: it is usually a command line, which
        # leaks more than a folder name would.
        name = digest6(name)
    else:
        payload["label"] = clean(name, 16)
        if len(argv) > 2 and argv[2]:
            payload["msg"] = clean(argv[2], 64)
    if env.get("EDGEWISE_EDGE"):
        payload["edge"] = int(env["EDGEWISE_EDGE"])

    # Retained, always: the badge rebuilds the whole board from retained
    # messages when it reconnects.
    publish(env, "slot/%s" % name, json.dumps(payload), True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
