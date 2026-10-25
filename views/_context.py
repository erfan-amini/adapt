"""Per-run analysis context shared by every page.

`build_context()` reproduces the filter state and data pipeline that lived at
the top of the legacy main(): committed filter values in cv_* session keys,
the occupancy-filtered building table, the community aggregate, year and
scenario options, and the inline settings row renderer. Pages receive the
result as a SimpleNamespace and unpack what they need.
"""
from types import SimpleNamespace

import pandas as pd
import streamlit as st

from adapt_core import config as C
from adapt_core import fragility as _fragility
from adapt_core import ui
from adapt_core.bundle import discover_bundles
from adapt_core.helpers import filter_by_occupancy
from adapt_core.analysis import aggregate_filtered_data

BASELINE_YEAR_TO_DROP = 2025


def get_data_store():
    """Load every bundle once per session (load_bundle itself is cached on the
    files' fingerprints, so edits on disk are picked up automatically)."""
    return discover_bundles()


def ensure_location_state(available_locations, registry):
    """Initialise / validate the global location in session state."""
    ss = st.session_state
    if not available_locations:
        return None
    if "cv_location" not in ss or ss.cv_location not in available_locations:
        ss.cv_location = "Mastic Beach" if "Mastic Beach" in available_locations else available_locations[0]
        _apply_location_defaults(ss.cv_location, registry)
    if "cv_mobile" not in ss:
        _apply_location_defaults(ss.cv_location, registry)
    return ss.cv_location


def _apply_location_defaults(location_name, registry):
    cfg = ui.location_cfg(registry, location_name) or {}
    st.session_state.cv_mobile = bool(cfg.get("mobile_homes_dominated", location_name == "Pamunkey"))


def location_selector(available_locations, registry, key, label="📍 Location", help=None):
    """A location selectbox bound to the global cv_location."""
    ss = st.session_state
    if not available_locations:
        st.info("No location data found.")
        return None
    idx = available_locations.index(ss.cv_location) if ss.cv_location in available_locations else 0

    def _cb():
        new = ss[key]
        if new != ss.cv_location:
            ss.cv_location = new
            _apply_location_defaults(new, registry)

    def _fmt(name):
        cfg = ui.location_cfg(registry, name) or {}
        return f"{name}  🔒" if cfg.get("protected") and name not in ss.get("unlocked_locations", set()) else name

    st.selectbox(label, available_locations, index=idx, key=key, on_change=_cb, help=help, format_func=_fmt)
    return ss.cv_location


def build_context(data_store, available_locations, registry):
    ss = st.session_state
    selected_location = ensure_location_state(available_locations, registry)

    def _keyed_container(_key):
        try:
            return st.container(key=_key)
        except TypeError:
            return st.container()

    # ---- committed filter state + callbacks ----
    def _cb_occ():  ss.cv_occ = ss.w_occ
    def _cb_year(): ss.cv_year = ss.w_year
    def _cb_scn():  ss.cv_scn = ss.w_scn
    def _cb_dfe():  ss.cv_dfe = ss.w_dfe
    def _cb_zero(): ss.cv_showzero = ss.w_showzero

    mobile_raise_only = bool(ss.get("cv_mobile", False))
    _RAISE_ONLY_ACTIONS = ('No mitigation', 'Elevate')

    def _restrict_to_raise_only(df):
        if df is None or 'Action' not in getattr(df, 'columns', []):
            return df
        return df[df['Action'].isin(_RAISE_ONLY_ACTIONS)].copy()

    ss.setdefault("cv_occ", "All")
    if ss.cv_occ not in ("All", "Residential", "Non-Residential"):
        ss.cv_occ = "All"
    selected_occupancy = ss.cv_occ

    df_agg_raw = None
    df_buildings_raw = None
    loc_entry = None
    if selected_location and selected_location in data_store:
        loc_entry = data_store[selected_location]
        df_agg_raw = loc_entry.get('agg')
        df_buildings_raw = loc_entry.get('buildings')

    df_buildings = filter_by_occupancy(df_buildings_raw, selected_occupancy)

    if df_buildings is not None and 'TargetYear' in df_buildings.columns:
        df_buildings = df_buildings[df_buildings['TargetYear'] != BASELINE_YEAR_TO_DROP].copy()
    if df_agg_raw is not None and 'TargetYear' in df_agg_raw.columns:
        df_agg_raw = df_agg_raw[df_agg_raw['TargetYear'] != BASELINE_YEAR_TO_DROP].copy()

    preloaded_agg = None
    if loc_entry is not None and loc_entry.get('agg_by_occ'):
        preloaded_agg = loc_entry['agg_by_occ'].get(selected_occupancy)
        if preloaded_agg is not None and 'TargetYear' in preloaded_agg.columns:
            preloaded_agg = preloaded_agg[preloaded_agg['TargetYear'] != BASELINE_YEAR_TO_DROP].copy()

    available_years = [2040, 2055, 2060, 2100]
    if df_buildings is not None and 'TargetYear' in df_buildings.columns:
        available_years = sorted(df_buildings['TargetYear'].unique())
    elif df_agg_raw is not None and 'TargetYear' in df_agg_raw.columns:
        available_years = sorted(df_agg_raw['TargetYear'].unique())

    target_year_labels = {}
    if loc_entry is not None and isinstance(loc_entry.get('target_year_labels'), dict):
        target_year_labels = loc_entry['target_year_labels']

    def _format_target_year(y):
        label = target_year_labels.get(int(y), str(int(y)))
        if label != str(int(y)) and label != 'Potential':
            return f"{label} ({int(y)})"
        return str(int(y))

    _default_year = available_years[0] if len(available_years) > 0 else None
    if len(available_years) > 1:
        for _y in available_years:
            if target_year_labels.get(int(_y)) not in (None, 'Potential'):
                _default_year = _y
                break
    if "cv_year" not in ss:
        ss.cv_year = _default_year
    if ss.cv_year not in list(available_years):
        ss.cv_year = _default_year
    target_year = ss.cv_year

    available_scenarios = ['50th-percentile', '90th-percentile']
    if df_buildings is not None and 'SLR' in df_buildings.columns:
        available_scenarios = sorted(df_buildings['SLR'].unique())
    elif df_agg_raw is not None and 'SLR' in df_agg_raw.columns:
        available_scenarios = sorted(df_agg_raw['SLR'].unique())
    if "cv_scn" not in ss:
        ss.cv_scn = available_scenarios[0] if available_scenarios else '50th-percentile'
    if ss.cv_scn not in list(available_scenarios):
        ss.cv_scn = available_scenarios[0] if available_scenarios else '50th-percentile'
    scenario = ss.cv_scn

    if df_buildings is not None and 'DFE_Status' in df_buildings.columns:
        fp_options = df_buildings['DFE_Status'].dropna().unique().tolist()
    else:
        fp_options = []
    if "cv_dfe" not in ss:
        ss.cv_dfe = list(fp_options)
    ss.cv_dfe = [d for d in ss.cv_dfe if d in fp_options]
    if not ss.cv_dfe and fp_options:
        ss.cv_dfe = list(fp_options)
    dfe_filter = ss.cv_dfe if fp_options else None

    ss.setdefault("cv_showzero", True)
    show_zero_damage = ss.cv_showzero

    location_name = selected_location if selected_location else ""
    occupancy_label = selected_occupancy if selected_occupancy != "All" else "All Buildings"

    # ---- community aggregate ----
    df_agg = None
    if preloaded_agg is not None and not preloaded_agg.empty:
        df_agg = preloaded_agg.copy()
    elif df_buildings is not None and len(df_buildings):
        agg_frames = []
        for yr in df_buildings['TargetYear'].unique():
            for scn in df_buildings['SLR'].unique():
                agg_df = aggregate_filtered_data(df_buildings, yr, scn)
                if agg_df is not None:
                    agg_frames.append(agg_df)
        if agg_frames:
            df_agg = pd.concat(agg_frames, ignore_index=True)

    # ---- inline settings row ----
    def _occ_fmt(x):
        return ("\U0001f3d8️\U0001f3e2 All Buildings" if x == "All"
                else "\U0001f3d8️ Residential" if x == "Residential"
                else "\U0001f3e2 Non-Residential")

    def _scn_fmt(x):
        return C.SCENARIO_PRETTY.get(x, x)

    def render_inline_settings(items):
        items = [i for i in items if (i != "dfe" or fp_options)]
        if not items:
            return
        cols = st.columns(len(items))
        for _col, name in zip(cols, items):
            with _col:
                if name == "occ":
                    ss.w_occ = ss.cv_occ
                    st.selectbox("\U0001f3e0 Occupancy", ["All", "Residential", "Non-Residential"],
                                 key="w_occ", on_change=_cb_occ, format_func=_occ_fmt)
                elif name == "year":
                    ss.w_year = ss.cv_year
                    st.selectbox("\U0001f4c5 Target Year", available_years,
                                 key="w_year", on_change=_cb_year, format_func=_format_target_year)
                elif name == "scn":
                    ss.w_scn = ss.cv_scn
                    st.selectbox("\U0001f30a SLR Scenario", available_scenarios,
                                 key="w_scn", on_change=_cb_scn, format_func=_scn_fmt)
                elif name == "dfe":
                    ss.w_dfe = ss.cv_dfe
                    st.multiselect("DFE status", fp_options, key="w_dfe", on_change=_cb_dfe)
                elif name == "showzero":
                    ss.w_showzero = ss.cv_showzero
                    st.checkbox("Show $0 buildings", key="w_showzero", on_change=_cb_zero)

    def _location_blurb(_loc):
        cfg = ui.location_cfg(registry, _loc) if _loc else None
        if cfg and cfg.get("blurb"):
            return cfg["blurb"]
        return ("A low-lying coastal community increasingly exposed to rising sea level and more "
                "frequent tidal and storm-surge flooding.")

    def render_fragility_curves(building_row=None, ctx="frag"):
        _fragility.bind_session_state(ss)
        return _fragility.render_fragility_curves(building_row=building_row, ctx=ctx)

    return SimpleNamespace(
        ss=ss, registry=registry, data_store=data_store, available_locations=available_locations,
        selected_location=selected_location, location_name=location_name,
        selected_occupancy=selected_occupancy, occupancy_label=occupancy_label,
        df_buildings=df_buildings, df_buildings_raw=df_buildings_raw, df_agg=df_agg, df_agg_raw=df_agg_raw,
        loc_entry=loc_entry, preloaded_agg=preloaded_agg, available_years=available_years,
        available_scenarios=available_scenarios, target_year=target_year, scenario=scenario,
        dfe_filter=dfe_filter, fp_options=fp_options, show_zero_damage=show_zero_damage,
        mobile_raise_only=mobile_raise_only, _restrict_to_raise_only=_restrict_to_raise_only,
        _RAISE_ONLY_ACTIONS=_RAISE_ONLY_ACTIONS, _format_target_year=_format_target_year,
        _scn_fmt=_scn_fmt, _occ_fmt=_occ_fmt, render_inline_settings=render_inline_settings,
        _keyed_container=_keyed_container, target_year_labels=target_year_labels,
        BASELINE_YEAR_TO_DROP=BASELINE_YEAR_TO_DROP, render_fragility_curves=render_fragility_curves,
        _location_blurb=_location_blurb, _cb_dfe=_cb_dfe, _cb_zero=_cb_zero,
    )


def guard_buildings(ctx):
    """Stop the page with a friendly message when there is nothing to show."""
    if not ctx.available_locations:
        st.error("No location data found. Add a results bundle under `data/<Location>/` "
                 "or run the model from the Developer module.")
        st.stop()
    if ctx.df_buildings is None or len(ctx.df_buildings) == 0:
        st.warning(f"No {ctx.selected_occupancy.lower()} buildings found in the data for {ctx.selected_location}.")
        st.stop()
