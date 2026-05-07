"""Plotly chart factories. Each returns a dict {id, html_div, html_script}.

Charts are rendered with plotly.graph_objects to avoid pandas-level coupling
and produce small, self-contained <div> + <script> pairs that templates embed
verbatim.
"""
from __future__ import annotations

import json
from typing import Iterable

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio

from build.fetchers.cso import (
    NACE_LETTERS_ORDER,
    PROGRESSIVE_NACE,
    STAGNANT_NACE,
    MNC_HEAVY,
)
from build.analysis.replication import NACE_FULL_LABELS


def _short_label(letter: str, n_words: int = 3) -> str:
    """Compact label that pairs a NACE letter with the leading words of the
    full sector name. Used everywhere a chart needs a sector label longer than
    just the letter.
    """
    full = NACE_FULL_LABELS.get(letter, "")
    if not full:
        return letter
    head = ", ".join(full.replace("; ", ", ").split(", ")[:1])
    head = " ".join(head.split()[:n_words]).rstrip(",.")
    return f"{letter} · {head}"

# Discrete colour palette: stagnant = warm, progressive = cool, mixed = grey
SECTOR_COLOURS: dict[str, str] = {}
_warms = ["#b30000", "#e34a33", "#fc8d59", "#fdbb84", "#fdd49e", "#fee8c8", "#fff7ec"]
_cools = ["#08519c", "#3182bd", "#6baed6", "#9ecae1"]
_neut = ["#525252", "#737373", "#969696", "#bdbdbd", "#d9d9d9", "#f0f0f0", "#252525", "#08306b"]

_w_idx = _c_idx = _n_idx = 0
for letter in NACE_LETTERS_ORDER:
    if letter in STAGNANT_NACE:
        SECTOR_COLOURS[letter] = _warms[_w_idx % len(_warms)]
        _w_idx += 1
    elif letter in PROGRESSIVE_NACE:
        SECTOR_COLOURS[letter] = _cools[_c_idx % len(_cools)]
        _c_idx += 1
    else:
        SECTOR_COLOURS[letter] = _neut[_n_idx % len(_neut)]
        _n_idx += 1
SECTOR_COLOURS["TOT"] = "#000000"


def _layout(**overrides) -> dict:
    base = dict(
        font=dict(family="ui-serif, Georgia, 'Times New Roman', serif", size=13, color="#222"),
        plot_bgcolor="#ffffff",
        paper_bgcolor="#ffffff",
        margin=dict(l=60, r=20, t=40, b=50),
        hovermode="x unified",
        legend=dict(orientation="h", y=-0.18, x=0, font=dict(size=11)),
        xaxis=dict(showgrid=False, showline=True, linecolor="#888", ticks="outside"),
        yaxis=dict(showgrid=True, gridcolor="#eee", zeroline=False),
    )
    base.update(overrides)
    return base


def fig_to_html(fig: go.Figure, *, div_id: str, height: int = 420) -> dict:
    """Return {div, script} that can be embedded in a template.

    Plotly is loaded once via CDN in the page <head>; we call Plotly.newPlot
    directly so each figure is a thin script.
    """
    fig_json = pio.to_json(fig)
    script = (
        f"<script>(function(){{var spec={fig_json};"
        f"Plotly.newPlot('{div_id}',spec.data,spec.layout,"
        f"{{displaylogo:false,responsive:true,modeBarButtonsToRemove:['lasso2d','select2d']}});}})();</script>"
    )
    div = f'<div id="{div_id}" class="chart" style="height:{height}px"></div>'
    return {"div": div, "script": script}


def stacked_compensation_area(long_df: pd.DataFrame, *, div_id: str = "stacked-compensation") -> dict:
    """Stacked area: total compensation by NACE sector over time.

    Compensation proxy = hourly_labour_cost × weekly_paid_hours × employment × 52.
    """
    d = long_df[long_df["sector"].isin(NACE_LETTERS_ORDER)].copy()
    pivot_emp = d[d["stat"] == "employment"].pivot_table(index="period", columns="sector", values="value")
    pivot_hwk = d[d["stat"] == "weekly_paid_hours"].pivot_table(index="period", columns="sector", values="value")
    pivot_cost = d[d["stat"] == "hourly_labour_cost"].pivot_table(index="period", columns="sector", values="value")
    common = pivot_emp.index.intersection(pivot_hwk.index).intersection(pivot_cost.index)
    cols = sorted(set(pivot_emp.columns) & set(pivot_hwk.columns) & set(pivot_cost.columns),
                  key=lambda x: NACE_LETTERS_ORDER.index(x) if x in NACE_LETTERS_ORDER else 99)
    comp = (
        pivot_emp.loc[common, cols] * pivot_hwk.loc[common, cols] * pivot_cost.loc[common, cols] * 52 / 1e9
    )

    fig = go.Figure()
    for sec in cols:
        label = _short_label(sec)
        fig.add_trace(go.Scatter(
            x=[str(p) for p in comp.index], y=comp[sec], name=label, mode="lines",
            stackgroup="one", line=dict(width=0.5, color=SECTOR_COLOURS.get(sec, "#888")),
            hovertemplate=f"<b>{label}</b> %{{x}}: €%{{y:.2f}}bn<extra></extra>",
        ))
    fig.update_layout(**_layout(
        title="Aggregate annual labour compensation by NACE sector (€ bn)",
        yaxis_title="€ billions",
        xaxis_title=None,
    ))
    return fig_to_html(fig, div_id=div_id, height=460)


def hourly_cost_small_multiples(long_df: pd.DataFrame, *, div_id: str = "hourly-cost-grid") -> dict:
    """Small multiples: hourly labour cost level by sector, all on one canvas."""
    d = long_df[(long_df["stat"] == "hourly_labour_cost")
                & (long_df["sector"].isin(NACE_LETTERS_ORDER))].copy()
    d["period_str"] = d["period"].astype(str)
    sectors = sorted(d["sector"].unique(), key=lambda x: NACE_LETTERS_ORDER.index(x))

    n = len(sectors)
    cols = 4
    rows = (n + cols - 1) // cols
    from plotly.subplots import make_subplots

    titles = [_short_label(s) for s in sectors]
    fig = make_subplots(rows=rows, cols=cols, subplot_titles=titles,
                        shared_xaxes=True, vertical_spacing=0.08, horizontal_spacing=0.05)
    for i, sec in enumerate(sectors):
        sd = d[d["sector"] == sec].sort_values("period")
        r = i // cols + 1
        c = i % cols + 1
        fig.add_trace(go.Scatter(
            x=sd["period_str"], y=sd["value"], mode="lines",
            line=dict(color=SECTOR_COLOURS.get(sec, "#666"), width=1.5),
            showlegend=False, hovertemplate="%{x}: €%{y:.2f}/hr<extra>" + _short_label(sec) + "</extra>",
        ), row=r, col=c)
    fig.update_layout(**_layout(
        title="Hourly labour cost (€/hr) by sector — quarterly",
        showlegend=False,
    ))
    fig.update_xaxes(showgrid=False, tickfont=dict(size=9))
    fig.update_yaxes(showgrid=True, gridcolor="#eee", tickfont=dict(size=10))
    return fig_to_html(fig, div_id=div_id, height=900)


def labour_cost_components_bars(long_df: pd.DataFrame, *, div_id: str = "lc-components") -> dict:
    """Latest quarter: stacked horizontal bars of wages vs other labour costs by sector."""
    latest = long_df["period"].max()
    d = long_df[(long_df["period"] == latest) & (long_df["sector"].isin(NACE_LETTERS_ORDER))].copy()
    pivot = d.pivot_table(index="sector", columns="stat", values="value")
    pivot = pivot.reindex([s for s in NACE_LETTERS_ORDER if s in pivot.index])

    y_labels = [_short_label(s, n_words=4) for s in pivot.index]
    fig = go.Figure()
    fig.add_trace(go.Bar(
        y=y_labels, x=pivot.get("hourly_earnings", 0), name="Wages & salaries (hourly)",
        orientation="h", marker_color="#3182bd",
        hovertemplate="<b>%{y}</b> wages: €%{x:.2f}/hr<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        y=y_labels, x=pivot.get("hourly_other_labour_cost", 0), name="Employer SSC + other (hourly)",
        orientation="h", marker_color="#fc8d59",
        hovertemplate="<b>%{y}</b> non-wage: €%{x:.2f}/hr<extra></extra>",
    ))
    fig.update_layout(**_layout(
        title=f"Hourly labour cost composition by sector — {latest}",
        barmode="stack",
        xaxis_title="€/hour",
        yaxis=dict(autorange="reversed", tickfont=dict(size=10)),
    ))
    return fig_to_html(fig, div_id=div_id, height=520)


def sector_table(long_df: pd.DataFrame) -> list[dict]:
    """Sortable table data: latest level, 5y CAGR, share of total compensation."""
    d = long_df[long_df["sector"].isin(NACE_LETTERS_ORDER)].copy()
    latest = d["period"].max()
    five_yr_ago = latest - 20  # 20 quarters
    cost = d[d["stat"] == "hourly_labour_cost"].pivot_table(index="period", columns="sector", values="value")
    emp = d[d["stat"] == "employment"].pivot_table(index="period", columns="sector", values="value")
    hwk = d[d["stat"] == "weekly_paid_hours"].pivot_table(index="period", columns="sector", values="value")
    if five_yr_ago not in cost.index:
        five_yr_ago = cost.index[max(0, cost.index.get_loc(latest) - 20)]

    rows = []
    total_comp_latest = (cost.loc[latest] * emp.loc[latest] * hwk.loc[latest] * 52).sum()
    for sec in [s for s in NACE_LETTERS_ORDER if s in cost.columns]:
        latest_cost = cost.loc[latest, sec]
        if pd.isna(latest_cost):
            continue
        five_yr_cost = cost.loc[five_yr_ago, sec]
        cagr = (np.log(latest_cost) - np.log(five_yr_cost)) / 5 if five_yr_cost > 0 else None
        comp_sec = cost.loc[latest, sec] * emp.loc[latest, sec] * hwk.loc[latest, sec] * 52
        share = float(comp_sec / total_comp_latest) if total_comp_latest > 0 else None
        sector_label = d[d["sector"] == sec]["sector_label"].iloc[0]
        rows.append({
            "sector": sec,
            "label": sector_label,
            "hourly_cost": round(float(latest_cost), 2),
            "cagr_5y_pct": round(float(cagr) * 100, 2) if cagr is not None else None,
            "share_pct": round(share * 100, 2) if share is not None else None,
        })
    return rows


def sigma_chart(sigma_df: pd.DataFrame, *, div_id: str = "sigma-chart") -> dict:
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=sigma_df["period"].astype(str), y=sigma_df["sigma"],
        mode="lines+markers", line=dict(color="#08519c", width=2), marker=dict(size=4),
        hovertemplate="%{x}: σ=%{y:.3f}<extra></extra>",
    ))
    fig.update_layout(**_layout(
        title="Cross-sector dispersion of log hourly labour cost (σ)",
        yaxis_title="σ(log hourly cost)",
        xaxis_title=None,
    ))
    return fig_to_html(fig, div_id=div_id, height=360)


def stagnant_share_chart(share_df: pd.DataFrame, *, div_id: str = "stagnant-share") -> dict:
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=share_df["period"].astype(str), y=share_df["stagnant_share"] * 100,
        mode="lines", name="Stagnant sectors (H,I,P,Q,R,S)",
        line=dict(color="#b30000", width=2),
        hovertemplate="%{x}: %{y:.1f}%<extra>stagnant</extra>",
    ))
    fig.add_trace(go.Scatter(
        x=share_df["period"].astype(str), y=share_df["progressive_share"] * 100,
        mode="lines", name="Progressive sectors (C,J,K)",
        line=dict(color="#08519c", width=2),
        hovertemplate="%{x}: %{y:.1f}%<extra>progressive</extra>",
    ))
    fig.update_layout(**_layout(
        title="Share of total wagebill: stagnant vs progressive sectors",
        yaxis_title="% of total wagebill",
        xaxis_title=None,
    ))
    return fig_to_html(fig, div_id=div_id, height=360)


def baumol_scatter(sectors_records: list[dict], *, div_id: str = "baumol-scatter") -> dict:
    """Cross-section scatter: cost CAGR vs initial log cost (convergence test)."""
    df = pd.DataFrame(sectors_records)
    labels = [_short_label(s) for s in df["sector"]]
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["initial_log_hourly_cost"], y=df["cagr_hourly_cost"] * 100,
        mode="markers+text", text=df["sector"], textposition="top center",
        customdata=labels,
        marker=dict(size=11, color=[SECTOR_COLOURS.get(s, "#666") for s in df["sector"]]),
        hovertemplate="<b>%{customdata}</b><br>initial €exp(%{x:.2f}), CAGR=%{y:.2f}%<extra></extra>",
    ))
    if df.shape[0] >= 4:
        slope, intercept = np.polyfit(df["initial_log_hourly_cost"], df["cagr_hourly_cost"] * 100, 1)
        xs = np.linspace(df["initial_log_hourly_cost"].min(), df["initial_log_hourly_cost"].max(), 50)
        fig.add_trace(go.Scatter(x=xs, y=slope * xs + intercept, mode="lines",
                                 line=dict(color="#888", dash="dash"), name="OLS fit", hoverinfo="skip"))
    fig.update_layout(**_layout(
        title="β-convergence test: hourly labour cost CAGR vs initial level",
        xaxis_title="log(initial hourly labour cost, base period)",
        yaxis_title="annualised cost growth, % per year",
        showlegend=False,
    ))
    return fig_to_html(fig, div_id=div_id, height=460)


def hicp_services_goods_chart(df: pd.DataFrame, *,
                                div_id: str = "hicp-svc-gd") -> dict:
    """Replicates DFin Box 5 Figure 11A — services CPI vs goods CPI, rebased to 2000=100."""
    df = df.copy()
    df["category"] = df["category"].apply(lambda s: "Services" if "Services" in s else "Goods")
    pivot = df.pivot_table(index="period", columns="category", values="index_value")
    pivot = pivot.dropna(how="any").sort_index()
    base = pivot[pivot.index.year == 2000].iloc[0]
    indexed = pivot / base * 100

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=indexed.index.astype(str), y=indexed["Services"], name="Services",
        line=dict(color="#b30000", width=2),
        hovertemplate="%{x}: %{y:.1f}<extra>Services</extra>",
    ))
    fig.add_trace(go.Scatter(
        x=indexed.index.astype(str), y=indexed["Goods"], name="Goods",
        line=dict(color="#08519c", width=2),
        hovertemplate="%{x}: %{y:.1f}<extra>Goods</extra>",
    ))
    latest_svc = indexed["Services"].iloc[-1]
    latest_gd = indexed["Goods"].iloc[-1]
    fig.update_layout(**_layout(
        title=f"Services vs goods HICP, Ireland, 2000=100 "
              f"(latest: services {latest_svc:.0f}, goods {latest_gd:.0f})",
        yaxis_title="Index, 2000=100",
        xaxis_title=None,
    ))
    return fig_to_html(fig, div_id=div_id, height=400)


def hicp_services_breakdown_chart(df: pd.DataFrame, *,
                                    div_id: str = "hicp-coicop") -> dict:
    """Selected service categories rebased to 2015=100 (HICP native base)."""
    df = df.copy()
    df["period_str"] = df["period"].astype(str)
    fig = go.Figure()
    palette = ["#b30000", "#e34a33", "#fc8d59", "#fdbb84", "#08519c", "#3182bd",
               "#6baed6", "#74c476", "#525252"]
    cats = df.sort_values("period").groupby("category_code")
    for i, (code, group) in enumerate(cats):
        label = group["category_label"].iloc[0]
        latest = group.sort_values("period")["index_value"].iloc[-1]
        fig.add_trace(go.Scatter(
            x=group["period_str"], y=group["index_value"],
            name=f"{label} ({latest:.0f})",
            line=dict(color=palette[i % len(palette)], width=1.5),
            hovertemplate=f"<b>{label}</b><br>%{{x}}: %{{y:.1f}}<extra></extra>",
        ))
    fig.update_layout(**_layout(
        title="HICP service categories, Ireland, 2015=100",
        yaxis_title="Index",
        legend=dict(orientation="h", y=-0.3, x=0, font=dict(size=10)),
    ))
    return fig_to_html(fig, div_id=div_id, height=480)


def replication_coefficient_plot(results: list[dict], *,
                                   div_id: str = "rep-forest") -> dict:
    """Forest-style plot of paper β vs our β, with 95% CI bars from each.
    Two productivity measures (TFP, LP) per outcome.
    """
    fig = go.Figure()
    outcomes_order = ["price", "real_gva", "nom_gva", "hours", "wages"]
    label_map = {"price": "Price", "real_gva": "Real GVA",
                 "nom_gva": "Nominal GVA", "hours": "Hours", "wages": "Wages"}
    y_pos, ticks = [], []
    yi = 0
    for o in outcomes_order:
        for prod in ["TFP", "LP"]:
            r = next((x for x in results
                      if x["outcome_key"] == o and x["productivity"] == prod), None)
            if not r:
                continue
            y = -yi
            ticks.append((y, f"{label_map[o]} ~ {prod}"))
            yi += 1
            fig.add_trace(go.Scatter(
                x=[r["paper_beta"]], y=[y - 0.18],
                mode="markers", name="DFin-ESRI 2026",
                marker=dict(color="#08519c", size=10, symbol="diamond"),
                error_x=dict(type="data", array=[1.96 * r["paper_se"]],
                              color="#08519c", thickness=1.2, width=4),
                showlegend=(yi == 1),
                hovertemplate=f"<b>Paper</b><br>β = {r['paper_beta']:.3f} (SE {r['paper_se']:.3f})<extra></extra>",
            ))
            fig.add_trace(go.Scatter(
                x=[r["our_beta"]], y=[y + 0.18],
                mode="markers", name="This site",
                marker=dict(color="#b30000", size=10, symbol="circle"),
                error_x=dict(type="data", array=[1.96 * r["our_se"]],
                              color="#b30000", thickness=1.2, width=4),
                showlegend=(yi == 1),
                hovertemplate=f"<b>Our replication</b><br>β = {r['our_beta']:.3f} (SE {r['our_se']:.3f})<extra></extra>",
            ))

    fig.add_vline(x=0, line=dict(color="#888", width=1, dash="dot"))
    fig.update_layout(**_layout(
        title="Replication coefficients vs Department of Finance / ESRI (Jan 2026), 95% CI",
        xaxis_title="Coefficient β",
        yaxis=dict(tickmode="array",
                   tickvals=[t[0] for t in ticks],
                   ticktext=[t[1] for t in ticks],
                   showgrid=False, zeroline=False),
        legend=dict(orientation="h", y=1.08, x=0),
    ))
    return fig_to_html(fig, div_id=div_id, height=560)


def cross_country_lp_plot(df: pd.DataFrame, *,
                            div_id: str = "cross-country-lp") -> dict:
    """Hartwig-style cross-country comparison of the price~LP coefficient."""
    df = df.dropna(subset=["beta"]).sort_values("beta")
    fig = go.Figure()
    colors = ["#b30000" if c == "IE" else "#525252" for c in df["country"]]
    fig.add_trace(go.Bar(
        x=df["beta"], y=df["country"], orientation="h",
        marker=dict(color=colors),
        error_x=dict(type="data", array=1.96 * df["se"], color="#888"),
        hovertemplate="<b>%{y}</b><br>β = %{x:.3f}<extra></extra>",
    ))
    fig.add_vline(x=0, line=dict(color="#888", width=1, dash="dot"))
    fig.update_layout(**_layout(
        title="Price~LP coefficient by country (Baumol cost-disease test, 1997–2021)",
        xaxis_title="β (negative = Baumol mechanism present)",
        yaxis=dict(autorange="reversed", tickfont=dict(size=11)),
        showlegend=False,
    ))
    return fig_to_html(fig, div_id=div_id, height=460)


def shift_share_chart(actual_by_year: dict, fixed_share_means: dict, *,
                        div_id: str = "shift-share") -> dict:
    """Nordhaus growth-disease test: actual aggregate LP growth vs fixed-base-year
    counterfactuals. If older base years yield higher mean growth, that is
    Baumol-Nordhaus growth disease.
    """
    bases = sorted(fixed_share_means.keys())
    values = [fixed_share_means[b]["mean_growth"] for b in bases]
    actual_mean = actual_by_year["mean_growth"]
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=[str(b) for b in bases], y=values,
        marker_color="#3182bd",
        name="Counterfactual (fixed shares)",
        hovertemplate="Base year %{x}: %{y:.2f}%/yr<extra></extra>",
    ))
    fig.add_hline(y=actual_mean, line=dict(color="#b30000", dash="dash"),
                   annotation_text=f"Actual mean ({actual_mean:.2f}%/yr)",
                   annotation_position="top right")
    fig.update_layout(**_layout(
        title="Aggregate LP growth: actual vs fixed-base-year counterfactual (Ireland)",
        xaxis_title="Sector-share base year",
        yaxis_title="Mean annual LP growth, %",
    ))
    return fig_to_html(fig, div_id=div_id, height=380)


def baumol_prod_gap_scatter(sectors_records: list[dict], *,
                             div_id: str = "baumol-prod-gap") -> dict:
    """The headline Baumol test: cost growth vs productivity gap.

    x = (mean productivity CAGR across sectors) − (own productivity CAGR).
    y = own labour-cost CAGR. Positive slope = Baumol's prediction holds.
    """
    df = pd.DataFrame(sectors_records).dropna(subset=["prod_gap", "cagr_hourly_cost"])
    if df.empty:
        return fig_to_html(go.Figure(), div_id=div_id, height=300)
    labels = [_short_label(s) for s in df["sector"]]
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["prod_gap"] * 100, y=df["cagr_hourly_cost"] * 100,
        mode="markers+text", text=df["sector"], textposition="top center",
        customdata=labels,
        marker=dict(size=12, color=[SECTOR_COLOURS.get(s, "#666") for s in df["sector"]],
                    line=dict(width=0.5, color="#222")),
        hovertemplate=(
            "<b>%{customdata}</b><br>productivity gap: %{x:.2f} pp/yr"
            "<br>cost growth: %{y:.2f}%/yr<extra></extra>"
        ),
    ))
    if len(df) >= 4:
        slope, intercept = np.polyfit(df["prod_gap"] * 100, df["cagr_hourly_cost"] * 100, 1)
        xs = np.linspace(df["prod_gap"].min() * 100, df["prod_gap"].max() * 100, 50)
        fig.add_trace(go.Scatter(x=xs, y=slope * xs + intercept, mode="lines",
                                 line=dict(color="#08519c", dash="dash"),
                                 name=f"OLS fit (slope={slope:.2f})", hoverinfo="skip"))
    fig.update_layout(**_layout(
        title="Baumol test: cost growth vs productivity gap",
        xaxis_title="Productivity gap (mean economy-wide CAGR − own CAGR), pp/yr",
        yaxis_title="Hourly labour cost CAGR, %/yr",
        showlegend=True,
    ))
    return fig_to_html(fig, div_id=div_id, height=460)
