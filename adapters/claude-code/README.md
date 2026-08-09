# Claude Code

**Walk away from the machine and let the badge call you back.** One edge per
checkout: amber while it is working, cyan and flashing when it wants you, green
when it is done, dark when the session ends.

That is the point of it. An agent that runs for ten minutes is an agent you
stop watching, and a terminal notification only reaches you at the terminal.

It works for every future session without anything having to remember it: the
hooks live in a settings file and *the harness* runs them, not the model.

This is one adapter among several — the badge has no idea what a coding agent
is, and the same four states come from CI, cron and a 3D printer elsewhere in
this directory.

## Install

Set up [the shell adapter](../shell/README.md) first — this one publishes
through it. Then:

```sh
./install-hooks.sh          # this project only
./install-hooks.sh --user   # every project
./install-hooks.sh --user --yes   # no confirmation prompt
```

`--yes` removes the question, not the evidence: it still prints the diff it is
about to write and still keeps a backup. Use it when something else is driving
the install.

It prints the exact JSON it will write, asks, and keeps a backup at
`settings.json.edgewise-backup`. Your other settings and your other hooks are
preserved; running it twice changes nothing the second time; and
`./install-hooks.sh --uninstall` removes exactly what it added.

It needs Python for the merge. Without it, paste [hooks.json](hooks.json) in by
hand and replace `__EDGEWISE_HOOK__` with the absolute path to
`edgewise-hook.sh`.

**On Windows**, `python3` may resolve to the Microsoft Store's app execution
alias, which exists, runs, and prints an advert instead of executing anything.
The installer asks each candidate interpreter to execute something before
believing in it, so this is handled — but it is why `command -v python3` is not
enough if you are writing something similar.

**The hooks embed an absolute path to this checkout.** Move the repo and
re-run the installer.

**On Windows the installer writes the command differently**, and it has to.
The editor runs hooks through `cmd`, which has no association for a `.sh` file:
it exits 0, prints nothing, and does nothing — so the hook looks installed and
fires into a void. The command therefore names an interpreter explicitly:

```
"C:/Program Files/Git/bin/bash.exe" "C:/path/to/edgewise-hook.sh" done
```

That needs Git for Windows. And because a hook does not inherit the `PATH` of
the shell you installed it from, `edgewise-pub.sh` looks for `mosquitto_pub` in
the usual places rather than trusting `PATH` — set `EDGEWISE_MOSQUITTO` if
yours lives somewhere unusual.

To check a hook really fires, run the command exactly as `settings.json` spells
it, from a shell the editor would use — not from Git Bash, which is the one
environment where both of these problems disappear.

Nothing here needs sudo, and nothing downloads anything. If a future version of
this script grows a `curl`, do not run it.

## What maps to what

| Hook event | Badge |
|---|---|
| `UserPromptSubmit` | `working` — amber, slow breathe |
| `Notification` | `needs_you` — cyan flash, with the notification text as the message |
| `Stop` | `done` — green |
| `SessionEnd` | slot cleared, edge fades out |

`Notification` fires when Claude Code itself raises one — a permission prompt,
or an idle wait. It does not fire for every question an agent might ask, so if
you want the badge lit whenever you are blocked, publish it directly:

```sh
../shell/edgewise-pub.sh "$(basename "$PWD")" needs_you "waiting on a decision"
```

The slot name is the basename of `CLAUDE_PROJECT_DIR` (falling back to the
session's `cwd`), lowercased and truncated to 16 characters. Two checkouts get
two edges. Two sessions in one checkout share an edge, which is usually what you
want — you care about the project, not the terminal.

Tap the edge to acknowledge. That publishes an `ack` event and stops the edge
flashing; it does not tell Claude Code anything, unless you turn on the approve
flow below.

## More than one badge

`EDGEWISE_ID` takes a list, and every publish reaches all of them — a badge on
the desk and another in the workshop show the same board:

```sh
EDGEWISE_ID="AAAAAAAAAAAAAAAAAAAAAAAAAA BBBBBBBBBBBBBBBBBBBBBBBBBB"
```

## Privacy

Slot names are directory names, which are usually project names, and on a shared
broker **the topic leaks them** — this is not just about the label. Set
`EDGEWISE_LABELS=hash` in `~/.config/edgewise/env` and the name becomes a
6-character digest everywhere, with no label and no message published at all.
Your board still tells you which edge is which; a stranger watching the broker
learns nothing. Use it for screenshots and any broker you do not own.

## Approve from the badge (advanced, off by default)

`edgewise-approve.sh` is a `PreToolUse` hook that puts the requested action on
an edge and waits for you to tap or hold it. Tap approves, a 0.6 s hold denies,
and nothing at all means the normal terminal prompt appears after the timeout.

**This turns an MQTT message into permission to run a command on your machine.**
Read [docs/security.md](../../docs/security.md) before enabling it. The script
refuses to run unless the broker is authenticated or TLS, because on an open
broker anyone who knows your device ID can publish a fake `ack` and this hook
would believe it. That gate is not overridable by an environment variable, on
purpose.

Enable it by adding to `~/.config/edgewise/env`:

```sh
EDGEWISE_APPROVE=1
EDGEWISE_APPROVE_MODE=prompt      # start here: the badge wakes you, you still
                                  # confirm in the terminal
EDGEWISE_APPROVE_TIMEOUT=60
```

and adding the hook to `.claude/settings.json` (the installer deliberately does
not do this for you):

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          { "type": "command",
            "command": "/absolute/path/to/adapters/claude-code/edgewise-approve.sh",
            "timeout": 90 }
        ]
      }
    ]
  }
}
```

Set the hook `timeout` above `EDGEWISE_APPROVE_TIMEOUT`, or the hook is killed
before the badge can answer.

Once you trust it, `EDGEWISE_APPROVE_MODE=allow` makes a tap approve outright.

Every failure path — no broker, no answer, no `jq`, an unparseable event —
exits 0 with no decision, so a badge that is switched off or out of range is
indistinguishable from not having installed this. The failure mode is "you
approve things by hand", never "things approve themselves".

## Troubleshooting

**Nothing lights up.** Run `../shell/edgewise-pub.sh --check`. A wrong device ID
cannot fail loudly: the broker accepts a publish to a topic nobody is listening
to.

**The edge stays amber after a session ends.** `SessionEnd` does not fire if the
terminal is killed outright. The slot expires on its TTL (an hour by default),
or clear it now with `../shell/edgewise-pub.sh --clear <name>`.

**`bad interpreter: no such file or directory`.** The scripts got CRLF line
endings, usually from a Windows checkout. `.gitattributes` pins them to LF; if
you edited one, fix it with `sed -i 's/\r$//'`.

**The hook seems slow.** Publishes are capped at five seconds where `timeout(1)`
exists, and the hook entries carry a 5 s timeout of their own. If your broker is
unreachable you will feel that once per prompt — point `EDGEWISE_BROKER` at
something real, or uninstall.
