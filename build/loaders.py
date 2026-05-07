"""Data loaders. Each function returns a tidy DataFrame ready for analysis.

Loaders are thin wrappers around fetchers that select the right slice and
do canonical column renames. Heavy transforms (regressions, decompositions)
live in build.analysis.
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd

from build.fetchers import cso, eurostat
from build.fetchers.cso import (
    NACE_LETTERS_ORDER,
    PROGRESSIVE_NACE,
    STAGNANT_NACE,
    MNC_HEAVY,
    quarter_to_period,
)


def _ehq03_long() -> pd.DataFrame:
    raw = cso.fetch_dataset("EHQ03")
    df = cso.to_long_df(raw)
    return df.rename(columns={
        "STATISTIC": "statistic",
        "TLIST(Q1)": "quarter_label",
        "TLIST(Q1)_code": "quarter_code",
        "C02665V03225": "sector_label",
        "C02665V03225_code": "sector_code",
        "C02397V02888": "employee_type",
        "C02397V02888_code": "employee_type_code",
    })


def labour_costs_quarterly(*, all_employees_only: bool = True) -> pd.DataFrame:
    """Hourly labour cost components by NACE letter sector, quarterly.

    Columns: period (PeriodIndex Q), sector (letter A..S), sector_label,
             stat (canonical key), value (€).
    """
    df = _ehq03_long()
    if all_employees_only:
        df = df[df["employee_type_code"] == "-"]
    df = df[df["sector_code"].isin(NACE_LETTERS_ORDER + ["-"])]
    df["period"] = df["quarter_code"].apply(quarter_to_period)

    stat_map = {
        "Average Hourly Total Labour Costs": "hourly_labour_cost",
        "Average Hourly Earnings": "hourly_earnings",
        "Average Hourly Other Labour Costs": "hourly_other_labour_cost",
        "Average Hourly Regular Earnings": "hourly_regular_earnings",
        "Average Hourly Irregular Earnings": "hourly_irregular_earnings",
        "Average Weekly Earnings": "weekly_earnings",
        "Average Weekly Paid Hours": "weekly_paid_hours",
        "Employment": "employment",
    }
    df = df[df["statistic"].isin(stat_map.keys())].copy()
    df["stat"] = df["statistic"].map(stat_map)
    out = df[["period", "sector_code", "sector_label", "stat", "value"]].rename(
        columns={"sector_code": "sector"}
    )
    out["sector"] = out["sector"].replace({"-": "TOT"})
    return out.reset_index(drop=True)


def labour_costs_wide() -> pd.DataFrame:
    """Wide form: index period, columns (sector, stat). Used by chart code."""
    long = labour_costs_quarterly()
    return long.pivot_table(index="period", columns=["sector", "stat"], values="value")


# --------- National Accounts: GVA + hours by sector ---------

def gva_by_sector_annual() -> pd.DataFrame:
    """GVA by NACE A21 sector at current and constant prices, annual.

    Tries CSO NQQ27 (quarterly current-price GVA by NACE) and falls back to
    Eurostat nama_10_a21 if missing. Returns long form: period, sector, measure, value.
    """
    raw = cso.fetch_dataset("NQQ27")
    df = cso.to_long_df(raw)
    cols = {c: c for c in df.columns}
    period_col = next((c for c in df.columns if c.startswith("TLIST")), None)
    sector_code_col = next((c for c in df.columns if c.endswith("_code") and df[c].astype(str).str.fullmatch(r"[A-S]|-").any()), None)
    if period_col is None or sector_code_col is None:
        return _gva_via_eurostat()
    sector_label_col = sector_code_col.replace("_code", "")
    period_code_col = period_col + "_code"
    df["period_code"] = df[period_code_col]
    df["period"] = df["period_code"].apply(lambda q: quarter_to_period(str(q)) if len(str(q)) == 5 else pd.Period(int(q), freq="Y"))
    df = df.rename(columns={sector_code_col: "sector", sector_label_col: "sector_label", "STATISTIC": "measure"})
    out = df[["period", "sector", "sector_label", "measure", "value"]].copy()
    out["sector"] = out["sector"].replace({"-": "TOT"})
    return out.reset_index(drop=True)


def _gva_via_eurostat() -> pd.DataFrame:
    raw = eurostat.fetch_dataset(
        "nama_10_a21",
        params={
            "geo": "IE",
            "na_item": ["B1G", "D1"],
            "unit": ["CP_MEUR", "CLV20_MEUR"],
        },
    )
    df = eurostat.to_long_df(raw)
    period_col = "time" if "time" in df.columns else next(c for c in df.columns if "TIME" in c.upper())
    df["period"] = df[period_col].astype(int).apply(lambda y: pd.Period(y, freq="Y"))
    df = df.rename(columns={"nace_r2_code": "sector", "nace_r2": "sector_label",
                            "na_item": "measure"})
    return df[["period", "sector", "sector_label", "measure", "unit", "value"]]


def hours_worked_annual() -> pd.DataFrame:
    """Total hours worked by NACE letter sector, annual (Eurostat nama_10_a64_e).

    Returns columns: period (Y), sector (letter), sector_label, hours_thousands.
    """
    raw = eurostat.fetch_dataset(
        "nama_10_a64_e",
        params={"geo": "IE", "unit": "THS_HW", "na_item": "EMP_DC"},
        max_age_hours=24,
    )
    df = eurostat.to_long_df(raw)
    df["period"] = df["time"].astype(int).apply(lambda y: pd.Period(y, freq="Y"))
    df = df[df["nace_r2_code"].str.fullmatch(r"[A-U]")]
    return df.rename(columns={"nace_r2_code": "sector",
                              "nace_r2": "sector_label",
                              "value": "hours_thousands"})[
        ["period", "sector", "sector_label", "hours_thousands"]
    ].reset_index(drop=True)


def real_gva_annual() -> pd.DataFrame:
    """Chain-linked real GVA by NACE letter, annual (Eurostat nama_10_a64).

    Returns columns: period (Y), sector (letter), sector_label, gva_meur (CLV20).
    """
    raw = eurostat.fetch_dataset(
        "nama_10_a64",
        params={"geo": "IE", "na_item": "B1G", "unit": "CLV20_MEUR"},
        max_age_hours=24,
    )
    df = eurostat.to_long_df(raw)
    df["period"] = df["time"].astype(int).apply(lambda y: pd.Period(y, freq="Y"))
    df = df[df["nace_r2_code"].str.fullmatch(r"[A-U]")]
    return df.rename(columns={"nace_r2_code": "sector",
                              "nace_r2": "sector_label",
                              "value": "gva_meur"})[
        ["period", "sector", "sector_label", "gva_meur"]
    ].reset_index(drop=True)


def labour_productivity_annual(*, exclude_mnc_heavy: bool = False) -> pd.DataFrame:
    """Real GVA per hour worked by NACE letter, annual.

    Returns columns: period, sector, prod (€ per hour, real CLV20).

    Note Irish caveat: for sectors C and J the productivity number reflects
    MNC-driven GVA, not domestic productivity. The exclude_mnc_heavy flag
    drops these from the returned frame for the robustness check.
    """
    g = real_gva_annual()
    h = hours_worked_annual()
    m = g.merge(h[["period", "sector", "hours_thousands"]],
                on=["period", "sector"], how="inner")
    m["prod"] = m["gva_meur"] * 1e6 / (m["hours_thousands"] * 1e3)
    if exclude_mnc_heavy:
        from build.fetchers.cso import MNC_HEAVY
        m = m[~m["sector"].isin(MNC_HEAVY)]
    return m[["period", "sector", "sector_label", "prod"]].reset_index(drop=True)


def hicp_cp11_decomposition() -> dict:
    """Decompose IE HICP CP11 (restaurants/accommodation) cumulative rise
    since 2019 into wage component and residual.

    Returns dict with: ie_total_pct, ea_total_pct, ie_wage_pct.
    Used by failure-premium claim 7.
    """
    out = {}
    for geo, key in [("IE", "ie_total_pct"), ("EA20", "ea_total_pct")]:
        raw = eurostat.fetch_dataset(
            "prc_hicp_midx",
            params={"geo": geo, "unit": "I15", "coicop": "CP11"},
            max_age_hours=24,
        )
        df = eurostat.to_long_df(raw)
        df["period"] = pd.PeriodIndex(df["time"], freq="M")
        df = df.sort_values("period")
        v_2019 = df[df["period"].dt.year == 2019]["value"].mean()
        v_latest = df.iloc[-1]["value"]
        out[key] = float((v_latest / v_2019 - 1) * 100)

    # Sector I labour cost growth, same window — fetched fresh from CSO via labour_costs_quarterly
    lc = labour_costs_quarterly()
    si = lc[(lc["sector"] == "I") & (lc["stat"] == "hourly_labour_cost")].sort_values("period")
    v_lc_2019 = si[si["period"].apply(lambda p: p.year == 2019)]["value"].mean()
    v_lc_latest = si.iloc[-1]["value"]
    out["ie_wage_pct"] = float((v_lc_latest / v_lc_2019 - 1) * 100)
    return out


def eu_self_employment_rates(year: int = 2024) -> pd.DataFrame:
    """EU self-employment rates (15-74) for the latest year via lfsa_egaps."""
    countries = ["IE", "DE", "FR", "NL", "BE", "DK", "SE", "AT", "FI", "LU",
                 "ES", "IT", "EL", "PT", "PL", "CZ", "HU", "SK", "SI", "HR",
                 "EE", "LV", "LT", "RO", "BG", "CY", "MT", "EU27_2020"]
    j_total = eurostat.fetch_dataset(
        "lfsa_egaps",
        params={"geo": countries, "age": "Y15-74", "sex": "T",
                "wstatus": "EMP", "time": str(year)},
        max_age_hours=24,
    )
    j_self = eurostat.fetch_dataset(
        "lfsa_egaps",
        params={"geo": countries, "age": "Y15-74", "sex": "T",
                "wstatus": "SELF", "time": str(year)},
        max_age_hours=24,
    )
    total = eurostat.to_long_df(j_total).set_index("geo_code")["value"]
    selfemp = eurostat.to_long_df(j_self).set_index("geo_code")["value"]
    rate = (selfemp / total * 100)
    rate.name = "rate"
    df = rate.reset_index().rename(columns={"geo_code": "country"})
    return df.dropna()


def hicp_actual_rentals_index() -> pd.DataFrame:
    """HICP CP041 (Actual rentals for housing) for IE, monthly index 2015=100.

    Used to verify failure-premium claim 6 ("rents up 89% since 2014").
    """
    raw = eurostat.fetch_dataset(
        "prc_hicp_midx",
        params={"geo": "IE", "unit": "I15", "coicop": "CP041"},
        max_age_hours=24,
    )
    df = eurostat.to_long_df(raw)
    df["period"] = pd.PeriodIndex(df["time"], freq="M")
    return df[["period", "value"]].rename(columns={"value": "rent_index"}).sort_values("period")


def hicp_services_vs_goods_indices() -> pd.DataFrame:
    """HICP indices (2015=100) for goods (CP_GD) and services (CP_SERV) for IE,
    monthly. Used for the services-vs-goods divergence chart that mirrors the
    Department of Finance Box 5 Figure 11A.

    Returns columns: period (Period[M]), category ('Goods' or 'Services'), index_value.
    """
    raw = eurostat.fetch_dataset(
        "prc_hicp_midx",
        params={
            "geo": "IE",
            "unit": "I15",
            "coicop": ["GD", "SERV"],
        },
        max_age_hours=24,
    )
    df = eurostat.to_long_df(raw)
    df["period"] = pd.PeriodIndex(df["time"], freq="M")
    df = df.rename(columns={"coicop": "category", "value": "index_value"})
    df["category"] = df["category"].replace({
        "Goods": "Goods", "Services": "Services",
    })
    return df[["period", "category", "index_value"]].sort_values("period")


def hicp_coicop_services_breakdown() -> pd.DataFrame:
    """HICP indices for selected COICOP services categories on the DFin Box 5
    footnote list — health insurance, postal, package holidays, medical
    services, total services, restaurants, accommodation. Monthly.
    """
    SERVICE_COICOPS = {
        "SERV":   "Total services",
        "CP044":  "Water, refuse and other dwelling services",
        "CP063":  "Hospital services",
        "CP073":  "Transport services",
        "CP09":   "Recreation and culture",
        "CP096":  "Package holidays",
        "CP11":   "Restaurants and hotels",
        "CP111":  "Catering services",
        "CP1112": "Accommodation services",
    }
    raw = eurostat.fetch_dataset(
        "prc_hicp_midx",
        params={
            "geo": "IE",
            "unit": "I15",
            "coicop": list(SERVICE_COICOPS.keys()),
        },
        max_age_hours=24,
    )
    df = eurostat.to_long_df(raw)
    df["period"] = pd.PeriodIndex(df["time"], freq="M")
    code_col = "coicop_code" if "coicop_code" in df.columns else "coicop"
    df["category_code"] = df[code_col]
    df["category_label"] = df["category_code"].map(SERVICE_COICOPS).fillna(df.get("coicop", df["category_code"]))
    return df[["period", "category_code", "category_label", "value"]].rename(columns={"value": "index_value"})


def eurostat_pli_panel(year: int = 2024) -> pd.DataFrame:
    """Eurostat Price Level Indices, EU27_2020 = 100, by COICOP category.

    Used by the steelman page. Returns long form: country, category_code,
    category_label, pli.
    """
    countries = ["IE", "DE", "FR", "NL", "BE", "DK", "SE", "AT", "FI", "LU",
                 "ES", "IT", "EL", "PT", "PL", "CZ", "UK", "EU27_2020", "EA20"]
    cats = ["GDP", "A01", "A0104", "A0106", "A010603",
            "A0107", "A010703", "A0110", "A0111", "A0112",
            "P02", "P0201", "P0202", "P020202"]
    raw = eurostat.fetch_dataset(
        "prc_ppp_ind",
        params={"geo": countries, "na_item": "PLI_EU27_2020",
                "time": str(year), "ppp_cat": cats},
        max_age_hours=24,
    )
    df = eurostat.to_long_df(raw)
    df = df.rename(columns={"geo_code": "country",
                             "ppp_cat_code": "category_code",
                             "ppp_cat": "category_label",
                             "value": "pli"})
    return df[["country", "category_code", "category_label", "pli"]]


def hicp_annual_ireland_vs_ea() -> pd.DataFrame:
    """HICP overall and selected COICOP for IE vs EA19/EA20, annual rate of change.

    Used in the failure-premium page (hotels/restaurants) and methods.
    """
    raw = eurostat.fetch_dataset(
        "prc_hicp_aind",
        params={
            "geo": ["IE", "EA20"],
            "unit": "RCH_A_AVG",
            "coicop": ["CP00", "CP11", "CP07", "CP04", "CP06"],
        },
    )
    df = eurostat.to_long_df(raw)
    period_col = "time" if "time" in df.columns else next(c for c in df.columns if "TIME" in c.upper())
    df["period"] = df[period_col].astype(int).apply(lambda y: pd.Period(y, freq="Y"))
    return df


# --------- Convenience: real wages, real labour cost ---------

def cso_cpi_quarterly() -> pd.DataFrame:
    """All-items CPI, base 2016=100, quarterly average."""
    raw = cso.fetch_dataset("CPM02")
    df = cso.to_long_df(raw)
    period_col = next(c for c in df.columns if c.startswith("TLIST"))
    df["month_code"] = df[period_col + "_code"].astype(str)
    df = df[df["month_code"].str.len() == 6].copy()
    df["period"] = pd.PeriodIndex(
        [f"{c[:4]}-{int(c[4:]):02d}" for c in df["month_code"]], freq="M"
    ).asfreq("Q", how="end")
    label_col = next((c for c in df.columns if "Commodity" in c or "Coicop" in c), None)
    code_col = label_col + "_code" if label_col else None
    if label_col:
        all_items_codes = ("-", "00")
        df = df[df[code_col].astype(str).isin(all_items_codes)]
    out = df.groupby("period", as_index=False)["value"].mean()
    out = out.rename(columns={"value": "cpi"})
    return out
