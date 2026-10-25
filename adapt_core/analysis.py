"""Map data preparation and aggregation helpers (ported)."""
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

from adapt_core.helpers import (is_residential, retrofit_applies, _flag_true, _elev_eligible_flag,
                                 _is_one_story_bldg, convert_floodplain_status, nice_round_up)
from adapt_core.bundle import _backfill_dropped_actions

def compute_damage_bin_breaks(df_buildings, scenario,
                              p_breaks=(0.20, 0.40, 0.60, 0.80),
                              thr=1000.0):
    """Compute stable damage-bin breakpoints across ALL years for the given
    SLR scenario. The breaks come from the pooled distribution of nonzero
    No-Mitigation P90 damages across every year, so the same building gets
    the same color regardless of which year is selected.
    
    This guarantees that switching the year on the Damage Bins map
    redistributes buildings *across* the same bins, rather than redefining
    the bins themselves - which makes year-to-year comparisons meaningful.
    
    The upper-tail percentile used here is **P90**, matching the workshop
    convention and the Distributions tab's "Building Counts by Adaptation
    Effectiveness" classifier - so the same building gets the same
    Damage-Bins color, the same Adaptation-Effectiveness category, and the
    same Distributions-tab bucket.

    When `nice_round_up` snapping causes two or more adjacent quantile
    breaks to collapse to the same value (common at locations like Pamunkey
    where most damages are clustered in a small dollar range), we return
    FEWER unique breaks rather than extrapolating upward. The earlier
    "pad-upward" behavior invented top breakpoints beyond the actual data
    range (e.g., a $25k top break for a dataset that maxes out around $12k),
    which inflated the legend and pushed mid-range buildings into a
    "red" bin despite being entirely typical for the location. Accepting
    a smaller number of bins is honest about what the data supports and
    keeps the downstream palette/labels/digitize logic intact (they all
    work with any 1–4 unique breaks → 2–5 bins).
    """
    df_nm = df_buildings[
        (df_buildings['Action'] == 'No mitigation') &
        (df_buildings['SLR'] == scenario)
    ]
    if df_nm.empty:
        return None
    
    pooled = df_nm['CumEAD_P90'].values.astype(float)
    pooled = pooled[~np.isnan(pooled)]
    nonzero = pooled[pooled > thr]
    if len(nonzero) == 0:
        return None
    
    raw = np.quantile(nonzero, list(p_breaks))
    nice = [nice_round_up(v) for v in raw]
    seen, unique_nice = set(), []
    for v in nice:
        if v > 0 and v not in seen:
            seen.add(v); unique_nice.append(v)
    return sorted(unique_nice)


def prepare_map_data(df_buildings, target_year, scenario):
    """Prepare building data for map display.
    
    Pulls per-action P05/P50/P90/P95 columns from the long-format
    per-building damage frame and pivots them into one wide row per
    building with `{action}_P05`, `{action}_P50`, `{action}_P90`,
    `{action}_P95` columns. P90 is the upper-tail proxy used by the
    Damage Bins and Adaptation Effectiveness map views (the Distributions
    tab uses the same convention); P95 is kept for any view that still
    wants the deeper-tail bound.
    """
    df_filtered = df_buildings[
        (df_buildings['TargetYear'] == target_year) &
        (df_buildings['SLR'] == scenario)
    ].copy()
    
    if df_filtered.empty:
        return None
    
    pct_cols = ['CumEAD_P05', 'CumEAD_P50', 'CumEAD_P90', 'CumEAD_P95']
    pct_cols = [c for c in pct_cols if c in df_filtered.columns]
    
    attr_cols = [col for col in df_filtered.columns if col not in 
                 ['Action', *pct_cols, 'TargetYear', 'SLR']]
    
    df_base = df_filtered[df_filtered['Action'] == 'No mitigation'][attr_cols].copy()
    
    if df_base.empty:
        first_action = df_filtered['Action'].iloc[0]
        df_base = df_filtered[df_filtered['Action'] == first_action][attr_cols].copy()
    
    for action in df_filtered['Action'].unique():
        df_action = df_filtered[df_filtered['Action'] == action][['id'] + pct_cols].copy()
        df_action.columns = ['id'] + [
            f'{action}_{c.split("_")[1]}' for c in pct_cols
        ]
        df_base = df_base.merge(df_action, on='id', how='left')
    
    if 'DFE_Status' in df_base.columns:
        df_base['DFE_Status'] = df_base['DFE_Status'].apply(convert_floodplain_status)
    
    return df_base


@st.cache_data(show_spinner=False)
def compute_flood_occurrences(_mc_df, _ffe_by_id, sig):
    """Per-building flood-occurrence counts from the MC water-level ensemble.

    For each Monte-Carlo realization (one of the up-to-1,000 `MC_*` columns)
    we count, across every year from the first MC year through the selected
    horizon, how many years the simulated annual-maximum water level exceeds
    the building's first-floor elevation (FFE). That yields one occurrence
    count per MC column per building - up to 1,000 numbers - and we then
    report percentiles of that distribution (P10 / P25 / P50 / P75 / P90),
    mirroring the percentile reporting used throughout the tool.

    Parameters
    ----------
    _mc_df : DataFrame
        Raw MC sheet for one SLR scenario: a 'Year' column plus MC_0001..
        Underscore-prefixed so Streamlit does NOT hash it (it's large).
    _ffe_by_id : dict[int, float | None]
        First-floor elevation (ft NAVD88) per building id. Underscore-
        prefixed for the same reason.
    sig : tuple
        (location_name, scenario, horizon_year) - the ONLY hashed argument.
        It uniquely determines `_mc_df` and `_ffe_by_id`, so the cache key is
        correct even though the big inputs are skipped. Keep this computed
        over ALL location buildings (not the occupancy/DFE-filtered subset)
        so the cache isn't poisoned by a narrower selection.

    Returns
    -------
    DataFrame with columns id, occ_P10, occ_P25, occ_P50, occ_P75, occ_P90,
    occ_mean, n_years, horizon - or None when there's nothing to compute.
    """
    location_name, scenario, horizon_year = sig
    mc_cols = [c for c in _mc_df.columns if c.startswith('MC_')]
    sub = _mc_df[_mc_df['Year'] <= int(horizon_year)]
    if sub.empty or not mc_cols:
        return None
    M = sub[mc_cols].to_numpy(dtype=float)            # (n_years, n_mc)
    n_years, n_mc = M.shape

    ids = [int(i) for i, f in _ffe_by_id.items()
           if f is not None and np.isfinite(f)]
    if not ids:
        return None
    ffe = np.array([float(_ffe_by_id[i]) for i in ids], dtype=float)   # (N,)

    # A building with FFE f floods in (year, realization) iff WL > f. For a
    # fixed realization column, the occurrence count as a function of FFE is a
    # step function, so we sort the column's water levels once and resolve all
    # buildings with a single searchsorted:
    #     count(WL > f) = n_years - searchsorted(sorted_col, f, side='right')
    # 'right' makes the comparison strictly greater-than (water above the
    # first floor), matching the building-depth convention elsewhere.
    occ = np.empty((len(ids), n_mc), dtype=np.int32)
    for j in range(n_mc):
        col_sorted = np.sort(M[:, j])
        occ[:, j] = n_years - np.searchsorted(col_sorted, ffe, side='right')

    # Nearest-rank (integer) percentiles: the occurrence count is an integer
    # number of years per realization, so we report an actual realized count
    # ("in the median realization it floods N years") rather than a linearly
    # interpolated fraction. Version-robust across numpy's method/interpolation
    # keyword rename.
    def _pctile_int(arr, p):
        try:
            return np.percentile(arr, p, axis=1, method='nearest')
        except TypeError:
            return np.percentile(arr, p, axis=1, interpolation='nearest')

    out = pd.DataFrame({'id': ids})
    for p in (10, 25, 50, 75, 90):
        out[f'occ_P{p:02d}'] = _pctile_int(occ, p).astype(int)
    out['occ_mean'] = occ.mean(axis=1)
    out['n_years']  = int(n_years)
    out['horizon']  = int(horizon_year)
    return out


def aggregate_filtered_data(df_buildings, target_year, scenario):
    """Aggregate building-level data to compute community totals."""
    df_filtered = df_buildings[
        (df_buildings['TargetYear'] == target_year) &
        (df_buildings['SLR'] == scenario)
    ].copy()

    if df_filtered.empty:
        return None

    # Count buildings a retrofit does not apply to at their No-mitigation
    # damage, so the strategy totals stay comparable with the baseline.
    df_filtered = _backfill_dropped_actions(df_filtered)

    agg_data = []
    for action in df_filtered['Action'].unique():
        df_action = df_filtered[df_filtered['Action'] == action]
        
        row = {
            'TargetYear': target_year,
            'SLR': scenario,
            'Action': action,
            'Total_CumEAD_P05': df_action['CumEAD_P05'].sum(),
            'Total_CumEAD_P50': df_action['CumEAD_P50'].sum(),
            'Total_CumEAD_P95': df_action['CumEAD_P95'].sum(),
            'Num_Buildings': df_action['id'].nunique()
        }
        
        if 'DFE_Status' in df_action.columns:
            df_under = df_action[df_action['DFE_Status'] == 'Under DFE']
            df_above = df_action[df_action['DFE_Status'] == 'Above DFE']
            row['InFP_CumEAD_P50'] = df_under['CumEAD_P50'].sum()
            row['OutFP_CumEAD_P50'] = df_above['CumEAD_P50'].sum()
        
        agg_data.append(row)
    
    return pd.DataFrame(agg_data)


