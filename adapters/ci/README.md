# CI

An edge that is amber while the build runs, green when it passes and red when it
breaks. One line at the start, one at the end.

## The one-liner

Any CI system, any language, no dependencies beyond `mosquitto_pub`:

```sh
mosquitto_pub -h "$EDGEWISE_BROKER" -t "edgewise/$EDGEWISE_ID/slot/build" -r \
  -m '{"state":"working","label":"build","ttl":3600}'
```

and at the end, with the job's status:

```sh
mosquitto_pub -h "$EDGEWISE_BROKER" -t "edgewise/$EDGEWISE_ID/slot/build" -r \
  -m '{"state":"done","label":"build"}'
```

`-r` is not optional. The badge rebuilds the whole board from retained messages
when it reconnects, so a build status published without it vanishes on the next
badge reboot.

If the CI machine has this repository checked out, use the wrapper instead and
get the state transitions for free:

```sh
adapters/shell/run-and-report build -- make test
```

## First, the awkward question: can your runner reach your broker?

A GitHub-hosted runner is on the public internet and your broker is probably on
your desk. It cannot reach it, and nothing below changes that. Your options, in
the order worth trying:

1. **A self-hosted runner** on the same network. Simplest, and the one most
   people already have if they care about a desk status board.
2. **A broker reachable from the internet**, with TLS and a password, and a
   credential scoped to publishing under one topic. Read
   [docs/security.md](../../docs/security.md) first: your slot labels are your
   project names.
3. **A public broker with `EDGEWISE_LABELS=hash`**, so the topic and the label
   are a 6-character digest. Your board still tells you which edge is the build;
   nobody watching the broker learns what you are building.

Do not put a broker password in a workflow file. Use your CI's secret store —
the example below does.

## GitHub Actions

[github-actions.yml](github-actions.yml) is a complete workflow. The parts that
matter:

```yaml
      - name: Tell the badge the build started
        run: |
          mosquitto_pub -h "$BROKER" -p "$PORT" -u "$USER" -P "$PASS" \
            -t "edgewise/$ID/slot/${{ github.event.repository.name }}" -r \
            -m '{"state":"working","label":"${{ github.event.repository.name }}","ttl":7200}'
```

and, crucially, `if: always()` on the reporting step — a step that only runs on
success leaves the edge amber forever on a failure, which is precisely backwards
from what you want a status board to do.

## The states worth using

| CI moment | State | Looks like |
|---|---|---|
| job started | `working` | amber, slow breathe |
| passed | `done` | green, solid |
| failed | `error` | red, double-blink then solid |
| awaiting a manual approval or a deploy gate | `needs_you` | cyan, flashing — and tapping it publishes an `ack` you can act on |
| nightly finished, nothing to say | `info` | white pulse, then dim |

Use `needs_you` sparingly. It is the only state that flashes, and a board where
everything flashes is a board nobody looks at.

## TTL

Set `ttl` to a bit longer than the job's timeout. The edge fades out on its own
if the runner is killed mid-job, which is the difference between a stale amber
edge you learn to ignore and a board you can trust.

## Reacting to a tap

The gate-approval case is the interesting one: publish `needs_you`, then have
the job wait on the badge.

```sh
mosquitto_sub -h "$EDGEWISE_BROKER" -t "edgewise/$EDGEWISE_ID/event" -C 1 -W 300 \
  | grep -q '"type":"ack"' && echo approved
```

Two warnings, both real. A dropped `ack` is possible — everything is QoS 0
because the on-badge client cannot do QoS 1 without blocking — so treat a
timeout as "no answer", never as a decision. And on an unauthenticated broker
anyone who knows your device ID can publish that `ack`. If a tap can deploy
something, the broker needs a password.
