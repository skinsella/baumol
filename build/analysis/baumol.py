"""Baumol-style cost-disease tests.

- Sigma-divergence: cross-sector standard deviation of log hourly compensation
  vs log productivity (or hourly earnings as proxy where productivity is missing).
- Unit labour cost levels and growth by sector.
- Headline regression: Δlog(real hourly cost) on Δlog(productivity).
- Share-shift: stagnant-sector share of compensation over time.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import statsmodels.api as sm

from build.fetchers.cso import NACE_LETTERS_ORDER, STAGNANT_NACE, PROGRESSIVE_NACE


def sigma_divergence(long_df: pd.DataFrame, *, stat: str = "hourly_labour_cost") -> pd.DataFrame:
    """Time-series of cross-sector σ(log value).

    long_df: from loaders.labour_costs_quarterly().
    """
    d = long_df[(long_df["stat"] == stat) & (long_df["sector"].isin(NACE_LETTERS_ORDER))].copy()
    d = d.dropna(subset=["value"])
    d = d[d["value"] > 0]
    d["log_v"] = np.log(d["value"])
    return d.groupby("period")["log_v"].std().rename("sigma").reset_index()


def hourly_labour_cost_indices(long_df: pd.DataFrame, *, base_period: pd.Period | None = None) -> pd.DataFrame:
    """Index hourly labour cost by sector to a base period (default first available)."""
    d = long_df[long_df["stat"] == "hourly_labour_cost"].copy()
    wide = d.pivot_table(index="period", columns="sector", values="value")
    if base_period is None:
        base_period = wide.first_valid_index()
    base = wide.loc[base_period]
    return (wide / base * 100).reset_index()


def stagnant_share_of_compensation(long_df: pd.DataFrame) -> pd.DataFrame:
    """Estimate stagnant-sector share of total wagebill.

    Wagebill ≈ employment × weekly_earnings × 52. Uses CSO EHQ03 employment
    where available; falls back to weekly_earnings × constant employment proxy.
    """
    d = long_df.copy()
    d = d[d["sector"].isin(NACE_LETTERS_ORDER)]
    emp = d[d["stat"] == "employment"].pivot_table(index="period", columns="sector", values="value")
    we = d[d["stat"] == "weekly_earnings"].pivot_table(index="period", columns="sector", values="value")
    common_idx = emp.index.intersection(we.index)
    common_cols = emp.columns.intersection(we.columns)
    wage_bill = (emp.loc[common_idx, common_cols] * we.loc[common_idx, common_cols] * 52)

    stagnant_cols = [c for c in wage_bill.columns if c in STAGNANT_NACE]
    progressive_cols = [c for c in wage_bill.columns if c in PROGRESSIVE_NACE]

    total = wage_bill.sum(axis=1)
    out = pd.DataFrame({
        "period": wage_bill.index,
        "stagnant_share": wage_bill[stagnant_cols].sum(axis=1) / total,
        "progressive_share": wage_bill[progressive_cols].sum(axis=1) / total,
    }).reset_index(drop=True)
    return out


def baumol_regression(long_df: pd.DataFrame, productivity_df: pd.DataFrame | None = None) -> dict:
    """Headline cross-sector regression.

    For each NACE letter, compute long-run growth (CAGR) of:
      - hourly labour cost (CSO EHQ03)
      - real GVA per hour worked (productivity), if productivity_df provided.

    Then regress: Δ_log_cost_i = α + β · Δ_log_prod_economy + γ · (Δ_log_prod_economy − Δ_log_prod_i)

    The Baumol prediction is γ > 0: sectors whose productivity falls behind the
    economy mean experience faster cost growth.

    If productivity_df is None, we fall back to a wage-only convergence test:
    sectors with low *initial* hourly cost should see faster growth (β-convergence).

    Returns dict with regression summary, coefficient table, and per-sector frame.
    """
    d = long_df[long_df["stat"] == "hourly_labour_cost"].copy()
    d = d[d["sector"].isin(NACE_LETTERS_ORDER)]
    wide = d.pivot_table(index="period", columns="sector", values="value").dropna(how="all")
    start = wide.first_valid_index()
    end = wide.last_valid_index()
    n_years = (end.year + (end.quarter - 1) / 4) - (start.year + (start.quarter - 1) / 4)
    cagr = (np.log(wide.loc[end]) - np.log(wide.loc[start])) / max(n_years, 1)
    cagr = cagr.dropna()
    initial = np.log(wide.loc[start]).reindex(cagr.index)

    summary = {
        "start": str(start),
        "end": str(end),
        "n_sectors": int(cagr.shape[0]),
        "convergence_test": None,
        "baumol_test": None,
    }

    X = sm.add_constant(initial.values)
    y = cagr.values
    model = sm.OLS(y, X, missing="drop").fit(cov_type="HC3")
    summary["convergence_test"] = {
        "alpha": float(model.params[0]),
        "beta_initial_log_cost": float(model.params[1]),
        "se_beta": float(model.bse[1]),
        "t_beta": float(model.tvalues[1]),
        "p_beta": float(model.pvalues[1]),
        "r2": float(model.rsquared),
        "n": int(model.nobs),
    }

    sector_frame = pd.DataFrame({
        "sector": cagr.index,
        "initial_log_hourly_cost": initial.values,
        "cagr_hourly_cost": cagr.values,
    }).reset_index(drop=True)

    if productivity_df is not None and not productivity_df.empty:
        productivity_df = productivity_df[~productivity_df["sector"].isin({"L", "T", "U"})]
        cost_start_year = start.year
        py = productivity_df.pivot_table(index="period", columns="sector", values="prod")
        py = py[py.index.year >= cost_start_year]
        if not py.empty:
            p_start = py.first_valid_index()
            p_end = py.last_valid_index()
            yrs = p_end.year - p_start.year
            prod_cagr = (np.log(py.loc[p_end]) - np.log(py.loc[p_start])) / max(yrs, 1)
            prod_cagr = prod_cagr.dropna()
            sector_frame = sector_frame.merge(
                prod_cagr.rename("cagr_prod").reset_index(),
                on="sector", how="left",
            )
            summary["baumol_test"] = _run_baumol_ols(sector_frame, exclude={})
            summary["baumol_test_ex_mnc"] = _run_baumol_ols(
                sector_frame, exclude={"C", "J"}
            )
            mean_prod = float(np.nanmean(sector_frame["cagr_prod"]))
            sector_frame["prod_gap"] = mean_prod - sector_frame["cagr_prod"]

    return {"summary": summary, "sectors": sector_frame.to_dict(orient="records")}


def _run_baumol_ols(sector_frame: pd.DataFrame, *, exclude: set[str]) -> dict | None:
    df = sector_frame.dropna(subset=["cagr_prod"]).copy()
    df = df[~df["sector"].isin(exclude)]
    if len(df) < 5:
        return None
    mean_prod = float(np.nanmean(df["cagr_prod"]))
    df["prod_gap"] = mean_prod - df["cagr_prod"]
    X = sm.add_constant(df[["prod_gap"]].values)
    y = df["cagr_hourly_cost"].values
    m = sm.OLS(y, X).fit(cov_type="HC3")
    return {
        "alpha": float(m.params[0]),
        "gamma_prod_gap": float(m.params[1]),
        "se_gamma": float(m.bse[1]),
        "t_gamma": float(m.tvalues[1]),
        "p_gamma": float(m.pvalues[1]),
        "r2": float(m.rsquared),
        "n": int(m.nobs),
        "excluded": sorted(exclude),
        "mean_prod_cagr": mean_prod,
    }


def unit_labour_cost(long_df: pd.DataFrame, productivity_df: pd.DataFrame) -> pd.DataFrame:
    """ULC = hourly labour cost / hourly real GVA, by sector by year (annual averages).

    Both inputs in nominal terms divided by a sectoral price deflator would be
    cleaner; here we approximate with real GVA per hour from constant-price NA
    series. Documented in the methods page.
    """
    cost_q = long_df[long_df["stat"] == "hourly_labour_cost"].copy()
    cost_q["year"] = cost_q["period"].apply(lambda p: p.year)
    cost_a = cost_q.groupby(["year", "sector"], as_index=False)["value"].mean()
    cost_a = cost_a.rename(columns={"value": "hourly_cost"})

    prod = productivity_df.copy()
    prod["year"] = prod["period"].apply(lambda p: p.year)
    merged = cost_a.merge(prod[["year", "sector", "prod"]], on=["year", "sector"], how="inner")
    merged["ulc"] = merged["hourly_cost"] / merged["prod"]
    return merged
