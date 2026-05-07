"""EU KLEMS & INTANProd fetcher.

Pulls the public Release 2023 CSVs from the LUISS Lab Dropbox mirror linked
from https://euklems-intanprod-llee.luiss.it/download/. Three tables:

- national accounts: VA_Q, VA_CP, VA_PI, H_EMP, EMPE, COMP
- capital accounts: Kq_GFCF (real capital stock, chain-linked)
- growth accounts: VATFP_I, LP1_G, etc. (Ireland mostly empty in this file)

Caches to data/raw_euklems/. Files are large (national 14MB, capital 36MB,
growth 200MB), so cache aggressively. The site build runs only the loader,
which reads cached files; the fetcher only runs on demand or in CI.
"""
from __future__ import annotations

import time
from pathlib import Path

import requests

CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "raw_euklems"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

URLS = {
    "national_accounts.csv":
        "https://www.dropbox.com/s/nkz7mdp0onken1j/national%20accounts.csv?dl=1",
    "labour_accounts.csv":
        "https://www.dropbox.com/s/vgtzptui1m1tj9l/labour%20accounts.csv?dl=1",
    "capital_accounts.csv":
        "https://www.dropbox.com/s/sp2p4m86et66nfg/capital%20accounts.csv?dl=1",
    "growth_accounts.csv":
        "https://www.dropbox.com/scl/fi/vw1drt9u8i5vcqtrhqbxx/"
        "growth-accounts.csv?rlkey=1tfoq18uo9vtkadhx24p3tx59&dl=1",
}


def fetch(name: str, *, max_age_days: float = 30.0, force: bool = False) -> Path:
    """Ensure a single EU KLEMS CSV is on disk, return path."""
    if name not in URLS:
        raise KeyError(f"unknown EU KLEMS file {name!r}")
    path = CACHE_DIR / name
    if not force and path.exists():
        age_days = (time.time() - path.stat().st_mtime) / 86400
        if age_days < max_age_days:
            return path
    url = URLS[name]
    with requests.get(url, stream=True, timeout=600) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                if chunk:
                    f.write(chunk)
    return path


def fetch_all(force: bool = False) -> dict[str, Path]:
    return {n: fetch(n, force=force) for n in URLS}
