"""Landing page: one screen. A hero with the ADAPT mark, the community, three doors."""
import html as _html
import os

import streamlit as st

from adapt_core import config as C
from adapt_core import ui
from adapt_core.helpers import fmt_money_short
from views import _context as X



def _site_line(ctx):
    entry = ctx.loc_entry or {}
    attrs = entry.get("bldg_attrs")
    meta = entry.get("metadata", {})
    if attrs is None or len(attrs) == 0:
        return ""
    n = len(attrs)
    under = int((attrs["DFE_Status"] == "Under DFE").sum()) if "DFE_Status" in attrs.columns else None
    bfe = meta.get("BFE_FT_NAVD88")
    val = float(attrs["structure_value"].sum()) if "structure_value" in attrs.columns else None
    bits = [f"<b>{n:,}</b> buildings"]
    if under is not None:
        bits.append(f"<b>{under:,}</b> under the DFE")
    if bfe not in (None, ""):
        bits.append(f"BFE <b>{float(bfe):g} ft</b>")
    if val:
        bits.append(f"<b>{fmt_money_short(val)}</b> structure value")
    return " &nbsp;·&nbsp; ".join(bits)


def render(ctx):
    ss = ctx.ss
    ui.landing_dark()
    art = ""
    logo = ui.first_existing(C.LOGO_FULL_CANDIDATES)
    if logo:
        art = f"<img src='{ui.image_data_uri(logo, os.path.getmtime(logo), 420)}' alt='ADAPT'/>"
    st.markdown(
        "<div class='adapt-hero dark'><div class='hero-text'>"
        "<div class='kicker'>Assessment of Damage and Adaptation Planning Tool</div>"
        "<h1>Know your flood risk. Compare your options.</h1>"
        "<p>A decision support tool co-developed with tribal nations and towns in New York and Virginia. "
        "It shows how flood damage grows with sea-level rise and what each adaptation option would change.</p>"
        f"</div><div class='hero-logo'>{art}</div></div>",
        unsafe_allow_html=True,
    )

    if not ctx.available_locations:
        st.error("No location data found. Add a results bundle under `data/<Location>/` or run the model "
                 "from the Developer module.")
        return

    c1, c2 = st.columns([0.36, 0.64], vertical_alignment="bottom")
    with c1:
        X.location_selector(ctx.available_locations, ctx.registry, key="w_location_home", label="Community")
    cfg = ui.location_cfg(ctx.registry, ss.cv_location) or {}
    locked = ui.is_protected(cfg) and not ui.location_unlocked(ss.cv_location)
    with c2:
        if locked:
            with ctx._keyed_container("gate_inline"):
                ui.location_gate(ss.cv_location, cfg, compact=True)
        else:
            st.markdown(f"<div class='adapt-siteline'>{_site_line(ctx)}</div>", unsafe_allow_html=True)

    # Each card is one click: the HTML card is drawn, and a full-size,
    # invisible page link is laid over it (see the st-key-go_ rules in ui.py).
    st.markdown("<style>" + "".join(
        f".st-key-go_{k}:hover .adapt-module {{ border-color: {m['accent']}; }}" for k, m in C.MODULES.items()
    ) + "</style>", unsafe_allow_html=True)
    # Developer is the narrow card; the two public modules share the rest equally.
    cols = st.columns([1, 1, 0.56], gap="medium")
    for col, (key, m) in zip(cols, C.MODULES.items()):
        with col:
            with ctx._keyed_container(f"go_{key}"):
                st.markdown(
                    f"<div class='adapt-module'>"
                    f"<div class='icon' style='background:{m['accent']}22; color:{m['accent']}'>{m['icon']}</div>"
                    f"<h3>{_html.escape(m['title'])}</h3>"
                    f"<div class='who'>{_html.escape(m['subtitle'])}</div>"
                    f"<div class='go' style='color:{m['accent']}'>Open <span>→</span></div></div>",
                    unsafe_allow_html=True,
                )
                st.page_link(ctx.pages[ctx.first_page[key]], label=f"Open {m['title']}", use_container_width=True)
