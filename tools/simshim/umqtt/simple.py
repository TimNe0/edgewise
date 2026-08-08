"""`umqtt.simple` for the EMF badge simulator, backed by paho-mqtt.

The simulator ships no `umqtt` at all, so without this the entire MQTT path --
connect, retained rebuild, availability, inbound routing, the ack round trip --
can only be exercised on real hardware. That is a slow loop for the part of the
app most likely to be wrong.

Dev-only: `export-ignore`d, copied into `sim/fakes/` by the sim launcher, and
never anywhere near a badge. On a badge the real frozen `umqtt.simple` is used.

**What this deliberately does not do is be nicer than the real thing.** The
value of a shim is only as good as its fidelity, so the behaviours the app is
built around are reproduced exactly:

* `connect()` blocks until the broker answers;
* `set_last_will()` after `connect()` is ignored, as it is in umqtt, because
  the CONNECT packet has already gone;
* `check_msg()` delivers at most one message and returns immediately when there
  is nothing waiting;
* nothing reconnects by itself -- a dropped link stays dropped until the caller
  notices and rebuilds it.

If a bug reproduces here it is a real bug. If a workaround is needed here that
the badge does not need, the shim is wrong, not the app.
"""

import time

try:
    import paho.mqtt.client as paho
except ImportError:  # pragma: no cover
    raise ImportError("umqtt shim needs paho-mqtt: pip install paho-mqtt")


class MQTTException(Exception):
    pass


class MQTTClient:
    def __init__(self, client_id, server, port=0, user=None, password=None,
                 keepalive=0, ssl=False, ssl_params=None):
        if isinstance(client_id, bytes):
            client_id = client_id.decode()
        self.client_id = client_id
        self.server = server
        self.port = port or (8883 if ssl else 1883)
        self.user = user
        self.password = password
        self.keepalive = keepalive or 60
        self.ssl = ssl

        self.cb = None
        self._will = None
        self._queue = []
        self._client = None
        self._connected = False

    def set_callback(self, f):
        self.cb = f

    def set_last_will(self, topic, msg, retain=False, qos=0):
        if self._connected:
            # umqtt builds the CONNECT packet from these fields, so a will set
            # afterwards is silently dropped. Reproduced rather than fixed:
            # the app has a test asserting it registers the will first, and a
            # shim that forgave the mistake would make that test meaningless.
            return
        self._will = (_s(topic), msg, qos, retain)

    def connect(self, clean_session=True):
        client = paho.Client(paho.CallbackAPIVersion.VERSION1,
                             client_id=self.client_id,
                             clean_session=clean_session)
        if self.user:
            client.username_pw_set(self.user, self.password)
        if self.ssl:
            client.tls_set()
        if self._will:
            topic, payload, qos, retain = self._will
            client.will_set(topic, payload, qos=qos, retain=retain)
        client.on_message = self._on_message
        client.connect(self.server, self.port, keepalive=self.keepalive)
        client.loop_start()

        # umqtt's connect() does not return until the broker has answered, and
        # the app's whole threading design exists because of that. Waiting here
        # keeps the shim honest about it.
        deadline = time.time() + 10
        while not client.is_connected() and time.time() < deadline:
            time.sleep(0.01)
        if not client.is_connected():
            client.loop_stop()
            raise OSError("connect timed out")

        self._client = client
        self._connected = True
        return 0

    def subscribe(self, topic, qos=0):
        self._require()
        self._client.subscribe(_s(topic), qos)

    def publish(self, topic, msg, retain=False, qos=0):
        self._require()
        info = self._client.publish(_s(topic), msg, qos=qos, retain=retain)
        if qos:
            info.wait_for_publish(timeout=5)

    def check_msg(self):
        """Deliver at most one queued message. Never blocks waiting for one."""
        self._require()
        if not self._queue:
            return None
        topic, payload = self._queue.pop(0)
        if self.cb:
            self.cb(topic, payload)
        return 1

    def wait_msg(self):
        self._require()
        while not self._queue:
            time.sleep(0.01)
        return self.check_msg()

    def ping(self):
        self._require()

    def disconnect(self):
        self._connected = False
        client, self._client = self._client, None
        if client is not None:
            try:
                client.loop_stop()
                client.disconnect()
            except Exception:  # noqa: BLE001
                pass

    def _on_message(self, client, userdata, message):
        # Bytes on both sides, exactly as umqtt hands them to the callback.
        self._queue.append((message.topic.encode(), message.payload))

    def _require(self):
        if not self._connected or self._client is None:
            raise OSError(107, "not connected")


def _s(value):
    return value.decode() if isinstance(value, (bytes, bytearray)) else value
