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
from adapt_core.helpers import (fmt_money_short, format_currency, smart_money_ticks)


def render(ctx):
    _restrict_to_raise_only = ctx._restrict_to_raise_only
    df_agg = ctx.df_agg
    df_buildings = ctx.df_buildings
    loc_entry = ctx.loc_entry
    location_name = ctx.location_name
    mobile_raise_only = ctx.mobile_raise_only
    occupancy_label = ctx.occupancy_label
    scenario = ctx.scenario
    selected_location = ctx.selected_location
    ss = ctx.ss
    target_year = ctx.target_year
    from adapt_core import ui as _ui
    _ui.page_header(
        "Community overview",
        "Community-wide cumulative damage for every adaptation strategy, separated into buildings "
        "<b>Under DFE</b> and <b>Above DFE</b>, with trajectories over time and a comparison of the two "
        "sea-level rise scenarios.",
        module_key="planners", location=location_name,
    )
    _ui.note(f"<b>📍 {location_name}</b> &nbsp;·&nbsp; {ctx._location_blurb(selected_location)}")
    ctx.render_inline_settings(["occ", "year", "scn"])
    if df_agg is not None:
        st.subheader(f"Community-Wide Damage Summary - {location_name} ({occupancy_label}) - {target_year}, {scenario}")

        # In the community summary, hide the WFP First-floor strategy for
        # Pamunkey (manufactured-/single-story-dominant, so first-floor wet
        # floodproofing isn't a meaningful community-wide option there).
        _sum_agg = (df_agg[df_agg['Action'] != 'WFP 1st']
                    if selected_location == "Pamunkey" else df_agg)
        # Mobile-homes-dominated area: compare only raising homes vs baseline.
        if mobile_raise_only:
            _sum_agg = _restrict_to_raise_only(_sum_agg)
            st.caption(
                "🏠 **Mobile-homes-dominated area:** only **raising (elevating) homes** is "
                "shown as an adaptation option, compared against the no-mitigation baseline. "
                "Uncheck the sidebar option to compare all strategies."
            )

        df_current = _sum_agg[
            (_sum_agg['TargetYear'] == target_year) & 
            (_sum_agg['SLR'] == scenario)
        ]

        col1, col2, col3, col4 = st.columns(4)

        baseline_row = df_current[df_current['Action'] == 'No mitigation']

        if not baseline_row.empty:
            baseline_p50 = baseline_row['Total_CumEAD_P50'].values[0]
            baseline_p05 = baseline_row['Total_CumEAD_P05'].values[0]
            baseline_p95 = baseline_row['Total_CumEAD_P95'].values[0]
            num_buildings = baseline_row['Num_Buildings'].values[0]

            infp_baseline = baseline_row['InFP_CumEAD_P50'].values[0] if 'InFP_CumEAD_P50' in baseline_row.columns else 0
            outfp_baseline = baseline_row['OutFP_CumEAD_P50'].values[0] if 'OutFP_CumEAD_P50' in baseline_row.columns else 0

            with col1:
                st.metric(label=f"Total Buildings", value=f"{int(num_buildings):,}")
            with col2:
                st.metric(label="No Mitigation Cumulative Damage (P50)", value=format_currency(baseline_p50),
                          help=f"Range: {format_currency(baseline_p05)} - {format_currency(baseline_p95)}")
            with col3:
                st.metric(label="Under DFE (No Mitigation)", value=format_currency(infp_baseline),
                          help="Buildings with FFE below Design Flood Elevation (BFE+2)")
            with col4:
                st.metric(label="Above DFE (No Mitigation)", value=format_currency(outfp_baseline),
                          help="Buildings with FFE above Design Flood Elevation (BFE+2)")

        st.divider()

        # ================================================================
        # AGGREGATED DAMAGE DISTRIBUTION - Both SLR scenarios side-by-side
        # Box edges = Q1/Q3 (interpolated from CDF); whiskers = P05/P95;
        # white center line = P50. Boxes summarize the distribution of
        # COMMUNITY-TOTAL damage across Monte Carlo realizations.
        # ================================================================
        st.subheader(f"Aggregated Damage Distribution - Year {target_year}")
        st.markdown(
            f"<p style='color:#64748b;font-size:0.95rem;margin-top:-0.5rem;'>"
            "Distribution of <b>community-total</b> cumulative damage across Monte Carlo "
            "realizations. Both SLR scenarios are shown side-by-side, regardless of the "
            "SLR Scenario chosen above.</p>",
            unsafe_allow_html=True,
        )

        df_year_agg = _sum_agg[_sum_agg['TargetYear'] == target_year].copy()

        if df_year_agg.empty:
            st.info(f"No aggregated data for year {target_year}.")
        else:
            action_order_cs = ['No mitigation', 'Raise Utilities', 'WFP B', 'Elevate', 'WFP 1st']
            action_labels_cs = {
                'No mitigation':   'No Mitigation',
                'Raise Utilities': 'Raise Utilities',
                'WFP B':           'WFP Basement',
                'WFP 1st':         'WFP 1st Floor',
                'Elevate':         'Elevate',
            }
            actions_present_cs = [a for a in action_order_cs
                                  if a in df_year_agg['Action'].unique()]

            # Pivots indexed by (Action, SLR). We pull P05/P25/P50/P75/P95
            # so the box edges are taken straight from the workbook's
            # stored quartiles (rather than interpolated between P05/P50/P95).
            piv_p05 = df_year_agg.pivot_table(index='Action', columns='SLR',
                                              values='Total_CumEAD_P05')
            piv_p25 = (df_year_agg.pivot_table(index='Action', columns='SLR',
                                               values='Total_CumEAD_P25')
                       if 'Total_CumEAD_P25' in df_year_agg.columns else None)
            piv_p50 = df_year_agg.pivot_table(index='Action', columns='SLR',
                                              values='Total_CumEAD_P50')
            piv_p75 = (df_year_agg.pivot_table(index='Action', columns='SLR',
                                               values='Total_CumEAD_P75')
                       if 'Total_CumEAD_P75' in df_year_agg.columns else None)
            piv_p95 = df_year_agg.pivot_table(index='Action', columns='SLR',
                                              values='Total_CumEAD_P95')

            # 5-tuple (P05, P25, P50, P75, P95) when stored quartiles are
            # available, otherwise fall back to the legacy 3-tuple and let
            # build_box_whisker_panel interpolate Q1/Q3.
            _have_quartiles = piv_p25 is not None and piv_p75 is not None

            def _agg_stats(action, slr_key):
                if (action not in piv_p50.index) or (slr_key not in piv_p50.columns):
                    return None
                if _have_quartiles:
                    return (
                        float(piv_p05.loc[action, slr_key]),
                        float(piv_p25.loc[action, slr_key]),
                        float(piv_p50.loc[action, slr_key]),
                        float(piv_p75.loc[action, slr_key]),
                        float(piv_p95.loc[action, slr_key]),
                    )
                return (
                    float(piv_p05.loc[action, slr_key]),
                    float(piv_p50.loc[action, slr_key]),
                    float(piv_p95.loc[action, slr_key]),
                )

            def _agg_red_stats(action, slr_key):
                if 'No mitigation' not in piv_p50.index:
                    return None
                if (action not in piv_p50.index) or (slr_key not in piv_p50.columns):
                    return None
                # Reduction = baseline_PX − strategy_PX, computed at each
                # percentile rank. With real P25/P75 we can give a true
                # 5-number summary of the reduction; otherwise we fall
                # back to a 3-number summary at P05/P50/P95.
                if _have_quartiles:
                    return (
                        float(piv_p05.loc['No mitigation', slr_key]) - float(piv_p05.loc[action, slr_key]),
                        float(piv_p25.loc['No mitigation', slr_key]) - float(piv_p25.loc[action, slr_key]),
                        float(piv_p50.loc['No mitigation', slr_key]) - float(piv_p50.loc[action, slr_key]),
                        float(piv_p75.loc['No mitigation', slr_key]) - float(piv_p75.loc[action, slr_key]),
                        float(piv_p95.loc['No mitigation', slr_key]) - float(piv_p95.loc[action, slr_key]),
                    )
                return (
                    float(piv_p05.loc['No mitigation', slr_key]) - float(piv_p05.loc[action, slr_key]),
                    float(piv_p50.loc['No mitigation', slr_key]) - float(piv_p50.loc[action, slr_key]),
                    float(piv_p95.loc['No mitigation', slr_key]) - float(piv_p95.loc[action, slr_key]),
                )

            # Median-label vertical position: adjustable via the slider
            # rendered directly under these two panels. Stored as a percent
            # of the axis height; 0 (default) sits each median label right
            # at its median line. Read BEFORE building the figures so the
            # slider (placed under the plots) drives them on the same rerun.
            _med_shift_key = "agg_med_label_shift_pct"
            ss.setdefault(_med_shift_key, 0.0)
            _med_shift = float(ss[_med_shift_key]) / 100.0

            # Cumulative-damage panel (all strategies)
            sd_cum = {slr: [_agg_stats(a, slr) for a in actions_present_cs]
                      for slr, *_ in SCENARIO_SPECS}
            fig_agg_cum = build_box_whisker_panel(
                group_labels=[action_labels_cs[a] for a in actions_present_cs],
                scenario_data=sd_cum,
                panel_title="Cumulative Damage",
                y_label="Community-Total Cumulative Damage",
                median_label_shift=_med_shift,
            )

            # Reduction panel (no baseline)
            actions_no_base = [a for a in actions_present_cs if a != 'No mitigation']
            if actions_no_base:
                sd_red = {slr: [_agg_red_stats(a, slr) for a in actions_no_base]
                          for slr, *_ in SCENARIO_SPECS}
                fig_agg_red = build_box_whisker_panel(
                    group_labels=[action_labels_cs[a] for a in actions_no_base],
                    scenario_data=sd_red,
                    panel_title="Damage Reduction vs. No Mitigation",
                    y_label="Community-Total Damage Reduction",
                    bg_color='#f3f4f6',
                    median_label_shift=_med_shift,
                )
            else:
                fig_agg_red = None

            col_aggL, col_aggR = st.columns(2)
            with col_aggL:
                st.plotly_chart(fig_agg_cum, use_container_width=True)
            with col_aggR:
                if fig_agg_red is not None:
                    st.plotly_chart(fig_agg_red, use_container_width=True)
                else:
                    st.info("No retrofit strategies available for the reduction panel.")

            # Adjust where the median value labels sit relative to their
            # median line. 0% (default) = no shift (label on the line);
            # positive lifts labels above the line, negative pushes below.
            st.slider(
                "Median label vertical shift (% of axis height)",
                min_value=-5.0, max_value=10.0, step=0.5,
                key=_med_shift_key,
                help="Nudge the median value labels up or down relative to "
                     "the median line on the two panels above. Defaults to "
                     "0% (no shift from the median).",
            )

            st.caption(
                "Each box summarizes the distribution of the **community-total** cumulative "
                "damage across Monte Carlo realizations. Box edges show the 25th and 75th "
                "percentiles, the white center line is the median (P50), and whiskers extend "
                "to the 5th and 95th percentiles. All five percentiles are taken directly "
                "from the aggregated Monte Carlo results stored in the workbook - no "
                "interpolation between bounds is performed. The reduction panel is computed "
                "percentile-by-percentile against the same-scenario No-Mitigation baseline."
            )

        st.divider()

        st.subheader("Adaptation Strategy Comparison by DFE Status")

        col_under, col_above = st.columns(2)

        with col_under:
            st.markdown("### 🔴 Under DFE (Below BFE+2)")

            if 'InFP_CumEAD_P50' in df_current.columns:
                # Canonical action order: severity-of-intervention, with
                # Elevate placed BEFORE WFP 1st Floor (per workshop
                # convention; both the bar chart x-axis and the table
                # rows below follow it).
                dfe_action_order = ['No mitigation', 'Raise Utilities',
                                    'WFP B', 'Elevate', 'WFP 1st']
                df_current_sorted = df_current.copy()
                df_current_sorted['_ord'] = df_current_sorted['Action'].apply(
                    lambda a: dfe_action_order.index(a)
                              if a in dfe_action_order else 999
                )
                df_current_sorted = df_current_sorted.sort_values('_ord')

                under_dfe_data = []
                baseline_infp = df_current[df_current['Action'] == 'No mitigation']['InFP_CumEAD_P50'].values
                baseline_infp = baseline_infp[0] if len(baseline_infp) > 0 else 0

                for _, row in df_current_sorted.iterrows():
                    action = row['Action']
                    val = row['InFP_CumEAD_P50']
                    savings = baseline_infp - val
                    pct = (savings / baseline_infp * 100) if baseline_infp > 0 else 0
                    under_dfe_data.append({
                        'Action': action,
                        'Cumulative Damage ($)': val,
                        'Savings': savings,
                        'Reduction (%)': pct
                    })

                df_under = pd.DataFrame(under_dfe_data)

                fig_under = px.bar(df_under, x='Action', y='Cumulative Damage ($)', color='Action',
                    color_discrete_map={'No mitigation': '#ef4444', 'Raise Utilities': '#f97316',
                        'WFP B': '#eab308', 'Elevate': '#22c55e', 'WFP 1st': '#3b82f6'},
                    category_orders={'Action': dfe_action_order},
                    title="Under DFE - All Strategies")
                # Smart $k/$M/$B y-axis ticks
                _u_max = df_under['Cumulative Damage ($)'].max()
                u_ticks, u_labels = smart_money_ticks(_u_max, target_n=5)
                fig_under.update_layout(
                    showlegend=False, height=300,
                    yaxis_title="Cumulative Damage",
                )
                fig_under.update_yaxes(tickmode='array', tickvals=u_ticks, ticktext=u_labels)
                st.plotly_chart(fig_under, use_container_width=True)

                df_under_display = df_under.copy()
                df_under_display['Cumulative Damage ($)'] = df_under_display['Cumulative Damage ($)'].apply(format_currency)
                df_under_display['Savings'] = df_under_display['Savings'].apply(format_currency)
                df_under_display['Reduction (%)'] = df_under_display['Reduction (%)'].apply(lambda x: f"{x:.1f}%")
                st.dataframe(df_under_display, use_container_width=True, hide_index=True)
            else:
                st.info("No Under DFE data available.")

        with col_above:
            st.markdown("### 🟢 Above DFE (Above BFE+2)")

            if 'OutFP_CumEAD_P50' in df_current.columns:
                # Same canonical order; Elevate is omitted from the
                # Above-DFE chart (already at/above BFE+2 by definition).
                dfe_action_order_above = ['No mitigation', 'Raise Utilities',
                                          'WFP B', 'WFP 1st']
                df_current_sorted_a = df_current.copy()
                df_current_sorted_a['_ord'] = df_current_sorted_a['Action'].apply(
                    lambda a: dfe_action_order_above.index(a)
                              if a in dfe_action_order_above else 999
                )
                df_current_sorted_a = df_current_sorted_a.sort_values('_ord')

                above_dfe_data = []
                baseline_outfp = df_current[df_current['Action'] == 'No mitigation']['OutFP_CumEAD_P50'].values
                baseline_outfp = baseline_outfp[0] if len(baseline_outfp) > 0 else 0

                for _, row in df_current_sorted_a.iterrows():
                    action = row['Action']
                    if action == 'Elevate':
                        continue
                    val = row['OutFP_CumEAD_P50']
                    savings = baseline_outfp - val
                    pct = (savings / baseline_outfp * 100) if baseline_outfp > 0 else 0
                    above_dfe_data.append({
                        'Action': action,
                        'Cumulative Damage ($)': val,
                        'Savings': savings,
                        'Reduction (%)': pct
                    })

                df_above = pd.DataFrame(above_dfe_data)

                if not df_above.empty:
                    fig_above = px.bar(df_above, x='Action', y='Cumulative Damage ($)', color='Action',
                        color_discrete_map={'No mitigation': '#ef4444', 'Raise Utilities': '#f97316',
                            'WFP B': '#eab308', 'WFP 1st': '#3b82f6'},
                        category_orders={'Action': dfe_action_order_above},
                        title="Above DFE - Strategies (excl. Elevate)")
                    _a_max = df_above['Cumulative Damage ($)'].max()
                    a_ticks, a_labels = smart_money_ticks(_a_max, target_n=5)
                    fig_above.update_layout(
                        showlegend=False, height=300,
                        yaxis_title="Cumulative Damage",
                    )
                    fig_above.update_yaxes(tickmode='array', tickvals=a_ticks, ticktext=a_labels)
                    st.plotly_chart(fig_above, use_container_width=True)

                    df_above_display = df_above.copy()
                    df_above_display['Cumulative Damage ($)'] = df_above_display['Cumulative Damage ($)'].apply(format_currency)
                    df_above_display['Savings'] = df_above_display['Savings'].apply(format_currency)
                    df_above_display['Reduction (%)'] = df_above_display['Reduction (%)'].apply(lambda x: f"{x:.1f}%")
                    st.dataframe(df_above_display, use_container_width=True, hide_index=True)
            else:
                st.info("No Above DFE data available.")

        st.divider()

        st.subheader("Damage Trajectory Over Time")

        # Include every adaptation strategy that's present in the data
        # (matching the Trends tab). The previous version omitted WFP 1st
        # despite it being available in the workbook, which biased the
        # visual story by hiding the first-floor wet-floodproof curve.
        traj_action_order = ['No mitigation', 'Raise Utilities',
                             'WFP B', 'Elevate', 'WFP 1st']
        traj_action_labels = {
            'No mitigation':   'No Mitigation',
            'Raise Utilities': 'Raise Utilities',
            'WFP B':           'WFP Basement',
            'WFP 1st':         'WFP 1st Floor',
            'Elevate':         'Elevate',
        }
        traj_color_map = {
            'No Mitigation':   '#ef4444',   # red
            'Raise Utilities': '#f97316',   # orange
            'WFP Basement':    '#eab308',   # yellow
            'WFP 1st Floor':   '#3b82f6',   # blue
            'Elevate':         '#22c55e',   # green
        }

        df_timeline = _sum_agg[
            (_sum_agg['SLR'] == scenario) &
            (_sum_agg['Action'].isin(traj_action_order))
        ].copy()
        # Apply the consistent action labels so the legend reads cleanly
        df_timeline['Strategy'] = df_timeline['Action'].map(traj_action_labels)
        traj_present = [traj_action_labels[a] for a in traj_action_order
                        if a in df_timeline['Action'].unique()]

        if not df_timeline.empty:
            fig_line = px.line(
                df_timeline, x='TargetYear', y='Total_CumEAD_P50',
                color='Strategy',
                category_orders={'Strategy': traj_present},
                markers=True, color_discrete_map=traj_color_map,
                title=f"Cumulative Damage Projection - {occupancy_label} ({scenario} SLR Scenario)",
            )
            _l_max = df_timeline['Total_CumEAD_P50'].max()
            l_ticks, l_labels = smart_money_ticks(_l_max, target_n=6)
            fig_line.update_layout(yaxis_title="Cumulative Damage",
                                   xaxis_title="Year", height=400)
            fig_line.update_yaxes(tickmode='array', tickvals=l_ticks, ticktext=l_labels)
            # All x-axis ticks render as plain integer years.
            _xs = sorted(df_timeline['TargetYear'].unique())
            _x_labels = [str(int(y)) for y in _xs]
            # Constrain the x-range to the actual data span (with a small
            # symmetric pad so markers don't sit on the axes). Without
            # this, Plotly's auto-range extends back ~5 years from the
            # first point, suggesting we have data we don't.
            if _xs:
                _xpad = max(1.0, 0.04 * (max(_xs) - min(_xs)))
                _xrange = [min(_xs) - _xpad, max(_xs) + _xpad]
            else:
                _xrange = None
            fig_line.update_xaxes(tickmode='array', tickvals=_xs, ticktext=_x_labels,
                                  range=_xrange)
            st.plotly_chart(fig_line, use_container_width=True)

        # ----------------------------------------------------------------
        # Damage breakdown by building category (replaces DFE pie chart)
        # ----------------------------------------------------------------
        if df_buildings is not None:
            # Filter to current year + scenario + No mitigation baseline
            df_bd = df_buildings[
                (df_buildings['TargetYear'] == target_year) &
                (df_buildings['SLR'] == scenario) &
                (df_buildings['Action'] == 'No mitigation')
            ].copy()

            # Pick the best available grouping column, preferring occupancy
            group_col = None
            group_label = None
            if 'occupancy_type' in df_bd.columns and df_bd['occupancy_type'].notna().sum() > 0:
                group_col = 'occupancy_type'
                group_label = 'Occupancy Type'
            elif 'foundation_type' in df_bd.columns and df_bd['foundation_type'].notna().sum() > 0:
                group_col = 'foundation_type'
                group_label = 'Foundation Type'

            if group_col is not None and not df_bd.empty:
                st.subheader(f"Damage Breakdown by {group_label}")

                by_grp = (df_bd.groupby(group_col, dropna=False)
                          .agg(total_damage=('CumEAD_P50', 'sum'),
                               n_buildings=('id', 'nunique'),
                               avg_damage=('CumEAD_P50', 'mean'))
                          .reset_index())
                by_grp[group_col] = by_grp[group_col].fillna('Unknown').astype(str)
                by_grp = by_grp[by_grp['total_damage'] > 0].sort_values('total_damage', ascending=True)

                if not by_grp.empty:
                    total_damage_all = by_grp['total_damage'].sum()
                    by_grp['share'] = by_grp['total_damage'] / total_damage_all * 100

                    # Bar labels: compact $ + count + share
                    bar_text = [
                        f"{fmt_money_short(d)}  •  {int(n):,} bldgs  •  {s:.1f}%"
                        for d, n, s in zip(by_grp['total_damage'], by_grp['n_buildings'], by_grp['share'])
                    ]

                    _bd_max = by_grp['total_damage'].max()
                    bd_ticks, bd_labels = smart_money_ticks(_bd_max, target_n=5)

                    fig_bd = go.Figure()
                    fig_bd.add_trace(go.Bar(
                        x=by_grp['total_damage'],
                        y=by_grp[group_col],
                        orientation='h',
                        marker=dict(
                            color=by_grp['total_damage'],
                            colorscale=[
                                [0.0, '#fee2e2'],
                                [0.5, '#f87171'],
                                [1.0, '#b91c1c'],
                            ],
                            line=dict(color='white', width=1),
                        ),
                        text=bar_text,
                        textposition='outside',
                        cliponaxis=False,
                        hovertemplate=(
                            f"<b>%{{y}}</b><br>"
                            f"Total damage: %{{customdata[0]}}<br>"
                            f"Buildings: %{{customdata[1]:,}}<br>"
                            f"Avg per building: %{{customdata[2]}}<br>"
                            f"Share of total: %{{customdata[3]:.1f}}%"
                            "<extra></extra>"
                        ),
                        customdata=np.stack([
                            [fmt_money_short(v) for v in by_grp['total_damage']],
                            by_grp['n_buildings'].values,
                            [fmt_money_short(v) for v in by_grp['avg_damage']],
                            by_grp['share'].values,
                        ], axis=-1),
                    ))

                    # Headroom so the outside text labels don't get clipped
                    x_head = _bd_max * 1.30 if _bd_max > 0 else 1.0

                    fig_bd.update_layout(
                        title=dict(
                            text=(f"No-Mitigation Cumulative Damage by {group_label} - "
                                  f"{occupancy_label} ({target_year}, {scenario})"),
                            x=0.02, xanchor='left', font=dict(size=15),
                        ),
                        height=max(300, 55 * len(by_grp) + 120),
                        plot_bgcolor='white',
                        showlegend=False,
                        xaxis=dict(
                            title="Cumulative Damage",
                            showgrid=True, gridcolor='#e5e7eb',
                            showline=True, linecolor='#cbd5e1',
                            tickmode='array', tickvals=bd_ticks, ticktext=bd_labels,
                            range=[0, x_head],
                        ),
                        yaxis=dict(
                            title=group_label,
                            showgrid=False,
                            showline=True, linecolor='#cbd5e1',
                        ),
                        margin=dict(l=20, r=30, t=60, b=50),
                    )

                    st.plotly_chart(fig_bd, use_container_width=True)
                    st.caption(
                        f"Each bar shows the total No-Mitigation cumulative damage for buildings in that "
                        f"{group_label.lower()}, with the number of buildings and share of total damage. "
                        "This helps identify which building categories drive community-wide damage."
                    )
    else:
        st.warning("No data available for this location.")

    _sv_attrs = loc_entry.get('bldg_attrs') if loc_entry else None
    if (_sv_attrs is not None and len(_sv_attrs) > 0
            and 'structure_value' in _sv_attrs.columns
            and 'occupancy_type' in _sv_attrs.columns):
        st.divider()
        st.subheader("Structure-Value Distribution by Building Category")
        st.markdown(
            "<p style='color:#64748b;font-size:0.95rem;margin-top:-0.5rem;'>"
            "How many buildings of a chosen category sit in each structure-value "
            "band. This view spans the full inventory and is independent of the "
            "occupancy filter above.</p>",
            unsafe_allow_html=True,
        )

        _occ_counts = (_sv_attrs['occupancy_type'].dropna().astype(str)
                       .str.upper().str.strip().value_counts())
        _occ_options = list(_occ_counts.index)
        if not _occ_options:
            st.info("No occupancy information is available for this location.")
        else:
            _def_idx = _occ_options.index('RES2') if 'RES2' in _occ_options else 0
            _sv_occ = st.selectbox(
                "Building category",
                _occ_options, index=_def_idx, key="ov_sv_occ",
                format_func=lambda c: f"{c}  ·  {int(_occ_counts[c]):,} buildings",
            )

            _occ_norm = _sv_attrs['occupancy_type'].astype(str).str.upper().str.strip()
            _vals = pd.to_numeric(
                _sv_attrs.loc[_occ_norm == _sv_occ, 'structure_value'],
                errors='coerce').dropna()
            _vals = _vals[_vals > 0]

            if len(_vals) == 0:
                st.info(f"No positive structure values recorded for {_sv_occ}.")
            else:
                def _nice_width(span, target=8):
                    raw = max(span / target, 1.0)
                    mag = 10.0 ** np.floor(np.log10(raw))
                    for _m in (1, 2, 2.5, 5, 10):
                        if raw <= _m * mag:
                            return float(_m * mag)
                    return float(10 * mag)

                def _fmt_money_k(v):
                    if v >= 1e6:
                        return f"${v/1e6:.1f}M".replace('.0M', 'M')
                    return f"${v/1e3:.0f}k"

                def _hex_lerp(c1, c2, t):
                    a = tuple(int(c1[i:i+2], 16) for i in (0, 2, 4))
                    b = tuple(int(c2[i:i+2], 16) for i in (0, 2, 4))
                    return '#%02x%02x%02x' % tuple(
                        int(round(a[k] + (b[k] - a[k]) * t)) for k in range(3))

                vmin = float(_vals.min()); vmax = float(_vals.max())
                width = _nice_width((vmax - vmin) if vmax > vmin else vmax, target=8)
                lo = float(np.floor(vmin / width) * width)
                hi = float(np.ceil(vmax / width) * width)
                if hi <= lo:
                    hi = lo + width
                edges = np.arange(lo, hi + width * 0.5, width)
                counts, _ = np.histogram(_vals, bins=edges)
                labels = [f"{_fmt_money_k(edges[i])}–{_fmt_money_k(edges[i+1])}"
                          for i in range(len(edges) - 1)]

                # Light→deep indigo gradient by band: lower value = lighter,
                # higher value = deeper, so the value axis reads at a glance.
                _nb = max(len(labels) - 1, 1)
                bar_colors = [_hex_lerp('c7d2fe', '3730a3', i / _nb)
                              for i in range(len(labels))]
                _avg = float(_vals.mean())
                _ymax = max(int(counts.max()), 1)

                fig_sv = go.Figure()
                fig_sv.add_trace(go.Bar(
                    x=labels, y=counts,
                    marker=dict(color=bar_colors,
                                line=dict(color='rgba(15,23,42,0.12)', width=1)),
                    text=[str(int(c)) if c > 0 else '' for c in counts],
                    textposition='outside',
                    textfont=dict(size=12, color='#334155'),
                    hovertemplate='%{x}<br><b>%{y}</b> buildings<extra></extra>',
                    cliponaxis=False,
                ))
                fig_sv.update_layout(
                    title=dict(
                        text=(f"{_sv_occ}  ·  {len(_vals):,} buildings  ·  "
                              f"average {_fmt_money_k(_avg)}"),
                        x=0.01, xanchor='left',
                        font=dict(size=15, color='#0f172a')),
                    height=430, plot_bgcolor='white', paper_bgcolor='white',
                    margin=dict(l=64, r=24, t=58, b=84),
                    bargap=0.18, showlegend=False,
                    xaxis=dict(title='Structure value', tickangle=-30,
                               automargin=True, showgrid=False,
                               tickfont=dict(size=11, color='#475569'),
                               title_font=dict(size=12, color='#334155')),
                    yaxis=dict(title='Number of buildings', automargin=True,
                               showgrid=True, gridcolor='#eef2f7', gridwidth=1,
                               zeroline=False, rangemode='tozero',
                               range=[0, _ymax * 1.18],
                               title_font=dict(size=12, color='#334155')),
                )
                # theme=None so the white background + custom gridlines are
                # honored (Streamlit's default plotly theme overrides them).
                st.plotly_chart(fig_sv, use_container_width=True,
                                theme=None, key="ov_sv_chart")
                st.caption(
                    f"Inventory for **{location_name}** · category **{_sv_occ}** · "
                    f"n = {len(_vals):,} · average {_fmt_money_k(_avg)} · "
                    f"range {_fmt_money_k(vmin)}–{_fmt_money_k(vmax)} · "
                    f"band width {_fmt_money_k(width)}. Structure values are "
                    "replacement-cost estimates from the building inventory."
                )

    st.divider()
    st.subheader("📈 Trends - scenario comparison across horizons")
    st.markdown('<p class="tab-description">Compare cumulative damage projections between Median (50th-percentile) and High-End (90th-percentile) sea level rise scenarios across all time horizons.</p>', unsafe_allow_html=True)

    if df_agg is not None:
        st.subheader(f"📈 Scenario Comparison - {location_name} ({occupancy_label})")

        col1, col2 = st.columns(2)

        with col1:
            st.markdown("### Median SLR (P50)")
            df_p50 = df_agg[(df_agg['SLR'] == '50th-percentile') & (df_agg['Action'] == 'No mitigation')].sort_values('TargetYear')
            for _, row in df_p50.iterrows():
                st.metric(label=f"Year {int(row['TargetYear'])}", value=format_currency(row['Total_CumEAD_P50']))

        with col2:
            st.markdown("### High-End SLR (P90)")
            df_p90 = df_agg[(df_agg['SLR'] == '90th-percentile') & (df_agg['Action'] == 'No mitigation')].sort_values('TargetYear')
            for _, row in df_p90.iterrows():
                p50_val = df_p50[df_p50['TargetYear'] == row['TargetYear']]['Total_CumEAD_P50'].values
                delta = row['Total_CumEAD_P50'] - p50_val[0] if len(p50_val) > 0 else 0
                st.metric(label=f"Year {int(row['TargetYear'])}", value=format_currency(row['Total_CumEAD_P50']),
                    delta=f"+{format_currency(delta)} vs Median", delta_color="inverse")

        st.divider()

        df_comparison = df_agg[df_agg['Action'] == 'No mitigation'].copy()
        df_comparison['Label'] = df_comparison['SLR'].map({'50th-percentile': 'Median SLR', '90th-percentile': 'High-End SLR'})

        if not df_comparison.empty:
            fig_comp = px.line(df_comparison, x='TargetYear', y='Total_CumEAD_P50', color='Label',
                markers=True, color_discrete_map={'Median SLR': '#3b82f6', 'High-End SLR': '#ef4444'},
                title=f"No Mitigation Damage: Median vs High-End SLR Scenarios - {occupancy_label}")
            _c_max = df_comparison['Total_CumEAD_P50'].max()
            c_ticks, c_labels = smart_money_ticks(_c_max, target_n=6)
            fig_comp.update_layout(height=450, yaxis_title="Cumulative Damage", xaxis_title="Year")
            fig_comp.update_yaxes(tickmode='array', tickvals=c_ticks, ticktext=c_labels)
            # Constrain x-axis to actual data span (otherwise Plotly's
            # auto-range pads backward, suggesting data we don't have).
            _comp_xs = sorted(df_comparison['TargetYear'].unique())
            if _comp_xs:
                _comp_xpad = max(1.0, 0.04 * (max(_comp_xs) - min(_comp_xs)))
                _comp_xrange = [min(_comp_xs) - _comp_xpad,
                                max(_comp_xs) + _comp_xpad]
            else:
                _comp_xrange = None
            fig_comp.update_xaxes(
                tickmode='array',
                tickvals=_comp_xs,
                ticktext=[str(int(y)) for y in _comp_xs],
                range=_comp_xrange,
            )
            st.plotly_chart(fig_comp, use_container_width=True)

        # ================================================================
        # Per-SLR-scenario trajectories - all mitigation actions together
        # Separate plot per SLR scenario so each strategy's trend over time
        # can be compared directly to the No-Mitigation baseline.
        # ================================================================
        st.divider()
        st.subheader("📉 Mitigation Strategies Over Time - by SLR Scenario")
        st.caption(
            "Cumulative community damage trajectory for every adaptation strategy "
            "(including No Mitigation), plotted separately for each sea-level rise "
            "scenario. Use this to compare how each strategy bends the damage curve "
            "as the planning horizon extends."
        )

        # Stable action ordering + display labels, matching the other tabs
        trend_action_order = ['No mitigation', 'Raise Utilities', 'WFP B', 'Elevate', 'WFP 1st']
        trend_action_labels = {
            'No mitigation':   'No Mitigation',
            'Raise Utilities': 'Raise Utilities',
            'WFP B':           'WFP Basement',
            'WFP 1st':         'WFP 1st Floor',
            'Elevate':         'Elevate',
        }
        # Fixed per-action colors so the two SLR panels look consistent
        trend_action_colors = {
            'No mitigation':   '#ef4444',   # red - baseline
            'Raise Utilities': '#0ea5e9',   # sky blue
            'WFP B':           '#6366f1',   # indigo
            'WFP 1st':         '#8b5cf6',   # violet
            'Elevate':         '#10b981',   # emerald
        }

        trend_slr_panels = [
            ('50th-percentile', 'Median SLR (P50)'),
            ('90th-percentile', 'High-End SLR (P90)'),
        ]
        # Shared y-axis range so both panels are directly comparable
        _trend_max = df_agg['Total_CumEAD_P95'].max() if 'Total_CumEAD_P95' in df_agg.columns else df_agg['Total_CumEAD_P50'].max()
        trend_ticks, trend_tick_labels = smart_money_ticks(_trend_max, target_n=6)
        trend_y_range = [0, _trend_max * 1.08 if pd.notna(_trend_max) and _trend_max > 0 else 1]

        t_col1, t_col2 = st.columns(2)
        for col, (slr_key, slr_label) in zip((t_col1, t_col2), trend_slr_panels):
            df_slr = df_agg[df_agg['SLR'] == slr_key].copy()
            if df_slr.empty:
                with col:
                    st.info(f"No data for {slr_label}.")
                continue

            actions_present = [a for a in trend_action_order if a in df_slr['Action'].unique()]
            fig_trend = go.Figure()
            for action in actions_present:
                df_a = df_slr[df_slr['Action'] == action].sort_values('TargetYear')
                if df_a.empty:
                    continue
                clr = trend_action_colors.get(action, '#475569')
                fig_trend.add_trace(go.Scatter(
                    x=df_a['TargetYear'],
                    y=df_a['Total_CumEAD_P50'],
                    mode='lines+markers',
                    name=trend_action_labels.get(action, action),
                    line=dict(color=clr, width=3 if action == 'No mitigation' else 2.2,
                              dash='solid' if action == 'No mitigation' else 'dot'),
                    marker=dict(size=10 if action == 'No mitigation' else 8,
                                line=dict(color='white', width=1.2)),
                    hovertemplate=(
                        f"<b>{trend_action_labels.get(action, action)}</b><br>"
                        "Year %{x}<br>P50: %{y:$,.0f}<extra></extra>"
                    ),
                ))

            fig_trend.update_layout(
                title=dict(text=f"{slr_label} - {occupancy_label}",
                           x=0.02, xanchor='left', font=dict(size=14)),
                height=450,
                hovermode='x unified',
                plot_bgcolor='white',
                paper_bgcolor='white',
                margin=dict(l=60, r=20, t=60, b=50),
                legend=dict(orientation='h', yanchor='bottom', y=1.02,
                            xanchor='right', x=1, bgcolor='rgba(255,255,255,0.85)'),
            )
            _trend_xs = sorted(df_slr['TargetYear'].unique())
            # Plain integer-year ticks (no 'Potential' / 2025 anchor).
            _trend_x_labels = [str(int(y)) for y in _trend_xs]
            # Tight x-range with a small symmetric padding so the first
            # data marker doesn't sit on the y-axis. Without an explicit
            # range, Plotly auto-extends to ~2020 (or further), which
            # made it look like the data started in some baseline year
            # we never actually computed.
            if _trend_xs:
                _trend_xpad = max(1.0, 0.04 * (max(_trend_xs) - min(_trend_xs)))
                _trend_xrange = [min(_trend_xs) - _trend_xpad,
                                 max(_trend_xs) + _trend_xpad]
            else:
                _trend_xrange = None
            fig_trend.update_xaxes(
                title="Year", showgrid=True, gridcolor='#e5e7eb',
                showline=True, linecolor='#cbd5e1',
                tickmode='array', tickvals=_trend_xs, ticktext=_trend_x_labels,
                range=_trend_xrange,
            )
            fig_trend.update_yaxes(
                title="Cumulative Damage", showgrid=True, gridcolor='#e5e7eb',
                showline=True, linecolor='#cbd5e1', zeroline=False,
                tickmode='array', tickvals=trend_ticks, ticktext=trend_tick_labels,
                range=trend_y_range,
            )
            with col:
                st.plotly_chart(fig_trend, use_container_width=True)

        st.subheader("📋 Full Data Table")

        df_display = df_agg.copy()
        for col in ['Total_CumEAD_P05', 'Total_CumEAD_P50', 'Total_CumEAD_P95', 'InFP_CumEAD_P50', 'OutFP_CumEAD_P50']:
            if col in df_display.columns:
                df_display[col] = df_display[col].apply(lambda x: f"${x:,.0f}")

        rename_cols = {'InFP_CumEAD_P50': 'Under_DFE_P50', 'OutFP_CumEAD_P50': 'Above_DFE_P50'}
        df_display = df_display.rename(columns={k: v for k, v in rename_cols.items() if k in df_display.columns})

        st.dataframe(df_display, use_container_width=True, hide_index=True)
    else:
        st.warning("No data available for this location.")

