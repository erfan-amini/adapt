"""Shared constants and small helpers (ported from the legacy app.py)."""
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

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

# ----------------------------------------------------------------------------
# Per-measure adaptation cost estimates.
#
# Source: the project workshop adaptation-measure table. Only the measure name
# (its first column) and the cost range (its 4th column, "Cost") are used here;
# the expected-benefit and remaining-recovery-cost columns from that table are
# intentionally NOT used. Values are keyed by the app's internal Action names:
#     "Elevate utilities"        -> Raise Utilities
#     "Wet-floodproof basement"  -> WFP B
#     "Wet-floodproof first floor" -> WFP 1st
#     "Elevate house"            -> Elevate
# ----------------------------------------------------------------------------
ADAPTATION_COST_ESTIMATES = {
    'Raise Utilities': '$5,000 - $40,000',
    'WFP B':           '$8,500 - $30,000',
    'WFP 1st':         '$10,000 - $100,000 or more',
    'Elevate':         'Per sq ft: $50 - $120 or more',
}
ADAPTATION_COST_SOURCE = (
    "Cost estimates per measure are taken from the project workshop "
    "adaptation-measure table (measure name and cost columns only)."
)

# ----------------------------------------------------------------------------
# Per-building retrofit flags written by the MATLAB pipeline
# (AAA___Main_v14.m) into {LOCATION}_bldg_lookup.csv:
#   ElevationEligible - False when the first floor is already within the
#       location's elevation threshold of the DFE. Elevate then returns the
#       no-action damage, so the per-building views hide it, the same way
#       they hide it for above-DFE buildings.
#   OneStoryAddStory  - True when the building's "WFP 1st" result is the
#       one-story add-a-story conversion: a new story is built on top and
#       the living space moves up one story (STORY_H_FT = 10 ft in MATLAB).
#       It is labelled "Add a story" in the per-building views. Other
#       one-story buildings have no upper floor, so WFP 1st is hidden there.
# Both flags only change what the per-building views show. No rows are
# dropped from the damage table, so community totals are unaffected.
# Bundles without these columns behave exactly as before.
# ----------------------------------------------------------------------------
ADD_STORY_LABEL = 'Add a story'
ADD_STORY_HEIGHT_FT = 10.0          # = STORY_H_FT in AAA___Main_v14.m
# No sourced cost for building a new story yet; the WFP 1st range above
# does not cover it. Replace this text once a source is chosen.
ADD_STORY_COST_TEXT = 'Not yet estimated'


def _flag_true(v):
    """Read a 0/1 or true/false flag from a bundle CSV cell. Missing -> False."""
    if v is None:
        return False
    try:
        if pd.isna(v):
            return False
    except (TypeError, ValueError):
        pass
    if isinstance(v, str):
        return v.strip().lower() in ('1', '1.0', 'true', 'yes')
    try:
        return bool(float(v))
    except (TypeError, ValueError):
        return False


def _elev_eligible_flag(v):
    """ElevationEligible cell -> bool. Missing -> True, the same defensive
    fallback the MATLAB writer uses, so older bundles keep Elevate."""
    if v is None:
        return True
    try:
        if pd.isna(v):
            return True
    except (TypeError, ValueError):
        pass
    return _flag_true(v)


def _is_one_story_bldg(soid, n_stories):
    """Same rule as is_one_story_fn in AAA___Main_v14.m: for RES1 SOIDs
    (R1 + story digit + basement flag) the story digit decides, R11x being
    one-story; any other class uses number_of_stories <= 1 (rounded).
    A missing story count counts as one-story, as the map hover always did."""
    s = '' if soid is None else str(soid).strip()
    if s.lower() == 'nan':
        s = ''
    if len(s) >= 3 and s.startswith('R1'):
        return s[2] == '1'
    try:
        if n_stories is None or pd.isna(n_stories):
            return True
        return float(n_stories) < 1.5
    except (TypeError, ValueError):
        return True


def parse_filename(filename):
    """Parse filename to extract location."""
    name = os.path.basename(filename)
    # Strip all known extensions
    for ext in ['.xlsx', '.XLSX', '.csv', '.CSV']:
        name = name.replace(ext, '')
    
    location = "Unknown Location"
    location_patterns = [
        ('MasticBeach', 'Mastic Beach'),
        ('Mastic_Beach', 'Mastic Beach'),
        ('WestPoint', 'West Point'),
        ('West_Point', 'West Point'),
        ('Shinnecock', 'Shinnecock'),
        ('Pamunkey', 'Pamunkey'),
        ('Hampton', 'Hampton'),
        ('Montauk', 'Montauk'),
    ]
    
    for pattern, display_name in location_patterns:
        if pattern.lower() in name.lower():
            location = display_name
            break
    
    if location == "Unknown Location":
        parts = name.split('_')
        for part in parts:
            if part not in ['CSV1', 'CSV2', 'Aggregated', 'PerBuilding', 'Results', 'RES', 'NONRES', 'COM', 'ALL']:
                if len(part) > 3:
                    location = part.replace('_', ' ')
                    break
    
    return location


def is_residential(occupancy_type):
    """Check if occupancy type is residential"""
    if pd.isna(occupancy_type):
        return False
    occ = str(occupancy_type).upper()
    return occ.startswith('RES')


# NSI foundation_type codes that mean "this building has a basement that
# could plausibly be wet-floodproofed". Anything else (Pier, Slab,
# Crawlspace, Solid wall, etc.) has no basement, so the WFP Basement
# retrofit is physically meaningless for it. Stored as a set so the
# applicability check is a single membership test.
_BASEMENT_FOUNDATION_CODES = {'B'}

# DFE-status strings (lowercased + whitespace-stripped) that mean
# "this building is already above design flood elevation". For such
# buildings, the Elevate retrofit provides no further benefit - the
# data generator typically encodes this as a no-op, but we drop the
# row explicitly as a backstop so it doesn't slip through into hovers
# or charts due to numerical drift.
_ABOVE_DFE_STATUS_STRINGS = {'above dfe', 'above_dfe', 'abovedfe'}


def retrofit_applies(action, foundation_type=None, dfe_status=None):
    """Return True if `action` physically applies to a building with the
    given foundation type / DFE status.

    A retrofit that doesn't apply (e.g., wet-floodproofing a basement
    that doesn't exist, or elevating a building that's already above
    DFE) should not appear in the UI - its "damage" value is an
    artifact of the math running regardless of whether the retrofit is
    meaningful, and showing it as a $0 / -100% saving is actively
    misleading.

    Rules:
      * 'WFP B' (Wet Floodproof Basement): requires foundation_type == 'B'.
        Missing foundation type → conservatively excluded.
      * 'Elevate': hidden for buildings already above DFE.
        Missing DFE status → NOT hidden (we don't have grounds to drop it).
      * Everything else ('No mitigation', 'Raise Utilities', 'WFP 1st'):
        applies universally.
    """
    if action == 'WFP B':
        if pd.isna(foundation_type):
            return False
        return str(foundation_type).strip().upper() in _BASEMENT_FOUNDATION_CODES
    if action == 'Elevate':
        if pd.isna(dfe_status):
            return True
        return str(dfe_status).strip().lower() not in _ABOVE_DFE_STATUS_STRINGS
    return True


def filter_by_occupancy(df, occupancy_selection):
    """Filter dataframe by occupancy type selection"""
    if df is None:
        return None
    
    if occupancy_selection == "All":
        return df
    
    if 'occupancy_type' not in df.columns:
        return df
    
    if occupancy_selection == "Residential":
        return df[df['occupancy_type'].apply(is_residential)].copy()
    elif occupancy_selection == "Non-Residential":
        return df[~df['occupancy_type'].apply(is_residential)].copy()
    
    return df


def convert_floodplain_status(status):
    """Normalize a building's DFE-status string to canonical values.

    Canonical bundles already ship `DFE_Status` with the values
    'Above DFE' / 'Under DFE' - those pass through unchanged. Legacy
    bundles shipped the column as 'In floodplain' / 'Out of floodplain';
    we map those to the canonical wording here so every downstream
    comparison can assume a single vocabulary. Anything we don't
    recognize is returned unchanged."""
    if pd.isna(status):
        return status
    if 'in floodplain' in str(status).lower() or 'in_floodplain' in str(status).lower():
        return 'Under DFE'
    elif 'out of floodplain' in str(status).lower() or 'out_of_floodplain' in str(status).lower():
        return 'Above DFE'
    return status


def format_currency(value):
    """Format large numbers as currency. Trailing zeros after the decimal
    point are dropped, e.g. $4.00M -> $4M but $12.79B -> $12.79B."""
    if pd.isna(value) or value == 0:
        return "$0"
    elif value >= 1e9:
        return f"${_strip_trailing_zeros(f'{value/1e9:.2f}')}B"
    elif value >= 1e6:
        return f"${_strip_trailing_zeros(f'{value/1e6:.2f}')}M"
    elif value >= 1e3:
        return f"${_strip_trailing_zeros(f'{value/1e3:.1f}')}K"
    else:
        return f"${value:.0f}"


# Damages whose absolute value is below this threshold render as "$0" in
# value labels (matches the workshop visualization convention).
ZERO_THRESH_DISPLAY = 1000


def _strip_trailing_zeros(num_str):
    """Strip trailing zeros (and a stranded '.') from a decimal number string.
    '1.00' -> '1' ; '12.50' -> '12.5' ; '12.79' -> '12.79' ; '7' -> '7'."""
    if '.' not in num_str:
        return num_str
    return num_str.rstrip('0').rstrip('.')


def fmt_money_short(value):
    """Compact currency label for chart labels/axes. Trailing zeros after the
    decimal point are dropped so '$4.00M' renders as '$4M' but '$12.79B' stays."""
    if pd.isna(value):
        return ""
    if value == 0:
        return "$0"
    sign = "-" if value < 0 else ""
    v = abs(value)
    if v >= 1e9:
        return f"{sign}${_strip_trailing_zeros(f'{v/1e9:.2f}')}B"
    elif v >= 1e6:
        return f"{sign}${_strip_trailing_zeros(f'{v/1e6:.2f}')}M"
    elif v >= 1e3:
        # $k with no decimals when >= $10k, 1 decimal otherwise for small values
        if v >= 1e4:
            return f"{sign}${v/1e3:.0f}k"
        else:
            return f"{sign}${_strip_trailing_zeros(f'{v/1e3:.1f}')}k"
    else:
        return f"{sign}${v:.0f}"


def fmt_money_rounded(value):
    """Rounded currency label for on-plot value annotations (box-and-whisker
    labels etc.). Millions and billions show ONE decimal place, with the
    decimal dropped when it's zero - e.g. $229.23M → $229.2M, $229.04M →
    $229M, $12.79B → $12.8B, $12.00B → $12B. Sub-million values are rounded
    to integers: $4.8k → $5k, $4800 → $4800. Zero renders as $0."""
    if pd.isna(value):
        return ""
    if value == 0:
        return "$0"
    sign = "-" if value < 0 else ""
    v = abs(value)
    if v >= 1e9:
        return f"{sign}${_strip_trailing_zeros(f'{v/1e9:.1f}')}B"
    elif v >= 1e6:
        return f"{sign}${_strip_trailing_zeros(f'{v/1e6:.1f}')}M"
    elif v >= 1e3:
        return f"{sign}${round(v/1e3):.0f}k"
    else:
        return f"{sign}${v:.0f}"


def nice_round_up(value):
    """Round value up to a 'nice' breakpoint of form (1, 2, 2.5, 5) × 10^k.
    Used for dynamic bin edges so legend labels look clean."""
    if value is None or pd.isna(value) or value <= 0:
        return 0
    magnitude = 10 ** np.floor(np.log10(value))
    normalized = value / magnitude
    if   normalized <= 1.0: return 1.0  * magnitude
    elif normalized <= 2.0: return 2.0  * magnitude
    elif normalized <= 2.5: return 2.5  * magnitude
    elif normalized <= 5.0: return 5.0  * magnitude
    else:                   return 10.0 * magnitude


def smart_money_ticks(max_value, target_n=5):
    """Return (tickvals, ticktext) spanning [0, max_value] with nice rounded
    tick spacing, labels formatted with fmt_money_short."""
    if max_value is None or max_value <= 0 or pd.isna(max_value):
        return [0], ["$0"]
    raw_step = max_value / max(target_n, 1)
    magnitude = 10 ** np.floor(np.log10(raw_step))
    normalized = raw_step / magnitude
    if normalized < 1.5:
        nice_step = 1 * magnitude
    elif normalized < 3.5:
        nice_step = 2 * magnitude
    elif normalized < 7.5:
        nice_step = 5 * magnitude
    else:
        nice_step = 10 * magnitude
    ticks = []
    t = 0.0
    # include one tick past the max so the axis has headroom
    while t <= max_value * 1.02:
        ticks.append(t)
        t += nice_step
    if ticks[-1] < max_value:
        ticks.append(ticks[-1] + nice_step)
    labels = [fmt_money_short(t) for t in ticks]
    return ticks, labels
