# Baumol's cost disease in the Irish economy

Live data site testing two competing explanations for high Irish service-sector
costs: the classical Baumol mechanism (wage-pull from progressive sectors) and
an institutional "failure premium" thesis advanced in popular commentary.
Rebuilt weekly from CSO PxStat and Eurostat dissemination APIs; output is
plain static HTML deployable to any host.

## Stack

- Python 3.12, `requests` + `pandas` for data, `statsmodels` for regressions,
  `plotly` for charts, `jinja2` for templating.
- No Node/JS toolchain. Output is static HTML in `dist/`.
- GitHub Action rebuilds on a weekly cron and on push to `main`.

## Local build

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m build.render
# open dist/index.html
```

The first build pulls fresh data from CSO and Eurostat and caches it in
`data/cache/`. Subsequent builds use the cache for 24 h; pass `force=True`
in the loader call to bypass.

## Layout

```
baumol-ireland/
├── build/
│   ├── fetchers/        # CSO + Eurostat API clients (JSON-stat 2.0)
│   ├── analysis/        # Baumol regressions, failure-premium claim tests
│   ├── loaders.py       # Tidy DataFrames per source dataset
│   ├── charts.py        # Plotly figure factories
│   └── render.py        # Jinja2 orchestrator (entry point)
├── templates/           # One HTML template per page, extending base.html
├── static/style.css     # Hand-rolled CSS, dark-mode aware
├── data/cache/          # On-disk dataset cache (gitignored)
├── dist/                # Build output (gitignored)
└── .github/workflows/build.yml
```

## Deploying to stephenkinsella.net/baumol

GitHub Pages cannot serve a path mount on a domain it does not control.
Three options, in order of simplicity:

### Option A — drop into your existing site repo (recommended for /baumol path)

Run the build in this repo, then sync `dist/` into a `/baumol/` directory
inside the existing `stephenkinsella.net` repo. The Action below has a
commented-out step showing how to push to a sibling repo via a deploy key.

### Option B — serve at a subdomain

Switch to `baumol.stephenkinsella.net`. In the GitHub repo's Pages settings
add the custom domain; in your DNS provider add a CNAME record pointing
`baumol` → `<gh-username>.github.io`. Drop a single-line `CNAME` file into
`dist/` containing the subdomain. This is the path of least resistance.

### Option C — reverse-proxy /baumol from your existing host

If `stephenkinsella.net` is served from a host that supports proxying
(Cloudflare Workers, Netlify, Vercel, nginx), proxy `/baumol/*` to the
`<gh-username>.github.io/baumol-ireland/` project page. This keeps the
path mount you asked for at the cost of one config rule.

## Refresh cadence

| Source | Refresh in build |
|---|---|
| CSO EHQ03 (labour costs) | Weekly |
| CSO NA series (GVA, hours) | Weekly |
| Eurostat HICP, LCI | Weekly |
| Eurostat construction cost index | Monthly |
| IPAS contracts (PDF scrape, gov.ie) | Monthly (planned) |
| HSE annual financial statements | Monthly (planned) |

The page footer shows the actual last-refresh timestamp.

## Sample period

Quarterly labour-cost data from CSO EHQ03 begin in 2008Q1; the pre-2008
NACE Rev. 1 series does not splice cleanly. Annual Eurostat series
extending back to 2000 are used as robustness checks where available.

## Methodological notes

The methods page on the live site documents:

- NACE Rev. 2 sector taxonomy (A21 letters)
- Stagnant/progressive prior used in descriptive charts
- MNC distortion handling for sectors C and J
- Compensation aggregate construction
- Failure-premium adjudication standard

## Re-running a single fetcher

```bash
source .venv/bin/activate
python -c "from build.loaders import labour_costs_quarterly; print(labour_costs_quarterly().tail())"
```

## Adding a new claim test

Add a function in `build/analysis/failure_premium.py` returning the standard
claim dict, then include it in the `all_claims()` aggregator. The
`failure-premium.html` template iterates without further changes.

## Licence

Public-domain data; site code MIT.
