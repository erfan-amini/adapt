"""Developer: embedded NSI field-survey tool (React/Leaflet, Google Sheets backend)."""
import os
import re as _re

import streamlit as st
import streamlit.components.v1 as _components

from adapt_core import config as C
from adapt_core import ui


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
    slug = _re.sub(r"[^a-z0-9]", "", str(ctx.selected_location or "").lower())
    inject = '<script>window.__ADAPT_LOCATION = "%s";</script>\n' % slug
    anchor = '<script type="text/babel"'
    html = html.replace(anchor, inject + anchor, 1)
    with ctx._keyed_container("nsi_embed"):
        _components.html(html, height=900, scrolling=False)
