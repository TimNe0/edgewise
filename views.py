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

    def draw(self, r, board, layout, engine, now_ms, link_state="", cfg=None,
             hhmm=None, weather=None, selected=None):
        r.clear(BG)
        self._arcs(r, layout, engine, now_ms, selected)
        self._labels(r, board, layout, now_ms, selected)
        self._centre(r, board, now_ms, hhmm, weather)
        self._footer(r, link_state, cfg)

    def _arcs(self, r, layout, engine, now_ms, selected=None):
        for edge in range(EDGES):
            start, end = edge_arc(edge)
            colour = engine.edge_colour(edge)
            if colour == (0, 0, 0):
                # An empty edge still shows a faint arc, so the board reads as
                # six slots with four free rather than as two floating lights.
                r.arc(0, 0, ARC_RADIUS, start, end, (0.11, 0.11, 0.13), ARC_WIDTH)
            else:
                r.arc(0, 0, ARC_RADIUS, start, end, rgb255(colour), ARC_WIDTH)
            if edge == selected:
                # A separate ring inside the arc rather than a brighter arc:
                # the arc's colour is the slot's state and must keep meaning
                # that, so the cursor has to be a mark of its own.
                r.arc(0, 0, ARC_RADIUS - ARC_WIDTH, start, end, ACCENT, 3)

    def _labels(self, r, board, layout, now_ms, selected=None):
        for edge in range(EDGES):
            name = layout.slot_at(edge)
            if name is None:
                continue
            slot = board.slots.get(name)
            if slot is None:
                continue
            x, y = edge_anchor(edge, 74)
            colour = ACCENT if edge == selected else (
                DIM if slot.is_stale(now_ms) else FG)
            r.text(slot.label[:10], x, y - 6, colour, size=15, align="center")
            age = short_age(slot.age_ms(now_ms))
            if slot.is_stale(now_ms):
                age += " ?"
            r.text(age, x, y + 8, DIM, size=12, align="center")

    def _centre(self, r, board, now_ms, hhmm=None, weather=None):
        needs, total = board.counts()
        if needs:
            # The clock gets out of the way entirely. Something needs you, and
            # that is the only thing this screen is for at that moment.
            r.text(str(needs), 0, -8, ACCENT, size=42, align="center")
            r.text("need you" if needs > 1 else "needs you",
                   0, 20, ACCENT, size=13, align="center")
            return

        if hhmm and weather:
            # Both: clock up, weather under it, status line last. The clock
            # moves up rather than shrinking -- a small clock is a clock nobody
            # reads from a desk away.
            r.text(hhmm, 0, -28, FG, size=34, align="center")
            self._weather_row(r, weather, 6)
            r.text("all clear" if total else "no jobs", 0, 36, DIM,
                   size=10, align="center")
        elif hhmm:
            # Big, because at rest this is a desk clock and it is being read
            # from across a room rather than at arm's length.
            r.text(hhmm, 0, -6, FG, size=38, align="center")
            r.text("all clear" if total else "no jobs", 0, 24, DIM,
                   size=11, align="center")
        elif weather:
            self._weather_row(r, weather, -6)
            r.text("all clear" if total else "no jobs", 0, 26, DIM,
                   size=11, align="center")
        elif total:
            # Deliberately calm. A board that shouts when nothing is wrong
            # teaches people to ignore it.
            r.text("all clear", 0, 0, DIM, size=16, align="center")
        else:
            r.text("no jobs", 0, -4, DIM, size=15, align="center")
            r.text("waiting", 0, 14, DIM, size=11, align="center")

    def _weather_row(self, r, weather, y):
        """Condition, temperature, chance of rain -- icon then number.

        Laid out by measuring, not by fixed columns: a publisher that sends only
        a temperature should get a centred temperature, not a temperature parked
        where an icon would have been. Every element is optional, and any
        combination of the three has to look deliberate.
        """
        icon = 11
        gap = 7
        parts = []
        if weather.get("cond"):
            parts.append(("icon", icon * 2))
        if weather.get("temp") is not None:
            # The number, then a drawn ring, then the unit. Not "°": nothing in
            # the firmware uses that character, so there is no evidence the font
            # carries it, and a tofu box where the temperature should be is a
            # poor way to find out.
            text = "%d" % weather["temp"]
            unit = weather.get("unit", "C")
            parts.append(("temp",
                          r.text_width(text, 15) + 5 + r.text_width(unit, 11),
                          text, unit))
        if weather.get("rain") is not None:
            text = "%d%%" % weather["rain"]
            parts.append((
                "rain", icon + 3 + r.text_width(text, 13), text))
        if not parts:
            return

        width = sum(p[1] for p in parts) + gap * (len(parts) - 1)
        x = -width / 2.0
        for part in parts:
            if part[0] == "icon":
                weather_icon(r, weather["cond"], x + icon, y, icon)
            elif part[0] == "temp":
                r.text(part[2], x, y + 5, FG, size=15, align="left")
                after = x + r.text_width(part[2], 15)
                r.circle(after + 3, y - 4, 2, FG, fill=False)
                r.text(part[3], after + 6, y + 5, FG, size=11, align="left")
            else:
                raindrop(r, x + 5, y - 1, 6)
                r.text(part[2], x + icon + 3, y + 5, DROP, size=13, align="left")
            x += part[1] + gap

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


SUN = (1.0, 0.78, 0.25)
CLOUD = (0.62, 0.66, 0.72)
DROP = (0.35, 0.68, 1.0)
BOLT = (1.0, 0.85, 0.3)


def _cloud(r, x, y, s, colour):
    """A cloud as three overlapping discs on a bar.

    Filled shapes rather than an outline: at this size a one-pixel stroke of a
    cloud silhouette turns to mush, while a solid blob still reads as a cloud
    from across a desk, which is the whole point of the thing.
    """
    r.circle(x - s * 0.42, y + s * 0.12, s * 0.36, colour, fill=True)
    r.circle(x + s * 0.36, y + s * 0.16, s * 0.32, colour, fill=True)
    r.circle(x - s * 0.02, y - s * 0.14, s * 0.46, colour, fill=True)
    r.poly(((x - s * 0.78, y + s * 0.12), (x + s * 0.7, y + s * 0.12),
            (x + s * 0.7, y + s * 0.48), (x - s * 0.78, y + s * 0.48)),
           colour, fill=True)


def _sun(r, x, y, s, colour=SUN, rays=True):
    r.circle(x, y, s * 0.42, colour, fill=True)
    if not rays:
        return
    import math

    for i in range(8):
        angle = i * math.pi / 4.0
        dx, dy = math.cos(angle), math.sin(angle)
        r.line(x + dx * s * 0.62, y + dy * s * 0.62,
               x + dx * s * 0.92, y + dy * s * 0.92, colour, 2)


def weather_icon(r, cond, x, y, s=11):
    """One small glyph for a condition. Nothing is drawn for an unknown one."""
    if cond == "clear":
        _sun(r, x, y, s)
    elif cond == "part":
        _sun(r, x + s * 0.34, y - s * 0.34, s * 0.66)
        _cloud(r, x - s * 0.12, y + s * 0.2, s * 0.78, CLOUD)
    elif cond == "cloud":
        _cloud(r, x, y, s, CLOUD)
    elif cond == "rain":
        _cloud(r, x, y - s * 0.22, s * 0.88, CLOUD)
        for i in (-1, 0, 1):
            dx = x + i * s * 0.42
            r.line(dx, y + s * 0.5, dx - s * 0.14, y + s * 0.95, DROP, 2)
    elif cond == "snow":
        _cloud(r, x, y - s * 0.22, s * 0.88, CLOUD)
        for i in (-1, 0, 1):
            r.circle(x + i * s * 0.42, y + s * 0.72, s * 0.12,
                     (0.9, 0.94, 1.0), fill=True)
    elif cond == "storm":
        _cloud(r, x, y - s * 0.26, s * 0.88, CLOUD)
        r.poly(((x + s * 0.12, y + s * 0.32), (x - s * 0.28, y + s * 0.42),
                (x - s * 0.02, y + s * 0.52), (x - s * 0.22, y + s * 1.0),
                (x + s * 0.3, y + s * 0.42), (x + s * 0.02, y + s * 0.36)),
               BOLT, fill=True)
    elif cond == "fog":
        for i, width in enumerate((0.9, 0.75, 0.85)):
            dy = y - s * 0.4 + i * s * 0.42
            r.line(x - s * width, dy, x + s * width, dy, CLOUD, 2)
    elif cond == "wind":
        for i, width in enumerate((0.8, 1.0)):
            dy = y - s * 0.22 + i * s * 0.5
            r.line(x - s * width, dy, x + s * width * 0.6, dy, CLOUD, 2)
            r.arc(x + s * width * 0.6, dy - s * 0.18, s * 0.2, 90, 300, CLOUD, 2)


def raindrop(r, x, y, s=6, colour=DROP):
    """The chance-of-rain marker: a teardrop, so the number beside it needs no
    label. A percentage on its own would read as humidity, or battery."""
    r.poly(((x, y - s), (x + s * 0.72, y + s * 0.38), (x - s * 0.72, y + s * 0.38)),
           colour, fill=True)
    r.circle(x, y + s * 0.34, s * 0.72, colour, fill=True)


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
