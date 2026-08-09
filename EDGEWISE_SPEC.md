# Edgewise — a desk status board for the EMF Tildagon / Spaceagon badge

**Project brief & build plan (hand this file to Claude Code as the project spec).**
Research snapshot: 2026-08-07/08 (app directory + docs verified then).

One sentence: **each edge of the hexagon is one job — a CI build, a 3D print, a coding agent, a
kiln — and the edge's LEDs tell you its state; when something needs you, its edge flashes and you
tap it to acknowledge (or approve) from the badge.** Anything that can send one MQTT message can
drive it. Badge button/touch presses are published back, so integrations are two-way.

**Positioning (important):** Edgewise is a *generic* status semaphore, not an AI accessory. The
Claude Code integration is one adapter in the docs, listed alongside CI, cron, OctoPrint and Home
Assistant adapters with equal billing. Nothing in the app UI or store listing mentions AI.

---

## 1. Goals

**MVP**
1. Up to 6 "slots" (jobs), each mapped to an edge; LED segment per edge shows slot state via a
   colour/animation language; round screen shows the dashboard (labels, states, waiting timers).
2. Adaptive layout: active slots always occupy the maximally spaced edges (2 slots → opposite
   edges, 3 → alternating) and re-balance automatically as slots come and go.
3. MQTT inbound: semantic slot updates, raw LED/segment control with WLED-style effects, short
   screen messages. Retained state + TTL expiry so the board survives reboots and dead jobs fade.
4. MQTT outbound events: taps, long-presses, acknowledgements, snooze (badge flipped face-down),
   online/offline availability.
5. Input: capacitive touch ring on the 2026 front board, with a full button-based fallback for
   2024 Tildagons; OS button conventions respected (CANCEL exits, CONFIRM confirms).
6. First-run demo mode that teaches the colour language in ~10 seconds, plus a QR to the repo.
7. Broker configurable (own broker recommended, public-broker convenience mode clearly flagged).
8. **Deliverable includes a public, well-documented GitHub repo** with copy-paste adapters
   (Claude Code hooks, Home Assistant, CI, shell/cron, OctoPrint) — see §13.

**Stretch (flagged)**
- Home Assistant MQTT discovery: edges appear as HA light entities, badge events as device
  triggers, availability sensor — zero-YAML setup.
- Signed messages (HMAC) and the **approve-from-badge** flow for Claude Code permission prompts.
- Background mode: keep LEDs updating while the badge menu / another app is in the foreground
  (v1 is a foreground app; see §15 V-6).
- Per-slot custom palettes; audible alert via buzzer hexpansions.

**Non-goals (v1):** running a broker on the badge; ~~non-MQTT transports (HTTP listener may come
later)~~ — **overridden, see below**; full WLED API compatibility (we borrow the *style* of its
effects, not the protocol); history/logging.

> **Override, v0.11.0 — the badge listens.** "HTTP listener may come later" was the escape hatch,
> and it has been taken. A host-side bridge existed briefly and was the wrong shape: a second
> machine that had to be running, holding the broker credentials, translating URLs into publishes.
> If you want to poke the badge, you should be able to poke the badge.
>
> MQTT is **not** replaced and is not redundant. It still carries what a listener cannot: slots
> retained so the board rebuilds itself after a reboot, the `event` stream that pushes taps to every
> subscriber at once, and reachability for publishers that cannot route to the badge.
>
> The thing the original non-goal was protecting against — a badge with an attack surface — is
> answered by construction rather than by abstention. The HTTP path builds the same payload the MQTT
> path does and hands it to the same validator, so it inherits every cap and the entire
> hostile-input corpus; the 3 Hz strobe limit applies without `httpd.py` mentioning it. It is off by
> default, requires a token, and every bound is checked before any work is done.

---

## 2. Prior art & naming

Full app-directory scan (244 apps, 2026-08-07): nothing does per-task status slots with two-way
integration. Nearest neighbours and the gap: **Gadgetbridge** / **iOS Notifications** mirror phone
notifications (no per-task slots, no custom sources); **HA Bridge** is HA→badge remote control
(LEDs/screen/battery) but not a task board and not source-agnostic; **CheerLights** is the shared
global-colour MQTT art project; **Social Battery** displays self-reported status only. These prove
demand (badge as desk notifier) and platform feasibility (MQTT apps ship today).

Name: **Edgewise** (sessions live on the edges; "get a word in edgewise"). No store collision as
of the snapshot. Store category **Apps**. Store description (≤140 chars):
`Desk status board: each edge is one job (CI, prints, agents, kiln). One MQTT line from anything;
the edge lights up when it needs you.`

---

## 3. Platform facts (verified; details in the SkyScope spec §3, same platform)

- ESP32-S3, 2 MB PSRAM, 8 MB flash, Wi-Fi, IMU; round 240×240 ctx canvas (coords −120…+120);
  six buttons; RGB LED ring; MicroPython "Tildagon OS". Same app API for 2024 + 2026 boards.
- App model: subclass `app.App`; `update(delta)` + `draw(ctx)` at ~20 Hz; `async run()` for the
  MQTT loop; `background_task()` exists; `minimise()` on CANCEL. Buttons via `events.input`.
  UI widgets in `app_components`. Docs: https://tildagon.badge.emfcamp.org/tildagon-apps/development/
- **MQTT:** official example — https://tildagon.badge.emfcamp.org/tildagon-apps/examples/mqtt/ —
  and shipped apps (HA Bridge, CheerLights, Froods) prove the client works on-badge. Confirm
  client details (umqtt variant, TLS, LWT, keepalive) in V-1.
- **LEDs:** the ring has more LEDs than edges (Tildagon 2024 ≈ 2 per edge). Exact count, index
  order and edge mapping per board revision is verification task **V-2** — the "Advanced LEDs"
  app (drives "badge ring and hexpansion neopixels as one string") is the reference to read.
- **Touch:** the 2026 front board has a capacitive touch ring (~12 pads — HexType does two-tap
  text entry on them; bat-cat and Herzog use ring taps). Find the firmware touch API and
  feature-detect it (**V-3**); 2024 badges get the button fallback (§7).
- **Board profiles (2024 / 2026 / future):** every hardware difference (LED count + index→edge
  map, touch availability + pad geometry, sensible defaults) lives in a per-board profile file —
  `boards/tildagon_2024.py`, `boards/spaceagon_2026.py`. The app auto-detects the board where
  firmware allows (**V-9** — the app store filters apps by "2026 Frontboard", so an API likely
  exists), always offers a manual override in settings, and shows a one-time picker on first run
  if detection is ambiguous. An **identify/calibrate** screen lights each edge segment in turn
  ("is this the top edge?") so an unknown board can be mapped by hand. A 2028 board then costs
  one new profile file — or nothing at all, via calibrate — with zero core-code changes.
- **Buttons (OS conventions, keep them):** A UP, B RIGHT, C CONFIRM, D DOWN, E LEFT, F CANCEL.
- Publishing: repo + `tildagon.toml` + release + `tildagon-app` topic;
  https://tildagon.badge.emfcamp.org/tildagon-apps/publish/
- Desk use: badge is powered over the USB-C lanyard cable; a 3D-printable "display platform
  hexpansion" exists for desk-standing — link it in the README. Add a **rotation setting** so
  "top edge" is correct whether the badge hangs, stands, or leans.

---

## 4. Core model: slots, states, layout

### 4.1 Slots and states
A **slot** is a named job (`api`, `kiln`, `build`). Max 12 tracked, 6 displayed (nearest-priority
first). Every slot has: `label`, `state`, optional `msg`, `last_change` timestamp, `ttl`, origin
(pinned edge or auto), and a staleness clock.

| State | Default LED language | Meaning |
|---|---|---|
| `working` | amber, slow breathe | job is running, ignore it |
| `needs_you` | cyan, flash (≤3 Hz hard cap) | input/permission/attention required |
| `done` | green, solid | finished, awaiting review |
| `error` | red, double-blink then solid | job failed |
| `info` | white, single pulse then dim | FYI, non-urgent |
| `clear` / TTL expiry | edge fades out over 2 s | slot removed |

Screen dashboard mirrors this with per-edge arcs, labels, and elapsed-in-state timers
("waiting 4m"), centre shows `N need you`. Palette is user-configurable; defaults chosen for
colour-blind separability (amber/cyan/green/red differ in brightness + animation, not hue alone).

### 4.2 Adaptive edge layout (the "2 sessions → top and bottom" behaviour)
Edges indexed 0–5, 0 = top after the rotation setting is applied, clockwise. For k unpinned
active slots, occupy the size-k edge subset with **maximum minimum circular spacing**:
k=1→{0} · k=2→{0,3} (opposite pair) · k=3→{0,2,4} (alternating) · k=4→{0,1,3,4} ·
k=5→{0,1,2,3,4} · k=6→all.

Rules: (a) **sticky** — when the subset changes, keep every already-placed slot that is still in
the new subset and move the fewest slots possible (assign movers to nearest free edge);
(b) **hysteresis** — re-layout no sooner than 10 s after the triggering change, so a session
flapping in/out doesn't reshuffle the board; (c) **pinning** — a slot with an explicit `edge`
field owns that edge; auto-layout distributes the rest across remaining edges with the same
max-spacing rule; (d) transitions animate (old segment fades as the new one fades in, ~500 ms).

---

## 5. LED engine (WLED-style, badge-scale)

Two abstractions:
- **Segment** = an ordered list of LED indices. Built-ins: `edge:0`…`edge:5` (that edge's LEDs,
  from the V-2 mapping), `ring` (all), `raw` (explicit index list).
- **Effect** = animation applied to a segment: `solid, breathe, blink, chase, comet, sparkle,
  rainbow, wipe`. Params: `rgb` (+ optional `rgb2`), `speed` 0–255, `intensity` 0–255,
  `brightness` 0–255. Implemented as a single ticker in `update()` writing one frame per tick —
  effects are pure functions of (t, params) so they cost no allocations per frame.

**Hard safety/comfort caps enforced after every request, no exceptions:**
- Flash/strobe frequency capped at **3 Hz** (photosensitive-seizure precaution) — faster
  requests are clamped, never honoured.
- Global brightness ceiling setting + **night mode** (schedule or manual: dim to ~10%,
  `needs_you` still animates but gently).
- Semantic states (§4.1) map to preset effect configs; the raw `led` topic can override a
  segment's look but *not* exceed the caps, and semantic state resumes when the raw TTL lapses.

---

## 6. MQTT protocol

Topic root: `edgewise/<device-id>/`. `<device-id>` = 128-bit random, base32 (~26 chars),
generated on first run, shown on demand in settings (with QR), regenerable.

| Topic (suffix) | Dir | Retained | Payload |
|---|---|---|---|
| `slot/<name>` | in | yes | semantic slot update (below) |
| `led` | in | optional | raw segment/effect control |
| `text` | in | no | short screen message |
| `event` | out | no | taps, acks, snooze, flips (§7) |
| `availability` | out | yes | `online` / `offline` — set via MQTT **Last Will** so the broker flips it if the badge drops |

**`slot/<name>` payload** (JSON; unknown fields ignored):
```json
{"state":"needs_you", "label":"kiln", "msg":"door open?",
 "edge":3, "ttl":7200, "ts":1754640000, "sig":"<hex hmac, signed mode only>"}
```
- `state` required (enum §4.1); `label` ≤16 chars (defaults to slot name); `msg` ≤64 chars;
  `edge` 0–5 optional pin; `ttl` seconds, default 3600, max 86400.
- Publishers SHOULD set the MQTT retained flag so the board repaints after reboot; **an empty
  retained payload deletes the slot** (standard MQTT retained-clear), and `{"state":"clear"}`
  does the same.

**`led` payload:** `{"segment":"edge:2", "effect":"comet", "rgb":[255,0,80], "speed":180,
"brightness":200, "ttl":600}` (or `"leds":[7,8,9]` instead of `segment`).

**`text` payload:** `{"msg":"Bins tonight", "duration":120, "level":"info"}` — `alert` level also
pulses the ring white once. `duration` ≤300 s.

QoS 1 for inbound subscriptions and outbound events; keepalive ~60 s; auto-reconnect with
backoff; on reconnect the retained messages rebuild the whole board (this is the crash-recovery
story — the badge holds no state that matters).

---

## 7. Input & outbound events (the two-way part)

| Gesture | 2026 (touch ring) | 2024 fallback (buttons) | Event published | Default meaning |
|---|---|---|---|---|
| select/ack a slot | tap the pads beside its edge | UP/DOWN to highlight edge, CONFIRM | `{"type":"ack","slot":"kiln","edge":3}` | acknowledge / approve; clears `needs_you` → `working` locally |
| dismiss/deny | long-press pads | highlight + long-press CONFIRM | `{"type":"deny", …}` | deny / dismiss; slot dims |
| open detail view | double-tap | highlight + RIGHT | `{"type":"tap", …}` | show label, msg, state, elapsed, origin |
| global snooze | flip badge face-down (IMU) | — | `{"type":"snooze"}` / `{"type":"wake"}` | all LEDs to night-dim until flipped back |
| exit / settings | F CANCEL / E LEFT | same | — | OS conventions untouched |

All events include `ts` and are plain observations — **the badge never interprets an ack as
"safe"; what an ack *does* is decided by the subscriber** (HA automation, hook script, etc.).
Detail view shows a slot's full `msg`, which is what makes approve-from-badge usable ("Claude
wants to run: npm install").

## 8. Screen UI

- **Dashboard (default):** six arc segments hugging the rim, coloured/animated to match their
  edge LEDs; label + elapsed timer per active slot; centre: big `2 need you` (or a calm tick
  when nothing does); thin footer: broker status dot + device-ID short form.
- **Detail view:** one slot — label, state, message, time in state, pinned/auto, ack/deny hints.
- **Demo mode (first run + settings entry):** self-running fake slots walk through the colour
  language with captions ("each edge is a job… amber = working… flashing = needs you"), ends on
  a QR code to the repo README. This is the app's 10-second pitch in the bar tent.
- **Settings:** broker (host/port/user/pass/TLS), device ID (show QR / regenerate), HA discovery
  on/off, brightness + night mode, **board** (Auto / Tildagon 2024 / Spaceagon 2026 / Custom via
  identify-edges calibration), rotation, palette, require-signed toggle, About (version,
  licence, repo QR).

---

## 9. Home Assistant integration (stretch, high value)

Two tiers so it works for everyone:

1. **Plain MQTT (zero setup):** any HA automation publishes to `edgewise/<id>/slot/…` — washing
   machine done → `{"state":"done","label":"wash"}`; doorbell → `text` alert. Ship 4–5 example
   automations in `docs/adapters/home-assistant.md`.
2. **MQTT discovery (toggle in settings, default off):** on connect the badge publishes HA
   discovery configs so it appears as a device with: six **light** entities (one per edge — raw
   `led` control mapped through), a **notify** target (→ `text`), **event/device-trigger**
   entities for `ack/deny/tap/snooze` (badge becomes a 6-key desk remote for HA), and an
   **availability** binary sensor riding the LWT topic. No YAML anywhere.

## 10. Claude Code adapter (`adapters/claude-code/`)

Claude Code hooks are shell commands fired on lifecycle events, configured in
`.claude/settings.json`; each receives JSON on stdin including the session's working directory
(reference: https://code.claude.com/docs/en/hooks). Mapping — slot name = `basename $PWD`:

| Hook event | Publish |
|---|---|
| `UserPromptSubmit` | `{"state":"working"}` |
| `Notification` | `{"state":"needs_you","msg":"<notification text, truncated 64>"}` |
| `Stop` | `{"state":"done"}` |
| `SessionEnd` | empty retained payload (clears the slot) |

Ship: `edgewise-pub.sh` (thin wrapper over `mosquitto_pub`, reads `EDGEWISE_ID`/`EDGEWISE_BROKER`
from `~/.config/edgewise/env`), a `paho-mqtt` Python alternative for machines without mosquitto
clients, and `install-hooks.sh` — **short, reviewable, idempotent**: prints the JSON it will
merge into `.claude/settings.json`, asks for confirmation, makes a backup, never needs sudo,
never pipes from the network. Label privacy flag: `EDGEWISE_LABELS=name|hash` (hash = 6-char
digest of the path instead of the folder name, for screenshots/public brokers).

**Approve-from-badge (advanced, off by default):** a `PermissionRequest` hook publishes
`needs_you` with the requested action as `msg`, then blocks on
`mosquitto_sub -C 1 -W <timeout>` filtered to this slot's `ack`/`deny` events. `ack` → exit 0
(allow flow proceeds), `deny` → exit 2 (blocked, reason on stderr), **timeout → exit 0 with no
decision, so the normal terminal prompt appears as if the hook wasn't there** — fail-safe.
Because this turns an MQTT message into a code-execution approval, the installer refuses to
enable it unless the broker is private/authenticated **or** signed mode (§11) is on, and it
documents why in bold.

## 11. Security & safety

**Threat model:** public-broker spoofing/eavesdropping; malicious payloads (oversize, junk,
strobe requests, screen-text abuse); privacy leakage (project names, presence); replay of
captured approval messages; supply-chain concerns about installer scripts.

Controls (badge side — all inbound is untrusted):
- Parse defensively: schema-check every message; cap `label` 16 / `msg` 64 / `text` 64 chars;
  strip to printable ASCII; ignore unknown topics/fields; max 12 retained slots; rate-limit
  inbound to ~5 msg/s (excess dropped + one status-line notice); TTL mandatory-with-default so
  nothing persists forever; payloads are data only — nothing is ever eval'd or executed.
- Strobe/brightness caps (§5) are enforced *after* all parsing paths, including raw `led`.
- Device ID: 128-bit random; shown only on demand; one-tap regenerate (re-publishes discovery,
  old topic goes silent).
- Broker: username/password supported; TLS if the on-badge MQTT client supports it within
  memory limits (**V-4**); the settings screen shows a persistent "public broker — anyone with
  your ID can write to your lights" note in convenience mode. Recommended: HA's Mosquitto
  add-on or any LAN broker.
- **Signed mode (stretch):** shared secret on badge; publishers append `sig` = HMAC-SHA256 over
  the canonical payload incl. `ts`; badge rejects bad sigs and stale `ts` (>60 s skew) to stop
  replays. Required for the approval adapter on non-private brokers.
- Outbound events carry no sensitive content (type, slot, edge, ts only).
- Repo ships `SECURITY.md` (reporting contact, threat model summary) and keeps every installer
  reviewable in <60 lines.

## 12. Config schema (persisted on badge; defaults shown)

```json
{"version":1,
 "broker":{"host":"broker.emqx.io","port":1883,"user":null,"pass":null,"tls":false},
 "device_id":"<generated>", "require_signed":false, "hmac_key":null,
 "ha_discovery":false, "board":"auto", "board_map":null, "rotation":0, "brightness":180,
 "night":{"enabled":true,"from":"22:00","to":"07:00","level":25},
 "palette":"default", "max_slots":12}
```
Storage per https://tildagon.badge.emfcamp.org/tildagon-apps/configuration/ (atomic writes,
tolerate missing/corrupt file). Default public broker is a placeholder — pick one with a stated
acceptable-use policy at build time (**V-5**) and label it clearly in the UI.

## 13. Repository & documentation deliverables (a first-class requirement)

Public GitHub repo (`tildagon-app` topic, MIT, releases). Layout:
```
edgewise/
├── app.py  tildagon.toml  conf.py  layout.py  ledfx.py  mqtt_link.py
├── model.py  views.py  demo.py  security.py  fixtures.py
├── boards/            (tildagon_2024.py, spaceagon_2026.py — LED maps, touch, defaults)
├── adapters/
│   ├── claude-code/   (hooks JSON, install-hooks.sh, edgewise-pub.sh, README)
│   ├── home-assistant/ (discovery notes, example automations YAML, README)
│   ├── ci/            (GitHub Actions step, generic post-build one-liner)
│   ├── shell/         (make/cron wrapper: `run-and-report <slot> -- <cmd>`)
│   └── octoprint/     (MQTT plugin topic mapping)
├── docs/protocol.md  docs/security.md  SECURITY.md  CONTRIBUTING.md  LICENSE  README.md
└── .gitattributes     (docs/, adapters/, README export-ignore so badges don't download them)
```
README bar: what-it-is in one paragraph + photo/GIF of the badge on a desk, 60-second quickstart
(install code → broker → one `mosquitto_pub` line that lights an edge), the state table, adapter
links, and the security section up front. Every adapter page ends with a **tested** copy-paste
block. Acceptance: a stranger with a badge and mosquitto-clients gets a lit edge in under five
minutes using only the README.

## 14. Milestones

- **M0 Scaffold + demo:** app shell, board-profile system (auto-detect → first-run picker →
  settings override), dashboard rendering with fake slots, demo mode, settings skeleton; runs in
  simulator + on badge. ✅ demo teaches the language unattended; wrong-board selection is
  recoverable from settings.
- **M1 Layout + LED engine:** §4.2 algorithm with unit tests (CPython), §5 effects at 20 Hz with
  zero per-frame allocation, caps enforced. ✅ 2→opposite / 3→alternating verified; sticky +
  hysteresis behave under churn fixtures.
- **M2 MQTT live:** subscribe, retained rebuild, TTL expiry, LWT availability, reconnect
  backoff, inbound validation + rate limiting. ✅ 24 h soak with a chaos publisher (junk, floods,
  oversize) — no crash, no cap bypass.
- **M3 Two-way input:** touch (2026) + button fallback (2024) + IMU flip; events published;
  detail view. ✅ ack from badge visible on `mosquitto_sub` in <500 ms.
- **M4 Adapters + docs:** everything in §13 written and *tested against a real badge*; repo
  public. ✅ the five-minute-stranger test passes.
- **M5 HA discovery.**  **M6 Signed mode + approval adapter.**  **M7 Publish to the store.**

## 15. Verification tasks (do first)

- **V-1** On-badge MQTT client capabilities: umqtt variant, QoS1, retained handling, LWT, TLS,
  keepalive behaviour while `draw()` runs. Read the official MQTT example + HA Bridge source.
- **V-2** LED map: exact ring LED count and index→edge mapping on Tildagon 2024 **and**
  Spaceagon 2026 (read "Advanced LEDs" app + firmware source; build a mapping table per board).
- **V-3** Touch API: locate the capacitive-ring interface in firmware (HexType/bat-cat sources),
  confirm feature-detection path and pad→edge geometry.
- **V-4** TLS feasibility within PSRAM; if too heavy, document auth-only + LAN-broker guidance.
- **V-5** Choose the default public broker (AUP, uptime) or decide to ship with none configured.
- **V-6** Decide foreground-only vs background LED updates: check what Background-category apps
  and the pattern system allow; v1 ships foreground, note the upgrade path.
- **V-7** Re-check the store for new lookalike apps; confirm "Edgewise" still free.
- **V-8** Confirm current Claude Code hook payloads/events against https://code.claude.com/docs/en/hooks
  before finalising the adapter (they evolve).
- **V-9** Board identification: find how firmware exposes the board/frontboard revision (the
  store filters by "2026 Frontboard", and the Capabilities system exists — read the
  "Capabilities" app `40302440` and firmware source); wire it to auto-detect with the manual
  override from §3. Confirm the LED API length can be queried at runtime as a sanity check.

## 16. Risks

| Risk | Mitigation |
|---|---|
| Public-broker abuse / spoofing | untrusted-input controls §11, signed mode, loud UI labelling, private-broker default docs |
| Approval flow misused | gated install (private broker or signed mode), fail-safe timeout to terminal prompt |
| LED/touch APIs differ across 2024/2026 boards, and 2028 may change again | V-2/V-3/V-9 first; per-board profile files + auto-detect + manual picker; identify/calibrate screen makes even an unknown future board mappable without a code update; button fallback path |
| MQTT client limits (TLS, LWT) | V-1/V-4 before M2; degrade gracefully with clear settings text |
| Effects engine starves `update()` loop | precomputed frames, no per-tick allocation, profile at M1 |
| "AI totem" perception | positioning in §0/§2: generic tool, adapter parity, neutral store copy |
| Community fork/PRs stall on docs debt | §13 acceptance test is a release gate |

## 17. References

- Tildagon docs root: https://tildagon.badge.emfcamp.org/ · MQTT example: …/tildagon-apps/examples/mqtt/
- App dev / ctx / ui-elements / configuration / publish: as listed in the SkyScope spec §3, §10
- App directory: https://apps.badge.emfcamp.org/ (HA Bridge `34021444`, CheerLights `21433412`,
  Gadgetbridge `12423340`, HexType `43322310`, Advanced LEDs `42130144` — read these sources)
- Firmware + simulator: https://github.com/emfcamp/badge-2024-software
- Claude Code hooks reference: https://code.claude.com/docs/en/hooks
- HA MQTT discovery: https://www.home-assistant.io/integrations/mqtt/ (verify current schema at build)
