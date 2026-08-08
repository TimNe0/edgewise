"""Which slot lights which edge.

The behaviour this exists for: two jobs sit on opposite edges, three sit on
alternating edges, and the board rearranges itself as jobs come and go without
looking twitchy. Everything here is pure -- no firmware imports, no wall clock --
because this is the app's signature behaviour and it is far cheaper to get right
under CPython than on a badge.

Three rules from the spec, in the order they are applied:

* **pinning** -- a slot with an explicit `edge` owns it; everything else spreads
  over what is left.
* **sticky** -- when the target set changes, a slot already sitting on an edge
  that is still in the set does not move. Only the slots that must move, move.
* **hysteresis** -- rebalancing waits 10 s so a job flapping in and out does not
  reshuffle the board. This deliberately does *not* delay placing a new slot;
  see `LayoutEngine`.
"""

from . import clock

EDGES = 6
ALL_MASK = (1 << EDGES) - 1

# Rebalancing (moving slots that are already placed) waits this long after the
# change that triggered it. Placing a newly arrived slot does not wait.
HYSTERESIS_MS = 10000
# Crossfade when a slot leaves an edge and another takes it.
FADE_MS = 500


def _popcount(mask):
    n = 0
    while mask:
        mask &= mask - 1
        n += 1
    return n


def edges_of(mask):
    return tuple(i for i in range(EDGES) if mask & (1 << i))


def gap_vector(subset, n=EDGES):
    """Circular gaps between consecutive occupied edges, sorted ascending.

    The spec calls the goal "maximum minimum circular spacing", which settles
    k=2 and k=3 but not k=4: {0,1,3,4} (gaps 1,2,1,2) and {0,1,2,3} (gaps
    1,1,1,3) both have a minimum gap of 1, yet only the first is the row the
    spec's own table asks for. Comparing the whole ascending gap vector
    lexicographically and taking the largest resolves that -- (1,1,2,2) beats
    (1,1,1,3) -- while still reducing to maximum-minimum-spacing wherever that
    alone is decisive. In words: spread them out, and where that ties, avoid
    leaving one big empty arc.

    It is still not quite enough on its own; see `dispersion`.
    """
    s = sorted(subset)
    if not s:
        return ()
    if len(s) == 1:
        return (n,)
    gaps = [(s[(i + 1) % len(s)] - s[i]) % n or n for i in range(len(s))]
    gaps.sort()
    return tuple(gaps)


def dispersion(subset, n=EDGES):
    """Total circular distance between every pair of occupied edges.

    The tie-breaker the gap vector needs. At k=4, {0,1,3,4} and {0,1,2,4} have
    the *same* gaps (1,1,2,2) -- the multiset cannot tell them apart, because
    what differs is the order the gaps appear in. {0,1,3,4} alternates 1,2,1,2
    and is two opposite pairs; {0,1,2,4} clusters as 1,1,2,2. Summing pairwise
    distances sees the difference (12 against 11) and picks the symmetric one,
    which is the row the spec lists.
    """
    s = sorted(subset)
    total = 0
    for i in range(len(s)):
        for j in range(i + 1, len(s)):
            d = (s[j] - s[i]) % n
            total += min(d, n - d)
    return total


_SUBSET_CACHE = {}


def choose_edges(free_mask, k):
    """The size-k subset of `free_mask` that spreads most evenly.

    Derived by search over the 64 possible masks rather than shipped as a
    lookup table: the table would need a build step and could drift from the
    rule it claims to encode, and this is paid at most once per distinct board
    shape thanks to the cache -- a millisecond or two, then never again.
    """
    if k <= 0:
        return 0
    key = (free_mask, k)
    hit = _SUBSET_CACHE.get(key)
    if hit is not None:
        return hit

    best_mask = 0
    best_key = None
    for mask in range(1 << EDGES):
        if mask & ~free_mask:
            continue
        if _popcount(mask) != k:
            continue
        subset = edges_of(mask)
        # Three levels: spread the gaps, then prefer the more symmetric
        # arrangement of the same gaps, then -- for shapes that are genuinely
        # equivalent, like the six rotations at k=1 -- take the lexicographically
        # smallest, which is what puts a lone slot on edge 0, the top, rather
        # than somewhere arbitrary.
        rank = (gap_vector(subset), dispersion(subset), tuple(-e for e in subset))
        if best_key is None or rank > best_key:
            best_key = rank
            best_mask = mask

    _SUBSET_CACHE[key] = best_mask
    return best_mask


def _circular_distance(a, b):
    d = (a - b) % EDGES
    return min(d, EDGES - d)


def assign(current, active, pins=None):
    """Place `active` slot names onto edges.

    `current` -- {name: edge} from the previous layout; used for stickiness.
    `active`  -- names in priority order (most urgent first). Order decides who
                 wins a contested pin and who gets first choice when moving.
    `pins`    -- {name: edge} explicit requests from the payload's `edge` field.

    Returns (placement, unplaced, pin_denied):
      placement  -- {name: edge}
      unplaced   -- names beyond the six edges, in priority order
      pin_denied -- names whose pin was taken, so the UI can say why
    """
    pins = pins or {}
    placement = {}
    pin_denied = []
    taken_mask = 0

    # 1. Pins first, in priority order: the first claimant of an edge wins and
    #    later ones are demoted to the auto pool rather than silently dropped.
    unpinned = []
    for name in active:
        edge = pins.get(name)
        if edge is None:
            unpinned.append(name)
            continue
        if not isinstance(edge, int) or not 0 <= edge < EDGES:
            unpinned.append(name)
            continue
        bit = 1 << edge
        if taken_mask & bit:
            pin_denied.append(name)
            unpinned.append(name)
            continue
        placement[name] = edge
        taken_mask |= bit

    # 2. The auto set spreads over whatever the pins left. This is the spec's
    #    rule (c): pinned slots own their edges, the rest share the remainder.
    free_mask = ALL_MASK & ~taken_mask
    capacity = _popcount(free_mask)
    k = min(len(unpinned), capacity)
    target_mask = choose_edges(free_mask, k)
    target = set(edges_of(target_mask))

    placeable = unpinned[:k]
    unplaced = unpinned[k:]

    # 3. Stayers keep their edge. This single step is the whole sticky rule, and
    #    it has to run over every slot before anything is assigned -- otherwise
    #    an early mover could take an edge its current occupant was entitled to.
    movers = []
    for name in placeable:
        edge = current.get(name)
        if edge is not None and edge in target:
            placement[name] = edge
            target.discard(edge)
        else:
            movers.append(name)

    # 4. Movers take the nearest remaining edge to where they were, so a
    #    rearrangement reads as slots sliding round rather than teleporting.
    for name in movers:
        if not target:
            unplaced.append(name)
            continue
        previous = current.get(name)
        if previous is None:
            # New slots have nowhere to be near, so pick deterministically --
            # an arbitrary choice here would make the tests flaky and the
            # board's behaviour unexplainable.
            edge = min(target)
        else:
            edge = min(target, key=lambda e: (_circular_distance(e, previous), e))
        placement[name] = edge
        target.discard(edge)

    return placement, unplaced, pin_denied


class LayoutEngine:
    """Holds the current placement and decides when it may change.

    The hysteresis rule is easy to over-apply. Deferring *everything* by ten
    seconds would mean a job that needs you waits ten seconds before its edge
    lights, which would make the board useless for the one thing it is for. So
    the delay applies only to rebalancing -- moving slots that are already
    placed, purely to restore even spacing:

    * a slot arriving takes a free edge immediately, and nobody else moves;
    * a slot leaving frees its edge immediately and only arms the timer;
    * if a slot needing attention arrives and there is no free edge, the
      rebalance is forced now. Attention beats tidiness.
    """

    def __init__(self):
        self.placement = {}
        self.unplaced = []
        self.pin_denied = []
        self._rebalance_at = None
        self._fades = {}

    def edge_of(self, name):
        return self.placement.get(name)

    def slot_at(self, edge):
        for name, e in self.placement.items():
            if e == edge:
                return name
        return None

    def occupied_mask(self):
        mask = 0
        for edge in self.placement.values():
            mask |= 1 << edge
        return mask

    def sync(self, active, pins, now_ms, urgent=()):
        """Reconcile the placement with the active slots.

        `active` is in priority order; `urgent` names slots whose arrival must
        not wait for the hysteresis window. Returns True if anything moved.
        """
        pins = pins or {}
        known = set(self.placement)
        wanted = set(active)

        departed = known - wanted
        arrived = [n for n in active if n not in known]

        changed = False

        # Departures free their edge at once -- a finished job should stop
        # showing immediately -- but only arm the timer for the rebalance.
        for name in departed:
            edge = self.placement.pop(name, None)
            if edge is not None:
                self._fades[edge] = now_ms
            changed = True
        if departed:
            self._arm(now_ms)

        # Arrivals take a free edge without disturbing anyone. Only if there is
        # nowhere free does an arrival force the rebalance.
        forced = False
        for name in arrived:
            edge = self._free_edge(name, pins)
            if edge is not None:
                self.placement[name] = edge
                changed = True
            elif name in urgent:
                forced = True
            else:
                self._arm(now_ms)

        if forced or self._due(now_ms):
            if self._rebalance(active, pins):
                changed = True
            self._rebalance_at = None

        return changed

    def _free_edge(self, name, pins):
        taken = self.occupied_mask()
        pinned = pins.get(name)
        if isinstance(pinned, int) and 0 <= pinned < EDGES:
            return pinned if not taken & (1 << pinned) else None
        free_mask = ALL_MASK & ~taken
        if not free_mask:
            return None
        # Placing into the shape the board would *want* at this size keeps the
        # cheap immediate placement consistent with the eventual rebalance, so
        # in the common case the rebalance turns out to be a no-op.
        k = _popcount(taken) + 1
        preferred = choose_edges(ALL_MASK, k) & free_mask
        candidates = edges_of(preferred) or edges_of(free_mask)
        return candidates[0]

    def _arm(self, now_ms):
        if self._rebalance_at is None:
            self._rebalance_at = clock.add_ms(now_ms, HYSTERESIS_MS)

    def _due(self, now_ms):
        return self._rebalance_at is not None and clock.expired(self._rebalance_at, now_ms)

    def _rebalance(self, active, pins):
        placement, unplaced, denied = assign(self.placement, active, pins)
        moved = placement != self.placement
        self.placement = placement
        self.unplaced = unplaced
        self.pin_denied = denied
        return moved

    def fade_progress(self, edge, now_ms):
        """0.0..1.0 through the crossfade on an edge that changed hands.

        Read by both the LED engine and the screen so the rim arc and the ring
        move together rather than each running its own timer.
        """
        started = self._fades.get(edge)
        if started is None:
            return 1.0
        t = clock.elapsed_ms(started, now_ms)
        if t >= FADE_MS:
            del self._fades[edge]
            return 1.0
        return t / FADE_MS
