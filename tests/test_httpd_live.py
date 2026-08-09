"""The server end to end, over a real socket, on the desktop.

Worth having and worth reading. The badge's other hardware-facing paths -- the
LED ring, the touch pads -- can only be proved on hardware, and this project has
paid for that repeatedly. The HTTP door is different: CPython has the same
asyncio.start_server the badge does, so the read loop, the bounds, the token and
every status code can be exercised here before a badge ever sees them.

What is still not proved here: MicroPython's asyncio is not CPython's, and the
badge has 2 MB of RAM. This catches logic, not the platform.
"""

import asyncio
import socket
import unittest

from edgewise import httpd


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def fetch(port, request, read_all=True):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(request.encode() if isinstance(request, str) else request)
    await writer.drain()
    data = await reader.read(-1) if read_all else await reader.read(64)
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:  # noqa: BLE001
        pass
    return data.decode(errors="replace")


class ServerCase(unittest.IsolatedAsyncioTestCase):
    TOKEN = "abc12345"

    async def asyncSetUp(self):
        self.seen = []

        async def handler(kind, name, payload):
            self.seen.append((kind, name, payload))
            if kind == httpd.KIND_HEALTH:
                return (200, '{"ok":true}')
            return (200, '{"done":true}')

        self.port = free_port()
        self.server = httpd.Server(self.port, self.TOKEN, handler)
        await self.server.start()

    async def asyncTearDown(self):
        await self.server.stop()

    def get(self, path, headers=""):
        return "GET %s HTTP/1.1\r\nHost: badge\r\n%s\r\n" % (path, headers)


class TestHappyPath(ServerCase):
    async def test_health_needs_no_token(self):
        raw = await fetch(self.port, self.get("/health"))
        self.assertIn("200 OK", raw)
        self.assertIn('"ok":true', raw)

    async def test_a_slot_with_the_token_in_a_header(self):
        raw = await fetch(self.port, self.get(
            "/slot/build?state=done", "X-Edgewise-Token: %s\r\n" % self.TOKEN))
        self.assertIn("200 OK", raw)
        self.assertEqual(self.seen[-1][0], httpd.KIND_SLOT)
        self.assertEqual(self.seen[-1][1], "build")

    async def test_the_token_may_come_in_the_query(self):
        # Because plenty of things that can fetch a URL cannot set a header.
        raw = await fetch(self.port, self.get(
            "/slot/build?state=done&token=" + self.TOKEN))
        self.assertIn("200 OK", raw)

    async def test_a_post_with_a_body_is_answered(self):
        body = "ignored=1"
        raw = await fetch(self.port,
                          "POST /slot/x?state=info&token=%s HTTP/1.1\r\n"
                          "Content-Length: %d\r\n\r\n%s"
                          % (self.TOKEN, len(body), body))
        self.assertIn("200 OK", raw)


class TestRefusals(ServerCase):
    async def test_no_token_is_401(self):
        raw = await fetch(self.port, self.get("/slot/build?state=done"))
        self.assertIn("401", raw)
        self.assertEqual(self.seen, [], "handler ran before the token check")

    async def test_a_wrong_token_is_401(self):
        raw = await fetch(self.port, self.get("/slot/x?state=done&token=nope1234"))
        self.assertIn("401", raw)

    async def test_an_unknown_endpoint_is_404(self):
        raw = await fetch(self.port, self.get("/nope?token=" + self.TOKEN))
        self.assertIn("404", raw)

    async def test_an_unsupported_method_is_400(self):
        raw = await fetch(self.port, "DELETE /slot/x HTTP/1.1\r\n\r\n")
        self.assertIn("400", raw)

    async def test_an_oversize_body_is_refused_not_buffered(self):
        raw = await fetch(self.port,
                          "POST /slot/x?state=info&token=%s HTTP/1.1\r\n"
                          "Content-Length: %d\r\n\r\n"
                          % (self.TOKEN, httpd.MAX_BODY * 100))
        self.assertIn("413", raw)
        self.assertEqual(self.seen, [])

    async def test_an_enormous_request_line_is_refused(self):
        raw = await fetch(self.port, self.get("/slot/x?a=" + "z" * 4000))
        self.assertIn("400", raw)

    async def test_garbage_does_not_take_the_server_down(self):
        for junk in (b"\x00\xff\xfe", b"\r\n\r\n", b"GET", b"?" * 300):
            try:
                await fetch(self.port, junk)
            except Exception:  # noqa: BLE001 - a hang-up is acceptable
                pass
        # Still answering afterwards, which is the whole point.
        raw = await fetch(self.port, self.get("/health"))
        self.assertIn("200 OK", raw)


class TestBounds(ServerCase):
    async def test_the_connection_ceiling_refuses_rather_than_queues(self):
        # Hold MAX_CONNECTIONS open by never finishing the request line, then
        # check the next caller is told to go away instead of waiting.
        held = []
        for _ in range(httpd.MAX_CONNECTIONS):
            reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
            writer.write(b"GET /health HTT")      # deliberately unfinished
            await writer.drain()
            held.append((reader, writer))
        await asyncio.sleep(0.2)
        raw = await fetch(self.port, self.get("/health"))
        self.assertIn("503", raw)
        for _, writer in held:
            writer.close()

    async def test_a_stalled_connection_is_dropped_not_kept(self):
        # A half-open socket must not pin one of four slots for ever.
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        writer.write(b"GET /health HTT")
        await writer.drain()
        await asyncio.sleep(httpd.CONNECTION_TIMEOUT_S + 1)
        raw = await fetch(self.port, self.get("/health"))
        self.assertIn("200 OK", raw)
        writer.close()


class TestRateLimit(ServerCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        from edgewise import security

        # The same limiter class MQTT uses, so HTTP cannot be a way around the
        # flood protection the radio is subject to.
        self.server.limiter = security.RateLimiter(rate=1, burst=2)

    async def test_a_flood_is_told_to_slow_down(self):
        codes = []
        for _ in range(6):
            raw = await fetch(self.port, self.get(
                "/slot/x?state=info&token=" + self.TOKEN))
            codes.append("429" if "429" in raw else "200")
        self.assertIn("429", codes)

    async def test_health_is_not_rate_limited(self):
        # It is how you find the badge, and it costs nothing to answer.
        for _ in range(8):
            raw = await fetch(self.port, self.get("/health"))
            self.assertIn("200 OK", raw)


if __name__ == "__main__":
    unittest.main()
