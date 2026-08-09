"""The MQTT link, and the thread boundary that keeps it off the render loop.

## Why this is not just `check_msg()` in `update()`

That is what the official Tildagon example does, and it is a trap. Reading the
`umqtt.simple` source:

* `connect()` does blocking DNS, a TCP connect, an optional TLS handshake, and
  then spins on a **blocking** socket waiting for CONNACK. Unbounded.
* `subscribe(qos=1)` and `publish(qos=1)` block until SUBACK/PUBACK arrive.
  Against a broker that has gone away without closing the socket, forever.
* `check_msg()` is only *almost* non-blocking. It peeks one byte with
  `setblocking(False)`, but the moment a byte is there it calls
  `setblocking(True)` and reads the rest of the packet blocking. A payload split
  across TCP segments stalls the caller until the rest arrives.

The badge runs one asyncio task, so any of those stalls the buttons and the LED
ring together. SkyScope measured 5.36 s of dead input doing network work inline
against 0.28 s on a worker thread. A status board animating LEDs continuously
cannot absorb that, and the failure only appears on congested Wi-Fi -- never in
the simulator, never on a desk.

## The contract

One long-lived worker thread owns the socket **exclusively**. The UI task never
touches the client. Two bounded lists cross the boundary, plus a handful of
status fields written only by the worker and read only by the UI.

There are no locks. A lock held across a blocking socket read would reintroduce
exactly the stall being removed. Instead this relies on the one guarantee
MicroPython gives: the interpreter switches threads between bytecodes, so a
single attribute rebind, and `list.append` / `list.pop(0)`, are atomic with
respect to each other. Nothing here relies on anything more than that, and
nothing should be added that does.

Inbound messages cross as **raw bytes**. The worker's only inbound work is an
O(1) length check, so an oversize payload never even gets retained in the shared
heap. Parsing, validation and rate limiting all run on the UI task, where the
frame budget is ours to spend.
"""

from . import clock

STATE_OFFLINE = "offline"
STATE_CONNECTING = "connecting"
STATE_ONLINE = "online"
STATE_ERROR = "error"

# Bounded so a flood cannot grow the heap. Inbound drops the *newest* -- during
# a burst the older messages are the ones already being displayed, and dropping
# those would make the board flicker. Outbound drops the *oldest*, because a
# stale ack is worth less than a fresh one.
INBOX_MAX = 16
OUTBOX_MAX = 8

KEEPALIVE_S = 60
# umqtt.simple does not ping on its own, so a link that is idle for longer than
# the keepalive is silently dropped by the broker and only discovered on the
# next publish. The worker pings at half the interval.
PING_INTERVAL_MS = (KEEPALIVE_S * 1000) // 2

BACKOFF_MS = (1000, 2000, 4000, 8000, 16000, 32000, 60000)

# QoS 0 throughout. On umqtt.simple a qos=1 publish blocks waiting for the
# PUBACK, and after a check_msg() has left the socket non-blocking it fails
# outright with EAGAIN. The durability the protocol actually needs comes from
# the retained flag, not from QoS: a dropped state message is superseded by the
# next one, and the retained copy survives a reboot either way.
QOS = 0

WORKER_STACK = 8 * 1024


class BrokerSpec:
    """An immutable snapshot of the broker settings.

    The worker is never handed the live config dict. It would be read on one
    thread while the settings screen mutated it on another, and the resulting
    half-applied broker change is the kind of bug that only shows up when
    somebody edits settings at the wrong moment.
    """

    __slots__ = ("host", "port", "user", "password", "tls", "prefix",
                 "device_id", "client_id")

    def __init__(self, cfg):
        broker = cfg["broker"]
        self.host = broker["host"]
        self.port = broker["port"]
        self.user = broker["user"]
        self.password = broker["pass"]
        self.tls = broker["tls"]
        self.prefix = broker["prefix"]
        self.device_id = cfg["device_id"]
        self.client_id = cfg["device_id"]

    def root(self):
        return "%s/%s" % (self.prefix, self.device_id)

    def same(self, other):
        if other is None:
            return False
        return (self.host == other.host and self.port == other.port
                and self.user == other.user and self.password == other.password
                and self.tls == other.tls and self.prefix == other.prefix
                and self.device_id == other.device_id)

    def usable(self):
        return bool(self.host)


class Link:
    """Owns the MQTT session. Everything public here is UI-task-safe."""

    def __init__(self, spec, client_factory=None, threaded=True):
        self.spec = spec
        self._factory = client_factory or _default_client
        self._threaded = threaded

        self._inbox = []
        self._outbox = []
        self._client = None
        self._stop = False
        self._running = False

        # Worker writes, UI reads. Plain rebinds only.
        self.state = STATE_OFFLINE
        self.state_msg = ""
        self.session_epoch = 0
        self.dropped_in = 0
        self.dropped_out = 0
        self.reconnects = 0
        self.alive_ms = 0

        self._attempt = 0
        self._next_try_ms = 0
        self._last_ping_ms = 0

    # -- UI side -------------------------------------------------------------

    def start(self):
        if self._running or not self.spec.usable():
            return False
        self._stop = False
        self._running = True
        if self._threaded:
            try:
                import _thread

                _thread.stack_size(WORKER_STACK)
                _thread.start_new_thread(self._loop, ())
                return True
            except Exception as exc:  # noqa: BLE001 - no threads on this build
                # Not fatal. Polled mode still works, it just stutters when the
                # link reconnects, so say so rather than failing silently.
                self._threaded = False
                self.state_msg = "polled mode"
                print("[edgewise] thread start failed, polling:", exc)
        return True

    def stop(self):
        self._stop = True
        self._running = False

    def drain(self, limit=4):
        """Take up to `limit` raw messages. The only way out of the inbox.

        The cap is what keeps a burst off the frame budget: even if two hundred
        retained messages land at once, this is four parses per frame, so the
        board fills over a couple of seconds without ever dropping below 20 Hz.
        """
        out = []
        inbox = self._inbox
        while inbox and len(out) < limit:
            out.append(inbox.pop(0))
        return out

    def publish_event(self, payload):
        """Queue an outbound event. Never blocks, never raises."""
        self._queue(self.spec.root() + "/event", payload, False)

    def publish_status(self, suffix, payload):
        """Queue a non-retained message on one of our own topics.

        Diagnostics use this. It exists so nothing outside this module has to
        reach for `_queue`, which is where the bounded-mailbox discipline lives
        and is not something a caller should be choosing to bypass.
        """
        self._queue("%s/%s" % (self.spec.root(), suffix), payload, False)

    def publish_slot(self, name, payload):
        self._queue("%s/slot/%s" % (self.spec.root(), name), payload, True)

    def _queue(self, topic, payload, retain):
        if len(self._outbox) >= OUTBOX_MAX:
            self._outbox.pop(0)
            self.dropped_out += 1
        self._outbox.append((topic, payload, retain))

    def pump(self, now_ms):
        """Advance the link on the UI task. Only used in polled mode.

        In threaded mode this is a no-op, so the caller does not need to know
        which mode is in force.
        """
        if self._threaded or not self._running:
            return
        self._step(now_ms)

    def connected(self):
        return self.state == STATE_ONLINE

    def status_line(self):
        # Polled mode is shown in *every* state, not just when online. A badge
        # that fell back to polling does its blocking connect on the render
        # loop, so its buttons freeze for as long as a DNS lookup or a TCP
        # handshake takes -- and while it is stuck retrying, the one state where
        # that is most obvious to the user was the one state that never said so.
        suffix = "" if self._threaded else " (polled)"
        if self.state == STATE_ERROR and self.state_msg:
            return (self.state_msg + suffix)[:24]
        return (self.state + suffix)[:24]

    # -- worker side ---------------------------------------------------------

    def _loop(self):
        """The worker. Nothing here touches anything the UI writes."""
        while not self._stop:
            now = clock.now_ms()
            try:
                self._step(now)
            except Exception as exc:  # noqa: BLE001 - the thread must not die
                self._fail(exc, now)
            self._sleep(20)
        self._disconnect()
        self._running = False

    def _sleep(self, ms):
        try:
            import time

            time.sleep_ms(ms)
        except (ImportError, AttributeError):  # pragma: no cover - CPython
            import time

            time.sleep(ms / 1000.0)

    def _step(self, now_ms):
        self.alive_ms = now_ms
        if self._client is None:
            self._try_connect(now_ms)
            return
        self._service(now_ms)

    def _try_connect(self, now_ms):
        if self._next_try_ms and not clock.expired(self._next_try_ms, now_ms):
            return
        self.state = STATE_CONNECTING
        try:
            client = self._factory(self.spec)
            client.set_callback(self._on_message)
            # Must precede connect(): umqtt builds the CONNECT packet from the
            # will fields, so setting it afterwards is a silent no-op and the
            # availability topic would simply never flip when the badge died.
            client.set_last_will(self.spec.root() + "/availability",
                                 b"offline", retain=True, qos=QOS)
            client.connect()
            # Bumped here -- after CONNACK, before the first subscribe -- and
            # the ordering is load-bearing.
            #
            # umqtt.simple's subscribe() waits for its SUBACK by calling
            # wait_msg(), which is the same function that dispatches incoming
            # PUBLISHes to the callback. So the broker's retained burst lands in
            # our inbox *during* these subscribe calls.
            #
            # This used to be bumped last, "so the UI cannot start a rebuild
            # before the retained messages can arrive". The effect was the
            # opposite: the UI drained those messages while the epoch was still
            # the old one, stamped them with the previous generation, and then
            # began a rebuild -- whose sweep deleted every slot it had just been
            # told about. On hardware that looked like the board loading and
            # then emptying itself a second later.
            #
            # Bumping first is safe in the other direction: nothing can be in
            # the inbox before this line, and the UI checks the epoch before it
            # drains, so any message it ever sees belongs to a rebuild that has
            # already started.
            self.session_epoch += 1
            for suffix in ("slot/+", "led", "text", "weather"):
                client.subscribe(("%s/%s" % (self.spec.root(), suffix)).encode())
            client.publish((self.spec.root() + "/availability").encode(),
                           b"online", retain=True, qos=QOS)
        except Exception as exc:  # noqa: BLE001
            self._fail(exc, now_ms)
            return

        self._client = client
        self._attempt = 0
        self._next_try_ms = 0
        self._last_ping_ms = now_ms
        self.state = STATE_ONLINE
        self.state_msg = ""
        self.reconnects += 1

    def _service(self, now_ms):
        client = self._client
        try:
            client.check_msg()
            while self._outbox:
                topic, payload, retain = self._outbox.pop(0)
                client.publish(topic.encode(), payload, retain=retain, qos=QOS)
            if clock.elapsed_ms(self._last_ping_ms, now_ms) >= PING_INTERVAL_MS:
                self._last_ping_ms = now_ms
                client.ping()
        except Exception as exc:  # noqa: BLE001
            self._fail(exc, now_ms)

    def _on_message(self, topic, payload):
        """Called by umqtt from inside check_msg(), on the worker thread.

        Deliberately minimal: a length check and an append. No JSON, no
        validation, no allocation beyond the tuple -- parsing here would put
        unbounded heap pressure, and possibly a garbage collection, in the
        middle of a socket read.
        """
        if payload is not None and len(payload) > 512:
            self.dropped_in += 1
            return
        if len(self._inbox) >= INBOX_MAX:
            self.dropped_in += 1
            return
        self._inbox.append((topic, payload))

    def _fail(self, exc, now_ms):
        """Back off from `now_ms`, never from the wall clock.

        Taking the time as an argument is not tidiness. A deadline computed
        from `clock.now_ms()` while the caller is stepping the link on some
        other timebase compares two unrelated clocks, and the backoff either
        never fires or fires immediately -- which is how a broker outage turns
        into a reconnect storm.
        """
        self._disconnect()
        self.state = STATE_ERROR
        self.state_msg = str(exc)[:24] or "link error"
        delay = BACKOFF_MS[min(self._attempt, len(BACKOFF_MS) - 1)]
        self._attempt += 1
        self._next_try_ms = clock.add_ms(now_ms, delay)

    def _disconnect(self):
        client, self._client = self._client, None
        if client is None:
            return
        try:
            client.disconnect()
        except Exception:  # noqa: BLE001 - already broken; nothing to salvage
            pass


def topic_suffix(topic, root):
    """The part of a topic after `<prefix>/<device-id>/`, or None."""
    if isinstance(topic, (bytes, bytearray)):
        try:
            topic = topic.decode("utf-8")
        except (UnicodeError, ValueError):
            return None
    if not isinstance(topic, str):
        return None
    prefix = root + "/"
    if not topic.startswith(prefix):
        return None
    return topic[len(prefix):]


def route(suffix):
    """(kind, name) for a topic suffix, or (None, None) if we do not serve it."""
    if suffix is None:
        return (None, None)
    if suffix.startswith("slot/"):
        name = suffix[5:]
        # One level only: `slot/a/b` is not a slot called "a/b", it is junk.
        if name and "/" not in name:
            return ("slot", name)
        return (None, None)
    if suffix in ("led", "text", "weather"):
        return (suffix, None)
    return (None, None)


# Enough for a handshake on a slow link, short enough that a polled-mode badge
# is not frozen for a minute at a time. Only used where the client supports it.
SOCKET_TIMEOUT_S = 8


def _default_client(spec):  # pragma: no cover - needs firmware
    from umqtt.simple import MQTTClient

    kwargs = dict(port=spec.port, user=spec.user, password=spec.password,
                  keepalive=KEEPALIVE_S, ssl=spec.tls)
    try:
        # Newer micropython-lib takes this; older frozen copies do not, and the
        # difference is a TypeError rather than anything discoverable. Without
        # it a dead broker that still completes a TCP connect blocks the caller
        # indefinitely -- forever on the worker thread, and on the render loop
        # itself if threads were unavailable.
        return MQTTClient(spec.client_id, spec.host,
                          socket_timeout=SOCKET_TIMEOUT_S, **kwargs)
    except TypeError:
        return MQTTClient(spec.client_id, spec.host, **kwargs)
