"""Households and Businesses > Neighborhood maps.

Flood risk summarized by hexagon, so residents can see how risk varies
across the community without reading any single building off the map.
Hexagons with fewer than HEX_MIN_BUILDINGS buildings are not drawn, and the
number of buildings in a hexagon is never shown.
"""
import math

import numpy as np
import pandas as pd
import streamlit as st

from adapt_core import config as C
from adapt_core import ui
from adapt_core.analysis import compute_flood_occurrences, prepare_map_data
from adapt_core.helpers import fmt_money_short
from adapt_core.mapexport import _basemap_config

CHEAP_CUTOFF = 20000.0   # P90 at or below this after a low-cost retrofit counts as protected

_METRICS = {
    "Homes with damage": dict(
        key="damaged", kind="pct", scale="YlOrRd",
        help="Share of buildings in the hexagon whose expected (median) cumulative damage through the "
             "selected year is above $0 with no action."),
    "Damage per home": dict(
        key="dmg", kind="money", scale="YlOrRd",
        help="Average over the hexagon's buildings of the expected (median) cumulative damage through the "
             "selected year with no action."),
    "Low-cost fix is enough": dict(
        key="cheap", kind="pct", scale="YlGn",
        help="Among damaged buildings in the hexagon, the share where raising the utilities or "
             "wet-floodproofing the basement brings the high-outcome (P90) damage to $20k or less."),
    "Elevation is needed": dict(
        key="elev", kind="pct", scale="OrRd",
        help="Among damaged buildings in the hexagon, the share where neither low-cost retrofit is enough "
             "and only elevating the home brings the high-outcome (P90) damage to $20k or less."),
    "Flood frequency": dict(
        key="floods", kind="count", scale="PuBu",
        help="Average number of years, from 2025 through the selected year, in which the water reaches the "
             "first floor, averaged over the 1,000 simulated futures and over the hexagon's buildings."),
    "Below the DFE": dict(
        key="under", kind="pct", scale="Purples",
        help="Share of buildings whose first floor is below the Design Flood Elevation (BFE + 2 ft)."),
}


def _building_metrics(df_map, loc_entry, location_name, scenario, target_year):
    """One row per building with the metric columns."""
    d = pd.DataFrame({"id": df_map["id"].astype(int).values,
                      "latitude": pd.to_numeric(df_map["latitude"], errors="coerce").values,
                      "longitude": pd.to_numeric(df_map["longitude"], errors="coerce").values})
    p50 = pd.to_numeric(df_map.get("No mitigation_P50"), errors="coerce").fillna(0).values
    p90 = pd.to_numeric(df_map.get("No mitigation_P90"), errors="coerce").fillna(0).values
    d["damaged"] = (p50 > 0.5).astype(float) * 100.0
    d["dmg"] = p50
    dmg90 = p90 > CHEAP_CUTOFF

    def col(name):
        if name in df_map.columns:
            v = pd.to_numeric(df_map[name], errors="coerce").values
            return np.where(np.isnan(v), p90, v)
        return p90.copy()
    cheap = (col("Raise Utilities_P90") <= CHEAP_CUTOFF) | (col("WFP B_P90") <= CHEAP_CUTOFF)
    elev = (col("Elevate_P90") <= CHEAP_CUTOFF) & ~cheap
    d["cheap"] = np.where(dmg90, cheap.astype(float) * 100.0, np.nan)
    d["elev"] = np.where(dmg90, elev.astype(float) * 100.0, np.nan)
    if "DFE_Status" in df_map.columns:
        d["under"] = df_map["DFE_Status"].fillna("").astype(str).str.lower().str.contains("under").values * 100.0
    else:
        d["under"] = np.nan

    # Flood occurrences through the horizon (cached per location/scenario/year)
    d["floods"] = np.nan
    wl = (loc_entry or {}).get("water_levels") or {}
    mc = wl.get(f"{scenario}_mc")
    attrs = (loc_entry or {}).get("bldg_attrs")
    if mc is not None and attrs is not None and "FFE_ft" in attrs.columns:
        ffe_by_id = {int(i): (float(f) if pd.notna(f) else None) for i, f in zip(attrs["id"], attrs["FFE_ft"])}
        occ = compute_flood_occurrences(mc, ffe_by_id, (location_name, scenario, int(target_year)))
        if occ is not None and len(occ):
            d["floods"] = d["id"].map(occ.set_index("id")["occ_mean"])
    return d.dropna(subset=["latitude", "longitude"])


def _nx_hexagons(d, size_m):
    lat_c = float(d["latitude"].mean())
    lon_span = float(d["longitude"].max() - d["longitude"].min())
    span_m = lon_span * 111320.0 * math.cos(math.radians(lat_c))
    return int(max(4, min(80, round(span_m / max(size_m, 40.0)))))


def _zoom(d):
    """Web-Mercator zoom that frames the buildings (same rule as the dynamic maps)."""
    try:
        lat, lon = d["latitude"].values, d["longitude"].values
        lon_span = max((float(np.nanmax(lon)) - float(np.nanmin(lon))) * 1.25, 1e-3)
        lat_span = max((float(np.nanmax(lat)) - float(np.nanmin(lat))) * 1.25, 1e-3)
        cl = max(math.cos(math.radians(float(np.nanmean(lat)))), 1e-6)
        z = min(math.log2(360.0 / lon_span), math.log2((360.0 / (lat_span * 1.4)) * cl))
        return float(max(min(z + 1.2, 16.5), 11.0))
    except Exception:
        return 13.0


def _fmt(kind, v):
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "n/a"
    if kind == "pct":
        return f"{v:.0f}%"
    if kind == "money":
        return fmt_money_short(v)
    return f"{v:.1f}"


def _hex_figure(d, vals, nx, metric_label, kind, scale, style, layers):
    """Choropleth of hexagons: counts from plotly's hexbin, metric = nanmean per hexagon."""
    from plotly.figure_factory._hexbin_mapbox import _compute_wgs84_hexbin, _hexagons_to_geojson
    import plotly.graph_objects as go
    lat, lon = d["latitude"].values.astype(float), d["longitude"].values.astype(float)
    lat_r, lon_r = np.array([lat.min(), lat.max()]), np.array([lon.min(), lon.max()])
    hlats, hlons, ids, counts = _compute_wgs84_hexbin(lat=lat, lon=lon, lat_range=lat_r, lon_range=lon_r,
                                                      color=None, nx=nx, agg_func=np.mean,
                                                      min_count=C.HEX_MIN_BUILDINGS)
    SENTINEL = -1.0e9

    def _agg(a):
        a = np.asarray(a, dtype=float)
        a = a[np.isfinite(a)]
        return float(a.mean()) if a.size else SENTINEL
    _, _, ids2, z = _compute_wgs84_hexbin(lat=lat, lon=lon, lat_range=lat_r, lon_range=lon_r, color=vals,
                                          nx=nx, agg_func=_agg, min_count=C.HEX_MIN_BUILDINGS)
    z = np.asarray(z, dtype=float)
    zs = pd.Series(z, index=list(ids2))
    z = zs.reindex(list(ids)).values
    z = np.where(z <= SENTINEL / 2, np.nan, z)
    counts = np.asarray(counts, dtype=float)
    geojson = _hexagons_to_geojson(hlats, hlons, ids)
    hover = [_fmt(kind, v) for v in z]
    fig = go.Figure()
    ok = np.isfinite(z)
    if (~ok).any():
        fig.add_trace(go.Choroplethmapbox(
            geojson=geojson, locations=np.asarray(ids)[~ok], z=np.zeros(int((~ok).sum())),
            colorscale=[[0, "#e2e8f0"], [1, "#e2e8f0"]], showscale=False, marker_opacity=0.55,
            marker_line=dict(color="rgba(255,255,255,0.7)", width=0.8),
            hovertemplate="No damaged buildings here<extra></extra>",
            name="n/a"))
    fig.add_trace(go.Choroplethmapbox(
        geojson=geojson, locations=np.asarray(ids)[ok], z=z[ok], colorscale=scale,
        zmin=0 if kind != "money" else None, zmax=100 if kind == "pct" else None,
        marker_opacity=0.74, marker_line=dict(color="rgba(255,255,255,0.7)", width=0.8),
        customdata=np.array(hover, dtype=object)[ok],
        hovertemplate=f"<b>{metric_label}</b>: %{{customdata}}<extra></extra>",
        colorbar=dict(title=dict(text=("%" if kind == "pct" else "$" if kind == "money" else "years"), side="right"),
                      thickness=14, len=0.6, x=0.99, tickprefix="$" if kind == "money" else None,
                      separatethousands=True),
        name=metric_label))
    fig.update_layout(
        mapbox=dict(style=style, layers=layers, zoom=_zoom(d),
                    center=dict(lat=float(np.nanmean(lat)), lon=float(np.nanmean(lon)))),
        height=620, margin=dict(l=0, r=0, t=0, b=0), showlegend=False,
    )
    return fig, z, counts


def render(ctx):
    loc_entry = ctx.loc_entry or {}
    location_name = ctx.location_name
    ui.page_header(
        "Neighborhood maps",
        "How flood risk varies across the community, summarized by hexagon. Each hexagon combines several "
        "buildings, so the map shows neighborhoods, not individual homes. For your own home, use "
        "<b>My home</b>.",
        module_key="community", location=location_name,
    )
    ctx.render_inline_settings(["occ", "year", "scn"])
    df_buildings = ctx.df_buildings
    if df_buildings is None or len(df_buildings) == 0:
        st.info("No results are loaded for this community yet.")
        return
    df_map = prepare_map_data(df_buildings, ctx.target_year, ctx.scenario)
    if df_map is None or len(df_map) == 0:
        st.warning("No buildings match the current filters.")
        return

    with ctx._keyed_container("hex_metric_row"):
        metric_label = st.segmented_control(
            "What to show", list(_METRICS), default=list(_METRICS)[0], selection_mode="single",
            key="hex_metric", label_visibility="collapsed",
        ) or st.session_state.get("_hex_metric_last", list(_METRICS)[0])
    st.session_state["_hex_metric_last"] = metric_label
    m = _METRICS[metric_label]

    adv = st.toggle("⚙️ Map options", key="hex_adv")
    size_m, basemap = C.HEX_DEFAULT_SIZE_M, "Light"
    with ctx._keyed_container("hex_adv_box"):
        if not adv:
            st.markdown("<style>.st-key-hex_adv_box { display:none !important; }</style>", unsafe_allow_html=True)
        a1, a2 = st.columns([1.2, 1])
        with a1:
            size_m = st.slider("Hexagon size (m)", 100, 400, C.HEX_DEFAULT_SIZE_M, 20, key="hex_size",
                               help="Width of each hexagon. Larger hexagons combine more buildings.")
        with a2:
            basemap = st.segmented_control("Basemap", ["Light", "Streets", "Aerial"], default="Light",
                                           selection_mode="single", key="hex_basemap") or "Light"

    d = _building_metrics(df_map, loc_entry, location_name, ctx.scenario, ctx.target_year)
    nx = _nx_hexagons(d, size_m)
    style, layers = _basemap_config(basemap)
    kind = m["kind"]

    # Community-wide figures for the same selection
    k1, k2, k3, k4 = st.columns(4)
    with k1:
        ui.kpi("Buildings", f"{len(d):,}")
    with k2:
        ui.kpi("With expected damage", f"{np.nanmean(d['damaged']):.0f}%")
    with k3:
        ui.kpi("Expected damage per home", fmt_money_short(float(np.nanmean(d['dmg']))))
    with k4:
        v = np.nanmean(d["cheap"]) if np.isfinite(d["cheap"]).any() else np.nan
        ui.kpi("Low-cost retrofit enough", "n/a" if not np.isfinite(v) else f"{v:.0f}% of damaged")

    vals = d[m["key"]].values.astype(float)
    if not np.isfinite(vals).any():
        st.info("This measure is not available for the current selection.")
        return
    fig, z, counts = _hex_figure(d, vals, nx, metric_label, kind, m["scale"], style, layers)
    st.plotly_chart(fig, use_container_width=True, config={"scrollZoom": True})
    st.caption(
        f"{metric_label}: {m['help']} Hexagons are about {size_m} m across (gray = no damaged buildings); "
        f"{ctx.target_year}, {'median' if ctx.scenario == '50th-percentile' else 'high'} sea-level rise. "
        "Hover a hexagon for its value."
    )
