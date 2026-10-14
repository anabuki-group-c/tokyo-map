"""Command-line entry point for the integrated server."""

from __future__ import annotations

import argparse

from dotenv import load_dotenv
from transit_experiment.cli import PROJECT_ROOT as TRANSIT_ROOT


def main() -> None:
    # The ODPT keys stay in transit_experiment/.env; there is no separate copy here.
    load_dotenv(TRANSIT_ROOT / ".env")
    parser = argparse.ArgumentParser(description="Serve the nearby places API and the transit map API together")
    subcommands = parser.add_subparsers(dest="command", required=True)
    web_parser = subcommands.add_parser("serve", help="Start both APIs on one port")
    web_parser.add_argument("--host", default="127.0.0.1")
    web_parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.command == "serve":
        from integrated_api.web import serve
        serve(args.host, args.port)
