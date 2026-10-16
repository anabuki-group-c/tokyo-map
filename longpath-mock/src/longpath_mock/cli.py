"""最長乗車経路実験の起動コマンド。"""

from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")

    parser = argparse.ArgumentParser(description="最長乗車経路実験（/route）")
    subcommands = parser.add_subparsers(dest="command", required=True)
    web_parser = subcommands.add_parser("serve", help="Start the /route experiment server")
    web_parser.add_argument("--host", default="127.0.0.1")
    web_parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.command == "serve":
        from longpath_mock.web import serve
        serve(args.host, args.port)


if __name__ == "__main__":
    main()
