# Controls

Single source of truth for what every input does. If this table and the code
disagree, the code is wrong.

Both badges run the same gestures. On a 2026 Spaceagon the touch ring drives
them directly; on a 2024 Tildagon you highlight an edge with UP/DOWN and use
CONFIRM. Both feed one recogniser, so the timings are identical.

## Dashboard

| Gesture | 2026 (touch ring) | 2024 (buttons) | Published | Means |
|---|---|---|---|---|
| acknowledge | tap the edge's pads | UP/DOWN to highlight, tap CONFIRM | `{"type":"ack","slot":…,"edge":…}` | acknowledge / approve; the edge stops asking |
| deny | hold the pads (0.6 s) | highlight, hold CONFIRM (0.6 s) | `{"type":"deny", …}` | deny / dismiss |
| detail view | double-tap | highlight, RIGHT | `{"type":"tap", …}` | label, state, message, elapsed, pinned |
| snooze | turn the badge face down | — | `{"type":"snooze"}` / `{"type":"wake"}` | everything dims; nothing is cleared |
| settings | LEFT | LEFT | — | |
| exit | CANCEL | CANCEL | — | clears the highlight first, then minimises |

## Detail view

| Input | Does |
|---|---|
| CANCEL / LEFT | back to the dashboard |

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
- **Snooze dims but never clears.** A snoozed badge that had forgotten what was
  waiting for you would be worse than one that never slept.

## What an ack actually does

Nothing, on the badge, beyond stopping the edge from asking. The badge never
treats an acknowledgement as "safe" — what it *means* is decided entirely by
whatever is subscribed: a Home Assistant automation, a hook script, a CI job.
Events carry only the type, slot, edge and timestamp; no labels, no messages,
no project names.
