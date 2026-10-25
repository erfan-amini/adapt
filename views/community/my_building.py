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
from adapt_core.charts import (SCENARIO_SPECS, build_box_whisker_panel)
from adapt_core.helpers import (ADAPTATION_COST_ESTIMATES, ADAPTATION_COST_SOURCE, ADD_STORY_COST_TEXT, ADD_STORY_HEIGHT_FT, ADD_STORY_LABEL, _elev_eligible_flag, _flag_true, _is_one_story_bldg, format_currency, smart_money_ticks)


def render(ctx):
    df_buildings = ctx.df_buildings
    loc_entry = ctx.loc_entry
    location_name = ctx.location_name
    occupancy_label = ctx.occupancy_label
    scenario = ctx.scenario
    selected_location = ctx.selected_location
    target_year = ctx.target_year
    from adapt_core import ui as _ui
    from views.community import building_depth as _depth
    _ui.page_header(
        "My building",
        "Pick a home or a business and see its flood exposure, projected damage over time, and what each "
        "adaptation option would save. The <b>Flood depth tables</b> tab reports water depth at the building "
        "for flood levels you choose.",
        module_key="community", location=location_name,
    )
    ctx.render_inline_settings(["occ", "year", "scn"])
    _tab_dmg, _tab_depth = st.tabs(["🏠 Damage and adaptation options", "📏 Flood depth tables"])
    with _tab_depth:
        _depth.render_depth_tool(ctx)
    with _tab_dmg:
        _render_damage(ctx, df_buildings, loc_entry, location_name, occupancy_label, scenario,
                       selected_location, target_year)


def _render_damage(ctx, df_buildings, loc_entry, location_name, occupancy_label, scenario,
                   selected_location, target_year):
    if df_buildings is not None:
        st.subheader(f"Individual building analysis - {location_name} ({occupancy_label})")

        building_ids = df_buildings['id'].unique()
        sorted_ids = sorted(building_ids)

        # Cross-tab sync: if the user just clicked a building on the map, the
        # click-handler stored its id in session_state['selected_building_id'].
        # Streamlit widgets with a `key` restore their value from session_state
        # on every rerun, which would OVERRIDE a new default index. To force
        # the selectbox to pick up the clicked building, we write directly to
        # the widget's own state key BEFORE the widget is created.
        stored_id = st.session_state.get('selected_building_id')
        if stored_id is not None:
            try:
                stored_id_int = int(stored_id)
                if stored_id_int in building_ids:
                    st.session_state['bldg_details_selectbox'] = stored_id_int
            except (ValueError, TypeError):
                pass

        try:
            if stored_id is not None:
                default_idx = sorted_ids.index(int(stored_id))
            elif selected_location == "Pamunkey" and 579536184 in building_ids:
                default_idx = sorted_ids.index(579536184)
            elif selected_location == "Mastic Beach" and 8386312 in building_ids:
                default_idx = sorted_ids.index(8386312)
            else:
                default_idx = 0
        except (ValueError, TypeError):
            default_idx = 0

        came_from_map = (stored_id is not None and default_idx > 0
                         and st.session_state.get('bldg_details_selectbox')
                         == int(stored_id) if stored_id is not None else False)
        if came_from_map:
            st.caption(
                f"📍 Showing the building you selected on the **Building Map** "
                f"(#{stored_id}). Use the controls below to choose a different one."
            )

        # --- Search-by-ID + selectbox, side-by-side ---
        col_search, col_pick = st.columns([1, 2])

        with col_search:
            search_raw = st.text_input(
                "🔍 Search by Building ID",
                value="",
                placeholder="e.g. 12345",
                key='bldg_search_input',
                help="Type a building ID and press Enter to jump to it.",
            )
            # If the user typed a valid id, make it take precedence over the
            # selectbox by writing to the selectbox's own state key BEFORE it
            # is instantiated (same trick as for the map-click handoff).
            search_hit = None
            if search_raw.strip():
                try:
                    sid = int(search_raw.strip())
                    if sid in building_ids:
                        search_hit = sid
                        st.session_state['bldg_details_selectbox'] = sid
                        # Re-resolve the default index so the selectbox opens on it
                        default_idx = sorted_ids.index(sid)
                    else:
                        st.caption(
                            f"⚠️ Building **#{sid}** not found in this dataset "
                            f"(filtered by {occupancy_label})."
                        )
                except ValueError:
                    st.caption("⚠️ Please enter a numeric building ID.")

        with col_pick:
            selected_id = st.selectbox(
                "Or pick from the list",
                options=sorted_ids,
                index=default_idx,
                format_func=lambda x: f"Building #{x}",
                key='bldg_details_selectbox',
            )

        if search_hit is not None:
            st.caption(f"✅ Jumped to **Building #{search_hit}**.")

        # Keep the cross-tab pointer in sync with the current dropdown value.
        if selected_id is not None:
            st.session_state['selected_building_id'] = int(selected_id)

        if selected_id:
            df_building = df_buildings[df_buildings['id'] == selected_id]
            building_info = df_building.iloc[0]

            building_dfe_status = building_info.get('DFE_Status', 'Unknown')
            is_above_dfe = building_dfe_status == 'Above DFE'

            # MATLAB v14 per-building flags (see ADD_STORY_LABEL notes).
            # Elevate is hidden when the building is above the DFE OR
            # elevation is not advised for it (ElevationEligible = 0).
            _elev_ok_b = _elev_eligible_flag(building_info.get('ElevationEligible'))
            hide_elevate_b = is_above_dfe or not _elev_ok_b
            # WFP 1st: relabelled "Add a story" for one-story homes the
            # model scored as an add-a-story conversion; hidden for any
            # other one-story building (only when the bundle carries the
            # OneStoryAddStory column, so older bundles are unchanged).
            _add_story_b = _flag_true(building_info.get('OneStoryAddStory'))
            _hide_wfp1_b = (
                'OneStoryAddStory' in building_info.index
                and not _add_story_b
                and _is_one_story_bldg(building_info.get('SOID'),
                                       building_info.get('number_of_stories'))
            )

            # Header line: lead with the address when we have one (NSI
            # ships them for ~75% of buildings); fall back to the bare
            # ID otherwise. Either way, the ID stays visible.
            addr = building_info.get('address') if 'address' in building_info else None
            if pd.notna(addr) and str(addr).strip():
                st.subheader(f"{addr}")
                st.caption(f"Building #{selected_id}")
            else:
                st.subheader(f"Building #{selected_id}")

            col1, col2, col3, col4 = st.columns(4)

            with col1:
                st.markdown("**Type**")
                st.write(building_info.get('occupancy_type', 'N/A'))
                if 'number_of_stories' in building_info:
                    st.markdown("**Stories**")
                    st.write(building_info.get('number_of_stories', 'N/A'))

            with col2:
                if 'year_built' in building_info:
                    st.markdown("**Year Built**")
                    yr = building_info.get('year_built')
                    st.write(f"{int(yr)}" if pd.notna(yr) else 'N/A')
                if 'area' in building_info:
                    st.markdown("**Area (sf)**")
                    area = building_info.get('area', 0)
                    st.write(f"{area:,.0f}" if pd.notna(area) else 'N/A')

            with col3:
                if 'foundation_type' in building_info:
                    st.markdown("**Foundation**")
                    st.write(building_info.get('foundation_type', 'N/A'))
                if 'FFE_ft' in building_info:
                    st.markdown("**FFE (ft)**")
                    ffe = building_info.get('FFE_ft', 0)
                    st.write(f"{ffe:.2f}" if pd.notna(ffe) else 'N/A')

            with col4:
                if 'structure_value' in building_info:
                    st.markdown("**Structure Value**")
                    st.write(format_currency(building_info.get('structure_value', 0)))
                if 'DFE_Status' in building_info:
                    st.markdown("**DFE Status**")
                    fp_status = building_info.get('DFE_Status', 'N/A')
                    if fp_status == 'Under DFE':
                        st.error(fp_status)
                    else:
                        st.success(fp_status)

            # Building-type and SOID provenance row - small, gray,
            # optional. Only renders when the bundle ships these fields.
            provenance_bits = []
            if pd.notna(building_info.get('building_type')):
                provenance_bits.append(f"Building type: **{building_info['building_type']}**")
            if pd.notna(building_info.get('SOID')):
                provenance_bits.append(f"Structural Occupancy ID: **{building_info['SOID']}**")
            if 'foundation_height' in building_info and pd.notna(building_info.get('foundation_height')):
                provenance_bits.append(f"Foundation height: **{building_info['foundation_height']:.1f} ft**")
            if 'ground_elevation' in building_info and pd.notna(building_info.get('ground_elevation')):
                provenance_bits.append(f"Ground elevation: **{building_info['ground_elevation']:.2f} ft NAVD88**")
            if provenance_bits:
                st.caption(" · ".join(provenance_bits))

            # ----------------------------------------------------------
            # Annual-max water level exposure panel
            # ----------------------------------------------------------
            # Direct decision-support metric the bundle's raw MC
            # ensemble unlocks: P(annual-max stillwater ≥ FFE) for this
            # building, evaluated year-by-year under both SLR scenarios.
            # We compute against the raw 1,000-realization MC sheets
            # (water_levels[<slr>_mc]); when those aren't available we
            # silently skip - no fallback to percentile interpolation
            # since those would be misleading for a binary threshold.
            ffe_val = building_info.get('FFE_ft')
            bfe_local = (loc_entry or {}).get('bfe_ft')
            wl_data = (loc_entry or {}).get('water_levels') or {}
            if (pd.notna(ffe_val) and ffe_val
                    and ('50th-percentile_mc' in wl_data
                         or '90th-percentile_mc' in wl_data)):
                st.divider()
                st.subheader("Annual flood exposure")
                st.caption(
                    "Probability that the annual-maximum stillwater level reaches or "
                    f"exceeds this building's first-floor elevation "
                    f"(**{ffe_val:.2f} ft NAVD88**) in each evaluation year, "
                    "computed directly from the 1,000-realization Monte Carlo ensemble."
                )

                # Available years from the per-building damage table, plus
                # the present-day baseline (the MC sheets' 2025 base year)
                # prepended so the panel leads with current conditions.
                # df_building drops the baseline, so we read it from the MC
                # 'Year' column directly.
                _exp_mc_years = set()
                for _k in ('50th-percentile', '90th-percentile'):
                    _m = wl_data.get(f'{_k}_mc')
                    if _m is not None and 'Year' in _m.columns:
                        _exp_mc_years |= set(int(v) for v in _m['Year'].unique())
                _dmg_years = [int(y) for y in df_building['TargetYear'].unique()]
                _present_y = min(_exp_mc_years) if _exp_mc_years else None
                target_years_local = sorted(_dmg_years)
                if _present_y is not None and _present_y not in _dmg_years:
                    target_years_local = [_present_y] + target_years_local

                exp_rows = []
                for slr_key, slr_label in (('50th-percentile', 'Median SLR (P50)'),
                                            ('90th-percentile', 'High-End SLR (P90)')):
                    mc_df = wl_data.get(f'{slr_key}_mc')
                    if mc_df is None or 'Year' not in mc_df.columns:
                        continue
                    mc_cols = [c for c in mc_df.columns if c.startswith('MC_')]
                    if not mc_cols:
                        continue
                    for yr in target_years_local:
                        yr_mc = mc_df[mc_df['Year'] == int(yr)]
                        if yr_mc.empty:
                            continue
                        arr = yr_mc[mc_cols].to_numpy(dtype=float).flatten()
                        n = arr.size
                        if n == 0:
                            continue
                        p_ffe = float((arr >= ffe_val).sum()) / n
                        p_bfe = (float((arr >= bfe_local).sum()) / n
                                 if bfe_local is not None else float('nan'))
                        # Present-day baseline is shown as "2026" to match
                        # the present column used in the other tabs.
                        label = ("2026" if (_present_y is not None
                                            and int(yr) == _present_y)
                                 else str(int(yr)))
                        exp_rows.append({
                            'SLR Scenario':         slr_label,
                            'Year':                 label,
                            f'P(WL ≥ FFE = {ffe_val:.1f} ft)':
                                f"{p_ffe*100:.1f}%",
                            **({f'P(WL ≥ BFE = {bfe_local:g} ft)':
                                f"{p_bfe*100:.1f}%"}
                               if bfe_local is not None else {}),
                            'Median annual max (ft)': f"{float(np.median(arr)):.2f}",
                            'P95 annual max (ft)':    f"{float(np.percentile(arr, 95)):.2f}",
                        })

                if exp_rows:
                    st.dataframe(pd.DataFrame(exp_rows),
                                 use_container_width=True, hide_index=True)
                    st.caption(
                        "**P(WL ≥ FFE)** is the simulated annual exceedance "
                        "probability for this building's first floor - a direct, "
                        "decision-relevant counterpart to the cumulative-damage "
                        "estimates below. Years and SLR trajectories use the "
                        "same MC realizations that fed the damage chain."
                    )

            # ----------------------------------------------------------
            # Flood depth at building location
            # ----------------------------------------------------------
            # Companion to the exposure panel, but framed in depth (ft)
            # rather than probability. We sample three percentiles of
            # the simulated annual-maximum stillwater distribution
            # under the SIDEBAR-SELECTED SLR scenario and convert each
            # to a flood depth at the building by subtracting the
            # ground elevation. Percentiles match the user's spec:
            #   * Yearly      → P01 of annual max  (level the property
            #                   sees in ~99 % of years)
            #   * 10 % chance → P10 of annual max
            #   * 1  % chance → P99 of annual max  (≈ 100-year flood)
            # Negative values are clipped to zero (site stays dry).
            ground_elev = building_info.get('ground_elevation')
            mc_for_scenario = wl_data.get(f'{scenario}_mc')
            # Prepend the present-day baseline (MC sheets' 2025 base year,
            # displayed as "2026") so the table leads with current conditions.
            _present_y2 = (int(mc_for_scenario['Year'].min())
                           if (mc_for_scenario is not None
                               and 'Year' in mc_for_scenario.columns
                               and len(mc_for_scenario)) else None)
            _dmg_years2 = [int(y) for y in df_building['TargetYear'].unique()]
            target_years_local = sorted(_dmg_years2)
            if _present_y2 is not None and _present_y2 not in _dmg_years2:
                target_years_local = [_present_y2] + target_years_local
            if (pd.notna(ground_elev)
                    and mc_for_scenario is not None
                    and 'Year' in mc_for_scenario.columns
                    and target_years_local):
                st.divider()
                scen_pretty = {
                    '50th-percentile': 'Median SLR (P50)',
                    '90th-percentile': 'High-End SLR (P90)',
                }.get(scenario, scenario)
                st.subheader(f"Flood depth at this building - {scen_pretty}")
                st.caption(
                    f"Annual-maximum stillwater **flood depth above ground** "
                    f"at this property (ground elevation: "
                    f"**{float(ground_elev):.2f} ft NAVD88**) under the "
                    f"{scen_pretty.lower()} trajectory. The three depth "
                    f"columns sample the simulated annual-maximum "
                    f"distribution at three return-period proxies. "
                    f"Negative values are clipped to zero (site stays dry)."
                )

                # (column-label, percentile)
                pct_specs = [
                    ('Yearly (P01)',     1),
                    ('10% chance (P10)', 10),
                    ('1% chance (P99)',  99),
                ]

                fd_rows = []
                mc_cols_fd = [c for c in mc_for_scenario.columns
                              if c.startswith('MC_')]
                for yr in target_years_local:
                    yr_mc = mc_for_scenario[mc_for_scenario['Year'] == int(yr)]
                    if yr_mc.empty or not mc_cols_fd:
                        continue
                    arr = yr_mc[mc_cols_fd].to_numpy(dtype=float).flatten()
                    if arr.size == 0:
                        continue
                    # Present-day baseline row is shown as "2026" to match
                    # the present column used in the other tabs.
                    yr_int = int(yr)
                    year_display = ("2026" if (_present_y2 is not None
                                               and yr_int == _present_y2)
                                    else str(yr_int))
                    row = {'Year': year_display}
                    for col_label, pct in pct_specs:
                        wl = float(np.percentile(arr, pct))
                        depth = wl - float(ground_elev)
                        wl_cell = f"{wl:.2f}"
                        if depth <= 0:
                            row[col_label] = f"Dry  (WL {wl_cell})"
                        else:
                            row[col_label] = f"{depth:.2f} ft  (WL {wl_cell})"
                    fd_rows.append(row)

                if fd_rows:
                    st.dataframe(pd.DataFrame(fd_rows),
                                 use_container_width=True, hide_index=True)
                    st.caption(
                        "Each cell shows **flood depth above ground (ft)** "
                        "with the corresponding **stillwater level (ft NAVD88)** "
                        "in parentheses. Depths are computed from this "
                        "building's ground elevation and the simulated "
                        "annual-maximum water-level percentile under the "
                        "selected SLR scenario; switch SLR above "
                        "to compare. "
                        "*Note:* the three return-period proxies use "
                        "P01 / P10 / P99 of the annual-maximum distribution "
                        "as you specified - let me know if you'd prefer "
                        "P50 / P90 / P99 (the standard engineering reading "
                        "of \"yearly / 10-year / 100-year\")."
                    )

            # ----------------------------------------------------------
            # Retrofit "slide" cards - the numbers laid out like the
            # workshop action slides (elevation references + Benefit and
            # Remaining-recovery-cost by horizon), framed so they're easy
            # to transcribe. Cards appear only when the retrofit applies:
            # Elevate is absent above DFE, WFP Basement absent without a
            # basement, WFP First-floor only for multi-story buildings.
            # ----------------------------------------------------------
            _scn_for_cards = scenario
            _scn_lbl_cards = {
                '50th-percentile': 'Median SLR (P50)',
                '90th-percentile': 'High-End SLR (P90)',
            }.get(_scn_for_cards, _scn_for_cards)
            _bfe = (loc_entry or {}).get('bfe_ft')
            _dfe = (float(_bfe) + 2.0) if (_bfe is not None and pd.notna(_bfe)) else None
            _gnd = building_info.get('ground_elevation')
            _ffe_c = building_info.get('FFE_ft')
            _nstory = building_info.get('number_of_stories')

            _card_horizons = [(2040, '15-yr'), (2055, '30-yr'), (2060, '35-yr')]
            _retrofit_specs = [
                ('Raise Utilities', 'raise mechanicals', False),
                ('WFP B',           'wet floodproof the basement', False),
                ('Elevate',         'raise the house', True),
                ('WFP 1st',         'wet floodproof the first floor', False),
            ]

            def _cum(action, yr, pct):
                _r = df_building[(df_building['Action'] == action)
                                 & (df_building['SLR'] == _scn_for_cards)
                                 & (df_building['TargetYear'] == yr)]
                col = f'CumEAD_{pct}'
                if _r.empty or col not in _r.columns:
                    return None
                v = _r[col].iloc[0]
                return float(v) if pd.notna(v) else None

            def _money_c(v):
                return format_currency(v) if v is not None else "-"

            _cards_html = []
            for _akey, _atitle, _show_raise in _retrofit_specs:
                if df_building[(df_building['Action'] == _akey)
                               & (df_building['SLR'] == _scn_for_cards)].empty:
                    continue
                # WFP 1st Floor is shown whenever the model has data for it
                # (it is dropped upstream for RES2 / manufactured homes, so
                # the emptiness check above already hides it there). It is
                # no longer gated on story count, so the residential example
                # always shows a "wet floodproof the first floor" box when
                # the building has WFP 1st data.
                # MATLAB v14: for a one-story home scored as add-a-story
                # the box becomes "add a story"; for any other one-story
                # building it is hidden. Elevate is hidden when elevation
                # is not advised (ElevationEligible = 0).
                if _akey == 'Elevate' and not _elev_ok_b:
                    continue
                if _akey == 'WFP 1st' and _hide_wfp1_b:
                    continue
                _is_add_story_card = (_akey == 'WFP 1st' and _add_story_b)
                if _is_add_story_card:
                    _atitle = 'add a story and move the living space up'

                _elev_bits = []
                if pd.notna(_gnd):
                    _elev_bits.append(f"Ground: <b>{float(_gnd):.1f} ft</b>")
                if pd.notna(_ffe_c):
                    if _show_raise and _dfe is not None:
                        _elev_bits.append(
                            f"First floor: <b>raised from {float(_ffe_c):.1f} ft to {_dfe:.1f} ft</b>")
                    elif _is_add_story_card:
                        _elev_bits.append(
                            f"Living floor: <b>moved up from {float(_ffe_c):.1f} ft to "
                            f"{float(_ffe_c) + ADD_STORY_HEIGHT_FT:.1f} ft</b>")
                    else:
                        _elev_bits.append(f"First floor: <b>{float(_ffe_c):.1f} ft</b>")
                if _dfe is not None:
                    _elev_bits.append(f"DFE: <b>{_dfe:.1f} ft</b>")
                if _bfe is not None and pd.notna(_bfe):
                    _elev_bits.append(f"BFE: <b>{float(_bfe):.1f} ft</b>")

                _ben_lines, _rem_lines = [], []
                for _yr, _hlabel in _card_horizons:
                    b50, a50 = _cum('No mitigation', _yr, 'P50'), _cum(_akey, _yr, 'P50')
                    b05, a05 = _cum('No mitigation', _yr, 'P05'), _cum(_akey, _yr, 'P05')
                    b95, a95 = _cum('No mitigation', _yr, 'P95'), _cum(_akey, _yr, 'P95')
                    if b50 is not None and a50 is not None:
                        ben = max(b50 - a50, 0.0)
                        rng = ""
                        if None not in (b05, a05, b95, a95):
                            _e1, _e2 = max(b05 - a05, 0.0), max(b95 - a95, 0.0)
                            rng = f" ({_money_c(min(_e1, _e2))}–{_money_c(max(_e1, _e2))})"
                        _ben_lines.append(
                            f"Benefit {_hlabel} ({_yr}): <b>{_money_c(ben)}</b>{rng}")
                    if a50 is not None:
                        rrng = (f" ({_money_c(a05)}–{_money_c(a95)})"
                                if (a05 is not None and a95 is not None) else "")
                        _rem_lines.append(
                            f"Remaining recovery cost {_hlabel} ({_yr}): <b>{_money_c(a50)}</b>{rrng}")

                card = (
                    '<div style="border:2px solid #1f6f8b;border-radius:10px;'
                    'padding:0.85rem 1.05rem;margin:0.6rem 0;background:#f5fafc;">'
                    f'<div style="font-size:1.18rem;font-weight:800;color:#0f4c5c;'
                    f'margin-bottom:0.35rem;">Should they {_atitle}?</div>'
                    + ('<div style="color:#374151;margin-bottom:0.5rem;">'
                       + ' &nbsp;•&nbsp; '.join(_elev_bits) + '</div>' if _elev_bits else '')
                    + ''.join(f'<div style="color:#1f2937;font-size:1.02rem;">{_l}</div>'
                              for _l in _ben_lines)
                    + ('<div style="height:0.35rem;"></div>' if _rem_lines else '')
                    + ''.join(f'<div style="color:#6b7280;">{_l}</div>' for _l in _rem_lines)
                    + '</div>'
                )
                _cards_html.append(card)

            if _cards_html:
                st.divider()
                st.subheader("Retrofit options - figures for the action slides")
                st.caption(
                    f"Computed under **{_scn_lbl_cards}** (switch SLR above). "
                    f"Benefit = avoided cumulative recovery cost vs. taking no action; remaining = the cost "
                    f"that still occurs with the action. Median with (low–high) range. Each box is one slide."
                )
                for _c in _cards_html:
                    st.markdown(_c, unsafe_allow_html=True)

            st.divider()

            st.subheader("Damage Trajectory: SLR Scenario Comparison")
            st.caption(
                "Cumulative damage under the No-Mitigation baseline for both sea-level rise scenarios. "
                "Solid lines are the median estimate (P50); shaded bands show the 90% confidence interval (P05–P95)."
            )

            df_traj = df_building[df_building['Action'] == 'No mitigation'].sort_values(['SLR', 'TargetYear'])

            if not df_traj.empty:
                fig_building = go.Figure()

                scenario_styles = {
                    '50th-percentile': {
                        'label': 'Median SLR (P50)',
                        'line_color': '#3b82f6',                  # blue, matches Tab 4
                        'fill_color': 'rgba(59, 130, 246, 0.18)',
                    },
                    '90th-percentile': {
                        'label': 'High-End SLR (P90)',
                        'line_color': '#ef4444',                  # red, matches Tab 4
                        'fill_color': 'rgba(239, 68, 68, 0.18)',
                    },
                }

                for slr_key, style in scenario_styles.items():
                    df_s = df_traj[df_traj['SLR'] == slr_key]
                    if df_s.empty:
                        continue
                    # 90% CI band first, so the central line draws on top
                    fig_building.add_trace(go.Scatter(
                        x=list(df_s['TargetYear']) + list(df_s['TargetYear'])[::-1],
                        y=list(df_s['CumEAD_P95']) + list(df_s['CumEAD_P05'])[::-1],
                        fill='toself', fillcolor=style['fill_color'],
                        line=dict(color='rgba(255,255,255,0)'),
                        name=f"{style['label']} - 90% CI",
                        hoverinfo='skip', showlegend=True))
                    # Central P50 line
                    fig_building.add_trace(go.Scatter(
                        x=df_s['TargetYear'], y=df_s['CumEAD_P50'],
                        mode='lines+markers',
                        name=style['label'],
                        line=dict(color=style['line_color'], width=3),
                        marker=dict(size=11, line=dict(color='white', width=1.5)),
                        hovertemplate='Year %{x}<br>P50: $%{y:,.0f}<extra></extra>'))

                # Smart y-axis ticks for the trajectory chart
                _t_max = df_traj['CumEAD_P95'].max() if 'CumEAD_P95' in df_traj.columns else df_traj['CumEAD_P50'].max()
                t_ticks, t_labels = smart_money_ticks(_t_max, target_n=6)

                fig_building.update_layout(
                    title=dict(
                        text=f"Cumulative Damage Trajectory - Building #{selected_id}",
                        x=0.02, xanchor='left', font=dict(size=16)),
                    xaxis_title="Year",
                    yaxis_title="Cumulative Damage",
                    height=460,
                    hovermode='x unified',
                    legend=dict(orientation='h', yanchor='bottom', y=1.02,
                                xanchor='right', x=1, bgcolor='rgba(255,255,255,0.85)'),
                    plot_bgcolor='white',
                    margin=dict(l=60, r=20, t=70, b=50),
                )
                _bd_xs = sorted(df_traj['TargetYear'].unique())
                _bd_x_labels = [str(int(y)) for y in _bd_xs]
                # Constrain x-range to actual data span with a small pad
                # (otherwise Plotly auto-extends back several years).
                if _bd_xs:
                    _bd_xpad = max(1.0, 0.04 * (max(_bd_xs) - min(_bd_xs)))
                    _bd_xrange = [min(_bd_xs) - _bd_xpad, max(_bd_xs) + _bd_xpad]
                else:
                    _bd_xrange = None
                fig_building.update_xaxes(
                    showgrid=True, gridcolor='#e5e7eb',
                    tickmode='array', tickvals=_bd_xs, ticktext=_bd_x_labels,
                    range=_bd_xrange,
                    showline=True, linecolor='#cbd5e1')
                fig_building.update_yaxes(
                    showgrid=True, gridcolor='#e5e7eb',
                    showline=True, linecolor='#cbd5e1', zeroline=False,
                    tickmode='array', tickvals=t_ticks, ticktext=t_labels)

                st.plotly_chart(fig_building, use_container_width=True)

                # ---- Side-by-side numeric comparison ----
                pivot_p50 = df_traj.pivot_table(index='TargetYear', columns='SLR', values='CumEAD_P50')
                pivot_p05 = df_traj.pivot_table(index='TargetYear', columns='SLR', values='CumEAD_P05')
                pivot_p95 = df_traj.pivot_table(index='TargetYear', columns='SLR', values='CumEAD_P95')

                def _fmt_range(lo, hi):
                    if pd.isna(lo) or pd.isna(hi):
                        return "-"
                    return f"{format_currency(lo)} – {format_currency(hi)}"

                def _fmt_val(v):
                    return "-" if pd.isna(v) else format_currency(v)

                cmp_rows = []
                for yr in sorted(pivot_p50.index):
                    med = pivot_p50.loc[yr].get('50th-percentile', float('nan'))
                    high = pivot_p50.loc[yr].get('90th-percentile', float('nan'))
                    med_lo = pivot_p05.loc[yr].get('50th-percentile', float('nan'))
                    med_hi = pivot_p95.loc[yr].get('50th-percentile', float('nan'))
                    high_lo = pivot_p05.loc[yr].get('90th-percentile', float('nan'))
                    high_hi = pivot_p95.loc[yr].get('90th-percentile', float('nan'))
                    if pd.notna(med) and pd.notna(high):
                        delta = high - med
                        pct = (delta / med * 100) if med > 0 else float('nan')
                    else:
                        delta, pct = float('nan'), float('nan')
                    cmp_rows.append({
                        'Year': int(yr),
                        'Median SLR - P50': _fmt_val(med),
                        'Median SLR - 90% CI': _fmt_range(med_lo, med_hi),
                        'High-End SLR - P50': _fmt_val(high),
                        'High-End SLR - 90% CI': _fmt_range(high_lo, high_hi),
                        'High-End vs Median (Δ)': _fmt_val(delta),
                        'Increase (%)': f"{pct:.1f}%" if pd.notna(pct) else "-",
                    })

                st.markdown("**Side-by-side comparison across planning horizons**")
                st.dataframe(pd.DataFrame(cmp_rows), use_container_width=True, hide_index=True)

            st.subheader(f"Adaptation Strategy Comparison - Both SLR Scenarios ({target_year})")
            st.caption(
                "How each adaptation strategy performs under both sea-level rise scenarios at the selected planning horizon. "
                "Box edges show the 25th and 75th percentiles, the white center line is the median (P50), "
                "and whiskers extend to the 5th and 95th percentiles of this building's cumulative damage distribution."
            )

            # Both scenarios for the selected target year
            df_building_year = df_building[df_building['TargetYear'] == target_year].copy()

            if hide_elevate_b:
                df_building_year = df_building_year[df_building_year['Action'] != 'Elevate']
            if _hide_wfp1_b:
                df_building_year = df_building_year[df_building_year['Action'] != 'WFP 1st']

            if not df_building_year.empty:
                # Consistent ordering: baseline first, then strategies from least → most invasive
                bd_action_order = ['No mitigation', 'Raise Utilities', 'WFP B', 'Elevate', 'WFP 1st']
                bd_action_labels = {
                    'No mitigation':   'No Mitigation',
                    'Raise Utilities': 'Raise Utilities',
                    'WFP B':           'WFP Basement',
                    'WFP 1st':         'WFP 1st Floor',
                    'Elevate':         'Elevate',
                }
                if _add_story_b:
                    bd_action_labels['WFP 1st'] = ADD_STORY_LABEL
                if _hide_wfp1_b:
                    bd_action_order = [a for a in bd_action_order if a != 'WFP 1st']
                # For RES2 (manufactured housing) and RES4 (small mixed-use /
                # temporary lodging) buildings, basement wet-floodproofing
                # and 1st-floor wet-floodproofing aren't viable retrofits -
                # these structures typically have no basement and no
                # conditioned 1st-floor envelope to dry out. Restrict the
                # benefit chart (and the strategy axis below) to the
                # retrofits that actually apply: Raise Utilities and
                # Elevate. Baseline (No Mitigation) is kept here for the
                # benefit-stats reference; the chart still drops it via
                # `bd_actions_for_chart` below.
                _occ_for_strategy_filter = str(
                    building_info.get('occupancy_type', '')
                ).upper()
                _is_res2_or_res4 = (
                    _occ_for_strategy_filter.startswith('RES2')
                    or _occ_for_strategy_filter.startswith('RES4')
                )
                if _is_res2_or_res4:
                    bd_action_order = [
                        a for a in bd_action_order
                        if a not in ('WFP B', 'WFP 1st')
                    ]
                bd_actions_present = [a for a in bd_action_order
                                      if a in df_building_year['Action'].unique()]

                # Pivots over (Action, SLR) - values are this building's P05/P50/P95
                bd_piv = df_building_year.pivot_table(
                    index='Action', columns='SLR',
                    values=['CumEAD_P05', 'CumEAD_P50', 'CumEAD_P95'],
                )

                def _pct(action, slr_key, col):
                    try:
                        v = float(bd_piv.loc[action, (col, slr_key)])
                        return v if pd.notna(v) else float('nan')
                    except (KeyError, ValueError):
                        return float('nan')

                def _benefit_stats(action, slr_key):
                    """Return a 5-tuple (P05, P25, P50, P75, P95) of
                    avoided-damage benefit for a retrofit strategy,
                    clamped so the median is always between the bounds.

                    Benefit is the avoided damage relative to No
                    Mitigation under the same SLR scenario, computed
                    percentile-by-percentile. Returns ``None`` for the
                    No-Mitigation row (no sensible self-benefit).

                    We pull P25 and P75 straight from the workbook when
                    available (the ALL format stores them), so the box
                    edges of the benefit chart are real percentile
                    differences rather than CDF-linear approximations
                    between P05 and P95.
                    """
                    if action == 'No mitigation':
                        return None
                    # Pull every percentile we have. P25/P75 will be NaN
                    # for older workbooks that don't store them; in that
                    # case we fall back to a 3-tuple.
                    b05 = _pct('No mitigation', slr_key, 'CumEAD_P05')
                    b25 = _pct('No mitigation', slr_key, 'CumEAD_P25')
                    b50 = _pct('No mitigation', slr_key, 'CumEAD_P50')
                    b75 = _pct('No mitigation', slr_key, 'CumEAD_P75')
                    b95 = _pct('No mitigation', slr_key, 'CumEAD_P95')
                    s05 = _pct(action, slr_key, 'CumEAD_P05')
                    s25 = _pct(action, slr_key, 'CumEAD_P25')
                    s50 = _pct(action, slr_key, 'CumEAD_P50')
                    s75 = _pct(action, slr_key, 'CumEAD_P75')
                    s95 = _pct(action, slr_key, 'CumEAD_P95')
                    if any(pd.isna(v) for v in (b05, b50, b95, s05, s50, s95)):
                        return None
                    raw_ben05 = b05 - s05
                    raw_ben50 = b50 - s50
                    raw_ben95 = b95 - s95
                    have_quartiles = not any(
                        pd.isna(v) for v in (b25, b75, s25, s75)
                    )
                    if have_quartiles:
                        raw_ben25 = b25 - s25
                        raw_ben75 = b75 - s75
                        # Clamp the lower whisker, Q1, Q3, and upper
                        # whisker so the box and whisker geometry is
                        # monotone - even if rank-correlation between
                        # baseline and strategy realizations briefly
                        # inverts, the plotted geometry stays sensible.
                        vals = sorted([raw_ben05, raw_ben25, raw_ben50,
                                       raw_ben75, raw_ben95])
                        lo, q1, _, q3, hi = vals
                        return (lo, q1, raw_ben50, q3, hi)
                    # Fallback: 3-tuple (whisker, median, whisker), with
                    # build_box_whisker_panel interpolating Q1/Q3.
                    lo = min(raw_ben05, raw_ben50, raw_ben95)
                    hi = max(raw_ben05, raw_ben50, raw_ben95)
                    return (lo, raw_ben50, hi)

                # Strategies shown on the chart exclude No Mitigation (no
                # self-benefit), matching the Benefit columns in the table.
                bd_actions_for_chart = [a for a in bd_actions_present
                                         if a != 'No mitigation']

                bd_scenario_data = {
                    slr_key: [_benefit_stats(a, slr_key) for a in bd_actions_for_chart]
                    for slr_key, *_ in SCENARIO_SPECS
                }

                if bd_actions_for_chart and any(
                    any(v is not None for v in vals)
                    for vals in bd_scenario_data.values()
                ):
                    fig_strat = build_box_whisker_panel(
                        group_labels=[bd_action_labels[a] for a in bd_actions_for_chart],
                        scenario_data=bd_scenario_data,
                        panel_title=(
                            f"Benefit (avoided damage) by Strategy and SLR Scenario - "
                            f"Building #{selected_id}, Year {target_year}"
                        ),
                        y_label="Benefit - avoided cumulative damage vs No Mitigation",
                        height=500,
                    )

                    st.plotly_chart(fig_strat, use_container_width=True)
                    st.caption(
                        "Each box shows the distribution of this building's **avoided damage** "
                        "(No-Mitigation damage minus the strategy's remaining damage) at the same "
                        "percentile rank, under the selected target year and for both SLR scenarios. "
                        "The white center line is the median benefit (matches the *Benefit - Median* "
                        "column below); the whiskers reach to the 5th and 95th percentile benefits "
                        "(matching the *Benefit - 5th pctile* and *Benefit - 95th pctile* columns). "
                        "No Mitigation is omitted because it has no self-benefit."
                    )

                # ============================================================
                # Strategy performance tables - Damage, Benefit, Remaining damage
                # Separate Median / Min / Max columns (not single "range" cells)
                # ============================================================
                def _fmt_v(v):
                    return "-" if pd.isna(v) else format_currency(v)
                def _fmt_pct(v):
                    return "-" if pd.isna(v) else f"{v:.1f}%"

                # Clamp (min, median, max) so the median is always bracketed by
                # the printed min and max. Under perfect positive rank-correlation
                # between baseline and strategy realizations, the benefit's
                # implied P05/P95 are baseline_PX - strategy_PX; those can
                # occasionally straddle the median tightly, so we clamp to keep
                # the display self-consistent.
                def _bracket(lo, med, hi):
                    vals = [v for v in (lo, med, hi) if pd.notna(v)]
                    if not vals:
                        return float('nan'), float('nan'), float('nan')
                    return min(vals), med, max(vals)

                # Mapping from sidebar's scenario label -> SLR key in the data
                _sidebar_slr_key = {
                    '50th-percentile': '50th-percentile',
                    '90th-percentile': '90th-percentile',
                }.get(scenario, scenario)

                def _build_strategy_rows_for_slr(year, slr_key):
                    """Per-strategy rows for a given (year, SLR) - split columns.
                    Returns list[dict] with Remaining damage (Med/Min/Max),
                    Benefit (Med/Min/Max), and Reduction %."""
                    df_y = df_building[df_building['TargetYear'] == year].copy()
                    if hide_elevate_b:
                        df_y = df_y[df_y['Action'] != 'Elevate']
                    if df_y.empty:
                        return []
                    df_ys = df_y[df_y['SLR'] == slr_key]
                    if df_ys.empty:
                        return []

                    actions_here = [a for a in bd_action_order
                                    if a in df_ys['Action'].unique()]

                    ps = df_ys.set_index('Action')

                    def _get(action, col):
                        try:
                            v = ps.loc[action, col]
                            # If duplicate, pandas may return a Series; take first
                            if hasattr(v, 'iloc'):
                                v = v.iloc[0]
                            return float(v) if pd.notna(v) else float('nan')
                        except (KeyError, ValueError):
                            return float('nan')

                    base05 = _get('No mitigation', 'CumEAD_P05')
                    base50 = _get('No mitigation', 'CumEAD_P50')
                    base95 = _get('No mitigation', 'CumEAD_P95')

                    rows = []
                    for action in actions_here:
                        s05 = _get(action, 'CumEAD_P05')
                        s50 = _get(action, 'CumEAD_P50')
                        s95 = _get(action, 'CumEAD_P95')

                        # Remaining damage bounds (clamped so median is inside)
                        dmg_lo, dmg_med, dmg_hi = _bracket(s05, s50, s95)

                        if action == 'No mitigation':
                            ben_lo = ben_med = ben_hi = float('nan')
                            red_pct = float('nan')
                        else:
                            raw_ben05 = base05 - s05 if pd.notna(base05) and pd.notna(s05) else float('nan')
                            raw_ben50 = base50 - s50 if pd.notna(base50) and pd.notna(s50) else float('nan')
                            raw_ben95 = base95 - s95 if pd.notna(base95) and pd.notna(s95) else float('nan')
                            ben_lo, ben_med, ben_hi = _bracket(raw_ben05, raw_ben50, raw_ben95)
                            red_pct = (raw_ben50 / base50 * 100) if pd.notna(raw_ben50) and base50 > 0 else float('nan')

                        rows.append({
                            'Strategy':                             bd_action_labels[action],
                            'Cost estimate':                        ("-" if action == 'No mitigation'
                                                                     else ADD_STORY_COST_TEXT
                                                                     if (action == 'WFP 1st' and _add_story_b)
                                                                     else ADAPTATION_COST_ESTIMATES.get(action, "-")),
                            'Remaining damage - 5th pctile':        _fmt_v(dmg_lo),
                            'Remaining damage - Median':            _fmt_v(dmg_med),
                            'Remaining damage - 95th pctile':       _fmt_v(dmg_hi),
                            'Benefit - 5th pctile':                 _fmt_v(ben_lo) if action != 'No mitigation' else "-",
                            'Benefit - Median':                     _fmt_v(ben_med) if action != 'No mitigation' else "-",
                            'Benefit - 95th pctile':                _fmt_v(ben_hi) if action != 'No mitigation' else "-",
                            'Reduction (median)':                   _fmt_pct(red_pct) if action != 'No mitigation' else "-",
                        })
                    return rows

                # ---- Selected-year tables, one per SLR scenario ----
                st.markdown(f"**Strategy performance - Year {target_year}**")
                for slr_key, slr_label in [
                    ('50th-percentile', 'Median SLR (P50)'),
                    ('90th-percentile', 'High-End SLR (P90)'),
                ]:
                    rows = _build_strategy_rows_for_slr(target_year, slr_key)
                    if not rows:
                        continue
                    st.markdown(f"*{slr_label}*")
                    st.dataframe(pd.DataFrame(rows),
                                 use_container_width=True, hide_index=True)

                st.caption(
                    "**Remaining damage** is the cumulative damage left under each strategy, "
                    "reported at the 5th percentile, median (50th), and 95th percentile of the "
                    "Monte Carlo damage realizations. **Benefit** is the avoided damage compared "
                    "to No Mitigation under the same SLR scenario, shown the same way. "
                    "**Reduction (median)** is the percent drop in median damage relative to "
                    "No Mitigation. The printed 5th and 95th percentiles are clamped so the "
                    "median always falls between them. **Cost estimate** is a rough per-measure "
                    "installed-cost range and does not vary by year or SLR scenario. "
                    + ADAPTATION_COST_SOURCE
                )
                if is_above_dfe:
                    st.info(
                        "ℹ️ The **Elevate Structure** option is not shown for this building because its first-floor "
                        "elevation already meets or exceeds the Design Flood Elevation (BFE + 2 ft)."
                    )
                elif not _elev_ok_b:
                    st.info(
                        "ℹ️ The **Elevate Structure** option is not shown for this building because its first floor "
                        "is already close to the Design Flood Elevation (BFE + 2 ft); the model only advises "
                        "elevation when the gap is larger than the threshold set for this location."
                    )
                if _add_story_b:
                    st.info(
                        "ℹ️ This is a one-story home, so **Add a story** replaces wet floodproofing the first floor: "
                        "a new story is built on top, the living space moves up one story, and the ground floor "
                        "is left to flood. Its cost is not yet estimated."
                    )

                # ---- Cross-year comparison table (selected SLR only) ----
                years_all = sorted(df_building['TargetYear'].unique())
                if len(years_all) > 1:
                    st.markdown("---")
                    scenario_label = (
                        "Median SLR (P50)" if _sidebar_slr_key == '50th-percentile'
                        else "High-End SLR (P90)" if _sidebar_slr_key == '90th-percentile'
                        else scenario
                    )
                    st.markdown(
                        "**Strategy performance across all planning horizons**  \n"
                        f"<span style='color:#64748b;font-size:0.9rem;'>"
                        f"Building #{selected_id} - {scenario_label} "
                        "(change the SLR Scenario above to see the other)</span>",
                        unsafe_allow_html=True,
                    )

                    multi_rows = []
                    for yr in years_all:
                        yr_rows = _build_strategy_rows_for_slr(yr, _sidebar_slr_key)
                        if not yr_rows:
                            continue
                        for r in yr_rows:
                            multi_rows.append({'Year': yr, **r})

                    if multi_rows:
                        df_multi = pd.DataFrame(multi_rows)
                        st.dataframe(df_multi, use_container_width=True, hide_index=True)
                        st.caption(
                            "Same building, same metrics as above, broken out by planning horizon "
                            f"under the **{scenario_label}** scenario. Use this to compare how a "
                            "given strategy performs in 2040 vs 2055 vs 2060 vs 2100 - and how its benefit "
                            "and remaining damage evolve as sea level rises."
                        )
    else:
        st.warning("No per-building data available for this location.")

