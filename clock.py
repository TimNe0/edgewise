"""Monotonic time, wrap-safe, and importable without firmware.

`model`, `layout` and `ledfx` all need a clock, and all three have to be
unit-testable under CPython, so none of them may `import time` and reach for
MicroPython's `ticks_*` family directly. They come here instead.

**The rule this module exists to enforce: never store a wall-clock deadline.**
MicroPython's `ticks_ms()` counts within a period of 2**30 ms (about 12.4 days)
and then wraps. It has no relationship to Unix time. A plain `deadline = now +
ttl` followed later by `now > deadline` is correct for twelve days and then
blanks the board, which is exactly the kind of bug that surfaces on the third
day of a festival and never in a test. Every comparison goes through
`diff_ms()`, which is wrap-safe in both directions.

Publisher timestamps (`ts` in a slot payload) are wall-clock and are kept only
for signed-mode skew checks. They never drive expiry.
"""

# MicroPython's ticks period. CPython's fallback uses the same modulus so the
# wrap behaviour -- and therefore the tests covering it -- is identical on both.
TICKS_PERIOD = 1 << 30
_HALF_PERIOD = TICKS_PERIOD >> 1

try:
    from time import ticks_add as _ticks_add
    from time import ticks_diff as _ticks_diff
    from time import ticks_ms as _ticks_ms

    _NATIVE = True
except ImportError:  # pragma: no cover - CPython, simulator, unit tests
    _NATIVE = False


if _NATIVE:  # pragma: no cover - exercised on badge, not in desktop tests

    def now_ms():
        return _ticks_ms()

    def diff_ms(a, b):
        """`a - b`, wrap-safe. Positive when `a` is later than `b`."""
        return _ticks_diff(a, b)

    def add_ms(t, delta):
        return _ticks_add(t, delta)

else:
    import time as _time

    def now_ms():
        return int(_time.monotonic() * 1000) % TICKS_PERIOD

    def diff_ms(a, b):
        # Same semantics as ticks_diff: fold the difference into
        # [-2**29, 2**29) so that a counter which has just wrapped still
        # compares as "slightly later" rather than "twelve days earlier".
        return ((a - b + _HALF_PERIOD) % TICKS_PERIOD) - _HALF_PERIOD

    def add_ms(t, delta):
        return (t + delta) % TICKS_PERIOD


def elapsed_ms(since, now):
    """Milliseconds from `since` to `now`, never negative.

    Callers that only want "how long has this been true" should use this rather
    than `diff_ms`, so a clock that jitters backwards by a millisecond cannot
    produce a negative age and, through it, a negative timer on screen.
    """
    d = diff_ms(now, since)
    return d if d > 0 else 0


def expired(deadline, now):
    """True once `now` has reached `deadline`. Wrap-safe."""
    return diff_ms(now, deadline) >= 0


# The badge has no battery-backed RTC. Until it reaches an NTP server its clock
# reads some time in 1970, and `int(time.time())` returns a number like 627 --
# which is not obviously wrong to anything downstream. The first ack ever
# published from real hardware carried exactly that.
#
# Zero is returned instead, because no real event happens at the epoch: a
# subscriber can test for it, where it cannot test for "suspiciously small".
# This matters beyond tidiness. Signed mode (M6) rejects stale messages using a
# 60-second window on `ts`, and a badge whose clock says 1970 would have every
# message it signs rejected forever, for a reason nobody would find quickly.
CLOCK_SET_YEAR = 2024

# MicroPython on embedded targets counts seconds from 2000-01-01, not 1970. This
# is the difference, and it is exactly 10957 days.
#
# Found on hardware: the first ack published after NTP synced carried
# 839580592, which a subscriber read as 1996-08-09 -- the right day and minute,
# thirty years early. The clock face never showed it, because the offset is a
# whole number of days and the "% 86400" that turns seconds into HH:MM cancels
# it exactly. A wrong epoch can hide behind a right-looking clock forever.
EMBEDDED_EPOCH_OFFSET = 946684800


# Resolved once for the real `time` module. Deliberately not a cache keyed on
# the module object: ids get reused after collection, and a wrong epoch that
# only appears sometimes is far worse than one that is always wrong.
_default_offset = None


def epoch_offset(time_mod):
    """Seconds to add to this platform's time() to get Unix time.

    Asked rather than assumed: `gmtime(0)` names the platform's own epoch, so
    this works on a badge, in CPython and on any future firmware without a
    version check to keep up to date.

    Cached per module, because the answer cannot change while the badge is
    running and this sits under every timestamp the badge publishes as well as
    under the clock on screen.
    """
    try:
        return EMBEDDED_EPOCH_OFFSET if time_mod.gmtime(0)[0] == 2000 else 0
    except Exception:  # noqa: BLE001 - no gmtime at all
        return 0


def wall_seconds(time_mod=None):
    """Unix seconds, or 0 when the badge does not know the date."""
    global _default_offset

    injected = time_mod is not None
    if not injected:
        try:
            import time as time_mod
        except ImportError:  # pragma: no cover - there is always time
            return 0
    try:
        if time_mod.localtime()[0] < CLOCK_SET_YEAR:
            return 0
        if injected:
            return int(time_mod.time()) + epoch_offset(time_mod)
        if _default_offset is None:
            _default_offset = epoch_offset(time_mod)
        return int(time_mod.time()) + _default_offset
    except Exception:  # noqa: BLE001 - no RTC at all
        return 0


def local_hhmm(offset_minutes=0, time_mod=None):
    """"HH:MM" in local time, or None when the badge does not know the date.

    None rather than "00:00": a stopped clock that shows a plausible time is
    worse than one that shows nothing, because only the second is obviously
    not to be trusted.
    """
    seconds = wall_seconds(time_mod)
    if not seconds:
        return None
    total = (seconds + offset_minutes * 60) % 86400
    return "%02d:%02d" % (total // 3600, (total % 3600) // 60)


def parse_utc_offset(text, default=0):
    """"+1", "-5", "5:30", "-3:30" -> minutes. Anything else is the default.

    Typed rather than picked from a list because the number dialog's alphabet is
    "0123456789." with no minus sign, so half the world could not enter theirs.
    """
    if text is None:
        return default
    text = str(text).strip().replace(" ", "")
    if not text:
        return default
    sign = 1
    if text[0] in "+-":
        sign = -1 if text[0] == "-" else 1
        text = text[1:]
    hours, _, minutes = text.partition(":")
    try:
        total = int(hours or 0) * 60 + int(minutes or 0)
    except ValueError:
        return default
    total *= sign
    # Real zones run from -12:00 to +14:00.
    return total if -720 <= total <= 840 else default


def format_utc_offset(minutes):
    sign = "-" if minutes < 0 else "+"
    minutes = abs(int(minutes))
    return "%s%d:%02d" % (sign, minutes // 60, minutes % 60)
