"""Eurostat dissemination API fetcher.

Hits https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/{ds}
and returns tidy long DataFrames. JSON-stat 2.0 format.
"""
from __future__ import annotations

import hashlib
import json
import time
import urllib.parse
from pathlib import Path

import pandas as pd
import requests

EUROSTAT_BASE = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data"
CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _cache_path(dataset: str, params: dict) -> Path:
    key = dataset + "?" + urllib.parse.urlencode(sorted(params.items()), doseq=True)
    h = hashlib.sha1(key.encode()).hexdigest()[:12]
    return CACHE_DIR / f"eurostat_{dataset}_{h}.json"


def fetch_dataset(dataset: str, params: dict | None = None, *, max_age_hours: float = 24.0,
                   force: bool = False) -> dict:
    params = dict(params or {})
    params.setdefault("format", "JSON")
    params.setdefault("lang", "EN")
    cache = _cache_path(dataset, params)
    if not force and cache.exists():
        age_h = (time.time() - cache.stat().st_mtime) / 3600
        if age_h < max_age_hours:
            return json.loads(cache.read_text())
    url = f"{EUROSTAT_BASE}/{dataset}?" + urllib.parse.urlencode(params, doseq=True)
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    body = resp.json()
    cache.write_text(json.dumps(body))
    return body


def to_long_df(jsonstat: dict) -> pd.DataFrame:
    """Eurostat JSON-stat is the same v2.0 shape as CSO; reuse the parser."""
    from build.fetchers.cso import to_long_df as _parse

    return _parse(jsonstat)
