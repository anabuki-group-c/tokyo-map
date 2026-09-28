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
    args = parser.parse_args()

    if args.command == "import-static-railways":
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
