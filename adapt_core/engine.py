"""ADAPT damage and adaptation engine.

A line-for-line Python port of the MATLAB pipeline `AAA___Main_v14.m`
(compute_building_damages_vectorized, convert_NSI_to_SOID, the one-story and
manufactured-housing rules, mc_to_pct and save_results_csvs). Validated
against the MATLAB CSV outputs: for buildings with identical inputs all 23
percentiles of every action match to machine precision.

Inputs per location
    NSI inventory (xlsx/csv) with columns
        id, occupancy_type, number_of_stories, foundation_type,
        foundation_height, ground_elevation, structure_value, content_value
        (+ longitude, latitude, address ... passed through to the lookup)
    Monte Carlo annual-maximum water levels for P50 and P90 sea-level rise
        Year, MC_0001 ... MC_1000  (ft NAVD88)
    FEMA/Hazus FAST curve tables
        flBldgStructDmgFinal.csv, flBldgStructDmgFn.csv,
        flBldgContDmgFinal.csv,   flBldgContDmgFn.csv

Outputs (the bundle the app reads)
    <LOC>_metadata.csv, <LOC>_bldg_lookup.csv, <LOC>_bldg_CumulativeDamage.csv,
    <LOC>_CumulativeDamage_categories.csv, <LOC>_skipped_buildings.csv,
    <LOC>_occupancy_fallback.csv
"""
from __future__ import annotations

import os
import time
import datetime as _dt
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from adapt_core.config import (ACTION_NAMES, ACTION_CODES, SCENARIOS, SCENARIO_LABELS,
                               PERCENTILES, TARGET_YEARS_DEFAULT, FLOOD_ZONE_DEFAULT,
                               UTIL_REPL_USD_DEFAULT, STORY_H_FT_DEFAULT, Y_NOW)

ENGINE_VERSION = "python-1.0 (port of AAA___Main_v14.m)"

DEPTHS = np.arange(-4, 25, dtype=float)             # 29 curve points, ft above first floor
DEPTH_COLS = [f"m{abs(d)}" if d < 0 else f"p{d}" for d in range(-4, 25)]
PCT_ARR = np.array(PERCENTILES, dtype=float)
PCT_HDR = [f"P{p:02d}" for p in PERCENTILES]

IX_BASE, IX_RAISE, IX_WFPB, IX_ELEV, IX_WFP1 = (ACTION_NAMES.index(a) for a in
                                                 ("No mitigation", "Raise Utilities", "WFP B", "Elevate", "WFP 1st"))


# ---------------------------------------------------------------------------
# Damage curves
# ---------------------------------------------------------------------------
class DamageCurves:
    """The four FAST tables plus a per-SOID cache of (struct, content) curves."""

    def __init__(self, folder):
        self.folder = folder
        self.smap = pd.read_csv(os.path.join(folder, "flBldgStructDmgFinal.csv"))
        self.sfn = pd.read_csv(os.path.join(folder, "flBldgStructDmgFn.csv"))
        self.cmap = pd.read_csv(os.path.join(folder, "flBldgContDmgFinal.csv"))
        self.cfn = pd.read_csv(os.path.join(folder, "flBldgContDmgFn.csv"))
        self.smap["SOccupId"] = self.smap["SOccupId"].astype(str).str.strip()
        self.cmap["SOccupId"] = self.cmap["SOccupId"].astype(str).str.strip()
        self._cache = {}
        self.warnings = []

    @staticmethod
    def zone_column(flood_zone):
        z = str(flood_zone).strip().upper()
        if z in ("A", "AE", "AH", "AO") or "COASTAL A" in z:
            return "HazardCA"
        if z in ("V", "VE") or "COASTAL V" in z:
            return "HazardCV"
        if z == "R" or "RIVERINE" in z:
            return "HazardR"
        return "HazardCA"

    def _row_curve(self, fn_table, id_col, fn_id):
        r = fn_table[fn_table[id_col] == fn_id]
        if r.empty:
            return None
        r = r.iloc[0]
        return np.array([float(r[c]) if c in fn_table.columns else 0.0 for c in DEPTH_COLS])

    def get(self, soid, flood_zone=FLOOD_ZONE_DEFAULT):
        """Return (struct_curve, content_curve, struct_fn_id, content_fn_id).
        struct_curve is None when the SOID has no curve."""
        key = (soid, str(flood_zone).upper())
        if key in self._cache:
            return self._cache[key]
        hz = self.zone_column(flood_zone)
        s = self.smap[(self.smap.SOccupId == soid) & (self.smap[hz] == 1)]
        struct = None
        sid = None
        if len(s):
            sid = int(s.BldgDmgFnId.iloc[0])
            struct = self._row_curve(self.sfn, "BldgDmgFnId", sid)
        c = self.cmap[(self.cmap.SOccupId == soid) & (self.cmap[hz] == 1)]
        cont = None
        cid = None
        if len(c):
            cid = int(c.ContDmgFnId.iloc[0])
            cont = self._row_curve(self.cfn, "ContDmgFnId", cid)
        if struct is not None and cont is None:
            self.warnings.append(f"No content damage curve for SOID={soid}, hazard={hz}; content damage zeroed.")
            cont = np.zeros(29)
        out = (struct, cont, sid, cid)
        self._cache[key] = out
        return out


# ---------------------------------------------------------------------------
# Occupancy -> SOID (convert_NSI_to_SOID)
# ---------------------------------------------------------------------------
def nsi_to_soid(occupancy, foundation_type, num_stories):
    """Return (soid, fallback_used). Mirrors convert_NSI_to_SOID in v14."""
    occ = str(occupancy).strip().upper()
    fcode = str(foundation_type).strip().upper()
    basement = "B" if (fcode == "B" or "WB" in occ) else "N"
    nst = float(num_stories)
    hcat = "L" if nst <= 2 else ("M" if nst <= 5 else "H")

    if occ.startswith("RES1"):
        if "-1S" in occ: st = "1"
        elif "-2S" in occ: st = "2"
        elif "-3S" in occ: st = "3"
        elif "SL" in occ: st = "S"
        else: st = str(int(min(3, max(1, round(nst)))))
        return "R1" + st + basement, False
    if occ.startswith("RES2"):
        return "R21" + basement, False
    if occ.startswith("RES3"):
        base = "R3A"
        for k in "ABCDEF":
            if f"RES3{k}" in occ or f"3{k}" in occ:
                base = "R3" + k
                break
        st = "1" if nst <= 2 else ("3" if nst <= 4 else "5")
        return base + st + basement, False
    for pref, code in (("RES4", "R4"), ("RES5", "R5"), ("RES6", "R6")):
        if occ.startswith(pref):
            return code + hcat + basement, False
    if occ.startswith("COM10"):
        return "C10" + hcat + basement, False
    for i in range(1, 10):
        if occ.startswith(f"COM{i}"):
            return f"C{i}" + hcat + basement, False
    for i in range(1, 7):
        if occ.startswith(f"IND{i}"):
            return f"I{i}" + hcat + basement, False
    if occ.startswith("AGR"):
        return "A1" + hcat + basement, False
    if occ.startswith("GOV1"):
        return "G1" + hcat + basement, False
    if occ.startswith("GOV2"):
        return "G2" + hcat + basement, False
    if occ.startswith("EDU1"):
        return "E1" + hcat + basement, False
    if occ.startswith("EDU2"):
        return "E2" + hcat + basement, False
    if occ.startswith("REL"):
        return "RE1" + hcat + basement, False
    # Unknown occupancy: RES1 fallback (v11)
    st = str(int(min(3, max(1, round(nst)))))
    return "R1" + st + basement, True


def is_manuf_or_temp(occ):
    o = str(occ).strip().upper()
    return o.startswith("RES2") or o.startswith("RES4")


def is_manuf_home(occ):
    return str(occ).strip().upper().startswith("RES2")


def is_one_story(num_stories, soid):
    """v14: RES1 SOIDs decide by the story digit; others use round(n) <= 1."""
    s = str(soid).strip()
    if len(s) >= 3 and s.startswith("R1"):
        return s[2] == "1"
    try:
        n = float(num_stories)
    except (TypeError, ValueError):
        return False
    if not np.isfinite(n) or n <= 0:
        return False
    return round(n) <= 1


def can_add_story(soid):
    s = str(soid).strip()
    return s.startswith("R1") or s.startswith("R3")


# ---------------------------------------------------------------------------
# Core damage transform (compute_building_damages_vectorized)
# ---------------------------------------------------------------------------
def _pct_on_curve(curve, depths_raw):
    d = np.clip(depths_raw, -4.0, 24.0)
    p = np.interp(d, DEPTHS, curve)
    p = np.where(depths_raw <= -4.0, 0.0, p)
    p = np.where(depths_raw >= 24.0, curve[-1], p)
    return np.clip(p, 0.0, 100.0)


def building_damages(WL, FFE, BFE, H, util_cost, struct_val, cont_val, struct_curve, cont_curve,
                     apply_manuf_restriction, has_basement, is_manuf_home_flag,
                     elev_min_shortfall_ft, one_story, add_story_ok):
    """Annual damage for one building under one SLR scenario.

    WL: (nYears, nMC) water levels, ft NAVD88. Returns (nActions, nYears, nMC).
    """
    nY, nM = WL.shape
    D = np.zeros((len(ACTION_NAMES), nY, nM))
    FFE_elevated = BFE + 2.0
    elev_eligible = FFE < (FFE_elevated - elev_min_shortfall_ft)

    dmg_at_0 = (np.interp(0.0, DEPTHS, struct_curve) / 100.0) * struct_val + \
               (np.interp(0.0, DEPTHS, cont_curve) / 100.0) * cont_val
    dmg_at_H = (np.interp(H, DEPTHS, struct_curve) / 100.0) * struct_val + \
               (np.interp(H, DEPTHS, cont_curve) / 100.0) * cont_val

    depths = WL - FFE
    base = (_pct_on_curve(struct_curve, depths) / 100.0) * struct_val + \
           (_pct_on_curve(cont_curve, depths) / 100.0) * cont_val
    D[IX_BASE] = base
    D[IX_RAISE] = np.maximum(0.0, base - util_cost)

    if has_basement:
        w = base - dmg_at_0
        w[depths <= 0.0] = 0.0
        D[IX_WFPB] = np.maximum(0.0, w)
    else:
        D[IX_WFPB] = base

    if elev_eligible:
        de = WL - FFE_elevated
        e = (_pct_on_curve(struct_curve, de) / 100.0) * struct_val + \
            (_pct_on_curve(cont_curve, de) / 100.0) * cont_val - dmg_at_0
        e[de <= 0.0] = 0.0
        D[IX_ELEV] = np.maximum(0.0, e)
    else:
        D[IX_ELEV] = base

    if one_story and add_story_ok:
        dc = WL - (FFE + H)
        c = (_pct_on_curve(struct_curve, dc) / 100.0) * struct_val + \
            (_pct_on_curve(cont_curve, dc) / 100.0) * cont_val - dmg_at_0
        c[dc <= 0.0] = 0.0
        D[IX_WFP1] = np.maximum(0.0, c)
    elif one_story:
        D[IX_WFP1] = base
    else:
        w1 = base - dmg_at_H
        w1[depths <= H] = 0.0
        D[IX_WFP1] = np.maximum(0.0, w1)

    if apply_manuf_restriction:
        D[IX_WFPB] = base
        D[IX_WFP1] = base
        if is_manuf_home_flag:
            D[IX_RAISE] = base
    return D


def single_event_damage(occupancy, foundation_type, num_stories, ground_elevation_ft,
                        foundation_height_ft, water_level_ft, action, structure_value,
                        content_value, curves: DamageCurves, bfe_ft, elev_min_shortfall_ft=0.0,
                        flood_zone=FLOOD_ZONE_DEFAULT, util_cost=UTIL_REPL_USD_DEFAULT,
                        story_h_ft=STORY_H_FT_DEFAULT, one_story_add_story=True):
    """Damage of one building for one water level and one action (the FFF
    estimator). `action` is an ACTION_NAMES entry. Returns a dict."""
    soid, fb = nsi_to_soid(occupancy, foundation_type, num_stories)
    sc, cc, sid, cid = curves.get(soid, flood_zone)
    if sc is None:
        return {"soid": soid, "total_usd": np.nan, "error": "no_damage_curve"}
    FFE = float(ground_elevation_ft) + float(foundation_height_ft)
    WL = np.array([[float(water_level_ft)]])
    manuf = is_manuf_or_temp(occupancy)
    D = building_damages(WL, FFE, bfe_ft, story_h_ft, util_cost, structure_value, content_value,
                         sc, cc, manuf, str(foundation_type).strip().upper() == "B",
                         is_manuf_home(occupancy), elev_min_shortfall_ft,
                         is_one_story(num_stories, soid), one_story_add_story and can_add_story(soid))
    ai = ACTION_NAMES.index(action)
    return {"soid": soid, "ffe_ft": FFE, "depth_ft": float(water_level_ft) - FFE,
            "total_usd": float(D[ai, 0, 0]), "baseline_usd": float(D[IX_BASE, 0, 0]),
            "elevation_eligible": bool(FFE < bfe_ft + 2.0 - elev_min_shortfall_ft),
            "occupancy_fallback": fb}


# ---------------------------------------------------------------------------
# Run settings and results
# ---------------------------------------------------------------------------
@dataclass
class RunSettings:
    location: str                                   # slug, e.g. "MasticBeach"
    bfe_ft: float
    elev_min_shortfall_ft: float = 0.0
    restrict_manuf_loc: bool = False                # reporting only (v14: rule is occupancy-driven)
    one_story_add_story: bool = True
    flood_zone: str = FLOOD_ZONE_DEFAULT
    util_repl_usd: float = UTIL_REPL_USD_DEFAULT
    story_h_ft: float = STORY_H_FT_DEFAULT
    target_years: list = field(default_factory=lambda: list(TARGET_YEARS_DEFAULT))
    occupancy_filter: str = "ALL"                   # ALL | RES | NONRES


@dataclass
class RunResult:
    settings: RunSettings
    metadata: pd.DataFrame
    bldg_lookup: pd.DataFrame
    bldg_cumdmg: pd.DataFrame
    categories: pd.DataFrame
    skipped: pd.DataFrame
    occupancy_fallback: pd.DataFrame
    missing_values: pd.DataFrame
    elapsed_s: float
    n_processed: int
    n_res: int
    n_nonres: int
    n_years: int
    n_mc: int
    year_range: tuple
    warnings: list

    def summary_lines(self):
        s = self.settings
        return [
            f"Location {s.location}: {self.n_processed} buildings processed "
            f"({self.n_res} residential, {self.n_nonres} non-residential), "
            f"{len(self.skipped)} skipped, {len(self.occupancy_fallback)} reclassified as RES1, "
            f"{len(self.missing_values)} kept without a damage estimate (no structure or content value).",
            f"Water levels: {self.n_years} years x {self.n_mc} Monte Carlo runs "
            f"({self.year_range[0]}..{self.year_range[1]}); BFE {s.bfe_ft:g} ft, "
            f"DFE {s.bfe_ft + 2:g} ft, elevation offered below {s.bfe_ft + 2 - s.elev_min_shortfall_ft:g} ft.",
            f"Run time {self.elapsed_s:.1f} s ({ENGINE_VERSION}).",
        ]


def _norm_nsi(T: pd.DataFrame) -> pd.DataFrame:
    T = T.copy()
    T.columns = [str(c).strip().lower() for c in T.columns]
    required = ["id", "occupancy_type", "number_of_stories", "foundation_type",
                "foundation_height", "ground_elevation", "structure_value", "content_value"]
    missing = [c for c in required if c not in T.columns]
    if missing:
        raise ValueError(f"NSI inventory is missing required column(s): {', '.join(missing)}")
    for c in ["number_of_stories", "foundation_height", "ground_elevation", "structure_value", "content_value"]:
        T[c] = pd.to_numeric(T[c], errors="coerce")
    return T


def _norm_wl(W: pd.DataFrame, label) -> tuple:
    cols = list(W.columns)
    ycol = next((c for c in cols if str(c).strip().lower() == "year"), None)
    if ycol is None:
        raise ValueError(f"{label} water-level file has no 'Year' column.")
    mc_cols = [c for c in cols if c != ycol]
    years = pd.to_numeric(W[ycol], errors="coerce").to_numpy()
    arr = W[mc_cols].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    if np.isnan(years).any():
        raise ValueError(f"{label} water-level file has non-numeric years.")
    return years.astype(int), arr


def run_location(nsi: pd.DataFrame, wl_p50: pd.DataFrame, wl_p90: pd.DataFrame,
                 curves: DamageCurves, settings: RunSettings, progress=None,
                 nsi_passthrough_cols=("longitude", "latitude", "address", "year_built",
                                       "area", "building_type", "number_of_stories",
                                       "foundation_type", "foundation_height")) -> RunResult:
    """Run the full pipeline for one location. `progress(i, n, msg)` is called
    periodically so a UI can show a bar."""
    t0 = time.time()
    s = settings
    T = _norm_nsi(nsi)
    years, W50 = _norm_wl(wl_p50, "P50")
    years90, W90 = _norm_wl(wl_p90, "P90")
    if len(years) != len(years90) or np.any(years != years90):
        raise ValueError("P50 and P90 water-level files cover different years.")
    if W50.shape[1] != W90.shape[1]:
        raise ValueError("P50 and P90 water-level files have different numbers of Monte Carlo runs.")
    nY, nM = W50.shape
    target_years = [int(y) for y in s.target_years]
    beyond = [y for y in target_years if y > int(years.max())]
    if beyond:
        raise ValueError(f"Water-level series ends at {int(years.max())} but target years include "
                         f"{', '.join(str(y) for y in beyond)}.")
    warnings = []
    if int(years.min()) > Y_NOW:
        warnings.append(f"Water-level series starts at {int(years.min())}, after {Y_NOW}.")

    # Occupancy filter
    occ_up = T["occupancy_type"].astype(str).str.strip().str.upper()
    is_res_all = occ_up.str.startswith("RES")
    if s.occupancy_filter == "RES":
        T = T[is_res_all]
    elif s.occupancy_filter == "NONRES":
        T = T[~is_res_all]
    T = T.reset_index(drop=True)

    WL = {"50th-percentile": W50, "90th-percentile": W90}
    masks = [years <= y for y in target_years]
    DFE = s.bfe_ft + 2.0
    ELIG_MAX = DFE - s.elev_min_shortfall_ft

    cat_labels = ["RES_UnderDFE", "RES_AboveDFE", "NONRES_UnderDFE", "NONRES_AboveDFE"]
    cat_agg = {c: {scn: np.zeros((len(ACTION_NAMES), nY, nM)) for scn in WL} for c in cat_labels}

    look = []
    skipped = []
    fallback = []
    missing_vals = []
    nB = len(T)
    nTY, nAc, nSc, nP = len(masks), len(ACTION_NAMES), len(WL), len(PCT_HDR)
    # Pre-allocated percentile block: one row per (building, year, action, scenario)
    pct_block = np.full((nB * nTY * nAc * nSc, nP), np.nan)
    key_bid, key_year, key_act, key_scn = [], [], [], []
    r_out = 0
    next_report = 0
    for i, r in T.iterrows():
        if progress is not None and (i >= next_report or i == nB - 1):
            progress(i + 1, nB, f"Building {i + 1} of {nB}")
            next_report = i + max(1, nB // 100)
        occ = str(r["occupancy_type"]).strip()
        nst = r["number_of_stories"]
        ft = str(r["foundation_type"]).strip() if pd.notna(r["foundation_type"]) else ""
        fh = r["foundation_height"]
        g = r["ground_elevation"]
        sv = r["structure_value"]
        cv = r["content_value"]
        bid = r["id"]
        if not (np.isfinite(fh) and np.isfinite(g) and np.isfinite(nst) and nst > 0):
            skipped.append((bid, i + 1, occ, "", "invalid_data"))
            continue
        missing_value = not (np.isfinite(sv) and np.isfinite(cv))
        if missing_value:
            # MATLAB produced NaN damage rows for these buildings and the NaN
            # then contaminated the category totals. They stay in the outputs
            # (NaN percentiles, as before, so the inventory count is unchanged)
            # but are left out of the category totals and listed separately.
            missing_vals.append((bid, i + 1, occ, "missing_value"))
        FFE = float(g) + float(fh)
        soid, fb = nsi_to_soid(occ, ft, nst)
        if fb:
            fallback.append((bid, i + 1, occ, soid, "RES1"))
        sc, cc, sid, cid = curves.get(soid, s.flood_zone)
        if sc is None:
            skipped.append((bid, i + 1, occ, soid, "no_damage_curve"))
            continue
        manuf = is_manuf_or_temp(occ)
        mh = is_manuf_home(occ)
        hb = ft.upper() == "B"
        one_story = is_one_story(nst, soid)
        add_ok = s.one_story_add_story and can_add_story(soid)
        dfe_status = "Under DFE" if FFE < DFE else "Above DFE"
        elev_elig = FFE < ELIG_MAX
        is_res = occ.upper().startswith("RES") or fb
        cat = ("RES" if is_res else "NONRES") + ("_UnderDFE" if dfe_status == "Under DFE" else "_AboveDFE")

        for scn, W in WL.items():
            if not missing_value:
                D = building_damages(W, FFE, s.bfe_ft, s.story_h_ft, s.util_repl_usd, float(sv), float(cv),
                                     sc, cc, manuf, hb, mh, s.elev_min_shortfall_ft, one_story, add_ok)
                cat_agg[cat][scn] += D
            for ti, m in enumerate(masks):
                if not missing_value:
                    cum = D[:, m, :].sum(axis=1)                                    # (nAc, nMC)
                    pc = np.percentile(cum, PCT_ARR, axis=1, method="hazen").T     # (nAc, nPct) = MATLAB prctile
                    pct_block[r_out:r_out + nAc, :] = pc
                for a in ACTION_NAMES:
                    key_bid.append(bid); key_year.append(str(target_years[ti])); key_act.append(a); key_scn.append(scn)
                r_out += nAc
        look.append({
            "BuildingID": bid,
            "Longitude": r.get("longitude", np.nan), "Latitude": r.get("latitude", np.nan),
            "OccupancyType": occ, "OccupancyGroup": "RES" if is_res else "NONRES", "SOID": soid,
            "StructureValue": float(sv), "ContentValue": float(cv),
            "GroundElevation_ft": float(g), "FFE_ft": FFE, "DFE_Status": dfe_status,
            "ManufRestriction": int(manuf), "OccupancyFallback": int(fb),
            "ElevationEligible": int(elev_elig),
            "OneStoryAddStory": int(one_story and add_ok and not manuf),
        })

    n_proc = len(look)
    lk = pd.DataFrame(look)
    n_res = int((lk["OccupancyGroup"] == "RES").sum()) if n_proc else 0
    n_nonres = n_proc - n_res

    bc = pd.DataFrame({"BuildingID": key_bid, "TargetYear": key_year, "Action": key_act, "SLR": key_scn})
    for j, h in enumerate(PCT_HDR):
        bc[h] = pct_block[:r_out, j]
    del pct_block

    # Category aggregates: percentiles of the summed MC trajectories
    cat_rows = []
    for c in cat_labels:
        for ti, m in enumerate(masks):
            for ai, a in enumerate(ACTION_NAMES):
                for scn in WL:
                    cum = cat_agg[c][scn][ai][m, :].sum(axis=0)                 # (nMC,)
                    pc = np.percentile(cum, PCT_ARR, method="hazen")
                    cat_rows.append((c, str(target_years[ti]), a, scn, *pc))
    cat_df = pd.DataFrame(cat_rows, columns=["Category", "TargetYear", "Action", "SLR"] + PCT_HDR)

    sk = pd.DataFrame(skipped, columns=["BuildingID", "NSI_row", "OccupancyType", "SOID", "Reason"])
    mv = pd.DataFrame(missing_vals, columns=["BuildingID", "NSI_row", "OccupancyType", "Reason"])
    fb_df = pd.DataFrame(fallback, columns=["BuildingID", "NSI_row", "OriginalOccupancy",
                                            "AssignedSOID", "FallbackOccupancyType"])

    meta = pd.DataFrame({"Key": [
        "LOCATION", "BFE_FT_NAVD88", "nBldgProcessed", "n_res", "n_nonres", "n_occupancy_fallback",
        "TARGET_YEARS", "TARGET_YEAR_LABELS", "ACTIONS", "ACTION_NAMES", "SCENARIOS", "SCENARIO_LABELS",
        "PERCENTILES", "RESTRICT_MANUF_LOC", "ELEV_MIN_SHORTFALL_FT", "ONE_STORY_ADD_STORY",
        "FLOOD_ZONE", "UTIL_REPL_USD", "STORY_H_FT", "OCCUPANCY_FILTER", "ENGINE", "RUN_DATE",
        "N_MC", "MC_YEARS", "n_missing_value"],
        "Value": [
        s.location, f"{s.bfe_ft:g}", str(n_proc), str(n_res), str(n_nonres), str(len(fallback)),
        ", ".join(str(y) for y in target_years), ", ".join(str(y) for y in target_years),
        ", ".join(str(a) for a in ACTION_CODES), ", ".join(ACTION_NAMES), ", ".join(SCENARIOS),
        ", ".join(SCENARIO_LABELS), ", ".join(PCT_HDR), str(int(bool(s.restrict_manuf_loc))),
        f"{s.elev_min_shortfall_ft:g}", str(int(bool(s.one_story_add_story))),
        s.flood_zone, f"{s.util_repl_usd:g}", f"{s.story_h_ft:g}", s.occupancy_filter, ENGINE_VERSION,
        _dt.datetime.now().strftime("%Y-%m-%d %H:%M"), str(nM), f"{int(years.min())}-{int(years.max())}",
        str(len(missing_vals))]})

    warnings.extend(curves.warnings)
    return RunResult(settings=s, metadata=meta, bldg_lookup=lk, bldg_cumdmg=bc, categories=cat_df,
                     skipped=sk, occupancy_fallback=fb_df, missing_values=mv, elapsed_s=time.time() - t0,
                     n_processed=n_proc, n_res=n_res, n_nonres=n_nonres, n_years=nY, n_mc=nM,
                     year_range=(int(years.min()), int(years.max())), warnings=sorted(set(warnings)))


def bundle_files(result: RunResult, slug: str) -> dict:
    """Map bundle file name -> CSV text (the files the app reads)."""
    out = {
        f"{slug}_metadata.csv": result.metadata.to_csv(index=False, quoting=2),
        f"{slug}_bldg_lookup.csv": result.bldg_lookup.to_csv(index=False),
        f"{slug}_bldg_CumulativeDamage.csv": result.bldg_cumdmg.to_csv(index=False, float_format="%.2f"),
        f"{slug}_CumulativeDamage_categories.csv": result.categories.to_csv(index=False, float_format="%.2f"),
        f"{slug}_skipped_buildings.csv": result.skipped.to_csv(index=False),
    }
    if len(result.occupancy_fallback):
        out[f"{slug}_occupancy_fallback.csv"] = result.occupancy_fallback.to_csv(index=False)
    if len(result.missing_values):
        out[f"{slug}_missing_values.csv"] = result.missing_values.to_csv(index=False)
    return out


def write_bundle(result: RunResult, folder: str, slug: str) -> list:
    os.makedirs(folder, exist_ok=True)
    written = []
    for name, text in bundle_files(result, slug).items():
        p = os.path.join(folder, name)
        with open(p, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        written.append(p)
    return written
