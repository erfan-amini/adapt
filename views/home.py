"""Landing page: one screen. A hero, the community, three doors."""
import html as _html

import streamlit as st

from adapt_core import config as C
from adapt_core import ui
from adapt_core.helpers import fmt_money_short
from views import _context as X

_CHIPS = {
    "community": ["Dynamic maps", "My building"],
    "planners": ["Overview", "Flood maps", "Road maps", "Distributions"],
    "developer": ["Data", "Survey tool", "Curves", "Run the model"],
}
_WHO = {
    "community": "Residents, businesses, property owners",
    "planners": "Planners, agencies, community leaders",
    "developer": "The project team",
}

# Original illustration for the hero: rising water lines over a shoreline of
# small buildings. Lightweight inline SVG, no external assets.
_HERO_ART = """
<svg viewBox="0 0 420 220" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
  <defs>
    <linearGradient id="w1" x1="0" x2="0" y1="0" y2="1">
      <stop offset="0" stop-color="#38bdf8" stop-opacity="0.95"/><stop offset="1" stop-color="#0ea5e9" stop-opacity="0.35"/>
    </linearGradient>
    <linearGradient id="w2" x1="0" x2="0" y1="0" y2="1">
      <stop offset="0" stop-color="#7dd3fc" stop-opacity="0.9"/><stop offset="1" stop-color="#0284c7" stop-opacity="0.25"/>
    </linearGradient>
  </defs>
  <g fill="#e2e8f0" opacity="0.95">
    <rect x="40" y="96" width="34" height="44" rx="2"/><polygon points="36,98 57,80 78,98"/>
    <rect x="98" y="88" width="42" height="52" rx="2"/><polygon points="94,90 119,70 144,90"/>
    <rect x="164" y="104" width="30" height="36" rx="2"/><polygon points="160,106 179,92 198,106"/>
    <rect x="222" y="78" width="56" height="62" rx="2"/><rect x="232" y="90" width="8" height="10" fill="#0f172a" opacity="0.5"/><rect x="248" y="90" width="8" height="10" fill="#0f172a" opacity="0.5"/><rect x="264" y="90" width="8" height="10" fill="#0f172a" opacity="0.5"/>
    <rect x="300" y="98" width="36" height="42" rx="2"/><polygon points="296,100 318,82 340,100"/>
    <rect x="356" y="108" width="26" height="32" rx="2"/><polygon points="352,110 369,98 386,110"/>
  </g>
  <g stroke="#94a3b8" stroke-width="2" opacity="0.6"><line x1="20" y1="140" x2="400" y2="140"/></g>
  <path d="M0 160 C 60 148, 100 172, 160 160 S 260 148, 320 160 S 400 172, 420 160 L420 220 L0 220 Z" fill="url(#w2)"/>
  <path d="M0 178 C 70 166, 120 190, 190 178 S 300 166, 360 178 S 410 190, 420 178 L420 220 L0 220 Z" fill="url(#w1)"/>
  <g stroke="#f8fafc" stroke-width="1.5" opacity="0.55" fill="none">
    <path d="M0 128 C 60 118, 100 138, 160 128 S 260 118, 320 128 S 400 138, 420 128" stroke-dasharray="4 6"/>
  </g>
  <text x="404" y="124" font-family="Inter, sans-serif" font-size="9" fill="#cbd5e1" text-anchor="end">2100</text>
  <text x="404" y="156" font-family="Inter, sans-serif" font-size="9" fill="#0f172a" text-anchor="end" opacity="0.8">2055</text>
  <text x="404" y="175" font-family="Inter, sans-serif" font-size="9" fill="#0f172a" text-anchor="end" opacity="0.8">today</text>
</svg>
"""


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
    st.markdown(
        "<div class='adapt-hero compact'><div class='hero-text'>"
        "<div class='kicker'>ADAPT · Assessment of Damage and Adaptation Planning Tool</div>"
        "<h1>Flood damage and adaptation planning, one building at a time.</h1>"
        "<p>Building-level flood damage under sea-level rise, and what each adaptation measure would save.</p>"
        "</div><div class='hero-art'>" + _HERO_ART + "</div></div>",
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
    with c2:
        if ui.is_protected(cfg) and not ui.location_unlocked(ss.cv_location):
            st.markdown("<span class='adapt-lock'>🔒 Results for this community are password protected.</span>",
                        unsafe_allow_html=True)
        else:
            st.markdown(f"<div class='adapt-siteline'>{_site_line(ctx)}</div>", unsafe_allow_html=True)
    if ui.is_protected(cfg) and not ui.location_unlocked(ss.cv_location):
        g1, g2 = st.columns([0.36, 0.64])
        with g1:
            ui.location_gate(ss.cv_location, cfg, compact=True)

    cols = st.columns(3, gap="medium")
    for col, (key, m) in zip(cols, C.MODULES.items()):
        with col:
            chips = "".join(f"<span class='chip'>{_html.escape(c)}</span>" for c in _CHIPS[key])
            st.markdown(
                f"<div class='adapt-module'>"
                f"<div class='top'><div class='icon' style='background:{m['accent']}22; color:{m['accent']}'>{m['icon']}</div>"
                f"<div><h3>{_html.escape(m['title'])}</h3><div class='who'>{_html.escape(_WHO[key])}</div></div></div>"
                f"<div class='chips'>{chips}</div></div>",
                unsafe_allow_html=True,
            )
            with ctx._keyed_container(f"go_{key}"):
                st.page_link(ctx.pages[ctx.first_page[key]], label=f"Open {m['title']}", use_container_width=True)
