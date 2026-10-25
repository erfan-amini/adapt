"""FEMA/Hazus FAST depth-damage curve explorer (ported from main())."""
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

import re as _re

ss = None  # bound per run by bind_session_state()


def bind_session_state(session_state):
    global ss
    ss = session_state


_OCC_FULL = {
    "RES1": "Single Family Dwelling", "RES2": "Manufactured Housing",
    "RES3A": "Multi-Family Duplex", "RES3B": "Multi-Family (3-4 Units)",
    "RES3C": "Multi-Family (5-9 Units)", "RES3D": "Multi-Family (10-19 Units)",
    "RES3E": "Multi-Family (20-49 Units)", "RES3F": "Multi-Family (50+ Units)",
    "RES4": "Temporary Lodging", "RES5": "Institutional Dormitory", "RES6": "Nursing Home",
    "COM1": "Retail Trade", "COM2": "Wholesale Trade", "COM3": "Personal & Repair Services",
    "COM4": "Professional/Technical", "COM5": "Banks/Financial", "COM6": "Hospital",
    "COM7": "Medical Office/Clinic", "COM8": "Entertainment & Recreation", "COM9": "Theaters",
    "COM10": "Parking", "IND1": "Heavy Industrial", "IND2": "Light Industrial",
    "IND3": "Food/Drugs/Chemicals", "IND4": "Metals/Minerals Processing", "IND5": "High Technology",
    "IND6": "Construction", "AGR1": "Agriculture", "REL1": "Church/Non-Profit",
    "GOV1": "General Government", "GOV2": "Emergency Response", "EDU1": "Schools (K-12)",
    "EDU2": "Colleges/Universities",
}

# --- Parse a FAST curve Description into (stories, basement, zone) ---
def _frag_parse_desc(desc):
    d = str(desc).lower()
    if "split" in d:
        stories = "Split level"
    elif "three or more" in d or "3 or more" in d:
        stories = "3+ stories"
    elif "two floor" in d or "two stor" in d or "2 floor" in d:
        stories = "2 stories"
    elif "one floor" in d or "one stor" in d or "1 floor" in d:
        stories = "1 story"
    elif "1to2" in d or "1 to 2" in d:
        stories = "1\u20132 stories"
    else:
        stories = None
    if "w/ basement" in d or "with basement" in d or "sub-grade" in d or "subgrade" in d:
        basement = "With basement"
    elif "no basement" in d or "slab" in d or "no_basement" in d:
        basement = "No basement"
    else:
        basement = None
    if "coastal a or v" in d:
        zone = "Coastal A/V"
    elif "v-zone" in d or "v zone" in d or "coastal v" in d:
        zone = "V-Zone"
    elif "a-zone" in d or "a zone" in d or "coastal a" in d:
        zone = "A-Zone"
    else:
        zone = None
    return stories, basement, zone

def _frag_story_key(x):
    s = str(x)
    m = _re.match(r"(\d+)", s)
    if m:
        return (0, int(m.group(1)))
    if "split" in s.lower():
        return (1, 0)
    return (2, s)

def _frag_curve_long(_df):
    """A FAST DmgFn table -> tidy rows [fnid, occ, stories, basement, zone, desc, depth, pct]."""
    idcol = next((c for c in _df.columns if str(c).strip().lower().endswith("dmgfnid")),
                 _df.columns[0])
    depth_cols = {}
    for c in _df.columns:
        cs = str(c).strip().lower()
        mm = _re.fullmatch(r"m(\d+)", cs)
        pp = _re.fullmatch(r"p(\d+)", cs)
        if mm:
            depth_cols[c] = -int(mm.group(1))
        elif pp:
            depth_cols[c] = int(pp.group(1))
    occ_col = next((c for c in _df.columns if str(c).strip().lower() == "occupancy"), None)
    desc_col = next((c for c in _df.columns if str(c).strip().lower() == "description"), None)
    rows = []
    for _, r in _df.iterrows():
        occ = str(r[occ_col]).strip() if occ_col else ""
        desc = str(r[desc_col]).strip() if desc_col else ""
        fnid = r[idcol]
        stories, basement, zone = _frag_parse_desc(desc)
        for c, d in depth_cols.items():
            try:
                v = float(r[c])
            except Exception:
                continue
            rows.append((fnid, occ, stories, basement, zone, desc, d, v))
    return pd.DataFrame(
        rows, columns=["fnid", "occ", "stories", "basement", "zone", "desc", "depth", "pct"]
    )

def _frag_map_lookup(_map, soid, idcol, zonecol):
    """SOID + hazard column -> DmgFnId (mirrors the MATLAB calc_damage_pcts)."""
    if _map is None or not soid:
        return None
    try:
        keycol = _map.columns[0]  # SOccupId
        m = _map[_map[keycol].astype(str).str.strip().str.upper() == str(soid).strip().upper()]
        if zonecol in m.columns:
            m = m[pd.to_numeric(m[zonecol], errors="coerce") == 1]
        idc = next((c for c in _map.columns if str(c).strip().lower() == idcol.lower()), None)
        if (not m.empty) and idc is not None:
            return m.iloc[0][idc]
    except Exception:
        pass
    return None

def _try_frag_autoload():
    """Find the FEMA/Hazus FAST CSVs already sitting in the app folder and load
    them, so nothing needs to be uploaded. Pulls the two depth-damage function
    tables (…DmgFn) and the two SOID->FnId mapping tables (…DmgFinal)."""
    if ss.get("_frag_autoload_tried"):
        return
    ss["_frag_autoload_tried"] = True
    import glob
    seen, cands = set(), []
    from adapt_core.config import CURVES_DIR, APP_ROOT
    for _d in (CURVES_DIR, APP_ROOT, ".", "data", "Data", "FAST", "fast", "curves", "depth_damage", "fragility"):
        for _f in glob.glob(os.path.join(_d, "*.csv")):
            if _f not in seen:
                seen.add(_f)
                cands.append(_f)
    sfn = cfn = smap = cmap = None
    for _p in cands:
        try:
            _head = pd.read_csv(_p, nrows=4)
        except Exception:
            continue
        cols = [str(c).strip().lower() for c in _head.columns]
        ndepth = sum(1 for c in cols if _re.fullmatch(r"[mp]\d+", c))
        is_cont = "cont" in os.path.basename(_p).lower()
        if ndepth >= 3 and "occupancy" in cols:          # …DmgFn (curves)
            if is_cont:
                cfn = cfn or _p
            else:
                sfn = sfn or _p
        elif "soccupid" in cols:                          # …DmgFinal (mapping)
            if is_cont:
                cmap = cmap or _p
            else:
                smap = smap or _p
    if sfn and cfn:
        try:
            ss["_frag_S"] = _frag_curve_long(pd.read_csv(sfn))
            ss["_frag_C"] = _frag_curve_long(pd.read_csv(cfn))
            ss["_frag_Smap"] = pd.read_csv(smap) if smap else None
            ss["_frag_Cmap"] = pd.read_csv(cmap) if cmap else None
            ss["_frag_src"] = (os.path.basename(sfn), os.path.basename(cfn))
        except Exception as _e:
            ss["_frag_err"] = str(_e)

def render_fragility_curves(building_row=None, ctx="frag"):
    # Auto-load the curves already shipped with the app.
    if ss.get("_frag_S") is None or ss.get("_frag_C") is None:
        _try_frag_autoload()
    S = ss.get("_frag_S")
    C = ss.get("_frag_C")
    if S is None or C is None or S.empty:
        st.info(
            "The FEMA/Hazus depth-damage curve files weren't found in the app folder. "
            "Expected the FAST tables `flBldgStructDmgFn.csv` and `flBldgContDmgFn.csv` "
            "(and optionally the `…DmgFinal.csv` mapping tables) next to `app.py`. "
            "You can also load them here once."
        )
        cA, cB = st.columns(2)
        with cA:
            upS = st.file_uploader("Structure depth-damage CSV", type=["csv"], key=f"{ctx}_upS")
        with cB:
            upC = st.file_uploader("Content depth-damage CSV", type=["csv"], key=f"{ctx}_upC")
        if upS is not None and upC is not None:
            try:
                ss["_frag_S"] = _frag_curve_long(pd.read_csv(upS))
                ss["_frag_C"] = _frag_curve_long(pd.read_csv(upC))
                st.rerun()
            except Exception as _e:
                st.error(f"Could not parse the curve files: {_e}")
        return

    occs = sorted(S["occ"].dropna().unique())
    if not occs:
        st.warning("No occupancy classes were detected in the curve files.")
        return

    def _occ_label(o):
        return f"{o} - {_OCC_FULL[o]}" if o in _OCC_FULL else o

    # Defaults / exact curve when embedded for a specific building.
    bld_occ = bld_zone = bld_base = None
    hl_S = hl_C = None
    if building_row is not None:
        _raw = str(building_row.get("occupancy_type", "") or "").strip().upper()
        bld_occ = _raw if _raw in occs else next((o for o in occs if _raw.startswith(o)), None)
        _soid = str(building_row.get("SOID", "") or "").strip()
        if _soid:
            hl_S = _frag_map_lookup(ss.get("_frag_Smap"), _soid, "BldgDmgFnId", "HazardCA")
            hl_C = _frag_map_lookup(ss.get("_frag_Cmap"), _soid, "ContDmgFnId", "HazardCA")
        # Default the selectors to the resolved curve's own attributes so the
        # building's exact curve is actually inside the filtered overlay.
        if hl_S is not None and (S["fnid"] == hl_S).any():
            _hrow = S[S["fnid"] == hl_S].iloc[0]
            bld_occ = _hrow["occ"] or bld_occ
            bld_zone = _hrow["zone"]
            bld_base = _hrow["basement"]
        elif _soid:
            bld_base = "With basement" if _soid.upper().endswith("B") else "No basement"

    _default_occ = (bld_occ if bld_occ in occs
                    else "RES1" if "RES1" in occs else occs[0])

    c1, c2, c3 = st.columns(3)
    with c1:
        sel_occ = st.selectbox(
            "Occupancy", occs, index=occs.index(_default_occ),
            format_func=_occ_label, key=f"{ctx}_occ",
        )
    subO = S[S["occ"] == sel_occ]
    zones = [z for z in ["A-Zone", "V-Zone", "Coastal A/V", "Riverine"]
             if z in set(subO["zone"].dropna())]
    with c2:
        if zones:
            _zi = (zones.index(bld_zone) if (bld_zone in zones)
                   else zones.index("A-Zone") if "A-Zone" in zones else 0)
            sel_zone = st.selectbox("Flood zone", zones, index=_zi, key=f"{ctx}_zone")
        else:
            sel_zone = None
            st.selectbox("Flood zone", ["(not in data)"], index=0, disabled=True, key=f"{ctx}_zone")
    subZ = subO if sel_zone is None else subO[subO["zone"] == sel_zone]
    bases = [b for b in ["No basement", "With basement"] if b in set(subZ["basement"].dropna())]
    with c3:
        if bases:
            _bi = bases.index(bld_base) if (bld_base in bases) else 0
            sel_base = st.selectbox("Basement", bases, index=_bi,
                                    key=f"{ctx}_base", disabled=(len(bases) == 1))
        else:
            sel_base = None
            st.selectbox("Basement", ["(not in data)"], index=0, disabled=True, key=f"{ctx}_base")

    def _filtered(df):
        sub = df[df["occ"] == sel_occ]
        if sel_zone is not None:
            sub = sub[sub["zone"] == sel_zone]
        if sel_base is not None:
            sub = sub[(sub["basement"] == sel_base) | (sub["basement"].isna())]
        return sub

    def _plot(df, title, hl_fnid):
        sub = _filtered(df)
        if sub.empty:
            st.info(f"No {title.lower()} curve for this selection.")
            return
        has_story = sub["stories"].notna().any() and sub["stories"].nunique() > 1
        series_col = "stories" if has_story else "desc"
        fig = go.Figure()
        for sv in sorted(sub[series_col].dropna().unique(), key=_frag_story_key):
            _s = sub[sub[series_col] == sv].sort_values("depth")
            is_hl = (hl_fnid is not None) and bool((_s["fnid"] == hl_fnid).any())
            _name = str(sv)
            if has_story is False:
                _name = (_name[:38] + "\u2026") if len(_name) > 39 else _name
            if is_hl:
                _name += "  \u25c0 this building"
            fig.add_trace(go.Scatter(
                x=_s["depth"], y=_s["pct"], mode="lines+markers",
                name=_name, line=dict(width=4 if is_hl else 2),
            ))
        fig.update_layout(
            title=title, xaxis_title="Flood depth above first floor (ft)",
            yaxis_title="Damage (% of value)", height=390,
            # Give the y tick numbers + rotated axis title their own room and
            # let automargin grow it as needed (with theme=None the old l=10
            # was too tight, so the numbers overlapped the title and clipped).
            margin=dict(l=70, r=20, t=44, b=52),
            legend=dict(orientation="h", y=-0.32, font=dict(size=10)),
            plot_bgcolor='white',
            xaxis=dict(showgrid=True, gridcolor='#e5e7eb', gridwidth=1,
                       zeroline=False, automargin=True),
            yaxis=dict(showgrid=True, gridcolor='#e5e7eb', gridwidth=1,
                       zeroline=False, automargin=True, title_standoff=12,
                       ticksuffix="  "),
        )
        # theme=None so the explicit white background + gridlines above are
        # honored; Streamlit's default plotly theme otherwise overrides the
        # axis grid settings and the vertical gridlines disappear.
        st.plotly_chart(fig, use_container_width=True, theme=None, key=f"{ctx}_{title}")

    p1, p2 = st.columns(2)
    with p1:
        _plot(S, "Structure damage", hl_S)
    with p2:
        _plot(C, "Content damage", hl_C)

    _src = ss.get("_frag_src")
    _srctxt = (f" Source files: {_src[0]} / {_src[1]}." if _src else "")
    st.caption(
        "Curves overlay the available number-of-stories variants (or specific-occupancy "
        "variants) for the selected occupancy, flood zone, and basement condition. "
        "Depth is measured above the first-floor elevation; values are percent of structure "
        "or contents value (FEMA/Hazus FAST depth-damage functions)." + _srctxt
    )
