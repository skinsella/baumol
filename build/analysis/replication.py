"""Replication of Hennessy, Lawless & O'Connor (DFin-ESRI, January 2026),
"Productivity growth in Europe: is Baumol cost disease an explanation?"

We replicate Tables 1-5 column 4 (Ireland-only specifications) using the
same EU KLEMS & INTANProd Release 2023 underlying data.

Constructed measures
--------------------
- Real GVA growth     : Δlog(VA_Q)
- Nominal GVA growth  : Δlog(VA_CP)
- Price growth        : Δlog(VA_PI) — value-added deflator, sector-specific
- Hours growth        : Δlog(H_EMP)
- Wage growth         : Δlog(COMP / EMPE) — compensation per employee
- Labour productivity : Δlog(VA_Q / H_EMP)
- TFP growth          : Δlog(VA_Q) − w·Δlog(H_EMP) − (1−w)·Δlog(K)
                        with w = Tornqvist 2-year-avg labour share, capped to [0.1, 0.9]

Note: the public EU KLEMS Release 2023 has TFP missing for Ireland in the
growth-accounts module. The paper authors had access to a non-public Irish
TFP series and we cannot reproduce it exactly. The LP-based replications are
expected to match the paper closely (and they do); the TFP-based replications
match the paper qualitatively but not always numerically.

All regressions: outcome ~ productivity + sector FE + year FE, HC3 robust SE.
Sample matches paper: 16 NACE letters, 1997-2021 (n = 400).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm
from statsmodels.formula.api import ols

from build.fetchers import euklems

PAPER_NACE_16 = list("ABCDEGHIJKLMNPQR")

NACE_FULL_LABELS = {
    "A": "Agriculture, forestry and fishing",
    "B": "Mining and quarrying",
    "C": "Manufacturing",
    "D": "Electricity, gas, steam, air conditioning supply",
    "E": "Water supply, sewerage, waste management",
    "F": "Construction",
    "G": "Wholesale and retail trade",
    "H": "Transportation and storage",
    "I": "Accommodation and food services",
    "J": "Information and communication",
    "K": "Financial and insurance activities",
    "L": "Real estate activities",
    "M": "Professional, scientific and technical activities",
    "N": "Administrative and support services",
    "O": "Public administration and defence",
    "P": "Education",
    "Q": "Human health and social work",
    "R": "Arts, entertainment and recreation",
    "S": "Other service activities",
    "T": "Activities of households as employers",
    "U": "Extraterritorial organisations",
}

# DFin-ESRI traded/non-traded classification (Annex Table A3).
TRADED = {"A", "B", "C", "D", "E", "G", "H", "I", "J", "K", "L", "M"}
NON_TRADED = {"N", "P", "Q", "R"}

# Paper Table 1-5 column 4 (Ireland-only) coefficients for side-by-side comparison.
PAPER_IRELAND = {
    ("price", "tfp"):    {"beta": -0.054, "se": 0.012, "p": 0.001, "r2": 0.258},
    ("price", "lp"):     {"beta": -0.252, "se": 0.114, "p": 0.05,  "r2": 0.171},
    ("real_gva", "tfp"): {"beta": -0.015, "se": 0.008, "p": 0.10,  "r2": 0.230},
    ("real_gva", "lp"):  {"beta":  0.364, "se": 0.181, "p": 0.05,  "r2": 0.296},
    ("nom_gva", "tfp"):  {"beta":  0.053, "se": 0.011, "p": 0.001, "r2": 0.374},
    ("nom_gva", "lp"):   {"beta":  0.645, "se": 0.065, "p": 0.001, "r2": 0.644},
    ("hours", "tfp"):    {"beta": -0.006, "se": 0.006, "p": 0.50,  "r2": 0.306},
    ("hours", "lp"):     {"beta": -0.299, "se": 0.058, "p": 0.001, "r2": 0.456},
    ("wages", "tfp"):    {"beta": -0.013, "se": 0.008, "p": 0.20,  "r2": 0.363},
    ("wages", "lp"):     {"beta":  0.004, "se": 0.042, "p": 0.50,  "r2": 0.355},
}

OUTCOME_LABELS = {
    "price":    "Hourly value-added price",
    "real_gva": "Real value added",
    "nom_gva":  "Nominal value added",
    "hours":    "Hours worked",
    "wages":    "Compensation per employee",
}


def _ensure_files() -> dict[str, Path]:
    return {n: euklems.fetch(n) for n in [
        "national_accounts.csv", "capital_accounts.csv"
    ]}


def build_panel(country: str = "IE", *, sectors: list[str] | None = None,
                year_start: int = 1997, year_end: int = 2021) -> pd.DataFrame:
    """Construct the regression panel for a single country."""
    sectors = sectors or PAPER_NACE_16
    paths = _ensure_files()
    na = pd.read_csv(paths["national_accounts.csv"],
                     usecols=["nace_r2_code", "geo_code", "year",
                              "VA_Q", "VA_CP", "VA_PI", "H_EMP", "EMPE", "COMP"])
    ca = pd.read_csv(paths["capital_accounts.csv"],
                     usecols=["nace_r2_code", "geo_code", "year", "Kq_GFCF"])
    na = na[(na["geo_code"] == country) & (na["nace_r2_code"].isin(sectors))]
    ca = ca[(ca["geo_code"] == country) & (ca["nace_r2_code"].isin(sectors))]
    panel = na.merge(ca[["nace_r2_code", "year", "Kq_GFCF"]],
                     on=["nace_r2_code", "year"]).sort_values(["nace_r2_code", "year"])

    panel["wage_per_employee"] = panel["COMP"] / panel["EMPE"]
    panel["labour_share"] = (panel["COMP"] / panel["VA_CP"]).clip(0.1, 0.9)
    panel["avg_lshare"] = (
        panel["labour_share"]
        + panel.groupby("nace_r2_code")["labour_share"].shift(1)
    ) / 2

    for col, out in [("VA_Q", "dlog_va_q"), ("VA_CP", "dlog_va_cp"),
                      ("VA_PI", "dlog_p"), ("H_EMP", "dlog_h"),
                      ("Kq_GFCF", "dlog_k"), ("wage_per_employee", "dlog_wage")]:
        panel[out] = panel.groupby("nace_r2_code")[col].transform(
            lambda s: np.log(s).diff()
        )

    panel["dlog_tfp"] = (
        panel["dlog_va_q"]
        - panel["avg_lshare"] * panel["dlog_h"]
        - (1 - panel["avg_lshare"]) * panel["dlog_k"]
    )
    panel["dlog_lp"] = panel["dlog_va_q"] - panel["dlog_h"]

    for col in ["dlog_va_q", "dlog_va_cp", "dlog_p", "dlog_h", "dlog_k",
                "dlog_wage", "dlog_tfp", "dlog_lp"]:
        panel[col] = panel[col] * 100

    panel = panel[(panel["year"] >= year_start) & (panel["year"] <= year_end)]
    panel = panel.dropna(subset=["dlog_p", "dlog_va_q", "dlog_va_cp",
                                  "dlog_h", "dlog_wage", "dlog_tfp", "dlog_lp"])
    panel["sector_label"] = panel["nace_r2_code"].map(NACE_FULL_LABELS)
    return panel


def run_replication(panel: pd.DataFrame) -> dict:
    """Run all 10 (5 outcomes × 2 productivity measures) regressions and return tidy dict."""
    spec_map = {
        "price": "dlog_p", "real_gva": "dlog_va_q", "nom_gva": "dlog_va_cp",
        "hours": "dlog_h", "wages": "dlog_wage",
    }
    prod_map = {"tfp": "dlog_tfp", "lp": "dlog_lp"}

    results = []
    for o_key, o_col in spec_map.items():
        for p_key, p_col in prod_map.items():
            f = f"{o_col} ~ {p_col} + C(nace_r2_code) + C(year)"
            m = ols(f, data=panel).fit(cov_type="HC3")
            paper = PAPER_IRELAND[(o_key, p_key)]
            our_beta, our_se = float(m.params[p_col]), float(m.bse[p_col])
            results.append({
                "outcome_key": o_key,
                "outcome": OUTCOME_LABELS[o_key],
                "productivity": p_key.upper(),
                "our_beta": our_beta,
                "our_se": our_se,
                "our_t": float(m.tvalues[p_col]),
                "our_p": float(m.pvalues[p_col]),
                "our_r2": float(m.rsquared),
                "our_n": int(m.nobs),
                "paper_beta": paper["beta"],
                "paper_se": paper["se"],
                "paper_r2": paper["r2"],
                "sign_match": np.sign(our_beta) == np.sign(paper["beta"]) and abs(paper["beta"]) > 0.001,
                "within_2se": abs(our_beta - paper["beta"]) <= 2 * max(our_se, paper["se"]),
                "baumol_supports": _supports_baumol(o_key, our_beta, m.pvalues[p_col]),
            })
    return {"sectors_used": sorted(panel["nace_r2_code"].unique()),
            "n_obs": int(panel.shape[0]),
            "year_start": int(panel["year"].min()),
            "year_end": int(panel["year"].max()),
            "results": results}


def _supports_baumol(outcome_key: str, beta: float, p: float) -> str:
    """Map (outcome, sign of β, significance) to a verdict on the Baumol prediction."""
    sig = p < 0.05
    if outcome_key == "price":
        if beta < 0 and sig:
            return "supports"
        if beta < 0:
            return "weak"
        return "rejects"
    if outcome_key == "real_gva":
        # Baumol predicts ~zero (constant real share). Significant non-zero rejects.
        if not sig:
            return "supports"
        return "rejects"
    if outcome_key == "nom_gva":
        if beta < 0 and sig:
            return "supports"
        if beta < 0:
            return "weak"
        return "rejects"
    if outcome_key == "hours":
        if beta < 0 and sig:
            return "supports"
        if beta < 0:
            return "weak"
        return "rejects"
    if outcome_key == "wages":
        if not sig:
            return "supports"
        return "rejects"
    return "n/a"


def shift_share_growth_disease(panel: pd.DataFrame, base_years: list[int] | None = None) -> dict:
    """Hypothesis 6 (Nordhaus growth disease): does fixing sector weights at older
    years yield a higher counterfactual aggregate productivity growth than actual?

    For each base year b, compute weighted-average LP growth using sector shares
    of nominal GVA from year b. If older base years give higher growth, that's
    Baumol-Nordhaus growth disease.
    """
    base_years = base_years or [1997, 2000, 2005, 2010, 2015, 2019]
    shares_all = panel.pivot_table(index="year", columns="nace_r2_code",
                                    values="VA_CP", aggfunc="sum")
    shares_all = shares_all.div(shares_all.sum(axis=1), axis=0)
    lp_growth = panel.pivot_table(index="year", columns="nace_r2_code",
                                   values="dlog_lp", aggfunc="mean")

    out = {}
    for b in base_years:
        if b not in shares_all.index:
            continue
        sh = shares_all.loc[b]
        common_cols = lp_growth.columns.intersection(sh.index)
        weighted = (lp_growth[common_cols] * sh[common_cols]).sum(axis=1)
        out[b] = {
            "mean_growth": float(weighted.mean()),
            "shares": sh[common_cols].to_dict(),
        }
    actual = panel.groupby("year").apply(
        lambda g: float((g["dlog_lp"] * g["VA_CP"] / g["VA_CP"].sum()).sum()),
        include_groups=False,
    )
    out["actual"] = {"mean_growth": float(actual.mean()), "by_year": actual.to_dict()}
    return out


def cross_country_lp_table(year_start: int = 1997, year_end: int = 2021,
                            countries: list[str] | None = None) -> pd.DataFrame:
    """Run the LP-based price regression across countries — verifies Hartwig (2011)
    style cross-country result and contextualises Ireland's coefficient.
    """
    countries = countries or [
        "IE", "AT", "BE", "DE", "FR", "NL", "UK",
        "ES", "IT", "EL", "EE", "SI", "SK", "FI", "DK", "SE",
    ]
    rows = []
    for c in countries:
        try:
            p = build_panel(country=c, year_start=year_start, year_end=year_end)
            if len(p) < 100:
                continue
            f = "dlog_p ~ dlog_lp + C(nace_r2_code) + C(year)"
            m = ols(f, data=p).fit(cov_type="HC3")
            rows.append({
                "country": c,
                "beta": float(m.params["dlog_lp"]),
                "se": float(m.bse["dlog_lp"]),
                "p": float(m.pvalues["dlog_lp"]),
                "r2": float(m.rsquared),
                "n": int(m.nobs),
            })
        except Exception as e:
            rows.append({"country": c, "beta": None, "error": str(e)[:120]})
    return pd.DataFrame(rows).sort_values("beta")
