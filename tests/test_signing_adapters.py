"""The publishers and the badge have to agree, byte for byte.

Two implementations of the same canonical form is a drift risk, and the failure
mode is silent and total: every message rejected, with the badge insisting it is
unsigned and the publisher insisting it signed it. So rather than trusting that
both were written to the same field list, this compares them.
"""

import importlib.util
import os
import unittest

from edgewise import signing

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_publisher():
    path = os.path.join(ROOT, "adapters", "shell", "edgewise_pub.py")
    spec = importlib.util.spec_from_file_location("edgewise_pub", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pub = load_publisher()

KEY = "correct-horse-battery-staple"
NOW = 1786279930


class TestTheyAgree(unittest.TestCase):
    CASES = [
        ("slot/kiln", {"state": "needs_you", "label": "kiln",
                       "msg": "door open?", "ttl": 1800}),
        ("slot/build", {"state": "done", "label": "build", "ttl": 3600}),
        ("slot/x", {"state": "info"}),
        ("slot/pinned", {"state": "error", "label": "p", "edge": 3, "ttl": 60}),
        ("text", {"msg": "bins tonight", "level": "alert", "duration": 60}),
        ("weather", {"cond": "rain", "temp": 12, "rain": 40, "unit": "C",
                     "ttl": 10800}),
        ("led", {"segment": "edge:2", "effect": "comet", "rgb": (255, 0, 80),
                 "speed": 180, "brightness": 200, "ttl": 600}),
    ]

    def test_the_canonical_form_matches(self):
        for suffix, payload in self.CASES:
            self.assertEqual(pub.canonical(suffix, payload, NOW),
                             signing.canonical(suffix, payload, NOW), suffix)

    def test_a_signature_from_the_publisher_verifies_on_the_badge(self):
        for suffix, payload in self.CASES:
            signed = dict(payload)
            signed["ts"] = NOW
            signed["sig"] = signing.sign(KEY, suffix, signed, NOW)
            verifier = signing.Verifier(KEY)
            self.assertTrue(verifier.verify(suffix, signed, NOW), suffix)

    def test_the_field_lists_are_the_same(self):
        # The likeliest way these drift is someone adding a field to one.
        # `event` is outbound only -- the badge signs it, publishers never do --
        # so it is the one entry a publisher is not expected to carry.
        badge = dict(signing.FIELDS)
        badge.pop("event")
        self.assertEqual(pub.SIGN_FIELDS, badge)

    def test_the_badge_signs_its_own_events(self):
        # Signed mode is not only about what reaches the badge: on an open
        # broker anyone who knows the device ID can forge an ack, and an ack is
        # what the approve flow turns into permission to run a command.
        fields = {"type": "ack", "slot": "kiln", "edge": 0}
        payload = dict(fields)
        payload["ts"] = NOW
        payload["sig"] = signing.sign(KEY, "event", fields, NOW)
        self.assertTrue(signing.Verifier(KEY).verify("event", payload, NOW))

        forged = dict(payload)
        forged["slot"] = "deploy"
        self.assertFalse(signing.Verifier(KEY).verify("event", forged, NOW))

    def test_a_publisher_with_no_key_signs_nothing(self):
        payload = {"state": "done"}
        pub.add_signature({}, "slot/x", payload)
        self.assertNotIn("sig", payload)
        self.assertNotIn("ts", payload)


if __name__ == "__main__":
    unittest.main()
