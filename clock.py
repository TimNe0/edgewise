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
