"""Slots: what the board is tracking, and when it stops tracking it.

A slot is one job -- a build, a print, a kiln. The badge holds no state that
matters: everything here is rebuilt from the broker's retained messages after a
reboot, which is why crash recovery needs no persistence and no history.

Pure logic, no firmware imports, so all of it is testable under CPython.
"""

from . import clock

STATE_WORKING = "working"
STATE_NEEDS_YOU = "needs_you"
STATE_DONE = "done"
STATE_ERROR = "error"
STATE_INFO = "info"
STATE_CLEAR = "clear"

STATES = (STATE_WORKING, STATE_NEEDS_YOU, STATE_DONE, STATE_ERROR, STATE_INFO)

# Which slots win the six edges when more than six are being tracked, and the
# order the dashboard lists them in. Anything asking for a human comes first.
PRIORITY = {
    STATE_NEEDS_YOU: 0,
    STATE_ERROR: 1,
    STATE_DONE: 2,
    STATE_WORKING: 3,
    STATE_INFO: 4,
}

# Slots that demand attention are never evicted to make room for one that does
# not, and are never quietly dropped.
URGENT = (STATE_NEEDS_YOU, STATE_ERROR)

DEFAULT_TTL_S = 3600
MAX_TTL_S = 86400
MIN_TTL_S = 1

# A `working` slot nobody has mentioned for this long is probably a publisher
# that died rather than a job that is still going. It greys out, but it does not
# disappear -- that distinction is the whole "dead jobs fade" behaviour.
STALE_AFTER_MS = 900000

EDGES = 6
MAX_DISPLAYED = EDGES

# How long after reconnecting we keep accepting retained messages as part of the
# rebuild. Calibrated on hardware (V-1g); a generous default is safe because the
# window only gates *deletion*, never display.
REBUILD_WINDOW_MS = 3000

ORIGIN_MQTT = "mqtt"
ORIGIN_DEMO = "demo"

# What `apply()` reports back, so the caller knows whether the change is worth
# the cost of a re-layout.
CHANGE_NONE = 0
CHANGE_META = 1
CHANGE_STATE = 2
CHANGE_ADDED = 3
CHANGE_REMOVED = 4


class Slot:
    __slots__ = (
        "name", "label", "state", "msg", "edge", "origin",
        "created_ms", "changed_ms", "seen_ms", "expires_ms", "gen",
    )

    def __init__(self, name, now_ms, origin=ORIGIN_MQTT):
        self.name = name
        self.label = name
        self.state = STATE_INFO
        self.msg = ""
        self.edge = None          # explicit pin from the payload, not placement
        self.origin = origin
        self.created_ms = now_ms
        self.changed_ms = now_ms
        self.seen_ms = now_ms
        self.expires_ms = clock.add_ms(now_ms, DEFAULT_TTL_S * 1000)
        self.gen = 0

    def age_ms(self, now_ms):
        """Time in the current state -- what the dashboard's timer shows."""
        return clock.elapsed_ms(self.changed_ms, now_ms)

    def is_stale(self, now_ms):
        return (self.state == STATE_WORKING
                and clock.elapsed_ms(self.seen_ms, now_ms) >= STALE_AFTER_MS)

    def is_urgent(self):
        return self.state in URGENT

    def priority(self):
        return PRIORITY.get(self.state, len(PRIORITY))


def finite(value):
    """True for a real number we can safely clamp and convert.

    NaN needs naming explicitly: it is an instance of float, and *every*
    comparison against it is False, so it slips through the usual
    `if value < low ... if value > high` shape untouched and only fails at
    `int()`. JSON has no NaN literal but plenty of parsers accept one, and a
    publisher can send `{"ttl": NaN}` for free.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if isinstance(value, float):
        if value != value:                      # NaN
            return False
        if value in (float("inf"), float("-inf")):
            return False
    return True


def clamp_ttl(ttl):
    if not finite(ttl):
        return DEFAULT_TTL_S
    if ttl < MIN_TTL_S:
        return MIN_TTL_S
    if ttl > MAX_TTL_S:
        return MAX_TTL_S
    return int(ttl)


class Board:
    """Every tracked slot, and the rules for adding and removing them."""

    def __init__(self, max_slots=12):
        self.max_slots = max_slots
        self.slots = {}
        self.dropped = 0          # incoming messages refused because the board was full
        self._gen = 0
        self._rebuild_until = None

    # -- mutation ------------------------------------------------------------

    def apply(self, name, payload, now_ms, origin=ORIGIN_MQTT):
        """Fold a validated slot payload in. Returns a CHANGE_* code.

        `payload` must already have been through `security.parse_slot`; this
        never sees raw bytes and never parses JSON.
        """
        state = payload.get("state")
        if state == STATE_CLEAR:
            return CHANGE_REMOVED if self.remove(name) else CHANGE_NONE
        if state not in STATES:
            return CHANGE_NONE

        slot = self.slots.get(name)
        added = slot is None
        if added:
            if not self._make_room(now_ms):
                self.dropped += 1
                return CHANGE_NONE
            slot = Slot(name, now_ms, origin)
            self.slots[name] = slot

        change = CHANGE_ADDED if added else CHANGE_NONE

        if slot.state != state:
            slot.state = state
            # Only a state change restarts the timer. A publisher repeating
            # `working` every thirty seconds should not keep resetting the
            # "running for 20m" the user is reading.
            slot.changed_ms = now_ms
            if change == CHANGE_NONE:
                change = CHANGE_STATE

        for key, attr in (("label", "label"), ("msg", "msg")):
            if key in payload:
                value = payload[key]
                if getattr(slot, attr) != value:
                    setattr(slot, attr, value)
                    if change == CHANGE_NONE:
                        change = CHANGE_META

        if "edge" in payload:
            edge = payload["edge"]
            edge = edge if isinstance(edge, int) and 0 <= edge < EDGES else None
            if slot.edge != edge:
                slot.edge = edge
                if change == CHANGE_NONE:
                    change = CHANGE_META

        slot.seen_ms = now_ms
        slot.origin = origin
        slot.gen = self._gen
        slot.expires_ms = clock.add_ms(now_ms, clamp_ttl(payload.get("ttl")) * 1000)
        return change

    def remove(self, name):
        return self.slots.pop(name, None) is not None

    def expire(self, now_ms):
        """Drop slots whose TTL has run out. Returns the names removed."""
        gone = [n for n, s in self.slots.items() if clock.expired(s.expires_ms, now_ms)]
        for name in gone:
            del self.slots[name]
        return gone

    def _make_room(self, now_ms):
        """Ensure there is space for one more slot. False if there cannot be.

        Eviction takes the least important, longest-untouched slot -- but never
        one that is asking for attention. A board that drops a `needs_you` to
        show another `needs_you` has lost the thing it exists to show, so in
        that case the *incoming* message is refused instead.
        """
        if len(self.slots) < self.max_slots:
            return True
        candidates = [s for s in self.slots.values() if not s.is_urgent() and s.edge is None]
        if not candidates:
            return False
        victim = min(candidates, key=lambda s: (-s.priority(), s.changed_ms))
        del self.slots[victim.name]
        return True

    # -- reading -------------------------------------------------------------

    def ordered(self):
        """Every slot, most-urgent first, oldest-in-state first within a state."""
        return sorted(self.slots.values(), key=lambda s: (s.priority(), s.changed_ms))

    def displayed(self):
        """The (at most six) slots that get an edge."""
        return self.ordered()[:MAX_DISPLAYED]

    def names(self):
        return [s.name for s in self.displayed()]

    def pins(self):
        return {s.name: s.edge for s in self.slots.values() if s.edge is not None}

    def urgent_names(self):
        return [s.name for s in self.slots.values() if s.is_urgent()]

    def counts(self, now_ms=None):
        """(needing attention, total tracked) -- the centre of the dashboard."""
        needs = sum(1 for s in self.slots.values() if s.state == STATE_NEEDS_YOU)
        return needs, len(self.slots)

    # -- retained rebuild ----------------------------------------------------

    def begin_rebuild(self, now_ms):
        """Start a generation. Called when the link reports a new session.

        Retained messages arrive in a burst after connecting and repopulate the
        board. Anything still carrying the previous generation when the window
        closes was retained-cleared while we were offline, so it goes -- but
        only if it came from MQTT in the first place.
        """
        self._gen += 1
        self._rebuild_until = clock.add_ms(now_ms, REBUILD_WINDOW_MS)

    def rebuilding(self, now_ms):
        return (self._rebuild_until is not None
                and not clock.expired(self._rebuild_until, now_ms))

    def end_rebuild(self, now_ms):
        """Close the window and sweep. Returns the names removed."""
        if self._rebuild_until is None:
            return []
        self._rebuild_until = None
        gone = [n for n, s in self.slots.items()
                if s.gen != self._gen and s.origin == ORIGIN_MQTT]
        for name in gone:
            del self.slots[name]
        return gone

    def abandon_rebuild(self):
        """Give up on a rebuild without deleting anything.

        If the link drops halfway through the burst we have only part of the
        picture, and sweeping on partial information would blank live slots.
        Better to keep showing what we had until the next successful connect.
        """
        self._rebuild_until = None
        for slot in self.slots.values():
            slot.gen = self._gen
