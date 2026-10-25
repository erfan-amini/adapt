"""Developer: FEMA/Hazus depth-damage curves and the SOID -> curve mapping in use."""
import os

import pandas as pd
import streamlit as st

from adapt_core import config as C
from adapt_core import ui
from adapt_core.engine import DamageCurves, nsi_to_soid


@st.cache_resource(show_spinner=False)
def _curves():
    return DamageCurves(C.CURVES_DIR)


def render(ctx):
    ui.page_header(
        "Fragility curves",
        "The FEMA/Hazus FAST depth-damage curves behind the damage model. Pick an occupancy, flood zone and "
        "basement condition to overlay the structure and content curves for each number of stories. The second "
        "section lists which curve every building type in the selected location is scored with.",
        module_key="developer", location=ctx.location_name,
    )
    ctx.render_fragility_curves(building_row=None, ctx="frag_main")

    st.markdown("---")
    st.subheader(f"Curves assigned to the buildings in {ctx.location_name}")
    attrs = (ctx.loc_entry or {}).get("bldg_attrs")
    if attrs is None or attrs.empty:
        st.info("No building inventory loaded for this location.")
        return
    if not all(os.path.exists(os.path.join(C.CURVES_DIR, f)) for f in
               ("flBldgStructDmgFinal.csv", "flBldgStructDmgFn.csv", "flBldgContDmgFinal.csv", "flBldgContDmgFn.csv")):
        st.warning(f"Curve tables not found in `{os.path.relpath(C.CURVES_DIR, C.APP_ROOT)}`.")
        return
    cur = _curves()
    rows = []
    g = attrs.copy()
    g["occupancy_type"] = g["occupancy_type"].astype(str)
    if "SOID" not in g.columns:
        g["SOID"] = [nsi_to_soid(o, f, n)[0] for o, f, n in
                     zip(g["occupancy_type"], g.get("foundation_type", ""), g.get("number_of_stories", 1))]
    for (occ, soid), sub in g.groupby(["occupancy_type", "SOID"]):
        sc, cc, sid, cid = cur.get(soid, C.FLOOD_ZONE_DEFAULT)
        sdesc = cur.sfn.loc[cur.sfn.BldgDmgFnId == sid, "Description"].iloc[0] if sid is not None and (cur.sfn.BldgDmgFnId == sid).any() else ""
        cdesc = cur.cfn.loc[cur.cfn.ContDmgFnId == cid, "Description"].iloc[0] if cid is not None and (cur.cfn.ContDmgFnId == cid).any() else ""
        rows.append({"Occupancy": occ, "SOID": soid, "Buildings": int(len(sub)),
                     "Structure curve": f"{sid} · {sdesc}" if sid is not None else "none",
                     "Content curve": f"{cid} · {cdesc}" if cid is not None else "none (zeroed)"})
    tbl = pd.DataFrame(rows).sort_values("Buildings", ascending=False)
    st.dataframe(tbl, use_container_width=True, hide_index=True)
    n_none = int((tbl["Structure curve"] == "none").sum())
    if n_none:
        st.warning(f"{n_none} building type(s) have no structure curve and are skipped by the model.")
    st.caption(f"Flood zone {C.FLOOD_ZONE_DEFAULT} (HazardCA column). Curve tables: "
               f"`{os.path.relpath(C.CURVES_DIR, C.APP_ROOT)}/`.")
