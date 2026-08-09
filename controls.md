# Controls

Single source of truth for what every input does. If this table and the code
disagree, the code is wrong.

On a 2026 Spaceagon the touch ring drives everything directly: tap an edge's
pads to acknowledge, hold to deny. On a 2024 Tildagon you highlight an edge with
UP/DOWN and press CONFIRM to open it, then decide from the detail view.

The two differ on purpose. Pointing at an edge is unambiguous about which slot
you mean; pressing a button is not, and CONFIRM means "select" everywhere else
on the badge. Both paths end at the same recogniser, so tap and hold keep
identical timings wherever a decision is actually made.

## Dashboard

| Gesture | 2026 (touch ring) | 2024 (buttons) | Published | Means |
|---|---|---|---|---|
| open a slot | double-tap | UP/DOWN to highlight, **CONFIRM** | `{"type":"tap", …}` | label, state, message, elapsed, pinned |
| acknowledge | tap the edge's pads | CONFIRM again, from the detail view | `{"type":"ack","slot":…,"edge":…}` | acknowledge / approve; the edge stops asking |
| deny | hold the pads (0.6 s) | hold CONFIRM (0.6 s) in the detail view | `{"type":"deny", …}` | deny / dismiss |
| dismiss locally | — | highlight, **hold CANCEL** (0.6 s) | — | takes it off this badge only; see below |
| snooze | turn the badge face down | — | `{"type":"snooze"}` / `{"type":"wake"}` | everything dims; nothing is cleared |
| settings | LEFT | LEFT | — | |
| exit | CANCEL | CANCEL | — | clears the highlight first, then minimises |

On buttons, CONFIRM **selects** — it opens the slot rather than acknowledging
it. That is what CONFIRM does in every other app on the badge, and it means you
have read the message before you decide, which for approve-from-badge is the
entire point of the message. The decision is one screen in.

The touch ring keeps its direct tap-to-acknowledge. Pointing at an edge says
which slot you mean without ambiguity, and there is no button convention to be
consistent with.

## Detail view

This is where decisions are made, because it is where the message is legible.

| Input | Does |
|---|---|
| CONFIRM | acknowledge, and back to the dashboard |
| hold CONFIRM (0.6 s) | deny, and back to the dashboard |
| CANCEL / LEFT | back to the dashboard, deciding nothing |

If the slot disappears while you are reading it -- acknowledged elsewhere,
expired, or cleared by its publisher -- the view returns to the board rather
than sitting on a tombstone.

## Settings

Reached with LEFT from the dashboard. Two levels: a list of groups, then the
items in one group.

| Input | Does |
|---|---|
| UP / DOWN | move the cursor; it wraps |
| CONFIRM / RIGHT | open a group, or edit the item |
| CANCEL / LEFT | back a level, then out to the dashboard |

Toggles and choices change in place, with no dialog. Anything you have to type
opens the badge's own text dialog — the same one the app store and every other
app uses, which is why a keyboard hexpansion works here without Edgewise
knowing it exists. CANCEL in that dialog leaves the old value alone; confirming
an empty field really does clear it.

Every change is saved and applied the moment it is made. Nothing waits for a
restart, because a setting that appears to do nothing is a setting people
assume is broken.

## Calibration

Reached from settings. Answers which LEDs make up an edge, which no firmware
source records.

| Input | Does |
|---|---|
| CONFIRM | yes, that is one complete edge — saves and exits |
| UP / DOWN | show the other option |
| RIGHT | neither looks right: switch to mapping LED by LED |
| CANCEL | give up, change nothing |

In per-LED mode, UP/DOWN choose an edge and CONFIRM assigns the lit LED to it.
Every LED must land on exactly one edge; an incomplete map is discarded rather
than saved, because a half-map renders wrongly forever and gives no clue why.

## Notes on the timings

- **Long-press fires while the button is still down**, not on release, so the
  ring can confirm at the moment it registers.
- **A single tap is held back for 350 ms** to tell it from a double tap. That
  delay is deliberate: an `ack` cannot be retracted once a subscriber has acted
  on it, so a spurious one is worse than a third of a second of latency.
- **Dragging a finger around the ring acknowledges nothing.** A gesture that
  ends because the contact moved to another edge is not a tap.
- **Dismissing is local, and says so.** Acknowledging tells the publisher you
  have seen something; what happens next is the publisher's decision, which is
  why an ack has never removed a slot. Holding CANCEL takes it off *this badge*.
  If it is still retained on the broker it comes back on the next reconnect,
  because the publisher still thinks it matters and the badge is not the
  authority on that.
- **Snooze dims but never clears.** A snoozed badge that had forgotten what was
  waiting for you would be worse than one that never slept.

## What an ack actually does

Nothing, on the badge, beyond stopping the edge from asking. The badge never
treats an acknowledgement as "safe" — what it *means* is decided entirely by
whatever is subscribed: a Home Assistant automation, a hook script, a CI job.
Events carry only the type, slot, edge and timestamp; no labels, no messages,
no project names.
