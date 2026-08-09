"""Test doubles for the MQTT layer.

`FakeMQTTClient` mirrors the `umqtt.simple` surface exactly -- including the
parts that bite. It is not a convenience stub: the failures it can be told to
inject are the ones the real client actually produces, so a test written
against it is a regression test for a real defect rather than for an
imagined one.
"""


class FakeBroker:
    """Just enough broker to make retained-message behaviour testable.

    Retained state is the entire crash-recovery story -- the badge keeps
    nothing that matters and repaints from retained messages on reconnect -- so
    "an empty retained payload deletes a slot" and "reconnecting rebuilds the
    board" have to be unit tests, not things we hope the soak notices.
    """

    def __init__(self):
        self.retained = {}
        self.published = []
        self.subscriptions = []

    def publish(self, topic, payload, retain=False, qos=0):
        self.published.append((topic, payload, retain, qos))
        if retain:
            if payload is None or len(payload) == 0:
                self.retained.pop(topic, None)
            else:
                self.retained[topic] = payload

    def matching(self, pattern):
        """Retained topics matching a subscription, honouring + and #."""
        out = []
        for topic in self.retained:
            if _topic_matches(pattern, topic):
                out.append((topic, self.retained[topic]))
        return out

    def last(self, suffix):
        for topic, payload, _, _ in reversed(self.published):
            if topic.endswith(suffix):
                return payload
        return None


def _topic_matches(pattern, topic):
    p = pattern.split("/")
    t = topic.split("/")
    for i, part in enumerate(p):
        if part == "#":
            return True
        if i >= len(t):
            return False
        if part == "+":
            continue
        if part != t[i]:
            return False
    return len(p) == len(t)


class FakeMQTTClient:
    """The `umqtt.simple` interface, with its real failure modes available."""

    def __init__(self, spec, broker=None):
        self.spec = spec
        self.broker = broker or FakeBroker()
        self.callback = None
        self.will = None
        self.connected = False
        self.disconnected = False
        self.subscriptions = []
        self.pings = 0
        self.published = []
        # Mirrors umqtt.simple's socket state. check_msg() leaves the socket
        # non-blocking, which is what breaks a subsequent qos=1 publish.
        self.blocking = True

        self._pending = []
        self._fail = {}
        self.setblocking_trap = False

    # -- failure injection ---------------------------------------------------

    def fail_next(self, method, exc):
        self._fail.setdefault(method, []).append(exc)

    def _maybe_fail(self, method):
        queue = self._fail.get(method)
        if queue:
            raise queue.pop(0)

    def deliver(self, topic, payload):
        """Queue an inbound message for the next check_msg()."""
        self._pending.append((topic, payload))

    def deliver_retained(self):
        """Replay everything the broker retained under our subscriptions."""
        for pattern in self.subscriptions:
            for topic, payload in self.broker.matching(pattern):
                self._pending.append((topic.encode(), payload))

    # -- the umqtt.simple surface -------------------------------------------

    def set_callback(self, fn):
        self.callback = fn

    def set_last_will(self, topic, msg, retain=False, qos=0):
        if self.connected:
            # umqtt builds the CONNECT packet from these fields, so a will set
            # after connecting is silently ignored by the real client. Made
            # loud here, because silent is how that bug survives to production.
            raise RuntimeError("set_last_will after connect is a no-op")
        self.will = (topic, msg, retain, qos)

    def connect(self, clean_session=True):
        self._maybe_fail("connect")
        self.connected = True
        self.disconnected = False
        return 0

    def subscribe(self, topic, qos=0):
        self._maybe_fail("subscribe")
        self._require_connection()
        pattern = topic.decode() if isinstance(topic, bytes) else topic
        self.subscriptions.append(pattern)
        # umqtt.simple waits for the SUBACK with wait_msg(), which is the same
        # call that dispatches incoming PUBLISHes -- so the broker's retained
        # burst is delivered *inside* subscribe(), not afterwards. The fake did
        # not model that, and the bug it hid emptied the board a second after
        # it filled on real hardware.
        if self.broker is not None and self.callback is not None:
            for topic_name, payload in self.broker.matching(pattern):
                self.callback(topic_name.encode(), payload)

    def publish(self, topic, msg, retain=False, qos=0):
        self._maybe_fail("publish")
        self._require_connection()
        if qos == 1 and not self.blocking and self.setblocking_trap:
            # The real defect: check_msg() calls setblocking(False), and a
            # qos=1 publish then blocks for a PUBACK on a non-blocking socket.
            raise OSError(11, "EAGAIN")
        name = topic.decode() if isinstance(topic, bytes) else topic
        self.published.append((name, msg, retain, qos))
        self.broker.publish(name, msg, retain, qos)

    def check_msg(self):
        self._maybe_fail("check_msg")
        self._require_connection()
        self.blocking = False
        if not self._pending:
            return None
        topic, payload = self._pending.pop(0)
        if self.callback:
            self.callback(topic, payload)
        return 1

    def ping(self):
        self._maybe_fail("ping")
        self._require_connection()
        self.pings += 1

    def disconnect(self):
        self.connected = False
        self.disconnected = True

    def _require_connection(self):
        if not self.connected:
            raise OSError(107, "not connected")

    def fire_will(self):
        """What the broker does when the badge stops answering."""
        if self.will:
            topic, msg, retain, qos = self.will
            self.broker.publish(topic, msg, retain, qos)


def factory(broker=None, configure=None):
    """A client_factory for Link that hands back a controllable fake."""
    made = []

    def make(spec):
        client = FakeMQTTClient(spec, broker)
        if configure:
            configure(client)
        made.append(client)
        return client

    make.made = made
    return make
