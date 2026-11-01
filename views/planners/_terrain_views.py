"""Flood map and Road map views of the Dynamic maps page.

Both views draw a bathtub flood layer (USGS 3DEP terrain, water level =
present-day flood level + modeled sea-level rise for the selected year and
scenario) under the interactive map. The Flood map colors buildings by
whether the water reaches the ground or the first floor; the Road map
classifies OpenStreetMap roads as flooded, cut off, or dry.
"""
import base64
import io
import math
import os

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from adapt_core.terrain import (WS_BINS_FT, WS_COLORS_BLUE, _cached_dem_roi, _cached_local_roads,
                                _cached_osm_roads, _ROAD_STYLES, fdem)

# Present-day flood levels offered in the view: settings key, short label, help
LEVELS = [
    ("mhhw_ft",    "High tide",  "Mean higher high water, ft NAVD88."),
    ("monthly_ft", "Monthly",    "A level reached roughly monthly, ft NAVD88."),
    ("annual_ft",  "Annual",     "Level reached most years: the median annual-maximum water level in the first "
                                 "model year."),
    ("pct10_ft",   "10% chance", "1-in-10-year level: the 90th percentile of the annual maximum in the first "
                                 "model year, unless set in the community settings."),
    ("pct1_ft",    "1% chance",  "1-in-100-year level: the 99th percentile of the annual maximum in the first "
                                 "model year, unless set in the community settings."),
]
DETAIL = {"Standard (10 m)": (10.0, 700), "Fine (5 m)": (5.0, 1000), "Finer (3 m)": (3.0, 1300)}


def _lvl(df, year, col):
    if df is None or df.empty or col not in df.columns:
        return None
    i = (df["Year"] - year).abs().idxmin()
    return float(df.loc[i, col])


def base_year(loc_entry):
    wl = (loc_entry or {}).get("water_levels") or {}
    ref = wl.get("50th-percentile")
    if ref is None:
        for k, v in wl.items():
            if not k.endswith("_mc"):
                ref = v
                break
    return int(ref["Year"].min()) if (ref is not None and not ref.empty) else 2025


def level_defaults(loc_entry, cfg):
    """Default present-day levels (ft NAVD88): the community settings first,
    then the water-level model's percentiles; 0 means not set."""
    wl = (loc_entry or {}).get("water_levels") or {}
    ref = wl.get("50th-percentile")
    if ref is None:
        for k, v in wl.items():
            if not k.endswith("_mc"):
                ref = v
                break
    by = base_year(loc_entry)
    fl = (cfg or {}).get("flood_levels") or {}
    out = {}
    for key, _lbl, _h in LEVELS:
        v = fl.get(key)
        if v is None or not np.isfinite(float(v)) or float(v) <= 0:
            v = {"annual_ft": _lvl(ref, by, "P50"), "pct10_ft": _lvl(ref, by, "P90"),
                 "pct1_ft": _lvl(ref, by, "P99")}.get(key)
        out[key] = round(float(v), 2) if (v is not None and np.isfinite(float(v))) else 0.0
    return out


def slr_ft(loc_entry, scenario, year):
    """Sea-level rise since the first model year: change in the median annual-maximum water level."""
    wl = (loc_entry or {}).get("water_levels") or {}
    df = wl.get(scenario)
    if df is None or df.empty:
        return 0.0
    a, b = _lvl(df, int(year), "P50"), _lvl(df, base_year(loc_entry), "P50")
    return (a - b) if (a is not None and b is not None) else 0.0


def roi(loc_entry):
    """Map area: the whole inventory plus a 350 m margin (rounded, so the terrain cache hits)."""
    attrs = (loc_entry or {}).get("bldg_attrs")
    lon = pd.to_numeric(attrs["longitude"], errors="coerce").to_numpy(dtype=float)
    lat = pd.to_numeric(attrs["latitude"], errors="coerce").to_numpy(dtype=float)
    lon, lat, _ = fdem.maybe_swap_lonlat(lon, lat)
    bb = fdem.roi_from_lonlat(lon, lat, buffer_m=350.0)
    return tuple(round(v, 5) for v in bb), (float(np.nanmean(lon)), float(np.nanmean(lat)))


def terrain(bbox_r, res_m):
    """(Z metres NAVD88, extent) from the cached USGS 3DEP read."""
    return _cached_dem_roi(bbox_r, res_m)


@st.cache_data(show_spinner=False, max_entries=24)
def depth_layer(bbox_r, res_m, wl_ft, opacity=0.78):
    """Mapbox image layer of the bathtub depth at `wl_ft` (blue ramp, dry transparent)."""
    from PIL import Image
    Zm, ext = _cached_dem_roi(bbox_r, res_m)
    depth = fdem.bathtub_depth_ft(Zm, wl_ft, mask_water=True)
    rgba = fdem.depth_to_rgba(depth, alpha=230, colors=WS_COLORS_BLUE)
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, format="PNG", optimize=True)
    uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    lon_min, lat_min, lon_max, lat_max = ext
    lat_m = (lat_max - lat_min) * 111320.0
    lon_m = (lon_max - lon_min) * 111320.0 * math.cos(math.radians(0.5 * (lat_min + lat_max)))
    cell_m2 = (lat_m / Zm.shape[0]) * (lon_m / Zm.shape[1])
    flooded_km2 = float(np.isfinite(depth).sum() * cell_m2 / 1e6)
    layer = {"sourcetype": "image", "source": uri, "opacity": opacity, "below": "traces",
             "coordinates": [[lon_min, lat_max], [lon_max, lat_max], [lon_max, lat_min], [lon_min, lat_min]]}
    return layer, flooded_km2, ext


def add_depth_legend(fig):
    """Legend-only entries for the depth bins."""
    labels = ["0 to 1 ft", "1 to 2 ft", "2 to 3 ft", "3 to 4 ft", "4 to 5 ft", "5 ft and more"]
    for (r, g, b), lab in zip(WS_COLORS_BLUE, labels):
        fig.add_trace(go.Scattermapbox(
            lat=[None], lon=[None], mode="markers",
            marker=dict(size=12, color=f"rgb({r},{g},{b})", symbol="circle"),
            name=f"Water {lab}", showlegend=True, hoverinfo="skip"))


def add_flood_buildings(fig, df_map, wl_ft, point_scale, ring_fn, ring_legend_fn, hover_header):
    """Buildings colored by what the water reaches at this level. Returns counts."""
    ground = pd.to_numeric(df_map.get("ground_elevation"), errors="coerce").values.astype(float)
    ffe = pd.to_numeric(df_map.get("FFE_ft"), errors="coerce").values.astype(float)
    above_ground = wl_ft - ground
    above_ffe = wl_ft - ffe
    cat = np.full(len(df_map), 0, dtype=int)           # 0 dry, 1 water at building, 2 first floor flooded
    cat[(above_ground > 0)] = 1
    cat[(above_ffe > 0)] = 2
    cat[~np.isfinite(ground) & ~np.isfinite(ffe)] = 0

    def _ft(v):
        return "n/a" if not np.isfinite(v) else f"{v:+.1f} ft"
    hover = []
    for i, row in enumerate(df_map.itertuples(index=False)):
        h = hover_header(row)
        h += (f"Water level <b>{wl_ft:.2f} ft</b> NAVD88<br>"
              f"Ground {ground[i]:.1f} ft · first floor {ffe[i]:.1f} ft<br>"
              f"Water vs ground: {_ft(above_ground[i])} · vs first floor: {_ft(above_ffe[i])}")
        hover.append([h, int(row.id)])
    df = df_map.copy()
    df["_cat"] = cat
    df["_hd"] = hover
    specs = [(0, "Dry at this level", "#22c55e"), (1, "Water reaches the lot, below the first floor", "#f59e0b"),
             (2, "First floor flooded", "#dc2626")]
    counts = {}
    for c, label, color in specs:
        sub = df[df["_cat"] == c]
        counts[c] = int(len(sub))
        if len(sub) == 0:
            fig.add_trace(go.Scattermapbox(lat=[None], lon=[None], mode="markers",
                                           marker=dict(size=8 * point_scale, color=color),
                                           name=f"{label} ({0})", showlegend=True, hoverinfo="skip"))
            continue
        ring_fn(fig, sub, ring_size=13 * point_scale)
        fig.add_trace(go.Scattermapbox(
            lat=sub["latitude"], lon=sub["longitude"], mode="markers",
            marker=dict(size=8 * point_scale, color=color, opacity=0.95),
            hovertemplate="%{customdata[0]}<extra></extra>", customdata=list(sub["_hd"]),
            name=f"{label} ({len(sub)})"))
    ring_legend_fn(fig)
    return counts


def roads(loc_entry, bbox_r):
    """OpenStreetMap roads for the map area: the saved file first, then Overpass."""
    slug = (loc_entry or {}).get("location_slug")
    folder = (loc_entry or {}).get("data_folder", ".")
    local = os.path.join(folder, f"{slug}_osm_roads.json") if slug else None
    rd = None
    if local and os.path.exists(local):
        rd = _cached_local_roads(local, os.path.getmtime(local), bbox_r)
    if rd is None:
        rd = _cached_osm_roads(bbox_r)
    return rd


@st.cache_data(show_spinner=False, max_entries=24)
def classified_roads(sig, bbox_r, res_m, wl_ft, access_on, entry_on, entry_manual, _loc_entry):
    """Road segments [(p0, p1, status)] and percentages for one water level.
    `sig` names the community so the cache key is unique per inventory."""
    Zm, ext = _cached_dem_roi(bbox_r, res_m)
    rd = roads(_loc_entry, bbox_r)
    entry, note = None, None
    if entry_on:
        txt = (entry_manual or "").strip()
        if txt:
            try:
                v = [float(x) for x in txt.replace(";", " ").replace(",", " ").split()]
                if len(v) >= 2:
                    a, b = v[0], v[1]
                    elon, elat = (a, b) if (a < 0 and b > 0) else (b, a)
                    try:
                        entry = fdem.detect_main_entrance(rd, ext, (elon, elat))
                    except Exception:
                        entry = None
                    entry = entry or [(elon, elat)]
            except Exception:
                entry = None
        if entry is None:
            try:
                _, (clon, clat) = roi(_loc_entry)
                entry = fdem.detect_main_entrance(rd, ext, (clon, clat))
            except Exception:
                entry = None
        if entry:
            note = (f"The main entrance near {entry[0][1]:.5f}, {entry[0][0]:.5f} is treated as a gateway: it can "
                    "flood without cutting the whole area off.")
        else:
            note = "No single main entrance could be detected, so the gateway option has no effect."
    segs, cnt = fdem.classify_roads_access(Zm, ext, rd, wl_ft, source="largest",
                                          entry_points=entry, entrance_reach_m=450.0)
    if not access_on:
        segs = [(p0, p1, 0 if s == 1 else s) for (p0, p1, s) in segs]
        cnt = dict(cnt)
        cnt["pct_dry"] = cnt.get("pct_dry", 0.0) + cnt.get("pct_inacc", 0.0)
        cnt["pct_inacc"] = 0.0
    return segs, dict(cnt), len(rd), note


def add_road_traces(fig, segs, counts, access_on):
    """Three line traces (dry, inaccessible, flooded), drawn in that order."""
    pct = {0: counts.get("pct_dry", 0.0), 1: counts.get("pct_inacc", 0.0), 2: counts.get("pct_flood", 0.0)}
    for status in (0, 1, 2):
        if status == 1 and not access_on:
            continue
        label, color, width = _ROAD_STYLES[status]
        lons, lats = [], []
        for p0, p1, s in segs:
            if s != status:
                continue
            lons += [p0[0], p1[0], None]
            lats += [p0[1], p1[1], None]
        if not lons:
            continue
        fig.add_trace(go.Scattermapbox(
            lat=lats, lon=lons, mode="lines", line=dict(color=color, width=width),
            hoverinfo="skip", name=f"{label} ({pct[status]:.0f}% of road length)"))


def add_context_buildings(fig, df_map, point_scale):
    fig.add_trace(go.Scattermapbox(
        lat=df_map["latitude"], lon=df_map["longitude"], mode="markers",
        marker=dict(size=4.5 * point_scale, color="rgba(71,85,105,0.55)"),
        hoverinfo="skip", name="Buildings", showlegend=True))
