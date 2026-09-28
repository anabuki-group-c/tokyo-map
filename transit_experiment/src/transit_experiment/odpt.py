"""Small ODPT client for optional realtime status data."""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TRAIN_INFORMATION_URL = "https://api.odpt.org/api/v4/odpt:TrainInformation"


def fetch_tokyo_metro_train_information(output_path: Path) -> int:
    """Fetch the current Tokyo Metro disruption/operation notices from ODPT."""
    consumer_key = os.environ.get("ODPT_CONSUMER_KEY")
    if not consumer_key:
        raise RuntimeError("ODPT_CONSUMER_KEY must be set to fetch ODPT realtime data")

    query = urllib.parse.urlencode(
        {
            "odpt:operator": "odpt.Operator:TokyoMetro",
            "acl:consumerKey": consumer_key,
        }
    )
    request = urllib.request.Request(f"{TRAIN_INFORMATION_URL}?{query}")
    with urllib.request.urlopen(request, timeout=30) as response:
        notices: list[dict[str, Any]] = json.load(response)

    payload = {
        "fetched_at": datetime.now(UTC).isoformat(),
        "source": TRAIN_INFORMATION_URL,
        "operator": "odpt.Operator:TokyoMetro",
        "notices": notices,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(notices)
