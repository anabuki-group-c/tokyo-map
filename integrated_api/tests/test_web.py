import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import urlopen

from integrated_api.web import make_handler


def fake_handler(name):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.respond(200, f"{name} {self.path}".encode(), "text/plain")

        def respond(self, status, body, content_type):
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            pass
    return Handler


class RoutingTests(unittest.TestCase):
    def test_paths_go_to_the_right_api(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(fake_handler("places"), fake_handler("transit")))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        for path, expected in [
            ("/api/v1/places/nearby?lat=1", "places /api/v1/places/nearby?lat=1"),
            ("/app.js", "transit /app.js"),
            ("/api/estimates", "transit /api/estimates"),
        ]:
            with urlopen(f"http://127.0.0.1:{server.server_port}{path}") as response:
                self.assertEqual(response.read().decode(), expected)
        # The merged page is served by this package itself and loads the places script.
        with urlopen(f"http://127.0.0.1:{server.server_port}/") as response:
            self.assertIn('src="/places.js', response.read().decode())


if __name__ == "__main__":
    unittest.main()
