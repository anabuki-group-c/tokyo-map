"""Command-line entry point for the transit experiment."""

from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import load_dotenv

from transit_experiment.n02 import import_tokyo_railways
from transit_experiment.odpt import fetch_tokyo_metro_train_information

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"


def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")

    parser = argparse.ArgumentParser(description="Fetch Tokyo transit data")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("import-static-railways", help="Download N02 and write JR East/Tokyo Metro GeoJSON")
    subcommands.add_parser("fetch-tokyo-metro-status", help="Fetch current ODPT Tokyo Metro notices")
    jr_parser = subcommands.add_parser("import-jr-gtfs", help="Import a licensed local JR GTFS ZIP; restart the server afterwards")
    jr_parser.add_argument("path", type=Path, help="GTFS ZIP you are authorized to use")
    web_parser = subcommands.add_parser("serve", help="Show the animated Tokyo Metro web map")
    web_parser.add_argument("--host", default="127.0.0.1")
    web_parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.command == "serve":
        from transit_experiment.web import serve
        serve(args.host, args.port)
    elif args.command == "import-jr-gtfs":
        from transit_experiment.gtfs import import_feed
        import_feed(args.path, PROCESSED_DIR / "jr-gtfs.sqlite")
        print("JR GTFS imported. Restart the web server to replace the headway model with this timetable.")
    elif args.command == "import-static-railways":
        counts = import_tokyo_railways(RAW_DIR, PROCESSED_DIR)
        print("Static railway import completed:")
        for name, count in counts.items():
            print(f"  {name}: {count} features")
    elif args.command == "fetch-tokyo-metro-status":
        count = fetch_tokyo_metro_train_information(
            PROCESSED_DIR / "realtime" / "tokyo-metro-train-information.json"
        )
        print(f"Fetched {count} Tokyo Metro train-information notice(s).")


if __name__ == "__main__":
    main()
