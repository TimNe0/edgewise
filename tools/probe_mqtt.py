"""On-badge probe for the MQTT questions the desktop cannot answer (V-1).

Run it on a real badge, not in the simulator:

    python -m mpremote connect COM<n> run tools/probe_mqtt.py

Edit HOST below first, and have a broker reachable from the badge's network.

Everything here is a question whose answer changes the design, so each prints an
explicit PASS or FAIL rather than just a value to squint at. **Probe 4 is the
go/no-go**: if a `umqtt` socket cannot be driven from a worker thread on this
firmware, the whole link has to fall back to polled mode, and it is far cheaper
to learn that now than after the app is built around the worker.
"""

HOST = "192.168.1.10"      # <-- your broker, reachable from the badge
PORT = 1883
TOPIC = b"edgewise/probe/test"

import gc
import time


def line(name, ok, detail=""):
    print("%-28s %s %s" % (name, "PASS" if ok else "FAIL", detail))
    return ok


def probe_1_import():
    print("\n-- 1. which client is frozen in this firmware --")
    ok = False
    try:
        from umqtt.simple import MQTTClient  # noqa: F401

        ok = line("umqtt.simple", True)
    except ImportError as exc:
        line("umqtt.simple", False, str(exc))
    try:
        import umqtt.robust  # noqa: F401

        line("umqtt.robust", True, "(fallback available)")
    except ImportError:
        line("umqtt.robust", False, "(not frozen; fine, we do not use it)")
    return ok


def probe_2_constructor():
    print("\n-- 2. constructor arguments the link depends on --")
    from umqtt.simple import MQTTClient

    try:
        client = MQTTClient("probe", HOST, port=PORT, user=None, password=None,
                            keepalive=60, ssl=False)
        line("keepalive + ssl kwargs", True)
    except TypeError as exc:
        return line("keepalive + ssl kwargs", False, str(exc))
    for method in ("set_last_will", "check_msg", "ping", "publish", "subscribe"):
        line("has %s()" % method, hasattr(client, method))
    return True


def probe_3_session():
    """Connect, retained publish, LWT registration, and a round trip."""
    print("\n-- 3. session: connect, retained, will, round trip --")
    from umqtt.simple import MQTTClient

    got = []

    client = MQTTClient("edgewise-probe", HOST, port=PORT, keepalive=60)
    client.set_callback(lambda t, m: got.append((t, m)))
    client.set_last_will(b"edgewise/probe/availability", b"offline",
                         retain=True, qos=0)
    start = time.ticks_ms()
    try:
        client.connect()
    except Exception as exc:  # noqa: BLE001
        return line("connect", False, str(exc))
    line("connect", True, "%d ms" % time.ticks_diff(time.ticks_ms(), start))

    client.subscribe(TOPIC)
    client.publish(TOPIC, b'{"state":"working"}', retain=True, qos=0)

    deadline = time.ticks_add(time.ticks_ms(), 3000)
    while not got and time.ticks_diff(deadline, time.ticks_ms()) > 0:
        client.check_msg()
        time.sleep_ms(20)
    line("retained round trip", bool(got), repr(got[:1]))

    # How long does check_msg() take when there is nothing to read? This is the
    # number that decides whether polled mode is even survivable.
    worst = 0
    for _ in range(50):
        t0 = time.ticks_us()
        client.check_msg()
        worst = max(worst, time.ticks_diff(time.ticks_us(), t0))
    line("idle check_msg < 5 ms", worst < 5000, "worst %d us" % worst)

    # The reason everything publishes at QoS 0: check_msg() leaves the socket
    # non-blocking, and a qos=1 publish then has no way to wait for its PUBACK.
    try:
        client.publish(TOPIC, b"x", qos=1)
        line("qos=1 after check_msg", True, "(worked; QoS 0 still preferred)")
    except Exception as exc:  # noqa: BLE001
        line("qos=1 after check_msg", False, "%s <- expected; QoS 0 is correct" % exc)

    client.publish(TOPIC, b"", retain=True, qos=0)
    client.disconnect()
    return True


def probe_4_thread():
    """THE GO/NO-GO: can a umqtt socket be driven from a worker thread?

    Some MicroPython builds have thread-unsafe lwIP bindings. If this fails,
    the link must run in polled mode and the app has to tolerate a stall on
    every reconnect.
    """
    print("\n-- 4. GO/NO-GO: umqtt socket from a _thread worker --")
    try:
        import _thread
    except ImportError as exc:
        return line("_thread available", False, str(exc))
    line("_thread available", True)

    from umqtt.simple import MQTTClient

    result = {"ok": None, "err": ""}

    def worker():
        try:
            client = MQTTClient("edgewise-probe-thr", HOST, port=PORT, keepalive=60)
            client.connect()
            client.subscribe(TOPIC)
            for _ in range(100):
                client.check_msg()
                time.sleep_ms(20)
            client.disconnect()
            result["ok"] = True
        except Exception as exc:  # noqa: BLE001
            result["err"] = str(exc)
            result["ok"] = False

    # Count main-loop iterations while the worker runs. If the socket work
    # blocks the interpreter, this number collapses -- which is the whole
    # question, since the app renders at 20 Hz.
    _thread.start_new_thread(worker, ())
    ticks = 0
    worst_gap = 0
    last = time.ticks_ms()
    deadline = time.ticks_add(last, 3000)
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        now = time.ticks_ms()
        worst_gap = max(worst_gap, time.ticks_diff(now, last))
        last = now
        ticks += 1
        time.sleep_ms(50)

    fps = ticks / 3.0
    line("worker connected", result["ok"] is True, result["err"])
    # 18 fps sustained is the bar: the ring runs at 20 Hz and a few dropped
    # frames are invisible, but a stall is not.
    line("main loop >= 18 fps", fps >= 18, "%.1f fps" % fps)
    return line("worst main-loop gap < 300 ms", worst_gap < 300, "%d ms" % worst_gap)


def probe_5_tls():
    """V-4: is TLS affordable, with the app's own allocations already made?"""
    print("\n-- 5. TLS and free memory --")
    gc.collect()
    before = gc.mem_free()
    print("free before: %d bytes" % before)
    from umqtt.simple import MQTTClient

    try:
        client = MQTTClient("edgewise-probe-tls", HOST, port=8883, keepalive=60,
                            ssl=True)
        client.connect()
        gc.collect()
        after = gc.mem_free()
        line("TLS handshake", True, "free after: %d" % after)
        line("free >= 200 kB after TLS", after >= 200000, "%d" % after)
        client.disconnect()
    except Exception as exc:  # noqa: BLE001
        line("TLS handshake", False, "%s (auth-only + LAN broker instead)" % exc)


def main():
    print("edgewise MQTT probe -> %s:%d" % (HOST, PORT))
    print("ring LEDs reported:", _ring_len())
    print("frontboard:", _frontboard())
    if not probe_1_import():
        print("\nno MQTT client on this firmware; nothing else can run")
        return
    probe_2_constructor()
    probe_3_session()
    probe_4_thread()
    probe_5_tls()
    print("\nprobe 4 is the one that decides the architecture.")


def _ring_len():
    try:
        from tildagonos import tildagonos

        return len(tildagonos.leds)
    except Exception as exc:  # noqa: BLE001
        return "unknown (%s)" % exc


def _frontboard():
    try:
        from frontboards.utils import detect_frontboard

        return hex(detect_frontboard())
    except Exception as exc:  # noqa: BLE001
        return "unknown (%s)" % exc


main()
