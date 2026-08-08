"""First-run demo: teach the colour language in about ten seconds.

The demo publishes its synthetic slots through exactly the same
`Board.apply()` path that real MQTT messages take. There is no separate demo
renderer and no demo-only drawing branch, which means if the demo looks right
then real messages look right -- and a rendering bug cannot hide behind a
special case that only the demo exercises.

Demo slots are tagged with a `demo` origin so the retained-rebuild sweep leaves
them alone, and are removed by name when the demo stops.
"""

from . import clock, fixtures, model


class Demo:
    def __init__(self, board):
        self.board = board
        self.caption = ""
        self.showing_qr = False
        self.running = False
        self._t0 = 0
        self._next = 0

    def start(self, now_ms):
        self.running = True
        self.showing_qr = False
        self.caption = ""
        self._t0 = now_ms
        self._next = 0
        self.stop_slots()

    def stop(self):
        self.running = False
        self.caption = ""
        self.showing_qr = False
        self.stop_slots()

    def stop_slots(self):
        for name in fixtures.demo_slots():
            self.board.remove(name)

    def tick(self, now_ms):
        """Advance the script. Returns True if anything changed."""
        if not self.running:
            return False
        elapsed = clock.elapsed_ms(self._t0, now_ms)
        changed = False

        while self._next < len(fixtures.DEMO_SCRIPT):
            entry = fixtures.DEMO_SCRIPT[self._next]
            if entry[0] > elapsed:
                break
            self._next += 1
            changed = self._perform(entry, now_ms) or changed

        if elapsed >= fixtures.DEMO_LOOP_MS:
            # Loops rather than ending, because the demo's job is to be running
            # when somebody wanders past the table.
            self.start(now_ms)
            changed = True
        return changed

    def _perform(self, entry, now_ms):
        kind = entry[1]
        if kind == "caption":
            self.caption = entry[2]
            return True
        if kind == "slot":
            self.board.apply(entry[2], entry[3], now_ms, origin=model.ORIGIN_DEMO)
            return True
        if kind == "clear":
            self.stop_slots()
            self.caption = ""
            return True
        if kind == "qr":
            self.showing_qr = True
            return True
        return False
