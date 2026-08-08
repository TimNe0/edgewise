"""The LED engine, and above all the flash-rate cap.

`TestStrobeCap` is the photosensitive-seizure control. It is not a style check
and it is not negotiable: if it fails, the app is unsafe to run in front of
someone, whatever else is passing.
"""

import os
import re
import unittest

from edgewise import boards, ledfx
from edgewise.ledfx import (
    EFFECTS, LedEngine, MIN_STROBE_PERIOD_MS, params, period_ms,
)

T0 = 200000
PROFILE = boards.load({})


def luminance(buf, i):
    j = i * 3
    return buf[j] + buf[j + 1] + buf[j + 2]


def transitions_per_second(fx, seg, p, seconds=3, step_ms=1):
    """Worst per-LED count of dark/lit transitions per second.

    Counting transitions rather than peaks catches an effect that flickers
    without ever reaching full black, which is still a strobe.
    """
    n = max(seg) + 1
    buf = bytearray(3 * n)
    worst = 0
    for i in seg:
        lit = None
        count = 0
        for t in range(0, seconds * 1000, step_ms):
            fx(buf, seg, t, p)
            now_lit = luminance(buf, i) > 40
            if lit is not None and now_lit != lit:
                count += 1
            lit = now_lit
        # Two transitions make one flash.
        worst = max(worst, count / 2.0 / seconds)
    return worst


class TestStrobeCap(unittest.TestCase):
    """No effect, at any speed, may flash faster than 3 Hz."""

    LIMIT_HZ = 1000.0 / MIN_STROBE_PERIOD_MS

    def test_period_never_goes_below_the_floor(self):
        for speed in range(-50, 300):
            self.assertGreaterEqual(period_ms(speed), MIN_STROBE_PERIOD_MS, speed)

    def test_smooth_period_floor_is_higher_still(self):
        for speed in range(0, 256):
            self.assertGreaterEqual(ledfx.smooth_period_ms(speed),
                                    ledfx.MIN_SMOOTH_PERIOD_MS, speed)

    def test_every_effect_at_every_speed_stays_under_the_cap(self):
        seg = (0, 1, 2, 3)
        # Sweeping all 256 speeds at 1 ms for 3 s per effect is slow, so step
        # the speed and pin the extremes, where the cap actually binds.
        speeds = list(range(0, 256, 15)) + [254, 255]
        for name, fx in EFFECTS.items():
            for speed in speeds:
                p = params((255, 255, 255), (0, 0, 0), speed=speed, intensity=255)
                hz = transitions_per_second(fx, seg, p)
                with self.subTest(effect=name, speed=speed):
                    self.assertLessEqual(
                        hz, self.LIMIT_HZ + 0.35,
                        "%s at speed %d flashes at %.2f Hz" % (name, speed, hz))

    def test_the_worst_case_request_is_still_safe(self):
        # What a hostile publisher would actually send.
        p = params((255, 255, 255), speed=255, intensity=255, brightness=255)
        hz = transitions_per_second(EFFECTS["blink"], (0, 1), p)
        self.assertLessEqual(hz, self.LIMIT_HZ + 0.35)

    def test_the_detector_would_catch_an_uncapped_effect(self):
        """Negative control: prove the sweep can fail.

        Every other test here passes when the caps work. This one passes only
        if the *measurement* works, which is what stops the sweep quietly
        degrading into a no-op that reports 0 Hz for everything and reassures
        us forever. The stand-in derives its period straight from speed, which
        is exactly the mistake the real effects must not make; measured at
        ~9.8 Hz against a 3 Hz limit.
        """
        def fx_uncapped(buf, seg, t, p):
            cycle = 4000 - (3900 * p[ledfx.P_SPEED]) // 255
            on = (t % cycle) * 2 < cycle
            for i in seg:
                if on:
                    ledfx._set(buf, i, 255, 255, 255)
                else:
                    ledfx._set(buf, i, 0, 0, 0)

        hz = transitions_per_second(fx_uncapped, (0, 1),
                                    params((255, 255, 255), speed=255))
        self.assertGreater(hz, self.LIMIT_HZ * 2,
                           "the strobe detector is not detecting anything")

    def test_no_effect_derives_a_rate_without_the_helper(self):
        """Structural: the cap only holds because period_ms() is the only route.

        An effect that divided by speed itself would be capped by nothing and
        would look perfectly reasonable in review, so this reads the source.
        """
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "ledfx.py")
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()
        body = source.split("# -- effects", 1)[1]
        offenders = []
        for line in body.splitlines():
            code = line.split("#", 1)[0]
            if "P_SPEED" not in code:
                continue
            if "period_ms(" in code:
                continue
            offenders.append(line.strip())
        self.assertEqual(offenders, [],
                         "speed used without going through period_ms()")


class TestEffectsArePure(unittest.TestCase):
    def test_same_time_gives_the_same_frame(self):
        seg = (0, 1, 2, 3)
        p = params((200, 100, 50), (10, 20, 30), speed=90, intensity=140)
        for name, fx in EFFECTS.items():
            a = bytearray(12)
            b = bytearray(12)
            fx(a, seg, 12345, p)
            fx(b, seg, 12345, p)
            self.assertEqual(a, b, name)

    def test_sparkle_is_deterministic_across_runs(self):
        seg = (0, 1, 2, 3)
        p = params((255, 255, 255), speed=100, intensity=128)
        frames = []
        for _ in range(3):
            buf = bytearray(12)
            EFFECTS["sparkle"](buf, seg, 7777, p)
            frames.append(bytes(buf))
        self.assertEqual(len(set(frames)), 1)

    def test_no_effect_writes_outside_its_segment(self):
        seg = (1, 2)
        p = params((255, 255, 255), (255, 255, 255), speed=200, intensity=255)
        for name, fx in EFFECTS.items():
            buf = bytearray(3 * 6)
            fx(buf, seg, 5000, p)
            for i in (0, 3, 4, 5):
                with self.subTest(effect=name, led=i):
                    self.assertEqual(luminance(buf, i), 0)

    def test_every_byte_stays_in_range(self):
        seg = (0, 1, 2)
        for name, fx in EFFECTS.items():
            for speed in (0, 128, 255):
                for t in (0, 137, 999, 100000):
                    buf = bytearray(9)
                    fx(buf, seg, t, params((255, 255, 255), (255, 255, 255),
                                           speed=speed, intensity=255))
                    for value in buf:
                        self.assertTrue(0 <= value <= 255, name)

    def test_empty_segment_is_harmless(self):
        for name, fx in EFFECTS.items():
            fx(bytearray(9), (), 1000, params((255, 0, 0)))


class TestEngineCaps(unittest.TestCase):
    def setUp(self):
        self.engine = LedEngine(PROFILE, {"brightness": 255, "palette": "default"})

    def test_brightness_ceiling_is_applied(self):
        self.engine.brightness = 128
        self.engine.set_state(0, "done", 0, T0)
        self.engine.render(T0)
        self.assertLessEqual(max(self.engine.frame()), 128)

    def test_night_mode_dims_further(self):
        self.engine.brightness = 255
        self.engine.set_state(0, "done", 0, T0)
        self.engine.render(T0)
        bright = max(self.engine.frame())
        self.engine.night_level = 25
        self.engine.render(T0 + 1)
        self.assertLess(max(self.engine.frame()), bright)

    def test_a_raw_override_cannot_exceed_the_ceiling(self):
        # The path the spec calls out: raw `led` control may change the look of
        # a segment but may not escape the caps.
        self.engine.brightness = 60
        self.engine.set_raw({"segment": "edge:0", "effect": "solid",
                             "rgb": (255, 255, 255), "brightness": 255,
                             "ttl": 600}, T0)
        self.engine.render(T0)
        self.assertLessEqual(max(self.engine.frame()), 60)

    def test_raw_shadows_semantic_then_gives_it_back(self):
        self.engine.set_state(0, "done", 0, T0)
        self.engine.render(T0)
        semantic = self.engine.edge_colour(0)

        self.engine.set_raw({"segment": "edge:0", "effect": "solid",
                             "rgb": (255, 0, 255), "ttl": 1}, T0)
        self.engine.render(T0 + 10)
        self.assertNotEqual(self.engine.edge_colour(0), semantic)

        # TTL lapses: the semantic layer was never removed, only hidden.
        self.engine.render(T0 + 2000)
        self.assertEqual(self.engine.edge_colour(0), semantic)

    def test_explicit_led_list_cannot_reach_past_the_ring(self):
        # tildagonos.leds 13..18 are not the ring. A payload naming them must
        # not be able to drive them.
        self.engine.set_raw({"leds": [0, 1, 99, 500], "effect": "solid",
                             "rgb": (255, 255, 255), "ttl": 600}, T0)
        self.engine.render(T0)
        self.assertEqual(len(self.engine.frame()), 3 * PROFILE.led_count)


class TestEngineFrames(unittest.TestCase):
    def setUp(self):
        self.engine = LedEngine(PROFILE, {"brightness": 200})

    def test_unchanged_frames_are_not_rewritten(self):
        self.engine.set_state(0, "done", 0, T0)
        self.assertTrue(self.engine.render(T0))
        self.assertFalse(self.engine.render(T0))

    def test_an_animated_state_keeps_changing(self):
        self.engine.set_state(0, "needs_you", 0, T0)
        self.engine.render(T0)
        changed = any(self.engine.render(T0 + dt)
                      for dt in range(50, 1200, 50))
        self.assertTrue(changed)

    def test_edge_colour_matches_the_frame(self):
        self.engine.set_state(2, "error", 5000, T0)
        self.engine.render(T0)
        led = PROFILE.edge_leds[2][0]
        frame = self.engine.frame()
        self.assertEqual(self.engine.edge_colour(2),
                         (frame[led * 3], frame[led * 3 + 1], frame[led * 3 + 2]))

    def test_states_are_distinguishable(self):
        seen = {}
        for state in ("working", "needs_you", "done", "error", "info"):
            self.engine.set_state(0, state, 5000, T0)
            self.engine.render(T0)
            seen[state] = self.engine.edge_colour(0)
        self.assertEqual(len(set(seen.values())), len(seen), seen)

    def test_cleared_edge_goes_dark(self):
        self.engine.set_state(1, "done", 0, T0)
        self.engine.render(T0)
        self.engine.clear_state(1)
        self.engine.render(T0 + 10)
        self.assertEqual(self.engine.edge_colour(1), (0, 0, 0))


class TestAllocation(unittest.TestCase):
    def test_render_does_not_grow_the_heap(self):
        """Twenty frames a second for hours: per-frame garbage is not optional.

        CPython has no gc.mem_alloc(), so this uses tracemalloc as a proxy. The
        real number comes from the badge; this catches the obvious regressions,
        like rebuilding a list of tuples per frame.
        """
        import tracemalloc

        engine = LedEngine(PROFILE, {"brightness": 200})
        for edge in range(6):
            engine.set_state(edge, "needs_you", 0, T0)
        engine.render(T0)

        tracemalloc.start()
        before = tracemalloc.get_traced_memory()[0]
        for i in range(200):
            engine.render(T0 + i * 50)
        after = tracemalloc.get_traced_memory()[0]
        tracemalloc.stop()
        self.assertLess(after - before, 20000, "per-frame allocation in render()")


if __name__ == "__main__":
    unittest.main()
