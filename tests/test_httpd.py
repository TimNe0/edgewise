"""The badge's own HTTP door: parsing, routing, bounds and the token.

Everything here runs without a socket, the same way `prefs` is testable without
a dialog. What is left on the badge is `asyncio.start_server` and a read loop.

The property that matters most is not tested in this file at all: routes build
a dict and hand it to `security.parse_*`, so HTTP inherits the same limits, the
same hostile-input corpus and the same structural caps as MQTT. `TestOneDoor`
below asserts that the handover happens; `test_security.py` is what proves the
validation behind it. A transport with its own idea of the limits would be a
second set of bugs, findable only on whichever door an attacker chose.
"""

import unittest

from edgewise import httpd, security


def line(text):
    return httpd.parse_request_line(text)


class TestRequestLine(unittest.TestCase):
    def test_a_plain_get(self):
        method, path, query = line("GET /slot/build?state=done HTTP/1.1")
        self.assertEqual((method, path), ("GET", "/slot/build"))
        self.assertEqual(query["state"], "done")

    def test_post_and_put_are_allowed(self):
        # Half the things that will point at this can only do one of them.
        for method in ("POST", "PUT"):
            self.assertEqual(line("%s /text?msg=hi HTTP/1.1" % method)[0], method)

    def test_other_methods_are_refused(self):
        for method in ("DELETE", "OPTIONS", "TRACE", "CONNECT", "PATCH"):
            self.assertIsNone(line("%s / HTTP/1.1" % method)[0], method)

    def test_an_overlong_request_line_is_refused_before_parsing(self):
        long_line = "GET /slot/x?a=%s HTTP/1.1" % ("z" * httpd.MAX_REQUEST_LINE)
        self.assertIsNone(line(long_line)[0])

    def test_rubbish_never_raises(self):
        for text in ("", "GET", "GET x", "/only/a/path", "\x00\xff",
                     "GET  HTTP/1.1", "G" * 600):
            try:
                line(text)
            except Exception as exc:  # noqa: BLE001
                self.fail("%r raised %s" % (text[:20], exc))

    def test_percent_and_plus_decoding(self):
        _, path, query = line("GET /slot/x?msg=front%20door+open HTTP/1.1")
        self.assertEqual(query["msg"], "front door open")

    def test_a_broken_escape_is_a_literal_not_an_error(self):
        _, _, query = line("GET /slot/x?msg=100%25%zz HTTP/1.1")
        self.assertIn("%", query["msg"])


class TestHeaders(unittest.TestCase):
    def test_names_are_lower_cased(self):
        headers = httpd.parse_headers(["X-Edgewise-Token: abc", "Host: badge"])
        self.assertEqual(headers["x-edgewise-token"], "abc")

    def test_the_count_is_bounded(self):
        many = ["H%d: v" % i for i in range(httpd.MAX_HEADERS * 4)]
        self.assertLessEqual(len(httpd.parse_headers(many)), httpd.MAX_HEADERS)

    def test_an_enormous_header_is_dropped_not_stored(self):
        headers = httpd.parse_headers(["X: " + "z" * httpd.MAX_HEADER_LINE])
        self.assertEqual(headers, {})


class TestRouting(unittest.TestCase):
    def route(self, path, query=None):
        return httpd.route(path, query or {})

    def test_a_slot(self):
        kind, name, payload = self.route("/slot/build", {"state": "done"})
        self.assertEqual((kind, name), (httpd.KIND_SLOT, "build"))
        self.assertEqual(payload["state"], "done")

    def test_slot_names_are_restricted(self):
        # The name is echoed into an MQTT topic and a JSON string.
        for name in ("a/b", "a b", "a#b", "a+b", 'a"b', "a|b", "x" * 17, ""):
            self.assertIsNone(self.route("/slot/%s" % name,
                                         {"state": "info"})[0], name)

    def test_state_is_not_validated_here(self):
        # Deliberate: `security.parse_slot` owns the enum. Two lists would
        # drift, and the interesting bugs would only show on one door.
        kind, _, payload = self.route("/slot/x", {"state": "exploded"})
        self.assertEqual(kind, httpd.KIND_SLOT)
        self.assertEqual(payload["state"], "exploded")
        self.assertIsNone(security.parse_slot(payload))

    def test_text_needs_a_message(self):
        self.assertIsNone(self.route("/text")[0])
        self.assertEqual(self.route("/text", {"msg": "hi"})[0], httpd.KIND_TEXT)

    def test_weather_needs_at_least_one_field(self):
        self.assertIsNone(self.route("/weather")[0])
        self.assertEqual(self.route("/weather", {"temp": "7"})[0],
                         httpd.KIND_WEATHER)

    def test_led_parses_a_colour(self):
        kind, _, payload = self.route("/led", {"segment": "edge:0",
                                               "rgb": "255,0,80"})
        self.assertEqual(kind, httpd.KIND_LED)
        self.assertEqual(payload["rgb"], (255, 0, 80))

    def test_a_bad_colour_is_omitted_not_invented(self):
        for text in ("1,2", "1,2,3,4", "a,b,c", "999,0,0", ""):
            self.assertIsNone(httpd.parse_rgb(text), text)

    def test_wait_carries_a_bounded_timeout(self):
        _, name, payload = self.route("/wait/deploy", {"timeout": "9999"})
        self.assertEqual(name, "deploy")
        self.assertEqual(payload["timeout"], httpd.DEFAULT_WAIT_S)
        _, _, payload = self.route("/wait/deploy", {"timeout": "30"})
        self.assertEqual(payload["timeout"], 30)

    def test_health_is_the_root_and_named(self):
        for path in ("/", "/health"):
            self.assertEqual(self.route(path)[0], httpd.KIND_HEALTH)

    def test_unknown_endpoints_say_so(self):
        for path in ("/nope", "/slot", "/slot/a/b", "/text/extra", "/wait"):
            self.assertIsNone(self.route(path)[0], path)


class TestOneDoor(unittest.TestCase):
    """The whole design in one place: what routing produces, the MQTT
    validator accepts, and the result is identical to the same message
    arriving over the wire."""

    def test_a_routed_slot_survives_the_mqtt_validator(self):
        _, name, payload = httpd.route("/slot/kiln", {
            "state": "needs_you", "label": "kiln", "msg": "door open?",
            "ttl": "1800"})
        parsed = security.parse_slot(payload)
        self.assertEqual(parsed["state"], "needs_you")
        self.assertEqual(parsed["label"], "kiln")
        self.assertEqual(parsed["ttl"], 1800)

    def test_http_and_mqtt_produce_the_same_slot(self):
        over_http = security.parse_slot(httpd.route("/slot/kiln", {
            "state": "done", "label": "kiln", "ttl": "600"})[2])
        over_mqtt = security.parse_slot(
            b'{"state":"done","label":"kiln","ttl":600}')
        self.assertEqual(over_http, over_mqtt)

    def test_the_caps_apply_to_the_http_path_too(self):
        # Nothing in httpd mentions these numbers; they come from security.
        _, _, payload = httpd.route("/slot/x", {
            "state": "info", "label": "L" * 40, "msg": "M" * 200,
            "ttl": "999999"})
        parsed = security.parse_slot(payload)
        self.assertEqual(len(parsed["label"]), security.LIMIT_LABEL)
        self.assertEqual(len(parsed["msg"]), security.LIMIT_MSG)
        self.assertEqual(parsed["ttl"], 86400)

    def test_hostile_query_values_are_refused_by_the_validator(self):
        for state in ("exploded", "", "../../etc", "needs_you\x00"):
            payload = httpd.route("/slot/x", {"state": state})[2]
            self.assertIsNone(security.parse_slot(payload), state)


class TestToken(unittest.TestCase):
    def test_header_or_query(self):
        self.assertTrue(httpd.token_ok("abc123", {"x-edgewise-token": "abc123"}, {}))
        self.assertTrue(httpd.token_ok("abc123", {}, {"token": "abc123"}))

    def test_wrong_absent_or_empty(self):
        self.assertFalse(httpd.token_ok("abc123", {}, {}))
        self.assertFalse(httpd.token_ok("abc123", {}, {"token": "abc124"}))
        self.assertFalse(httpd.token_ok("abc123", {}, {"token": ""}))
        self.assertFalse(httpd.token_ok("abc123", {}, {"token": "abc1234"}))

    def test_a_badge_with_no_token_accepts_nothing(self):
        # Rather than accepting everything, which is the direction this kind of
        # check fails in when nobody is looking.
        self.assertFalse(httpd.token_ok("", {}, {"token": ""}))
        self.assertFalse(httpd.token_ok(None, {}, {"token": "anything"}))

    def test_health_is_the_only_open_endpoint(self):
        self.assertFalse(httpd.needs_token(httpd.KIND_HEALTH))
        for kind in (httpd.KIND_SLOT, httpd.KIND_TEXT, httpd.KIND_WEATHER,
                     httpd.KIND_LED, httpd.KIND_WAIT):
            self.assertTrue(httpd.needs_token(kind), kind)


class TestResponses(unittest.TestCase):
    def test_a_response_is_well_formed_and_closes(self):
        raw = httpd.response(200, '{"ok":true}').decode()
        self.assertTrue(raw.startswith("HTTP/1.0 200 OK"))
        self.assertIn("Content-Length: 11", raw)
        # Keep-alive would mean holding one of four sockets for a caller who
        # may never come back.
        self.assertIn("Connection: close", raw)

    def test_every_status_used_has_a_reason(self):
        for status in (200, 400, 401, 404, 408, 413, 429, 503):
            self.assertNotIn("Error", httpd.response(status, "{}").decode())

    def test_an_error_body_cannot_carry_anything_odd(self):
        body = httpd.json_error('he said "hi"\n\\ and \x00 left')
        self.assertNotIn('"hi"', body)
        self.assertNotIn("\\", body)
        self.assertNotIn("\x00", body)
        self.assertTrue(body.startswith('{"error":"'))
        self.assertTrue(body.endswith('"}'))


class TestBounds(unittest.TestCase):
    """Each of these is a number a stranger on the network gets to push
    against, on a device with 2 MB of RAM in front of a seizure cap."""

    def test_they_are_small_on_purpose(self):
        self.assertLessEqual(httpd.MAX_CONNECTIONS, 8)
        self.assertLessEqual(httpd.MAX_WAITERS, httpd.MAX_CONNECTIONS)
        self.assertLessEqual(httpd.MAX_BODY, 4096)
        self.assertLessEqual(httpd.MAX_REQUEST_LINE, 1024)
        self.assertLessEqual(httpd.MAX_WAIT_S, 300)
        self.assertLessEqual(httpd.CONNECTION_TIMEOUT_S, 15)

    def test_a_wait_cannot_outlive_its_ceiling(self):
        _, _, payload = httpd.route("/wait/x", {"timeout": str(httpd.MAX_WAIT_S * 10)})
        self.assertLessEqual(payload["timeout"], httpd.MAX_WAIT_S)


if __name__ == "__main__":
    unittest.main()
