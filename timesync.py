"""Asking the badge what time it is, because nothing else will.

`ntptime` is frozen into Tildagon OS -- `tildagon/manifest.py` requires it --
but the only caller in the whole firmware is the OTA updater, which syncs the
clock as a side effect of installing an update. A badge that has not been
updated this session believes it is 1970. `modules/wifi.py` notes that
`RTC.ntp_sync()` does not exist in this MicroPython build, so there is no
automatic route either. An app that wants the time has to ask.

This is not only about a clock face. Every outbound event carries `ts`, and
until the badge knows the date that field is a lie dressed as data -- the first
ack ever published from hardware read `"ts":627`. Signed mode (M6) rejects
messages outside a 60-second window, so a badge stuck in 1970 would have every
message it signs rejected, for a reason nobody would find quickly.

`ntptime.settime()` blocks on a UDP round trip, so it runs on its own thread for
the same reason the MQTT client does: the badge has one asyncio task, and
anything that blocks on it freezes the buttons and the ring together. Where
threads are unavailable the caller can step it instead, and pays the stall.

It fails soft, always. No Wi-Fi, a captive portal, or a firewall eating port 123
means no time -- the clock shows nothing, `ts` stays 0, and the status board
carries on doing its actual job.
"""

from . import clock

# First attempt straight away, then backing off. Wi-Fi is usually still
# associating when the app starts, so the first try failing is the normal case
# rather than an error worth reporting.
RETRY_MS = (0, 5000, 15000, 60000, 300000)

# The ESP32's RTC drifts, and a desk clock that is minutes out is worse than one
# that is obviously stopped. An hour is far more often than the drift needs and
# costs one UDP packet.
RESYNC_MS = 3600000

WORKER_STACK = 4 * 1024


class TimeSync:
    """Owns the clock's relationship with the network. UI-task-safe to read."""

    def __init__(self, ntp=None, time_mod=None, threaded=True):
        self._ntp = ntp
        self._time = time_mod
        self._threaded = threaded

        self.synced = False
        self.attempts = 0
        self.last_error = ""
        # Rebound by the worker, read by the UI. A single attribute rebind is
        # atomic between bytecodes, which is all this relies on -- the same
        # contract mqtt_link works under, and for the same reason.
        self.synced_at_ms = 0

        self._next_ms = 0
        self._stop = False
        self._running = False

    # -- UI side -------------------------------------------------------------

    def start(self):
        if self._running:
            return False
        self._stop = False
        self._running = True
        if self._threaded:
            try:
                import _thread

                _thread.stack_size(WORKER_STACK)
                _thread.start_new_thread(self._loop, ())
                return True
            except Exception as exc:  # noqa: BLE001 - no threads on this build
                self._threaded = False
                print("[edgewise] time sync thread failed, polling:", exc)
        return True

    def stop(self):
        self._stop = True
        self._running = False

    def pump(self, now_ms):
        """Advance on the UI task. A no-op unless threads were unavailable."""
        if self._threaded or not self._running:
            return
        self.step(now_ms)

    def status(self):
        if self.synced:
            return "synced"
        if self.attempts == 0:
            return "not tried"
        return self.last_error or "no time"

    # -- the work ------------------------------------------------------------

    def step(self, now_ms):
        """One attempt, if one is due. Never raises."""
        if self._next_ms and not clock.expired(self._next_ms, now_ms):
            return False
        self.attempts += 1
        try:
            self._settime()
        except Exception as exc:  # noqa: BLE001 - every failure is soft
            self.last_error = str(exc)[:24] or "ntp failed"
            index = min(self.attempts, len(RETRY_MS) - 1)
            self._next_ms = clock.add_ms(now_ms, RETRY_MS[index])
            return False

        # Trust but verify: a server that answers with a nonsense year would
        # otherwise be indistinguishable from success, and the whole point of
        # this module is that a wrong clock looks exactly like a right one.
        if clock.wall_seconds(self._time_module()) == 0:
            self.last_error = "bad ntp reply"
            self._next_ms = clock.add_ms(now_ms, RETRY_MS[-1])
            return False

        self.synced = True
        self.synced_at_ms = now_ms
        self.last_error = ""
        self._next_ms = clock.add_ms(now_ms, RESYNC_MS)
        return True

    def _settime(self):
        ntp = self._ntp
        if ntp is None:
            import ntptime as ntp
        ntp.settime()

    def _time_module(self):
        if self._time is not None:
            return self._time
        try:
            import time

            return time
        except ImportError:  # pragma: no cover - there is always time
            return None

    def _loop(self):
        while not self._stop:
            now = clock.now_ms()
            try:
                self.step(now)
            except Exception as exc:  # noqa: BLE001 - the thread must not die
                self.last_error = str(exc)[:24]
            self._sleep(1000)
        self._running = False

    def _sleep(self, ms):
        try:
            import time

            time.sleep_ms(ms)
        except (ImportError, AttributeError):  # pragma: no cover - CPython
            import time

            time.sleep(ms / 1000.0)
