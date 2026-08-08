"""The MQTT link: routing, retained rebuild, LWT, backoff, and the mailbox.

Everything here runs the link in unthreaded mode and steps it by hand, so the
protocol behaviour is deterministic. Whether a `umqtt` socket actually works
from a worker thread on this firmware is a hardware question -- see
`tools/probe_mqtt.py` -- and no amount of CPython testing can answer it.
"""

import unittest

from edgewise import conf, mqtt_link
from edgewise.mqtt_link import BrokerSpec, Link, route, topic_suffix
from tests.fakes import FakeBroker, FakeMQTTClient, factory

T0 = 300000


def make_cfg(**over):
    cfg = conf.validate({})
    cfg["broker"]["host"] = "broker.example"
    cfg["device_id"] = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"[:26]
    for key, value in over.items():
        if key in ("host", "port", "prefix", "user", "pass", "tls"):
            cfg["broker"][key] = value
        else:
            cfg[key] = value
    return conf.validate(cfg)


def make_link(broker=None, configure=None, cfg=None):
    spec = BrokerSpec(cfg or make_cfg())
    make = factory(broker, configure)
    link = Link(spec, client_factory=make, threaded=False)
    link.start()
    return link, make


class TestTopics(unittest.TestCase):
    ROOT = "edgewise/ABC"

    def test_suffix_extraction(self):
        self.assertEqual(topic_suffix(b"edgewise/ABC/slot/kiln", self.ROOT), "slot/kiln")

    def test_foreign_topics_are_rejected(self):
        for topic in (b"other/ABC/slot/x", b"edgewise/XYZ/slot/x", b"", b"edgewise"):
            self.assertIsNone(topic_suffix(topic, self.ROOT), topic)

    def test_invalid_utf8_topic_is_rejected(self):
        self.assertIsNone(topic_suffix(b"edgewise/ABC/slot/\xff\xfe", self.ROOT))

    def test_routing(self):
        self.assertEqual(route("slot/kiln"), ("slot", "kiln"))
        self.assertEqual(route("led"), ("led", None))
        self.assertEqual(route("text"), ("text", None))

    def test_unknown_topics_are_ignored(self):
        for suffix in ("event", "availability", "slot/", "slot/a/b", "junk", None):
            self.assertEqual(route(suffix), (None, None), suffix)

    def test_prefix_is_configurable(self):
        # EMF's broker only lets anonymous clients publish under `open/`, so
        # the root has to be able to move.
        cfg = make_cfg(prefix="open/edgewise")
        self.assertEqual(BrokerSpec(cfg).root(), "open/edgewise/" + cfg["device_id"])


class TestConnection(unittest.TestCase):
    def test_connects_and_subscribes(self):
        link, make = make_link()
        link.pump(T0)
        client = make.made[0]
        self.assertTrue(client.connected)
        self.assertEqual(link.state, mqtt_link.STATE_ONLINE)
        self.assertEqual(len(client.subscriptions), 3)

    def test_last_will_is_registered_before_connecting(self):
        """The fake raises if the order is wrong, because the real one does not.

        umqtt builds the CONNECT packet from the will fields, so setting the
        will after connecting does nothing at all -- and the symptom is only
        that a badge which loses power never shows as offline, which nobody
        notices until they rely on it.
        """
        link, make = make_link()
        link.pump(T0)
        client = make.made[0]
        self.assertIsNotNone(client.will)
        topic, payload, retain, _ = client.will
        self.assertTrue(topic.endswith("/availability"))
        self.assertEqual(payload, b"offline")
        self.assertTrue(retain)

    def test_publishes_online_retained(self):
        broker = FakeBroker()
        link, _ = make_link(broker)
        link.pump(T0)
        self.assertEqual(broker.last("/availability"), b"online")

    def test_the_will_flips_availability_when_the_badge_dies(self):
        broker = FakeBroker()
        link, make = make_link(broker)
        link.pump(T0)
        make.made[0].fire_will()
        self.assertEqual(broker.last("/availability"), b"offline")

    def test_session_epoch_bumps_only_after_subscriptions_are_live(self):
        # If it bumped first, the UI would open a retained-rebuild window
        # before any retained message could arrive, and then sweep the board.
        seen = {}

        def configure(client):
            original = client.subscribe

            def watched(topic, qos=0):
                seen["epoch_at_subscribe"] = link.session_epoch
                return original(topic, qos)

            client.subscribe = watched

        link, _ = make_link(configure=configure)
        link.pump(T0)
        self.assertEqual(seen["epoch_at_subscribe"], 0)
        self.assertEqual(link.session_epoch, 1)

    def test_no_broker_configured_does_not_start(self):
        cfg = make_cfg(host="")
        link = Link(BrokerSpec(cfg), client_factory=factory(), threaded=False)
        self.assertFalse(link.start())
        self.assertEqual(link.state, mqtt_link.STATE_OFFLINE)


def fail_first(method, exc):
    """Break only the first client the factory hands out.

    The factory's `configure` hook runs for every client, so a naive
    `fail_next` would sabotage the retry as well and make a working backoff
    look broken.
    """
    seen = []

    def configure(client):
        if not seen:
            seen.append(client)
            client.fail_next(method, exc)

    return configure


class TestBackoff(unittest.TestCase):
    def test_failure_backs_off_and_retries(self):
        link, make = make_link(configure=fail_first("connect", OSError(113, "down")))
        link.pump(T0)
        self.assertEqual(link.state, mqtt_link.STATE_ERROR)

        # Too soon: no second attempt.
        link.pump(T0 + 100)
        self.assertEqual(len(make.made), 1)

        # After the first backoff step it tries again, and this client works.
        link.pump(T0 + mqtt_link.BACKOFF_MS[0] + 50)
        self.assertEqual(len(make.made), 2)
        self.assertEqual(link.state, mqtt_link.STATE_ONLINE)

    def test_backoff_grows(self):
        def configure(client):
            client.fail_next("connect", OSError(113, "unreachable"))

        link, make = make_link(configure=configure)
        now = T0
        for step in range(3):
            link.pump(now)
            now += mqtt_link.BACKOFF_MS[step] + 50
        self.assertGreaterEqual(len(make.made), 3)

    def test_a_broken_link_recovers(self):
        link, make = make_link()
        link.pump(T0)
        make.made[0].fail_next("check_msg", OSError(104, "reset by peer"))
        link.pump(T0 + 100)
        self.assertEqual(link.state, mqtt_link.STATE_ERROR)
        link.pump(T0 + 100 + mqtt_link.BACKOFF_MS[0] + 50)
        self.assertEqual(link.state, mqtt_link.STATE_ONLINE)
        self.assertEqual(link.session_epoch, 2)

    def test_the_worker_never_dies_on_an_unexpected_error(self):
        link, make = make_link()
        link.pump(T0)
        make.made[0].fail_next("check_msg", ValueError("something odd"))
        link.pump(T0 + 100)
        # Recovered rather than wedged: the loop catches everything, because a
        # dead worker means a board that silently stops updating.
        link.pump(T0 + 100 + mqtt_link.BACKOFF_MS[0] + 50)
        self.assertEqual(link.state, mqtt_link.STATE_ONLINE)


class TestMailbox(unittest.TestCase):
    def test_messages_cross_as_raw_bytes(self):
        link, make = make_link()
        link.pump(T0)
        make.made[0].deliver(b"edgewise/X/slot/a", b'{"state":"working"}')
        link.pump(T0 + 10)
        self.assertEqual(link.drain(), [(b"edgewise/X/slot/a", b'{"state":"working"}')])

    def test_drain_is_capped(self):
        link, make = make_link()
        link.pump(T0)
        client = make.made[0]
        for i in range(10):
            client.deliver(b"edgewise/X/slot/%d" % i, b'{"state":"working"}')
        for _ in range(10):
            link.pump(T0 + 10)
        self.assertEqual(len(link.drain(4)), 4)

    def test_inbox_is_bounded_and_counts_drops(self):
        link, make = make_link()
        link.pump(T0)
        client = make.made[0]
        for i in range(mqtt_link.INBOX_MAX + 20):
            client.deliver(b"edgewise/X/slot/a", b"{}")
        for _ in range(mqtt_link.INBOX_MAX + 20):
            link.pump(T0 + 10)
        self.assertLessEqual(len(link._inbox), mqtt_link.INBOX_MAX)
        self.assertGreater(link.dropped_in, 0)

    def test_oversize_payloads_never_enter_the_mailbox(self):
        # Rejected on the worker, so a hostile publisher cannot make the shared
        # heap grow at a moment the UI task does not control.
        link, make = make_link()
        link.pump(T0)
        make.made[0].deliver(b"edgewise/X/slot/a", b"x" * 5000)
        link.pump(T0 + 10)
        self.assertEqual(link.drain(), [])
        self.assertEqual(link.dropped_in, 1)

    def test_outbox_is_bounded_and_drops_oldest(self):
        link, _ = make_link()
        for i in range(mqtt_link.OUTBOX_MAX + 5):
            link.publish_event(b'{"n":%d}' % i)
        self.assertEqual(len(link._outbox), mqtt_link.OUTBOX_MAX)
        self.assertEqual(link.dropped_out, 5)
        # The freshest events survived; a stale ack is worth less than a new one.
        self.assertEqual(link._outbox[-1][1], b'{"n":12}')

    def test_queued_events_are_published_on_the_next_service(self):
        broker = FakeBroker()
        link, _ = make_link(broker)
        link.pump(T0)
        link.publish_event(b'{"type":"ack"}')
        link.pump(T0 + 10)
        self.assertEqual(broker.last("/event"), b'{"type":"ack"}')

    def test_publishing_while_offline_does_not_raise(self):
        cfg = make_cfg()
        link = Link(BrokerSpec(cfg), client_factory=factory(), threaded=False)
        link.publish_event(b'{"type":"ack"}')   # never started
        self.assertEqual(len(link._outbox), 1)


class TestQoSChoice(unittest.TestCase):
    """Why everything publishes at QoS 0."""

    def test_everything_is_published_at_qos_zero(self):
        broker = FakeBroker()
        link, _ = make_link(broker)
        link.pump(T0)
        link.publish_event(b"{}")
        link.publish_slot("kiln", b'{"state":"done"}')
        link.pump(T0 + 10)
        for topic, _, _, qos in broker.published:
            self.assertEqual(qos, 0, topic)

    def test_qos1_after_check_msg_is_the_bug_being_avoided(self):
        """The concrete reason, pinned so nobody 'improves' it back to QoS 1.

        check_msg() leaves the socket non-blocking. A qos=1 publish then blocks
        waiting for a PUBACK it cannot wait for, and fails with EAGAIN.
        """
        client = FakeMQTTClient(None, FakeBroker())
        client.setblocking_trap = True
        client.connect()
        client.check_msg()
        with self.assertRaises(OSError):
            client.publish(b"t", b"payload", qos=1)
        # QoS 0 is unaffected, which is what makes it the right choice here.
        client.publish(b"t", b"payload", qos=0)

    def test_slot_publishes_are_retained(self):
        broker = FakeBroker()
        link, _ = make_link(broker)
        link.pump(T0)
        link.publish_slot("kiln", b'{"state":"done"}')
        link.pump(T0 + 10)
        retained = [r for t, _, r, _ in broker.published if t.endswith("slot/kiln")]
        self.assertEqual(retained, [True])

    def test_events_are_not_retained(self):
        # An ack is a moment, not a state. Retaining it would replay a stale
        # approval to every future subscriber.
        broker = FakeBroker()
        link, _ = make_link(broker)
        link.pump(T0)
        link.publish_event(b'{"type":"ack"}')
        link.pump(T0 + 10)
        retained = [r for t, _, r, _ in broker.published if t.endswith("/event")]
        self.assertEqual(retained, [False])


class TestKeepalive(unittest.TestCase):
    def test_pings_at_half_the_keepalive(self):
        """umqtt.simple never pings by itself.

        Without this the broker drops an idle link at the keepalive, and the
        badge only finds out on its next publish -- so a board that nobody has
        touched for two minutes silently stops receiving.
        """
        link, make = make_link()
        link.pump(T0)
        client = make.made[0]
        self.assertEqual(client.pings, 0)
        link.pump(T0 + mqtt_link.PING_INTERVAL_MS + 100)
        self.assertEqual(client.pings, 1)

    def test_does_not_ping_early(self):
        link, make = make_link()
        link.pump(T0)
        link.pump(T0 + mqtt_link.PING_INTERVAL_MS - 1000)
        self.assertEqual(make.made[0].pings, 0)


class TestRetainedRebuild(unittest.TestCase):
    """Reconnecting repaints the board from the broker's retained messages."""

    def test_retained_messages_replay_on_subscribe(self):
        broker = FakeBroker()
        cfg = make_cfg()
        root = BrokerSpec(cfg).root()
        broker.publish(root + "/slot/kiln", b'{"state":"needs_you"}', retain=True)
        broker.publish(root + "/slot/build", b'{"state":"working"}', retain=True)

        link, make = make_link(broker, cfg=cfg)
        link.pump(T0)
        make.made[0].deliver_retained()
        for _ in range(4):
            link.pump(T0 + 10)

        names = sorted(route(topic_suffix(t, root))[1] for t, _ in link.drain(10))
        self.assertEqual(names, ["build", "kiln"])

    def test_an_empty_retained_payload_clears_the_broker_copy(self):
        broker = FakeBroker()
        root = "edgewise/X"
        broker.publish(root + "/slot/kiln", b'{"state":"done"}', retain=True)
        self.assertIn(root + "/slot/kiln", broker.retained)
        broker.publish(root + "/slot/kiln", b"", retain=True)
        self.assertNotIn(root + "/slot/kiln", broker.retained)


if __name__ == "__main__":
    unittest.main()
