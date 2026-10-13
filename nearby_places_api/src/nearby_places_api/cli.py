"""Command-line entry point for the nearby places API."""

from __future__ import annotations

import argparse
from pathlib import Path

from nearby_places_api.boundary import load_tokyo_boundary
from nearby_places_api.store import import_places
from nearby_places_api.web import DATABASE


def main() -> None:
    parser = argparse.ArgumentParser(description="Search restaurants and tourism places near a point in Tokyo")
    subcommands = parser.add_subparsers(dest="command", required=True)
    import_parser = subcommands.add_parser("import", help="Import Overture Places GeoJSON/GeoJSONSeq inside Tokyo")
    import_parser.add_argument("paths", type=Path, nargs="+", help="Places files downloaded with the overturemaps CLI")
    import_parser.add_argument("--boundary", type=Path, required=True,
                               help="Overture division_area file containing Tokyo (region JP-13); places outside it are dropped")
    web_parser = subcommands.add_parser("serve", help="Start the nearby places API")
    web_parser.add_argument("--host", default="127.0.0.1")
    web_parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.command == "serve":
        from nearby_places_api.web import serve
        serve(args.host, args.port)
    elif args.command == "import":
        counts = import_places(args.paths, DATABASE, load_tokyo_boundary(args.boundary))
        print(f"Read {counts['read']} places: {counts['restaurant']} restaurant, {counts['tourism']} tourism, "
              f"{counts['skipped']} skipped, {counts['outside']} outside Tokyo, {counts['duplicates']} duplicates -> {DATABASE}")
