"""Per-location result bundle loader (ported from the legacy app.py).

A bundle lives in data/<Slug>/ (preferred) or flat next to app.py.
"""
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

from adapt_core.helpers import (parse_filename, convert_floodplain_status, is_residential,
                                 _ABOVE_DFE_STATUS_STRINGS, _BASEMENT_FOUNDATION_CODES)

# ----------------------------------------------------------------------------
# CSV-bundle loader (current ADAPT data format)
# ----------------------------------------------------------------------------
# The upstream pipeline ships a per-location bundle of seven files:
#
#   {LOCATION}_metadata.csv                            - key/value metadata
#   {LOCATION}_bldg_lookup.csv                         - analysis-ready bldg attrs
#   {LOCATION}_bldg_CumulativeDamage.csv               - bldg × (year, action, slr) × pcts
#   {LOCATION}_CumulativeDamage_categories.csv         - 4 leaf categories × ... × pcts
#   {LOCATION}_skipped_buildings.csv                   - provenance log (optional)
#   DDD___{LOCATION}___NSI.xlsx                        - full NSI descriptors
#   DDD___{LOCATION}_MC_annual_max_waterlevels_P50.csv - Year × MC_0001..MC_1000
#   DDD___{LOCATION}_MC_annual_max_waterlevels_P90.csv - same for high-end SLR
#
# Canonical schema (matches the current Pamunkey rerun and the format every
# location will ship in going forward):
#   * `{LOCATION}_bldg_lookup.csv` exposes the column `DFE_Status` with
#     values 'Above DFE' / 'Under DFE' directly. An extra `ManufRestriction`
#     flag may also be present for manufactured-housing-heavy inventories;
#     it is passed through but not currently used.
#   * `TargetYear` ships as integer-like strings ('2025', '2040', '2055',
#     '2100'). Categories rows use the same year column.
#   * `{LOCATION}_CumulativeDamage_categories.csv` uses the four leaves
#     RES_UnderDFE / RES_AboveDFE / NONRES_UnderDFE / NONRES_AboveDFE.
#
# Legacy compatibility shims (kept so older archived bundles still load):
#   * `Floodplain_Status` is accepted as an alias for `DFE_Status` and the
#     legacy values 'In floodplain' / 'Out of floodplain' are normalized to
#     'Under DFE' / 'Above DFE' via convert_floodplain_status.
#   * `TargetYear == 'Potential'` is mapped to 2025 via
#     _bundle_normalize_target_year.
#   * The categories file is loaded but only used for an optional sanity
#     check, so the legacy RES_InFP / RES_OutFP naming doesn't matter at
#     runtime - community aggregates are now built from the per-building
#     table (sum-of-percentiles), eliminating the ~5% reconciliation gap
#     the old format had between sum-of-medians and median-of-sum.

# Percentile manifest the bundle ships (23 columns: dense at both tails
# plus quartiles).
BUNDLE_PCT_LIST = ['P01','P02','P03','P04','P05','P06','P07','P08','P09','P10',
                   'P25','P50','P75',
                   'P90','P91','P92','P93','P94','P95','P96','P97','P98','P99']

# Subset kept on per-building damage rows. Mirrors what the existing tabs
# read (P05/P10/P25/P50/P75/P90/P95) plus deeper tails (P01/P99) for any
# future tail-risk panel.
PER_BLDG_PCT_KEEP = ['P01','P05','P10','P25','P50','P75','P90','P95','P99']


def _bundle_read_metadata(path):
    """Parse `{LOC}_metadata.csv` (Key,Value pairs) into a dict, splitting
    list-valued fields (TARGET_YEARS, ACTION_NAMES, etc.) into Python lists."""
    raw = pd.read_csv(path)
    out = {}
    for _, row in raw.iterrows():
        k = str(row['Key']).strip()
        v = str(row['Value']).strip().strip('"')
        out[k] = v
    for list_key in ('TARGET_YEARS', 'TARGET_YEAR_LABELS', 'ACTIONS',
                     'ACTION_NAMES', 'SCENARIOS', 'SCENARIO_LABELS',
                     'PERCENTILES'):
        if list_key in out:
            out[list_key] = [s.strip() for s in out[list_key].split(',')]
    return out


def _bundle_normalize_target_year(series):
    """'Potential'→2025 + cast to int. Bundle ships TargetYear as mixed
    strings; the rest of the app uses numeric comparisons."""
    return (series.astype(str)
                  .replace({'Potential': '2025'})
                  .astype(int))


def _bundle_build_attrs_table(lookup_df, nsi_df):
    """Combine bldg_lookup (analysis-ready) with NSI (inventory source of truth).

    The NSI file is the maintained building inventory, so it is authoritative
    for every attribute it carries - structure/content value, occupancy,
    ground elevation, lon/lat, and the descriptive fields (building_type,
    number_of_stories, area, foundation_type, foundation_height, year_built,
    address). For those, the NSI value is used where present and the lookup
    fills only the gaps. Fields the NSI file does NOT carry but that drove the
    damage calculation - FFE_ft, DFE_Status, SOID, occupancy_group - always
    come from the lookup.
    """
    # 1. Map the analysis lookup to the app's lowercase schema. FFE_ft,
    #    DFE_Status, and SOID keep their original case because the UI looks
    #    them up by those exact names.
    rename = {
        'BuildingID':         'id',
        'OccupancyType':      'occupancy_type',
        'OccupancyGroup':     'occupancy_group',
        'StructureValue':     'structure_value',
        'ContentValue':       'content_value',
        'GroundElevation_ft': 'ground_elevation',
        'Longitude':          'longitude',
        'Latitude':           'latitude',
        # Legacy bundles ship 'Floodplain_Status' ('In/Out of floodplain');
        # canonical bundles ship 'DFE_Status' ('Above/Under DFE'). Accept the
        # legacy name and normalize values via convert_floodplain_status below.
        'Floodplain_Status':  'DFE_Status',
    }
    out = lookup_df.rename(columns=rename).copy()

    # 2. NSI already uses the lowercase schema; align it by id and let it win.
    nsi = nsi_df.rename(columns={'ID': 'id'}).copy()
    if 'id' in nsi.columns and 'id' in out.columns:
        out = out.set_index('id')
        nsi = nsi.set_index('id')
        nsi = nsi[~nsi.index.duplicated(keep='first')]
        # 3. Overlay every NSI-supplied column: the NSI value wins where it is
        #    present (non-null), the lookup fills the gaps, and NSI-only
        #    columns (building_type, stories, area, …) are added outright.
        for col in nsi.columns:
            nsi_col = nsi[col].reindex(out.index)
            if col in out.columns:
                out[col] = nsi_col.combine_first(out[col])
            else:
                out[col] = nsi_col
        out = out.reset_index()

    if 'DFE_Status' in out.columns:
        out['DFE_Status'] = out['DFE_Status'].apply(convert_floodplain_status)
    return out


def _bundle_wl_percentiles_from_mc(wl_mc):
    """Compute (Year × Pxx) percentile DataFrame from a raw MC sheet.

    The bundle ships the underlying 1,000 MC realizations (Year × MC_0001..
    MC_1000) per SLR scenario instead of pre-baked percentiles. We expose
    a percentile DataFrame for any consumer that wants P05/P50/P95-style
    bands; the raw MC matrix is also kept on the data_store for any new
    threshold-exceedance / per-building exposure feature.
    """
    mc_cols = [c for c in wl_mc.columns if c.startswith('MC_')]
    arr = wl_mc[mc_cols].to_numpy(dtype=float)
    out = pd.DataFrame({'Year': wl_mc['Year'].astype(int).values})
    for p in [1,2,3,4,5,6,7,8,9,10,25,50,75,90,91,92,93,94,95,96,97,98,99]:
        out[f'P{p:02d}'] = np.percentile(arr, p, axis=1)
    return out



def _backfill_dropped_actions(df):
    """Community totals: count every building under every strategy.

    load_bundle's applicability filter removes (building, action) rows for
    retrofits that do not apply to a building (WFP Basement without a
    basement, Elevate above the DFE, RES2 retrofits). That is right for the
    per-building views, but a community total built from the remaining rows
    leaves those buildings' damage out of the strategy total while it stays
    in the No-mitigation total, so baseline minus strategy shows their whole
    damage as a fake benefit. Here, for every strategy still offered
    somewhere in `df`, each building with no row for it is added back at its
    No-mitigation damage (the retrofit does nothing for it). A strategy
    dropped for every building (e.g. WFP Basement in a location with almost
    no basements) is not added back, so it still vanishes. Use this only on
    the copy that feeds community totals, never on the per-building frame.
    """
    if df is None or df.empty or 'Action' not in df.columns:
        return df
    key = [c for c in ('id', 'TargetYear', 'SLR') if c in df.columns]
    base = df[df['Action'] == 'No mitigation']
    if base.empty or 'id' not in key:
        return df
    pieces = [df]
    for action in df['Action'].unique():
        if action == 'No mitigation':
            continue
        have = df.loc[df['Action'] == action, key].drop_duplicates()
        missing = base.merge(have, on=key, how='left', indicator=True)
        missing = missing[missing['_merge'] == 'left_only'].drop(columns='_merge')
        if not missing.empty:
            missing = missing.copy()
            missing['Action'] = action
            pieces.append(missing[df.columns])
    if len(pieces) == 1:
        return df
    return pd.concat(pieces, ignore_index=True)


@st.cache_data(show_spinner=False)
def load_bundle(data_folder, location_slug, file_sig=None):
    """Load a single-location CSV bundle and return the data_store entry.

    `file_sig` is a fingerprint (per-file mtime+size) of the bundle's files.
    It is unused in the body but participates in the cache key, so editing
    any bundle file (e.g. the NSI xlsx) invalidates the cache and the bundle
    is re-read on the next run - no manual "Clear cache" needed.
    """
    join = lambda *p: os.path.join(data_folder, *p)

    # 1. Metadata
    metadata = _bundle_read_metadata(join(f'{location_slug}_metadata.csv'))
    bfe_ft = float(metadata.get('BFE_FT_NAVD88', 9))

    # Map TargetYear int → display label (2025 → 'Potential', etc.)
    target_year_labels = {}
    if ('TARGET_YEARS' in metadata
            and 'TARGET_YEAR_LABELS' in metadata
            and len(metadata['TARGET_YEARS']) == len(metadata['TARGET_YEAR_LABELS'])):
        for ys, lab in zip(metadata['TARGET_YEARS'], metadata['TARGET_YEAR_LABELS']):
            try:
                target_year_labels[int(ys)] = lab
            except ValueError:
                continue

    # 2. NSI (271 rows incl. the building that gets skipped - joined later)
    nsi = pd.read_excel(join(f'DDD___{location_slug}___NSI.xlsx'))

    # 3. Building lookup (270 rows - only buildings that were actually analyzed)
    lookup = pd.read_csv(join(f'{location_slug}_bldg_lookup.csv'))
    bldg_attrs = _bundle_build_attrs_table(lookup, nsi)

    # 4. Skipped-buildings log (small, possibly empty)
    try:
        skipped = pd.read_csv(join(f'{location_slug}_skipped_buildings.csv'))
    except (FileNotFoundError, pd.errors.EmptyDataError):
        skipped = pd.DataFrame()

    # 5. Per-building cumulative damage
    bldg_dmg = pd.read_csv(join(f'{location_slug}_bldg_CumulativeDamage.csv'))
    bldg_dmg = bldg_dmg.rename(columns={'BuildingID': 'id'})
    bldg_dmg['TargetYear'] = _bundle_normalize_target_year(bldg_dmg['TargetYear'])
    pct_present = [p for p in BUNDLE_PCT_LIST if p in bldg_dmg.columns]
    pct_keep = [p for p in PER_BLDG_PCT_KEEP if p in pct_present]
    bldg_dmg = bldg_dmg.drop(columns=[p for p in pct_present if p not in pct_keep])
    bldg_dmg = bldg_dmg.rename(columns={p: f'CumEAD_{p}' for p in pct_keep})

    # ---- Defensive correction for MATLAB Elevate no-op artifact ----
    # The MATLAB damage generator (AAA___Main_v9.m) skips the elevation math
    # for buildings tagged `DFE_Status == "Above DFE"` and just
    # sets `Dmg(:,:,4,si) = dmg_baseline` - i.e. Elevate equals No mitigation
    # exactly, by construction. The DFE_Status flag is itself derived
    # from the building's first-floor elevation (FFE) being above the design
    # flood elevation (DFE = BFE + 2 ft), but FFE = ground + foundation height,
    # so a building can sit on low ground (yard/garage flood frequently) and
    # still be tagged Above-DFE because its raised foundation lifts
    # the FFE above the DFE. The combined effect: a non-trivial number of
    # Above-DFE buildings have substantial baseline damage AND a no-op'd Elevate
    # column, which makes them look like "Residual: even elevation can't help"
    # on the Adaptation Effectiveness map - when in reality elevation simply
    # wasn't computed for them. We detect the no-op (Elevate exactly equals
    # No-mitigation across every percentile column for a given (building,
    # year, SLR)) and replace the affected Elevate row's percentile values
    # with NaN. Downstream code already handles NaN as "retrofit not
    # available" rather than "elevation is fully ineffective", so the map
    # and the Distributions plots stop labeling these buildings Residual on
    # spurious grounds.
    pct_cols_in_bldg = [c for c in bldg_dmg.columns if c.startswith('CumEAD_P')]
    if 'Action' in bldg_dmg.columns and pct_cols_in_bldg:
        # Build a No-mitigation lookup by (id, TargetYear, SLR)
        nm_lookup = (bldg_dmg[bldg_dmg['Action'] == 'No mitigation']
                     [['id', 'TargetYear', 'SLR'] + pct_cols_in_bldg]
                     .rename(columns={c: f'_NM_{c}' for c in pct_cols_in_bldg}))
        bldg_dmg = bldg_dmg.merge(nm_lookup, on=['id', 'TargetYear', 'SLR'],
                                  how='left')
        # Compare Elevate row by row against its No-Mit twin. We use exact
        # equality across ALL percentile columns simultaneously - anything
        # else is an actual elevation calculation, even if it happens to be
        # numerically close to baseline. exact-match catches the no-op.
        is_elev = bldg_dmg['Action'] == 'Elevate'
        is_noop = is_elev.copy()
        for c in pct_cols_in_bldg:
            nm_col = f'_NM_{c}'
            # Treat both NaN as no-evidence-of-difference (don't trip noop on missing)
            same = (bldg_dmg[c] == bldg_dmg[nm_col]) | \
                   (bldg_dmg[c].isna() & bldg_dmg[nm_col].isna())
            is_noop &= same
        # Also require at least one column to be non-zero - a real
        # all-zeros Elevate row (no damage to begin with) shouldn't be
        # flagged as no-op.
        any_nonzero = pd.Series(False, index=bldg_dmg.index)
        for c in pct_cols_in_bldg:
            any_nonzero |= bldg_dmg[c].fillna(0) > 0
        is_noop &= any_nonzero
        n_noop = int(is_noop.sum())
        if n_noop > 0:
            # Replace the percentile values on no-op'd Elevate rows with NaN
            for c in pct_cols_in_bldg:
                bldg_dmg.loc[is_noop, c] = np.nan
            # Stash the count on the entry so we can surface it in the UI
            _matlab_noop_count = n_noop
        else:
            _matlab_noop_count = 0
        # Drop the temporary lookup columns
        bldg_dmg = bldg_dmg.drop(columns=[f'_NM_{c}' for c in pct_cols_in_bldg])
    else:
        _matlab_noop_count = 0

    # Merge attrs onto every (id, year, action, slr) row so each row carries
    # the descriptive context the Map / Details / Distributions tabs need.
    df_buildings = bldg_dmg.merge(bldg_attrs, on='id', how='left')

    # ------------------------------------------------------------------
    # Drop (building, action) rows for retrofits that don't physically
    # apply to the building.
    # ------------------------------------------------------------------
    # The upstream damage generator runs the math for every retrofit on
    # every building, regardless of whether the retrofit makes physical
    # sense for that building (e.g., it computes WFP Basement damage for
    # buildings with no basement, and Elevate damage for buildings
    # already above DFE). Those numbers are then displayed as if they
    # were real adaptation options, producing misleading entries like
    # "WFP Basement: $0 (-100%)" on a manufactured home sitting on piers.
    #
    # We fix this at the data layer: any (building, action) row where
    # the action doesn't physically apply is simply removed from
    # df_buildings. That makes the action vanish from the hover, the
    # map classification, the dropdowns, the box plots, the
    # trajectories, and every aggregate downstream - one filter, one
    # consistent story across the whole app. The wide-form pivot in
    # prepare_map_data() will produce NaN for the dropped (building,
    # action) cells, and the hover loop below skips NaN cells so
    # they don't render as a misleading $0.
    #
    # Community totals are the exception: they are built from a copy where
    # each dropped (building, action) row is added back at the building's
    # No-mitigation damage (_backfill_dropped_actions), so a retrofit's
    # community benefit is never inflated by the buildings it skips.
    #
    # Vectorized so this stays cheap on multi-thousand-building inventories.
    if 'foundation_type' in df_buildings.columns:
        _foundation_norm = (
            df_buildings['foundation_type']
            .fillna('').astype(str).str.strip().str.upper()
        )
        _is_basement = _foundation_norm.isin(_BASEMENT_FOUNDATION_CODES)
        # Treat truly-missing foundation_type as "not basement" so we
        # don't surface WFP B for buildings we can't verify have one.
        _is_basement &= df_buildings['foundation_type'].notna()
    else:
        # No foundation column at all → no building can be confirmed as
        # having a basement, so WFP B applies nowhere.
        _is_basement = pd.Series(False, index=df_buildings.index)

    if 'DFE_Status' in df_buildings.columns:
        _dfe_norm = (
            df_buildings['DFE_Status']
            .fillna('').astype(str).str.strip().str.lower()
        )
        _is_above_dfe = _dfe_norm.isin(_ABOVE_DFE_STATUS_STRINGS)
    else:
        _is_above_dfe = pd.Series(False, index=df_buildings.index)

    _action = df_buildings['Action']
    _keep = pd.Series(True, index=df_buildings.index)
    _keep &= ~((_action == 'WFP B')   & ~_is_basement)
    _keep &= ~((_action == 'Elevate') &  _is_above_dfe)

    # Manufactured housing (RES2) sits on piers: no basement, no conditioned
    # upper floor to relocate utilities to, and no separable 1st-floor
    # envelope to dry/wet-floodproof. The only physically meaningful retrofit
    # is raising (elevating) the whole home, so drop Raise Utilities, WFP
    # Basement, and WFP 1st Floor for RES2 buildings. This makes those actions
    # vanish from the hover, the map (including the Adaptation-Effectiveness
    # yellow "cheap retrofit" bucket), the distributions, and every aggregate
    # - a RES2 home can only ever be classified under Elevate.
    if 'occupancy_type' in df_buildings.columns:
        _is_res2 = (df_buildings['occupancy_type']
                    .fillna('').astype(str).str.upper().str.startswith('RES2'))
        _keep &= ~(_is_res2 & _action.isin(['Raise Utilities', 'WFP B', 'WFP 1st']))

    # Manufactured-housing-dominant inventory (e.g., Pamunkey): if basements
    # are essentially absent, drop Wet-Floodproof-Basement for the whole
    # location so it never surfaces as an option anywhere.
    _n_bldg_total = df_buildings['id'].nunique()
    _n_basement_bldg = df_buildings.loc[_is_basement, 'id'].nunique()
    _basement_share = (_n_basement_bldg / _n_bldg_total) if _n_bldg_total else 0.0
    if _basement_share < 0.10:
        _keep &= ~(_action == 'WFP B')

    _n_dropped_wfpb = int(((_action == 'WFP B')   & ~_is_basement).sum())
    _n_dropped_elev = int(((_action == 'Elevate') &  _is_above_dfe).sum())
    df_buildings = df_buildings[_keep].copy()
    # Stash counts on the loader so the UI can report them if useful.
    # (Not currently surfaced, but cheap to keep available.)
    _applicability_drop_counts = {
        'WFP B (no basement)':   _n_dropped_wfpb,
        'Elevate (above DFE)':   _n_dropped_elev,
    }

    # 6. Aggregate community-total damage tables per occupancy filter.
    #
    # Earlier versions of this loader sourced the community totals from the
    # `{LOC}_CumulativeDamage_categories.csv` rollup file. That file is
    # supposed to give MC-correct community percentiles (computed from sums
    # of MC realizations across buildings, not from summing per-building
    # percentiles), but real data sometimes ships with the No-Mitigation
    # rows zeroed out - which makes the Summary metric read $0 while the
    # retrofit rows still hold real values, and the "Damage Reduction"
    # chart then plots NEGATIVE reductions because it computes
    # `baseline − retrofit = 0 − $235M`. To keep the Summary, the box-
    # plot, and the Map/Details visualizations all consistent, we now
    # build the aggregate **directly from the per-building damage table**
    # (the same source the per-building visuals read from). The
    # statistical concession is that we use sum-of-percentiles rather
    # than percentile-of-sum for the tails (P05/P95), which slightly
    # overstates community tails - but consistency is far more important
    # for decision support than that small bias, and the median (P50) is
    # essentially unaffected.
    is_res = bldg_attrs['occupancy_type'].apply(is_residential)
    bldg_counts = {
        'All':             int(len(bldg_attrs)),
        'Residential':     int(is_res.sum()),
        'Non-Residential': int((~is_res).sum()),
    }
    res_ids = set(bldg_attrs.loc[is_res, 'id'].astype(int).tolist())
    nonres_ids = set(bldg_attrs.loc[~is_res, 'id'].astype(int).tolist())
    occ_id_map = {
        'All':             None,        # no filter
        'Residential':     res_ids,
        'Non-Residential': nonres_ids,
    }

    grp_cols = ['TargetYear', 'Action', 'SLR']
    pct_cols_in_bldg = [c for c in df_buildings.columns
                        if c.startswith('CumEAD_P')]

    # Buildings a retrofit does not apply to count at their No-mitigation
    # damage in that retrofit's community total (see
    # _backfill_dropped_actions). Without this, the totals left their damage
    # out and reported it as benefit.
    df_for_totals = _backfill_dropped_actions(df_buildings)

    agg_by_occ = {}
    for occ, ids_filter in occ_id_map.items():
        if ids_filter is None:
            df_o = df_for_totals
        else:
            if not ids_filter:
                agg_by_occ[occ] = pd.DataFrame()
                continue
            df_o = df_for_totals[df_for_totals['id'].astype(int).isin(ids_filter)]

        if df_o.empty or not pct_cols_in_bldg:
            agg_by_occ[occ] = pd.DataFrame()
            continue

        total = df_o.groupby(grp_cols, as_index=False)[pct_cols_in_bldg].sum()
        total = total.rename(columns={
            c: f"Total_CumEAD_{c.split('_')[1]}" for c in pct_cols_in_bldg
        })

        # InFP / OutFP P50 split from DFE_Status. The split sums
        # to Total_CumEAD_P50 by construction (every building falls into
        # exactly one DFE bucket), so the metric reconciles cleanly.
        if 'DFE_Status' in df_o.columns:
            df_in = df_o[df_o['DFE_Status'] == 'Under DFE']
            df_out = df_o[df_o['DFE_Status'] == 'Above DFE']
            in_p50 = (df_in.groupby(grp_cols, as_index=False)['CumEAD_P50']
                      .sum()
                      .rename(columns={'CumEAD_P50': 'InFP_CumEAD_P50'})
                      if not df_in.empty
                      else pd.DataFrame(columns=grp_cols + ['InFP_CumEAD_P50']))
            out_p50 = (df_out.groupby(grp_cols, as_index=False)['CumEAD_P50']
                       .sum()
                       .rename(columns={'CumEAD_P50': 'OutFP_CumEAD_P50'})
                       if not df_out.empty
                       else pd.DataFrame(columns=grp_cols + ['OutFP_CumEAD_P50']))
            merged = (total.merge(in_p50, on=grp_cols, how='left')
                           .merge(out_p50, on=grp_cols, how='left'))
            merged['InFP_CumEAD_P50']  = merged['InFP_CumEAD_P50'].fillna(0.0)
            merged['OutFP_CumEAD_P50'] = merged['OutFP_CumEAD_P50'].fillna(0.0)
        else:
            merged = total.copy()
            merged['InFP_CumEAD_P50']  = 0.0
            merged['OutFP_CumEAD_P50'] = 0.0

        merged['Num_Buildings'] = bldg_counts[occ]
        agg_by_occ[occ] = merged.reset_index(drop=True)

    # Optional sanity comparison: if the categories CSV is present, log
    # how its 'All' P50 differs from the per-building P50 we just built.
    # We don't surface this in the UI because the per-building source
    # is now canonical - the comparison just helps when debugging
    # data-pipeline issues for a new location.
    cat_csv_path = join(f'{location_slug}_CumulativeDamage_categories.csv')
    cat = None
    try:
        cat = pd.read_csv(cat_csv_path)
        cat['TargetYear'] = _bundle_normalize_target_year(cat['TargetYear'])
    except (FileNotFoundError, pd.errors.EmptyDataError):
        pass

    # 7. Water levels - load raw MC + pre-compute percentile shim
    water_levels = {}
    for slr_key, fname_suffix in (('50th-percentile', 'P50'),
                                   ('90th-percentile', 'P90')):
        wl_path = join(f'DDD___{location_slug}_MC_annual_max_waterlevels_{fname_suffix}.csv')
        try:
            wl_mc = pd.read_csv(wl_path)
        except FileNotFoundError:
            continue
        if 'Year' not in wl_mc.columns:
            continue
        water_levels[slr_key] = _bundle_wl_percentiles_from_mc(wl_mc)
        # Raw MC ensemble, suffixed `_mc` so legacy code paths that iterate
        # `for slr in water_levels` and assume percentile rows ignore them.
        water_levels[f'{slr_key}_mc'] = wl_mc

    return {
        'buildings':          df_buildings,
        'agg':                agg_by_occ.get('All'),
        'agg_by_occ':         agg_by_occ,
        'bldg_attrs':         bldg_attrs,
        'water_levels':       water_levels,
        'metadata':           metadata,
        'skipped':            skipped,
        'bfe_ft':             bfe_ft,
        'target_year_labels': target_year_labels,
        'format':             'bundle',
        'location_slug':      location_slug,
        'data_folder':        data_folder,
        'matlab_elev_noop_rows': _matlab_noop_count,
    }


def _bundle_pretty_location_name(slug):
    """Turn 'MasticBeach' into 'Mastic Beach'. Falls back to a sensible
    rendering of the slug (CamelCase-split or underscore-replaced) when
    the slug isn't in the known-locations table."""
    pretty = parse_filename(slug + '.csv')
    # parse_filename's known-pattern path returns names with spaces
    # ('Mastic Beach'); its fallback path returns the slug unchanged. We
    # only trust the result when it actually inserted a space - otherwise
    # we run our own CamelCase-aware splitter to handle slugs the table
    # doesn't know about.
    if pretty != 'Unknown Location' and ' ' in pretty:
        return pretty
    # Generic fallback: replace underscores with spaces, then split CamelCase
    # boundaries (keeps acronyms intact: 'NYCBay' → 'NYC Bay').
    import re
    s = slug.replace('_', ' ')
    s = re.sub(r'(?<=[a-z])([A-Z])', r' \1', s)
    s = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1 \2', s)
    return s.strip()


def load_data_from_folder(data_folder="."):
    """Discover bundles in `data_folder` and return the data_store.

    A bundle is identified by the presence of `{slug}_metadata.csv` plus
    the four required companion files. Locations missing any required
    file are skipped silently - we don't claim partial bundles.
    """
    data_store = {}
    available_locations = set()

    if not os.path.exists(data_folder):
        return data_store, []

    metas = sorted(glob.glob(os.path.join(data_folder, '*_metadata.csv')))
    for meta_path in metas:
        slug = os.path.basename(meta_path)[:-len('_metadata.csv')]
        required = [
            f'{slug}_bldg_lookup.csv',
            f'{slug}_bldg_CumulativeDamage.csv',
            f'{slug}_CumulativeDamage_categories.csv',
            f'DDD___{slug}___NSI.xlsx',
        ]
        if not all(os.path.exists(os.path.join(data_folder, r)) for r in required):
            continue
        try:
            # Fingerprint every bundle file (mtime + size) so an edit to any of
            # them - e.g. correcting a structure value in the NSI xlsx -
            # invalidates load_bundle's cache and forces a fresh read.
            _sig = tuple(
                (os.path.getmtime(p), os.path.getsize(p))
                for p in [meta_path] + [os.path.join(data_folder, r) for r in required]
            )
        except OSError:
            _sig = None
        try:
            entry = load_bundle(data_folder, slug, file_sig=_sig)
        except Exception as e:
            # Don't kill the app on a malformed bundle - log and skip.
            print(f"[loader] failed to load '{slug}': {e}")
            continue
        # Use the metadata's own LOCATION when present; fall back to slug.
        location_name = entry['metadata'].get('LOCATION', '').strip().strip('"')
        if not location_name:
            location_name = _bundle_pretty_location_name(slug)
        else:
            # Even when LOCATION is set, prefer the human-readable form
            # from parse_filename if it knows the slug (e.g. MasticBeach →
            # Mastic Beach).
            pretty = _bundle_pretty_location_name(slug)
            if pretty != slug:
                location_name = pretty
        data_store[location_name] = entry
        available_locations.add(location_name)

    return data_store, sorted(list(available_locations))




def discover_bundles(data_root=None, extra_folders=()):
    """Discover result bundles in `data_root/<Slug>/` sub-folders (preferred
    layout) and in any flat folder listed in `extra_folders` (legacy layout).
    Returns (data_store, sorted location names). Later folders never override
    a location found earlier."""
    from adapt_core.config import DATA_DIR, APP_ROOT
    if data_root is None:
        data_root = DATA_DIR
    folders = []
    if os.path.isdir(data_root):
        for name in sorted(os.listdir(data_root)):
            full = os.path.join(data_root, name)
            if os.path.isdir(full) and not name.startswith(('_', '.')):
                folders.append(full)
    folders.extend(list(extra_folders) or [APP_ROOT])
    data_store = {}
    for folder in folders:
        store, names = load_data_from_folder(folder)
        for n in names:
            if n not in data_store:
                data_store[n] = store[n]
    return data_store, sorted(data_store)


def bundle_folder_for_slug(slug, data_root=None):
    """Folder where a location's bundle lives (created on demand)."""
    from adapt_core.config import DATA_DIR
    root = data_root or DATA_DIR
    return os.path.join(root, slug)


def bundle_status(folder, slug):
    """Which bundle files are present for a slug in `folder`."""
    names = {
        'metadata': f'{slug}_metadata.csv',
        'lookup': f'{slug}_bldg_lookup.csv',
        'cumdmg': f'{slug}_bldg_CumulativeDamage.csv',
        'categories': f'{slug}_CumulativeDamage_categories.csv',
        'skipped': f'{slug}_skipped_buildings.csv',
        'nsi': f'DDD___{slug}___NSI.xlsx',
        'wl_p50': f'DDD___{slug}_MC_annual_max_waterlevels_P50.csv',
        'wl_p90': f'DDD___{slug}_MC_annual_max_waterlevels_P90.csv',
        'roads': f'{slug}_osm_roads.json',
    }
    out = {}
    for k, n in names.items():
        p = os.path.join(folder, n)
        out[k] = {'file': n, 'path': p, 'exists': os.path.exists(p),
                  'size': os.path.getsize(p) if os.path.exists(p) else 0,
                  'mtime': os.path.getmtime(p) if os.path.exists(p) else None}
    return out
