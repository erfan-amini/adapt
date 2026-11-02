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
from adapt_core.analysis import (compute_damage_bin_breaks, compute_flood_occurrences, prepare_map_data)
from adapt_core.helpers import (ADD_STORY_LABEL, ZERO_THRESH_DISPLAY, _elev_eligible_flag, _flag_true, _is_one_story_bldg, fmt_money_short, format_currency, is_residential, nice_round_up, smart_money_ticks)
from adapt_core.mapexport import (_basemap_config, build_publication_map_figure, export_map_image)
from views.planners import _terrain_views as TV


def render(ctx):
    _RAISE_ONLY_ACTIONS = ctx._RAISE_ONLY_ACTIONS
    df_buildings = ctx.df_buildings
    dfe_filter = ctx.dfe_filter
    fp_options = ctx.fp_options
    loc_entry = ctx.loc_entry
    location_name = ctx.location_name
    mobile_raise_only = ctx.mobile_raise_only
    occupancy_label = ctx.occupancy_label
    scenario = ctx.scenario
    selected_location = ctx.selected_location
    show_zero_damage = ctx.show_zero_damage
    ss = ctx.ss
    target_year = ctx.target_year
    _cb_dfe = ctx._cb_dfe
    _cb_zero = ctx._cb_zero
    from adapt_core import ui as _ui
    _ui.page_header(
        "Dynamic maps",
        "Interactive map of building-level flood risk. Switch the <b>Map View</b> between binned damage, "
        "adaptation effectiveness, <b>flood occurrences</b> (how often each building floods through a chosen "
        "horizon), the <b>flood map</b> (water over the terrain at a chosen flood level plus sea-level rise) and "
        "the <b>road map</b> (flooded and cut-off roads). Hover any building for details; click one to open it "
        "below.",
        module_key="planners", location=location_name,
    )
    # Occupancy is under Advanced options on this page (it applies to the whole module).
    ctx.render_inline_settings(["year", "scn"])
    # The map requires per-building longitude/latitude. The bundle
    # format guarantees these columns are present (they live in
    # bldg_lookup.csv). The guard remains as a safety net for any
    # future loader that might not populate them.
    _has_coords = (
        df_buildings is not None
        and {'longitude', 'latitude'}.issubset(df_buildings.columns)
        and df_buildings['latitude'].notna().any()
        and df_buildings['longitude'].notna().any()
    )

    if df_buildings is not None and not _has_coords:
        st.info(
            "🗺️ Map view is unavailable for this dataset - building "
            "coordinates are missing. All other tabs remain fully functional."
        )
    elif df_buildings is not None:
        st.subheader(f"Building Risk Map - {location_name} ({occupancy_label}) - {target_year}, {scenario}")

        # ---- Map view: one row of segmented buttons -------------------------
        _VIEW_ICONS = {
            "Damage Bins": "📊 Damage bins",
            "Adaptation Effectiveness": "🛡️ Adaptation effectiveness",
            "Flood Occurrences": "🌊 Flood occurrences",
            "Flood Map": "💧 Flood map",
            "Road Map": "🛣️ Road map",
        }
        # Fixed default + callback: clicking the active view would deselect it
        # (value None); the callback puts the last view back. Keeping the
        # default constant keeps the widget ID stable, so the elements below
        # (including an open Advanced options panel) are not re-mounted.
        def _keep_selection(key, last_key, fallback):
            if ss.get(key) is None:
                ss[key] = ss.get(last_key, fallback)
            ss[last_key] = ss[key]

        with ctx._keyed_container("map_view_row"):
            _mv = st.segmented_control(
                "Map view", options=list(_VIEW_ICONS), format_func=lambda v: _VIEW_ICONS[v],
                default="Damage Bins", selection_mode="single",
                key="map_view_seg", label_visibility="collapsed",
                on_change=_keep_selection, args=("map_view_seg", "_map_view_last", "Damage Bins"),
                help=(
                    "**Damage bins**: discrete bins of upper-tail (P90) damage with breakpoints fixed across years. "
                    "**Adaptation effectiveness**: classifies each building by the cheapest retrofit that "
                    "brings its P90 (or median) cumulative damage below a cutoff you choose. "
                    "**Flood occurrences**: how many times each building's first floor floods from 2025 "
                    "through the selected horizon. "
                    "**Flood map**: the water at a chosen flood level plus sea-level rise over the terrain, and "
                    "what it reaches at each building. "
                    "**Road map**: which roads are flooded, cut off, or dry at that level."
                ),
            )
        # Clicking the active segment deselects it; keep the last view instead.
        map_view = _mv or ss.get("_map_view_last", "Damage Bins")
        if map_view not in _VIEW_ICONS:          # a view removed from the menu (older session state)
            map_view = "Damage Bins"
        ss["_map_view_last"] = map_view

        # Flood Occurrences view - which statistic of the per-building MC
        # occurrence-count distribution drives the color. Always defined so
        # downstream code can reference it regardless of the active view.
        flood_pct_key = 'occ_mean'
        if map_view == "Flood Occurrences":
            _FP = {"Mean (average)": 'occ_mean', "P10 (low)": 'occ_P10',
                   "Median (P50)": 'occ_P50', "P90 (high)": 'occ_P90'}
            with ctx._keyed_container("map_fo_row"):
                _fp_label = st.segmented_control(
                    "Flood-occurrence statistic (across the 1,000 water-level realizations)",
                    options=list(_FP), default="Mean (average)",
                    selection_mode="single", key="flood_occ_seg",
                    on_change=_keep_selection, args=("flood_occ_seg", "_map_fo_last", "Mean (average)"),
                    help=(
                        "For each building we count, in every realization, how many times it floods "
                        "(annual-max water level above its first-floor elevation) from 2025 through the "
                        "selected horizon, giving 1,000 counts per building. This picks which statistic "
                        "of those counts colors the map; the mean is rounded to the nearest whole year."
                    ),
                )
            _fp_label = _fp_label or ss.get("_map_fo_last", "Mean (average)")
            ss["_map_fo_last"] = _fp_label
            flood_pct_key = _FP[_fp_label]

        # Flood map / Road map - which present-day flood level to map. The
        # levels themselves are edited under Advanced options (keys
        # map_fl_<key>); 0 means "not set" and the level is not offered.
        _terrain_view = map_view in ("Flood Map", "Road Map")
        _cfg_loc = _ui.location_cfg(ctx.registry, selected_location) or {}
        _fl_defaults = TV.level_defaults(loc_entry, _cfg_loc)
        _fl_values = {k: float(ss.get(f"map_fl_{k}_{selected_location}", _fl_defaults[k])) for k, _l, _h in TV.LEVELS}
        _fl_options = {f"{lbl} ({_fl_values[k]:.1f} ft)": k for k, lbl, _h in TV.LEVELS if _fl_values[k] > 0}
        flood_level_key = None
        if _terrain_view:
            if not _fl_options:
                st.warning("No present-day flood levels are set. Enter them under Advanced options.")
            else:
                _fl_first = next(iter(_fl_options))
                with ctx._keyed_container("map_fo_row"):
                    _fl_pick = st.segmented_control(
                        "Flood level", options=list(_fl_options), default=_fl_first,
                        selection_mode="single", key=f"map_fl_pick_{selected_location}",
                        on_change=_keep_selection,
                        args=(f"map_fl_pick_{selected_location}", "_map_fl_last", _fl_first),
                        help="Present-day flood level (ft NAVD88). The map adds the modeled sea-level rise for "
                             "the selected year and scenario. Edit the levels under Advanced options.",
                    )
                _fl_pick = _fl_pick if _fl_pick in _fl_options else ss.get("_map_fl_last", _fl_first)
                if _fl_pick not in _fl_options:
                    _fl_pick = _fl_first
                ss["_map_fl_last"] = _fl_pick
                flood_level_key = _fl_options[_fl_pick]

        # ---- Advanced options (collapsed) ----------------------------------
        # Adaptation Effectiveness - a measure is effective when it brings
        # the chosen statistic (P90 by default, or the median) of the
        # building's cumulative damage to or below the cutoff. The cutoff is
        # a dollar amount, a share of the structure value, or "removes 90%
        # of the damage". Below $0.50 counts as $0 (the hover rounds to
        # whole dollars).
        AE_ZERO = 0.5
        _AE_STATS = {"P90 (high outcome)": "P90", "Median": "P50"}
        _CUTOFF_OPTIONS = {
            "$0": 0.0, "$1k": 1000.0, "$5k": 5000.0, "$10k": 10000.0, "$15k": 15000.0, "$20k": 20000.0,
            "1% of structure value": ("pct", 0.01), "2% of structure value": ("pct", 0.02),
            "5% of structure value": ("pct", 0.05), "10% of structure value": ("pct", 0.10),
            "Removes 90% of the damage": ("cut", 0.90),
        }
        _CUTOFF_DEFAULT = "$15k"
        _bfe_default = (loc_entry or {}).get('bfe_ft')
        _bfe_default = float(_bfe_default) if _bfe_default is not None and pd.notna(_bfe_default) else 8.0
        elev_cutoff_ft = _bfe_default
        HIGHLIGHT_TTL_SEC = 8.0
        # Pamunkey defaults to 2x point size (few buildings, small dots at
        # the natural zoom); each location remembers its own setting.
        _default_scale = 2.0 if location_name == "Pamunkey" else 1.0

        # Advanced options: a switch plus a panel. The panel's widgets are
        # always rendered (hidden with CSS when the switch is off), so their
        # values persist, and the open/closed state survives view changes.
        adv_on = st.toggle("⚙️ Advanced options", key="map_adv_toggle")
        with ctx._keyed_container("map_adv_box"):
            if not adv_on:
                st.markdown("<style>.st-key-map_adv_box { display: none !important; }</style>", unsafe_allow_html=True)
            st.markdown("<div class='adapt-adv-label' style='margin-top:0'>Display</div>", unsafe_allow_html=True)
            a1, a2, a3, a4 = st.columns([1.15, 1.25, 1, 1], vertical_alignment="center")
            with a1:
                basemap_choice = st.segmented_control(
                    "Basemap", options=["Streets", "Aerial"], default="Streets",
                    selection_mode="single", key="map_basemap_seg",
                    on_change=_keep_selection, args=("map_basemap_seg", "_map_basemap_last", "Streets"),
                    help="**Streets**: OpenStreetMap roads and place labels. **Aerial**: ESRI World Imagery, "
                         "useful for spotting individual buildings and shoreline detail.",
                ) or "Streets"
            with a2:
                _point_scale = st.slider(
                    "Point size", min_value=0.5, max_value=4.0, value=_default_scale, step=0.25,
                    format="%.2fx", key=f"map_point_scale_{location_name}",
                    help="Scale all building markers up or down.",
                )
            with a3:
                ring_nonres = st.checkbox(
                    "Ring non-residential", key="map_ring_nonres",
                    help="Draw a black ring around every non-residential building (shops, offices, "
                         "churches, schools, government and industrial buildings) on every map view.",
                )
            with a4:
                ss.w_showzero = ss.cv_showzero
                st.checkbox("Show buildings with $0 damage", key="w_showzero", on_change=_cb_zero)

            # Effectiveness rule. Always drawn (disabled outside the Adaptation
            # effectiveness view) so the panel keeps its layout across views.
            _ae = (map_view == "Adaptation Effectiveness")
            st.markdown("<div class='adapt-adv-label'>Adaptation effectiveness rule</div>", unsafe_allow_html=True)
            b1, b2, b3 = st.columns([0.95, 1.25, 0.9], vertical_alignment="bottom")
            with b1:
                _stat_label = st.segmented_control(
                    "Damage statistic", options=list(_AE_STATS), default="P90 (high outcome)",
                    selection_mode="single", key="map_ae_stat_seg", disabled=not _ae,
                    on_change=_keep_selection, args=("map_ae_stat_seg", "_map_ae_stat_last", "P90 (high outcome)"),
                    help="Which statistic of the building's cumulative damage the rule tests. **P90**: the "
                         "damage exceeded in only 10% of the 1,000 simulated futures (a bad outcome). **Median**: "
                         "the damage exceeded in half of them (a typical outcome).",
                ) or ss.get("_map_ae_stat_last", "P90 (high outcome)")
            with b2:
                cutoff_label = st.selectbox(
                    "Minimal-damage cutoff", list(_CUTOFF_OPTIONS),
                    index=list(_CUTOFF_OPTIONS).index(_CUTOFF_DEFAULT),
                    key="map_ae_cutoff_v3", disabled=not _ae,
                    help="A measure counts as effective when it brings the chosen statistic of the building's "
                         "cumulative damage through the target year to or below this cutoff. The cutoff is also "
                         "the 'no damage' line for the green category. 'Removes 90% of the damage' compares each "
                         "measure with the building's own damage under no action instead.",
                )
            with b3:
                elev_cutoff_ft = st.number_input(
                    "Elevation cutoff (ft NAVD88)",
                    value=float(_bfe_default), step=0.5, format="%.1f",
                    key=f"map_ae_elev_cutoff_{location_name}", disabled=not _ae,
                    help=f"Buildings whose first floor is at or above this elevation are not considered for "
                         f"elevation and are never colored Residual. Default = BFE "
                         f"({_bfe_default:g} ft) = DFE - 2 ft. Set it to the DFE ({_bfe_default + 2:g} ft) to "
                         f"include every Under-DFE building.",
                )

            # Flood map / Road map inputs (disabled outside those views).
            st.markdown("<div class='adapt-adv-label'>Flood and road maps · present-day flood levels (ft NAVD88)</div>",
                        unsafe_allow_html=True)
            _flc = st.columns(len(TV.LEVELS), gap="small")
            for _col, (_k, _lbl, _help) in zip(_flc, TV.LEVELS):
                with _col:
                    st.number_input(_lbl, value=float(_fl_defaults[_k]), min_value=0.0, step=0.1, format="%.2f",
                                    key=f"map_fl_{_k}_{selected_location}", disabled=not _terrain_view, help=_help)
            d1, d2, d3, d4 = st.columns([1.0, 0.9, 1.15, 1.25], vertical_alignment="bottom")
            with d1:
                _detail_label = st.selectbox("Terrain detail", list(TV.DETAIL), index=0, key="map_terrain_detail",
                                             disabled=not _terrain_view,
                                             help="Grid size of the USGS 3DEP terrain. Finer is sharper and slower.")
            with d2:
                _slr_override = st.number_input("Sea-level rise override (ft)", value=0.0, min_value=0.0, step=0.1,
                                                format="%.2f", key="map_slr_override", disabled=not _terrain_view,
                                                help="0 = the modeled rise for the selected year and scenario. Enter "
                                                     "a value to map a rise of your own instead.")
            with d3:
                _road_access = st.checkbox("Roads: mark cut-off roads", value=True, key="map_road_access",
                                           disabled=map_view != "Road Map",
                                           help="A dry road is 'cut off' when flooding severs every dry route from "
                                                "it to the main road network.")
            with d4:
                _road_entry = st.checkbox("Roads: keep the main entrance open", value=True, key="map_road_entry",
                                          disabled=map_view != "Road Map",
                                          help="The access road off the main road often floods first and would "
                                               "make the whole area read as cut off. With this on it still shows "
                                               "flooded but does not cut the area off.")
            _road_entry_xy = ""
            if map_view == "Road Map" and _road_entry:
                _road_entry_xy = st.text_input("Entrance override (lat, lon; blank = auto-detect)", value="",
                                               key="map_road_entry_xy", placeholder="e.g. 40.7650, -72.8450")

            st.markdown("<div class='adapt-adv-label'>Filters and search</div>", unsafe_allow_html=True)
            c0, c1, c2, c3 = st.columns([0.85, 1.75, 1.0, 0.8], vertical_alignment="bottom")
            with c0:
                # Occupancy is set here for the whole Community Planning
                # module (the overview and building pages follow it and show
                # it in their subheaders).
                ctx.render_occupancy("Occupancy", help="Which buildings the maps, the community overview and the "
                                                       "building reports include. Applies to all Community "
                                                       "Planning pages.")
            with c1:
                if fp_options:
                    ss.w_dfe = ss.cv_dfe
                    st.multiselect("DFE status filter", fp_options, key="w_dfe", on_change=_cb_dfe)
            with c2:
                search_id_text = st.text_input(
                    "Find building by ID", value="", key="map_search_bldg_id", placeholder="e.g. 8466717",
                    help=(f"Enter a Building ID and press Find. A magenta ring flashes on that building for "
                          f"~{int(HIGHLIGHT_TTL_SEC)} seconds, then disappears. Clear removes it at once."),
                )
            with c3:
                btn_a, btn_b = st.columns(2)
                with btn_a:
                    find_clicked = st.button("🔍 Find", key="map_search_find", use_container_width=True)
                with btn_b:
                    clear_clicked = st.button("✖ Clear", key="map_search_clear", use_container_width=True)
        ss["_map_ae_stat_last"] = _stat_label
        _res_m, _target_px = TV.DETAIL[_detail_label]
        _fl_values = {k: float(ss.get(f"map_fl_{k}_{selected_location}", _fl_defaults[k])) for k, _l, _h in TV.LEVELS}
        _ae_stat = _AE_STATS[_stat_label]                 # 'P90' or 'P50'
        _ae_stat_word = "P90" if _ae_stat == "P90" else "median"
        _cutoff_spec = _CUTOFF_OPTIONS[cutoff_label]
        _cutoff_is_cut = isinstance(_cutoff_spec, tuple) and _cutoff_spec[0] == "cut"

        # Resolve the click → session-state interaction
        import time as _time_mod
        if 'map_highlight_id' not in st.session_state:
            st.session_state['map_highlight_id']    = None
            st.session_state['map_highlight_until'] = 0.0
            st.session_state['map_highlight_msg']   = None

        if clear_clicked:
            st.session_state['map_highlight_id']    = None
            st.session_state['map_highlight_until'] = 0.0
            st.session_state['map_highlight_msg']   = None
        elif find_clicked and search_id_text.strip():
            # Try to coerce the user's input to the same dtype as the
            # 'id' column. The bundle stores ids as ints, but a user
            # might paste them with whitespace, leading zeros, or a
            # stray decimal point - be forgiving.
            raw = search_id_text.strip()
            try:
                bid_target = int(float(raw))
            except (TypeError, ValueError):
                bid_target = None

            if bid_target is None:
                st.session_state['map_highlight_msg'] = (
                    'warning', f"'{raw}' isn't a valid building ID."
                )
                st.session_state['map_highlight_id']    = None
                st.session_state['map_highlight_until'] = 0.0
            else:
                found_row = df_buildings[df_buildings['id'] == bid_target]
                if found_row.empty:
                    st.session_state['map_highlight_msg'] = (
                        'warning',
                        f"Building #{bid_target} isn't in the current "
                        f"selection. Try clearing the DFE / occupancy "
                        f"filters or check the ID."
                    )
                    st.session_state['map_highlight_id']    = None
                    st.session_state['map_highlight_until'] = 0.0
                else:
                    st.session_state['map_highlight_id']    = bid_target
                    st.session_state['map_highlight_until'] = (
                        _time_mod.time() + HIGHLIGHT_TTL_SEC
                    )
                    st.session_state['map_highlight_msg'] = (
                        'success',
                        f"Highlighting #{bid_target} for "
                        f"~{int(HIGHLIGHT_TTL_SEC)}s."
                    )

        # Surface any feedback message from the resolution above.
        _hl_msg = st.session_state.get('map_highlight_msg')
        if _hl_msg:
            _kind, _text = _hl_msg
            if _kind == 'warning':
                st.warning(_text, icon='⚠️')
            elif _kind == 'success':
                st.caption(f"✓ {_text}")

        df_map = prepare_map_data(df_buildings, target_year, scenario)

        if df_map is None or len(df_map) == 0:
            st.warning("No buildings match the current filters.")
        else:
            if dfe_filter and 'DFE_Status' in df_map.columns:
                df_map = df_map[df_map['DFE_Status'].isin(dfe_filter)]

            # ----------------------------------------------------------
            # "Hide $0-damage buildings" - pick the right damage metric
            # ----------------------------------------------------------
            # The hide filter must look at the SAME statistic the active
            # map view colors by, otherwise we hide buildings the user
            # would expect to see:
            #   * Damage Heatmap        → P50 (what the heatmap colors)
            #   * Damage Bins           → P90 (the bins are upper-tail)
            #   * Adaptation Effective. → the chosen statistic (P90 default)
            # In particular, for the bins view, hiding by P50 would
            # silently drop every building with P50 = 0 but P90 > 0 -
            # the very buildings that drive tail-risk planning.
            if map_view in ("Flood Occurrences", "Flood Map", "Road Map"):
                # These views show ALL buildings (a building that never
                # floods is drawn green, not hidden), so the damage-based
                # "$0 damage" hide filter doesn't apply here.
                zero_filter_col = None
            elif map_view == "Damage Heatmap":
                zero_filter_col = 'No mitigation_P50' if 'No mitigation_P50' in df_map.columns else None
            elif map_view == "Adaptation Effectiveness":
                zero_filter_col = (f'No mitigation_{_ae_stat}' if f'No mitigation_{_ae_stat}' in df_map.columns
                                   else 'No mitigation_P50' if 'No mitigation_P50' in df_map.columns else None)
            else:
                # Upper-tail view - fall back to P50 only if P90 isn't loaded
                zero_filter_col = (
                    'No mitigation_P90' if 'No mitigation_P90' in df_map.columns
                    else 'No mitigation_P50' if 'No mitigation_P50' in df_map.columns
                    else None
                )

            # The downstream colorbar/hover code still keys off P50
            # (it expects the heatmap's coloring metric), so keep
            # baseline_col aligned with the heatmap convention.
            baseline_col = 'No mitigation_P50' if 'No mitigation_P50' in df_map.columns else None

            if zero_filter_col and not show_zero_damage:
                df_map = df_map[df_map[zero_filter_col] > 0]

            if len(df_map) == 0:
                st.warning("No buildings match the current filters.")
            else:
                if baseline_col:
                    non_zero_damages = df_map[df_map[baseline_col] > 0][baseline_col]
                    if len(non_zero_damages) > 0:
                        # Absolute max damage (used for hover context)
                        max_damage_raw = float(non_zero_damages.max())
                        # Cap the colorbar at the 90th percentile of nonzero
                        # damages so the gradient is readable across the bulk
                        # of buildings instead of being compressed by the
                        # single worst outlier. Buildings above the cap still
                        # render at the top red (their true damage shows in
                        # the hover tooltip).
                        p90_damage = float(non_zero_damages.quantile(0.90))
                        # Snap to a nice rounded value for the legend
                        color_cap = nice_round_up(p90_damage)
                        # If the data is very flat (cap collapses to 0), fall
                        # back to the true max so we don't render a dead scale.
                        if color_cap <= 0 or color_cap >= max_damage_raw:
                            color_cap = max_damage_raw
                            cap_is_clipped = False
                        else:
                            cap_is_clipped = True
                    else:
                        max_damage_raw = 1.0
                        color_cap = 1.0
                        cap_is_clipped = False
                    max_damage = color_cap   # legacy alias used elsewhere
                else:
                    max_damage_raw = 1.0
                    color_cap = 1.0
                    cap_is_clipped = False
                    max_damage = 1.0

                action_cols_p50 = [col for col in df_map.columns if col.endswith('_P50')]
                # The hover lists each measure at the statistic the active
                # view classifies by: the chosen statistic (with its % cut
                # and a check mark when the rule is met) in Adaptation
                # effectiveness, the median everywhere else.
                _hover_ae = (map_view == "Adaptation Effectiveness")
                _hstat = _ae_stat if _hover_ae else 'P50'
                _hover_p90 = (_hstat == 'P90')
                action_cols_hover = [col for col in df_map.columns if col.endswith(f'_{_hstat}')]

                # ---- Mark non-residential buildings (used by categorical maps) ----
                if 'occupancy_type' in df_map.columns:
                    is_nonres_series = ~df_map['occupancy_type'].apply(is_residential)
                else:
                    is_nonres_series = pd.Series(False, index=df_map.index)
                df_map = df_map.copy()
                df_map['_is_nonres'] = is_nonres_series.values

                # ---- Build the standard hover text (shared across all views) ----
                # Hover text is duplicated across every map trace × every
                # building, so each byte multiplies into the JSON payload.
                # We keep the new NSI fields (address, year built, etc.)
                # but render them compactly so even ~5,000-building
                # locations stay well under Streamlit's per-chart JSON
                # limit (~3 MB before the frontend starts truncating).
                hover_texts = []
                for idx, row in df_map.iterrows():
                    addr = row.get('address') if 'address' in row else None
                    if pd.notna(addr) and str(addr).strip():
                        text = f"<b>{addr}</b> <span style='color:#94a3b8'>#{row['id']}</span><br>"
                    else:
                        text = f"<b>Building #{row['id']}</b><br>"

                    # One compact attribute line: type · stories · sqft · foundation · year
                    attr_bits = []
                    if 'occupancy_type' in row and pd.notna(row.get('occupancy_type')):
                        attr_bits.append(str(row['occupancy_type']))
                    if 'number_of_stories' in row and pd.notna(row.get('number_of_stories')):
                        attr_bits.append(f"{int(row['number_of_stories'])}-story")
                    if 'area' in row and pd.notna(row.get('area')):
                        attr_bits.append(f"{int(row['area']):,} sqft")
                    if 'foundation_type' in row and pd.notna(row.get('foundation_type')):
                        attr_bits.append(f"fnd {row['foundation_type']}")
                    if 'year_built' in row and pd.notna(row.get('year_built')):
                        attr_bits.append(f"{int(row['year_built'])}")
                    if attr_bits:
                        text += " · ".join(attr_bits) + "<br>"

                    # One compact value line: structure value + DFE status
                    val_bits = []
                    if 'structure_value' in row and pd.notna(row['structure_value']):
                        val_bits.append(f"{format_currency(row['structure_value'])}")
                    if 'DFE_Status' in row and pd.notna(row.get('DFE_Status')):
                        val_bits.append(str(row['DFE_Status']))
                    if val_bits:
                        text += " · ".join(val_bits) + "<br>"

                    text += ("<br><b>Cumulative Damage by strategy (P90)</b><br>" if _hover_p90
                             else "<br><b>Cumulative Damage by strategy (median)</b><br>" if _hover_ae
                             else "<br><b>Cumulative Damage by strategy</b><br>")

                    # No Mitigation gets two values because the three map
                    # views use TWO different statistics to color the
                    # buildings:
                    #   • Damage Heatmap         → P50 (median)
                    #   • Damage Bins            → P90 (upper tail)
                    #   • Adaptation Effectiveness → chosen statistic (P90 or
                    #     median) tested against the cutoff
                    # Showing only the P50 here used to confuse users on
                    # the P90-based views: a building with P50 = $14.6k
                    # and P90 = $250k would land in a "$100k–$500k" bin
                    # and look misclassified, because the user couldn't
                    # see the P90 value that put it there. Surface both
                    # so the bin / category placement is always traceable
                    # from the hover.
                    baseline_p50 = row.get('No mitigation_P50', 0)
                    baseline_p90 = row.get('No mitigation_P90', np.nan)
                    # Savings % in the loop below are relative to the
                    # baseline at the hover statistic.
                    baseline_val = baseline_p90 if _hover_p90 else baseline_p50
                    # The dollar line each measure must reach in this view.
                    if _hover_ae:
                        if _cutoff_is_cut:
                            _row_thr = (1.0 - _cutoff_spec[1]) * (baseline_val if pd.notna(baseline_val) else 0.0)
                        elif isinstance(_cutoff_spec, tuple):
                            _sv_row = row.get('structure_value', np.nan)
                            _row_thr = (float(_sv_row) * _cutoff_spec[1]) if pd.notna(_sv_row) else AE_ZERO
                        else:
                            _row_thr = float(_cutoff_spec)
                        _row_thr = max(_row_thr, AE_ZERO)

                    if (pd.notna(baseline_p90)
                        and pd.notna(baseline_p50)
                        and not np.isclose(baseline_p50, baseline_p90)):
                        text += (
                            f"<b>No Mitigation</b>: "
                            f"{format_currency(baseline_p50)} (median) · "
                            f"{format_currency(baseline_p90)} (P90)<br>"
                        )
                        _no_mit_shown_inline = True
                    else:
                        _no_mit_shown_inline = False

                    # Mobile/manufactured homes (RES2) have only one realistic
                    # retrofit - raising (elevating) the whole home - so their
                    # hover lists just Elevate (+ the No-Mitigation baseline).
                    # Any non-RES2 home (RES1, RES3, RES4, …) shows every
                    # applicable option. This is decided per building from its
                    # own occupancy type, so a RES4 sitting inside a
                    # mobile-homes-dominated area still shows its full menu.
                    _row_occ = str(row.get('occupancy_type', '') or '').upper()
                    _row_is_mobile = _row_occ.startswith('RES2')

                    # Wet-floodproofing the FIRST floor only makes sense for
                    # multi-story buildings (you retreat upstairs). For a
                    # single-story building it would mean floodproofing the
                    # whole living space, so we hide that line from the hover -
                    # mirroring the building-detail cards. NaN stories => hide.
                    # Exception (MATLAB v14): a one-story home flagged
                    # OneStoryAddStory is scored as an add-a-story conversion,
                    # so its line stays and reads "Add a story". One-story is
                    # decided with the MATLAB rule (SOID story digit for RES1).
                    _row_add_story = _flag_true(row.get('OneStoryAddStory'))
                    _row_one_story = (not _row_add_story) and _is_one_story_bldg(
                        row.get('SOID'), row.get('number_of_stories'))
                    # Elevation not advised for this building (first floor
                    # within the location's threshold of the DFE): the
                    # Elevate value equals no action, so hide the line.
                    _row_elev_ok = _elev_eligible_flag(row.get('ElevationEligible'))

                    for col in action_cols_hover:
                        action_name = col.replace(f'_{_hstat}', '')
                        val = row.get(col, 0)

                        # RES2 (mobile/manufactured): suppress every non-Elevate
                        # action from the hover's strategy list, leaving the
                        # No-Mitigation baseline and Elevate. Non-RES2 homes fall
                        # through and show their full applicable set.
                        if _row_is_mobile and action_name not in _RAISE_ONLY_ACTIONS:
                            continue

                        # Single-story buildings: wet-floodproofing the first
                        # floor is not an applicable strategy - hide the line.
                        if action_name == 'WFP 1st' and _row_one_story:
                            continue
                        if action_name == 'Elevate' and not _row_elev_ok:
                            continue

                        # Skip retrofits that don't physically apply to this
                        # building. Those rows were dropped in load_bundle's
                        # applicability filter, which means the wide-form
                        # pivot in prepare_map_data() left this cell NaN.
                        # Rendering a NaN here would either show "$nan" or,
                        # via the format_currency rounding, a misleading $0.
                        # Skipping the line entirely is what "hide it
                        # completely" actually looks like in the hover.
                        if action_name != 'No mitigation' and pd.isna(val):
                            continue

                        # Skip the No-mitigation column here if we already
                        # rendered it inline with both percentiles above.
                        if action_name == 'No mitigation' and _no_mit_shown_inline:
                            continue

                        display_name = action_name
                        if action_name == 'WFP B':
                            display_name = 'WFP Basement'
                        elif action_name == 'WFP 1st':
                            display_name = (ADD_STORY_LABEL if _row_add_story
                                            else 'WFP 1st Floor')

                        if action_name == 'No mitigation':
                            text += f"<b>No Mitigation</b>: {format_currency(val)}<br>"
                        else:
                            savings = baseline_val - val if baseline_val > 0 else 0
                            pct = (savings / baseline_val * 100) if baseline_val > 0 else 0
                            if _hover_ae:
                                # Check mark = this measure meets the rule.
                                _ok = val <= _row_thr * (1 + 1e-9)
                                _pct_txt = f" (-{math.floor(pct):.0f}%)" if savings > 0 else ""
                                text += (f"{display_name}: {format_currency(val)}{_pct_txt}"
                                         f"{' ✓' if _ok else ''}<br>")
                            elif savings > 0:
                                text += f"{display_name}: {format_currency(val)} (-{pct:.0f}%)<br>"
                            else:
                                text += f"{display_name}: {format_currency(val)}<br>"

                    hover_texts.append(text)
                df_map['hover_text'] = hover_texts
                # Two-element customdata: [hover_html, building_id]
                # The id rides along so click events can identify the building.
                df_map['hover_data'] = [
                    [t, int(i)] for t, i in zip(hover_texts, df_map['id'])
                ]

                # ---- Common map center ----
                center_lat = df_map['latitude'].mean()
                center_lon = df_map['longitude'].mean()

                # Default zoom: frame the community tightly rather than the
                # old fixed wide zoom. Fit the building extent (with a little
                # padding) to a Web-Mercator zoom, clamped to a sensible range.
                try:
                    _mlat = df_map['latitude'].dropna().to_numpy()
                    _mlon = df_map['longitude'].dropna().to_numpy()
                    if _mlat.size >= 2:
                        _lon_span = max((float(np.nanmax(_mlon)) - float(np.nanmin(_mlon))) * 1.3, 1e-3)
                        _lat_span = max((float(np.nanmax(_mlat)) - float(np.nanmin(_mlat))) * 1.3, 1e-3)
                        _zoom_lon = math.log2(360.0 / _lon_span)
                        _cl = max(math.cos(math.radians(float(center_lat))), 1e-6)
                        _zoom_lat = math.log2((360.0 / (_lat_span * 1.4)) * _cl)
                        _default_map_zoom = float(min(_zoom_lon, _zoom_lat))
                        if _default_map_zoom >= 11.5:
                            # A small community: bias toward a closer view.
                            _default_map_zoom = max(min(_default_map_zoom + 1.5, 17.5), 13.0)
                        else:
                            # A wide area (a county): show all of it, centred on
                            # the middle of the extent rather than the mean point.
                            _default_map_zoom = max(min(_default_map_zoom + 0.8, 13.0), 8.5)
                            center_lat = (float(np.nanmax(_mlat)) + float(np.nanmin(_mlat))) / 2.0
                            center_lon = (float(np.nanmax(_mlon)) + float(np.nanmin(_mlon))) / 2.0
                    else:
                        _default_map_zoom = 15.5
                except Exception:
                    _default_map_zoom = 15.5

                # Tighter framing for the Pamunkey study area using the
                # team-supplied corner coordinates, so the initial view
                # focuses on the village rather than the full extent.
                if selected_location == "Pamunkey":
                    _pam_lat = [37.5830, 37.5831, 37.5639, 37.5633]
                    _pam_lon = [-76.9852, -77.0255, -77.0257, -76.9844]
                    center_lat = sum(_pam_lat) / len(_pam_lat)
                    center_lon = sum(_pam_lon) / len(_pam_lon)
                    try:
                        _plon = max((max(_pam_lon) - min(_pam_lon)) * 1.12, 1e-3)
                        _plat = max((max(_pam_lat) - min(_pam_lat)) * 1.12, 1e-3)
                        _pz_lon = math.log2(360.0 / _plon)
                        _pcl = max(math.cos(math.radians(center_lat)), 1e-6)
                        _pz_lat = math.log2((360.0 / (_plat * 1.4)) * _pcl)
                        _default_map_zoom = float(max(min(min(_pz_lon, _pz_lat) + 0.4, 17.5), 13.5))
                    except Exception:
                        _default_map_zoom = 14.5

                # `_point_scale` is set by the "Point size" slider at the
                # top of the map tab (see search_col3 above). All map
                # markers below multiply their literal sizes by this
                # factor so the user can dial dot size up/down on the fly.
                # Defaults to 2× for Pamunkey, 1× for other locations.

                # =====================================================
                # Helper: ring a small set of specific Tribal buildings
                # beneath any colored category trace. Only the IDs in
                # _HIGHLIGHT_RING_IDS are ringed, and only for Pamunkey -
                # every other building (residential or not) is left
                # unringed. (Replaces the old non-residential ring.)
                # =====================================================
                _HIGHLIGHT_RING_IDS = {10001, 10000013, 10002, 579513008, 10016}

                def _ring_mask(df_subset):
                    """Rows to ring: the Pamunkey Tribal set, plus every
                    non-residential building when the 'Ring non-residential'
                    box is ticked."""
                    mask = pd.Series(False, index=df_subset.index)
                    if location_name == "Pamunkey":
                        _ids = pd.to_numeric(df_subset['id'], errors='coerce')
                        mask |= _ids.isin(_HIGHLIGHT_RING_IDS)
                    if ring_nonres and 'occupancy_type' in df_subset.columns:
                        mask |= ~df_subset['occupancy_type'].apply(is_residential)
                    return mask

                def _add_highlight_ring(fig, df_subset, ring_size=13):
                    if len(df_subset) == 0:
                        return
                    hl = df_subset[_ring_mask(df_subset)]
                    if len(hl) > 0:
                        fig.add_trace(go.Scattermapbox(
                            lat=hl['latitude'], lon=hl['longitude'],
                            mode='markers',
                            marker=dict(size=ring_size, color='black', opacity=1.0),
                            hoverinfo='skip',
                            showlegend=False,
                            name='_highlight_ring',
                        ))

                def _add_highlight_ring_legend(fig):
                    """Legend-only stubs explaining the black rings, shown
                    only when something on screen is actually ringed."""
                    if location_name == "Pamunkey" and pd.to_numeric(
                            df_map['id'], errors='coerce').isin(_HIGHLIGHT_RING_IDS).any():
                        fig.add_trace(go.Scattermapbox(
                            lat=[None], lon=[None], mode='markers',
                            marker=dict(size=10 * _point_scale, color='black', opacity=1.0),
                            name='Tribal buildings (ringed)',
                            showlegend=True, hoverinfo='skip',
                        ))
                    if ring_nonres and 'occupancy_type' in df_map.columns and \
                            (~df_map['occupancy_type'].apply(is_residential)).any():
                        fig.add_trace(go.Scattermapbox(
                            lat=[None], lon=[None], mode='markers',
                            marker=dict(size=10 * _point_scale, color='black', opacity=1.0),
                            name='Non-residential (black ring)',
                            showlegend=True, hoverinfo='skip',
                        ))

                fig_map = go.Figure()
                bin_caption_extra = ""  # for the Damage Bins view
                ae_gap_note = ""        # for the Adaptation Effectiveness view
                flood_occ_note = ""     # for the Flood Occurrences view
                _extra_layers = []      # flood-depth image layer for the Flood/Road map views
                terrain_note = ""       # caption for those views
                _flood_occ_df = None    # populated by the Flood Occurrences view

                # =====================================================
                # VIEW 1 - Damage Heatmap (existing; continuous color)
                # =====================================================
                if map_view == "Damage Heatmap":
                    if baseline_col:
                        df_zero = df_map[df_map[baseline_col] == 0]
                        df_nonzero = df_map[df_map[baseline_col] > 0]
                    else:
                        df_zero = pd.DataFrame()
                        df_nonzero = df_map

                    # Ring the Tribal buildings beneath the markers.
                    _add_highlight_ring(fig_map, df_map, ring_size=13 * _point_scale)

                    if len(df_zero) > 0:
                        fig_map.add_trace(go.Scattermapbox(
                            lat=df_zero['latitude'], lon=df_zero['longitude'],
                            mode='markers',
                            marker=dict(size=8 * _point_scale, color='#22c55e', opacity=0.85),
                            hovertemplate='%{customdata[0]}<extra></extra>',
                            customdata=list(df_zero['hover_data']),
                            name='No Damage ($0)'
                        ))

                    if len(df_nonzero) > 0 and baseline_col:
                        cb_ticks, cb_labels = smart_money_ticks(color_cap, target_n=5)
                        # If we're clipping at the 90th-percentile cap, mark the
                        # top tick as "≥ X" so the user knows a worse building
                        # would still render at the top red.
                        if cap_is_clipped and len(cb_labels) > 0:
                            cb_labels = list(cb_labels)
                            cb_labels[-1] = f"≥ {cb_labels[-1]}"
                        fig_map.add_trace(go.Scattermapbox(
                            lat=df_nonzero['latitude'], lon=df_nonzero['longitude'],
                            mode='markers',
                            marker=dict(
                                size=10 * _point_scale,
                                color=df_nonzero[baseline_col],
                                colorscale=[
                                    [0,    '#facc15'],
                                    [0.25, '#f59e0b'],
                                    [0.50, '#f97316'],
                                    [0.75, '#ef4444'],
                                    [1.0,  '#b91c1c'],
                                ],
                                cmin=0, cmax=color_cap,
                                colorbar=dict(
                                    title="No Mitigation<br>Cumulative Damage",
                                    tickmode='array',
                                    tickvals=cb_ticks, ticktext=cb_labels,
                                    len=0.7, y=0.5,
                                ),
                                opacity=0.85
                            ),
                            hovertemplate='%{customdata[0]}<extra></extra>',
                            customdata=list(df_nonzero['hover_data']),
                            name='At Risk'
                        ))
                    _add_highlight_ring_legend(fig_map)

                # =====================================================
                # VIEW 2 - Adaptation Effectiveness
                # A measure is effective when it brings the chosen statistic
                # (P90 or median) of the building's cumulative damage through
                # the target year to or below the cutoff: a dollar amount, a
                # share of the structure value, or a 90% cut of the damage
                # under no action. The hover shows each measure at that
                # statistic with a check mark when it meets the rule.
                # =====================================================
                elif map_view == "Adaptation Effectiveness":
                    # Raise Utilities and WFP Basement are separate groups.
                    # Either column may be absent for an inventory (WFP
                    # Basement is dropped upstream at Pamunkey's RES2/pier
                    # inventory), in which case that group is empty and
                    # omitted.
                    col_nomit  = f'No mitigation_{_ae_stat}'
                    col_elev   = f'Elevate_{_ae_stat}'
                    col_raiseu = f'Raise Utilities_{_ae_stat}'
                    col_wfpb   = f'WFP B_{_ae_stat}'
                    _has_raiseu = col_raiseu in df_map.columns
                    _has_wfpb   = col_wfpb   in df_map.columns

                    # No mitigation + Elevate are required; the cheap
                    # retrofits are optional.
                    _core_cols = (col_nomit, col_elev)
                    missing = [c for c in _core_cols if c not in df_map.columns]
                    if missing:
                        st.warning(
                            f"This view needs {_ae_stat} columns for No mitigation and "
                            f"Elevate. Missing: {', '.join(missing)}"
                        )
                    else:
                        # Dollar line per building: cutoff, share of value, or
                        # 10% of the baseline for the 90%-cut rule. Never below
                        # $0.50, so a hover that reads $0 always passes.
                        if _cutoff_is_cut:
                            thr = None                 # set after the baseline is known
                        elif isinstance(_cutoff_spec, tuple):
                            _sv = pd.to_numeric(df_map.get('structure_value'), errors='coerce') \
                                if 'structure_value' in df_map.columns else pd.Series(np.nan, index=df_map.index)
                            thr = np.where(np.isfinite(_sv.values.astype(float)),
                                           _sv.values.astype(float) * _cutoff_spec[1], AE_ZERO)
                            thr = np.maximum(thr, AE_ZERO)
                        else:
                            thr = max(float(_cutoff_spec), AE_ZERO)

                        # Preserve NaN so missing retrofit values don't silently
                        # count as effective (fillna(0) would do that).
                        no_mit_raw = df_map[col_nomit].values.astype(float)
                        elev_raw   = df_map[col_elev].values.astype(float)

                        no_mit = np.where(np.isnan(no_mit_raw), 0.0, no_mit_raw)
                        elev   = np.where(np.isnan(elev_raw), no_mit, elev_raw)
                        if _cutoff_is_cut:
                            thr = np.maximum((1.0 - _cutoff_spec[1]) * no_mit, AE_ZERO)
                        # Most damage a measure may leave and still count as
                        # effective. "Damaged" (not green) = baseline above the
                        # dollar cutoff; for the 90%-cut rule, baseline above $0.
                        _left_max = thr * (1 + 1e-9)
                        _green_line = AE_ZERO if _cutoff_is_cut else thr

                        # Per-retrofit "meets the rule" masks, computed
                        # SEPARATELY for Raise Utilities and WFP Basement so
                        # each gets its own group. NaN in a retrofit column
                        # ==> "retrofit not applied" ==> baseline (so a
                        # missing value can't push a building into a group).
                        # An absent column ==> an all-False mask.
                        def _brings_to_thr(colname, present):
                            if not present:
                                return np.zeros(len(df_map), dtype=bool)
                            _raw = df_map[colname].values.astype(float)
                            _val = np.where(np.isnan(_raw), no_mit, _raw)
                            return _val <= _left_max
                        raiseu_to_thr = _brings_to_thr(col_raiseu, _has_raiseu)
                        wfpb_to_thr   = _brings_to_thr(col_wfpb,   _has_wfpb)

                        # --- MAP classifier ---
                        # Buckets ordered by the cheapest measure that meets
                        # the rule. Reading the colors top to bottom gives a
                        # decision pyramid: do nothing -> cheap retrofit ->
                        # elevation -> nothing works.
                        #
                        #   1 = No Damage        baseline at or below the cutoff
                        #   2 = Raise Utilities  meets the rule
                        #   3 = WFP Basement     Raise Utilities does not, WFP
                        #                        Basement meets the rule
                        #   4 = Elevation        neither cheap retrofit does,
                        #                        elevation meets the rule
                        #   5 = Residual         Under-DFE, first floor below
                        #                        the elevation cutoff, and even
                        #                        elevation misses the rule. The
                        #                        conversation moves to buyout,
                        #                        relocation or community-scale
                        #                        measures.
                        #   6 = Not drawn        Above-DFE buildings (their
                        #                        options differ: WFP 1st floor,
                        #                        dry floodproofing) and
                        #                        Under-DFE buildings at or above
                        #                        the elevation cutoff that no
                        #                        cheap retrofit clears.
                        elev_to_thr = elev <= _left_max

                        # Under-DFE membership mask (Above-DFE buildings
                        # are NEVER classified as Residual - they fall
                        # through to cat=6 / omitted instead).
                        if 'DFE_Status' in df_map.columns:
                            _dfe_lower = (df_map['DFE_Status']
                                          .fillna('').astype(str)
                                          .str.strip().str.lower())
                            is_under_dfe = _dfe_lower.str.contains('under').values
                        else:
                            is_under_dfe = np.zeros(len(df_map), dtype=bool)

                        # Above-BFE exclusion: a home whose first-floor
                        # elevation (FFE) is at or above the Base Flood
                        # Elevation is already reasonably protected, so it
                        # must NOT be colored Residual Damage - even when it
                        # sits in the BFE-to-DFE freeboard band (still tagged
                        # "Under DFE"). Only homes we can affirmatively place
                        # BELOW BFE stay eligible for Residual; a missing
                        # FFE or BFE leaves a building eligible (we don't have
                        # grounds to exclude it). NaN FFE compares False here.
                        _bfe_val = elev_cutoff_ft          # adjustable; default = BFE = DFE - 2
                        if 'FFE_ft' in df_map.columns and _bfe_val is not None and pd.notna(_bfe_val):
                            _ffe_vals = pd.to_numeric(df_map['FFE_ft'], errors='coerce').values.astype(float)
                            is_above_bfe = _ffe_vals >= float(_bfe_val)
                        else:
                            is_above_bfe = np.zeros(len(df_map), dtype=bool)

                        # Priority classification. Default = 6 (out of
                        # scope / omitted) so any building not affirmatively
                        # placed in 1-5 drops off the map quietly. Cheapest
                        # working measure first (Raise Utilities before WFP
                        # Basement, per the workshop cost ordering).
                        _dmg = no_mit > _green_line
                        cat = np.full(len(df_map), 6, dtype=int)
                        cat[~_dmg] = 1
                        cat[_dmg & raiseu_to_thr] = 2
                        cat[_dmg & ~raiseu_to_thr & wfpb_to_thr] = 3
                        cat[_dmg & ~raiseu_to_thr & ~wfpb_to_thr & elev_to_thr] = 4
                        cat[_dmg & ~raiseu_to_thr & ~wfpb_to_thr & ~elev_to_thr
                            & is_under_dfe & ~is_above_bfe] = 5

                        df_map['_cat_action'] = cat
                        # Damaged buildings between the BFE and the DFE whose
                        # Elevate result equals the baseline (an older run that
                        # did not compute elevation for them) are not drawn in
                        # this view. Say so, with the count, instead of leaving
                        # silent holes on the map.
                        _elev_missing = np.isnan(elev_raw) | (elev_raw == no_mit_raw)
                        _undrawn = (cat == 6) & is_under_dfe & _dmg
                        _n_gap = int(_undrawn.sum())
                        _n_noelev = int((_undrawn & _elev_missing).sum())
                        ae_gap_note = ""
                        if _n_gap > 0:
                            ae_gap_note = (
                                f" **{_n_gap:,} damaged Under-DFE buildings are not drawn**: their first floor is at or "
                                f"above the elevation cutoff ({float(elev_cutoff_ft):g} ft) and no cheaper "
                                "measure meets the rule."
                            )
                            if _n_noelev > 0:
                                ae_gap_note += (
                                    f" {_n_noelev:,} of them also have no elevation estimate in the current results "
                                    "(older elevation rule); re-running the model in the Developer module adds one."
                                )

                        # Legend counts - each building appears in exactly
                        # one bucket, so these add up to (buildings shown).
                        cat_legend_counts = {
                            1: int((cat == 1).sum()),
                            2: int((cat == 2).sum()),
                            3: int((cat == 3).sum()),
                            4: int((cat == 4).sum()),
                            5: int((cat == 5).sum()),
                        }

                        # Palette walks the decision pyramid green -> lime ->
                        # yellow -> orange -> red. cat=6 (Above-DFE
                        # fall-through) is intentionally absent from cat_specs
                        # and therefore not plotted.
                        cat_specs = [
                            (1, 'No Damage',        '#22c55e'),  # green
                            (2, 'Raise Utilities',  '#a3e635'),  # lime
                            (3, 'WFP Basement',     '#facc15'),  # yellow
                            (4, 'Elevation',        '#f97316'),  # orange
                            (5, 'Residual Damage',  '#dc2626'),  # red
                        ]
                        # Drop a cheap-retrofit group entirely when its column
                        # isn't present for this inventory (its bucket is
                        # necessarily empty).
                        if not _has_raiseu:
                            cat_specs = [cs for cs in cat_specs if cs[0] != 2]
                        if not _has_wfpb:
                            cat_specs = [cs for cs in cat_specs if cs[0] != 3]

                        for ci, label, color in cat_specs:
                            df_c = df_map[df_map['_cat_action'] == ci]
                            # Legend label uses the INDEPENDENT count
                            # (e.g. Elevation includes buildings where WFP-B
                            # also works); the colored markers on the map
                            # still follow priority order, so each building
                            # appears in only one color.
                            legend_count = cat_legend_counts.get(ci, len(df_c))
                            if len(df_c) == 0:
                                # No markers in this priority bucket - still
                                # show a legend stub with the independent count.
                                fig_map.add_trace(go.Scattermapbox(
                                    lat=[None], lon=[None],
                                    mode='markers',
                                    marker=dict(size=8 * _point_scale, color=color, opacity=0.92),
                                    name=f"{label} ({legend_count})",
                                    showlegend=True, hoverinfo='skip',
                                ))
                                continue
                            _add_highlight_ring(fig_map, df_c, ring_size=13 * _point_scale)
                            fig_map.add_trace(go.Scattermapbox(
                                lat=df_c['latitude'], lon=df_c['longitude'],
                                mode='markers',
                                marker=dict(size=8 * _point_scale, color=color, opacity=0.92),
                                hovertemplate='%{customdata[0]}<extra></extra>',
                                customdata=list(df_c['hover_data']),
                                name=f"{label} ({legend_count})",
                            ))
                        _add_highlight_ring_legend(fig_map)

                # =====================================================
                # VIEW 3 - Damage Bins (5 categories with dynamic breaks)
                # Ported from generate_damage_animation_v3.m
                # =====================================================
                elif map_view == "Damage Bins":
                    # P90 is the upper-tail proxy here, matching
                    # compute_damage_bin_breaks() above and the
                    # Distributions tab's classifier.
                    col_nomit = 'No mitigation_P90'
                    if col_nomit not in df_map.columns:
                        st.warning(f"This view needs the '{col_nomit}' column.")
                    else:
                        dmg = df_map[col_nomit].fillna(0).values
                        thr = ZERO_THRESH_DISPLAY

                        # Separate "no damage" (<= $1k) from damaged buildings.
                        no_damage_mask = dmg <= thr
                        df_no_dmg = df_map[no_damage_mask]
                        n_no_dmg  = len(df_no_dmg)

                        nonzero = dmg[~no_damage_mask]

                        # ---- Plot the green "No Damage" layer first, so the
                        # damaged-building markers draw on top of it. ----
                        if n_no_dmg > 0:
                            _add_highlight_ring(fig_map, df_no_dmg, ring_size=13 * _point_scale)
                            fig_map.add_trace(go.Scattermapbox(
                                lat=df_no_dmg['latitude'], lon=df_no_dmg['longitude'],
                                mode='markers',
                                marker=dict(size=8 * _point_scale, color='#22c55e', opacity=0.85),
                                hovertemplate='%{customdata[0]}<extra></extra>',
                                customdata=list(df_no_dmg['hover_data']),
                                name=f"No Damage ($0)  ({n_no_dmg})",
                            ))

                        if len(nonzero) == 0:
                            if n_no_dmg == 0:
                                st.info("No buildings match the current filters.")
                            bin_caption_extra = ""
                        else:
                            # ---- Stable bin breakpoints across all years ----
                            # Computed once from the pooled distribution of
                            # nonzero No-Mit P90 damages over every year for
                            # the selected SLR scenario, so the same building
                            # gets the same bin color regardless of which
                            # year is active. Bins recompute when the user
                            # switches SLR scenario (because the underlying
                            # damage distribution shifts).
                            breaks = compute_damage_bin_breaks(df_buildings, scenario)
                            if not breaks:
                                # Degenerate case (no nonzero damages anywhere
                                # in this SLR scenario) - fall back to the
                                # current year's data
                                raw_breaks = np.quantile(nonzero, [0.20, 0.40, 0.60, 0.80])
                                breaks = sorted({nice_round_up(v) for v in raw_breaks if v > 0})[:4]
                            # bin edges for damaged buildings: thr, b1, …, bk, +inf
                            # where k = len(breaks) ∈ [1..4], giving 2..5 bins.
                            # The bin count is data-driven: locations with
                            # tightly-clustered damages collapse adjacent
                            # quantile breaks during nice-number rounding, and
                            # we now ACCEPT the lower bin count rather than
                            # extrapolating breaks beyond the actual data
                            # range (which used to push typical buildings
                            # into a misleadingly-red bin at low-damage
                            # locations like Pamunkey).
                            edges = [float(thr)] + breaks + [np.inf]
                            n_bins = len(edges) - 1

                            # MATLAB workshop palette (5 bins for damaged buildings)
                            bin_palette = [
                                '#f0d200',   # yellow
                                '#d25f00',   # dark orange
                                '#dc2626',   # red
                                '#8b0000',   # dark red
                                '#6a1b9a',   # purple
                            ]
                            bin_colors = bin_palette[:n_bins]

                            def _bin_label(lo, hi):
                                if hi == np.inf:
                                    return f"> {fmt_money_short(lo)}"
                                # Lower-edge label: "≤ hi" for the first bin (lo is $1k threshold)
                                if lo == float(thr):
                                    return f"≤ {fmt_money_short(hi)}"
                                return f"{fmt_money_short(lo)} – {fmt_money_short(hi)}"

                            bin_labels = [_bin_label(edges[i], edges[i+1]) for i in range(n_bins)]

                            # Classify damaged buildings into the 5 bins. np.digitize
                            # returns 0..n-1 where bin 0 = (thr, b1], bin n-1 = (b4, inf).
                            damaged_mask = ~no_damage_mask
                            df_damaged   = df_map[damaged_mask].copy()
                            dmg_damaged  = dmg[damaged_mask]
                            bin_idx = np.digitize(dmg_damaged, edges[1:-1], right=False)
                            df_damaged['_bin_idx'] = bin_idx

                            for ci in range(n_bins):
                                df_c = df_damaged[df_damaged['_bin_idx'] == ci]
                                if len(df_c) == 0:
                                    continue
                                count = len(df_c)
                                _add_highlight_ring(fig_map, df_c, ring_size=13 * _point_scale)
                                fig_map.add_trace(go.Scattermapbox(
                                    lat=df_c['latitude'], lon=df_c['longitude'],
                                    mode='markers',
                                    marker=dict(size=8 * _point_scale, color=bin_colors[ci], opacity=0.92),
                                    hovertemplate='%{customdata[0]}<extra></extra>',
                                    customdata=list(df_c['hover_data']),
                                    name=f"{bin_labels[ci]} ({count})",
                                ))

                            bin_caption_extra = (
                                "Damaged buildings are binned at the 20/40/60/80th percentiles "
                                "of pooled nonzero damages **across all years for the selected SLR scenario**, "
                                "snapped to nice rounded values "
                                f"({', '.join(fmt_money_short(b) for b in breaks)}). "
                                "Bins stay constant when you change the year (so the same building "
                                "keeps the same color across 2040 / 2055 / 2060 / 2100), but recompute when "
                                "you switch SLR scenario. Buildings with no damage (≤ $1k) are shown in green."
                            )

                        _add_highlight_ring_legend(fig_map)

                # =====================================================
                # VIEW 4 - Flood Occurrences (MC exceedance of FFE)
                # For each building, count per MC realization how many
                # years (2025 → horizon) the annual-maximum water level
                # exceeds its first-floor elevation, then color by a
                # chosen percentile (P10 / median / P90) of that
                # 1,000-wide occurrence-count distribution.
                # =====================================================
                elif map_view == "Flood Occurrences":
                    _wl_map = loc_entry.get('water_levels', {}) if loc_entry else {}
                    _mc_df = _wl_map.get(f'{scenario}_mc')
                    _attrs_fo = loc_entry.get('bldg_attrs') if loc_entry else None
                    if _mc_df is None or 'Year' not in getattr(_mc_df, 'columns', []):
                        _scn_lbl = ('High-End SLR (P90)' if scenario == '90th-percentile'
                                    else 'Median SLR (P50)')
                        st.warning(
                            f"Monte-Carlo water levels for the {_scn_lbl} scenario aren't "
                            f"available for {location_name}, so the flood-occurrences map "
                            f"can't be built. Switch the SLR scenario (above the map) to one "
                            f"that has an MC ensemble for this location."
                        )
                    elif _attrs_fo is None or 'FFE_ft' not in _attrs_fo.columns:
                        st.warning(
                            "First-floor elevations (FFE) aren't available for this "
                            "location, so flood occurrences can't be evaluated."
                        )
                    else:
                        # FFE per building computed over the FULL location
                        # inventory (not the occupancy/DFE-filtered subset),
                        # so the cached result is keyed correctly by
                        # (location, scenario, horizon) alone.
                        _ffe_by_id = {
                            int(i): (float(f) if pd.notna(f) else None)
                            for i, f in zip(_attrs_fo['id'], _attrs_fo['FFE_ft'])
                        }
                        _occ = compute_flood_occurrences(
                            _mc_df, _ffe_by_id,
                            (location_name, scenario, int(target_year)),
                        )
                        if _occ is None or _occ.empty:
                            st.info("No buildings with a first-floor elevation to evaluate.")
                        else:
                            n_years_win = int(_occ['n_years'].iloc[0])
                            occ_lut = _occ.set_index('id')

                            # Attach occurrence stats to the shown buildings.
                            dM = df_map.copy()
                            for _c in ('occ_mean', 'occ_P10', 'occ_P50', 'occ_P90'):
                                dM[_c] = dM['id'].map(occ_lut[_c])
                            dM['occ_show'] = dM['id'].map(occ_lut[flood_pct_key])
                            if flood_pct_key == 'occ_mean':
                                # The mean across realizations is fractional;
                                # round to the nearest whole year (round half
                                # up) so the bins/labels stay whole-year
                                # integers like the percentiles do. A mean that
                                # rounds to 0 (e.g. 0.3) reads as "never floods".
                                dM['occ_show'] = np.floor(dM['occ_show'] + 0.5)
                            # Buildings without an FFE drop out (NaN) rather
                            # than being miscolored.
                            dM = dM[dM['occ_show'].notna()].copy()

                            _pct_word = {'occ_mean': 'mean, rounded',
                                         'occ_P10': '10th-pct',
                                         'occ_P50': 'median',
                                         'occ_P90': '90th-pct'}[flood_pct_key]

                            # Flood-specific hover (building id rides along in
                            # customdata[1] so map clicks still resolve).
                            _hover = []
                            for _r in dM.itertuples(index=False):
                                _addr = getattr(_r, 'address', None)
                                if isinstance(_addr, str) and _addr.strip():
                                    _h = (f"<b>{_addr}</b> "
                                          f"<span style='color:#94a3b8'>#{int(_r.id)}</span><br>")
                                else:
                                    _h = f"<b>Building #{int(_r.id)}</b><br>"
                                _ffe_v = getattr(_r, 'FFE_ft', None)
                                if pd.notna(_ffe_v):
                                    _h += f"FFE {float(_ffe_v):.2f} ft NAVD88<br>"
                                _h += (f"Floods <b>{_r.occ_show:.0f}</b> of {n_years_win} times "
                                       f"by {int(target_year)} ({_pct_word} MC)<br>")
                                _h += (f"<span style='color:#64748b'>MC spread - "
                                       f"mean {_r.occ_mean:.1f} · "
                                       f"P10 {_r.occ_P10:.0f} · P50 {_r.occ_P50:.0f} · "
                                       f"P90 {_r.occ_P90:.0f} times</span>")
                                _hover.append(_h)
                            dM['_fhd'] = [[t, int(i)] for t, i in zip(_hover, dM['id'])]

                            _never = dM[dM['occ_show'] <= 0]
                            _flood = dM[dM['occ_show'] > 0].copy()

                            # Green "never floods" layer first (drawn under).
                            if len(_never) > 0:
                                _add_highlight_ring(fig_map, _never, ring_size=13 * _point_scale)
                                fig_map.add_trace(go.Scattermapbox(
                                    lat=_never['latitude'], lon=_never['longitude'],
                                    mode='markers',
                                    marker=dict(size=8 * _point_scale, color='#22c55e', opacity=0.85),
                                    hovertemplate='%{customdata[0]}<extra></extra>',
                                    customdata=list(_never['_fhd']),
                                    name=f"Never floods (0 times)  ({len(_never)})",
                                ))

                            if len(_flood) == 0:
                                flood_occ_note = (
                                    f"No building's first floor floods in any year through "
                                    f"{int(target_year)} at the {_pct_word} MC level under this "
                                    f"SLR scenario."
                                )
                            else:
                                # Horizon-robust bins: split the 1..n_years
                                # window at 10/25/50/75 % of years, expressed
                                # back in absolute year counts. Bins recompute
                                # with the horizon (window length) but read the
                                # same way at every horizon.
                                fracs = [0.10, 0.25, 0.50, 0.75]
                                cuts = sorted({max(1, int(math.ceil(n_years_win * fr)))
                                               for fr in fracs})
                                edges = sorted(set([0] + cuts + [n_years_win + 1]))
                                n_bins = len(edges) - 1
                                # Sequential severity ramp: warm yellow (rare)
                                # → deep red (floods almost every year). Warm
                                # hues keep adjacent bins distinguishable
                                # (an all-blue ramp blurs together) while the
                                # green "never floods" markers stay clearly
                                # separate from the flooded buildings.
                                flood_palette = ['#fed976', '#feb24c', '#fd8d3c',
                                                 '#f03b20', '#bd0026']
                                if n_bins <= len(flood_palette):
                                    bin_colors = flood_palette[:n_bins]
                                else:
                                    bin_colors = (flood_palette +
                                                  [flood_palette[-1]] * (n_bins - len(flood_palette)))

                                def _occ_bin_label(ci):
                                    # bin ci covers counts in [a, b]; bin 0's
                                    # zero is already split out as "never".
                                    a = max(edges[ci], 1) if ci == 0 else edges[ci]
                                    b = edges[ci + 1] - 1
                                    if b >= n_years_win:
                                        b = n_years_win
                                    if a >= b:
                                        return f"{a} time" if a == 1 else f"{a} times"
                                    return f"{a}\u2013{b} times"

                                vals = _flood['occ_show'].to_numpy()
                                _bidx = np.digitize(vals, edges[1:-1], right=False)
                                _flood['_bin'] = _bidx
                                for ci in range(n_bins):
                                    dfc = _flood[_flood['_bin'] == ci]
                                    if len(dfc) == 0:
                                        continue
                                    _add_highlight_ring(fig_map, dfc, ring_size=13 * _point_scale)
                                    fig_map.add_trace(go.Scattermapbox(
                                        lat=dfc['latitude'], lon=dfc['longitude'],
                                        mode='markers',
                                        marker=dict(size=8 * _point_scale,
                                                    color=bin_colors[ci], opacity=0.92),
                                        hovertemplate='%{customdata[0]}<extra></extra>',
                                        customdata=list(dfc['_fhd']),
                                        name=f"{_occ_bin_label(ci)}  ({len(dfc)})",
                                    ))
                                flood_occ_note = (
                                    f"Each building is colored by the **{_pct_word}** number of times "
                                    f"its first floor floods between 2025 and {int(target_year)} "
                                    f"(a {n_years_win}-year window), taken across the 1,000 MC "
                                    f"water-level realizations. \u201cFlooded\u201d means the simulated "
                                    f"annual-maximum water level exceeds the building's first-floor "
                                    f"elevation. Bins split the window at 10 / 25 / 50 / 75 % of its length; "
                                    f"buildings that never flood under the selected statistic are green."
                                )

                            _add_highlight_ring_legend(fig_map)
                            _flood_occ_df = dM   # for the metrics/caption below

                # =====================================================
                # VIEW 5 / 6 - Flood map and Road map: bathtub flooding
                # on USGS 3DEP terrain for a present-day flood level plus
                # the modeled sea-level rise (or an override).
                # =====================================================
                elif map_view in ("Flood Map", "Road Map") and flood_level_key is not None:
                    _wl_base = float(_fl_values[flood_level_key])
                    _slr_used = float(_slr_override) if _slr_override and _slr_override > 0 \
                        else float(TV.slr_ft(loc_entry, scenario, target_year))
                    _wl_now = _wl_base + _slr_used
                    _lvl_label = next(lbl for k, lbl, _h in TV.LEVELS if k == flood_level_key)
                    try:
                        _bbox_r, _site_c = TV.roi(loc_entry)
                        with st.spinner("Reading terrain (USGS 3DEP) and flooding it..."):
                            _depth_layer, _flooded_km2, _dem_ext = TV.depth_layer(
                                _bbox_r, _res_m, round(_wl_now, 2),
                                palette="flood" if map_view == "Flood Map" else "blue")
                        _extra_layers.append(_depth_layer)
                    except Exception as _e:
                        _depth_layer = None
                        st.error("Could not load terrain for this area from USGS 3DEP (the app needs internet "
                                 f"access at run time). Details: {_e}")
                    _slr_txt = (f"your override of {_slr_used:.2f} ft" if (_slr_override and _slr_override > 0)
                                else f"{_slr_used:.2f} ft of modeled sea-level rise by {int(target_year)} "
                                     f"({'median' if scenario == '50th-percentile' else 'high'} scenario)")
                    if _depth_layer is not None:
                        TV.add_depth_legend(fig_map, palette="flood" if map_view == "Flood Map" else "blue")
                        if map_view == "Flood Map":
                            # Same look as the original flood maps: the depth
                            # ramp over the basemap, no building markers.
                            terrain_note = (
                                f"**{_lvl_label} flood** at **{_wl_now:.2f} ft NAVD88** = present-day level "
                                f"{_wl_base:.2f} ft + {_slr_txt}. Flooded land about **{_flooded_km2:.2f} km²**. "
                                f"Bathtub model on USGS 3DEP terrain (~{_res_m:.0f} m grid), no hydraulic "
                                f"connectivity; open water (below 0 ft NAVD88) hidden."
                            )
                        else:
                            try:
                                with st.spinner("Classifying roads..."):
                                    _segs, _rcnt, _n_roads, _entry_note = TV.classified_roads(
                                        selected_location, _bbox_r, _res_m, round(_wl_now, 2), bool(_road_access),
                                        bool(_road_entry), (_road_entry_xy or "").strip(), loc_entry)
                            except Exception as _e:
                                _segs = None
                                st.error("Could not load the OpenStreetMap roads for this area (the public Overpass "
                                         f"servers may be busy; try again in a moment). Details: {_e}")
                            if _segs is not None:
                                TV.add_context_buildings(fig_map, df_map, _point_scale)
                                TV.add_road_traces(fig_map, _segs, _rcnt, bool(_road_access))
                                _acc_txt = (f", **{_rcnt.get('pct_inacc', 0):.0f}%** dry but cut off"
                                            if _road_access else "")
                                terrain_note = (
                                    f"**{_lvl_label} flood** at **{_wl_now:.2f} ft NAVD88** = present-day level "
                                    f"{_wl_base:.2f} ft + {_slr_txt}. Of {_n_roads:,} OpenStreetMap road ways: "
                                    f"**{_rcnt.get('pct_flood', 0):.0f}%** of road length flooded{_acc_txt}, "
                                    f"**{_rcnt.get('pct_dry', 0):.0f}%** dry and reachable. Road elevations and flood "
                                    f"depth from USGS 3DEP (~{_res_m:.0f} m grid)."
                                    + (f" {_entry_note}" if _entry_note else "")
                                )

                # ---- Search highlight (auto-expires) ----
                # If the user just searched for a Building ID and the
                # highlight is still fresh, drop a magenta ring on top
                # of all other traces. Two layered markers give a clear
                # visual flag: an outer translucent halo + an inner
                # solid ring. Both vanish on the next rerun once the
                # TTL has elapsed.
                #
                # Streamlit doesn't rerun on a wall-clock timer, so we
                # schedule a one-shot rerun right after the TTL using
                # st_autorefresh - without it, the ring would persist
                # on screen until the user moved the mouse / resized
                # the page, which defeats the "clean screenshot"
                # goal.
                _hl_id    = st.session_state.get('map_highlight_id')
                _hl_until = float(st.session_state.get('map_highlight_until', 0.0))
                _hl_remaining = _hl_until - _time_mod.time()
                if _hl_id is not None and _hl_remaining > 0:
                    # Look up the building's coordinates in the same
                    # df_map the rest of the figure was built from, so
                    # the ring lands on the same dot the user sees.
                    _hl_row = df_map[df_map['id'] == _hl_id]
                    if not _hl_row.empty:
                        _hl_lat = float(_hl_row['latitude'].iloc[0])
                        _hl_lon = float(_hl_row['longitude'].iloc[0])
                        _addr = _hl_row.get('address', pd.Series([None])).iloc[0]
                        _hl_label = (
                            f"Building #{_hl_id}"
                            + (f" - {_addr}" if pd.notna(_addr) and str(_addr).strip() else "")
                        )
                        # Outer halo - large translucent magenta circle
                        fig_map.add_trace(go.Scattermapbox(
                            lat=[_hl_lat], lon=[_hl_lon],
                            mode='markers',
                            marker=dict(size=44 * _point_scale, color='rgba(217, 70, 239, 0.32)'),
                            hoverinfo='skip',
                            showlegend=False,
                            name='_search_halo',
                        ))
                        # Inner solid ring
                        fig_map.add_trace(go.Scattermapbox(
                            lat=[_hl_lat], lon=[_hl_lon],
                            mode='markers',
                            marker=dict(size=22 * _point_scale, color='rgba(217, 70, 239, 0.95)'),
                            hovertemplate=f'<b>{_hl_label}</b><extra></extra>',
                            showlegend=True,
                            name=f'🔍 {_hl_label}',
                        ))
                        # Schedule a single rerun ~200ms after the TTL
                        # so the ring auto-disappears without manual
                        # interaction. Falls back gracefully if the
                        # st_autorefresh package isn't installed -
                        # the ring still vanishes on the next user
                        # interaction (pan, zoom, click, etc.).
                        try:
                            from streamlit_autorefresh import st_autorefresh
                            st_autorefresh(
                                interval=int(_hl_remaining * 1000) + 200,
                                limit=1,
                                key=f"map_hl_expire_{_hl_id}_{int(_hl_until)}",
                            )
                        except ImportError:
                            # Optional dependency - silently skip the
                            # auto-clear. The user can hit "✖ Clear"
                            # or trigger any UI event to clear it.
                            pass

                # ---- Common layout ----
                # Resolve the user's basemap choice (Streets / Aerial) into
                # the (style, layers) pair plotly needs. Aerial is a
                # white-bg style with an ESRI raster tile layer drawn
                # under the data traces; Streets is plain OSM.
                _bm_style, _bm_layers = _basemap_config(basemap_choice)
                fig_map.update_layout(
                    mapbox=dict(
                        style=_bm_style,
                        layers=list(_bm_layers) + list(_extra_layers),
                        center=dict(lat=center_lat, lon=center_lon),
                        zoom=_default_map_zoom
                    ),
                    margin={"r":0,"t":0,"l":0,"b":0},
                    height=600,
                    hoverlabel=dict(bgcolor="white", font_size=12, font_family="Arial"),
                    showlegend=True,
                    legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01,
                                bgcolor="rgba(255,255,255,0.85)",
                                bordercolor="rgba(0,0,0,0.15)", borderwidth=1),
                )

                # Click capture: tag the chart with a stable key + on_select="rerun"
                # so clicks bubble back to Python. selection_mode="points" limits
                # the interaction to simple clicks (no lasso/box select), which also
                # prevents Plotly from dimming unselected markers.
                # The clicked building's id rides along in customdata[1] (set when
                # df_map['hover_data'] was built) - we extract it below.
                map_event = st.plotly_chart(
                    fig_map,
                    use_container_width=True,
                    key="bldg_map",
                    on_select="rerun",
                    selection_mode="points",
                )

                # --- Extract clicked building id (defensive against several
                #     return-value shapes Streamlit can emit). If the event
                #     comes back with an empty points array, treat that as a
                #     deselection (user clicked an empty area of the map).
                clicked_id = None
                map_selection_seen = False   # True iff event payload reached us
                pts_nonempty = False
                try:
                    sel = None
                    if map_event is not None:
                        sel = getattr(map_event, 'selection', None) or \
                              (map_event.get('selection') if isinstance(map_event, dict) else None)
                    pts = None
                    if sel is not None:
                        pts = getattr(sel, 'points', None)
                        if pts is None and isinstance(sel, dict):
                            pts = sel.get('points')
                        # `pts` may be None (no event), [] (empty click), or non-empty.
                        map_selection_seen = pts is not None
                        pts_nonempty = bool(pts)
                    if pts_nonempty:
                        p0 = pts[0]
                        cd = p0.get('customdata') if isinstance(p0, dict) else getattr(p0, 'customdata', None)
                        # cd may be [text, id]  OR  [[text, id]] (nested) - unwrap as needed
                        if isinstance(cd, (list, tuple)):
                            if len(cd) >= 2 and not isinstance(cd[1], (list, tuple)):
                                clicked_id = int(cd[1])
                            elif len(cd) >= 1 and isinstance(cd[0], (list, tuple)) and len(cd[0]) >= 2:
                                clicked_id = int(cd[0][1])
                except (TypeError, ValueError, AttributeError, IndexError):
                    clicked_id = None

                if clicked_id is not None:
                    st.session_state['selected_building_id'] = clicked_id
                elif map_selection_seen and not pts_nonempty:
                    # User clicked an empty area of the map - clear the
                    # selection everywhere (map caption, Building Details tab).
                    # NOTE: Plotly on Scattermapbox doesn't always emit an
                    # event for empty-area clicks, so this path is a
                    # best-effort fallback. The Clear button below is the
                    # reliable way to deselect.
                    st.session_state.pop('selected_building_id', None)

                if 'selected_building_id' in st.session_state:
                    col_sel, col_btn = st.columns([4, 1])
                    with col_sel:
                        st.caption(
                            f"📍 **Selected building:** #{st.session_state['selected_building_id']} "
                            "- open the **Details** tab to see its full profile."
                        )
                    with col_btn:
                        if st.button("✖ Clear selection", key="clear_map_selection",
                                     use_container_width=True):
                            st.session_state.pop('selected_building_id', None)
                            # Also clear the widget-state key used by the
                            # Building Details selectbox so its value
                            # resets on the next render.
                            st.session_state.pop('bldg_details_selectbox', None)
                            st.session_state.pop('bldg_search_input', None)
                            st.rerun()

                # ---- View-specific caption ----
                if map_view == "Damage Heatmap":
                    cap_note = ""
                    if cap_is_clipped:
                        cap_note = (
                            f" The color scale is capped at the 90th percentile of nonzero "
                            f"damages (about {fmt_money_short(color_cap)}) so the gradient is "
                            f"readable across the bulk of buildings. Buildings above the cap "
                            f"(max in this view: {fmt_money_short(max_damage_raw)}) still render "
                            f"at the top red and show their true damage in the hover tooltip."
                        )
                    st.caption(
                        "Each at-risk building is colored by its No-Mitigation P50 cumulative "
                        "damage." + cap_note
                    )
                elif map_view == "Adaptation Effectiveness":
                    _cut_txt = cutoff_label.replace("$", "\\$")   # escape $ so markdown does not start math mode
                    if _cutoff_is_cut:
                        _rule_txt = (f"removes at least 90% of its {_ae_stat_word} cumulative damage** through the "
                                     f"selected year ({_ae_stat_word} after the measure at most 10% of the "
                                     f"{_ae_stat_word} with no action)")
                        _green_txt = f"{_ae_stat_word} is \\$0 with no action"
                        _red_txt = "even elevation removes less than 90%"
                    else:
                        _rule_txt = (f"brings its {_ae_stat_word} cumulative damage to or below {_cut_txt}** "
                                     f"through the selected year")
                        _green_txt = f"{_ae_stat_word} at or below {_cut_txt} with no action"
                        _red_txt = f"even elevation leaves more than {_cut_txt}"
                    _stat_note = (" P90 is the damage exceeded in only 10% of the 1,000 simulated futures; a bad "
                                  "outcome, not the typical one." if _ae_stat == "P90" else
                                  " The median is the damage exceeded in half of the 1,000 simulated futures; "
                                  "the hover also shows the P90 for the worse futures.")
                    if mobile_raise_only:
                        st.caption(
                            "🏠 **Mobile-homes-dominated area:** only **raising (elevating) homes** is considered. "
                            f"Raising a home counts as effective when it **{_rule_txt}. The hover shows each "
                            f"{_ae_stat_word}; \u2713 marks a measure that meets the rule. **No Damage** "
                            f"({_green_txt}) \u2192 **Elevation** \u2192 **Residual Damage** (Under-DFE homes below "
                            f"the elevation cutoff where {_red_txt}; the conversation moves to buyout, relocation, "
                            "or community-scale measures). Above-DFE buildings are not plotted." + _stat_note
                            + ae_gap_note
                        )
                    else:
                        st.caption(
                            f"Each building is colored by the **cheapest measure that {_rule_txt}. The hover shows "
                            f"each measure's {_ae_stat_word}; \u2713 marks a measure that meets the rule. Priority "
                            f"order: **No Damage** ({_green_txt}) \u2192 **Raise Utilities** \u2192 **WFP Basement** "
                            f"\u2192 **Elevation** \u2192 **Residual Damage** (Under-DFE, below the elevation cutoff, "
                            f"and {_red_txt}). Change the statistic and cutoff under Advanced options." + _stat_note
                            + ae_gap_note
                        )
                elif map_view == "Damage Bins":
                    st.caption(
                        "Each building is colored by its No-Mitigation P90 cumulative damage. " +
                        bin_caption_extra +
                        " Non-residential buildings are marked with a black ring."
                    )
                elif map_view == "Flood Occurrences":
                    if flood_occ_note:
                        st.caption(
                            flood_occ_note +
                            " Non-residential buildings are marked with a black ring. "
                            "Switch the SLR scenario or horizon (year) above to update, and use "
                            "the percentile selector to read the low / median / high MC outcome."
                        )
                elif map_view in ("Flood Map", "Road Map"):
                    if terrain_note:
                        st.caption(terrain_note + " Change the flood level above the map, and the present-day "
                                   "levels, terrain detail and sea-level rise under Advanced options.")

                col1, col2 = st.columns(2)

                with col1:
                    if map_view == "Flood Occurrences" and _flood_occ_df is not None:
                        st.metric("Buildings Evaluated", f"{len(_flood_occ_df):,}")
                    elif map_view == "Flood Map" and terrain_note:
                        st.metric("Flooded land (km²)", f"{_flooded_km2:.2f}")
                    else:
                        st.metric("Buildings Shown", f"{len(df_map):,}")
                with col2:
                    if map_view in ("Flood Map", "Road Map") and terrain_note:
                        st.metric("Water level mapped (ft NAVD88)", f"{_wl_now:.2f}")
                    elif map_view == "Flood Occurrences" and _flood_occ_df is not None and len(_flood_occ_df):
                        _nflood = int((_flood_occ_df['occ_show'] > 0).sum())
                        _pctw = {'occ_mean': 'mean', 'occ_P10': 'P10',
                                 'occ_P50': 'median', 'occ_P90': 'P90'}[flood_pct_key]
                        st.metric(
                            f"Buildings flooding \u22651 time by {int(target_year)} ({_pctw} MC)",
                            f"{_nflood:,}",
                        )
                    else:
                        total_baseline = df_map[baseline_col].sum() if baseline_col else 0
                        st.metric("Total No Mitigation Cumulative Damage", format_currency(total_baseline))

                # ========================================================
                # HIGH-RESOLUTION MAP EXPORT
                # ========================================================
                # Generates a publication-quality PNG/PDF/SVG of the
                # current map view: title, scenario subtitle, scale bar
                # (geographically correct at the chosen zoom), north
                # arrow, and credits footer with data sources.
                # Output is sized for ~300 DPI print at the chosen
                # paper aspect (8×6 in by default).
                with st.expander("🖨️ Export Map (High-Resolution, Publication-Ready)", expanded=False):
                    st.markdown(
                        "Generate a high-resolution image of the current map view, with a "
                        "title, scale bar, north arrow, and full credits footer (data sources, "
                        "basemap attribution, and date). Formats below are sized for a journal "
                        "figure at ~300 DPI print quality."
                    )
                    exp_col1, exp_col2, exp_col3 = st.columns([1.1, 1.1, 1])
                    with exp_col1:
                        export_format = st.selectbox(
                            "Format",
                            options=["PNG (raster)", "PDF (vector)", "SVG (vector)"],
                            index=0,
                            key="map_export_format",
                            help="PNG: best for slides and web. PDF/SVG: best for "
                                 "journal submission since they remain crisp at any zoom."
                        )
                    with exp_col2:
                        export_size = st.selectbox(
                            "Size",
                            options=[
                                "Single column (1600×1200)",
                                "1.5 column (2000×1500)",
                                "Double column (2400×1800)",
                                "Full page (3200×2400)",
                            ],
                            index=2,
                            key="map_export_size",
                            help="Approximate journal figure widths. Larger = sharper "
                                 "print at the cost of file size."
                        )
                    with exp_col3:
                        export_scale = st.selectbox(
                            "DPI",
                            options=[1, 2, 3, 4],
                            index=1,
                            key="map_export_scale",
                            format_func=lambda x: f"{x}× (~{x*150} DPI)",
                            help="Multiplier on rendered pixel density. 2× ≈ 300 DPI; "
                                 "3× ≈ 450 DPI. SVG/PDF ignore this (always vector)."
                        )
                    # Second row: basemap choice. We deliberately omit
                    # "open-street-map" here because OSM's tile servers
                    # block headless render requests (kaleido's bundled
                    # browser doesn't send the Referer header OSM's tile
                    # usage policy requires), so an OSM export consistently
                    # comes back peppered with "Access blocked" tiles.
                    # The live map can still use OSM - your real browser
                    # supplies the Referer there. For exports use Carto
                    # (which has no such restriction) or the white-bg
                    # option.
                    export_basemap = st.selectbox(
                        "Basemap (printed export only)",
                        options=[
                            "carto-positron",
                            "carto-darkmatter",
                            "Aerial",
                            "white-bg (no basemap)",
                        ],
                        index=0,   # carto-positron is the cleanest print default
                        key="map_export_basemap",
                        help="Tile-based basemaps need internet access at export time; "
                             "if a tile server refuses the request the export will "
                             "auto-fall-back to a white background. `carto-positron` "
                             "(light, neutral) and `carto-darkmatter` (dark) are the "
                             "preferred print styles. `Aerial` uses ESRI World "
                             "Imagery satellite tiles - great for showing physical "
                             "context (shorelines, vegetation, building footprints) "
                             "but heavier and lower-contrast in print. Pick "
                             "`white-bg` for a guaranteed-working export with no "
                             "roads/labels. OpenStreetMap is intentionally excluded "
                             "- its tile servers block headless render requests, so "
                             "OSM exports come back covered in 'Access blocked' tiles."
                    )

                    # Third row: framing override.
                    # Default behavior is auto-fit-to-data; "Custom" lets
                    # the user dial in center/zoom to match their live
                    # map view (Streamlit doesn't surface Plotly pan/zoom
                    # state back to Python, so a manual override is the
                    # most reliable WYSIWYG path).
                    st.markdown("**Map framing**")
                    # Bbox-fit defaults so the user can tweak from there
                    # rather than starting from scratch.
                    _df_lat = df_map['latitude'].dropna()
                    _df_lon = df_map['longitude'].dropna()
                    _bbox_lat = float(_df_lat.mean()) if len(_df_lat) else 40.86
                    _bbox_lon = float(_df_lon.mean()) if len(_df_lon) else -72.49
                    frame_mode = st.radio(
                        "Frame",
                        options=["Auto-fit data (default)", "Custom center & zoom"],
                        index=0, horizontal=True,
                        key="map_export_frame_mode",
                        help="Auto-fit centers on the data bbox and chooses a zoom "
                             "that shows everything. Custom lets you set the center "
                             "and zoom - useful when you've panned/zoomed in the live "
                             "map and want the export to match. (Streamlit doesn't "
                             "expose the live map's current pan/zoom back to Python, "
                             "so this is the most reliable way to get WYSIWYG.)"
                    )
                    custom_center_lat = None
                    custom_center_lon = None
                    custom_zoom = None
                    if frame_mode == "Custom center & zoom":
                        fc1, fc2, fc3 = st.columns(3)
                        with fc1:
                            custom_center_lat = st.number_input(
                                "Center latitude (°N)",
                                value=_bbox_lat, format="%.4f", step=0.001,
                                key="map_export_center_lat",
                            )
                        with fc2:
                            custom_center_lon = st.number_input(
                                "Center longitude (°E)",
                                value=_bbox_lon, format="%.4f", step=0.001,
                                key="map_export_center_lon",
                            )
                        with fc3:
                            custom_zoom = st.number_input(
                                "Zoom level",
                                min_value=1.0, max_value=20.0,
                                value=13.0, step=0.5,
                                key="map_export_zoom",
                                help="Web Mercator zoom: 11≈town, 13≈neighborhood, "
                                     "15≈street level. Increase to zoom in.",
                            )
                        st.caption(
                            "💡 *To match your current map view: hover the live map "
                            "to read coordinates near the center, copy them here, and "
                            "pick a zoom that frames the same area. Generate, inspect, "
                            "adjust if needed.*"
                        )

                    # Parse selections
                    _fmt_map = {"PNG (raster)": "png", "PDF (vector)": "pdf", "SVG (vector)": "svg"}
                    _size_map = {
                        "Single column (1600×1200)":   (1600, 1200),
                        "1.5 column (2000×1500)":      (2000, 1500),
                        "Double column (2400×1800)":   (2400, 1800),
                        "Full page (3200×2400)":       (3200, 2400),
                    }
                    fmt = _fmt_map[export_format]
                    ex_w, ex_h = _size_map[export_size]
                    # Strip the trailing description for white-bg
                    export_basemap_value = export_basemap.split(' ')[0]

                    if st.button("📤 Generate Export", key="generate_map_export",
                                 type="primary", use_container_width=False):
                        with st.spinner("Rendering high-resolution map…"):
                            _scenario_label = ('Median SLR (P50)' if scenario == '50th-percentile'
                                               else 'High-End SLR (P90)' if scenario == '90th-percentile'
                                               else scenario)
                            pub_fig = build_publication_map_figure(
                                fig_map,
                                location=location_name,
                                occupancy=occupancy_label,
                                target_year=int(target_year),
                                scenario_label=_scenario_label,
                                map_view=map_view,
                                df_map=df_map,
                                extra_layers=list(_extra_layers),
                                width_px=ex_w, height_px=ex_h,
                                mapbox_style=export_basemap_value,
                                center_lat_override=custom_center_lat,
                                center_lon_override=custom_center_lon,
                                zoom_override=custom_zoom,
                            )
                            img_bytes, mime, ext, info = export_map_image(
                                pub_fig, fmt=fmt, scale=int(export_scale),
                            )
                        if img_bytes is None:
                            # Hard failure (kaleido missing, or fallback also failed)
                            st.error(f"❌ Export failed: {info}")
                            st.info(
                                "If you're running this app yourself, install the export "
                                "engine with `pip install -U kaleido` and run "
                                "`plotly_get_chrome` once to set up the headless browser."
                            )
                        else:
                            # Filename: location_year_scenario_view.ext (sanitized)
                            _safe = lambda s: ''.join(
                                c if c.isalnum() or c in '-_' else '_' for c in str(s)
                            )
                            _scn_short = ('P50' if scenario == '50th-percentile'
                                          else 'P90' if scenario == '90th-percentile'
                                          else _safe(scenario))
                            _occ_short = (_safe(occupancy_label).replace('Buildings','').strip('_')
                                          or 'All')
                            fname = (f"ADAPT_{_safe(location_name)}_{int(target_year)}_"
                                     f"{_scn_short}_{_safe(map_view)}_{_occ_short}.{ext}")
                            st.success(
                                f"✅ Map ready - {ex_w}×{ex_h} px"
                                f"{' (×' + str(int(export_scale)) + ')' if fmt == 'png' else ''}, "
                                f"{len(img_bytes)/1024:.0f} KB"
                            )
                            if info == "basemap_fallback":
                                st.warning(
                                    f"⚠ The chosen basemap (`{export_basemap_value}`) "
                                    "couldn't be rendered - its tile server refused the "
                                    "request from the headless browser. The export was "
                                    "generated on a clean white background instead. "
                                    "Try `carto-positron` or `white-bg` for a more "
                                    "reliable export, or supply a Mapbox API token if "
                                    "you need branded tiles."
                                )
                            st.download_button(
                                label=f"⬇ Download {fmt.upper()}",
                                data=img_bytes,
                                file_name=fname,
                                mime=mime,
                                key="download_map_export",
                                use_container_width=False,
                            )
                            st.caption(
                                "Includes: title, scenario subtitle, geographically-correct "
                                "scale bar, north arrow, and credits footer (data sources, "
                                "basemap attribution, generation date). Cite as: "
                                "*Generated with ADAPT, Center for Climate Systems Research, "
                                "The Climate School, Columbia University.*"
                            )

                st.subheader(f"🔴 Top 10 Highest Risk Buildings (No Mitigation)")

                display_cols = ['id']
                if 'occupancy_type' in df_map.columns:
                    display_cols.append('occupancy_type')
                if 'structure_value' in df_map.columns:
                    display_cols.append('structure_value')
                if 'DFE_Status' in df_map.columns:
                    display_cols.append('DFE_Status')
                display_cols.extend(action_cols_p50)

                if baseline_col:
                    top10 = df_map.nlargest(10, baseline_col)[display_cols].copy()
                else:
                    top10 = df_map.head(10)[display_cols].copy()

                if 'structure_value' in top10.columns:
                    top10['structure_value'] = top10['structure_value'].apply(format_currency)
                for col in action_cols_p50:
                    top10[col] = top10[col].apply(format_currency)

                rename_map = {col: col.replace('_P50', '') for col in action_cols_p50}
                rename_map['DFE_Status'] = 'DFE Status'
                top10 = top10.rename(columns=rename_map)

                st.dataframe(top10, use_container_width=True, hide_index=True)
    else:
        st.warning("No per-building data available for this location.")

    # --------------------------------------------------------------
    # Data Notes - provenance / coverage information for the bundle
    # --------------------------------------------------------------
    # Surfaced below the map so users have a one-click path to
    # check what's in the inventory, what got skipped, and which
    # hazard inputs the analysis used. The expander stays collapsed
    # by default to keep the main view clean.
    if loc_entry is not None:
        with st.expander("ℹ️ Data Notes - coverage, exclusions, and bundle metadata", expanded=False):
            meta = loc_entry.get('metadata') or {}
            bfe_ft_local = loc_entry.get('bfe_ft')

            # Coverage line
            bldg_attrs = loc_entry.get('bldg_attrs')
            n_total = int(len(bldg_attrs)) if bldg_attrs is not None else None
            skipped_df = loc_entry.get('skipped')
            n_skipped = len(skipped_df) if isinstance(skipped_df, pd.DataFrame) else 0

            cov_col1, cov_col2, cov_col3 = st.columns(3)
            with cov_col1:
                if n_total is not None:
                    st.markdown(f"**Buildings analyzed**  \n{n_total:,}")
            with cov_col2:
                if bfe_ft_local is not None:
                    st.markdown(f"**Base Flood Elevation (BFE)**  \n{bfe_ft_local:g} ft NAVD88")
                    st.caption(f"DFE = BFE + 2 = {bfe_ft_local + 2:g} ft NAVD88")
            with cov_col3:
                target_year_labels_local = loc_entry.get('target_year_labels') or {}
                if target_year_labels_local:
                    years_str = ", ".join(
                        f"{lab} ({y})" if lab != str(y) else str(y)
                        for y, lab in sorted(target_year_labels_local.items())
                    )
                    st.markdown(f"**Evaluation horizons**  \n{years_str}")

            # Skipped buildings table - only shown when there are any.
            if n_skipped > 0:
                st.markdown("---")
                st.markdown(f"**Excluded buildings ({n_skipped})**")
                st.caption(
                    "These buildings appear in the National Structure Inventory for "
                    "this location but were excluded from the damage analysis "
                    "(typically due to invalid or incomplete attributes)."
                )
                # Show the relevant columns; the CSV ships
                # BuildingID / NSI_row / OccupancyType / SOID / Reason
                show_cols = [c for c in ['BuildingID', 'OccupancyType', 'Reason']
                             if c in skipped_df.columns]
                st.dataframe(
                    skipped_df[show_cols] if show_cols else skipped_df,
                    use_container_width=True, hide_index=True,
                )

            # Bundle metadata - small, gray, for the technically curious.
            if meta:
                st.markdown("---")
                st.caption("**Bundle metadata**")
                # Format key fields nicely; dump the rest as a list.
                pretty_lines = []
                if 'LOCATION' in meta:
                    pretty_lines.append(f"Location: **{meta['LOCATION']}**")
                if 'n_res' in meta and 'n_nonres' in meta:
                    pretty_lines.append(
                        f"Residential: **{meta['n_res']}** · "
                        f"Non-Residential: **{meta['n_nonres']}**"
                    )
                actions_meta = meta.get('ACTION_NAMES')
                if isinstance(actions_meta, list):
                    pretty_lines.append("Adaptation actions: " + ", ".join(f"`{a}`" for a in actions_meta))
                pcts_meta = meta.get('PERCENTILES')
                if isinstance(pcts_meta, list):
                    pretty_lines.append(
                        f"Percentiles available: **{len(pcts_meta)}** "
                        f"({pcts_meta[0]}–{pcts_meta[-1]}, dense at tails plus quartiles)"
                    )
                # Water-level MC realizations
                wl = loc_entry.get('water_levels') or {}
                mc_keys = [k for k in wl.keys() if k.endswith('_mc')]
                if mc_keys:
                    # Use the first MC sheet to count realizations
                    first_mc = wl[mc_keys[0]]
                    n_mc = sum(1 for c in first_mc.columns if c.startswith('MC_'))
                    n_years = len(first_mc)
                    pretty_lines.append(
                        f"Water-level Monte Carlo: **{n_mc:,}** annual-max realizations × "
                        f"**{n_years}** years × {len(mc_keys)} SLR scenarios"
                    )
                if pretty_lines:
                    st.markdown("\n".join(f"- {ln}" for ln in pretty_lines))

