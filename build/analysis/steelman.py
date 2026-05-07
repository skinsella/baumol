"""Steelman test of the failure-premium thesis.

The steelman version of the institutional thesis splits Ireland's high
service-price experience into two parts:

  Baumol-typical drift                + Ireland-specific level premium
  (the slope; European-typical)        (the intercept; institutional)

The slope-level distinction is the critical move: cross-country price-on-
productivity regressions absorb level differences into country fixed effects,
so finding 12 of 16 European countries display the Baumol pattern (which we
do, on the replication page) is silent on whether Ireland's *level* is
institutionally inflated above what wages predict.

This module formalises three tests of the steelman thesis using public
Eurostat data:

  Test A — PLI rank vs GDP-PLI rank, across countries and categories.
           Ireland's "excess rank" in State-procurement-heavy categories
           identifies the failure premium signature.
  Test B — within-Ireland comparison: PLI for State-buyer-heavy categories
           vs PLI for private-market categories, both expressed as
           Ireland-PLI minus expected-PLI given GDP-PLI.
  Test C — concentration test: do the highest excess-PLI categories cluster
           in domains where the State is a dominant or significant buyer?
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import statsmodels.api as sm
from statsmodels.formula.api import ols


# Categorisation of PLI categories by State-buyer intensity.
STATE_BUYER_HIGH = {
    "A0106",     # Health
    "A010603",   # Hospital services (subset)
    "A0104",     # Housing, water, electricity, gas, fuels
}
STATE_BUYER_MIXED = {
    "A0110",     # Education
    "A0111",     # Restaurants and hotels (IPAS effect)
    "P0202",     # Government services aggregate
    "P020202",   # Individual services
}
STATE_BUYER_LOW = {
    "A0107",     # Transport (mostly private market)
    "A010703",   # Transport services
    "A0112",     # Miscellaneous goods and services
    "A01",       # Total individual consumption (broad)
    "P02",       # Total services (broad)
    "P0201",     # Consumer services
}

CATEGORY_FRIENDLY = {
    "GDP": "Overall economy (GDP)",
    "A01": "All consumer goods and services",
    "A0104": "Housing, utilities and fuel",
    "A0106": "Health",
    "A010603": "Hospital services",
    "A0107": "Transport (incl. cars and fuel)",
    "A010703": "Transport services",
    "A0110": "Education",
    "A0111": "Restaurants and hotels",
    "A0112": "Misc. goods and services",
    "P02": "All services (P-classification)",
    "P0201": "Consumer services",
    "P0202": "Government services",
    "P020202": "Individual services",
}


def excess_rank_table(panel: pd.DataFrame, country: str = "IE") -> pd.DataFrame:
    """For each category, compute the country's PLI rank and how that rank
    differs from the country's GDP-PLI rank.

    A negative "rank delta" (lower-numbered rank = more expensive) in a
    category vs GDP rank means the country is more expensive in that
    category than its overall wage level predicts — the steelman
    signature.
    """
    countries_only = panel[~panel["country"].isin({"EU27_2020", "EA20"})]
    pivot = countries_only.pivot_table(index="country", columns="category_code",
                                        values="pli")
    if "GDP" not in pivot.columns or country not in pivot.index:
        return pd.DataFrame()

    gdp_rank = pivot["GDP"].rank(ascending=False)
    gdp_rank_country = gdp_rank.loc[country]

    rows = []
    for cat in pivot.columns:
        if cat == "GDP":
            continue
        col = pivot[cat].dropna()
        if country not in col.index:
            continue
        cat_rank = col.rank(ascending=False)
        cat_rank_country = cat_rank.loc[country]
        rows.append({
            "category_code": cat,
            "category_label": CATEGORY_FRIENDLY.get(cat, cat),
            "pli": float(pivot.loc[country, cat]),
            "gdp_rank": float(gdp_rank_country),
            "category_rank": float(cat_rank_country),
            "rank_delta": float(cat_rank_country - gdp_rank_country),
            "n_countries": int(col.shape[0]),
            "state_buyer": (
                "high" if cat in STATE_BUYER_HIGH else
                "mixed" if cat in STATE_BUYER_MIXED else
                "low" if cat in STATE_BUYER_LOW else "other"
            ),
        })
    return pd.DataFrame(rows).sort_values("rank_delta")


def predicted_pli_residuals(panel: pd.DataFrame) -> pd.DataFrame:
    """Cross-country regression: log(PLI_category) on log(GDP_PLI), per category.

    Country residual = how far its PLI sits above (or below) what the country's
    overall wage/price level predicts. The Irish residual in State-buyer
    categories is the cleanest steelman test.
    """
    countries_only = panel[~panel["country"].isin({"EU27_2020", "EA20"})].copy()
    pivot = countries_only.pivot_table(index="country", columns="category_code",
                                        values="pli")
    if "GDP" not in pivot.columns:
        return pd.DataFrame()

    gdp = pivot["GDP"].dropna()
    rows = []
    for cat in pivot.columns:
        if cat == "GDP":
            continue
        col = pivot[cat].dropna()
        common = gdp.index.intersection(col.index)
        if len(common) < 8:
            continue
        x = np.log(gdp.loc[common].values)
        y = np.log(col.loc[common].values)
        X = sm.add_constant(x)
        m = sm.OLS(y, X).fit(cov_type="HC3")
        resid = pd.Series(m.resid, index=common)
        for c in common:
            rows.append({
                "country": c,
                "category_code": cat,
                "category_label": CATEGORY_FRIENDLY.get(cat, cat),
                "pli": float(col.loc[c]),
                "predicted_log_pli": float(m.params[0] + m.params[1] * np.log(gdp.loc[c])),
                "log_pli_residual": float(resid.loc[c]),
                "pli_excess_pct": float((np.exp(resid.loc[c]) - 1) * 100),
                "elasticity": float(m.params[1]),
                "r2": float(m.rsquared),
                "n": int(m.nobs),
                "state_buyer": (
                    "high" if cat in STATE_BUYER_HIGH else
                    "mixed" if cat in STATE_BUYER_MIXED else
                    "low" if cat in STATE_BUYER_LOW else "other"
                ),
            })
    return pd.DataFrame(rows)


def steelman_summary(residuals: pd.DataFrame, country: str = "IE") -> dict:
    """Single-line summary statistics for the headline page.

    Reports Ireland's mean residual by State-buyer-intensity bucket; if the
    high bucket is materially positive vs the low bucket, the steelman has
    survived the test.
    """
    df = residuals[residuals["country"] == country]
    if df.empty:
        return {}
    grouped = df.groupby("state_buyer")["pli_excess_pct"].agg(["mean", "count"]).round(1)
    out = {bucket: {"mean": float(grouped.loc[bucket, "mean"]),
                    "n": int(grouped.loc[bucket, "count"])}
           for bucket in grouped.index}

    high_mean = out.get("high", {}).get("mean", 0.0)
    low_mean = out.get("low", {}).get("mean", 0.0)
    out["differential_high_minus_low_pp"] = round(high_mean - low_mean, 1)
    out["headline_category"] = (
        df.sort_values("pli_excess_pct", ascending=False).iloc[0].to_dict()
        if not df.empty else None
    )
    return out
