"""Build orchestrator. Reads raw data, runs analyses, renders templates to dist/."""
from __future__ import annotations

import datetime as dt
import shutil
from pathlib import Path

import jinja2
import numpy as np
import pandas as pd

from build import charts
from build.analysis import baumol, failure_premium, replication
from build.loaders import (
    labour_costs_quarterly, labour_productivity_annual,
    hicp_annual_ireland_vs_ea, hicp_services_vs_goods_indices,
    hicp_coicop_services_breakdown,
)

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
TEMPLATES = ROOT / "templates"
STATIC = ROOT / "static"

env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(TEMPLATES)),
    autoescape=jinja2.select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def _now() -> tuple[str, str]:
    now = dt.datetime.now(dt.timezone.utc)
    return now.isoformat(), now.strftime("%Y-%m-%d %H:%M UTC")


def _format_pct(x, digits: int = 2) -> str:
    if x is None or pd.isna(x):
        return "n/a"
    return f"{float(x):.{digits}f}"


def _format_num(x, digits: int = 3) -> str:
    if x is None or pd.isna(x):
        return "n/a"
    return f"{float(x):.{digits}f}"


def _stars(p: float) -> str:
    if p is None or pd.isna(p):
        return ""
    if p < 0.01:
        return "***"
    if p < 0.05:
        return "**"
    if p < 0.10:
        return "*"
    return "n.s."


def build():
    DIST.mkdir(exist_ok=True)
    assets = DIST / "assets"
    assets.mkdir(exist_ok=True)
    shutil.copy(STATIC / "style.css", assets / "style.css")

    build_iso, build_human = _now()

    print("Loading labour cost data...")
    long_df = labour_costs_quarterly()

    print("Building charts...")
    components = charts.labour_cost_components_bars(long_df)
    stacked = charts.stacked_compensation_area(long_df)
    smalls = charts.hourly_cost_small_multiples(long_df)
    table_rows = charts.sector_table(long_df)

    print("Computing Baumol diagnostics...")
    sigma_df = baumol.sigma_divergence(long_df, stat="hourly_labour_cost")
    sigma_chart = charts.sigma_chart(sigma_df)
    share_df = baumol.stagnant_share_of_compensation(long_df)
    share_chart = charts.stagnant_share_chart(share_df)

    print("Loading productivity series...")
    try:
        prod_df = labour_productivity_annual()
    except Exception as e:
        print(f"  productivity load failed: {e}")
        prod_df = None
    reg = baumol.baumol_regression(long_df, productivity_df=prod_df)
    baumol_chart = charts.baumol_scatter(reg["sectors"])
    prod_gap_chart = (
        charts.baumol_prod_gap_scatter(reg["sectors"])
        if any("prod_gap" in r for r in reg["sectors"])
        else None
    )

    print("Loading HICP for failure-premium claim 7...")
    try:
        hicp_df = hicp_annual_ireland_vs_ea()
    except Exception as e:
        print(f"  HICP load failed: {e}")
        hicp_df = pd.DataFrame()

    print("Loading HICP services-vs-goods for headline chart...")
    try:
        sg_df = hicp_services_vs_goods_indices()
        hicp_chart = charts.hicp_services_goods_chart(sg_df)
    except Exception as e:
        print(f"  services/goods load failed: {e}")
        hicp_chart = None
    try:
        coicop_df = hicp_coicop_services_breakdown()
    except Exception as e:
        print(f"  COICOP load failed: {e}")
        coicop_df = pd.DataFrame()

    print("Building failure-premium claim cards...")
    claims = failure_premium.all_claims(hicp_df=hicp_df)
    if not coicop_df.empty:
        for c in claims:
            if c["id"] == 7:
                c["chart"] = charts.hicp_services_breakdown_chart(coicop_df)
                break

    print("Running DFin-ESRI replication on EU KLEMS data...")
    rep_data = None
    rep_chart = None
    cross_country_chart = None
    cross_country_commentary = ""
    shift_share_chart = None
    shift_share_commentary = ""
    try:
        panel = replication.build_panel("IE")
        rep_data = replication.run_replication(panel)
        rep_chart = charts.replication_coefficient_plot(rep_data["results"])
        ss = replication.shift_share_growth_disease(panel)
        actual = ss.pop("actual")
        if ss:
            shift_share_chart = charts.shift_share_chart(actual, ss)
            bases = sorted(ss.keys())
            oldest = ss[bases[0]]["mean_growth"]
            latest_base = ss[bases[-1]]["mean_growth"]
            actual_m = actual["mean_growth"]
            shift_share_commentary = (
                f"Counterfactual mean LP growth using {bases[0]} weights: "
                f"<strong>{oldest:.2f}%/yr</strong>; using {bases[-1]} weights: "
                f"<strong>{latest_base:.2f}%/yr</strong>; actual: <strong>{actual_m:.2f}%/yr</strong>. "
                + ("Older weights yield higher counterfactual growth, consistent with "
                   "Baumol-Nordhaus growth disease."
                   if oldest > actual_m else
                   "Older weights do <em>not</em> yield higher counterfactual growth — "
                   "no Baumol-Nordhaus growth-disease drag is visible in these data, "
                   "matching the paper's headline conclusion.")
            )
    except Exception as e:
        print(f"  replication failed: {e}")

    try:
        cc_df = replication.cross_country_lp_table()
        cross_country_chart = charts.cross_country_lp_plot(cc_df)
        cc_clean = cc_df.dropna(subset=["beta"])
        if "IE" in cc_clean["country"].values:
            ie_row = cc_clean[cc_clean["country"] == "IE"].iloc[0]
            ie_rank = (cc_clean.sort_values("beta")["country"].tolist()).index("IE") + 1
            n_countries = len(cc_clean)
            n_negative_sig = ((cc_clean["beta"] < 0) & (cc_clean["p"] < 0.05)).sum()
            cross_country_commentary = (
                f"Ireland's price~LP coefficient is "
                f"<strong>{ie_row['beta']:.3f}</strong> "
                f"(SE {ie_row['se']:.3f}, p = {ie_row['p']:.3f}), ranking "
                f"{ie_rank} of {n_countries}. "
                f"Of the {n_countries} countries, {n_negative_sig} return a negative "
                f"and statistically significant coefficient — i.e. Baumol's price-disease "
                f"prediction is widely supported across Europe, not unique to Ireland."
            )
    except Exception as e:
        print(f"  cross-country sweep failed: {e}")

    common = dict(build_iso=build_iso, build_human=build_human)

    # ----- Index page KPIs -----
    sigma_latest_row = sigma_df.iloc[-1]
    share_latest_row = share_df.iloc[-1]
    health_row = next((r for r in table_rows if r["sector"] == "Q"), None)
    conv = reg["summary"]["convergence_test"]
    kpi = {
        "sigma_latest": _format_num(sigma_latest_row["sigma"], 3),
        "sigma_period": str(sigma_latest_row["period"]),
        "stagnant_share": _format_pct(share_latest_row["stagnant_share"] * 100, 1),
        "share_period": str(share_latest_row["period"]),
        "health_cagr": _format_pct(health_row["cagr_5y_pct"] if health_row else None, 1),
        "beta": _format_num(conv["beta_initial_log_cost"], 3),
        "beta_sig": _stars(conv["p_beta"]),
        "beta_n": conv["n"],
    }

    headline_paragraph = (
        f"Cross-sector dispersion of log hourly labour cost was "
        f"<strong>{kpi['sigma_latest']}</strong> in {kpi['sigma_period']}, "
        f"with stagnant sectors (H, I, P, Q, R, S) accounting for "
        f"<strong>{kpi['stagnant_share']}%</strong> of the constructed wagebill. "
        f"The β-convergence regression of cost growth on initial level returns "
        f"β = <strong>{kpi['beta']}</strong> ({kpi['beta_sig']}, n = {kpi['beta_n']}) — "
        f"{'consistent with' if conv['beta_initial_log_cost'] < 0 and conv['p_beta'] < 0.10 else 'not strongly consistent with'} "
        f"the Baumol prediction that initially low-cost sectors grow faster."
    )

    # ----- Render -----
    pages = {
        "index.html": dict(
            page="index", page_title="Overview",
            kpi=kpi, headline_paragraph=headline_paragraph,
            hicp_chart=hicp_chart,
        ),
        "labour-costs.html": dict(
            page="labour-costs", page_title="Labour costs",
            data_period=f"{long_df['period'].min()} to {long_df['period'].max()}",
            components_chart=components,
            stacked_chart=stacked,
            small_multiples=smalls,
            table_rows=table_rows,
            table_period=str(long_df["period"].max()),
        ),
        "productivity.html": dict(
            page="productivity", page_title="Productivity & pay",
            sigma_chart=sigma_chart,
            sigma_commentary=_sigma_commentary(sigma_df),
            baumol_chart=baumol_chart,
            conv={
                "beta_initial_log_cost": _format_num(conv["beta_initial_log_cost"], 3),
                "se_beta": _format_num(conv["se_beta"], 3),
                "t_beta": _format_num(conv["t_beta"], 2),
                "p_beta": _format_num(conv["p_beta"], 3),
                "r2": _format_num(conv["r2"], 3),
                "n": conv["n"],
            },
            conv_interpretation=_conv_interpretation(conv),
            share_chart=share_chart,
            share_commentary=_share_commentary(share_df),
            prod_gap_chart=prod_gap_chart,
            baumol_test=_format_baumol_test(reg["summary"].get("baumol_test")),
            baumol_test_ex_mnc=_format_baumol_test(reg["summary"].get("baumol_test_ex_mnc")),
            baumol_test_interpretation=_baumol_test_interpretation(reg["summary"].get("baumol_test")),
        ),
        "failure-premium.html": dict(
            page="failure-premium", page_title="Failure premium",
            claims=claims,
        ),
        "methods.html": dict(
            page="methods", page_title="Methods",
        ),
    }
    if rep_data is not None:
        pages["replication.html"] = dict(
            page="replication", page_title="Replication",
            rep=rep_data,
            replication_chart=rep_chart,
            shift_share_chart=shift_share_chart,
            shift_share_commentary=shift_share_commentary,
            cross_country_chart=cross_country_chart,
            cross_country_commentary=cross_country_commentary,
        )

    for name, ctx in pages.items():
        ctx.update(common)
        tpl = env.get_template(name)
        out = DIST / name
        out.write_text(tpl.render(**ctx))
        print(f"  wrote {out.relative_to(ROOT)}")


def _sigma_commentary(df: pd.DataFrame) -> str:
    if df.empty:
        return ""
    first, last = df.iloc[0]["sigma"], df.iloc[-1]["sigma"]
    delta = last - first
    direction = "narrowed" if delta < 0 else "widened"
    return (
        f"Over the sample, σ has {direction} from {first:.3f} to {last:.3f} "
        f"(Δ = {delta:+.3f})."
    )


def _conv_interpretation(conv: dict) -> str:
    b, p = conv["beta_initial_log_cost"], conv["p_beta"]
    if b < 0 and p < 0.05:
        return (
            "A negative and statistically significant β indicates β-convergence: "
            "sectors starting from a lower hourly cost have grown faster, which is "
            "consistent with the Baumol mechanism pulling lower-paid sectors up "
            "toward the economy mean."
        )
    if b < 0:
        return (
            "β is negative — the sign predicted by Baumol — but the estimate is "
            "imprecise; the convergence reading should not be over-interpreted on "
            "this sample."
        )
    return (
        "β is non-negative on this sample, which is not what straight Baumol "
        "convergence predicts. Possible explanations: composition effects in MNC-"
        "heavy sectors (C, J), measurement at the letter-sector level, or "
        "a sample window dominated by large structural shocks."
    )


def _format_baumol_test(bt: dict | None) -> dict | None:
    if not bt:
        return None
    return {
        "gamma": _format_num(bt["gamma_prod_gap"], 3),
        "se": _format_num(bt["se_gamma"], 3),
        "t": _format_num(bt["t_gamma"], 2),
        "p": _format_num(bt["p_gamma"], 3),
        "r2": _format_num(bt["r2"], 3),
        "n": bt["n"],
        "stars": _stars(bt["p_gamma"]),
    }


def _baumol_test_interpretation(bt: dict | None) -> str:
    if not bt:
        return (
            "Productivity series not loaded for this build, so the headline "
            "Baumol regression is not yet reported. The convergence test above "
            "uses cost data only."
        )
    g, p = bt["gamma_prod_gap"], bt["p_gamma"]
    if g > 0 and p < 0.05:
        return (
            "γ is positive and statistically significant: sectors whose "
            "productivity has fallen behind the economy-wide mean by 1 pp/yr "
            f"see hourly cost growth roughly {abs(g)*100:.2f} pp/yr faster than the "
            "average sector. This is direct evidence for the Baumol mechanism "
            "in Irish data."
        )
    if g > 0:
        return (
            "γ is positive — the sign Baumol predicts — but imprecisely "
            "estimated. The sign is consistent with the mechanism; the cross-"
            "section is small (regression is across NACE letters, not a panel) "
            "and the standard errors reflect that."
        )
    if g < 0 and p < 0.05:
        return (
            f"γ = {g:.3f} is negative <em>and</em> statistically significant "
            f"(p = {p:.3f}). This is the opposite of the strong-form Baumol "
            "prediction: in 2008–2024 Irish data, sectors that fell behind "
            "in productivity growth saw <em>slower</em> hourly labour cost "
            "growth, not faster. The simple cross-sector wage-pull mechanism "
            "is rejected on this specification. Caveats: the test is a "
            "between-sector cross-section at NACE-letter granularity (small N), "
            "the productivity series uses chain-linked real GVA (which still "
            "carries some MNC noise even after C and J are excluded in the "
            "robustness check), and a softer Baumol pattern can still be "
            "present in the σ-dispersion and share-shift evidence below."
        )
    return (
        "γ is negative but imprecisely estimated. The headline Baumol "
        "prediction is not supported on this specification; the σ-divergence "
        "and share-shift evidence below are the better diagnostics on this "
        "sample."
    )


def _share_commentary(df: pd.DataFrame) -> str:
    if df.empty:
        return ""
    first = df.iloc[0]
    last = df.iloc[-1]
    delta = (last["stagnant_share"] - first["stagnant_share"]) * 100
    direction = "risen" if delta > 0 else "fallen"
    return (
        f"On this measure the stagnant-sector share has {direction} from "
        f"{first['stagnant_share']*100:.1f}% in {first['period']} to "
        f"{last['stagnant_share']*100:.1f}% in {last['period']} "
        f"(Δ = {delta:+.1f} pp), with progressive sectors moving in the "
        f"opposite direction."
    )


if __name__ == "__main__":
    build()
