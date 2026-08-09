"""Edgewise -- a desk status board for the EMF Tildagon / Spaceagon badge.

Each edge of the hexagon is one job. The edge's LEDs show its state, and when
something needs you it flashes until you acknowledge it from the badge. Anything
that can send one MQTT message can drive it.

One async task does everything on screen. The MQTT link (M2) runs on its own
thread and hands messages across through a bounded mailbox, because the badge
runs a single asyncio task and anything that blocks on it freezes the buttons
and the ring together.
"""

import asyncio
import time

import app
from app_components import Notification, clear_background
from events.input import BUTTON_TYPES, Buttons
from system.eventbus import eventbus
from system.patterndisplay.events import (
    PatternDisable, PatternEnable, PatternReload, PatternSet,
)

from . import boards, clock, conf as C, demo as demo_mod, gestures as gest
from . import httpd, prefs, signing, timesync as timesync_mod
from . import layout as layout_mod, ledfx, model, mqtt_link, security, touch as touch_mod
from . import views
from .render_ctx import CtxRenderer


def _wifi_up():
    try:
        import wifi

        return bool(wifi.status())
    except Exception:  # noqa: BLE001 - no wifi module off-badge
        return False


def _wifi_ip():
    try:
        import wifi

        return wifi.get_ip() or "?"
    except Exception:  # noqa: BLE001
        return "?"


def _off_pattern():
    """The firmware's own do-nothing pattern, if this build has one.

    Feature-detected: a firmware without it simply keeps the old behaviour of
    disabling the pattern and leaving its task spinning, which is what shipped
    until now and is merely slow rather than broken.
    """
    try:
        from patterns.off import OffPattern

        return OffPattern
    except ImportError:  # pragma: no cover - not every firmware has it
        return None


VERSION = "0.12.0"

SCREEN_DASH = 0
SCREEN_DETAIL = 1
SCREEN_SETTINGS = 2
SCREEN_CALIBRATE = 3
SCREEN_PICKER = 4
SCREEN_DEMO = 5
SCREEN_DEVICE = 6

# Ignore button state briefly after launch, so the press that opened the app is
# not read as a command.
STARTUP_GRACE_MS = 400

# The ring animates at 20 Hz. The screen does not need to: its slowest-changing
# element is a timer that ticks once a second, and ctx rasterisation is the most
# expensive thing this app does.
LED_INTERVAL_MS = 50
IDLE_REDRAW_MS = 1000
# How long the board stays empty before the ring goes back to the OS. Taking it
# back is immediate; giving it away waits, so a slot that expires and returns
# does not hand the ring to and fro.
RING_RELEASE_MS = 3000

# How often the badge reports its own loop timing. Buttons are polled once per
# iteration, so the loop rate *is* the input latency: at 5 Hz a press has to be
# held a fifth of a second to be seen at all, and a short press vanishes. This
# exists because "it takes a while to notice I pressed a button" was reported
# from a badge and guessing at the cause from a desktop was not working.
STATS_INTERVAL_MS = 10000



class EdgewiseApp(app.App):
    def __init__(self):
        super().__init__()
        self.button_states = Buttons(self)
        self.cfg = C.load()

        self.profile = boards.load(self.cfg)
        self.board = model.Board(max_slots=self.cfg["max_slots"])
        self.layout = layout_mod.LayoutEngine()
        self.engine = ledfx.LedEngine(self.profile, self.cfg)
        self.renderer = CtxRenderer()

        self.dashboard = views.Dashboard()
        self.detail = views.DetailView()
        self.calibrator = views.CalibrateView()
        self.picker = views.PickerView()
        self.messages = views.MessageView()
        self.demo = demo_mod.Demo(self.board)
        self.settings_view = views.SettingsView()
        self.device_view = views.DeviceIdView()
        self.prefs = prefs.SettingsModel(self.cfg)
        # Set by the button handler, consumed by the async loop. Buttons are
        # handled synchronously and a platform dialog has to be awaited, so the
        # two cannot meet directly.
        self._pending = None

        # Nothing else in Tildagon OS sets the clock: the only ntptime call
        # in the firmware is inside the OTA updater. Ask for it ourselves,
        # on a thread, because settime() blocks on a UDP round trip.
        self.timesync = timesync_mod.TimeSync()

        # The badge's own HTTP door, off unless asked for. Started from
        # background_task once there is an address to bind to.
        self.http = None
        self._http_waiters = {}
        self._http_restart = False

        # Two hardware sources, one recogniser: pads on a 2026 badge, the
        # highlighted edge plus CONFIRM on a 2024 one. Both absent is fine --
        # the ring no-ops and the buttons always work.
        self.touch = touch_mod.TouchRing()
        self.flip = touch_mod.FlipDetector()
        self.gestures = gest.GestureRecogniser()
        self.pads = gest.PadReader(self.profile)
        self.snoozed = False

        self.notification = None
        self.link = None
        self.link_state = "offline"
        # Policy, not frame protection: the drain cap is what protects the
        # render loop. This is what stops a publisher filling the board faster
        # than anyone could read it.
        self.limiter = security.RateLimiter()
        # Signed mode. The settings screen has offered this since M0 while
        # nothing read it; it reads it now.
        self.verifier = signing.Verifier(self.cfg["hmac_key"])
        self._unsigned_notice_ms = 0
        self._seen_epoch = 0
        self._rebuilding = False
        self._flood_notice_ms = 0
        self.message = None
        self.message_until_ms = 0
        # Weather for the middle of the dashboard. The badge fetches
        # nothing itself: this arrives over MQTT from whatever already
        # knows, like everything else here.
        self.weather = None
        self.weather_until_ms = 0

        self.screen = SCREEN_DASH
        self.selected_edge = None
        self._detail_name = None

        # Calibration state.
        self._cal_mode = views.CalibrateView.MODE_PHASE
        self._cal_index = 0
        self._cal_phase = 0
        self._cal_map = []

        self._picker_options = boards.profiles()
        self._picker_index = 0

        self._held = set()
        self._press_ms = {}
        self._confirm_was_down = False
        self._cancel_was_down = False
        # Set once a CANCEL hold has already dismissed something, so releasing
        # the same press does not then also run the tap action.
        self._cancel_consumed = False
        self._uptime_ms = 0
        self._last_tick_ms = time.ticks_ms()
        self._led_timer = 0
        self._ring_idle_ms = 0
        self._leds_owned = False

        self._dirty = True
        self._since_draw_ms = 0
        self._last_draw_state = None

        self._loops = 0
        self._renders = 0
        self._hhmm_cache = None
        self._hhmm_at_ms = -99999
        self._night_cache = 255
        self._night_at_ms = 0
        # Bumped whenever the weather changes, so the redraw check can compare
        # an int instead of sorting a dict every iteration.
        self._weather_gen = 0
        # Milliseconds spent in each phase over the stats window. The loop is
        # running at 6 Hz on an idle badge with nothing to draw, so the cost is
        # somewhere in here and guessing which has not been working.
        self._phase_ms = {}
        self._worst_ms = 0
        self._stats_ms = 0

        # A board whose profile was guessed rather than detected, on a first
        # run, is the one case where asking is better than assuming.
        if (self.profile.source == boards.SOURCE_FALLBACK
                and self.cfg["board"] == boards.KEY_AUTO):
            self.screen = SCREEN_PICKER
        elif not self.cfg["seen_demo"]:
            self._start_demo()

        self._open_link()
        self.timesync.start()

    # -- MQTT ----------------------------------------------------------------

    def _open_link(self):
        """Start the link, if there is a broker to talk to.

        An unconfigured badge is not an error: demo mode and the settings
        screen both work without a broker, so first run is never a dead end.
        """
        if self.link is not None:
            self.link.stop()
            self.link = None
        if not C.configured(self.cfg):
            self.link_state = "no broker set"
            return
        self.link = mqtt_link.Link(mqtt_link.BrokerSpec(self.cfg))
        self._seen_epoch = 0
        self.link.start()

    def _service_link(self, now):
        link = self.link
        if link is None:
            return
        # A no-op in threaded mode; the fallback path when threads are absent.
        link.pump(now)

        if link.session_epoch != self._seen_epoch:
            # A new session: the broker is about to replay every retained slot.
            # Open a generation window so slots that were retained-cleared while
            # we were offline can be swept once the burst has landed.
            self._seen_epoch = link.session_epoch
            self.board.begin_rebuild(now)
            self._rebuilding = True
            self._dirty = True
        elif self._rebuilding and not link.connected():
            # Dropped mid-burst, so we only have part of the picture. Sweeping
            # on that would blank slots that are still perfectly alive.
            self.board.abandon_rebuild()
            self._rebuilding = False
        elif self._rebuilding and not self.board.rebuilding(now):
            if self.board.end_rebuild(now):
                self._dirty = True
            self._rebuilding = False

        state = link.status_line()
        if state != self.link_state:
            self.link_state = state
            self._dirty = True

        self._process_inbound(link, now)

    def _process_inbound(self, link, now):
        """Drain, validate, rate-limit, apply -- in that order, on this task.

        The order is the point. The drain cap bounds the work per frame; the
        parser is the only thing that ever sees untrusted bytes; the limiter is
        policy applied to what survived; and only then does anything reach the
        model.
        """
        root = link.spec.root()
        rebuilding = self.board.rebuilding(now)
        for topic, payload in link.drain(4):
            kind, name = mqtt_link.route(mqtt_link.topic_suffix(topic, root))
            if kind is None:
                continue
            # The retained burst after a reconnect is a legitimate dozen
            # messages at once, and rate-limiting it would make the board
            # reveal itself in slow motion. It is still bounded by the drain.
            if not rebuilding and not self.limiter.allow(now):
                self._note_flood(now)
                continue
            # Parsed once, here, so signed mode has something to check and the
            # handlers are not each re-parsing the same bytes.
            if kind == "slot":
                parsed = security.parse_slot(payload)
            elif kind == "led":
                parsed = security.parse_led(payload)
            elif kind == "text":
                parsed = security.parse_text(payload)
            else:
                parsed = security.parse_weather(payload)
            if parsed is None:
                continue

            if self.cfg["require_signed"]:
                suffix = "slot/%s" % name if kind == "slot" else kind
                if not self.verifier.verify(suffix, parsed, self._wall_clock()):
                    self._note_unsigned(now)
                    continue

            if kind == "slot":
                self._apply_slot(name, parsed, now)
            elif kind == "led":
                self.engine.set_raw(parsed, now)
            elif kind == "text":
                self._show_message(parsed, now)
            else:
                self._show_weather(parsed, now)

    def _note_unsigned(self, now):
        """Say it once in a while, rather than on every rejected message.

        A misconfigured publisher will send hundreds; a notification per message
        would be its own denial of service. The count goes out in the stats
        topic for anyone actually debugging it.
        """
        if clock.elapsed_ms(self._unsigned_notice_ms, now) < 10000:
            return
        self._unsigned_notice_ms = now
        self.notification = Notification("Unsigned - ignored")

    def _apply_slot(self, name, payload, now):
        name = security.clean_text(name, 24)
        if not name:
            return
        parsed = security.parse_slot(payload)
        if parsed is None:
            return
        if self.board.apply(name, parsed, now) != model.CHANGE_NONE:
            self._dirty = True

    def _show_weather(self, parsed, now):
        """Retained, so it survives a reboot -- and expiring, so it cannot
        outlive its usefulness. Weather half a day stale is not weather, it is
        misinformation with an icon on it."""
        if parsed is None:
            return
        if parsed.get("cleared"):
            self.weather = None
        else:
            self.weather = parsed
            self.weather_until_ms = clock.add_ms(now, parsed["ttl"] * 1000)
        self._weather_gen += 1
        self._dirty = True

    def _show_message(self, parsed, now):
        if not parsed:
            return
        self.message = parsed
        self.message_until_ms = clock.add_ms(now, parsed["duration"] * 1000)
        self._dirty = True

    def _note_flood(self, now):
        """One notice per ten seconds.

        Turning a flood of messages into a flood of notifications is the
        obvious own-goal, and would make the screen less usable than just
        dropping them silently.
        """
        if clock.elapsed_ms(self._flood_notice_ms, now) < 10000:
            return
        self._flood_notice_ms = now
        self.notification = Notification("Ignoring flood")

    # -- lifecycle -----------------------------------------------------------

    async def run(self, render_update):
        while True:
            now = time.ticks_ms()
            delta = time.ticks_diff(now, self._last_tick_ms)
            self._last_tick_ms = now
            self._since_draw_ms += delta
            self.update(delta)

            if self._pending is not None:
                item, self._pending = self._pending, None
                await self._edit(item, render_update)
                continue

            if not getattr(self, "_foreground", True):
                # render_update blocks until the app is on screen again.
                if await render_update():
                    self._dirty = True
            elif self._needs_draw():
                await self._render(render_update)
            else:
                await asyncio.sleep(0.02)

    async def _edit(self, item, render_update):
        """Run one platform dialog and apply its result.

        `TextDialog` and friends come from `app_components`, so this is the same
        text entry every other app on the badge uses -- which also means a
        keyboard hexpansion works here for free, and the screen-reader alt text
        comes with it. A hand-rolled character picker would have been a worse
        version of all three.

        A modal dialog suspends this loop, and that is safe: the MQTT worker
        owns its own thread, so retained messages keep arriving while you type.
        On a build with no threads the link is polled from here and does stall
        for as long as the dialog is open, which is a fair price for the only
        screen where nothing is being watched.
        """
        try:
            from app_components.dialog import NumberDialog, TextDialog, YesNoDialog
        except ImportError:      # pragma: no cover - desktop tests, no firmware
            return

        if item.key == "regenerate":
            confirm = YesNoDialog("New device ID?", self)
            if await confirm.run(render_update):
                self.prefs.cfg = prefs.put(
                    self.cfg, "device_id", security.new_device_id())
                self._commit_settings(item)
                self.notification = Notification("New device ID")
            self._dirty = True
            return

        current = prefs.get(self.cfg, item.key)
        prompt = item.label
        if item.kind == prefs.KIND_NUMBER:
            dialog = NumberDialog(prompt, self)
        else:
            dialog = TextDialog(prompt, self,
                                masked=item.kind == prefs.KIND_PASSWORD)
        # Seeded with the current value so correcting one character of a
        # hostname is not the same work as typing it from nothing.
        if current not in (None, "") and item.kind != prefs.KIND_PASSWORD:
            dialog.text = str(current)

        result = await dialog.run(render_update)
        if self.prefs.apply(item, result):
            self._commit_settings(item)
        self._dirty = True

    async def _render(self, render_update):
        self._renders += 1
        self._dirty = False
        self._since_draw_ms = 0
        self._last_draw_state = self._draw_state()
        return await render_update()

    def update(self, delta):
        self._uptime_ms += delta
        now = clock.now_ms()

        # One poll of the buttons happens per iteration, so this counts input
        # latency directly rather than by proxy.
        self._loops += 1
        if delta > self._worst_ms:
            self._worst_ms = delta
        self._stats_ms += delta
        if self._stats_ms >= STATS_INTERVAL_MS:
            self._publish_stats()

        mark = time.ticks_ms()

        def phase(name):
            # ticks_ms has 1 ms resolution, so a single fast phase reads zero;
            # accumulated over ten seconds the totals still separate cleanly.
            nonlocal mark
            after = time.ticks_ms()
            self._phase_ms[name] = (self._phase_ms.get(name, 0)
                                    + time.ticks_diff(after, mark))
            mark = after

        self.timesync.pump(now)
        phase("sync")
        self._handle_buttons()
        self._handle_touch(now)
        self._handle_gestures(now)
        phase("input")
        self._handle_flip(delta, now)
        phase("imu")
        self._service_link(now)
        phase("link")

        if self.screen == SCREEN_DEMO and self.demo.tick(now):
            self._dirty = True

        if self.weather is not None and clock.expired(self.weather_until_ms, now):
            self.weather = None
            self._weather_gen += 1
            self._dirty = True
        if self.message is not None and clock.expired(self.message_until_ms, now):
            self.message = None
            self._dirty = True

        # The platform's Notification opens, waits three seconds and animates
        # shut -- but only if something ticks it, and nothing ever did. Since
        # _needs_draw() treats a live notification as "redraw now", the first
        # "Acknowledged" of a session pinned the badge to a full screen render
        # every iteration for the rest of it: renders_per_s came back equal to
        # loops_per_s, at 5 Hz, on hardware.
        #
        # It is briefly "closed" before its open animation has run, so waiting
        # on _is_closed() alone would drop it at birth.
        if self.notification is not None:
            self.notification.update(delta)
            if not self.notification._open and self.notification._is_closed():
                self.notification = None
            self._dirty = True

        gone = self.board.expire(now)
        if gone:
            self._dirty = True

        self._sync_layout(now)
        phase("layout")

    def _sync_layout(self, now):
        names = self.board.names()
        urgent = self.board.urgent_names()
        if self.layout.sync(names, self.board.pins(), now, urgent):
            self._dirty = True
        # Photographed on a badge: "no jobs" in the middle, and still a cursor
        # ring and a "CONFIRM open - hold CANCEL drop" hint offering actions on
        # a slot that had expired underneath them.
        if (self.selected_edge is not None
                and self.layout.slot_at(self.selected_edge) is None):
            self.selected_edge = None
            self._dirty = True

    # -- the badge's own HTTP door -------------------------------------------

    async def background_task(self):
        """The base loop, plus the listener.

        `background_task` is what the scheduler creates per app and is where
        the OS runs its own long-lived work. Starting the server here rather
        than in `run()` means it keeps answering while the app is minimised,
        which is the point: a poke should land whether or not you happen to be
        looking at the badge.
        """
        import asyncio

        asyncio.create_task(self._serve_http())
        await super().background_task()

    async def _serve_http(self):
        """Bring the listener up, and take it down again when it is turned off.

        Polled rather than assumed: Wi-Fi has to be up before there is an
        address to bind to, and a badge that has not joined a network yet would
        otherwise fail once and never try again.
        """
        import asyncio

        while True:
            if self._http_restart and self.http is not None:
                # The port or the token moved under it. Closing here is what
                # makes "turn it off" mean the socket actually goes away.
                server, self.http = self.http, None
                await server.stop()
            self._http_restart = False

            want = self.cfg["http_enabled"] and _wifi_up()
            if want and self.http is None:
                try:
                    self.http = httpd.Server(
                        self.cfg["http_port"], self.cfg["http_token"],
                        self._http_request, self.limiter)
                    await self.http.start()
                    print("[edgewise] http on %s:%d"
                          % (_wifi_ip(), self.cfg["http_port"]))
                except Exception as exc:  # noqa: BLE001 - port taken, no wifi
                    print("[edgewise] http failed:", exc)
                    self.http = None
            elif not want and self.http is not None:
                server, self.http = self.http, None
                await server.stop()
            await asyncio.sleep(2)

    async def _http_request(self, kind, name, payload):
        """One request, answered through the handlers MQTT already uses.

        Every payload goes through `security.parse_*` before it reaches the
        board, so a slot set over HTTP and the same slot set over MQTT are
        indistinguishable by the time anything is lit. That is the whole reason
        this is a door and not a second implementation.
        """
        now = clock.now_ms()

        if kind == httpd.KIND_HEALTH:
            return (200, '{"ok":true,"version":"%s","slots":%d,"up_ms":%d}'
                    % (VERSION, len(self.board.slots), self._uptime_ms))

        if kind == httpd.KIND_SLOT:
            parsed = security.parse_slot(payload)
            if parsed is None:
                return (400, httpd.json_error("not a valid slot update"))
            self._apply_slot(name, parsed, now)
            return (200, '{"slot":"%s","state":"%s"}' % (name, parsed["state"]))

        if kind == httpd.KIND_TEXT:
            parsed = security.parse_text(payload)
            if parsed is None:
                return (400, httpd.json_error("not a valid message"))
            self._show_message(parsed, now)
            return (200, '{"shown":true}')

        if kind == httpd.KIND_WEATHER:
            parsed = security.parse_weather(payload)
            if parsed is None:
                return (400, httpd.json_error("not a valid weather report"))
            self._show_weather(parsed, now)
            return (200, '{"weather":true}')

        if kind == httpd.KIND_LED:
            parsed = security.parse_led(payload)
            if parsed is None:
                return (400, httpd.json_error("not a valid led request"))
            self.engine.set_raw(parsed, now)
            return (200, '{"led":true}')

        if kind == httpd.KIND_WAIT:
            return await self._http_wait(name, payload["timeout"])

        return (404, httpd.json_error("unknown endpoint"))

    async def _http_wait(self, slot, seconds):
        """Hold the request open until that slot is acknowledged or denied.

        A tap becomes an exit code for anything that can call a URL. Bounded to
        two waiters, because each is holding one of four sockets -- and a
        timeout answers 408 rather than 200 with nothing in it, so a caller
        cannot read "no answer" as approval.
        """
        import asyncio

        if len(self._http_waiters) >= httpd.MAX_WAITERS:
            return (503, httpd.json_error("too many waiting"))
        box = [asyncio.Event(), None]
        self._http_waiters.setdefault(slot, []).append(box)
        try:
            await asyncio.wait_for(box[0].wait(), seconds)
        except Exception:  # noqa: BLE001 - a timeout is the expected ending
            pass
        finally:
            waiting = self._http_waiters.get(slot) or []
            if box in waiting:
                waiting.remove(box)
            if not waiting:
                self._http_waiters.pop(slot, None)
        if box[1] is None:
            return (408, httpd.json_error("no answer"))
        return (200, '{"type":"%s","slot":"%s"}' % (box[1], slot))

    def _release_http_waiters(self, slot, kind):
        """Called wherever a decision is recorded, however it was made."""
        for box in self._http_waiters.pop(slot, []):
            box[1] = kind
            box[0].set()

    # -- LEDs ----------------------------------------------------------------

    def background_update(self, delta):
        """Drive the ring, off the foreground loop.

        The firmware runs this from `background_task()` for every app, at 20 Hz,
        and it is where the OS itself drives the ring. Doing it inline in the
        foreground loop -- as this did until now -- made every millisecond of
        LED work a millisecond of button latency, and the badge measured 8 loops
        a second with LEDs taking 43% of the time.
        """
        now = clock.now_ms()
        if not getattr(self, "_foreground", True):
            # Focus taken by something else -- give the ring back immediately,
            # not after the idle delay. This is also the only place that notices
            # focus being lost by any route other than our own CANCEL handler.
            if self._leds_owned:
                self._release_ring()
            return

        wanted = ledfx.ring_wanted(
            True,
            self.screen == SCREEN_CALIBRATE,
            bool(self.board.slots),
            self.cfg["idle_pattern"])

        if wanted:
            self._ring_idle_ms = 0
            if not self._leds_owned:
                self._take_ring()
        else:
            self._ring_idle_ms += delta
            if self._leds_owned and self._ring_idle_ms >= RING_RELEASE_MS:
                self._release_ring()
        if not self._leds_owned:
            return

        self._led_timer += delta
        if self._led_timer < LED_INTERVAL_MS:
            return
        self._led_timer = 0
        self._drive_leds(delta, now)

    def _take_ring(self):
        """Take the ring, and stop the OS animating one we now own.

        PatternDisable alone only stops it *painting*: its task keeps running at
        the pattern's own fps -- 30 for the default rainbow -- calling
        settings.get() and computing a frame every time, on the same scheduler
        as this app. Swapping in the `off` pattern drops that to once a second.
        """
        self._leds_owned = True
        eventbus.emit(PatternDisable())
        pattern = _off_pattern()
        if pattern is not None:
            eventbus.emit(PatternSet(pattern))

    def _release_ring(self):
        """Hand the ring back, with whatever pattern the user had chosen."""
        self._leds_owned = False
        self.engine.all_off()
        eventbus.emit(PatternReload())
        eventbus.emit(PatternEnable())

    def _drive_leds(self, delta, now):
        if self.screen == SCREEN_CALIBRATE:
            self._calibration_frame(now)
            return

        self.engine.night_level = (self.cfg["night"]["level"] * 255 // 100
                                   if self.snoozed else self._night_level(now))
        for edge in range(layout_mod.EDGES):
            name = self.layout.slot_at(edge)
            slot = self.board.slots.get(name) if name else None
            if slot is None:
                self.engine.clear_state(edge, now)
            else:
                self.engine.set_state(edge, slot.state, slot.age_ms(now), now,
                                      slot.is_stale(now))
        self.engine.render(now)

    def _night_level(self, now_ms=0):
        # Cached: this reads the RTC twice and parses two "HH:MM" strings, and
        # it ran on every LED frame. Night mode changes on a schedule measured
        # in hours; a minute of lag is invisible.
        if self._night_at_ms and clock.elapsed_ms(self._night_at_ms, now_ms) < 60000:
            return self._night_cache
        self._night_at_ms = now_ms or 1
        self._night_cache = self._night_level_now()
        return self._night_cache

    def _night_level_now(self):
        night = self.cfg["night"]
        if not night["enabled"] or not C.clock_is_set():
            return 255
        try:
            local = time.localtime()
            minutes = local[3] * 60 + local[4]
        except Exception:  # noqa: BLE001
            return 255
        start = C.parse_hhmm(night["from"], 22 * 60)
        end = C.parse_hhmm(night["to"], 7 * 60)
        active = (start <= minutes or minutes < end) if start > end \
            else (start <= minutes < end)
        if not active:
            return 255
        return max(1, min(255, night["level"] * 255 // 100))

    def _calibration_frame(self, now):
        """Light exactly what the calibrate screen is asking about."""
        engine = self.engine
        engine.clear_raw()
        for edge in range(layout_mod.EDGES):
            engine.clear_state(edge, now)
        if self._cal_mode == views.CalibrateView.MODE_PHASE:
            groups = boards.edge_leds(self.profile.led_count, self._cal_phase)
            engine.set_raw({"segment": None, "leds": list(groups[0]),
                            "effect": "solid", "rgb": (0, 220, 255),
                            "ttl": 60}, now)
        else:
            engine.set_raw({"segment": None, "leds": [self._cal_index],
                            "effect": "solid", "rgb": (0, 220, 255),
                            "ttl": 60}, now)
        engine.render(now)

    # -- drawing -------------------------------------------------------------

    def _draw_state(self):
        """Everything that changes what the screen looks like."""
        return (
            self.screen, self._detail_name, self.selected_edge,
            len(self.board.slots), self.board.counts(),
            self.link_state, self.demo.caption, self.demo.showing_qr,
            self._cal_mode, self._cal_index, self._cal_phase,
            self._picker_index,
            # Moving the cursor changes nothing else in this tuple, so without
            # these the settings list would not repaint until something
            # unrelated happened to it.
            self.prefs.group, self.prefs.index, self.snoozed,
            self._hhmm(), self._weather_gen,
        )

    def _needs_draw(self):
        if self._dirty or self.notification is not None or self.overlays:
            return True
        if self._draw_state() != self._last_draw_state:
            return True
        # Timers tick, so the dashboard needs a slow refresh even when nothing
        # else has changed. Static screens do not.
        if self.screen in (SCREEN_DASH, SCREEN_DETAIL):
            return self._since_draw_ms >= IDLE_REDRAW_MS
        return False

    def draw(self, ctx):
        clear_background(ctx)
        r = self.renderer.begin(ctx)
        now = clock.now_ms()

        if self.screen == SCREEN_PICKER:
            self.picker.draw(r, self._picker_options, self._picker_index)
        elif self.screen == SCREEN_SETTINGS:
            self.settings_view.draw(
                r, self.prefs,
                "set a broker to begin" if self.prefs.needs_broker() else "")
        elif self.screen == SCREEN_DEVICE:
            self.device_view.draw(r, self.cfg["device_id"],
                                  self.cfg["broker"]["prefix"],
                                  self._http_details())
        elif self.screen == SCREEN_CALIBRATE:
            self.calibrator.draw(r, self._cal_mode, self._cal_index,
                                 self.profile.led_count, self._cal_phase)
        elif self.screen == SCREEN_DETAIL:
            slot = self.board.slots.get(self._detail_name)
            edge = self.layout.edge_of(self._detail_name)
            self.detail.draw(r, slot, edge, now,
                             pinned=bool(slot and slot.edge is not None))
        else:
            self.dashboard.draw(r, self.board, self.layout, self.engine, now,
                                views.link_summary(self.link_state, self.cfg),
                                self.cfg, self._hhmm(), self.weather,
                                self.selected_edge, self.snoozed)
            if self.screen == SCREEN_DEMO and self.demo.caption:
                self._demo_caption(r)
            elif self.message is not None:
                self.messages.draw(r, self.message["msg"], self.message["level"])

        if self.notification is not None:
            self.notification.draw(ctx)

        # Last, and never conditionally: a platform dialog is an overlay, and
        # without this it opens, takes every button, and draws nothing at all.
        self.draw_overlays(ctx)

    def _http_details(self):
        """What to show on the device screen, or None when the door is shut."""
        if not self.cfg["http_enabled"]:
            return None
        return {"address": "%s:%d" % (_wifi_ip(), self.cfg["http_port"]),
                "token": self.cfg["http_token"]}

    def _hhmm(self):
        """Local time, or None until the badge has been told what it is.

        None rather than a placeholder: a clock showing 00:00 on a desk is worse
        than no clock, because only one of the two is obviously not to be
        trusted.
        """
        # Cached: this is called from the redraw check on every iteration, and
        # it used to format a string and read the RTC each time. The clock
        # changes once a minute; checking five times a second is plenty and
        # costs nothing in between.
        if self._uptime_ms - self._hhmm_at_ms >= 200:
            self._hhmm_at_ms = self._uptime_ms
            self._hhmm_cache = clock.local_hhmm(self.cfg["utc_offset"])
        return self._hhmm_cache

    def _demo_caption(self, r):
        r.text(self.demo.caption, 0, 62, views.FG, size=13, align="center")
        r.text("any button to skip", 0, 100, (0.35, 0.35, 0.38), size=9,
               align="center")

    # -- input ---------------------------------------------------------------

    def _pressed(self, name):
        """Edge-triggered button read.

        Buttons.pressed() would do this, but it only exists on newer firmware;
        tracking held state here keeps the app working on 2024 badges too.
        """
        button = BUTTON_TYPES[name]
        down = self.button_states.get(button)
        if down and name not in self._held:
            self._held.add(name)
            self._press_ms[name] = self._uptime_ms
            return True
        if not down:
            self._held.discard(name)
            self._press_ms.pop(name, None)
        return False

    def _held_for(self, name):
        """How long a button has been down, for long-press gestures."""
        started = self._press_ms.get(name)
        if started is None or name not in self._held:
            return 0
        return self._uptime_ms - started

    def _handle_buttons(self):
        if self._uptime_ms < STARTUP_GRACE_MS and not self._held:
            self.button_states.clear()
            return

        if self.screen == SCREEN_PICKER:
            return self._picker_buttons()
        if self.screen == SCREEN_CALIBRATE:
            return self._calibrate_buttons()
        if self.screen == SCREEN_DEMO:
            for name in ("CONFIRM", "CANCEL", "UP", "DOWN", "LEFT", "RIGHT"):
                if self._pressed(name):
                    self._end_demo()
                    return
            return
        if self.screen == SCREEN_DETAIL:
            # A slot can vanish while you are reading it -- acknowledged, TTL
            # expired, or retained-cleared by its publisher. Showing "gone" and
            # staying there is a dead end that reads as a hang; the board is
            # what you want at that point, and it is one press away anyway.
            if self.board.slots.get(self._detail_name) is None:
                self.screen = SCREEN_DASH
                self._dirty = True
                return
            if self._pressed("CANCEL") or self._pressed("LEFT"):
                self.screen = SCREEN_DASH
                self._dirty = True
                return
            # The view has always drawn "CONFIRM ack - hold deny" and this
            # handler has always ignored both. The detail screen is where the
            # message is legible, so it is where a decision actually gets made:
            # promising the action and dropping it is the worst of the options.
            #
            # Fed through the same recogniser as the dashboard, so tap and hold
            # keep one set of timings and one test suite.
            if self._pressed("CONFIRM"):
                self.gestures.press(self.selected_edge, clock.now_ms())
            elif "CONFIRM" not in self._held and self._confirm_was_down:
                self.gestures.release(self.selected_edge, clock.now_ms())
            self._confirm_was_down = "CONFIRM" in self._held
            return
        if self.screen == SCREEN_SETTINGS:
            return self._settings_buttons()
        if self.screen == SCREEN_DEVICE:
            if self._pressed("CANCEL") or self._pressed("LEFT"):
                self.screen = SCREEN_SETTINGS
                self._dirty = True
            return

        if self._pressed("LEFT"):
            # controls.md has documented this since M3. Until now it was the
            # only line in that table with no code behind it.
            self.prefs = prefs.SettingsModel(self.cfg)
            self.screen = SCREEN_SETTINGS
            self._dirty = True
            return
        # CANCEL acts on release, because it now has two meanings: a tap backs
        # out, a hold dismisses the selected slot. Acting on press would fire
        # the tap action before a hold could be recognised.
        if self._pressed("CANCEL"):
            self._cancel_consumed = False
        if "CANCEL" in self._held:
            if (not self._cancel_consumed
                    and self._held_for("CANCEL") >= gest.LONG_MS
                    and self._selected_name() is not None):
                self._cancel_consumed = True
                self._dismiss_selected()
            self._cancel_was_down = True
            return
        if self._cancel_was_down:
            self._cancel_was_down = False
            if not self._cancel_consumed:
                if self.selected_edge is not None:
                    self.selected_edge = None
                    self._dirty = True
                else:
                    self._shutdown()
            return
        if self._pressed("UP"):
            self._move_selection(-1)
            return
        if self._pressed("DOWN"):
            self._move_selection(1)
            return
        if self._pressed("RIGHT"):
            # Kept as an alias so anyone who learned RIGHT is not stranded.
            self._open_detail()
            return
        # CONFIRM opens the slot rather than acknowledging it outright. Every
        # other app on the badge treats CONFIRM as "select", and acknowledging
        # from the dashboard meant deciding without having read the message --
        # which for approve-from-badge is the entire point of the message.
        #
        # The decision itself lives one screen in, where the text is legible.
        # The touch ring keeps its direct tap-to-ack: on a 2026 you are
        # pointing at the edge, so there is no ambiguity about which slot you
        # mean, and no button to be consistent with.
        if self._pressed("CONFIRM"):
            self._open_detail()
        self._confirm_was_down = "CONFIRM" in self._held

    # -- settings -------------------------------------------------------------

    def _settings_buttons(self):
        if self._pressed("CANCEL") or self._pressed("LEFT"):
            if self.prefs.back():
                self.screen = SCREEN_DASH
            self._dirty = True
            return
        if self._pressed("UP"):
            self.prefs.move(-1)
            self._dirty = True
            return
        if self._pressed("DOWN"):
            self.prefs.move(1)
            self._dirty = True
            return
        if not self._pressed("CONFIRM") and not self._pressed("RIGHT"):
            return

        kind, item = self.prefs.select()
        self._dirty = True
        if kind in (None, prefs.KIND_GROUP):
            return
        if kind in (prefs.KIND_TOGGLE, prefs.KIND_CHOICE):
            self._commit_settings(item)
            return
        if kind == prefs.KIND_ACTION:
            self._settings_action(item)
            return
        # Text, password and number all need a platform dialog, which only the
        # async loop can await.
        self._pending = item

    def _settings_action(self, item):
        if item.key == "device_id":
            self.screen = SCREEN_DEVICE
        elif item.key == "regenerate":
            self._pending = item          # confirmed with a YesNoDialog first
        elif item.key == "calibrate":
            # The calibrate screen has existed since M0 with nothing calling it.
            self.open_calibration()
        elif item.key == "http_regen":
            self.prefs.cfg = prefs.put(self.cfg, "http_token",
                                       security.new_token(C.HTTP_TOKEN_CHARS))
            self._commit_settings(item)
            self.screen = SCREEN_DEVICE
        elif item.key == "replay_demo":
            self.cfg["seen_demo"] = False
            C.save(self.cfg)
            self._start_demo()
        elif item.key in ("version", "repo"):
            self.notification = Notification("edgewise %s" % VERSION)

    def _commit_settings(self, item=None):
        """Persist an edit and apply whatever it changed, immediately.

        Settings that only take effect on restart are settings people believe
        are broken, so every one of these is applied in place: the ring changes
        brightness as you hold DOWN, and a corrected broker reconnects before
        you have left the screen.
        """
        self.cfg = self.prefs.cfg
        C.save(self.cfg)
        key = item.key if item is not None else ""
        if key in ("board", "rotation"):
            self._reload_profile()
            self.pads = gest.PadReader(self.profile)
        elif key.startswith("broker.") or key == "device_id":
            self._open_link()
        elif key == "hmac_key":
            self.verifier = signing.Verifier(self.cfg["hmac_key"])
        elif key == "require_signed" and self.cfg["require_signed"]:
            # Refuse to switch on a check that cannot run. Leaving it on with no
            # key -- or on a build with no hashing -- would put the badge back
            # exactly where it was: a security control that appears to be on and
            # verifies nothing.
            if not self.verifier.usable():
                self.cfg = prefs.put(self.cfg, "require_signed", False)
                self.prefs.cfg = self.cfg
                C.save(self.cfg)
                self.notification = Notification(
                    "Set a signing key first" if signing.available()
                    else "No hashing on this build")
        elif key.startswith("http"):
            # _serve_http notices on its next pass; dropping it here is what
            # makes "turn it off" mean the socket actually closes.
            self._http_restart = True
        self.engine.brightness = self.cfg["brightness"]
        self.engine.palette = self.cfg["palette"]
        self._dirty = True

    # -- touch, gestures, flip ------------------------------------------------

    def _handle_touch(self, now):
        """Feed the pads into the same recogniser the buttons use."""
        if not self.touch.available or self.screen not in (SCREEN_DASH, SCREEN_DETAIL):
            return
        edge = self.pads.poll(self.touch.read(), self.gestures, now)
        if edge is not None and edge != self.selected_edge:
            # Touching an edge selects it, so the detail view and the button
            # path agree about what "the current slot" means.
            self.selected_edge = edge
            self._dirty = True

    def _handle_gestures(self, now):
        for edge, kind in self.gestures.tick(now):
            name = self.layout.slot_at(edge)
            if name is None:
                continue
            self.selected_edge = edge
            if kind == gest.TAP:
                self._acknowledge()
                # Back to the board, so the decision is visibly done rather
                # than leaving you on a page describing what you just cleared.
                if self.screen == SCREEN_DETAIL:
                    self.screen = SCREEN_DASH
            elif kind == gest.LONG:
                self._deny()
                if self.screen == SCREEN_DETAIL:
                    self.screen = SCREEN_DASH
            elif kind == gest.DOUBLE:
                self._detail_name = name
                self.screen = SCREEN_DETAIL
                self._dirty = True

    def _handle_flip(self, delta, now):
        """Face-down snoozes the whole board until it is picked up again."""
        if not self.flip.update(delta, now):
            return
        self.snoozed = self.flip.flipped
        self._publish_event("snooze" if self.snoozed else "wake", "", None)
        self._dirty = True

    def _open_detail(self):
        name = self._selected_name()
        if not name:
            return
        self._detail_name = name
        self.screen = SCREEN_DETAIL
        self._dirty = True

    def _dismiss_selected(self):
        """Take a slot off this badge, without pretending to have acted on it.

        An ack says "I have seen this" to whatever published it; the publisher
        then owns what happens next, which is why acknowledging has never
        removed anything. Sometimes you just want it off your board -- and with
        nothing subscribed to `event`, that was previously impossible from the
        badge at all.

        Local only, and honest about it: if the slot is still retained on the
        broker it returns on the next reconnect, because the publisher still
        thinks it matters and the badge is not the authority on that.
        """
        name = self._selected_name()
        if not name:
            return
        self.board.remove(name)
        self.selected_edge = None
        self.notification = Notification("Dismissed")
        self._dirty = True

    def _selected_name(self):
        if self.selected_edge is None:
            return None
        return self.layout.slot_at(self.selected_edge)

    def _acknowledge(self):
        name = self._selected_name()
        if name is None:
            return
        self._publish_event("ack", name, self.selected_edge)
        self._release_http_waiters(name, "ack")
        # Locally the slot stops asking. What an ack *means* is the
        # subscriber's decision -- the badge never treats it as "safe" -- but
        # leaving the edge flashing after it has been acknowledged would train
        # people to ignore the one signal that matters.
        slot = self.board.slots.get(name)
        if slot is not None and slot.state == model.STATE_NEEDS_YOU:
            slot.state = model.STATE_WORKING
            slot.changed_ms = clock.now_ms()
        self.notification = Notification("Acknowledged")
        self._dirty = True

    def _deny(self):
        name = self._selected_name()
        if name is None:
            return
        self._publish_event("deny", name, self.selected_edge)
        self._release_http_waiters(name, "deny")
        self.notification = Notification("Denied")
        self._dirty = True

    def _publish_event(self, kind, name, edge):
        """Outbound events carry no content beyond what happened, and where.

        No labels, no messages, no project names: an event topic is the one
        thing a subscriber cannot have already seen, so it is kept boring.
        """
        if self.link is None:
            return
        # A slot name is quoted JSON, so it has to survive being embedded. It
        # has already been through clean_text on the way in, which leaves no
        # quotes or backslashes, but building JSON by hand is exactly where
        # that assumption stops being obvious -- so re-check it here.
        name = (name or "").replace('"', "").replace("\\", "")
        slot = '"%s"' % name if name else "null"
        ts = self._wall_clock()
        payload = '{"type":"%s","slot":%s,"edge":%s,"ts":%d' % (
            kind, slot, edge if edge is not None else "null", ts)

        # Signed outbound too, when a key is set. A subscriber that checks this
        # knows the tap happened on this badge rather than being forged by
        # anyone who learned the device ID -- which matters most for the flow
        # that turns an ack into permission to run a command.
        if self.cfg["hmac_key"] and ts:
            fields = {"type": kind, "slot": name or None,
                      "edge": edge if edge is not None else None}
            sig = signing.sign(self.cfg["hmac_key"], "event", fields, ts)
            if sig:
                payload += ',"sig":"%s"' % sig
        self.link.publish_event((payload + "}").encode())

    def _publish_stats(self):
        """Loop rate, worst iteration, renders, free heap. Not retained.

        Diagnostics rather than protocol: a subscriber that has never heard of
        this topic is unaffected, and a badge nobody is watching pays one small
        publish every ten seconds.
        """
        elapsed = self._stats_ms or 1
        loops = self._loops * 1000 // elapsed
        renders = self._renders * 1000 // elapsed
        self._loops = self._renders = 0
        self._stats_ms = 0
        worst, self._worst_ms = self._worst_ms, 0

        try:
            import gc

            free = gc.mem_free()
        except Exception:  # noqa: BLE001 - not every build has it
            free = -1

        if self.link is None:
            return
        phases = ",".join('"%s":%d' % (k, v)
                          for k, v in sorted(self._phase_ms.items()))
        self._phase_ms = {}
        # Which LED write path actually bound, and on how many LEDs. v0.8.0's
        # fast path is feature-detected against a class that is frozen into the
        # ESP32 port and absent from the source checkout, so "it fell back
        # silently" and "it is bound and still slow" look identical from here.
        engine = self.engine
        compose_ms = engine.us_compose // 1000
        write_ms = engine.us_write // 1000
        engine.us_compose = engine.us_write = 0

        payload = ('{"loops_per_s":%d,"renders_per_s":%d,"worst_ms":%d,'
                   '"free":%d,"slots":%d,"dropped_in":%d,"path":"%s",'
                   '"leds":%d,"offset":%d,"compose_ms":%d,"write_ms":%d,'
                   '"unsigned":%d,"ms":{%s}}') % (
            loops, renders, worst, free, len(self.board.slots),
            self.link.dropped_in, engine.path(), self.profile.led_count,
            self.profile.led_offset, compose_ms, write_ms,
            self.verifier.rejected, phases)
        self.link.publish_status("stats", payload.encode())

    def _wall_clock(self):
        # 0 when the badge has never reached NTP, rather than a 1970 timestamp
        # that looks real. See clock.wall_seconds.
        return clock.wall_seconds()

    def _move_selection(self, direction):
        """Move the highlight, landing only on edges that have a slot."""
        occupied = [e for e in range(layout_mod.EDGES)
                    if self.layout.slot_at(e) is not None]
        if not occupied:
            self.selected_edge = None
            return
        if self.selected_edge not in occupied:
            self.selected_edge = occupied[0]
        else:
            i = occupied.index(self.selected_edge)
            self.selected_edge = occupied[(i + direction) % len(occupied)]
        self._dirty = True

    # -- demo ----------------------------------------------------------------

    def _start_demo(self):
        self.screen = SCREEN_DEMO
        self.demo.start(clock.now_ms())
        self._dirty = True

    def _end_demo(self):
        self.demo.stop()
        self.screen = SCREEN_DASH
        if not self.cfg["seen_demo"]:
            self.cfg["seen_demo"] = True
            C.save(self.cfg)
        self._dirty = True

    # -- board picker --------------------------------------------------------

    def _picker_buttons(self):
        if self._pressed("UP"):
            self._picker_index = (self._picker_index - 1) % len(self._picker_options)
            self._dirty = True
        elif self._pressed("DOWN"):
            self._picker_index = (self._picker_index + 1) % len(self._picker_options)
            self._dirty = True
        elif self._pressed("CONFIRM"):
            key = self._picker_options[self._picker_index][0]
            self.cfg["board"] = key
            C.save(self.cfg)
            self._reload_profile()
            self.screen = SCREEN_DASH if self.cfg["seen_demo"] else SCREEN_DEMO
            if self.screen == SCREEN_DEMO:
                self._start_demo()
            self._dirty = True

    def _reload_profile(self):
        self.profile = boards.load(self.cfg)
        self.engine = ledfx.LedEngine(self.profile, self.cfg)

    # -- calibration ---------------------------------------------------------

    def open_calibration(self, mode=views.CalibrateView.MODE_PHASE):
        self._cal_mode = mode
        self._cal_index = 0
        self._cal_phase = 0
        self._cal_map = [[] for _ in range(layout_mod.EDGES)]
        self.screen = SCREEN_CALIBRATE
        self._dirty = True

    def _calibrate_buttons(self):
        if self._pressed("CANCEL"):
            self.screen = SCREEN_DASH
            self._dirty = True
            return

        if self._cal_mode == views.CalibrateView.MODE_PHASE:
            if self._pressed("CONFIRM"):
                self._save_phase(self._cal_phase)
            elif self._pressed("DOWN") or self._pressed("UP"):
                # Show the other hypothesis. Two options, so either key flips.
                self._cal_phase = 1 - self._cal_phase
                self._dirty = True
            elif self._pressed("RIGHT"):
                # Neither looked right: fall through to mapping LED by LED,
                # which is what makes an unknown board workable at all.
                self._cal_mode = views.CalibrateView.MODE_LED
                self._cal_index = 0
                self._dirty = True
            return

        if self._pressed("UP"):
            self.selected_edge = ((self.selected_edge or 0) - 1) % layout_mod.EDGES
            self._dirty = True
        elif self._pressed("DOWN"):
            self.selected_edge = ((self.selected_edge or 0) + 1) % layout_mod.EDGES
            self._dirty = True
        elif self._pressed("CONFIRM"):
            self._record_led(self.selected_edge or 0)

    def _save_phase(self, phase):
        """Store the phase as an explicit map, so it cannot be reinterpreted."""
        groups = boards.edge_leds(self.profile.led_count, phase)
        self.cfg["board_map"] = [list(g) for g in groups]
        C.save(self.cfg)
        self._reload_profile()
        self.screen = SCREEN_DASH
        self.notification = Notification("Edges calibrated")
        self._dirty = True

    def _record_led(self, edge):
        self._cal_map[edge].append(self._cal_index)
        self._cal_index += 1
        if self._cal_index < self.profile.led_count:
            self._dirty = True
            return
        if boards.valid_map(self._cal_map, self.profile.led_count):
            self.cfg["board_map"] = [list(g) for g in self._cal_map]
            C.save(self.cfg)
            self._reload_profile()
            self.notification = Notification("Edges calibrated")
        else:
            # Every LED has to land somewhere, exactly once. Rather than save a
            # half-map that would render wrongly forever, say so and stop.
            self.notification = Notification("Incomplete - not saved")
        self.screen = SCREEN_DASH
        self._dirty = True

    # -- shutdown ------------------------------------------------------------

    def _shutdown(self):
        self._release_ring()
        self.button_states.clear()
        self.minimise()


__app_export__ = EdgewiseApp
