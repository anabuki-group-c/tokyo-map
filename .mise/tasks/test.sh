#!/usr/bin/env bash
# Run unit tests
set -euo pipefail

cd "$(dirname "$0")/../../nearby_places_api"
PYTHONPATH=src uv run python -m unittest discover -s tests -v
