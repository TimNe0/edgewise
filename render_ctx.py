"""Renderer backed by the badge's built-in ctx vector canvas.

Ported from SkyScope, with arcs promoted to a first-class operation because the
dashboard is six arcs hugging the rim.

Coordinates are the badge's native centred space: -120..+120 on both axes, y
growing downwards. `rgb` is a 3-tuple of floats in 0.0..1.0 throughout.
"""

SIZE = 240
TWO_PI = 6.283185307179586
DEG = TWO_PI / 360.0


def rgb255(triple):
    """Convert an 0-255 LED colour to the renderer's 0.0-1.0 space.

    The dashboard draws its arcs with the colour the LED engine actually
    computed, caps and all, so the ring and the screen cannot drift apart.
    """
    return (triple[0] / 255.0, triple[1] / 255.0, triple[2] / 255.0)


class CtxRenderer:
    """Adapts the ctx canvas to the renderer interface. No allocation per frame."""

    size = SIZE
    vector = True

    def __init__(self, ctx=None):
        self.ctx = ctx
        self._align = None

    def begin(self, ctx):
        """Bind the ctx handed to draw() for this frame."""
        self.ctx = ctx
        if self._align is None:
            # ctx.LEFT and friends live on the canvas object rather than the
            # module, so this table can only be built once a canvas exists --
            # but the values are constants, so build it once and not per frame.
            self._align = {"left": ctx.LEFT, "center": ctx.CENTER, "right": ctx.RIGHT}
        return self

    def clear(self, rgb):
        self.ctx.rgb(*rgb).rectangle(-120, -120, SIZE, SIZE).fill()

    def line(self, x0, y0, x1, y1, rgb, w=1):
        ctx = self.ctx
        ctx.line_width = w
        ctx.rgb(*rgb).begin_path().move_to(x0, y0).line_to(x1, y1).stroke()

    def circle(self, x, y, r, rgb, fill=False, w=1):
        ctx = self.ctx
        ctx.rgb(*rgb).begin_path().arc(x, y, r, 0, TWO_PI, 0)
        if fill:
            ctx.fill()
        else:
            ctx.line_width = w
            ctx.stroke()

    def arc(self, x, y, r, start_deg, end_deg, rgb, w=8):
        """A stroked arc. Angles in degrees, 0 at twelve o'clock, clockwise.

        Screen space has y growing downwards and ctx measures from three
        o'clock, so the -90 rotation here is what puts 0 at the top of the
        display. Where an *edge* sits is a separate question and not this
        function's business: the badge is a hexagon on its point, so twelve
        o'clock is a corner. `views.edge_centre_deg` owns that offset.
        """
        ctx = self.ctx
        ctx.line_width = w
        ctx.rgb(*rgb).begin_path().arc(
            x, y, r, (start_deg - 90) * DEG, (end_deg - 90) * DEG, 0)
        ctx.stroke()

    def poly(self, pts, rgb, fill=True, w=1):
        self.polys((pts,), rgb, fill, w)

    def polys(self, shapes, rgb, fill=True, w=1):
        """Draw several shapes of one colour as a single path.

        One colour change, one path setup and one fill instead of one of each
        per shape, which is most of the per-frame saving on a busy screen.
        """
        ctx = self.ctx
        started = False
        for pts in shapes:
            if len(pts) < 2:
                continue
            if not started:
                ctx.rgb(*rgb).begin_path()
                started = True
            ctx.move_to(pts[0][0], pts[0][1])
            for i in range(1, len(pts)):
                ctx.line_to(pts[i][0], pts[i][1])
            ctx.close_path()
        if not started:
            return
        if fill:
            ctx.fill()
        else:
            ctx.line_width = w
            ctx.stroke()

    def text(self, s, x, y, rgb, size=12, align="left"):
        ctx = self.ctx
        ctx.font_size = size
        ctx.text_align = self._align.get(align, ctx.LEFT)
        ctx.text_baseline = ctx.MIDDLE
        ctx.rgb(*rgb).move_to(x, y).text(s)

    def text_width(self, s, size=12):
        ctx = self.ctx
        ctx.font_size = size
        return ctx.text_width(s)

    def flush(self):
        # The scheduler's render task presents the ctx frame for us.
        pass
