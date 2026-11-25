"""One server for both APIs and one page that shows the transit map with nearby place search."""
from __future__ import annotations

from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from integrated_api import search

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


PLACE_KINDS = {"restaurant": "飲食店", "tourism": "観光地"}


def named_places(store, names, query):
    """Stations and bus stops, then places, whose name contains the query."""
    try:
        found = store.named(query)
    except (places.DataNotReady, places.DataOutdated, places.DataInvalid):
        found = []  # Stations still help when the places database is missing.
    return search.search(names, query) + [
        {"name": place["name"], "detail": place["address"] or PLACE_KINDS.get(place["category"], ""), "lat": place["lat"], "lon": place["lon"]}
        for place in found]


def make_handler(places_handler, transit_handler, store=None, names=()):
    # Responses go through the places handler's respond() (no-store for /api/, local CORS) for both APIs.
    class Handler(places_handler):
        def do_GET(self):
            url = urlsplit(self.path)
            path = url.path
            if path == "/api/search":
                query = (parse_qs(url.query).get("q") or [""])[0].strip()
                if not 1 <= len(query) <= 50:
                    return self.respond_json(400, {"error": "q（地名）は1〜50文字で指定してください。"})
                return self.respond_json(200, {"results": named_places(store, names, query)})
            if path in STATIC_FILES:
                filename, content_type = STATIC_FILES[path]
                return self.respond(200, (STATIC / filename).read_bytes(), content_type)
            if path.startswith("/api/v1/places/"):
                return places_handler.do_GET(self)
            return transit_handler.do_GET(self)
    return Handler


def serve(host="127.0.0.1", port=8000, database: Path = places.DATABASE):
    transit_handler, services = transit.build_handler()
    store = places.PlaceStore(database)
    handler = make_handler(places.make_handler(store), transit_handler, store, search.load(transit.ROOT))
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Transit map + nearby places: http://{host}:{port}/ (Ctrl+C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for service in services:
            service.stop.set()
        server.server_close()
