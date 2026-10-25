"""Landing page: choose a location, then a module."""
import html as _html

import streamlit as st

from adapt_core import config as C
from adapt_core import ui
from adapt_core.helpers import fmt_money_short
from views import _context as X


def _site_stats(ctx):
    """A few headline numbers for the selected location."""
    entry = ctx.loc_entry or {}
    attrs = entry.get("bldg_attrs")
    meta = entry.get("metadata", {})
    n = int(len(attrs)) if attrs is not None else 0
    n_res = int(attrs["occupancy_type"].apply(lambda o: str(o).upper().startswith("RES")).sum()) if n else 0
    under = int((attrs["DFE_Status"] == "Under DFE").sum()) if (n and "DFE_Status" in attrs.columns) else None
    bfe = meta.get("BFE_FT_NAVD88")
    total_value = float(attrs["structure_value"].sum()) if (n and "structure_value" in attrs.columns) else None
    return n, n_res, under, bfe, total_value


def render(ctx):
    ss = ctx.ss
    st.markdown(
        "<div class='adapt-header'><div class='eyebrow'><span>Assessment of Damage and Adaptation Planning Tool</span></div>"
        "<h1>Building-level flood damage and adaptation planning under sea-level rise</h1>"
        "<p class='lede'>ADAPT estimates flood damage to every building in a community for today and for "
        "future sea levels, and compares what each adaptation measure would save. Choose a location, "
        "then open the module written for you.</p></div>",
        unsafe_allow_html=True,
    )

    if not ctx.available_locations:
        st.error("No location data found. Add a results bundle under `data/<Location>/` or run the model "
                 "from the Developer module.")
        return

    c1, c2 = st.columns([0.38, 0.62])
    with c1:
        X.location_selector(ctx.available_locations, ctx.registry, key="w_location_home",
                            label="📍 Location",
                            help="The location is remembered on every page. You can change it in the sidebar at any time.")
        cfg = ui.location_cfg(ctx.registry, ss.cv_location) or {}
        st.checkbox(
            "Mobile-homes-dominated area",
            value=bool(ss.get("cv_mobile", False)), key="w_mobile_home",
            on_change=lambda: ss.__setitem__("cv_mobile", ss.w_mobile_home),
            help="When on, the adaptation comparison considers only raising (elevating) homes, the realistic "
                 "retrofit for manufactured housing, against the no-mitigation baseline. Default follows the location.",
        )
    with c2:
        n, n_res, under, bfe, total_value = _site_stats(ctx)
        ui.note(f"<b>📍 {_html.escape(str(ss.cv_location))}</b> &nbsp;·&nbsp; {ctx._location_blurb(ss.cv_location)}")
        k1, k2, k3, k4 = st.columns(4)
        with k1:
            ui.kpi("Buildings", f"{n:,}", f"{n_res:,} residential")
        with k2:
            ui.kpi("Under DFE", f"{under:,}" if under is not None else "n/a",
                   "first floor below BFE + 2 ft" if under is not None else None)
        with k3:
            ui.kpi("Base flood elevation", f"{float(bfe):g} ft" if bfe not in (None, "") else "n/a", "NAVD88")
        with k4:
            ui.kpi("Structure value", fmt_money_short(total_value) if total_value else "n/a", "replacement cost, all buildings")

    st.markdown("<div style='height:0.6rem'></div>", unsafe_allow_html=True)
    st.markdown("#### Choose your module")

    cards = st.columns(3)
    bullets = {
        "community": ["Dynamic maps of damage, adaptation effectiveness and flood frequency",
                      "Pick your building: damage over time and what each retrofit saves",
                      "Flood depth at your door for chosen flood levels"],
        "planners": ["Community-wide damage and adaptation strategy comparison",
                     "Flood inundation maps for planning horizons",
                     "Road flooding and access, damage distributions across the building stock"],
        "developer": ["Building inventories, water levels and per-location settings",
                      "NSI field survey tool and FEMA/Hazus depth-damage curves",
                      "Run the damage model and publish new results"],
    }
    for col, (key, m) in zip(cards, C.MODULES.items()):
        with col:
            body = f"<div class='sub'>{_html.escape(m['subtitle'])}</div><p>{_html.escape(m['blurb'])}</p><ul>" + \
                   "".join(f"<li>{_html.escape(b)}</li>" for b in bullets[key]) + "</ul>"
            st.markdown(
                f"<div class='adapt-card' style='border-top:4px solid {m['accent']}'>"
                f"<div class='icon'>{m['icon']}</div><h3>{_html.escape(m['title'])}</h3>{body}</div>",
                unsafe_allow_html=True,
            )
            first_key = {"community": "dyn_maps", "planners": "overview", "developer": "dev_data"}[key]
            label = "Open" if key != "developer" else "Open (password)"
            st.page_link(ctx.pages[first_key], label=f"{label} →", icon=None, use_container_width=True)

    st.markdown("<div style='height:0.8rem'></div>", unsafe_allow_html=True)
    with st.expander("How the numbers are produced"):
        st.markdown(
            "- **Building inventory.** The National Structure Inventory (NSI), corrected with field surveys, "
            "gives each building's occupancy, stories, foundation, first-floor elevation and replacement value.\n"
            "- **Water levels.** 1,000 Monte Carlo trajectories of annual maximum water level per year from 2025 to 2100, "
            "for median (50th percentile) and high-end (90th percentile) sea-level rise.\n"
            "- **Damage.** FEMA/Hazus depth-damage curves convert the water depth above the first floor into "
            "structure and content damage. Damage is accumulated year by year to each target year.\n"
            "- **Adaptation.** Elevating utilities, wet-floodproofing the basement or the first floor, and "
            "elevating the building are modelled as transformations of the same curves, with physical "
            "eligibility rules (a basement must exist, elevation only below the design flood elevation)."
        )
