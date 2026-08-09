"""Signed mode.

"Require signed" and "Signing key" have been on the settings screen since M0,
persisting a preference that nothing read. A security control that appears to be
on and does nothing is worse than one that is absent, so these tests care most
about the refusals: every way a message can fail to be genuine has to end in
False, including the ways that involve this module being unable to do its job.
"""

import unittest

from edgewise import signing

KEY = "correct-horse-battery-staple"
NOW = 1786279930          # a real badge timestamp, from the first hardware ack


def slot(**over):
    payload = {"state": "needs_you", "label": "kiln", "msg": "door open?",
               "ttl": 1800}
    payload.update(over)
    return payload


def signed(suffix="slot/kiln", payload=None, ts=NOW, key=KEY):
    payload = dict(payload or slot())
    payload["ts"] = ts
    payload["sig"] = signing.sign(key, suffix, payload, ts)
    return payload


class TestHmac(unittest.TestCase):
    """Written out from the hash primitive because MicroPython has hashlib and
    not hmac. That makes it worth checking against the reference."""

    def test_it_matches_the_standard_library(self):
        import hashlib
        import hmac as reference

        for key, message in ((b"key", b"The quick brown fox"),
                             (b"", b""),
                             (b"k" * 200, b"a longer key than the block size"),
                             ("unicode-ish".encode(), "payload".encode())):
            self.assertEqual(
                signing.hmac_sha256(key, message),
                reference.new(key, message, hashlib.sha256).digest(),
                key[:12])

    def test_str_and_bytes_are_interchangeable(self):
        self.assertEqual(signing.hmac_sha256("k", "m"),
                         signing.hmac_sha256(b"k", b"m"))

    def test_a_signature_is_hex_and_fits_the_protocol_field(self):
        sig = signing.sign(KEY, "slot/kiln", slot(), NOW)
        self.assertEqual(len(sig), 64)          # docs/protocol.md caps sig at 64
        self.assertTrue(all(c in "0123456789abcdef" for c in sig))


class TestCanonicalForm(unittest.TestCase):
    """JSON is not canonical -- key order, whitespace and number formatting all
    vary -- so the signature covers named fields in a fixed order instead."""

    def test_field_order_does_not_depend_on_dict_order(self):
        one = {"state": "done", "label": "a", "ttl": 60}
        two = {"ttl": 60, "label": "a", "state": "done"}
        self.assertEqual(signing.canonical("slot/x", one, NOW),
                         signing.canonical("slot/x", two, NOW))

    def test_absent_fields_are_skipped_not_blanked(self):
        text = signing.canonical("slot/x", {"state": "done"}, NOW)
        self.assertNotIn("label=", text)
        self.assertIn("state=done", text)

    def test_the_topic_is_signed_too(self):
        # Otherwise a signature for one slot would move another slot.
        self.assertNotEqual(signing.canonical("slot/a", slot(), NOW),
                            signing.canonical("slot/b", slot(), NOW))

    def test_the_timestamp_is_signed_too(self):
        self.assertNotEqual(signing.canonical("slot/a", slot(), NOW),
                            signing.canonical("slot/a", slot(), NOW + 1))

    def test_a_colour_signs_the_same_from_a_tuple_or_a_string(self):
        # rgb is a triple on the badge and "255,0,80" in a query string.
        as_tuple = signing.canonical("led", {"rgb": (255, 0, 80)}, NOW)
        as_text = signing.canonical("led", {"rgb": "255,0,80"}, NOW)
        self.assertEqual(as_tuple, as_text)


class TestVerify(unittest.TestCase):
    def setUp(self):
        self.v = signing.Verifier(KEY)

    def test_a_genuine_message_verifies(self):
        self.assertTrue(self.v.verify("slot/kiln", signed(), NOW))

    def test_a_tampered_field_does_not(self):
        payload = signed()
        payload["state"] = "done"
        self.assertFalse(self.v.verify("slot/kiln", payload, NOW))

    def test_a_message_moved_to_another_slot_does_not(self):
        self.assertFalse(self.v.verify("slot/other", signed(), NOW))

    def test_the_wrong_key_does_not(self):
        self.assertFalse(self.v.verify(
            "slot/kiln", signed(key="a different key"), NOW))

    def test_an_unsigned_message_does_not(self):
        payload = slot()
        payload["ts"] = NOW
        self.assertFalse(self.v.verify("slot/kiln", payload, NOW))

    def test_a_missing_timestamp_does_not(self):
        payload = signed()
        del payload["ts"]
        self.assertFalse(self.v.verify("slot/kiln", payload, NOW))

    def test_rubbish_in_the_signature_does_not_raise(self):
        for sig in ("", "zz", "not hex at all", "0" * 64, None, 12345):
            payload = signed()
            payload["sig"] = sig
            try:
                self.assertFalse(self.v.verify("slot/kiln", payload, NOW), sig)
            except Exception as exc:  # noqa: BLE001
                self.fail("%r raised %s" % (sig, exc))


class TestFreshness(unittest.TestCase):
    def setUp(self):
        self.v = signing.Verifier(KEY)

    def test_inside_the_window_either_side(self):
        for offset in (-signing.SKEW_S + 1, 0, signing.SKEW_S - 1):
            v = signing.Verifier(KEY)
            self.assertTrue(v.verify("slot/kiln", signed(ts=NOW + offset), NOW),
                            offset)

    def test_too_old_is_refused(self):
        self.assertFalse(self.v.verify(
            "slot/kiln", signed(ts=NOW - signing.SKEW_S - 5), NOW))

    def test_too_far_in_the_future_is_refused(self):
        # A clock ahead of the badge is as suspicious as one behind it, and
        # accepting the future would make captured messages usable later.
        self.assertFalse(self.v.verify(
            "slot/kiln", signed(ts=NOW + signing.SKEW_S + 5), NOW))

    def test_an_unset_badge_clock_verifies_nothing(self):
        # clock.wall_seconds returns 0 before NTP lands. A freshness window
        # against an unknown clock is a coin toss, not a check.
        self.assertFalse(self.v.verify("slot/kiln", signed(), 0))

    def test_an_exact_replay_inside_the_window_is_refused(self):
        payload = signed()
        self.assertTrue(self.v.verify("slot/kiln", payload, NOW))
        self.assertFalse(self.v.verify("slot/kiln", payload, NOW),
                         "the same signature was accepted twice")

    def test_the_replay_memory_does_not_grow_without_bound(self):
        for i in range(signing.REPLAY_MEMORY * 3):
            self.v.verify("slot/kiln", signed(ts=NOW, payload=slot(ttl=i)), NOW)
        self.assertLessEqual(len(self.v._seen), signing.REPLAY_MEMORY)


class TestFailsClosed(unittest.TestCase):
    """Every way this module can be unable to do its job has to end in a
    refusal. A build that cannot check signatures must reject signed traffic,
    not wave it through."""

    def test_no_key_verifies_nothing(self):
        self.assertFalse(signing.Verifier(None).verify("slot/x", signed(), NOW))
        self.assertFalse(signing.Verifier("").verify("slot/x", signed(), NOW))

    def test_usable_is_false_without_a_key(self):
        self.assertFalse(signing.Verifier(None).usable())
        self.assertTrue(signing.Verifier(KEY).usable())

    def test_without_hashing_nothing_verifies(self):
        original = signing._sha256
        signing._sha256 = lambda: None
        try:
            self.assertIsNone(signing.hmac_sha256("k", "m"))
            self.assertIsNone(signing.sign(KEY, "slot/x", slot(), NOW))
            self.assertFalse(signing.Verifier(KEY).usable())
            self.assertFalse(
                signing.Verifier(KEY).verify("slot/kiln", signed(), NOW))
        finally:
            signing._sha256 = original

    def test_refusals_are_counted(self):
        v = signing.Verifier(KEY)
        v.verify("slot/kiln", slot(), NOW)
        self.assertEqual(v.rejected, 1)


if __name__ == "__main__":
    unittest.main()
