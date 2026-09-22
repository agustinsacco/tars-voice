import asyncio
import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from gateway.relay import HttpTarsRelay


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.headers.get("Authorization") != "Bearer " + "x" * 32:
            self.send_response(401)
            self.end_headers()
            return
        length = int(self.headers.get("content-length", "0"))
        payload = json.loads(self.rfile.read(length))
        assert payload["text"] == "hello"
        self.send_response(200)
        self.send_header("content-type", "application/x-ndjson")
        self.end_headers()
        for event in ({"type": "accepted"}, {"type": "answer", "text": "Hello."}, {"type": "done"}):
            self.wfile.write((json.dumps(event) + "\n").encode())
            self.wfile.flush()
            time.sleep(0.01)

    def log_message(self, *_):
        pass


class RelayContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_real_adapter_streams_ndjson(self):
        async def run():
            relay = HttpTarsRelay(f"http://127.0.0.1:{self.server.server_port}/", "x" * 32)
            return [event async for event in relay.run("hello", "session")]

        events = asyncio.run(run())
        self.assertEqual([event.type for event in events], ["accepted", "answer", "done"])
        self.assertEqual(events[1].text, "Hello.")


if __name__ == "__main__":
    unittest.main()
