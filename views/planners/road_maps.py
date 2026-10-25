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
from adapt_core.terrain import (_cached_basemap, _cached_dem_roi, _cached_local_roads, _cached_osm_roads, fdem)


def render(ctx):
    available_years = ctx.available_years
    data_store = ctx.data_store
    df_buildings = ctx.df_buildings
    loc_entry = ctx.loc_entry
    selected_location = ctx.selected_location
    from adapt_core import ui as _ui
    _ui.page_header(
        "Road maps",
        "OpenStreetMap roads classified against the same bathtub flood levels as the Flood maps page. "
        "Each road is sampled along its length and every segment is flagged "
        "<b style='color:#dc1414'>flooded</b> (surface below the water level), "
        "<b style='color:#8a2be2'>inaccessible</b> (dry but cut off by flooding) or "
        "<b style='color:#228b22'>dry</b>.",
        module_key="planners", location=ctx.location_name,
    )

    _rfl_ok = (selected_location in data_store and df_buildings is not None)
    _rhas_xy = (
        df_buildings is not None
        and {'longitude', 'latitude'}.issubset(df_buildings.columns)
        and df_buildings['latitude'].notna().any()
        and df_buildings['longitude'].notna().any()
    )
    try:
        import rasterio as _rio2  # noqa: F401
        _rrio_ok = True
    except Exception:
        _rrio_ok = False

    if not _rfl_ok:
        st.info("Select a location with building data to map road flooding.")
    elif not _rhas_xy:
        st.warning("This location has no per-building coordinates, so the map extent can't be determined.")
    elif not _rrio_ok:
        st.error(
            "Road maps need the **rasterio** package to read the terrain. "
            "Add `rasterio` to the app's requirements.txt and redeploy."
        )
    else:
        _rwl = loc_entry.get('water_levels', {}) if loc_entry else {}
        _rscn_keys = [k for k in _rwl.keys() if not k.endswith('_mc')]
        _rscn_label = {
            '50th-percentile': 'Intermediate-High SLR (50th pct)',
            '90th-percentile': 'High SLR (90th pct)',
        }
        _rscn_pretty = lambda k: _rscn_label.get(k, k)
        _rref = '50th-percentile' if '50th-percentile' in _rwl else (_rscn_keys[0] if _rscn_keys else None)
        _rref_df = _rwl.get(_rref)
        _rbase_year = int(_rref_df['Year'].min()) if (_rref_df is not None and not _rref_df.empty) else 2025

        def _rlvl(df, year, col):
            if df is None or df.empty or col not in df.columns:
                return None
            i = (df['Year'] - year).abs().idxmin()
            return float(df.loc[i, col])

        def _rslr(scn_key, year):
            df = _rwl.get(scn_key)
            a, b = _rlvl(df, year, 'P50'), _rlvl(df, _rbase_year, 'P50')
            return (a - b) if (a is not None and b is not None) else 0.0

        _rpf = {
            'annual': _rlvl(_rref_df, _rbase_year, 'P50'),
            'ten':    _rlvl(_rref_df, _rbase_year, 'P90'),
            'one':    _rlvl(_rref_df, _rbase_year, 'P99'),
        }

        st.markdown(
            f"**Present-day base levels** (ft NAVD88) - the same inputs as the Flood Maps tab; future "
            f"levels add each scenario's sea-level rise."
        )

        _ris_pam = (selected_location == "Pamunkey")
        if _ris_pam:
            _rdefs = [
                ("High-tide flood",         2.37,           True,
                 "High-tide flooding (HTF) level, ft NAVD88."),
                ("Monthly flood",           None,           False,
                 "Optional. A level reached roughly monthly, ft NAVD88."),
                ("Annual flood",            _rpf['annual'], False,
                 "≈ the level reached most years (50th-percentile annual maximum)."),
                ("10% annual-chance flood", 5.78,           True,
                 "1-in-10-year level (90th-percentile annual maximum)."),
                ("1% annual-chance flood",  7.38,           True,
                 "1-in-100-year level (99th-percentile annual maximum)."),
            ]
        else:
            _rdefs = [
                ("High-tide flood",         None,           False,
                 "Optional. e.g. the local NOAA minor/nuisance-flood threshold, ft NAVD88."),
                ("Monthly flood",           None,           False,
                 "Optional. A level reached roughly monthly, ft NAVD88."),
                ("Annual flood",            _rpf['annual'], _rpf['annual'] is not None,
                 "≈ the level reached most years (50th-percentile annual maximum)."),
                ("10% annual-chance flood", _rpf['ten'],    _rpf['ten'] is not None,
                 "1-in-10-year level (90th-percentile annual maximum)."),
                ("1% annual-chance flood",  _rpf['one'],    _rpf['one'] is not None,
                 "1-in-100-year level (99th-percentile annual maximum)."),
            ]

        _rrows = []
        for _lbl, _val, _on, _help in _rdefs:
            _c0, _c1 = st.columns([0.34, 0.66])
            _inc = _c0.checkbox(_lbl, value=_on, key=f"rdf_inc_{_lbl}", help=_help)
            _default = float(_val) if _val is not None else 0.0
            _lv = _c1.number_input(
                f"{_lbl} level (ft NAVD88)", value=_default, step=0.5, format="%.2f",
                key=f"rdf_val_{_lbl}", label_visibility="collapsed",
            )
            if _inc:
                _rrows.append((_lbl, float(_lv)))

        _rc_scn, _rc_yr = st.columns(2)
        _rscn_sel = _rc_scn.multiselect(
            "SLR scenarios", options=_rscn_keys, default=_rscn_keys,
            format_func=_rscn_pretty, key="rd_scn",
        )
        _rmap_years = sorted(set([2026] + [int(y) for y in available_years]))
        _ryears_sel = _rc_yr.multiselect(
            "Planning horizons", options=_rmap_years, default=_rmap_years,
            format_func=lambda y: f"{int(y)} (present)" if int(y) == 2026 else str(int(y)),
            key="rd_years",
        )
        _rcz, _rcd, _rcb = st.columns([0.36, 0.32, 0.32])
        _rzoom = _rcz.slider(
            "Zoom (tighten)", min_value=0.5, max_value=3.0, value=1.0, step=0.25,
            key="rd_zoom", help="Higher = tighter crop around the community.",
        )
        _rres_label = _rcd.selectbox(
            "Map detail", ["Standard", "Fine", "Finer"], index=2, key="rd_detail",
            help="Higher detail = sharper, larger images; a bit slower.",
        )
        _rres_m, _rtarget_px = {
            "Standard": (10.0, 700), "Fine": (5.0, 1000), "Finer": (3.0, 1300),
        }[_rres_label]
        _rbase_label = _rcb.selectbox("Basemap", ["OSM (color)", "Light", "Dark"], index=0, key="rd_base")
        # Reachability + entrance-gateway toggles on a single line. The
        # entrance toggle only applies when reachability is on, so its
        # column stays empty when reachability is off.
        _rk1, _rk2 = st.columns(2)
        with _rk1:
            _raccess_on = st.checkbox(
                "Define \u201creachable\u201d from the largest connected network",
                value=True, key="rd_access",
                help="On (default): a dry road is flagged 'inaccessible' when flooding severs every dry "
                     "route from it to the largest connected road cluster, which is treated as the "
                     "mainland. Off: skip the reachability analysis entirely and color roads only as "
                     "flooded vs dry (no 'inaccessible' category).",
            )
        _rsource = "largest"
        _rentry_on = False
        _rentry_manual = ""
        with _rk2:
            if _raccess_on:
                _rentry_on = st.checkbox(
                    "Keep the main entrance open as a guaranteed gateway", value=True, key="rd_entry",
                    help="The site's main access road off the main road often floods first and would otherwise make "
                         "the whole area read as inaccessible. With this on, that entrance is treated as a guaranteed "
                         "gateway: it still shows as flooded, but it no longer cuts the area off - interior roads are "
                         "still judged on their own flooding. The entrance is auto-detected from the building cluster "
                         "and shown below the maps so you can verify it.",
                )
        if _raccess_on and _rentry_on:
            _rentry_manual = st.text_input(
                "Entrance location override - lat, lon (optional)", value="", key="rd_entry_xy",
                placeholder="auto-detect (leave blank)",
                help="Leave blank to auto-detect the entrance from the building cluster. If the detected "
                     "point shown below the maps isn't the right road, pin it by pasting the entrance "
                     "coordinate here in the same 'lat, lon' order shown there (e.g. 37.5554, -76.8361).",
            )
        st.caption(
            "A road map is produced for every ticked level × horizon × scenario. The grid shows them "
            "as static images; pick any map in “Zoom into a map” to zoom in and out of a "
            "section of it. Roads come live from OpenStreetMap (Overpass) for the map area."
        )

        # Settings fingerprint, so maps kept from an earlier Generate can be
        # flagged when the controls above have changed since.
        _rsig = (
            selected_location, tuple(_rrows), tuple(int(y) for y in _ryears_sel),
            tuple(_rscn_sel), float(_rzoom), _rres_label, _rbase_label,
            bool(_raccess_on), bool(_rentry_on), (_rentry_manual or "").strip(),
        )

        if not _rrows:
            st.info("Tick at least one flood level to generate maps.")
        elif not _ryears_sel:
            st.info("Select at least one planning horizon.")
        elif not _rscn_sel:
            st.info("Select at least one SLR scenario.")
        elif st.button("🛣️ Generate road maps", type="primary", key="rd_go"):
            _lonA = df_buildings['longitude'].to_numpy(dtype=float)
            _latA = df_buildings['latitude'].to_numpy(dtype=float)
            _lonA, _latA, _swap = fdem.maybe_swap_lonlat(_lonA, _latA)
            try:
                _bb = fdem.roi_from_lonlat(_lonA, _latA, buffer_m=350.0)
                _cx = 0.5 * (_bb[0] + _bb[2]); _cy = 0.5 * (_bb[1] + _bb[3])
                _hw = 0.5 * (_bb[2] - _bb[0]) / _rzoom; _hh = 0.5 * (_bb[3] - _bb[1]) / _rzoom
                _bb = (_cx - _hw, _cy - _hh, _cx + _hw, _cy + _hh)
                _bbr = tuple(round(v, 5) for v in _bb)
            except Exception as _e:
                st.error(f"Could not determine the map area: {_e}")
                _bbr = None

            _Zm = None
            _ext = None
            _base_img = None
            _roads = None
            if _bbr is not None:
                with st.spinner("Fetching terrain (3DEP), basemap, and OSM roads…"):
                    try:
                        _Zm, _ext = _cached_dem_roi(_bbr, _rres_m)
                    except Exception as _e:
                        st.error("Could not load terrain from USGS 3DEP (needs internet at runtime). %s" % _e)
                    try:
                        _zt = fdem.tile_zoom_for_bbox(_bbr, target_px=_rtarget_px)
                        _base_img, _ = _cached_basemap(_bbr, _zt, _rbase_label)
                    except Exception as _e:
                        st.warning("Basemap tiles unavailable; roads will draw on a plain background. %s" % _e)
                    # A saved roads file next to the data ({slug}_osm_roads.json,
                    # made by download_osm_roads.py) is used first, so the maps
                    # don't depend on the public Overpass servers being reachable.
                    _rslug = (loc_entry or {}).get('location_slug')
                    _rlocal = (os.path.join((loc_entry or {}).get('data_folder', '.'), f"{_rslug}_osm_roads.json")
                               if _rslug else None)
                    if _rlocal and os.path.exists(_rlocal):
                        _roads = _cached_local_roads(_rlocal, os.path.getmtime(_rlocal), _bbr)
                    if _roads is None:
                        try:
                            _roads = _cached_osm_roads(_bbr)
                        except Exception as _e:
                            st.error(
                                "Could not download OSM roads from Overpass (needs internet at runtime; the "
                                "public Overpass servers may be busy - try again in a moment). %s" % _e
                            )
                            if _rlocal:
                                st.caption(
                                    f"To stop depending on Overpass, run download_osm_roads.py on a computer "
                                    f"with internet, in the folder with this location's data, and add the "
                                    f"`{_rlocal}` it writes next to app.py."
                                )

            if _Zm is not None and _ext is not None and _roads is not None:
                if _base_img is None:
                    _base_img = np.full((max(2, _Zm.shape[0]), max(2, _Zm.shape[1]), 3), 245, dtype=np.uint8)

                # Entrance gateway: a manual override (lat, lon) wins; otherwise
                # auto-detect from the building cluster, so a flooded entrance doesn't
                # make the whole area read as inaccessible.
                _entry = None
                _entry_manual = False
                _entry_note = None
                if _rentry_on:
                    _mtxt = (_rentry_manual or "").strip()
                    if _mtxt:
                        try:
                            _v = [float(x) for x in
                                  _mtxt.replace(";", " ").replace(",", " ").split()]
                            if len(_v) >= 2:
                                _a, _b = _v[0], _v[1]
                                if _a < 0 and _b > 0:        # given as lon, lat
                                    _elon, _elat = _a, _b
                                else:                         # given as lat, lon (as shown)
                                    _elat, _elon = _a, _b
                                # treat the typed point as a "near here" hint and
                                # snap it to the actual entrance gateway, so it need
                                # not land exactly on a road node.
                                try:
                                    _snap = fdem.detect_main_entrance(_roads, _ext, (_elon, _elat))
                                except Exception:
                                    _snap = None
                                _entry = _snap if _snap else [(_elon, _elat)]
                                _entry_manual = True
                        except Exception:
                            _entry = None
                    if _entry is None:
                        try:
                            _site_lon = float(np.nanmean(_lonA)); _site_lat = float(np.nanmean(_latA))
                            _entry = fdem.detect_main_entrance(_roads, _ext, (_site_lon, _site_lat))
                        except Exception:
                            _entry = None
                    if _entry:
                        _elon, _elat = _entry[0]
                        _how = "pinned manually" if _entry_manual else "auto-detected from the building cluster"
                        _entry_note = (
                            f"Main entrance treated as a guaranteed gateway near "
                            f"**{_elat:.5f}, {_elon:.5f}** ({_how}). "
                            f"It still shows flooded, but no longer cuts the area off. If this isn't the right "
                            f"road, set the override above or untick the option."
                        )
                    else:
                        _entry_note = (
                            "Main-entrance exemption is on, but no single entrance could be auto-detected "
                            "from the network here, so it has no effect. You can pin one with the override above."
                        )

                _rmaps = []
                with st.spinner("Classifying roads and building maps…"):
                    for _scn in _rscn_sel:
                        for _lbl, _blv in _rrows:
                            for _yr in _ryears_sel:
                                _wl = _blv + _rslr(_scn, _yr)
                                _segs, _cnt = fdem.classify_roads_access(
                                    _Zm, _ext, _roads, _wl, source=_rsource,
                                    entry_points=_entry, entrance_reach_m=450.0)
                                if not _raccess_on:
                                    # Reachability analysis off: color roads as
                                    # flooded vs dry only, folding any inaccessible
                                    # segments back into dry/reachable. Flood
                                    # detection (status 2) is identical either way,
                                    # so only the inaccessible overlay is dropped.
                                    _segs = [(p0, p1, 0 if s == 1 else s)
                                             for (p0, p1, s) in _segs]
                                    _cnt = dict(_cnt)
                                    _cnt['pct_dry'] = _cnt.get('pct_dry', 0.0) + _cnt.get('pct_inacc', 0.0)
                                    _cnt['pct_inacc'] = 0.0
                                _depth = fdem.bathtub_depth_ft(_Zm, _wl, mask_water=True)
                                if _raccess_on:
                                    _metric_line = (
                                        f"flooded {_cnt['pct_flood']:.0f}% · "
                                        f"inaccessible {_cnt['pct_inacc']:.0f}% · "
                                        f"dry {_cnt['pct_dry']:.0f}%")
                                else:
                                    _metric_line = (
                                        f"flooded {_cnt['pct_flood']:.0f}% · "
                                        f"dry {_cnt['pct_dry']:.0f}%")
                                _rmaps.append(dict(
                                    scn=_scn, lbl=_lbl, yr=int(_yr), wl=float(_wl),
                                    metric=_metric_line, segs=_segs,
                                    png=fdem.compose_road_png(_base_img, _depth, _segs, _ext),
                                    jpg=fdem.compose_flood_jpeg(_base_img, _depth),
                                ))

                # Keep the maps in the session so they survive reruns (picking a
                # map to zoom, or any other control, no longer wipes them).
                st.session_state['rd_results'] = dict(
                    sig=_rsig, location=selected_location, maps=_rmaps, ext=_ext,
                    n_roads=len(_roads), entry_note=_entry_note,
                    access_on=bool(_raccess_on), res_m=float(_rres_m),
                )

        # ---- Show the last generated maps (kept across reruns) ----
        _rres = st.session_state.get('rd_results')
        if _rres and _rres.get('location') == selected_location and _rres.get('maps'):
            if _rres.get('sig') != _rsig:
                st.info("These maps were made with different settings than the ones above. "
                        "Click **Generate road maps** to update them.")
            st.markdown(
                '<div style="margin:0.5rem 0 0.8rem;padding:0.55rem 0.8rem;background:#f8fafc;'
                'border:1px solid #e2e8f0;border-radius:8px;font-size:1.05rem;">'
                '<b style="font-size:1.2rem;">Roads</b>&nbsp;&nbsp;'
                '<span style="color:#dc1414;font-weight:800;">━</span> flooded (surface below the water level)'
                '&nbsp;&nbsp;&nbsp;<span style="color:#8a2be2;font-weight:800;">━</span> '
                'inaccessible (dry but cut off from the network by flooding)'
                '&nbsp;&nbsp;&nbsp;<span style="color:#228b22;font-weight:800;">━</span> dry (accessible)'
                '<br><b style="font-size:1.05rem;">Water</b>&nbsp;&nbsp;'
                '<span style="background:rgb(198,219,239);">&nbsp;&nbsp;</span>'
                '<span style="background:rgb(107,174,214);">&nbsp;&nbsp;</span>'
                '<span style="background:rgb(33,113,181);">&nbsp;&nbsp;</span>'
                '<span style="background:rgb(8,69,148);">&nbsp;&nbsp;</span>'
                '&nbsp; flood depth - light → dark blue = shallow → deep'
                '</div>',
                unsafe_allow_html=True,
            )
            st.caption(f"{_rres['n_roads']} OpenStreetMap road way(s) in view.")
            if _rres.get('entry_note'):
                st.caption(_rres['entry_note'])

            _rmaps = _rres['maps']

            # ---- Zoom into a map: one interactive, full-width view ----
            # Only ONE map is interactive at a time: each carries its raster
            # (~0.5 MB), and sending dozens at once over the app's connection
            # can drop it, which reruns the page.
            st.markdown("##### 🔍 Zoom into a map")
            _rpick = st.selectbox(
                "Map to explore", options=list(range(len(_rmaps))),
                format_func=lambda i: (f"{_rmaps[i]['lbl']} - {_rmaps[i]['yr']} - "
                                       f"{_rscn_pretty(_rmaps[i]['scn'])}"),
                key="rd_zoom_pick",
            )
            if _rpick is None or _rpick >= len(_rmaps):
                _rpick = 0
            _rm = _rmaps[_rpick]
            st.markdown(
                f"**{_rm['lbl']} - {_rm['yr']} - {_rscn_pretty(_rm['scn'])}**<br>"
                f"<span style='font-size:0.95rem;color:#374151'>"
                f"WL ≈ {_rm['wl']:.2f} ft NAVD88 &nbsp;•&nbsp; {_rm['metric']}</span>",
                unsafe_allow_html=True,
            )
            _rname = (f"roads_{selected_location}_{_rm['lbl']}_{_rm['yr']}_{_rm['scn']}"
                      .replace(" ", "_").replace("%", "pct"))
            st.plotly_chart(
                fdem.road_map_figure(_rm['jpg'], _rm['segs'], _rres['ext']),
                use_container_width=True,
                config=fdem.road_map_plotly_config(_rname),
                key="rd_zoom_fig",
            )
            st.caption(
                "Drag a box over any part of the map to zoom into it; double-click to zoom back out. "
                "The toolbar (top right of the map) also has zoom in/out, pan, reset and a "
                "high-resolution PNG download of the current view."
            )

            # ---- All maps (static grid) ----
            st.markdown("##### All maps")
            for _scn in dict.fromkeys(m['scn'] for m in _rmaps):
                st.markdown(f"###### {_rscn_pretty(_scn)}")
                _cols = st.columns(2)
                _k = 0
                for _m in _rmaps:
                    if _m['scn'] != _scn:
                        continue
                    _tgt = _cols[_k % 2]
                    _tgt.markdown(
                        f"**{_m['lbl']} - {_m['yr']}**<br>"
                        f"<span style='font-size:0.95rem;color:#374151'>"
                        f"WL ≈ {_m['wl']:.2f} ft NAVD88 &nbsp;•&nbsp; "
                        f"{_m['metric']}</span>",
                        unsafe_allow_html=True,
                    )
                    _tgt.image(_m['png'], use_container_width=True)
                    _k += 1

            if _rres.get('access_on'):
                st.caption(
                    "Road segments: red = flooded (sampled surface below the water level), "
                    "violet = dry but inaccessible (every dry route to the largest connected network is "
                    "severed by flooding), green = dry and reachable. Percentages are by segment count. "
                    "Accessibility is computed on the OpenStreetMap network graph (intersections recovered "
                    "from shared road vertices); roads that were already disconnected in the raw data are "
                    "not counted as flood-caused. Road "
                    f"elevations and flood shading from USGS 3DEP (~10 m), displayed at ~{_rres['res_m']:.0f} m; "
                    "roads from OpenStreetMap. Open water (Z ≤ 0) is excluded from the flood mask."
                )
            else:
                st.caption(
                    "Road segments: red = flooded (sampled surface below the water level), "
                    "green = dry. Reachability analysis is off, so roads are colored by flooding only "
                    "(no 'inaccessible' category). Percentages are by segment count. Road "
                    f"elevations and flood shading from USGS 3DEP (~10 m), displayed at ~{_rres['res_m']:.0f} m; "
                    "roads from OpenStreetMap. Open water (Z ≤ 0) is excluded from the flood mask."
                )

