"""A small HTTP door into the badge, for callers that cannot speak MQTT.

A webhook, a phone shortcut, a browser bookmark, `curl` in a Makefile: none of
them can publish MQTT and all of them can fetch a URL. Until now that needed a
second machine running a bridge, which is not a poke API -- it is a translator
with your broker credentials in it.

**MQTT does not go away.** It still carries the things a listener cannot: slots
retained so the board rebuilds itself after a reboot, the outbound `event`
stream that lets Home Assistant and CI see your taps, and reachability for
publishers that cannot route to the badge. This is a second door, not a
replacement.

## One validation path

Nothing here decides what a legal slot is. Routes build a plain dict and hand it
to `security.parse_slot` and friends -- the same functions the MQTT path uses,
with the same caps, exercised by the same hostile-input corpus. The 3 Hz strobe
limit and the brightness ceiling apply without being mentioned here at all,
because they are structural in `ledfx`.

A second transport with its own idea of the limits would be a second set of
bugs, and the interesting ones would only appear on whichever door the attacker
chose.

## Everything is bounded

This is the first thing on this device that listens, on 2 MB of RAM, in front of
a photosensitive-seizure cap. Every constant below is a number a stranger on
your network gets to push against, so each is small and each is checked before
any work is done rather than after.

Parsing is pure and lives here; sockets live in `Server`, which is thin on
purpose -- everything that can be tested on a desktop is.
"""

MAX_REQUEST_LINE = 512
MAX_HEADERS = 16
MAX_HEADER_LINE = 256
MAX_BODY = 1024

# Sockets are the scarcest thing on this device, and `/wait` holds one open for
# as long as it waits. Four connections and two waiters is not generosity, it is
# what a badge can afford while still animating a ring at 20 Hz.
MAX_CONNECTIONS = 4
MAX_WAITERS = 2
MAX_WAIT_S = 120
DEFAULT_WAIT_S = 60
CONNECTION_TIMEOUT_S = 5

DEFAULT_PORT = 8420
TOKEN_CHARS = 8

METHODS = ("GET", "POST", "PUT")

# What a route asks the app to do. The app maps these to the same handlers the
# MQTT path uses; nothing here touches the board.
KIND_SLOT = "slot"
KIND_TEXT = "text"
KIND_WEATHER = "weather"
KIND_LED = "led"
KIND_WAIT = "wait"
KIND_HEALTH = "health"


def _unquote(text):
    """Percent-decoding, without importing urllib on a badge.

    Tolerant by design: a stray `%` is a literal `%` rather than an error. The
    validators downstream decide what is acceptable, and a 400 for a malformed
    escape would be this module inventing a rule of its own.
    """
    if "%" not in text:
        return text.replace("+", " ")
    out = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "%" and i + 2 < len(text) + 0:
            try:
                out.append(chr(int(text[i + 1:i + 3], 16)))
                i += 3
                continue
            except ValueError:
                pass
        out.append(" " if ch == "+" else ch)
        i += 1
    return "".join(out)


def parse_query(raw):
    """`a=1&b=2` to a dict. Last value wins; empty keys are dropped."""
    query = {}
    if not raw:
        return query
    for pair in raw.split("&"):
        if not pair:
            continue
        key, _, value = pair.partition("=")
        key = _unquote(key)
        if key:
            query[key] = _unquote(value)
    return query


def parse_request_line(line):
    """(method, path, query) from a request line, or (None, reason, None).

    Refused rather than parsed: anything over MAX_REQUEST_LINE, an unknown
    method, or a path that is not absolute. A caller that sends a 4 KB URL to a
    badge has already told you what it is.
    """
    if not line or len(line) > MAX_REQUEST_LINE:
        return (None, "request line too long", None)
    parts = line.split(" ")
    if len(parts) < 2:
        return (None, "malformed request line", None)
    method, target = parts[0], parts[1]
    if method not in METHODS:
        return (None, "method not allowed", None)
    if not target.startswith("/"):
        return (None, "malformed path", None)
    path, _, raw_query = target.partition("?")
    return (method, path, parse_query(raw_query))


def parse_headers(lines):
    """Header lines to a lower-cased dict, bounded in both count and length."""
    headers = {}
    for line in lines[:MAX_HEADERS]:
        if not line or len(line) > MAX_HEADER_LINE:
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        headers[key.strip().lower()] = value.strip()
    return headers


def _int(value, low, high):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if low <= number <= high else None


def route(path, query):
    """(kind, name, payload) for a request, or (None, reason, None).

    `payload` is a plain dict for `security.parse_*` to validate. This function
    decides *which* door was knocked on, never what is allowed through it.

    Stricter than the badge's MQTT path on one point only: an unknown endpoint
    or a missing required parameter is an error rather than silence. A publisher
    on a radio cannot be told; a person holding a terminal can, and would much
    rather be.
    """
    parts = [p for p in path.split("/") if p]

    if not parts or (len(parts) == 1 and parts[0] == "health"):
        return (KIND_HEALTH, None, None)

    if parts[0] == "slot":
        if len(parts) != 2:
            return (None, "usage: /slot/<name>", None)
        name = parts[1]
        if not name or len(name) > 16:
            return (None, "slot name must be 1-16 characters", None)
        for ch in name:
            # The name becomes one level of an MQTT topic when it is echoed
            # back out, and a JSON string on the way in.
            if not (ch.isalpha() or ch.isdigit() or ch in "._-"):
                return (None, "slot names allow only A-Za-z0-9._-", None)
        payload = {"state": query.get("state", "info")}
        for key in ("label", "msg"):
            if query.get(key):
                payload[key] = query[key]
        for key in ("ttl", "edge"):
            if query.get(key) is not None and query.get(key) != "":
                payload[key] = _int(query[key], -1, 1000000)
        return (KIND_SLOT, name, payload)

    if parts[0] == "text" and len(parts) == 1:
        if not query.get("msg"):
            return (None, "usage: /text?msg=...", None)
        payload = {"msg": query["msg"], "level": query.get("level", "info")}
        if query.get("duration"):
            payload["duration"] = _int(query["duration"], -1, 100000)
        return (KIND_TEXT, None, payload)

    if parts[0] == "weather" and len(parts) == 1:
        payload = {}
        if query.get("cond"):
            payload["cond"] = query["cond"]
        for key in ("temp", "rain", "ttl"):
            if query.get(key):
                payload[key] = _int(query[key], -1000, 1000000)
        if query.get("unit"):
            payload["unit"] = query["unit"]
        if not payload:
            return (None, "usage: /weather?cond=rain&temp=12&rain=40", None)
        return (KIND_WEATHER, None, payload)

    if parts[0] == "led" and len(parts) == 1:
        payload = {}
        for key in ("segment", "effect"):
            if query.get(key):
                payload[key] = query[key]
        for key in ("speed", "intensity", "brightness", "ttl"):
            if query.get(key):
                payload[key] = _int(query[key], -1, 1000000)
        rgb = parse_rgb(query.get("rgb"))
        if rgb is not None:
            payload["rgb"] = rgb
        if not payload:
            return (None, "usage: /led?segment=edge:0&rgb=255,0,0", None)
        return (KIND_LED, None, payload)

    if parts[0] == "wait":
        if len(parts) != 2 or not parts[1]:
            return (None, "usage: /wait/<slot>", None)
        seconds = _int(query.get("timeout"), 1, MAX_WAIT_S)
        return (KIND_WAIT, parts[1],
                {"timeout": DEFAULT_WAIT_S if seconds is None else seconds})

    return (None, "unknown endpoint", None)


def parse_rgb(text):
    """`255,0,80` to a tuple. None for anything else -- the validator in
    `security` decides whether a missing colour is fatal, not this."""
    if not text:
        return None
    parts = text.split(",")
    if len(parts) != 3:
        return None
    out = []
    for part in parts:
        value = _int(part.strip(), 0, 255)
        if value is None:
            return None
        out.append(value)
    return tuple(out)


def token_ok(expected, headers, query):
    """Whether a request carried the right token.

    Compared over the whole string rather than exiting at the first difference.
    Against someone who can watch your LAN this proves nothing -- the token is
    in the clear -- but it costs nothing and the alternative is a habit worth
    not having.
    """
    if not expected:
        return False
    given = headers.get("x-edgewise-token") or query.get("token")
    if given is None or len(given) != len(expected):
        return False
    difference = 0
    for a, b in zip(given, expected):
        difference |= ord(a) ^ ord(b)
    return difference == 0


def needs_token(kind):
    """`/health` is open so the badge can be found without a secret. It says
    the version and how many slots are lit, and nothing else."""
    return kind != KIND_HEALTH


def response(status, body, extra=""):
    """A complete HTTP/1.0 response.

    1.0 and `Connection: close` on purpose: keep-alive means holding sockets
    open for callers who may never come back, and there are four of them.
    """
    reasons = {200: "OK", 400: "Bad Request", 401: "Unauthorized",
               404: "Not Found", 408: "Request Timeout",
               413: "Payload Too Large", 429: "Too Many Requests",
               503: "Service Unavailable"}
    raw = body.encode() if isinstance(body, str) else body
    head = ("HTTP/1.0 %d %s\r\nContent-Type: application/json\r\n"
            "Content-Length: %d\r\nConnection: close\r\n%s\r\n"
            % (status, reasons.get(status, "Error"), len(raw), extra))
    return head.encode() + raw


def json_error(reason):
    # Hand-built, because a badge has no json.dumps worth spending on an error
    # path, and because the reason is ours and contains nothing to escape.
    safe = "".join(c for c in str(reason) if c.isalpha() or c.isdigit()
                   or c in " .,:/<>-_?=")
    return '{"error":"%s"}' % safe
