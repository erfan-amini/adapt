"""Developer: embedded NSI field-survey tool (React/Leaflet, Google Sheets backend)."""
import json as _json
import os
import re as _re

import pandas as pd
import streamlit as st
import streamlit.components.v1 as _components

from adapt_core import config as C
from adapt_core import ui

SURVEY_DATA_DIR = os.path.join(C.DATA_DIR, "_survey")


@st.cache_data(show_spinner=False)
def _read_ground_3dep(path, mtime):
    df = pd.read_csv(path, usecols=["uid", "ground_3dep_ft"]).dropna()
    return {str(u): round(float(v), 2) for u, v in zip(df["uid"], df["ground_3dep_ft"])}


def _ground_3dep(slug):
    """{uid: 3DEP ground elevation (ft NAVD88)} for a survey location, or {}."""
    path = os.path.join(SURVEY_DATA_DIR, f"{slug}_ground_3dep.csv")
    if not slug or not os.path.exists(path):
        return {}
    try:
        return _read_ground_3dep(path, os.path.getmtime(path))
    except Exception:
        return {}


def render(ctx):
    ui.page_header(
        "NSI survey tool",
        "Walk the map to add, verify, move or flag buildings and record their structural attributes. "
        "Edits sync to the shared Google Sheet backend. Use <b>Data and settings → Pull from Google Sheet</b> "
        "to bring the verified rows into the inventory the model reads.",
        module_key="developer", location=ctx.location_name,
    )
    st.markdown(
        "<style>"
        "section[data-testid='stMain'] .block-container,[data-testid='stMainBlockContainer']{padding-bottom:0;}"
        ".st-key-nsi_embed [data-testid='stCustomComponentV1'],.st-key-nsi_embed [data-testid='stIFrame'],"
        ".st-key-nsi_embed iframe{height:calc(100vh - 260px)!important;min-height:560px!important;"
        "width:100%!important;border:none!important;display:block;border-radius:12px;}"
        "</style>",
        unsafe_allow_html=True,
    )
    if not os.path.exists(C.NSI_TOOL_HTML):
        st.error("`assets/nsi_tool.html` was not found in the deployment.")
        return
    with open(C.NSI_TOOL_HTML, "r", encoding="utf-8") as fh:
        html = fh.read()
    # Pass the global location into the embedded tool (it keys locations by
    # slug: 'Mastic Beach' -> 'masticbeach'). The plain <script> runs before
    # Babel transpiles the app, so window.__ADAPT_LOCATION is set in time.
    cfg = ui.location_cfg(ctx.registry, ctx.selected_location) or {}
    slug = cfg.get("survey_key") or _re.sub(r"[^a-z0-9]", "", str(ctx.selected_location or "").lower())
    inject = '<script>window.__ADAPT_LOCATION = "%s";</script>\n' % slug
    # USGS 3DEP ground elevation per building (data/_survey/<slug>_ground_3dep.csv).
    # When present, the tool flags ground elevations that are placeholders or
    # sit well below 3DEP.
    dem = _ground_3dep(slug)
    if dem:
        payload = _json.dumps({"loc": slug, "ft": dem}, separators=(",", ":")).replace("</", "<\\/")
        inject += "<script>window.__ADAPT_GROUND_3DEP = %s;</script>\n" % payload
        st.caption(
            "Ground elevation flags compare each building's ground elevation with USGS 3DEP "
            "(1 m bare-earth lidar). Red: a placeholder value (0, 0.33 or 3.82 ft). Purple: more than "
            "4 ft below 3DEP. Yellow: 2 to 4 ft below 3DEP. The building panel shows the 3DEP value."
        )
    anchor = '<script type="text/babel"'
    html = html.replace(anchor, inject + anchor, 1)
    with ctx._keyed_container("nsi_embed"):
        _components.html(html, height=900, scrolling=False)
