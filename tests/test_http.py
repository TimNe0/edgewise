"""The HTTP bridge's routing table and its waiting room.

The bridge exists so things that cannot speak MQTT -- webhooks, phone
shortcuts, `curl` in a Makefile -- can still drive the badge. Its request
parsing is pure, so all of it is testable here without a socket or a broker;
what is left needs paho and a network and is not tested.

It is deliberately stricter than the badge. The badge ignores anything
malformed, which is right for an untrusted radio and useless for someone who
has just typo'd a state name at a command line and would rather be told.
"""

import importlib.util
import json
import os
import threading
import unittest
from urllib.parse import parse_qs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PATH = os.path.join(ROOT, "adapters", "http", "edgewise-http.py")


def _load():
    spec = importlib.util.spec_from_file_location("edgewise_http", _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


http = _load()


def build(path, query=""):
    return http.build(path, parse_qs(query))


class TestSlots(unittest.TestCase):
    def test_a_state_becomes_a_retained_slot(self):
        suffix, payload, retain = build("/slot/build", "state=done")
        self.assertEqual(suffix, "slot/build")
        self.assertTrue(retain, "slots must be retained or they vanish on reboot")
        self.assertEqual(json.loads(payload)["state"], "done")

    def test_the_label_defaults_to_the_slot_name(self):
        _, payload, _ = build("/slot/kiln", "state=info")
        self.assertEqual(json.loads(payload)["label"], "kiln")

    def test_clear_sends_the_retained_clear_idiom(self):
        suffix, payload, retain = build("/slot/old", "state=clear")
        self.assertEqual((suffix, payload, retain), ("slot/old", "", True))

    def test_an_unknown_state_is_refused_rather_than_ignored(self):
        suffix, why, _ = build("/slot/x", "state=exploded")
        self.assertIsNone(suffix)
        self.assertIn("state must be one of", why)

    def test_slot_names_that_would_break_a_topic_are_refused(self):
        for name in ("a/b", "with space", "hash#", "plus+", "", "x" * 17):
            suffix, _, _ = build("/slot/%s" % name, "state=info")
            self.assertIsNone(suffix, name)

    def test_lengths_are_capped_the_way_the_badge_caps_them(self):
        _, payload, _ = build("/slot/x", "state=info&label=%s&msg=%s"
                              % ("L" * 40, "M" * 200))
        data = json.loads(payload)
        self.assertEqual(len(data["label"]), 16)
        self.assertEqual(len(data["msg"]), 64)

    def test_ttl_and_edge_are_range_checked(self):
        _, payload, _ = build("/slot/x", "state=info&ttl=999999&edge=9")
        data = json.loads(payload)
        self.assertEqual(data["ttl"], 3600, "out of range falls back to default")
        self.assertNotIn("edge", data)
        _, payload, _ = build("/slot/x", "state=info&ttl=600&edge=3")
        self.assertEqual(json.loads(payload)["edge"], 3)


class TestOtherTopics(unittest.TestCase):
    def test_text_is_not_retained(self):
        suffix, payload, retain = build("/text", "msg=bins&level=alert")
        self.assertEqual(suffix, "text")
        self.assertFalse(retain, "a retained message reappears on every connect")
        self.assertEqual(json.loads(payload)["level"], "alert")

    def test_an_unknown_text_level_falls_back_rather_than_failing(self):
        _, payload, _ = build("/text", "msg=hi&level=shouty")
        self.assertEqual(json.loads(payload)["level"], "info")

    def test_weather_needs_at_least_one_real_field(self):
        suffix, _, _ = build("/weather", "unit=F")
        self.assertIsNone(suffix)

    def test_weather_accepts_partial_reports(self):
        _, payload, retain = build("/weather", "temp=7")
        self.assertTrue(retain)
        self.assertEqual(json.loads(payload)["temp"], 7)

    def test_an_unknown_condition_is_refused(self):
        suffix, why, _ = build("/weather", "cond=plague&temp=9")
        self.assertIsNone(suffix)
        self.assertIn("cond must be one of", why)

    def test_unknown_endpoints_say_so(self):
        for path in ("/", "/nope", "/slot", "/slot/a/b", "/text/extra"):
            self.assertIsNone(build(path, "state=info")[0], path)


class TestWaiters(unittest.TestCase):
    """`/wait/<slot>` turns a tap on the badge into an exit code, so the
    handover between the MQTT thread and the HTTP threads has to be right."""

    def test_an_event_releases_the_caller(self):
        waiters = http.Waiters()
        box = waiters.park("deploy")
        waiters.deliver({"type": "ack", "slot": "deploy"})
        self.assertTrue(box["event"].wait(1))
        self.assertEqual(box["result"]["type"], "ack")

    def test_another_slot_does_not(self):
        waiters = http.Waiters()
        box = waiters.park("deploy")
        waiters.deliver({"type": "ack", "slot": "something-else"})
        self.assertFalse(box["event"].wait(0.05))

    def test_a_board_wide_event_releases_nobody(self):
        # snooze and wake carry slot: null, and must not be read as a decision
        # about whatever happened to be waiting.
        waiters = http.Waiters()
        box = waiters.park("deploy")
        waiters.deliver({"type": "snooze", "slot": None})
        self.assertFalse(box["event"].wait(0.05))

    def test_several_callers_on_one_slot_are_all_released(self):
        waiters = http.Waiters()
        boxes = [waiters.park("deploy") for _ in range(3)]
        waiters.deliver({"type": "deny", "slot": "deploy"})
        for box in boxes:
            self.assertTrue(box["event"].wait(1))
            self.assertEqual(box["result"]["type"], "deny")

    def test_leaving_removes_the_caller(self):
        # A client that hangs up must not leave its box behind to be filled by
        # the next ack and leak.
        waiters = http.Waiters()
        box = waiters.park("deploy")
        waiters.leave("deploy", box)
        self.assertEqual(waiters._waiting, {})

    def test_delivery_from_another_thread(self):
        waiters = http.Waiters()
        box = waiters.park("deploy")
        threading.Timer(
            0.05, waiters.deliver, ({"type": "ack", "slot": "deploy"},)).start()
        self.assertTrue(box["event"].wait(2))


if __name__ == "__main__":
    unittest.main()
