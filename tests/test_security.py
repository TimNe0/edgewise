"""Hostile input. Nothing here may raise, and nothing may exceed a cap."""

import unittest

from edgewise import fixtures, model, security
from edgewise.security import (
    LIMIT_LABEL, LIMIT_MSG, LIMIT_TEXT, RateLimiter, clean_text,
    new_device_id, parse_led, parse_slot, parse_text,
)


class TestHostileCorpus(unittest.TestCase):
    """The corpus that also drives the 24 h soak in tools/chaos.py."""

    def test_slot_parser_never_raises(self):
        for name, payload in fixtures.HOSTILE_SLOT_PAYLOADS:
            with self.subTest(payload=name):
                try:
                    parse_slot(payload)
                except Exception as exc:  # noqa: BLE001 - that is the point
                    self.fail("%s raised %r" % (name, exc))

    def test_led_parser_never_raises(self):
        for name, payload in fixtures.HOSTILE_LED_PAYLOADS:
            with self.subTest(payload=name):
                try:
                    parse_led(payload)
                except Exception as exc:  # noqa: BLE001
                    self.fail("%s raised %r" % (name, exc))

    def test_text_parser_never_raises(self):
        for name, payload in fixtures.HOSTILE_TEXT_PAYLOADS:
            with self.subTest(payload=name):
                try:
                    parse_text(payload)
                except Exception as exc:  # noqa: BLE001
                    self.fail("%s raised %r" % (name, exc))

    def test_no_hostile_slot_payload_exceeds_a_cap(self):
        for name, payload in fixtures.HOSTILE_SLOT_PAYLOADS:
            out = parse_slot(payload)
            if out is None:
                continue
            with self.subTest(payload=name):
                self.assertIn(out["state"], model.STATES + (model.STATE_CLEAR,))
                self.assertLessEqual(len(out.get("label", "")), LIMIT_LABEL)
                self.assertLessEqual(len(out.get("msg", "")), LIMIT_MSG)
                if "edge" in out:
                    self.assertTrue(0 <= out["edge"] < model.EDGES)
                if "ttl" in out:
                    self.assertTrue(model.MIN_TTL_S <= out["ttl"] <= model.MAX_TTL_S)

    def test_no_hostile_led_payload_escapes_the_ring(self):
        for name, payload in fixtures.HOSTILE_LED_PAYLOADS:
            out = parse_led(payload)
            if out is None:
                continue
            with self.subTest(payload=name):
                for key in ("speed", "intensity", "brightness"):
                    self.assertTrue(0 <= out[key] <= 255)
                for component in out["rgb"]:
                    self.assertTrue(0 <= component <= 255)
                self.assertIn(out["effect"], security.EFFECTS)


class TestParseSlot(unittest.TestCase):
    def test_a_real_payload(self):
        out = parse_slot(fixtures.VALID_SLOT)
        self.assertEqual(out["state"], "needs_you")
        self.assertEqual(out["label"], "kiln")
        self.assertEqual(out["msg"], "door open?")
        self.assertEqual(out["edge"], 3)
        self.assertEqual(out["ttl"], 7200)

    def test_empty_payload_is_a_delete(self):
        # The MQTT retained-clear idiom, and the only way to remove a slot
        # without the publisher still being around to say so.
        self.assertEqual(parse_slot(b""), {"state": "clear"})

    def test_explicit_clear(self):
        self.assertEqual(parse_slot(b'{"state":"clear"}'), {"state": "clear"})

    def test_unknown_fields_are_dropped(self):
        out = parse_slot(b'{"state":"working","exec":"rm -rf /","edge":1}')
        self.assertEqual(set(out), {"state", "edge"})

    def test_oversize_payload_is_refused_before_parsing(self):
        self.assertIsNone(parse_slot(b'{"state":"working","msg":"' + b"x" * 600 + b'"}'))

    def test_label_is_truncated_not_rejected(self):
        out = parse_slot(b'{"state":"working","label":"' + b"A" * 100 + b'"}')
        self.assertEqual(len(out["label"]), LIMIT_LABEL)

    def test_label_that_cleans_to_nothing_is_treated_as_absent(self):
        # A blank edge label reads as a rendering fault rather than as abuse,
        # so the slot falls back to its own name instead.
        out = parse_slot(b'{"state":"working","label":"\\u0000\\u0000"}')
        self.assertNotIn("label", out)


class TestCleanText(unittest.TestCase):
    def test_keeps_printable_ascii(self):
        self.assertEqual(clean_text("build #42 ok", 64), "build #42 ok")

    def test_strips_control_characters(self):
        self.assertEqual(clean_text("a\x07b", 64), "a b")

    def test_strips_ansi_escapes(self):
        self.assertNotIn("\x1b", clean_text("\x1b[31mRED", 64))

    def test_collapses_whitespace(self):
        self.assertEqual(clean_text("a" + " " * 20 + "b", 64), "a b")

    def test_truncates(self):
        self.assertEqual(len(clean_text("x" * 500, LIMIT_MSG)), LIMIT_MSG)

    def test_non_ascii_becomes_a_space(self):
        self.assertEqual(clean_text("hi\U0001f525there", 64), "hi there")

    def test_rejects_wrong_types(self):
        for value in (None, 42, [], {}, True):
            self.assertEqual(clean_text(value, 16), "")

    def test_never_exceeds_the_limit(self):
        for _, payload in fixtures.HOSTILE_SLOT_PAYLOADS:
            self.assertLessEqual(len(clean_text(payload, LIMIT_LABEL)), LIMIT_LABEL)


class TestParseLed(unittest.TestCase):
    def test_segment_form(self):
        out = parse_led(b'{"segment":"edge:2","effect":"comet","rgb":[255,0,80],'
                        b'"speed":180,"brightness":200,"ttl":600}')
        self.assertEqual(out["segment"], "edge:2")
        self.assertEqual(out["effect"], "comet")
        self.assertEqual(out["rgb"], (255, 0, 80))

    def test_explicit_led_list(self):
        out = parse_led(b'{"leds":[7,8,9],"rgb":[0,255,0]}')
        self.assertEqual(out["leds"], [7, 8, 9])

    def test_unknown_effect_falls_back_to_solid(self):
        out = parse_led(b'{"segment":"ring","effect":"strobe","rgb":[255,0,0]}')
        self.assertEqual(out["effect"], "solid")

    def test_duplicate_indices_are_collapsed(self):
        out = parse_led(b'{"leds":[3,3,3,4],"rgb":[255,0,0]}')
        self.assertEqual(out["leds"], [3, 4])


class TestParseText(unittest.TestCase):
    def test_a_real_payload(self):
        out = parse_text(b'{"msg":"Bins tonight","duration":120,"level":"info"}')
        self.assertEqual(out["msg"], "Bins tonight")
        self.assertEqual(out["duration"], 120)

    def test_duration_is_capped(self):
        self.assertEqual(parse_text(b'{"msg":"hi","duration":99999}')["duration"],
                         security.DEFAULT_TEXT_DURATION_S)

    def test_unknown_level_falls_back(self):
        self.assertEqual(parse_text(b'{"msg":"hi","level":"emergency"}')["level"], "info")

    def test_msg_is_capped(self):
        out = parse_text(b'{"msg":"' + b"z" * 500 + b'"}')
        self.assertLessEqual(len(out["msg"]), LIMIT_TEXT)


class TestRateLimiter(unittest.TestCase):
    def test_burst_is_allowed_then_throttled(self):
        limiter = RateLimiter(rate=5, burst=10)
        allowed = sum(1 for _ in range(20) if limiter.allow(1000))
        self.assertEqual(allowed, 10)
        self.assertEqual(limiter.dropped, 10)

    def test_tokens_refill_over_time(self):
        limiter = RateLimiter(rate=5, burst=10)
        for _ in range(20):
            limiter.allow(1000)
        self.assertTrue(limiter.allow(1400))

    def test_a_flood_cannot_exceed_the_rate(self):
        limiter = RateLimiter(rate=5, burst=10)
        allowed = 0
        for ms in range(0, 10000, 2):   # 500 msg/s for ten seconds
            if limiter.allow(ms):
                allowed += 1
        # Ten seconds at five per second, plus the initial burst.
        self.assertLessEqual(allowed, 10 + 5 * 10 + 1)


class TestDeviceId(unittest.TestCase):
    def test_shape(self):
        device_id = new_device_id()
        self.assertEqual(len(device_id), 26)
        self.assertTrue(all(c in security._B32 for c in device_id))

    def test_is_not_constant(self):
        self.assertNotEqual(new_device_id(), new_device_id())


if __name__ == "__main__":
    unittest.main()
