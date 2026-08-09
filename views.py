"""Screens: the dashboard, a slot's detail page, and the board calibrator.

Drawing only. Everything here takes a renderer and reads state; nothing mutates
the model, so a rendering bug can never corrupt the board.

The dashboard's rim arcs are drawn with the colour the LED engine actually
computed for that edge, caps and all -- not with a parallel palette lookup. Two
renderers reading one source is what stops the screen and the ring disagreeing,
which would be the most confusing possible failure for a board whose job is to
tell you where to look.
"""

from . import clock, conf
from .render_ctx import rgb255

BG = (0.03, 0.03, 0.04)
DIM = (0.35, 0.35, 0.38)
FG = (0.85, 0.85, 0.88)
ACCENT = (0.0, 0.86, 1.0)
WARN = (1.0, 0.55, 0.0)

EDGES = 6
ARC_RADIUS = 104
ARC_WIDTH = 11
# A gap between arcs so six of them read as six things, not a ring.
ARC_GAP_DEG = 7.0

# The badge is a hexagon standing on a point, so twelve o'clock is a *corner*,
# not an edge. Edge centres sit half a span round from there: 30, 90, 150...
#
# Without this the arcs are drawn half an edge anticlockwise of the LEDs they
# are supposed to be describing -- the screen says top, the ring lights one
# o'clock -- which is the one failure this module exists to prevent. It is also
# not fixable from settings: `rotation` moves in whole 60-degree edges and can
# never express 30.
EDGE_CENTRE_OFFSET_DEG = 30.0


def edge_centre_deg(edge):
    """Clockwise degrees from twelve o'clock to the middle of an edge."""
    return edge * (360.0 / EDGES) + EDGE_CENTRE_OFFSET_DEG


def edge_arc(edge):
    """(start, end) degrees for an edge's rim segment.

    Edge 0 is the first edge clockwise of the top corner -- roughly one to two
    o'clock -- which is where the LED map starts on both board profiles.
    """
    span = 360.0 / EDGES
    centre = edge_centre_deg(edge)
    half = (span - ARC_GAP_DEG) / 2.0
    return centre - half, centre + half


def edge_anchor(edge, radius):
    """Where a label for this edge sits, in screen coordinates."""
    import math

    angle = (edge_centre_deg(edge) - 90.0) * (math.pi / 180.0)
    return math.cos(angle) * radius, math.sin(angle) * radius


def short_age(ms):
    """Compact elapsed time. Space on a round screen is the binding constraint."""
    seconds = ms // 1000
    if seconds < 60:
        return "%ds" % seconds
    if seconds < 3600:
        return "%dm" % (seconds // 60)
    if seconds < 86400:
        return "%dh" % (seconds // 3600)
    return "%dd" % (seconds // 86400)


class Dashboard:
    """The default screen: six arcs, labels, and a count in the middle."""

    def draw(self, r, board, layout, engine, now_ms, link_state="", cfg=None):
        r.clear(BG)
        self._arcs(r, layout, engine, now_ms)
        self._labels(r, board, layout, now_ms)
        self._centre(r, board, now_ms)
        self._footer(r, link_state, cfg)

    def _arcs(self, r, layout, engine, now_ms):
        for edge in range(EDGES):
            start, end = edge_arc(edge)
            colour = engine.edge_colour(edge)
            if colour == (0, 0, 0):
                # An empty edge still shows a faint arc, so the board reads as
                # six slots with four free rather than as two floating lights.
                r.arc(0, 0, ARC_RADIUS, start, end, (0.11, 0.11, 0.13), ARC_WIDTH)
            else:
                r.arc(0, 0, ARC_RADIUS, start, end, rgb255(colour), ARC_WIDTH)

    def _labels(self, r, board, layout, now_ms):
        for edge in range(EDGES):
            name = layout.slot_at(edge)
            if name is None:
                continue
            slot = board.slots.get(name)
            if slot is None:
                continue
            x, y = edge_anchor(edge, 74)
            colour = DIM if slot.is_stale(now_ms) else FG
            r.text(slot.label[:10], x, y - 6, colour, size=15, align="center")
            age = short_age(slot.age_ms(now_ms))
            if slot.is_stale(now_ms):
                age += " ?"
            r.text(age, x, y + 8, DIM, size=12, align="center")

    def _centre(self, r, board, now_ms):
        needs, total = board.counts()
        if needs:
            r.text(str(needs), 0, -8, ACCENT, size=42, align="center")
            r.text("need you" if needs > 1 else "needs you",
                   0, 20, ACCENT, size=13, align="center")
        elif total:
            # Deliberately calm. A board that shouts when nothing is wrong
            # teaches people to ignore it.
            r.text("all clear", 0, 0, DIM, size=16, align="center")
        else:
            r.text("no jobs", 0, -4, DIM, size=15, align="center")
            r.text("waiting", 0, 14, DIM, size=11, align="center")

    def _footer(self, r, link_state, cfg):
        if link_state:
            r.text(link_state, 0, 100, DIM, size=10, align="center")
        if cfg:
            r.text(cfg["device_id"][:6], 0, 112, (0.22, 0.22, 0.25),
                   size=9, align="center")


class DetailView:
    """One slot in full: label, state, message, age, how it got its edge.

    This is what makes approving from the badge usable -- the message is the
    only place the user learns *what* they are being asked about.
    """

    def draw(self, r, slot, edge, now_ms, pinned=False):
        r.clear(BG)
        if slot is None:
            r.text("gone", 0, 0, DIM, size=18, align="center")
            return
        r.text(slot.label[:16], 0, -74, FG, size=20, align="center")
        r.text(slot.state.replace("_", " "), 0, -50, ACCENT, size=14, align="center")

        if slot.msg:
            self._wrapped(r, slot.msg, -22)

        r.text("for " + short_age(slot.age_ms(now_ms)), 0, 44, DIM,
               size=13, align="center")
        where = "edge %d" % edge if edge is not None else "no edge"
        if pinned:
            where += " (pinned)"
        r.text(where, 0, 62, DIM, size=11, align="center")
        r.text("CONFIRM ack  ·  hold deny", 0, 88, (0.4, 0.4, 0.44),
               size=10, align="center")

    def _wrapped(self, r, text, y):
        # The panel is round, so the usable width narrows away from the middle.
        # Three short lines beats two wide ones that clip at the edges.
        words = text.split(" ")
        lines = []
        line = ""
        for word in words:
            candidate = (line + " " + word).strip()
            if len(candidate) > 22 and line:
                lines.append(line)
                line = word
            else:
                line = candidate
        if line:
            lines.append(line)
        for i, text_line in enumerate(lines[:3]):
            r.text(text_line, 0, y + i * 17, FG, size=13, align="center")


class CalibrateView:
    """Answers the one thing no source can: which LEDs make up an edge.

    Two modes, because most boards need only the first:

    * **phase** -- light edge 0 under each hypothesis in turn and ask which one
      looks like a single complete edge. One button press, and it is the whole
      calibration for any board with its LEDs evenly spaced.
    * **per-LED** -- light one LED at a time and ask which edge it is on. Slower,
      but it maps a board nobody has written a profile for, which is what keeps
      a future revision from needing a code change at all.
    """

    MODE_PHASE = "phase"
    MODE_LED = "led"

    def draw(self, r, mode, index, total, phase=None):
        r.clear(BG)
        if mode == self.MODE_PHASE:
            r.text("look at the top", 0, -66, DIM, size=12, align="center")
            r.text("edge", 0, -50, DIM, size=12, align="center")
            r.text("A" if phase == 0 else "B", 0, -12, ACCENT, size=48,
                   align="center")
            r.text("is this ONE", 0, 26, FG, size=14, align="center")
            r.text("complete edge?", 0, 44, FG, size=14, align="center")
            r.text("CONFIRM yes · DOWN no", 0, 84, (0.4, 0.4, 0.44),
                   size=10, align="center")
        else:
            r.text("which edge is lit?", 0, -60, FG, size=15, align="center")
            r.text("LED %d of %d" % (index + 1, total), 0, -36, DIM,
                   size=12, align="center")
            r.text("tap it, or", 0, 40, (0.4, 0.4, 0.44), size=10, align="center")
            r.text("UP/DOWN then CONFIRM", 0, 56, (0.4, 0.4, 0.44),
                   size=10, align="center")
            r.text("CANCEL to give up", 0, 84, (0.35, 0.35, 0.38),
                   size=9, align="center")


class PickerView:
    """First-run board choice, shown only when detection could not decide."""

    def draw(self, r, options, selected):
        r.clear(BG)
        r.text("which badge", 0, -70, FG, size=17, align="center")
        r.text("is this?", 0, -50, FG, size=17, align="center")
        for i, (_, name) in enumerate(options):
            y = -14 + i * 26
            colour = ACCENT if i == selected else DIM
            r.text(name, 0, y, colour, size=15, align="center")
        r.text("you can change this", 0, 74, (0.4, 0.4, 0.44), size=10,
               align="center")
        r.text("in settings", 0, 88, (0.4, 0.4, 0.44), size=10, align="center")


class SettingsView:
    """The settings list. Values live on the right, so a column of them can be
    read at a glance without entering anything."""

    ROWS = 5

    def draw(self, r, model, hint=""):
        r.clear(BG)
        r.text(model.title(), 0, -88, ACCENT, size=13, align="center")

        items = model.items()
        # Scroll so the cursor is never against an edge unless the list ends
        # there; a highlighted row with no context above it reads as the top of
        # the list even when it is the middle.
        first = max(0, min(model.index - self.ROWS // 2, len(items) - self.ROWS))
        for row, item in enumerate(items[first:first + self.ROWS]):
            index = first + row
            y = -50 + row * 24
            selected = index == model.index
            colour = ACCENT if selected else FG
            r.text(item.label, -74, y, colour, size=13, align="left")
            summary = model.summary(item)
            if summary:
                r.text(summary, 78, y, ACCENT if selected else DIM,
                       size=12, align="right")
            elif item.kind == "group":
                r.text(">", 78, y, colour, size=12, align="right")

        if len(items) > self.ROWS:
            r.text("%d/%d" % (model.index + 1, len(items)), 0, 78,
                   (0.35, 0.35, 0.38), size=9, align="center")
        r.text(hint or "CONFIRM select   CANCEL back", 0, 92,
               (0.4, 0.4, 0.44), size=9, align="center")


class DeviceIdView:
    """The full device ID, which is the one thing a publisher cannot guess.

    Shown in two halves: 26 base32 characters on one line at a legible size do
    not fit a 240-pixel round screen, and this is a string people type."""

    def draw(self, r, device_id, prefix):
        r.clear(BG)
        r.text("device id", 0, -84, ACCENT, size=13, align="center")
        r.text(device_id[:13], 0, -52, FG, size=15, align="center")
        r.text(device_id[13:], 0, -32, FG, size=15, align="center")
        r.text("topic", 0, 2, (0.4, 0.4, 0.44), size=10, align="center")
        r.text("%s/<id>/slot/<name>" % prefix, 0, 18, DIM, size=10,
               align="center")
        r.text("anyone who knows this", 0, 52, (0.4, 0.4, 0.44), size=9,
               align="center")
        r.text("can write to your lights", 0, 64, (0.4, 0.4, 0.44), size=9,
               align="center")
        r.text("CANCEL back", 0, 92, (0.4, 0.4, 0.44), size=9, align="center")


class MessageView:
    """A short `text` message from the broker, over the dashboard."""

    def draw(self, r, message, level="info"):
        colour = WARN if level == "alert" else FG
        r.circle(0, 0, 92, (0.06, 0.06, 0.08), fill=True)
        words = message.split(" ")
        lines = []
        line = ""
        for word in words:
            candidate = (line + " " + word).strip()
            if len(candidate) > 18 and line:
                lines.append(line)
                line = word
            else:
                line = candidate
        if line:
            lines.append(line)
        start = -((len(lines) - 1) * 9)
        for i, text_line in enumerate(lines[:4]):
            r.text(text_line, 0, start + i * 18, colour, size=15, align="center")


def link_summary(state, cfg):
    """The one-line broker status in the dashboard footer."""
    if not conf.configured(cfg):
        return "no broker set"
    return state
