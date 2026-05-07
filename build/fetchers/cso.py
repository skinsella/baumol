"""CSO PxStat fetcher.

Reads JSON-stat 2.0 datasets from data.cso.ie via the public JSON-RPC API
and returns tidy long-form pandas DataFrames keyed on (sector, period, statistic).
"""
from __future__ import annotations

import hashlib
import json
import time
import urllib.parse
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests

CSO_BASE = "https://ws.cso.ie/public/api.jsonrpc"
CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _cache_path(matrix: str) -> Path:
    return CACHE_DIR / f"cso_{matrix}.json"


def fetch_dataset(matrix: str, *, max_age_hours: float = 24.0, force: bool = False) -> dict:
    """Fetch a CSO PxStat matrix and return the parsed JSON-stat 2.0 result.

    Cached on disk; pass force=True or max_age_hours=0 to bypass.
    """
    cache = _cache_path(matrix)
    if not force and cache.exists():
        age_h = (time.time() - cache.stat().st_mtime) / 3600
        if age_h < max_age_hours:
            return json.loads(cache.read_text())

    payload = {
        "jsonrpc": "2.0",
        "method": "PxStat.Data.Cube_API.ReadDataset",
        "params": {
            "class": "query",
            "id": [],
            "dimension": {},
            "extension": {
                "pivot": None,
                "codes": False,
                "language": {"code": "en"},
                "format": {"type": "JSON-stat", "version": "2.0"},
                "matrix": matrix,
            },
            "version": "2.0",
        },
        "id": 1,
    }
    url = f"{CSO_BASE}?data={urllib.parse.quote(json.dumps(payload))}"
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    body = resp.json()
    if "result" not in body:
        raise RuntimeError(f"CSO returned no result for {matrix}: {body}")
    cache.write_text(json.dumps(body["result"]))
    return body["result"]


def to_long_df(jsonstat: dict) -> pd.DataFrame:
    """Convert a JSON-stat 2.0 result dict to a tidy long DataFrame.

    Columns: one per dimension (with category labels), plus 'value'.
    """
    dim_ids: list[str] = jsonstat["id"]
    sizes: list[int] = jsonstat["size"]
    values = jsonstat["value"]
    if isinstance(values, dict):
        n = 1
        for s in sizes:
            n *= s
        flat: list[float | None] = [None] * n
        for k, v in values.items():
            flat[int(k)] = v
        values = flat

    cat_labels: dict[str, list[tuple[str, str]]] = {}
    for d in dim_ids:
        cat = jsonstat["dimension"][d]["category"]
        idx_map = cat["index"]
        if isinstance(idx_map, list):
            ordered_codes = idx_map
        else:
            ordered_codes = [None] * len(idx_map)
            for code, i in idx_map.items():
                ordered_codes[i] = code
        labels = cat.get("label", {})
        cat_labels[d] = [(c, labels.get(c, c)) for c in ordered_codes]

    rows = []
    strides = []
    s = 1
    for sz in reversed(sizes):
        strides.insert(0, s)
        s *= sz

    for flat_idx, val in enumerate(values):
        if val is None:
            continue
        idx_per_dim = []
        rem = flat_idx
        for st in strides:
            idx_per_dim.append(rem // st)
            rem = rem % st
        row = {}
        for dim_pos, d in enumerate(dim_ids):
            code, label = cat_labels[d][idx_per_dim[dim_pos]]
            row[d + "_code"] = code
            row[d] = label
        row["value"] = val
        rows.append(row)
    return pd.DataFrame(rows)


def quarter_to_period(q: str) -> pd.Period:
    """CSO quarter codes look like '20081' meaning 2008Q1. Parse to pd.Period."""
    return pd.Period(year=int(q[:4]), quarter=int(q[4:]), freq="Q")


# Sector taxonomy: NACE Rev 2 letters. Keep ordering for stacked charts.
NACE_LETTERS_ORDER = list("ABCDEFGHIJKLMNOPQRS")

# Heuristic Baumol taxonomy. Cited as a *prior* in the methods page; the
# regression doesn't depend on it.
PROGRESSIVE_NACE = {"C", "J", "K"}  # manufacturing (MNC-distorted), ICT, finance
STAGNANT_NACE = {"H", "I", "P", "Q", "R", "S"}  # transport+storage, accom+food, education, health, arts, other
MIXED_NACE = {"A", "B", "D", "E", "F", "G", "L", "M", "N", "O"}

# Sectors heavily distorted by MNC activity in Ireland; flagged in MNC robustness.
MNC_HEAVY = {"C", "J"}
