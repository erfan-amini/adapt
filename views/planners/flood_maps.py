import os
import math
import re as _re
import numpy as np
import pandas as pd
import streamlit as st
import streamlit.components.v1 as _components
import plotly.express as px
import plotly.graph_objects as go
from types import SimpleNamespace
from adapt_core.terrain import (_cached_basemap, _cached_dem_roi, fdem)


def render(ctx):
    available_years = ctx.available_years
    data_store = ctx.data_store
    df_buildings = ctx.df_buildings
    loc_entry = ctx.loc_entry
    selected_location = ctx.selected_location
    ss = ctx.ss
    from adapt_core import ui as _ui
    _ui.page_header(
        "Flood maps",
        "Bathtub inundation maps for water levels you specify. Enter present-day flood levels (ft NAVD88); "
        "the app adds each scenario's projected sea-level rise for every planning horizon and maps the "
        "resulting depth. Terrain is the USGS 3DEP 1/3 arc-second (~10 m) DEM, read on demand for this area.",
        module_key="planners", location=ctx.location_name,
    )
    _fl_ok = (selected_location in data_store and df_buildings is not None)
    _has_xy = (
        df_buildings is not None
        and {'longitude', 'latitude'}.issubset(df_buildings.columns)
        and df_buildings['latitude'].notna().any()
        and df_buildings['longitude'].notna().any()
    )

    try:
        import rasterio as _rio  # noqa: F401
        _rio_ok = True
    except Exception:
        _rio_ok = False

    if not _fl_ok:
        st.info("Select a location with building data to generate flood maps.")
    elif not _has_xy:
        st.warning("This location has no per-building coordinates, so the map extent can't be determined.")
    elif not _rio_ok:
        st.error(
            "Flood maps need the **rasterio** package to read the terrain. "
            "Add `rasterio` to the app's requirements.txt and redeploy."
        )
    else:
        _wl = loc_entry.get('water_levels', {}) if loc_entry else {}
        # SLR scenario keys present in the bundle (exclude raw MC sheets)
        _scn_keys = [k for k in _wl.keys() if not k.endswith('_mc')]
        _scn_label = {
            '50th-percentile': 'Intermediate-High SLR (50th pct)',
            '90th-percentile': 'High SLR (90th pct)',
        }
        _scn_pretty = lambda k: _scn_label.get(k, k)
        # Present-day prefill comes from a reference scenario (prefer 50th).
        _ref = '50th-percentile' if '50th-percentile' in _wl else (_scn_keys[0] if _scn_keys else None)
        _ref_df = _wl.get(_ref)
        _base_year = int(_ref_df['Year'].min()) if (_ref_df is not None and not _ref_df.empty) else 2025

        def _lvl(df, year, col):
            if df is None or df.empty or col not in df.columns:
                return None
            i = (df['Year'] - year).abs().idxmin()
            return float(df.loc[i, col])

        def _slr(scn_key, year):
            df = _wl.get(scn_key)
            a, b = _lvl(df, year, 'P50'), _lvl(df, _base_year, 'P50')
            return (a - b) if (a is not None and b is not None) else 0.0

        _pf = {
            'annual': _lvl(_ref_df, _base_year, 'P50'),
            'ten':    _lvl(_ref_df, _base_year, 'P90'),
            'one':    _lvl(_ref_df, _base_year, 'P99'),
        }
        if not _scn_keys:
            st.warning(
                "No Monte-Carlo water-level data found for this location, so the three flood types "
                "aren't prefilled and sea-level rise can't be derived (future levels will equal what "
                "you enter). You can still enter levels manually."
            )

        st.markdown(
            f"**Present-day base levels** (ft NAVD88) - the same for both SLR scenarios; future "
            f"levels add each scenario's sea-level rise. Tick the levels to map and edit any value. "
            f"Annual / 10% / 1% are prefilled from the {_base_year} water-level percentiles where available."
        )

        _is_pamunkey = (selected_location == "Pamunkey")
        if _is_pamunkey:
            # Pamunkey: HTF / 10% / 1% are selected by default with project values.
            _defs = [
                ("High-tide flood",         2.37,          True,
                 "High-tide flooding (HTF) level, ft NAVD88."),
                ("Monthly flood",           None,          False,
                 "Optional. A level reached roughly monthly, ft NAVD88."),
                ("Annual flood",            _pf['annual'], False,
                 "≈ the level reached most years (50th-percentile annual maximum)."),
                ("10% annual-chance flood", 5.78,          True,
                 "1-in-10-year level (90th-percentile annual maximum)."),
                ("1% annual-chance flood",  7.38,          True,
                 "1-in-100-year level (99th-percentile annual maximum)."),
            ]
        else:
            _defs = [
                ("High-tide flood",         None,          False,
                 "Optional. e.g. the local NOAA minor/nuisance-flood threshold, ft NAVD88."),
                ("Monthly flood",           None,          False,
                 "Optional. A level reached roughly monthly, ft NAVD88."),
                ("Annual flood",            _pf['annual'], _pf['annual'] is not None,
                 "≈ the level reached most years (50th-percentile annual maximum)."),
                ("10% annual-chance flood", _pf['ten'],    _pf['ten'] is not None,
                 "1-in-10-year level (90th-percentile annual maximum)."),
                ("1% annual-chance flood",  _pf['one'],    _pf['one'] is not None,
                 "1-in-100-year level (99th-percentile annual maximum)."),
            ]

        _rows = []
        for _i, (_lbl, _val, _on, _help) in enumerate(_defs):
            _c0, _c1 = st.columns([0.34, 0.66])
            _inc = _c0.checkbox(_lbl, value=_on, key=f"fld_inc_{_lbl}", help=_help)
            _default = float(_val) if _val is not None else 0.0
            _lv = _c1.number_input(
                f"{_lbl} level (ft NAVD88)", value=_default, step=0.5, format="%.2f",
                key=f"fld_val_{_lbl}", label_visibility="collapsed",
            )
            if _inc:
                _rows.append((_lbl, float(_lv)))

        _c_scn, _c_yr = st.columns(2)
        _scn_sel = _c_scn.multiselect(
            "SLR scenarios", options=_scn_keys, default=_scn_keys, format_func=_scn_pretty,
        )
        _map_years = sorted(set([2026] + [int(y) for y in available_years]))
        _years_sel = _c_yr.multiselect(
            "Planning horizons", options=_map_years, default=_map_years,
            format_func=lambda y: f"{int(y)} (present)" if int(y) == 2026 else str(int(y)),
        )
        _cz, _cd, _cb = st.columns([0.36, 0.32, 0.32])
        _zoom_factor = _cz.slider(
            "Zoom (tighten)", min_value=0.5, max_value=3.0, value=1.0, step=0.25,
            help="Higher = tighter crop around the community (more zoomed in).",
        )
        _res_label = _cd.selectbox(
            "Map detail", ["Standard", "Fine", "Finer"], index=2,
            help="Higher detail = sharper, larger images; a bit slower.",
        )
        _res_m, _target_px = {
            "Standard": (10.0, 700), "Fine": (5.0, 1000), "Finer": (3.0, 1300),
        }[_res_label]
        _base_label = _cb.selectbox("Basemap", ["OSM (color)", "Light", "Dark"], index=0)
        st.caption(
            f"A map is produced for every ticked level × horizon × scenario. For each scenario, SLR is "
            f"the rise in the median annual-maximum water level from {_base_year} to that horizon. "
            f"Open water (Z ≤ 0) is hidden. Maps render as static images, so there is no limit on how "
            f"many can be shown."
        )

        if not _rows:
            st.info("Tick at least one flood level to generate maps.")
        elif not _years_sel:
            st.info("Select at least one planning horizon.")
        elif not _scn_sel:
            st.info("Select at least one SLR scenario.")
        elif st.button("🌊 Generate flood maps", type="primary"):
            _lonA = df_buildings['longitude'].to_numpy(dtype=float)
            _latA = df_buildings['latitude'].to_numpy(dtype=float)
            _lonA, _latA, _swapped = fdem.maybe_swap_lonlat(_lonA, _latA)
            try:
                _bbox = fdem.roi_from_lonlat(_lonA, _latA, buffer_m=350.0)
                # Apply the zoom (tighten) factor: shrink the half-extent about the centre.
                _cx = 0.5 * (_bbox[0] + _bbox[2])
                _cy = 0.5 * (_bbox[1] + _bbox[3])
                _hw = 0.5 * (_bbox[2] - _bbox[0]) / _zoom_factor
                _hh = 0.5 * (_bbox[3] - _bbox[1]) / _zoom_factor
                _bbox = (_cx - _hw, _cy - _hh, _cx + _hw, _cy + _hh)
                _bbox_r = tuple(round(v, 5) for v in _bbox)
            except Exception as _e:
                st.error(f"Could not determine the map area: {_e}")
                _bbox_r = None

            _Zm = None
            _ext = None
            _base_img = None
            if _bbox_r is not None:
                with st.spinner("Fetching terrain (USGS 3DEP) and basemap…"):
                    try:
                        _Zm, _ext = _cached_dem_roi(_bbox_r, _res_m)
                    except Exception as _e:
                        st.error(
                            "Could not load terrain for this area from USGS 3DEP "
                            "(this needs internet access at runtime). Details: %s" % _e
                        )
                    try:
                        _ztiles = fdem.tile_zoom_for_bbox(_bbox_r, target_px=_target_px)
                        _base_img, _ = _cached_basemap(_bbox_r, _ztiles, _base_label)
                    except Exception as _e:
                        st.warning(
                            "Could not load basemap tiles; maps will show flooding on a plain "
                            "background. Details: %s" % _e
                        )

            with st.expander("Map-area diagnostics"):
                _fin = np.isfinite(_lonA) & np.isfinite(_latA)
                st.write(
                    f"Buildings with coordinates: {int(_fin.sum())} of {len(_lonA)}"
                    + ("  •  longitude/latitude looked transposed and were auto-swapped" if _swapped else "")
                )
                if _fin.any():
                    st.write("lon  min / median / max: "
                             f"{np.nanmin(_lonA[_fin]):.4f} / {np.nanmedian(_lonA[_fin]):.4f} / {np.nanmax(_lonA[_fin]):.4f}")
                    st.write("lat  min / median / max: "
                             f"{np.nanmin(_latA[_fin]):.4f} / {np.nanmedian(_latA[_fin]):.4f} / {np.nanmax(_latA[_fin]):.4f}")
                if _bbox_r is not None:
                    st.write(f"Region of interest (lon_min, lat_min, lon_max, lat_max): {_bbox_r}")
                if _Zm is not None:
                    _zland = np.isfinite(_Zm) & (_Zm > 0)
                    st.write(
                        f"DEM grid: {_Zm.shape[0]} × {_Zm.shape[1]} cells (~{_res_m:.0f} m)  •  "
                        f"land cells: {int(_zland.sum())}  •  elevation "
                        f"{np.nanmin(_Zm):.1f} to {np.nanmax(_Zm):.1f} m NAVD88"
                    )

            if _Zm is not None and _ext is not None:
                st.markdown(fdem.legend_html(), unsafe_allow_html=True)
                _lat_m = (_ext[3] - _ext[1]) * 111320.0
                _lon_m = (_ext[2] - _ext[0]) * 111320.0 * math.cos(math.radians(0.5 * (_ext[1] + _ext[3])))
                _cell_m2 = (_lat_m / _Zm.shape[0]) * (_lon_m / _Zm.shape[1])
                if _base_img is None:
                    _base_img = np.full((max(2, _Zm.shape[0]), max(2, _Zm.shape[1]), 3), 245, dtype=np.uint8)

                for _scn in _scn_sel:
                    st.markdown(f"##### {_scn_pretty(_scn)}")
                    _cols = st.columns(2)
                    _k = 0
                    for _lbl, _base_lv in _rows:
                        for _yr in _years_sel:
                            _wl_ft = _base_lv + _slr(_scn, _yr)
                            _depth = fdem.bathtub_depth_ft(_Zm, _wl_ft, mask_water=True)
                            _nflood = int(np.isfinite(_depth).sum())
                            _flood_km2 = _nflood * _cell_m2 / 1e6
                            _note = (f"flooded ≈ {_flood_km2:.2f} km²" if _nflood > 0 else "no inundation")
                            _tgt = _cols[_k % 2]
                            # Title ABOVE the image (separate element - cannot overlap the figure).
                            _tgt.markdown(
                                f"**{_lbl} - {int(_yr)}**<br>"
                                f"<span style='font-size:0.95rem;color:#374151'>"
                                f"WL ≈ {_wl_ft:.2f} ft NAVD88 &nbsp;•&nbsp; {_note}</span>",
                                unsafe_allow_html=True,
                            )
                            _png = fdem.compose_flood_png(_base_img, _depth)
                            _tgt.image(_png, use_container_width=True)
                            _k += 1

                st.caption(
                    f"Bathtub model (no hydraulic connectivity); open water (Z ≤ 0) hidden. Terrain: "
                    f"USGS 3DEP 1/3 arc-second (~10 m), displayed at ~{_res_m:.0f} m. Basemap: "
                    f"{_base_label} - © OpenStreetMap contributors / © CARTO."
                )

        # ================================================================
        # EXTRA MAPS - custom years with a directly-entered sea-level rise
        # (for horizons outside the study's Monte-Carlo water levels). The
        # base flood levels come from the inputs above; only the SLR term
        # changes. Uses the same zoom / detail / basemap controls and the
        # same bathtub renderer, so the output matches the default maps.
        # ================================================================
        def _prepare_flood_canvas():
            """Fetch terrain + basemap for the current ROI using the shared
            zoom / detail / basemap controls above. Returns
            (Zm, ext, base_img, cell_m2) or (None, None, None, None)."""
            _lonA = df_buildings['longitude'].to_numpy(dtype=float)
            _latA = df_buildings['latitude'].to_numpy(dtype=float)
            _lonA, _latA, _sw = fdem.maybe_swap_lonlat(_lonA, _latA)
            try:
                _bb = fdem.roi_from_lonlat(_lonA, _latA, buffer_m=350.0)
                _cx = 0.5 * (_bb[0] + _bb[2]); _cy = 0.5 * (_bb[1] + _bb[3])
                _hw = 0.5 * (_bb[2] - _bb[0]) / _zoom_factor
                _hh = 0.5 * (_bb[3] - _bb[1]) / _zoom_factor
                _bb = (_cx - _hw, _cy - _hh, _cx + _hw, _cy + _hh)
                _bbr = tuple(round(v, 5) for v in _bb)
            except Exception as _e:
                st.error(f"Could not determine the map area: {_e}")
                return None, None, None, None
            _Zm = _ext = _base_img = None
            with st.spinner("Fetching terrain (USGS 3DEP) and basemap…"):
                try:
                    _Zm, _ext = _cached_dem_roi(_bbr, _res_m)
                except Exception as _e:
                    st.error(
                        "Could not load terrain for this area from USGS 3DEP "
                        "(this needs internet access at runtime). Details: %s" % _e
                    )
                try:
                    _zt = fdem.tile_zoom_for_bbox(_bbr, target_px=_target_px)
                    _base_img, _ = _cached_basemap(_bbr, _zt, _base_label)
                except Exception as _e:
                    st.warning(
                        "Could not load basemap tiles; maps will show flooding on a plain "
                        "background. Details: %s" % _e
                    )
            if _Zm is None or _ext is None:
                return None, None, None, None
            _lat_m = (_ext[3] - _ext[1]) * 111320.0
            _lon_m = (_ext[2] - _ext[0]) * 111320.0 * math.cos(math.radians(0.5 * (_ext[1] + _ext[3])))
            _cell_m2 = (_lat_m / _Zm.shape[0]) * (_lon_m / _Zm.shape[1])
            if _base_img is None:
                _base_img = np.full((max(2, _Zm.shape[0]), max(2, _Zm.shape[1]), 3), 245, dtype=np.uint8)
            return _Zm, _ext, _base_img, _cell_m2

        st.divider()
        with st.expander("➕ Extra maps - custom years & sea-level rise", expanded=_is_pamunkey):
            st.markdown(
                "Map flood levels for **years outside the study horizons**, where sea-level "
                "rise is entered directly instead of derived from the Monte-Carlo water levels. "
                "Pick which present-day flood levels to map (their base values are the ones set "
                "above), enter one row per year with that year's sea-level rise, then generate. "
                "Each map shows the base level plus the year's sea-level rise, drawn with the "
                "same zoom / detail / basemap settings as the maps above."
            )

            # Present-day base value for each of the five standard levels,
            # read back from the number inputs above (fall back to defaults).
            _base_levels = {}
            for (_lbl, _val, _on, _help) in _defs:
                _base_levels[_lbl] = float(
                    ss.get(f"fld_val_{_lbl}", _val if _val is not None else 0.0)
                )

            _default_levels = [lbl for (lbl, _lv) in _rows] or [
                l for l in ("10% annual-chance flood", "1% annual-chance flood")
                if l in _base_levels
            ]
            _xtra_levels = st.multiselect(
                "Flood levels to map", options=list(_base_levels.keys()),
                default=_default_levels, key="xtra_levels",
                format_func=lambda l: f"{l}  ({_base_levels[l]:.2f} ft)",
                help="Base present-day levels come from the inputs above; the extra maps "
                     "add each year's sea-level rise from the table below.",
            )

            _xtra_label = st.text_input(
                "Scenario label (optional, shown above the maps)",
                value=("Intermediate-High SLR" if _is_pamunkey else ""),
                key="xtra_scn_label",
                placeholder="e.g. Intermediate-High SLR",
            )

            # Seed the (year, SLR) table. Pamunkey ships the project's
            # Intermediate-High sea-level-rise curve (ft, relative to 2026).
            if _is_pamunkey:
                _seed = pd.DataFrame({
                    "Year": [2026, 2030, 2040, 2050, 2060, 2070, 2080, 2090, 2100],
                    "Sea-level rise (ft)": [0.0, 0.25, 0.76, 1.43, 2.12, 2.93, 3.80, 4.75, 5.82],
                })
            else:
                _seed = pd.DataFrame({
                    "Year": [2026, 2050, 2075, 2100],
                    "Sea-level rise (ft)": [0.0, 0.0, 0.0, 0.0],
                })

            _xtra_tbl = st.data_editor(
                _seed, key="xtra_years_tbl", num_rows="dynamic",
                use_container_width=True, hide_index=True,
                column_config={
                    "Year": st.column_config.NumberColumn(
                        "Year", min_value=1900, max_value=2200, step=1, format="%d"),
                    "Sea-level rise (ft)": st.column_config.NumberColumn(
                        "Sea-level rise (ft)", step=0.05, format="%.2f"),
                },
            )

            # Clean into ordered, de-duplicated (year, slr) pairs.
            _xtra_rows = []
            try:
                for _, _r in _xtra_tbl.iterrows():
                    _y = _r.get("Year"); _s = _r.get("Sea-level rise (ft)")
                    if pd.notna(_y) and pd.notna(_s):
                        _xtra_rows.append((int(_y), float(_s)))
            except Exception:
                _xtra_rows = []
            _seen = set()
            _xtra_rows = [
                (_y, _s) for (_y, _s) in _xtra_rows
                if not (_y in _seen or _seen.add(_y))
            ]

            if not _xtra_levels:
                st.info("Pick at least one flood level to map.")
            elif not _xtra_rows:
                st.info("Enter at least one year with its sea-level rise.")
            elif st.button("➕ Generate extra maps", type="primary", key="xtra_go"):
                _Zm, _ext, _base_img, _cell_m2 = _prepare_flood_canvas()
                if _Zm is not None and _ext is not None:
                    if _xtra_label.strip():
                        st.markdown(f"##### {_xtra_label.strip()}")
                    st.markdown(fdem.legend_html(), unsafe_allow_html=True)
                    _cols = st.columns(2)
                    _k = 0
                    for _lbl in _xtra_levels:
                        _base_lv = _base_levels[_lbl]
                        for (_yr, _slr_ft) in _xtra_rows:
                            _wl_ft = _base_lv + _slr_ft
                            _depth = fdem.bathtub_depth_ft(_Zm, _wl_ft, mask_water=True)
                            _nflood = int(np.isfinite(_depth).sum())
                            _flood_km2 = _nflood * _cell_m2 / 1e6
                            _note = (f"flooded ≈ {_flood_km2:.2f} km²" if _nflood > 0 else "no inundation")
                            _tgt = _cols[_k % 2]
                            _tgt.markdown(
                                f"**{_lbl} - {int(_yr)}**<br>"
                                f"<span style='font-size:0.95rem;color:#374151'>"
                                f"WL ≈ {_wl_ft:.2f} ft NAVD88 &nbsp;•&nbsp; "
                                f"SLR {_slr_ft:+.2f} ft &nbsp;•&nbsp; {_note}</span>",
                                unsafe_allow_html=True,
                            )
                            _png = fdem.compose_flood_png(_base_img, _depth)
                            _tgt.image(_png, use_container_width=True)
                            _k += 1
                    st.caption(
                        f"Bathtub model (no hydraulic connectivity); open water (Z ≤ 0) hidden. "
                        f"Sea-level rise is taken directly from the table above, not derived from "
                        f"the Monte-Carlo water levels. Terrain: USGS 3DEP 1/3 arc-second (~10 m), "
                        f"displayed at ~{_res_m:.0f} m. Basemap: {_base_label} - © OpenStreetMap "
                        f"contributors / © CARTO."
                    )

