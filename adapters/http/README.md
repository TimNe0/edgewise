# HTTP

A front door for everything that cannot speak MQTT.

Webhooks, phone shortcuts, a browser bookmark, `curl` in a Makefile, a Grafana
alert, a router's "new device joined" action — none of those can publish MQTT,
and every one of them can fetch a URL.

```sh
pip install paho-mqtt
./edgewise-http.py --token hunter2
```

It reads the same `~/.config/edgewise/env` as [the shell
adapter](../shell/README.md), so set that up first — including several badges
in `EDGEWISE_ID` if you have them.

`--token` can also come from `EDGEWISE_HTTP_TOKEN`, in the environment or in
that same env file, which keeps it out of your shell history and out of the
process list.

| Flag | |
|---|---|
| `--token` | required. Or set `EDGEWISE_HTTP_TOKEN` |
| `--listen` | default `0.0.0.0`. Use `127.0.0.1` to keep it on this machine |
| `--port` | default `8420` |

## Poking it

```sh
T='X-Edgewise-Token: hunter2'

curl -H "$T" "http://desk:8420/slot/build?state=working"
curl -H "$T" "http://desk:8420/slot/build?state=error&msg=3+tests+failed"
curl -H "$T" "http://desk:8420/slot/build?state=clear"

curl -H "$T" "http://desk:8420/text?msg=bins+tonight&level=alert"
curl -H "$T" "http://desk:8420/weather?cond=rain&temp=12&rain=40"

curl "http://desk:8420/health"          # no token needed
```

| Endpoint | Parameters |
|---|---|
| `/slot/<name>` | `state` (required in effect; defaults to `info`), `label`, `msg`, `ttl`, `edge` |
| `/text` | `msg` (required), `level`, `duration` |
| `/weather` | any of `cond`, `temp`, `rain`; plus `unit`, `ttl` |
| `/wait/<name>` | `timeout` seconds, default 300, max 900 |
| `/health` | — |

GET and POST both work, because half the things you will point at this can only
do one of them. The token goes in the `X-Edgewise-Token` header, or `?token=`
if whatever is calling cannot set headers.

Unlike the badge, this is **strict**: a misspelled state gets a 400 telling you
the valid ones. The badge ignores malformed input because it is listening to an
untrusted radio; you are at a command line and would rather be told.

## Waiting for a tap

`/wait/<slot>` holds the request open until you acknowledge or deny that slot on
the badge:

```sh
curl -H "$T" "http://desk:8420/slot/deploy?state=needs_you&msg=ship+v2"
curl -H "$T" --max-time 310 "http://desk:8420/wait/deploy"
```

```json
{"type":"ack","slot":"deploy","edge":0,"ts":1786279930}
```

`ack` and `deny` both return 200 with the event; a timeout returns **408**, not
a 200 with nothing in it — a caller that treats "no answer" as approval is a
caller that approves every time the badge is switched off.

`snooze` and `wake` carry no slot and release nobody.

That makes the badge an approval gate for anything that can call a URL:

```sh
if curl -fsS -H "$T" --max-time 310 "http://desk:8420/wait/deploy" \
     | grep -q '"type":"ack"'; then
    ./deploy.sh
fi
```

## What the token is, and is not

It stops the rest of your network lighting your badge by accident. That is all
it is for.

It is sent in the clear over plain HTTP, so it is no protection against anyone
who can watch your LAN, and it is not a reason to put this on the internet. The
bridge refuses to start without one, because "it is only my LAN" is how a thing
ends up on a guest network.

**`/wait` deserves a second thought.** It turns a tap on a badge into an exit
code, and a script that deploys on exit 0 has turned a tap into an
authorisation. The badge does not know who pressed it, and on an
unauthenticated broker anyone who knows your device ID can publish a fake
`ack`. [docs/security.md](../../docs/security.md) has the whole picture; the
short version is that an ack is an observation, and what it *means* is decided
by whatever is subscribed — here, by you.

## Why the badge does not do this itself

It has no HTTP client, and non-MQTT transports are an explicit non-goal
(`EDGEWISE_SPEC.md` §1). A badge that terminates connections is a badge with an
attack surface, on hardware with 2 MB of RAM and a 3 Hz safety cap to enforce.
This runs on a machine that can afford to be careless.
