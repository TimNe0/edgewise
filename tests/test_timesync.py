"""NTP, and the clock that depends on it.

Nothing in Tildagon OS sets the badge's clock outside the OTA updater, so a
badge that has not been updated this session thinks it is 1970. That is not
just a cosmetic problem: every outbound event carries `ts`, and the first ack
ever published from real hardware read `"ts":627`.

The simulator ships `sim/fakes/ntptime.py` containing `def settime(): pass`, so
it cannot fail here and cannot tell us anything either -- the same blind spot it
has for the LED ring. Hence fakes with teeth.
"""

import unittest

from edgewise import clock, conf, timesync


class FakeNtp:
    """An NTP module that can succeed, fail, or lie."""

    def __init__(self, clock_obj=None, error=None):
        self.clock = clock_obj
        self.error = error
        self.calls = 0

    def settime(self):
        self.calls += 1
        if self.error is not None:
            raise self.error
        if self.clock is not None:
            self.clock.year = 2026


class FakeTime:
    def __init__(self, year=1970, epoch=627):
        self.year = year
        self.epoch = epoch

    def localtime(self):
        return (self.year, 8, 9, 12, 0, 0, 0, 0)

    def time(self):
        return self.epoch if self.year >= 2024 else 627


def sync(ntp, time_mod):
    return timesync.TimeSync(ntp=ntp, time_mod=time_mod, threaded=False)


class TestSync(unittest.TestCase):
    def test_a_successful_sync_reports_synced(self):
        t = FakeTime()
        t.epoch = 1786261821
        s = sync(FakeNtp(t), t)
        self.assertTrue(s.step(1000))
        self.assertTrue(s.synced)
        self.assertEqual(s.status(), "synced")

    def test_failure_is_soft_and_retried(self):
        t = FakeTime()
        s = sync(FakeNtp(t, error=OSError("no route to host")), t)
        self.assertFalse(s.step(1000))
        self.assertFalse(s.synced)
        # Still usable, still counting, nothing raised.
        self.assertIn("no route", s.status())

    def test_it_backs_off_rather_than_hammering_the_server(self):
        t = FakeTime()
        ntp = FakeNtp(t, error=OSError("timed out"))
        s = sync(ntp, t)
        s.step(0)
        self.assertEqual(ntp.calls, 1)
        # Too soon: the retry delay has not elapsed.
        s.step(100)
        self.assertEqual(ntp.calls, 1)
        s.step(timesync.RETRY_MS[1] + 1)
        self.assertEqual(ntp.calls, 2)

    def test_a_server_that_answers_with_nonsense_is_not_a_success(self):
        # settime() returning without raising is not proof of anything. A reply
        # that leaves the clock in 1970 has to read as failure, or the badge
        # would publish 1970 timestamps believing they were checked.
        t = FakeTime()
        s = sync(FakeNtp(clock_obj=None), t)   # settime succeeds, clock unmoved
        self.assertFalse(s.step(1000))
        self.assertFalse(s.synced)
        self.assertEqual(s.last_error, "bad ntp reply")

    def test_it_resyncs_later_rather_than_trusting_the_rtc_forever(self):
        t = FakeTime()
        t.epoch = 1786261821
        ntp = FakeNtp(t)
        s = sync(ntp, t)
        s.step(0)
        self.assertEqual(ntp.calls, 1)
        s.step(timesync.RESYNC_MS - 1000)
        self.assertEqual(ntp.calls, 1)
        s.step(timesync.RESYNC_MS + 1)
        self.assertEqual(ntp.calls, 2)

    def test_an_unstarted_sync_does_not_pump(self):
        t = FakeTime()
        ntp = FakeNtp(t)
        s = sync(ntp, t)
        s.pump(1000)
        self.assertEqual(ntp.calls, 0)

    def test_status_before_the_first_attempt(self):
        self.assertEqual(sync(FakeNtp(), FakeTime()).status(), "not tried")


class TestLocalTime(unittest.TestCase):
    def test_no_clock_means_no_time_rather_than_midnight(self):
        self.assertIsNone(clock.local_hhmm(0, FakeTime(year=1970)))

    def test_utc(self):
        # 1786261821 is the broker's timestamp for the first ack the badge ever
        # published: 07:50:21 UTC, which showed as 08:50 locally because this
        # machine is on BST. Getting that hour wrong is exactly the mistake a
        # UTC offset setting exists to let a user make, and fix.
        self.assertEqual(clock.local_hhmm(0, FakeTime(2026, 1786261821)),
                         "07:50")

    def test_a_positive_offset(self):
        self.assertEqual(clock.local_hhmm(60, FakeTime(2026, 1786261821)),
                         "08:50")

    def test_a_negative_offset_wraps_backwards_over_midnight(self):
        # 00:50 UTC minus two hours is 22:50 the previous day.
        midnight_fifty = 1786236600
        self.assertEqual(clock.local_hhmm(-120, FakeTime(2026, midnight_fifty)),
                         "22:50")

    def test_a_half_hour_zone(self):
        self.assertEqual(clock.local_hhmm(330, FakeTime(2026, 1786261821)),
                         "13:20")


class TestUtcOffsetEntry(unittest.TestCase):
    """Typed rather than picked, because the number dialog's alphabet is
    "0123456789." -- no minus sign, so half the world could not enter theirs."""

    def test_plain_hours(self):
        self.assertEqual(clock.parse_utc_offset("1"), 60)
        self.assertEqual(clock.parse_utc_offset("+1"), 60)
        self.assertEqual(clock.parse_utc_offset("-5"), -300)

    def test_hours_and_minutes(self):
        self.assertEqual(clock.parse_utc_offset("5:30"), 330)
        self.assertEqual(clock.parse_utc_offset("-3:30"), -210)

    def test_zero(self):
        self.assertEqual(clock.parse_utc_offset("0"), 0)

    def test_rubbish_keeps_what_was_there(self):
        self.assertEqual(clock.parse_utc_offset("banana", 60), 60)
        self.assertEqual(clock.parse_utc_offset("", 60), 60)
        self.assertEqual(clock.parse_utc_offset(None, 60), 60)

    def test_impossible_zones_are_refused(self):
        # Real zones run from -12:00 to +14:00.
        self.assertEqual(clock.parse_utc_offset("+25", 0), 0)
        self.assertEqual(clock.parse_utc_offset("-13", 0), 0)
        self.assertEqual(clock.parse_utc_offset("+14"), 840)

    def test_it_round_trips_through_the_display_format(self):
        for minutes in (-720, -210, 0, 60, 330, 840):
            self.assertEqual(
                clock.parse_utc_offset(clock.format_utc_offset(minutes)),
                minutes)

    def test_the_config_clamps_it_too(self):
        cfg = conf.validate({"utc_offset": 99999})
        self.assertEqual(cfg["utc_offset"], conf.UTC_OFFSET_MAX)


if __name__ == "__main__":
    unittest.main()
