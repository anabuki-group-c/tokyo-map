"""One server for both APIs and one page that shows the transit map with nearby place search."""
from __future__ import annotations

from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from nearby_places_api import web as places
from transit_experiment import web as transit

STATIC = Path(__file__).parent / "static"
# The merged page; its other assets (/app.js, /style.css, ...) come from the transit handler.
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/places.js": ("places.js", "text/javascript; charset=utf-8"),
    "/journey.js": ("journey.js", "text/javascript; charset=utf-8"),
    "/places.css": ("places.css", "text/css; charset=utf-8"),
}


def make_handler(places_handler, transit_handler):
    # Responses go through the places handler's respond() (no-store for /api/, local CORS) for both APIs.
    class Handler(places_handler):
        def do_GET(self):
            path = urlsplit(self.path).path
            if path in STATIC_FILES:
                filename, content_type = STATIC_FILES[path]
                return self.respond(200, (STATIC / filename).read_bytes(), content_type)
            if path.startswith("/api/v1/places/"):
                return places_handler.do_GET(self)
            return transit_handler.do_GET(self)
    return Handler


def serve(host="127.0.0.1", port=8000, database: Path = places.DATABASE):
    transit_handler, services = transit.build_handler()
    server = ThreadingHTTPServer((host, port), make_handler(places.make_handler(places.PlaceStore(database)), transit_handler))
    print(f"Transit map + nearby places: http://{host}:{port}/ (Ctrl+C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for service in services:
            service.stop.set()
        server.server_close()
