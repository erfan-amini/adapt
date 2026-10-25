"""
ADAPT - Assessment of Damage and Adaptation Planning Tool
Center for Climate Systems Research, The Climate School, Columbia University

Entry point. Three audience modules behind a landing page:
    Community Members  - dynamic maps, my building
    Decision Makers    - community overview, flood maps, road maps, damage distributions
    Developer          - data and settings, NSI survey tool, fragility curves, run the model
"""
import os

import streamlit as st

from adapt_core import config as C
from adapt_core import ui
from views import _context as X

st.set_page_config(
    page_title="ADAPT | Flood Risk Tool",
    page_icon="🌊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Page registry
# ---------------------------------------------------------------------------
from views import home as _home
from views.community import dynamic_maps as _dyn, my_building as _mine
from views.planners import overview as _ovw, flood_maps as _fld, road_maps as _rds, distributions as _dst
from views.developer import data as _dev_data, nsi_survey as _dev_nsi, curves as _dev_curves, run_model as _dev_run

# key, module, title, icon, url_path, callable
PAGES = [
    ("home",        None,        "Home",                  "🏠", "home",          _home.render),
    ("dyn_maps",    "community", "Dynamic maps",          "🗺️", "maps",          _dyn.render),
    ("my_building", "community", "My building",           "🏡", "my-building",   _mine.render),
    ("overview",    "planners",  "Community overview",    "📊", "overview",      _ovw.render),
    ("flood_maps",  "planners",  "Flood maps",            "🌊", "flood-maps",    _fld.render),
    ("road_maps",   "planners",  "Road maps",             "🛣️", "road-maps",     _rds.render),
    ("dist",        "planners",  "Damage distributions",  "📦", "distributions", _dst.render),
    ("dev_data",    "developer", "Data and settings",     "🗄️", "dev-data",      _dev_data.render),
    ("dev_nsi",     "developer", "NSI survey tool",       "📍", "dev-nsi",       _dev_nsi.render),
    ("dev_curves",  "developer", "Fragility curves",      "📈", "dev-curves",    _dev_curves.render),
    ("dev_run",     "developer", "Run the model",         "▶️", "dev-run",       _dev_run.render),
]
MODULE_PAGE_KEYS = {m: [p[0] for p in PAGES if p[1] == m] for m in C.MODULES}
FIRST_PAGE = {m: keys[0] for m, keys in MODULE_PAGE_KEYS.items()}


def _make_page(key, module, title, icon, url_path, fn):
    def _page():
        _run_page(key, module, title, fn)
    _page.__name__ = f"page_{key}"
    if key == "home":
        # The default page is always served at the root URL.
        return st.Page(_page, title=title, icon=icon, default=True)
    return st.Page(_page, title=title, icon=icon, url_path=url_path)


ST_PAGES = {p[0]: _make_page(*p) for p in PAGES}


# ---------------------------------------------------------------------------
# Sidebar (module pages only; the landing page hides it)
# ---------------------------------------------------------------------------
def _sidebar(module, available_locations, registry):
    with st.sidebar:
        ui.sidebar_brand()
        st.page_link(ST_PAGES["home"], label="Home", icon="🏠")
        if available_locations:
            X.location_selector(available_locations, registry, key="w_location_side")
        m = C.MODULES[module]
        st.markdown(f"<div class='adapt-module-tag'>{m['icon']} {m['title']}</div>", unsafe_allow_html=True)
        for key in MODULE_PAGE_KEYS[module]:
            p = next(p for p in PAGES if p[0] == key)
            st.page_link(ST_PAGES[key], label=p[2], icon=p[3])
        st.divider()
        st.markdown("<div class='adapt-side-note'>Other modules</div>", unsafe_allow_html=True)
        for k in C.MODULES:
            if k != module:
                st.page_link(ST_PAGES[FIRST_PAGE[k]], label=C.MODULES[k]["title"], icon=C.MODULES[k]["icon"])
        st.divider()
        st.markdown("<div class='adapt-side-note'>Center for Climate Systems Research<br>"
                    "The Climate School, Columbia University</div>", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Page runner
# ---------------------------------------------------------------------------
def _run_page(key, module, title, fn):
    accent, accent_dark = ui.module_accent(module) if module else (C.SKY, C.SKY_DARK)
    ui.inject_css(accent, accent_dark)
    registry = ui.load_location_registry()
    data_store, available_locations = X.get_data_store()
    location = X.ensure_location_state(available_locations, registry)

    if module is None:                       # landing page: no sidebar
        ui.hide_sidebar()
        ctx = X.build_context(data_store, available_locations, registry)
        ctx.pages, ctx.module, ctx.page_key, ctx.first_page = ST_PAGES, None, key, FIRST_PAGE
        fn(ctx)
        ui.footer()
        return

    _sidebar(module, available_locations, registry)

    # Protected locations (tribal partner data) need the access password on
    # every module. The Developer module is open unless a developer password
    # is configured in secrets.
    cfg = ui.location_cfg(registry, location) if location else None
    if location and not ui.location_gate(location, cfg):
        ui.footer()
        return
    if module == "developer" and not ui.developer_gate():
        ui.footer()
        return

    ctx = X.build_context(data_store, available_locations, registry)
    ctx.pages, ctx.module, ctx.page_key, ctx.first_page = ST_PAGES, module, key, FIRST_PAGE
    fn(ctx)
    ui.footer()


pg = st.navigation(list(ST_PAGES.values()), position="hidden")
pg.run()
