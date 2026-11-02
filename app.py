"""
ADAPT - Assessment of Damage and Adaptation Planning Tool
Center for Climate Systems Research, The Climate School, Columbia University

Entry point. Three audience modules behind a landing page:
    Households and Businesses - my home (address report), neighborhood maps
    Community Planning        - community overview (summary, trends, damage distributions),
                                dynamic maps (damage, adaptation, flood occurrences, flood and
                                road maps), building-specific reports
    Developer                 - data and settings, NSI survey tool, fragility curves, run the model

The pages are registered with st.navigation before anything heavy is
imported: each view module (pandas, plotly, terrain, ...) is imported only
when its page runs. Otherwise a fresh visit to a page URL such as /maps
reaches the browser before the page list does, and Streamlit shows a
"Page not found" dialog.
"""
import importlib

import streamlit as st

from adapt_core import config as C     # light: paths and constants only

st.set_page_config(
    page_title="ADAPT | Flood Risk Tool",
    page_icon="🌊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Page registry: key, module, title, icon, url_path, view module
# ---------------------------------------------------------------------------
PAGES = [
    ("home",        None,        "Home",                  "🏠", "home",          "views.home"),
    ("my_home",     "community", "My home",               "🏡", "my-home",       "views.community.my_home"),
    ("hex_maps",    "community", "Neighborhood maps",     "🔷", "neighborhood",  "views.community.neighborhood"),
    ("overview",    "planners",  "Community overview",    "📊", "overview",      "views.planners.overview"),
    ("dyn_maps",    "planners",  "Dynamic maps",          "🗺️", "maps",          "views.planners.dynamic_maps"),
    ("my_building", "planners",  "Building-specific reports", "🏠", "building-reports", "views.planners.my_building"),
    ("dev_data",    "developer", "Data and settings",     "🗄️", "dev-data",      "views.developer.data"),
    ("dev_nsi",     "developer", "NSI survey tool",       "📍", "dev-nsi",       "views.developer.nsi_survey"),
    ("dev_curves",  "developer", "Fragility curves",      "📈", "dev-curves",    "views.developer.curves"),
    ("dev_run",     "developer", "Run the model",         "▶️", "dev-run",       "views.developer.run_model"),
]
MODULE_PAGE_KEYS = {m: [p[0] for p in PAGES if p[1] == m] for m in C.MODULES}
FIRST_PAGE = {m: keys[0] for m, keys in MODULE_PAGE_KEYS.items()}


def _make_page(key, module, title, icon, url_path, view_module):
    def _page():
        fn = importlib.import_module(view_module).render
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
    from adapt_core import ui
    from views import _context as X
    with st.sidebar:
        ui.sidebar_brand(ST_PAGES["home"])      # the logo and name link to the landing page
        settings_slot = None
        if module == "planners" and available_locations:
            # Community Planning: the location full width, then target year
            # and SLR scenario side by side in one compact row. The two
            # slots are filled once the data is loaded (see _run_page).
            # Occupancy lives under Advanced options on the Dynamic maps page.
            with st.container(key="side_grid"):
                X.location_selector(available_locations, registry, key="w_location_side")
                r2 = st.columns(2, gap="small")
            settings_slot = {"year": r2[0], "scn": r2[1]}
        elif available_locations:
            X.location_selector(available_locations, registry, key="w_location_side")
        m = C.MODULES[module]
        st.markdown(f"<div class='adapt-module-tag'>{m['icon']} {m['title']}</div>", unsafe_allow_html=True)
        for key in MODULE_PAGE_KEYS[module]:
            p = next(p for p in PAGES if p[0] == key)
            st.page_link(ST_PAGES[key], label=p[2], icon=p[3])
        st.divider()
    return settings_slot


def _sidebar_tail(module, ctx, title):
    """After the page context exists: the assistant panel, then the other
    module(s) at the very bottom, then the credit line. Developer is reached
    from the landing page only, so it is not listed here."""
    from adapt_core import assistant
    with st.sidebar:
        assistant.sidebar_panel(module, ctx, title)
        st.markdown("<div class='adapt-side-note'>Center for Climate Systems Research<br>"
                    "The Climate School, Columbia University</div>", unsafe_allow_html=True)
        others = [k for k in C.MODULES if k != module and k != "developer"]
        if others:
            # Pinned to the bottom of the sidebar (see .st-key-side_switch in ui.py).
            with st.container(key="side_switch"):
                st.divider()
                st.markdown("<div class='adapt-side-note'>Switch to</div>", unsafe_allow_html=True)
                for k in others:
                    st.page_link(ST_PAGES[FIRST_PAGE[k]], label=C.MODULES[k]["title"], icon=C.MODULES[k]["icon"])


# ---------------------------------------------------------------------------
# Page runner
# ---------------------------------------------------------------------------
def _run_page(key, module, title, fn):
    from adapt_core import ui
    from views import _context as X

    accent, accent_dark = ui.module_accent(module) if module else (C.SKY, C.SKY_DARK)
    ui.inject_css(accent, accent_dark)
    ui.dismiss_first_load_page_not_found()
    registry = ui.load_location_registry()
    data_store, available_locations = X.get_data_store()
    location = X.ensure_location_state(available_locations, registry)

    if module is None:                       # landing page: no sidebar
        ui.hide_sidebar()
        st.session_state["_settings_in_sidebar"] = False
        ctx = X.build_context(data_store, available_locations, registry)
        ctx.pages, ctx.module, ctx.page_key, ctx.first_page = ST_PAGES, None, key, FIRST_PAGE
        fn(ctx)
        ui.footer()
        return

    # One password, asked once on the landing page, opens the whole app.
    # A module page opened directly while locked goes back to the landing page.
    if ui.any_protected(registry) and not ui.app_unlocked():
        st.switch_page(ST_PAGES["home"])

    settings_slot = _sidebar(module, available_locations, registry)
    st.session_state["_settings_in_sidebar"] = (module == "planners")

    # Per-location gate: passes once the app password is in. The Developer
    # module also asks for a developer password if one is set in secrets.
    cfg = ui.location_cfg(registry, location) if location else None
    if location and not ui.location_gate(location, cfg):
        _sidebar_tail(module, None, title)
        ui.footer()
        return
    if module == "developer" and not ui.developer_gate():
        _sidebar_tail(module, None, title)
        ui.footer()
        return

    ctx = X.build_context(data_store, available_locations, registry)
    ctx.pages, ctx.module, ctx.page_key, ctx.first_page = ST_PAGES, module, key, FIRST_PAGE
    if module == "planners" and settings_slot:
        ctx.render_sidebar_settings(settings_slot)
    _sidebar_tail(module, ctx, title)
    fn(ctx)
    ui.footer()


pg = st.navigation(list(ST_PAGES.values()), position="hidden")
pg.run()
