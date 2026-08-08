"""The 2026 capacitive touch ring.

Feature-detected, and a clean no-op when absent. That covers three real cases,
none of which is a fault worth logging: a 2024 badge has no touch hardware at
all, the simulator stubs the pads out and never fires them, and a future
firmware might move the interface.

The detection sequence is SkyScope's, which is running in production: identify
the frontboard, then check every pad key actually exists before declaring the
ring usable. That last step matters -- a board reporting 2026 while exposing
fewer pads would otherwise index out of range on the first touch, in the field,
in front of somebody.

No firmware imports at module scope, so the module stays importable off-badge.
"""

PADS = 12


class TouchRing:
    """Reads the pad states. Turning those into gestures is `gestures.py`."""

    def __init__(self, pads=PADS):
        self.pads = pads
        self.available = False
        # Why the ring is unavailable, for the about screen. Not printed: a
        # 2024 badge having no touch hardware is normal.
        self.reason = ""
        self._states = None
        self._keys = ()
        self._snapshot = {}
        self._load()

    def _load(self):
        try:
            from frontboards.utils import detect_frontboard

            if (detect_frontboard() & 0xFF00) != 0x2600:
                self.reason = "2026 badge only"
                return
            from frontboards.twentysix import TwentyTwentySix

            self._states = TwentyTwentySix.touch_states
            self._keys = tuple("TOUCH%02d" % (i + 1) for i in range(self.pads))
            for key in self._keys:
                if key not in self._states:
                    self.reason = "no touch pads"
                    return
            self._snapshot = {i: False for i in range(self.pads)}
            self.available = True
        except Exception as exc:  # noqa: BLE001 - absent hardware, not a fault
            self.reason = str(exc)[:24]

    def read(self):
        """Current pad states as {pad index: bool}. Empty when unavailable.

        The dict is reused rather than rebuilt, because this runs every frame.
        """
        if not self.available:
            return self._snapshot
        states = self._states
        snapshot = self._snapshot
        for i, key in enumerate(self._keys):
            try:
                snapshot[i] = bool(states[key][0])
            except (KeyError, IndexError, TypeError):
                # A pad that stops answering mid-session should degrade to
                # "not touched", not take the app down.
                snapshot[i] = False
        return snapshot

    def describe(self):
        if self.available:
            return "Touch ring: %d pads" % self.pads
        return "Touch ring: " + (self.reason or "unavailable")


class FlipDetector:
    """Face-down detection, for the global snooze.

    Thresholds are deliberately asymmetric. A badge resting at an angle sits
    near zero on the relevant axis, and symmetric thresholds would chatter
    between snoozed and awake as it settled -- which would be worse than not
    having the feature. It also has to stay face-down for a while before
    snoozing, so putting the badge down carelessly does not silence it.

    Which axis and which sign mean "face down" is unverified (V-3b); the
    settings screen has a live readout so it can be confirmed on a real badge
    in a few seconds rather than by rebuilding.
    """

    AXIS = 2
    ENTER = -6.0
    EXIT = 2.0
    HOLD_MS = 1200
    # An I2C transaction, not something to do at 20 Hz.
    POLL_MS = 200

    def __init__(self):
        self.available = False
        self.flipped = False
        self.reason = ""
        self._imu = None
        self._since_ms = None
        self._poll_timer = 0
        self._last = 0.0
        self._load()

    def _load(self):
        try:
            import imu

            imu.acc_read()
            self._imu = imu
            self.available = True
        except Exception as exc:  # noqa: BLE001 - no IMU in the simulator
            self.reason = str(exc)[:24]

    def reading(self):
        return self._last

    def update(self, delta_ms, now_ms):
        """Returns True if the flip state changed."""
        if not self.available:
            return False
        self._poll_timer += delta_ms
        if self._poll_timer < self.POLL_MS:
            return False
        self._poll_timer = 0

        try:
            value = self._imu.acc_read()[self.AXIS]
        except Exception:  # noqa: BLE001
            return False
        self._last = value

        if not self.flipped:
            if value <= self.ENTER:
                if self._since_ms is None:
                    self._since_ms = now_ms
                elif now_ms - self._since_ms >= self.HOLD_MS:
                    self.flipped = True
                    self._since_ms = None
                    return True
            else:
                self._since_ms = None
        else:
            # Waking up is immediate: picking the badge up is an explicit act,
            # and making someone hold it the right way up for a second would
            # feel like the app had stopped responding.
            if value >= self.EXIT:
                self.flipped = False
                self._since_ms = None
                return True
        return False
