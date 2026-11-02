"""Households and Businesses > My home.

Type an address, confirm the building on an aerial map, and get the home
report: house information, expected flood depths, and the damage each
adaptation action avoids, on screen and as a PDF.
"""
import html as _html

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from adapt_core import config as C
from adapt_core import geocode as G
from adapt_core import home_report as R
from adapt_core import ui
from adapt_core.mapexport import _basemap_config

_SCN = {"Median SLR": "50th-percentile", "High SLR (90th pct)": "90th-percentile"}
_CARD = ("<div class='adapt-report-card'>{body}</div>")


def _with_place_text(addr, place_hint):
    """'1 Elfin Pl' -> '1 Elfin Pl, Mastic Beach, NY' unless the town is already there."""
    first = (place_hint or "").split(",")[0].strip().lower()
    if first and first in addr.lower():
        return addr
    return f"{addr}, {place_hint}" if place_hint else addr


def _reset():
    for k in ("home_geo", "home_candidates", "home_confirmed_id", "home_address", "home_no_match"):
        st.session_state.pop(k, None)


def _candidate_map(geo, cands, chosen_id, attrs_all):
    """Aerial map centered on the geocoded point with the candidate buildings."""
    style, layers = _basemap_config("Aerial")
    fig = go.Figure()
    # Other inventory buildings nearby, faint, for orientation
    near = G.nearest_buildings(attrs_all, geo["lat"], geo["lon"], 140.0, limit=60)
    if len(near):
        fig.add_trace(go.Scattermapbox(
            lat=near["latitude"], lon=near["longitude"], mode="markers",
            marker=dict(size=9, color="rgba(255,255,255,0.55)"), hoverinfo="skip", name="Other buildings"))
    if len(cands):
        colors = ["#f97316" if int(i) == int(chosen_id) else "#38bdf8" for i in cands["id"]]
        fig.add_trace(go.Scattermapbox(
            lat=cands["latitude"], lon=cands["longitude"], mode="markers+text",
            marker=dict(size=18, color=colors, opacity=0.95),
            text=[str(k + 1) for k in range(len(cands))], textfont=dict(color="white", size=12),
            textposition="middle center", hoverinfo="text",
            hovertext=[f"Candidate {k + 1}: NSI {int(r.id)} · {r.occupancy_type} · {r.distance_m:.0f} m from the address"
                       for k, r in enumerate(cands.itertuples())], name="Candidates"))
    fig.add_trace(go.Scattermapbox(
        lat=[geo["lat"]], lon=[geo["lon"]], mode="markers",
        marker=dict(size=12, color="#dc2626"), hoverinfo="text", hovertext=["Geocoded address"], name="Address"))
    fig.update_layout(
        mapbox=dict(style=style, layers=layers, center=dict(lat=geo["lat"], lon=geo["lon"]), zoom=18.2),
        margin=dict(l=0, r=0, t=0, b=0), height=330, showlegend=False,
    )
    return fig


def _damage_chart(rep):
    """Grouped bars: expected damage by action for each horizon, with the P05 to P95 range."""
    fig = go.Figure()
    pal = {"No mitigation": "#94a3b8", "Raise Utilities": "#a3e635", "WFP B": "#facc15",
           "Elevate": "#f97316", "WFP 1st": "#38bdf8"}
    short = {"No mitigation": "Do nothing", "Raise Utilities": "Elevate utilities", "WFP B": "Wet-floodproof basement",
             "Elevate": "Elevate home", "WFP 1st": "Wet-floodproof 1st floor"}
    for y in rep["horizons"]:
        items = rep["damage"].get(y, [])
        if not items:
            continue
        fig.add_trace(go.Bar(
            name=f"By {y}", x=[short.get(i["action"], i["action"]) if i["action"] != "WFP 1st" or
                                "story" not in i["label"] else "Add a story" for i in items],
            y=[i["p50"] for i in items],
            error_y=dict(type="data", symmetric=False,
                         array=[max(0.0, (i["p95"] or 0) - i["p50"]) for i in items],
                         arrayminus=[max(0.0, i["p50"] - (i["p05"] or 0)) for i in items],
                         color="#475569", thickness=1.2, width=4),
            marker_color=[pal.get(i["action"], "#64748b") for i in items],
            marker_opacity=0.95 if y == rep["horizons"][0] else 0.6,
            text=[R.money(i["p50"]) for i in items], textposition="outside", cliponaxis=False,
            hovertemplate="%{x}<br>Expected %{text}<extra>By " + str(y) + "</extra>",
        ))
    fig.update_layout(
        barmode="group", height=360, margin=dict(l=10, r=10, t=30, b=10), plot_bgcolor="white",
        legend=dict(orientation="h", y=1.12, x=0), yaxis=dict(title="Cumulative damage ($)", gridcolor="#e5e7eb",
                                                              tickprefix="$", separatethousands=True),
        xaxis=dict(title=None), font=dict(family="Source Sans Pro, sans-serif"),
    )
    return fig


def render(ctx):
    ss = st.session_state
    loc_entry = ctx.loc_entry or {}
    location_name = ctx.location_name
    cfg = ui.location_cfg(ctx.registry, ctx.selected_location) or {}
    attrs = loc_entry.get("bldg_attrs")

    ui.page_header("My home", None, module_key="community", location=location_name)
    st.markdown(
        "<style>"
        ".adapt-report-card { background:#ffffff; border:1px solid #e2e8f0; border-radius:16px; padding:1.1rem 1.3rem; "
        "  box-shadow:0 1px 2px rgba(15,23,42,0.04); margin-bottom:0.6rem; }"
        ".adapt-report-card h3 { margin:0 0 0.5rem 0; font-size:1.05rem; color:#0f172a; }"
        ".adapt-kv { display:grid; grid-template-columns: 1fr 1fr; gap:0.35rem 1.2rem; font-size:0.93rem; }"
        ".adapt-kv div { display:flex; justify-content:space-between; gap:0.8rem; border-bottom:1px dashed #e2e8f0; padding:0.25rem 0; }"
        ".adapt-kv span:first-child { color:#64748b; } .adapt-kv span:last-child { font-weight:600; color:#0f172a; text-align:right; }"
        ".adapt-callout { background:#eff6ff; border-left:4px solid #0ea5e9; border-radius:10px; padding:0.6rem 0.9rem; margin:0.4rem 0 0.2rem 0; font-size:0.93rem; }"
        ".adapt-step { font-size:0.74rem; font-weight:700; letter-spacing:0.08em; text-transform:uppercase; color:#0369a1; margin:0.2rem 0 0.2rem 0; }"
        ".adapt-dmg-line { font-size:0.95rem; margin:0.15rem 0; } .adapt-dmg-line b { color:#0f172a; }"
        ".adapt-dmg-sub { font-size:0.86rem; color:#15803d; margin:0 0 0.35rem 1.1rem; }"
        # Step 1: the search panel
        ".st-key-home_search { background:linear-gradient(180deg,#f0f9ff 0%,#ffffff 100%); border:1px solid #bae6fd; border-radius:18px; "
        "  padding:1.2rem 1.3rem 1.1rem 1.3rem; box-shadow:0 8px 24px rgba(14,165,233,0.10); gap:0.6rem; }"
        ".adapt-search-title { font-size:1.35rem; font-weight:800; color:#0f172a; letter-spacing:-0.01em; margin:0; }"
        ".adapt-search-sub { font-size:0.92rem; color:#475569; margin:0.15rem 0 0.4rem 0; }"
        ".adapt-steps { display:flex; flex-direction:column; gap:0.4rem; margin:0.2rem 0 0.5rem 0; }"
        ".adapt-steps div { display:flex; align-items:center; gap:0.6rem; font-size:0.88rem; color:#334155; }"
        ".adapt-steps span { flex:0 0 1.5rem; width:1.5rem; height:1.5rem; border-radius:999px; background:#0ea5e9; color:#fff; "
        "  font-weight:700; font-size:0.78rem; display:inline-flex; align-items:center; justify-content:center; }"
        ".st-key-home_search [data-testid='stTextInput'] input { font-size:1rem; height:2.7rem; }"
        # Streamlit sizes elements to the column, not to the padded card: keep them inside it.
        ".st-key-home_search > div, .st-key-home_search [data-testid='stElementContainer'], "
        ".st-key-home_search [data-testid='stTextInput'], .st-key-home_search [data-testid='stHorizontalBlock'] "
        "  { width:100% !important; max-width:100% !important; }"
        ".st-key-home_search [data-testid='stMarkdownContainer'] { margin-bottom:0 !important; }"
        "</style>", unsafe_allow_html=True,
    )

    if attrs is None or len(attrs) == 0:
        st.info("No building inventory is loaded for this community yet.")
        return

    place_hint = f"{cfg.get('name', location_name)}, {cfg.get('state', '')}".strip(", ")

    def _search(addr_in):
        """Geocode the typed address and stash the candidates in session state."""
        _reset()
        addr = G.clean_address(addr_in)
        if not addr:
            ss["home_no_match"] = "Type a street address first."
            return
        # 1. Addresses the project team recorded in the inventory
        inv_hits = G.match_inventory_address(attrs, addr)
        geo = None
        if len(inv_hits):
            r0 = inv_hits.iloc[0]
            geo = {"lat": float(r0["latitude"]), "lon": float(r0["longitude"]),
                   "label": str(r0["address"]), "source": "project inventory",
                   "query": addr}
        else:
            # 2. Public geocoders
            with st.spinner("Looking up the address..."):
                geo = G.geocode(addr, place_hint)
        if geo and geo.get("error"):
            ss["home_no_match"] = geo["error"]
        elif not geo:
            ss["home_no_match"] = ("We could not find that address. Check the house number and street name, "
                                   "or add the town and state.")
        else:
            if len(inv_hits):
                cands = inv_hits.head(3)
            else:
                cands = G.nearest_buildings(attrs, geo["lat"], geo["lon"], C.ADDRESS_CANDIDATES_M, limit=3)
            if len(cands) == 0 or float(cands["distance_m"].iloc[0]) > C.ADDRESS_SNAP_M:
                wider = G.nearest_buildings(attrs, geo["lat"], geo["lon"], 150.0, limit=3)
                if len(wider) == 0:
                    ss["home_no_match"] = (f"The address was found ({geo['label']}), but no building in our "
                                           f"{location_name} inventory is within 150 m of it. The inventory may "
                                           "not cover that street yet.")
                else:
                    ss["home_geo"] = geo
                    ss["home_candidates"] = wider
                    ss["home_far"] = True
            else:
                ss["home_geo"] = geo
                ss["home_candidates"] = cands
                ss["home_far"] = False
            ss["home_address"] = G.clean_address(_with_place_text(addr, place_hint))

    # ---- Step 1: address ----------------------------------------------------
    # Before a home is found: the search panel. Afterwards: a compact
    # address bar at the top.
    if not ss.get("home_geo"):
        _pad, mid, _pad2 = st.columns([0.6, 1.6, 0.6])
        with mid:
            with ctx._keyed_container("home_search"):
                st.markdown("<p class='adapt-search-title'>Get the report for your home</p>"
                            "<p class='adapt-search-sub'>Flood depths at your house, the damage to expect, and what "
                            "each action would avoid, on screen and as a PDF.</p>"
                            "<div class='adapt-steps'><div><span>1</span>Type your street address</div>"
                            "<div><span>2</span>Confirm your home on the aerial photo</div>"
                            "<div><span>3</span>Read or download your report</div></div>", unsafe_allow_html=True)
                addr_in = st.text_input("Street address", key="home_addr_input",
                                        placeholder=f"e.g. 1 Elfin Pl, {place_hint}", label_visibility="collapsed")
                b1, b2 = st.columns(2)
                with b1:
                    find = st.button("🔎 Find my home", type="primary", use_container_width=True)
                with b2:
                    if st.button("Start over", use_container_width=True):
                        _reset()
                        st.rerun()
                if find:
                    _search(addr_in)
                    if ss.get("home_geo"):
                        st.rerun()
                if ss.get("home_no_match"):
                    st.warning(ss["home_no_match"])
        return

    st.markdown("<div class='adapt-step'>Step 1 · Your address</div>", unsafe_allow_html=True)
    c1, c2, c3 = st.columns([2.2, 0.6, 0.5], vertical_alignment="bottom")
    with c1:
        addr_in = st.text_input("Street address", key="home_addr_input", placeholder=f"e.g. 1 Elfin Pl, {place_hint}",
                                label_visibility="collapsed")
    with c2:
        find = st.button("🔎 Find my home", type="primary", use_container_width=True)
    with c3:
        if st.button("Start over", use_container_width=True):
            _reset()
            st.rerun()
    if find:
        _search(addr_in)
        st.rerun()

    if ss.get("home_no_match"):
        st.warning(ss["home_no_match"])
        return
    geo = ss.get("home_geo")

    cands = ss.get("home_candidates")
    # ---- Step 2: confirm ----------------------------------------------------
    if not ss.get("home_confirmed_id"):
        st.markdown("<div class='adapt-step'>Step 2 · Confirm your home</div>", unsafe_allow_html=True)
        if ss.get("home_far"):
            st.warning("No building sits within 40 m of the geocoded address. The nearest buildings in the inventory "
                       "are shown; pick one only if it is your home.")
        st.caption(f"Matched address: **{_html.escape(geo['label'])}** ({geo['source']}).")
        if len(cands) > 1:
            opts = [f"{k + 1}" for k in range(len(cands))]
            labels = {f"{k + 1}": f"Building {k + 1} · {int(r.distance_m)} m away" for k, r in enumerate(cands.itertuples())}
            pick = st.segmented_control("Which marker is your home?", opts, format_func=lambda o: labels[o],
                                        default="1", selection_mode="single", key="home_pick") or "1"
            chosen = int(cands.iloc[int(pick) - 1]["id"])
        else:
            chosen = int(cands.iloc[0]["id"])
        st.plotly_chart(_candidate_map(geo, cands, chosen, attrs), use_container_width=True,
                        config={"displayModeBar": False})
        row = cands[cands["id"].astype(int) == chosen].iloc[0]
        bits = [f"NSI {chosen}"]
        if pd.notna(row.get("number_of_stories")):
            bits.append(f"{int(row['number_of_stories'])}-story")
        if isinstance(row.get("occupancy_type"), str):
            bits.append(row["occupancy_type"])
        if pd.notna(row.get("year_built")):
            bits.append(f"built {int(row['year_built'])}")
        st.markdown(f"The orange marker is **{' · '.join(bits)}**, {row['distance_m']:.0f} m from the address point. "
                    "The red dot is where the address was geocoded.")
        b1, b2 = st.columns([0.5, 0.5])
        with b1:
            if st.button("✅ Yes, this is my home", type="primary", use_container_width=True):
                ss["home_confirmed_id"] = chosen
                st.rerun()
        with b2:
            if st.button("No, try another address", use_container_width=True):
                _reset()
                st.rerun()
        return

    # ---- Step 3: the report -------------------------------------------------
    bid = int(ss["home_confirmed_id"])
    addr_text = ss.get("home_address") or geo.get("label", "")
    st.markdown("<div class='adapt-step'>Step 3 · Your report</div>", unsafe_allow_html=True)
    t1, t2, t3 = st.columns([1.35, 1.05, 0.8], vertical_alignment="center")
    with t1:
        st.markdown(f"**{_html.escape(addr_text)}** &nbsp;·&nbsp; NSI {bid}", unsafe_allow_html=True)
    with t2:
        scn_label = st.segmented_control("Sea-level rise", list(_SCN), default=list(_SCN)[0],
                                         selection_mode="single", key="home_scn", label_visibility="collapsed") \
            or list(_SCN)[0]
    scenario = _SCN[scn_label]

    fema = G.fema_flood_zone(float(attrs[attrs["id"].astype(int) == bid]["latitude"].iloc[0]),
                             float(attrs[attrs["id"].astype(int) == bid]["longitude"].iloc[0]))
    rep = R.build_report(loc_entry, cfg, bid, addr_text, scenario=scenario,
                         mobile_raise_only=bool(ctx.mobile_raise_only), fema=fema)
    if rep is None:
        st.error("No results for this building.")
        return
    with t3:
        st.download_button("⬇️ Download PDF", data=R.render_pdf(rep),
                           file_name=f"ADAPT_report_{bid}.pdf", mime="application/pdf",
                           use_container_width=True, type="primary")

    # Intro (short on screen; the PDF carries the full text)
    med, high = rep["slr_in"]["50th-percentile"], rep["slr_in"]["90th-percentile"]
    hz = rep["horizons"]
    slr_txt = " · ".join(f"{y}: {R.inches_phrase(med.get(y))} (median), {R.inches_phrase(high.get(y))} (high)" for y in hz)
    st.markdown(
        f"<div class='adapt-callout'>Sea-level rise used here, relative to 2025: {slr_txt}. "
        f"Expected damage is the median of 1,000 simulated futures; the range runs from the 5th to the 95th "
        f"percentile. Results use {rep['scenario_word']} sea-level rise.</div>", unsafe_allow_html=True)

    # 1. House information
    kv = "".join(f"<div><span>{_html.escape(k)}</span><span>{_html.escape(v)}</span></div>" for k, v in R.house_lines(rep))
    body = f"<h3>1. House information</h3><div class='adapt-kv'>{kv}</div>"
    if R.shortfall_sentence(rep):
        body += f"<div class='adapt-callout'>{_html.escape(R.shortfall_sentence(rep))}</div>"
    st.markdown(_CARD.format(body=body), unsafe_allow_html=True)

    # 2. Flood depths
    body = "<h3>2. Expected future flood depth above ground</h3>"
    if rep["depth_rows"]:
        body += ("<p style='color:#475569;font-size:0.9rem;margin:0 0 0.5rem 0'>How high the water is expected to "
                 "rise during different flood conditions relative to the ground at your house.</p>")
        table = pd.DataFrame(
            {f"By {y}": [R.depth_phrase(r["by_year"][y]["above_ground_ft"]) for r in rep["depth_rows"]] for y in hz},
            index=[r["label"] for r in rep["depth_rows"]])
        st.markdown(_CARD.format(body=body), unsafe_allow_html=True)
        st.dataframe(table, use_container_width=True)
    else:
        body += ("<p style='color:#475569'>Present-day flood levels for this community have not been entered yet "
                 "(Developer > Data and settings).</p>")
        st.markdown(_CARD.format(body=body), unsafe_allow_html=True)

    # 3+. Damage by action
    n = 3
    st.markdown(_CARD.format(body=f"<h3>{n}. Cumulative damage depending on action</h3>"
                             "<p style='color:#475569;font-size:0.9rem;margin:0'>Expected flood damage from now "
                             "through each horizon, depending on the action you take. Bars show the expected value; "
                             "the thin lines show the range."
                             + (" " + _html.escape(R.elevate_sentence(rep)) if R.elevate_sentence(rep) else "")
                             + "</p>"), unsafe_allow_html=True)
    st.plotly_chart(_damage_chart(rep), use_container_width=True, config={"displayModeBar": False})
    cols = st.columns(len(hz))
    for col, y in zip(cols, hz):
        with col:
            lines = [f"<div style='font-weight:700;color:#0f172a;margin-bottom:0.3rem'>By {y}</div>"]
            for it in rep["damage"].get(y, []):
                lines.append(f"<div class='adapt-dmg-line'>{_html.escape(it['label'])}: <b>{R.money(it['p50'])}</b>, "
                             f"range {R.money(it['p05'])} – {R.money(it['p95'])}</div>")
                if it["avoided"] is not None:
                    lines.append(f"<div class='adapt-dmg-sub'>Avoids {it['avoided'] * 100:.0f}% of the expected damage</div>")
            st.markdown(_CARD.format(body="".join(lines)), unsafe_allow_html=True)

    st.caption(f"Let us know if any information about your house is wrong, or if you want other horizons. "
               f"Contact {rep['contact']}. Damage estimates are planning estimates, not appraisals.")
