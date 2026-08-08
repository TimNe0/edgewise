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
from system.patterndisplay.events import PatternDisable, PatternEnable

from . import boards, clock, conf as C, demo as demo_mod, layout as layout_mod, ledfx
from . import model, mqtt_link, security, views
from .render_ctx import CtxRenderer

VERSION = "0.1.0"

SCREEN_DASH = 0
SCREEN_DETAIL = 1
SCREEN_SETTINGS = 2
SCREEN_CALIBRATE = 3
SCREEN_PICKER = 4
SCREEN_DEMO = 5

# Ignore button state briefly after launch, so the press that opened the app is
# not read as a command.
STARTUP_GRACE_MS = 400

# The ring animates at 20 Hz. The screen does not need to: its slowest-changing
# element is a timer that ticks once a second, and ctx rasterisation is the most
# expensive thing this app does.
LED_INTERVAL_MS = 50
SCREEN_INTERVAL_MS = 200
IDLE_REDRAW_MS = 1000
# The OS pattern generator has to be told repeatedly to keep off the ring.
PATTERN_SUPPRESS_MS = 1000

LONG_PRESS_MS = 600


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

        self.notification = None
        self.link = None
        self.link_state = "offline"
        # Policy, not frame protection: the drain cap is what protects the
        # render loop. This is what stops a publisher filling the board faster
        # than anyone could read it.
        self.limiter = security.RateLimiter()
        self._seen_epoch = 0
        self._rebuilding = False
        self._flood_notice_ms = 0
        self.message = None
        self.message_until_ms = 0

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
        self._denied = False
        self._uptime_ms = 0
        self._last_tick_ms = time.ticks_ms()
        self._led_timer = 0
        self._pattern_timer = 0
        self._leds_owned = False

        self._dirty = True
        self._since_draw_ms = 0
        self._last_draw_state = None

        # A board whose profile was guessed rather than detected, on a first
        # run, is the one case where asking is better than assuming.
        if (self.profile.source == boards.SOURCE_FALLBACK
                and self.cfg["board"] == boards.KEY_AUTO):
            self.screen = SCREEN_PICKER
        elif not self.cfg["seen_demo"]:
            self._start_demo()

        self._open_link()

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
            if kind == "slot":
                self._apply_slot(name, payload, now)
            elif kind == "led":
                spec = security.parse_led(payload)
                if spec:
                    self.engine.set_raw(spec, now)
            elif kind == "text":
                self._show_message(security.parse_text(payload), now)

    def _apply_slot(self, name, payload, now):
        name = security.clean_text(name, 24)
        if not name:
            return
        parsed = security.parse_slot(payload)
        if parsed is None:
            return
        if self.board.apply(name, parsed, now) != model.CHANGE_NONE:
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

    def background_update(self, delta):
        """Runs even when the app is not on screen.

        The only place that notices focus being taken away by something other
        than our own CANCEL handler. Without it the ring keeps whatever the last
        frame left on it, with the OS pattern still suppressed, and the LEDs
        look stuck on.
        """
        wanted = getattr(self, "_foreground", True)
        if wanted == self._leds_owned:
            return
        self._leds_owned = wanted
        if wanted:
            eventbus.emit(PatternDisable())
        else:
            self.engine.all_off()
            eventbus.emit(PatternEnable())

    async def run(self, render_update):
        while True:
            now = time.ticks_ms()
            delta = time.ticks_diff(now, self._last_tick_ms)
            self._last_tick_ms = now
            self._since_draw_ms += delta
            self.update(delta)

            if not getattr(self, "_foreground", True):
                # render_update blocks until the app is on screen again.
                if await render_update():
                    self._dirty = True
            elif self._needs_draw():
                await self._render(render_update)
            else:
                await asyncio.sleep(0.02)

    async def _render(self, render_update):
        self._dirty = False
        self._since_draw_ms = 0
        self._last_draw_state = self._draw_state()
        return await render_update()

    def update(self, delta):
        self._uptime_ms += delta
        now = clock.now_ms()

        self._handle_buttons()
        self._service_link(now)

        if self.screen == SCREEN_DEMO and self.demo.tick(now):
            self._dirty = True

        if self.message is not None and clock.expired(self.message_until_ms, now):
            self.message = None
            self._dirty = True

        gone = self.board.expire(now)
        if gone:
            self._dirty = True

        self._sync_layout(now)
        self._drive_leds(delta, now)

    def _sync_layout(self, now):
        names = self.board.names()
        urgent = self.board.urgent_names()
        if self.layout.sync(names, self.board.pins(), now, urgent):
            self._dirty = True

    # -- LEDs ----------------------------------------------------------------

    def _drive_leds(self, delta, now):
        if not self._leds_owned and getattr(self, "_foreground", True):
            self._leds_owned = True
            eventbus.emit(PatternDisable())
        if not self._leds_owned:
            return

        self._led_timer += delta
        if self._led_timer < LED_INTERVAL_MS:
            return
        self._led_timer = 0

        # The OS pattern generator keeps trying to reclaim the ring; once a
        # second is enough to hold it off.
        self._pattern_timer += LED_INTERVAL_MS
        if self._pattern_timer >= PATTERN_SUPPRESS_MS:
            self._pattern_timer = 0
            eventbus.emit(PatternDisable())

        if self.screen == SCREEN_CALIBRATE:
            self._calibration_frame(now)
            return

        self.engine.night_level = self._night_level()
        for edge in range(layout_mod.EDGES):
            name = self.layout.slot_at(edge)
            slot = self.board.slots.get(name) if name else None
            if slot is None:
                self.engine.clear_state(edge)
            else:
                self.engine.set_state(edge, slot.state, slot.age_ms(now), now,
                                      slot.is_stale(now))
        self.engine.render(now)

    def _night_level(self):
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
            engine.clear_state(edge)
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
                                self.cfg)
            if self.screen == SCREEN_DEMO and self.demo.caption:
                self._demo_caption(r)
            elif self.message is not None:
                self.messages.draw(r, self.message["msg"], self.message["level"])

        if self.notification is not None:
            self.notification.draw(ctx)

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
            if name == "CONFIRM":
                self._denied = False
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
            if self._pressed("CANCEL") or self._pressed("LEFT"):
                self.screen = SCREEN_DASH
                self._dirty = True
            return

        if self._pressed("CANCEL"):
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
            name = self._selected_name()
            if name:
                self._detail_name = name
                self.screen = SCREEN_DETAIL
                self._dirty = True
            return
        if self._pressed("CONFIRM"):
            self._acknowledge()
            return
        # Long-press CONFIRM denies. Fired on the timer while still held rather
        # than on release, so the ring can confirm the moment it registers --
        # holding a button for a second with no feedback reads as broken.
        if ("CONFIRM" in self._held and not self._denied
                and self._held_for("CONFIRM") >= LONG_PRESS_MS):
            self._denied = True
            self._deny()

    def _selected_name(self):
        if self.selected_edge is None:
            return None
        return self.layout.slot_at(self.selected_edge)

    def _acknowledge(self):
        name = self._selected_name()
        if name is None:
            return
        self._publish_event("ack", name, self.selected_edge)
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
        self.notification = Notification("Denied")
        self._dirty = True

    def _publish_event(self, kind, name, edge):
        """Outbound events carry no content beyond what happened, and where.

        No labels, no messages, no project names: an event topic is the one
        thing a subscriber cannot have already seen, so it is kept boring.
        """
        if self.link is None:
            return
        payload = '{"type":"%s","slot":"%s","edge":%s,"ts":%d}' % (
            kind, name, edge if edge is not None else "null", self._wall_clock())
        self.link.publish_event(payload.encode())

    def _wall_clock(self):
        try:
            return int(time.time())
        except Exception:  # noqa: BLE001
            return 0

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
        if boards._valid_map(self._cal_map, self.profile.led_count):
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
        self._leds_owned = False
        self.engine.all_off()
        eventbus.emit(PatternEnable())
        self.button_states.clear()
        self.minimise()


__app_export__ = EdgewiseApp
