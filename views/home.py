"""Landing page: one hero, the location, three doors."""
import html as _html

import streamlit as st

from adapt_core import config as C
from adapt_core import ui
from views import _context as X

_CHIPS = {
    "community": ["Dynamic maps", "My building", "Flood depth at my door"],
    "planners": ["Community overview", "Flood maps", "Road maps", "Damage distributions"],
    "developer": ["Inventories and settings", "Survey tool", "Depth-damage curves", "Run the model"],
}
_WHO = {
    "community": "Residents, businesses and property owners",
    "planners": "Planners, agencies and community leaders",
    "developer": "The project team",
}
_LEDE = {
    "community": "Which buildings flood, how often, and what each adaptation option would save for a home or business like yours.",
    "planners": "Community-wide damage, flood and road maps under sea-level rise, and how damage is spread across the building stock.",
    "developer": "Building inventories, water levels and settings, the curves behind the model, and the model itself.",
}


def render(ctx):
    ss = ctx.ss
    n_loc = len(ctx.available_locations)
    st.markdown(
        "<div class='adapt-hero'>"
        "<div class='kicker'>ADAPT · Assessment of Damage and Adaptation Planning Tool</div>"
        "<h1>Flood damage and adaptation planning, one building at a time.</h1>"
        "<p>ADAPT estimates flood damage to every building in a coastal community for today and for "
        "future sea levels, and compares what each adaptation measure would save. Pick a community, "
        "then open the module written for you.</p>"
        "<div class='meta'><span><b>1,000</b> sea-level rise futures per building</span>"
        "<span><b>FEMA/Hazus</b> depth-damage curves</span>"
        f"<span><b>{n_loc}</b> communities</span>"
        "<span>Center for Climate Systems Research, Columbia University</span></div>"
        "</div>",
        unsafe_allow_html=True,
    )

    if not ctx.available_locations:
        st.error("No location data found. Add a results bundle under `data/<Location>/` or run the model "
                 "from the Developer module.")
        return

    st.markdown("<div class='adapt-section'>Community</div>", unsafe_allow_html=True)
    c1, c2 = st.columns([0.42, 0.58], vertical_alignment="top")
    with c1:
        X.location_selector(ctx.available_locations, ctx.registry, key="w_location_home", label="Location",
                            help="Remembered on every page; change it any time from the sidebar.")
    cfg = ui.location_cfg(ctx.registry, ss.cv_location) or {}
    with c2:
        if ui.is_protected(cfg) and not ui.location_unlocked(ss.cv_location):
            st.markdown("<span class='adapt-lock'>🔒 Password protected: results for this community are "
                        "shared with its partners.</span>", unsafe_allow_html=True)
            ui.location_gate(ss.cv_location, cfg, compact=True)
        else:
            st.markdown(f"<p style='color:#475569; font-size:0.98rem; line-height:1.55; margin:0.35rem 0 0 0;'>"
                        f"{_html.escape(ctx._location_blurb(ss.cv_location))}</p>", unsafe_allow_html=True)

    st.markdown("<div class='adapt-section'>Choose your module</div>", unsafe_allow_html=True)
    cols = st.columns(3, gap="medium")
    for col, (key, m) in zip(cols, C.MODULES.items()):
        with col:
            chips = "".join(f"<span class='chip'>{_html.escape(c)}</span>" for c in _CHIPS[key])
            st.markdown(
                f"<div class='adapt-module'>"
                f"<div class='icon' style='background:{m['accent']}22; color:{m['accent']}'>{m['icon']}</div>"
                f"<h3>{_html.escape(m['title'])}</h3>"
                f"<div class='who'>{_html.escape(_WHO[key])}</div>"
                f"<p>{_html.escape(_LEDE[key])}</p>"
                f"<div class='chips'>{chips}</div></div>",
                unsafe_allow_html=True,
            )
            with ctx._keyed_container(f"go_{key}"):
                st.page_link(ctx.pages[ctx.first_page[key]], label=f"Open {m['title']}", use_container_width=True)

    with st.expander("How the numbers are produced", expanded=False):
        st.markdown(
            "- **Building inventory.** The National Structure Inventory (NSI), corrected with field surveys, "
            "gives each building's occupancy, stories, foundation, first-floor elevation and replacement value.\n"
            "- **Water levels.** 1,000 Monte Carlo trajectories of the annual maximum water level for every year "
            "from 2025 to 2100, for median (50th percentile) and high-end (90th percentile) sea-level rise.\n"
            "- **Damage.** FEMA/Hazus depth-damage curves convert the water depth above the first floor into "
            "structure and content damage, accumulated year by year to each target year.\n"
            "- **Adaptation.** Elevating utilities, wet-floodproofing the basement or the first floor, and "
            "elevating the building are modelled as transformations of the same curves, with physical "
            "eligibility rules (a basement must exist; elevation only below the design flood elevation)."
        )
