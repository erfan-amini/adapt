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
from adapt_core.helpers import (ZERO_THRESH_DISPLAY)


def render(ctx):
    _restrict_to_raise_only = ctx._restrict_to_raise_only
    df_buildings = ctx.df_buildings
    dfe_filter = ctx.dfe_filter
    location_name = ctx.location_name
    mobile_raise_only = ctx.mobile_raise_only
    occupancy_label = ctx.occupancy_label
    target_year = ctx.target_year
    from adapt_core import ui as _ui
    _ui.page_header(
        "Damage distributions",
        "How cumulative damage is distributed <b>across individual buildings</b>, and how many buildings each "
        "adaptation measure protects. Both sea-level rise scenarios are shown side by side for the selected "
        "target year.",
        module_key="planners", location=location_name,
    )
    ctx.render_inline_settings(["occ", "year", "scn"])
    if df_buildings is None or df_buildings.empty:
        st.warning("No per-building data available for this location.")
    else:
        df_b_year = df_buildings[df_buildings['TargetYear'] == target_year].copy()

        # Honor the sidebar DFE filter here too. Previously this tab
        # always ran on the full inventory regardless of the sidebar
        # selection, which made the per-building distribution panels
        # look insensitive to a control that visibly drives the Map.
        # The filter is in scope because the sidebar is rendered before
        # the tabs.
        if dfe_filter and 'DFE_Status' in df_b_year.columns:
            df_b_year = df_b_year[df_b_year['DFE_Status'].isin(dfe_filter)]

        # Mobile-homes-dominated area: keep only the raise-home and baseline
        # rows so the strategy distributions compare elevation vs no mitigation.
        if mobile_raise_only:
            df_b_year = _restrict_to_raise_only(df_b_year)
            st.caption(
                "🏠 **Mobile-homes-dominated area:** distributions and effectiveness counts "
                "consider only **raising (elevating) homes** vs the no-mitigation baseline."
            )

        if df_b_year.empty:
            st.warning(f"No per-building data for year {target_year}.")
        else:
            st.subheader(
                f"Damage Distribution Across Buildings - {location_name} "
                f"({occupancy_label}) - Year {target_year}"
            )

            # ----- Strategy ordering & labels -----
            action_order  = ['No mitigation', 'Raise Utilities', 'WFP B', 'Elevate', 'WFP 1st']
            action_labels_plain = {
                'No mitigation':   'No Mitigation',
                'Raise Utilities': 'Raise Utilities',
                'WFP B':           'WFP Basement',
                'WFP 1st':         'WFP 1st Floor',
                'Elevate':         'Elevate',
            }
            actions_present = [a for a in action_order if a in df_b_year['Action'].unique()]

            # When the user has restricted the view to ONLY Above-DFE
            # buildings (DFE_Status == 'Above DFE'), drop
            # Elevate from every Distributions chart on this tab. The
            # data generator treats Elevate as a no-op for those buildings
            # (Elevate damage = baseline damage by construction), so an
            # Elevate box would just mirror the No-Mitigation box and
            # mislead readers into thinking elevation does nothing.
            # The opposite case (only Under-DFE selected) keeps Elevate.
            only_above_dfe = (
                dfe_filter
                and len(dfe_filter) == 1
                and 'DFE_Status' in df_b_year.columns
                and {str(v).strip().lower() for v in dfe_filter} <= {
                    'out of floodplain', 'out_of_floodplain', 'above dfe'
                }
            )
            if only_above_dfe and 'Elevate' in actions_present:
                actions_present = [a for a in actions_present if a != 'Elevate']

            thr = ZERO_THRESH_DISPLAY

            # =============================================================
            # CROSS-BUILDING DISTRIBUTION PANELS
            # Two panels: left = distribution of per-building MEDIAN (P50)
            #             right = distribution of per-building UPPER-TAIL (P95)
            # Each box summarizes one statistic across damaged buildings.
            # Mirrors the workshop's make_perbldg_bw / pb_p50, pb_p90.
            # =============================================================

            # Filter to "damaged" buildings: those with No-Mitigation P50 > thr
            # under at least one SLR (so the same building-set is used for
            # all strategies, matching the workshop).
            d_nomit = df_b_year[df_b_year['Action'] == 'No mitigation']
            damaged_ids = set()
            n_total = 0
            for slr_key, _, _, _ in SCENARIO_SPECS:
                ds = d_nomit[d_nomit['SLR'] == slr_key]
                if ds.empty:
                    continue
                n_total = max(n_total, int(ds['id'].nunique()))
                damaged_ids.update(ds.loc[ds['CumEAD_P50'] > thr, 'id'].tolist())
            n_aff = len(damaged_ids)

            if n_aff == 0:
                st.info(
                    f"No buildings exceed the median-damage threshold (≥ ${thr:,}) "
                    f"under either SLR scenario for {target_year}. "
                    "Cross-building distribution panels are not shown."
                )
            else:
                st.markdown(
                    f"**{n_aff:,} of {n_total:,} buildings damaged** "
                    f"(median cumulative damage > ${thr:,} under at least one SLR scenario)"
                )

                # Pre-pivot building-level damages for fast access
                # Indexed by building id, columns = (Action, SLR) → P50 / P95
                pb = df_b_year[df_b_year['id'].isin(damaged_ids)]

                def _cross_bldg_stats(stat_col):
                    """Build scenario_data for the box-whisker helper, where
                    each 'group' is a strategy and the box summarizes the
                    distribution of `stat_col` across damaged buildings.

                    Returns 5-tuples ``(p05, p25, p50, p75, p95)`` per
                    group: whiskers reach to the 5th/95th percentiles of
                    damage across buildings, and box edges sit at the
                    25th/75th percentiles of the same distribution.
                    Computing P25 and P75 directly from the building
                    values means the box edges are real quartiles of the
                    cross-building distribution rather than CDF-linear
                    approximations between P05 and P95. The wider
                    5th/95th window (vs the earlier 10th/90th) surfaces
                    residual damage on Elevate that a tighter window
                    would clip out - many buildings only see non-zero
                    post-elevation damage in the upper tail of the
                    cross-building spread.
                    """
                    out = {slr_key: [] for slr_key, *_ in SCENARIO_SPECS}
                    for action in actions_present:
                        for slr_key, *_ in SCENARIO_SPECS:
                            vals = pb[(pb['Action'] == action) &
                                      (pb['SLR'] == slr_key)][stat_col].values
                            if len(vals) == 0:
                                out[slr_key].append(None)
                                continue
                            p05 = float(np.percentile(vals, 5))
                            p25 = float(np.percentile(vals, 25))
                            p50 = float(np.percentile(vals, 50))
                            p75 = float(np.percentile(vals, 75))
                            p95 = float(np.percentile(vals, 95))
                            # build_box_whisker_panel reads a 5-tuple as
                            # (lower-whisker, Q1, median, Q3, upper-whisker)
                            # - exactly the cross-building 5/25/50/75/95
                            # we just computed. No interpolation needed.
                            out[slr_key].append((p05, p25, p50, p75, p95))
                    return out

                sd_p50 = _cross_bldg_stats('CumEAD_P50')
                sd_p95 = _cross_bldg_stats('CumEAD_P95')

                fig_pb_left = build_box_whisker_panel(
                    group_labels=[action_labels_plain[a] for a in actions_present],
                    scenario_data=sd_p50,
                    panel_title=(
                        "Median per-building damage: distribution across "
                        f"{n_aff:,} damaged buildings"
                    ),
                    y_label="Per-Building Cumulative Damage",
                    lower_label="P05", upper_label="P95",
                    lower_pct=0.05, upper_pct=0.95,
                )
                fig_pb_right = build_box_whisker_panel(
                    group_labels=[action_labels_plain[a] for a in actions_present],
                    scenario_data=sd_p95,
                    panel_title=(
                        "Upper-tail (P95) per-building damage: distribution across "
                        f"{n_aff:,} damaged buildings"
                    ),
                    y_label="Per-Building Cumulative Damage",
                    lower_label="P05", upper_label="P95",
                    lower_pct=0.05, upper_pct=0.95,
                )

                col_l, col_r = st.columns(2)
                with col_l:
                    st.plotly_chart(fig_pb_left, use_container_width=True)
                with col_r:
                    st.plotly_chart(fig_pb_right, use_container_width=True)

                st.caption(
                    "Each box summarizes how a single damage statistic varies across the "
                    "damaged buildings in this community. The **left panel** shows the "
                    "distribution of each building's *median* cumulative damage; the "
                    "**right panel** shows the distribution of each building's *upper-tail "
                    "(P95)* cumulative damage. Box edges show the 25th and 75th percentiles "
                    "across buildings, the white center line is the median across buildings, "
                    "and whiskers extend to the 5th and 95th percentiles across buildings."
                )

            st.divider()

            # =============================================================
            # PLOTS 3, 4, 5 - Per-building damage classification
            # Ported from VVV_Visualization_for_workshop_MasticBeach.py
            # Uses per-building P90 as upper-tail proxy (matches the
            # workshop convention; P95 was tested earlier but the user
            # asked for P90 because it's less sensitive to the tail's
            # smallest realizations and reads more conservatively).
            # =============================================================
            st.subheader(f"Building Counts by Adaptation Effectiveness - Year {target_year}")

            # Compute per-scenario stats (analog of compute_bldg_stats)
            per_scen_stats = {}
            for slr_key, slr_label, line_clr, _fill in SCENARIO_SPECS:
                ds = df_b_year[df_b_year['SLR'] == slr_key]
                if ds.empty:
                    per_scen_stats[slr_key] = None
                    continue

                d_nomit_s  = ds[ds['Action'] == 'No mitigation'].set_index('id')
                d_wfpb_s   = ds[ds['Action'] == 'WFP B'].set_index('id')
                d_elev_s   = ds[ds['Action'] == 'Elevate'].set_index('id')
                # Raise Utilities only matters for the Pamunkey variant of
                # fig4 below - we still always compute it so the stats
                # dict has a stable schema across locations.
                d_raiseu_s = ds[ds['Action'] == 'Raise Utilities'].set_index('id')

                n_tot_s = int(d_nomit_s.index.nunique()) if not d_nomit_s.empty else 0
                if n_tot_s == 0:
                    per_scen_stats[slr_key] = None
                    continue

                ids_s = d_nomit_s.index
                no_p50 = d_nomit_s['CumEAD_P50'].reindex(ids_s).fillna(0).values
                no_p90 = d_nomit_s['CumEAD_P90'].reindex(ids_s).fillna(0).values
                wb_p90 = (d_wfpb_s['CumEAD_P90'].reindex(ids_s).fillna(np.nan).values
                          if not d_wfpb_s.empty else None)
                el_p90 = (d_elev_s['CumEAD_P90'].reindex(ids_s).fillna(np.nan).values
                          if not d_elev_s.empty else None)
                ru_p90 = (d_raiseu_s['CumEAD_P90'].reindex(ids_s).fillna(np.nan).values
                          if not d_raiseu_s.empty else None)

                any_damage = no_p90 > thr
                mask_p50   = no_p50 > thr
                mask_sev   = no_p90 > thr

                # --- WFP-Basement bucket: same threshold rule as Map ---
                # A damaged building counts here iff WFP B brings P90 ≤ $1k.
                # WFP B is the cheapest fix that "eliminates" damage, so the
                # threshold reading still makes sense for this bucket.
                mask_wfpb = np.zeros(n_tot_s, dtype=bool)
                if wb_p90 is not None:
                    wb_arr = np.where(np.isnan(wb_p90), no_p90, wb_p90)
                    mask_wfpb = any_damage & (wb_arr <= thr)

                # --- Raise-Utilities bucket: same threshold rule as WFP B ---
                # A damaged building counts here iff Raise Utilities brings
                # P90 ≤ $1k. Used by the Pamunkey variant of fig4 where
                # basement floodproofing isn't a relevant retrofit
                # (manufactured housing / RES2-dominant inventory).
                mask_raiseu = np.zeros(n_tot_s, dtype=bool)
                if ru_p90 is not None:
                    ru_arr = np.where(np.isnan(ru_p90), no_p90, ru_p90)
                    mask_raiseu = any_damage & (ru_arr <= thr)

                # --- Elevation bucket: dominance rule (matches the Map) ---
                # An "Elevation" building is one that is damaged at baseline,
                # WFP Basement is NOT enough on its own, AND Elevation
                # strictly outperforms WFP Basement on P90 - i.e., elevation
                # provides meaningful additional protection beyond WFP B.
                # This replaces the earlier strict ≤$1k rule, which was too
                # demanding under high MC realizations: most damaged
                # Under-DFE buildings can't get P90 below $1k with
                # elevation either, even though Elevate is substantially
                # better than WFP B for them (Mastic Beach 2055-50th: median
                # P90 ~$27k under Elevate vs ~$41k under WFP B). The
                # dominance rule also auto-handles the MATLAB convention
                # where Above-DFE buildings have Elevate = baseline (those
                # have Elevate ≥ WFP B by construction → they fall through
                # to "Residual" rather than getting credited as Elevation
                # successes).
                mask_elev_works = np.zeros(n_tot_s, dtype=bool)
                if mobile_raise_only:
                    # No cheap retrofit in scope: an Elevation success is a
                    # damaged building whose P90 drops to ≤ $1k when raised.
                    if el_p90 is not None:
                        el_arr = np.where(np.isnan(el_p90), no_p90, el_p90)
                        mask_elev_works = any_damage & (el_arr <= thr)
                elif el_p90 is not None and wb_p90 is not None:
                    el_arr = np.where(np.isnan(el_p90), no_p90, el_p90)
                    wb_arr = np.where(np.isnan(wb_p90), no_p90, wb_p90)
                    wfpb_to_thr = wb_arr <= thr
                    elev_dom    = el_arr < wb_arr
                    mask_elev_works = (any_damage
                                       & ~wfpb_to_thr
                                       & elev_dom)

                per_scen_stats[slr_key] = {
                    'label':     slr_label,
                    'color':     line_clr,
                    'n_tot':     n_tot_s,
                    'n_p50_dmg': int(mask_p50.sum()),
                    'n_sev_dmg': int(mask_sev.sum()),
                    'n_damaged': int(any_damage.sum()),
                    'n_wfpb':    int(mask_wfpb.sum()),
                    'n_raiseu':  int(mask_raiseu.sum()),
                    'n_elev':    int(mask_elev_works.sum()),
                }

            valid_stats = {k: v for k, v in per_scen_stats.items() if v is not None}
            if not valid_stats:
                st.warning("No per-building data available for the selected year.")
            else:
                n_tot_max = max(s['n_tot'] for s in valid_stats.values())

                def _make_paired_bar(title, value_fn, count_fn,
                                     x_left='Median damage > $0',
                                     x_right='P90 damage > $0',
                                     single_group=False):
                    fig = go.Figure()
                    if single_group:
                        cat_labels = [x_left]
                    else:
                        cat_labels = [x_left, x_right]
                    y_max = 0.0
                    for slr_key, slr_label, line_clr, _fill in SCENARIO_SPECS:
                        if slr_key not in valid_stats:
                            continue
                        stats = valid_stats[slr_key]
                        pcts = value_fn(stats); cnts = count_fn(stats)
                        fig.add_trace(go.Bar(
                            name=slr_label,
                            x=cat_labels,                # CATEGORICAL x
                            y=pcts,
                            offsetgroup=slr_key,         # side-by-side pair
                            alignmentgroup='slr_pair',
                            marker=dict(color=line_clr, opacity=0.88,
                                        line=dict(color='white', width=1.5)),
                            text=[f"<b>{p:.1f}%</b><br>({n:,})"
                                  for p, n in zip(pcts, cnts)],
                            textposition='outside',
                            textfont=dict(size=13, color=line_clr),
                            cliponaxis=False,
                            customdata=list(zip(cat_labels, cnts)),
                            hovertemplate=(
                                f"<b>{slr_label}</b><br>"
                                "%{customdata[0]}<br>"
                                "Share: %{y:.1f}%<br>"
                                "Buildings: %{customdata[1]:,}"
                                "<extra></extra>"
                            ),
                        ))
                        y_max = max(y_max, max(pcts) if len(pcts) > 0 else 0)
                    y_head = min(100.0, max(y_max + 18, 25))
                    fig.update_layout(
                        title=dict(text=title, x=0.02, xanchor='left',
                                   font=dict(size=15)),
                        height=420,
                        plot_bgcolor='white', paper_bgcolor='white',
                        showlegend=True,
                        legend=dict(orientation='h', yanchor='bottom', y=1.02,
                                    xanchor='right', x=1,
                                    bgcolor='rgba(255,255,255,0.85)'),
                        margin=dict(l=60, r=20, t=70, b=70),
                        barmode='group',
                        bargap=0.30,
                        bargroupgap=0.10,
                        xaxis=dict(
                            type='category',
                            categoryorder='array',
                            categoryarray=cat_labels,
                            showgrid=False, showline=True, linecolor='#cbd5e1',
                            zeroline=False,
                        ),
                        yaxis=dict(
                            title="Share of buildings (%)",
                            showgrid=True, gridcolor='#e5e7eb',
                            showline=True, linecolor='#cbd5e1', zeroline=False,
                            range=[0, y_head], ticksuffix='%',
                        ),
                    )
                    return fig

                # Plot 3
                def _v3(s): return [
                    100.0 * s['n_p50_dmg'] / s['n_tot'] if s['n_tot'] else 0,
                    100.0 * s['n_sev_dmg'] / s['n_tot'] if s['n_tot'] else 0,
                ]
                def _c3(s): return [s['n_p50_dmg'], s['n_sev_dmg']]
                fig3 = _make_paired_bar(
                    f"Buildings experiencing flood damage by {target_year} "
                    f"(of {n_tot_max:,} buildings)",
                    _v3, _c3,
                    x_left='Median damage > $0',
                    x_right='Upper-tail (P90) damage > $0',
                )
                st.plotly_chart(fig3, use_container_width=True)

                # Plots 4 & 5
                # For Pamunkey, basement floodproofing isn't a relevant
                # retrofit (the inventory is RES2-heavy / mostly without
                # basements), so we swap the "WFP Basement eliminates P90"
                # bucket for the analogous "Raise Utilities eliminates
                # P90" bucket. Everything downstream (caption, summary
                # table column) follows the same swap so the labels stay
                # internally consistent.
                _use_raiseu_for_fig4 = (location_name == "Pamunkey")
                if mobile_raise_only:
                    # Only raising homes is in scope: a single Elevation chart,
                    # using the threshold rule (raising brings P90 ≤ $1k).
                    def _v5(s):
                        nd = s['n_damaged']
                        return [100.0 * s['n_elev'] / nd if nd > 0 else 0]
                    def _c5(s): return [s['n_elev']]
                    fig5 = _make_paired_bar(
                        f"Damaged buildings where raising the home eliminates "
                        f"upper-tail damage by {target_year}",
                        _v5, _c5,
                        x_left='Elevate',
                        single_group=True,
                    )
                    col_p5, = st.columns(1)
                    with col_p5:
                        st.plotly_chart(fig5, use_container_width=True)
                else:
                    if _use_raiseu_for_fig4:
                        def _v4(s):
                            nd = s['n_damaged']
                            return [100.0 * s['n_raiseu'] / nd if nd > 0 else 0]
                        def _c4(s): return [s['n_raiseu']]
                        fig4 = _make_paired_bar(
                            f"Damaged buildings where Raise Utilities eliminates "
                            f"upper-tail damage by {target_year}",
                            _v4, _c4,
                            x_left='Raise Utilities',
                            single_group=True,
                        )
                    else:
                        def _v4(s):
                            nd = s['n_damaged']
                            return [100.0 * s['n_wfpb'] / nd if nd > 0 else 0]
                        def _c4(s): return [s['n_wfpb']]
                        fig4 = _make_paired_bar(
                            f"Damaged buildings where WFP Basement eliminates "
                            f"upper-tail damage by {target_year}",
                            _v4, _c4,
                            x_left='WFP Basement',
                            single_group=True,
                        )

                    def _v5(s):
                        nd = s['n_damaged']
                        return [100.0 * s['n_elev'] / nd if nd > 0 else 0]
                    def _c5(s): return [s['n_elev']]
                    fig5 = _make_paired_bar(
                        f"Damaged buildings where Elevation outperforms "
                        f"WFP Basement on P90 damage by {target_year}",
                        _v5, _c5,
                        x_left='Elevate',
                        single_group=True,
                    )

                    # When only Above-DFE buildings are selected, Elevate is
                    # a no-op in the data generator (Elevate damage = baseline
                    # by construction), so the "Elevation outperforms WFP B"
                    # chart would be 0 by definition and the corresponding
                    # column in the summary table would mislead. Hide both.
                    if only_above_dfe:
                        col_p4, = st.columns(1)
                        with col_p4:
                            st.plotly_chart(fig4, use_container_width=True)
                    else:
                        col_p4, col_p5 = st.columns(2)
                        with col_p4:
                            st.plotly_chart(fig4, use_container_width=True)
                        with col_p5:
                            st.plotly_chart(fig5, use_container_width=True)

                # Per-scenario summary table
                tbl_rows = []
                for slr_key in ['50th-percentile', '90th-percentile']:
                    if slr_key not in valid_stats:
                        continue
                    s = valid_stats[slr_key]
                    nd = s['n_damaged']
                    if mobile_raise_only:
                        # Only the raise-home column, threshold semantics.
                        row = {
                            'SLR Scenario':          s['label'],
                            'Buildings':             f"{s['n_tot']:,}",
                            'Damaged (P90 > $0)':    f"{s['n_sev_dmg']:,}  ({100*s['n_sev_dmg']/s['n_tot']:.1f}%)" if s['n_tot'] else "-",
                            'Damaged (median > $0)': f"{s['n_p50_dmg']:,}  ({100*s['n_p50_dmg']/s['n_tot']:.1f}%)" if s['n_tot'] else "-",
                            'Raising the home eliminates P90':
                                f"{s['n_elev']:,}  ({100*s['n_elev']/nd:.1f}%)" if nd > 0 else "-",
                        }
                        tbl_rows.append(row)
                        continue
                    # Match the table's eliminator column to the strategy
                    # used by fig4 so the figure and the table never tell
                    # different stories.
                    if _use_raiseu_for_fig4:
                        elim_col_label = 'Raise Utilities eliminates P90'
                        elim_count = s['n_raiseu']
                    else:
                        elim_col_label = 'WFP Basement eliminates P90'
                        elim_count = s['n_wfpb']
                    row = {
                        'SLR Scenario':              s['label'],
                        'Buildings':                 f"{s['n_tot']:,}",
                        'Damaged (P90 > $0)':        f"{s['n_sev_dmg']:,}  ({100*s['n_sev_dmg']/s['n_tot']:.1f}%)" if s['n_tot'] else "-",
                        'Damaged (median > $0)':     f"{s['n_p50_dmg']:,}  ({100*s['n_p50_dmg']/s['n_tot']:.1f}%)" if s['n_tot'] else "-",
                        elim_col_label:              f"{elim_count:,}  ({100*elim_count/nd:.1f}%)" if nd > 0 else "-",
                    }
                    if not only_above_dfe:
                        row['Elevation > WFP B (where WFP B fails)'] = (
                            f"{s['n_elev']:,}  ({100*s['n_elev']/nd:.1f}%)" if nd > 0 else "-"
                        )
                    tbl_rows.append(row)
                if tbl_rows:
                    st.markdown("**Per-scenario summary**")
                    st.dataframe(pd.DataFrame(tbl_rows),
                                 use_container_width=True, hide_index=True)

                if mobile_raise_only:
                    st.caption(
                        "🏠 Mobile-homes-dominated area: only raising (elevating) homes is "
                        "considered. Per-building counts use the **P90** of cumulative damage as "
                        "the upper-tail proxy. The **damaged-buildings chart** shows the share of "
                        "buildings with median and with P90 damage greater than zero. The "
                        "**Elevation chart** shows, among damaged buildings, the share for which "
                        "raising the home brings P90 damage to ≤ $1k."
                    )
                elif _use_raiseu_for_fig4:
                    st.caption(
                        "Per-building counts use the **P90** of the per-building cumulative damage as "
                        "the upper-tail proxy (matching the workshop visualization convention). "
                        "The **damaged-buildings chart** shows the share of buildings with median "
                        "damage greater than zero and the share with P90 damage greater than zero. "
                        "The **Raise Utilities chart** shows, among buildings that experience any "
                        "damage, the share for which raising utilities above BFE+2 ft brings P90 "
                        "damage to ≤ $1k. The **Elevation chart** shows, among damaged buildings "
                        "where WFP Basement is **not** sufficient on its own, the share for which "
                        "elevation strictly outperforms WFP Basement on P90 - i.e. elevation "
                        "provides meaningful additional protection beyond what basement "
                        "floodproofing achieves. This dominance rule matches the Map tab's "
                        "Adaptation Effectiveness classifier "
                        "(No Damage \u2192 Raise Utilities \u2192 Elevation \u2192 Residual)."
                    )
                else:
                    st.caption(
                        "Per-building counts use the **P90** of the per-building cumulative damage as "
                        "the upper-tail proxy (matching the workshop visualization convention). "
                        "The **damaged-buildings chart** shows the share of buildings with median "
                        "damage greater than zero and the share with P90 damage greater than zero. "
                        "The **WFP Basement chart** shows, among buildings that experience any "
                        "damage, the share for which wet-floodproofing the basement brings P90 "
                        "damage to ≤ $1k. The **Elevation chart** shows, among damaged buildings "
                        "where WFP Basement is **not** sufficient on its own, the share for which "
                        "elevation strictly outperforms WFP Basement on P90 - i.e. elevation "
                        "provides meaningful additional protection beyond what basement "
                        "floodproofing achieves. This dominance rule matches the Map tab's "
                        "Adaptation Effectiveness classifier "
                        "(No Damage \u2192 Raise Utilities \u2192 WFP Basement \u2192 Elevation \u2192 Residual)."
                    )


