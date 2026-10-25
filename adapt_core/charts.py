"""Shared Plotly construction helpers (ported)."""
import os
import re
import math
import glob
import numpy as np
import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from types import SimpleNamespace

from adapt_core.helpers import fmt_money_short, fmt_money_rounded, format_currency, nice_round_up, smart_money_ticks, ZERO_THRESH_DISPLAY

# ============================================================================
# SHARED PLOT CONSTRUCTION HELPERS
# ============================================================================

# Workshop palette (green = P50 SLR, orange = P90 SLR), shared by all tabs
CLR_P50_LINE = 'rgb(74, 124, 89)'      # workshop green
CLR_P90_LINE = 'rgb(212, 121, 25)'     # workshop orange
CLR_P50_FILL = 'rgba(74, 124, 89, 0.55)'
CLR_P90_FILL = 'rgba(212, 121, 25, 0.55)'

SCENARIO_SPECS = [
    ('50th-percentile', 'Median SLR (P50)',   CLR_P50_LINE, CLR_P50_FILL),
    ('90th-percentile', 'High-End SLR (P90)', CLR_P90_LINE, CLR_P90_FILL),
]

# CDF-linear interpolation factors for Q1 and Q3 from P05/P50/P95
# Q1 sits at CDF=0.25, between P05 and P50: (0.25-0.05)/(0.50-0.05)
# Q3 sits at CDF=0.75, between P50 and P95: (0.75-0.50)/(0.95-0.50)
Q1_FRAC = (0.25 - 0.05) / (0.50 - 0.05)
Q3_FRAC = (0.75 - 0.50) / (0.95 - 0.50)


def build_box_whisker_panel(group_labels, scenario_data, panel_title="",
                            y_label="Cumulative Damage", bg_color=None,
                            height=520, label_zero_thresh=None,
                            lower_label="P05", upper_label="P95",
                            lower_pct=0.05, upper_pct=0.95,
                            median_label_shift=0.0):
    """Construct a Plotly box-and-whisker panel with grouped pairs of boxes.

    Parameters
    ----------
    group_labels : list[str]
        X-axis category labels (one per group, e.g. one per strategy).
    scenario_data : dict[slr_key -> list[tuple or None]]
        Per-group statistics for each SLR scenario. Use ``None`` for
        missing groups. Each tuple is one of:
          * ``(p05, p50, p95)`` - 3-tuple. Q1/Q3 are estimated by
            linear-CDF interpolation between the supplied bounds.
          * ``(p05, p25, p50, p75, p95)`` - 5-tuple. Q1 and Q3 are taken
            **directly** from the supplied stored P25/P75 values, which is
            the preferred path when the workbook already contains them
            (the ALL format does). This eliminates the small bias that
            CDF-linear interpolation introduces for skewed damage tails.
        Both shapes can be mixed across groups; each group is interpreted
        on its own.
        Keys must match the first element of SCENARIO_SPECS tuples.
    panel_title : str
    y_label : str
    bg_color : str or None
        Optional plot background (e.g. light gray for the reduction panel).
    height : int
    label_zero_thresh : float or None
        Damages whose absolute value is below this threshold render as "$0"
        in value labels. Defaults to ZERO_THRESH_DISPLAY.

    Returns
    -------
    plotly.graph_objects.Figure
    """
    if label_zero_thresh is None:
        label_zero_thresh = ZERO_THRESH_DISPLAY

    # Linear-CDF interpolation factors used as the FALLBACK for callers that
    # only have (p05, p50, p95) available (e.g. the per-building benefit
    # tuples in the Details tab, where benefits are derived as differences
    # of percentiles rather than read from a column). These factors are
    # exact only for symmetric distributions; for skewed flood-damage
    # distributions they're a small approximation. Whenever the caller
    # supplies a 5-tuple with real stored P25/P75, those are used directly.
    _q1_frac = (0.25 - lower_pct) / max(1e-9, (0.50 - lower_pct))
    _q3_frac = (0.75 - 0.50)       / max(1e-9, (upper_pct - 0.50))
    _q1_frac = min(max(_q1_frac, 0.0), 1.0)
    _q3_frac = min(max(_q3_frac, 0.0), 1.0)

    fig = go.Figure()

    # Numeric x-axis: each group sits at integer x=0,1,2,... and each SLR
    # box within a group is offset by ±BOX_HALF_OFFSET from the group center.
    # Annotations use those same numeric x positions so labels land *exactly*
    # above their box, independent of rendered plot width.
    BOX_HALF_OFFSET = 0.20          # horizontal half-spacing of paired boxes
    BOX_WIDTH = 0.34                # visible width of each individual box

    annot_records = []   # (x_num, p50, p95, line_clr)
    y_min = 0.0
    y_max_whisker = 0.0

    for slr_key, slr_label, line_clr, fill_clr in SCENARIO_SPECS:
        if slr_key not in scenario_data:
            continue

        offset_sign = -1 if slr_key == '50th-percentile' else +1
        x_num, x_hover_labels, p05_arr, p50_arr, p95_arr, q1_arr, q3_arr = \
            [], [], [], [], [], [], []

        for gi, stats in enumerate(scenario_data[slr_key]):
            if stats is None:
                continue
            # Two accepted shapes - see the docstring above.
            if len(stats) == 5:
                p05, p25, p50, p75, p95 = stats
                q1, q3 = p25, p75
                if any(pd.isna(v) for v in (p05, p25, p50, p75, p95)):
                    continue
            elif len(stats) == 3:
                p05, p50, p95 = stats
                if any(pd.isna(v) for v in (p05, p50, p95)):
                    continue
                # Fallback: estimate Q1/Q3 by linear-CDF interpolation.
                q1 = p05 + _q1_frac * (p50 - p05)
                q3 = p50 + _q3_frac * (p95 - p50)
            else:
                # Unrecognized shape - skip rather than crash.
                continue

            x_center = gi + offset_sign * BOX_HALF_OFFSET
            x_num.append(x_center)
            x_hover_labels.append(group_labels[gi])
            p05_arr.append(p05); p50_arr.append(p50); p95_arr.append(p95)
            q1_arr.append(q1)
            q3_arr.append(q3)

            annot_records.append((x_center, p05, p50, p95, line_clr))

            if pd.notna(p95):
                y_max_whisker = max(y_max_whisker, float(p95))
            if pd.notna(p05):
                y_min = min(y_min, float(p05))

        if not x_num:
            continue

        fig.add_trace(go.Box(
            name=slr_label,
            x=x_num,                        # NUMERIC x - exact positions
            q1=q1_arr,
            median=p50_arr,
            q3=q3_arr,
            lowerfence=p05_arr,
            upperfence=p95_arr,
            fillcolor=fill_clr,
            line=dict(color=line_clr, width=1.6),
            marker_color=line_clr,
            whiskerwidth=0.55,
            width=BOX_WIDTH,
            customdata=x_hover_labels,
            hovertemplate=(
                f'<b>{slr_label}</b><br>'
                '%{customdata}<br>'
                f'{lower_label}: ' '%{lowerfence:$,.0f}<br>'
                'P50: %{median:$,.0f}<br>'
                f'{upper_label}: ' '%{upperfence:$,.0f}'
                '<extra></extra>'
            ),
        ))
        
        # Overlay an explicit "$0" marker for any box that collapses to zero
        # across all five summary stats. Plotly draws nothing visible when
        # q1=q3=median=lower=upper=0, which makes a true-zero strategy look
        # missing - but the message we want is "this strategy reduces damage
        # to zero," not "no data." The overlay is a small filled diamond
        # placed right at zero, in the same line color as the box, with a
        # hover that confirms the zero reading.
        zero_x, zero_labels = [], []
        for xc, p05_v, q1_v, p50_v, q3_v, p95_v, lab in zip(
            x_num, p05_arr, q1_arr, p50_arr, q3_arr, p95_arr, x_hover_labels
        ):
            if all(abs(v) < label_zero_thresh for v in (p05_v, q1_v, p50_v, q3_v, p95_v)):
                zero_x.append(xc)
                zero_labels.append(lab)
        if zero_x:
            fig.add_trace(go.Scatter(
                x=zero_x, y=[0.0] * len(zero_x),
                mode='markers',
                marker=dict(symbol='diamond', size=10,
                            color=line_clr,
                            line=dict(color='white', width=1.2)),
                customdata=zero_labels,
                hovertemplate=(
                    f'<b>{slr_label}</b><br>'
                    '%{customdata}<br>'
                    'All percentiles ≈ $0'
                    '<extra></extra>'
                ),
                showlegend=False,
            ))

    # Y-axis range with headroom for whisker labels
    y_span = max(abs(y_max_whisker), abs(y_min))
    if y_span <= 0:
        y_span = 1.0
    y_head = y_max_whisker * 1.22 if y_max_whisker > 0 else y_span * 0.2
    y_floor = y_min * 1.15 if y_min < 0 else 0
    label_gap = y_span * 0.025       # small vertical gap above each line/whisker
    # Median-label vertical offset. 0 (default) sits the label right at the
    # median line; positive lifts it above the line, negative pushes it below.
    # Expressed as a fraction of the y-axis span so it scales with the plot.
    med_label_gap = float(median_label_shift) * y_span

    # Annotations: every label sits directly above its reference line -
    #   * Median label sits at the median line by default (yanchor='bottom'),
    #     offset up/down by med_label_gap (driven by median_label_shift).
    #   * P95 label sits just above the upper whisker
    # The lower whisker (P05) is intentionally NOT labeled.
    # X position is the exact numeric center of each box, so labels never
    # drift away from their own box regardless of plot width.
    for x_center, p05_v, p50_v, p95_v, line_clr in annot_records:
        med_text = fmt_money_rounded(p50_v) if abs(p50_v) >= label_zero_thresh else "$0"
        # Median - bold colored text on a semi-transparent white pill,
        # positioned relative to the median line by median_label_shift.
        fig.add_annotation(
            x=x_center, y=p50_v + med_label_gap,
            text=f"<b>{med_text}</b>",
            showarrow=False,
            xanchor='center', yanchor='bottom',
            font=dict(size=12, color=line_clr),
            bgcolor='rgba(255,255,255,0.85)',
            borderpad=2, bordercolor='rgba(0,0,0,0)',
        )
        if abs(p95_v) >= label_zero_thresh:
            # P95 - bold colored text just above the upper whisker
            fig.add_annotation(
                x=x_center, y=p95_v + label_gap,
                text=f"<b>{fmt_money_rounded(p95_v)}</b>",
                showarrow=False,
                xanchor='center', yanchor='bottom',
                font=dict(size=12, color=line_clr),
            )

    tickvals, ticktext = smart_money_ticks(y_head, target_n=6)

    fig.update_layout(
        title=dict(text=panel_title, x=0.02, xanchor='left', font=dict(size=15)),
        height=height,
        plot_bgcolor=(bg_color if bg_color else 'white'),
        paper_bgcolor='white',
        showlegend=True,
        legend=dict(orientation='h', yanchor='bottom', y=1.02,
                    xanchor='right', x=1, bgcolor='rgba(255,255,255,0.85)'),
        margin=dict(l=60, r=20, t=70, b=80),
        boxmode='overlay',              # numeric x already places boxes correctly
        xaxis=dict(
            title="Adaptation Strategy",
            tickmode='array',
            tickvals=list(range(len(group_labels))),
            ticktext=group_labels,
            tickangle=-20,
            range=[-0.5, len(group_labels) - 0.5],
            showgrid=False, showline=True, linecolor='#cbd5e1',
            zeroline=False,
        ),
        yaxis=dict(
            title=y_label,
            showgrid=True, gridcolor='#e5e7eb',
            showline=True, linecolor='#cbd5e1', zeroline=False,
            tickmode='array', tickvals=tickvals, ticktext=ticktext,
            range=[y_floor, y_head],
        ),
    )
    return fig


