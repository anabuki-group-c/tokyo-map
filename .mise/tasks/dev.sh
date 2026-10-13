#!/usr/bin/env bash
# Start the nearby places API + simple UI on http://127.0.0.1:8000/
set -euo pipefail

cd "$(dirname "$0")/../../nearby_places_api"
exec uv run places serve --host 127.0.0.1 --port 8000
