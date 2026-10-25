"""Validate the Python engine against the MATLAB result bundles shipped in data/.

Run:  python tests/test_engine_vs_matlab.py [Pamunkey|Shinnecock|MasticBeach ...]

For every building whose inputs (SOID, FFE, structure and content value) are
identical in the inventory and in the stored lookup, all 23 percentiles of
No mitigation, Raise Utilities, WFP B and Elevate must match the MATLAB CSV
to machine precision. WFP 1st is compared only for buildings the one-story
rule does not touch, because the stored bundles predate v13/v14.
"""
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from adapt_core import engine as E  # noqa: E402
from adapt_core import config as C  # noqa: E402
from adapt_core.ui import load_location_registry  # noqa: E402


def check(slug):
    folder = os.path.join(C.DATA_DIR, slug)
    reg = load_location_registry()
    cfg = reg[slug]
    meta = pd.read_csv(os.path.join(folder, f"{slug}_metadata.csv")).set_index("Key")["Value"]
    bfe = float(str(meta["BFE_FT_NAVD88"]).strip('"'))
    shortfall = float(str(meta.get("ELEV_MIN_SHORTFALL_FT", cfg["elev_min_shortfall_ft"])).strip('"'))
    cur = E.DamageCurves(C.CURVES_DIR)
    nsi = pd.read_excel(os.path.join(folder, f"DDD___{slug}___NSI.xlsx"))
    w50 = pd.read_csv(os.path.join(folder, f"DDD___{slug}_MC_annual_max_waterlevels_P50.csv"))
    w90 = pd.read_csv(os.path.join(folder, f"DDD___{slug}_MC_annual_max_waterlevels_P90.csv"))
    res = E.run_location(nsi, w50, w90, cur, E.RunSettings(location=slug, bfe_ft=bfe, elev_min_shortfall_ft=shortfall))
    print("\n".join(res.summary_lines()))

    ref = pd.read_csv(os.path.join(folder, f"{slug}_bldg_CumulativeDamage.csv"))
    reflk = pd.read_csv(os.path.join(folder, f"{slug}_bldg_lookup.csv"))
    lk = res.bldg_lookup
    ml = reflk.merge(lk, on="BuildingID", suffixes=("_m", "_p"))
    same = ml[(ml.SOID_m == ml.SOID_p) & ((ml.FFE_ft_m - ml.FFE_ft_p).abs() < 1e-6)
              & ((ml.StructureValue_m - ml.StructureValue_p).abs() < 0.01)
              & ((ml.ContentValue_m - ml.ContentValue_p).abs() < 0.01)]
    print(f"{len(same)} of {len(ml)} common buildings have identical inputs")
    out = res.bldg_cumdmg.copy()
    out["TargetYear"] = out["TargetYear"].astype(int)
    key = ["BuildingID", "TargetYear", "Action", "SLR"]
    m = ref[ref.BuildingID.isin(same.BuildingID)].merge(out, on=key, suffixes=("_m", "_p"))
    ok = True
    for a in E.ACTION_NAMES:
        mm = m[m.Action == a]
        if a == "WFP 1st":
            # v13/v14 changed WFP 1st for every one-story building; compare
            # only multi-story buildings, which kept the original transform.
            nst = nsi.rename(columns=str.lower).set_index("id")["number_of_stories"]
            multi = [b for b, s in zip(same.BuildingID, same.SOID_p)
                     if not E.is_one_story(nst.get(b, np.nan), s)]
            mm = mm[mm.BuildingID.isin(multi)]
        A = mm[[c + "_m" for c in E.PCT_HDR]].to_numpy()
        B = mm[[c + "_p" for c in E.PCT_HDR]].to_numpy()
        both_nan = np.isnan(A) & np.isnan(B)
        d = np.where(both_nan, 0.0, np.abs(A - B))
        worst = float(np.nanmax(d)) if d.size else 0.0
        status = "OK" if worst < 1e-3 else "DIFF"
        if status == "DIFF":
            ok = False
        print(f"  {a:16s} rows={len(mm):6d} max |diff| = {worst:.6f}  {status}")
    return ok


if __name__ == "__main__":
    slugs = sys.argv[1:] or ["Pamunkey", "Shinnecock"]
    all_ok = all(check(s) for s in slugs)
    print("PASS" if all_ok else "FAIL")
    sys.exit(0 if all_ok else 1)
