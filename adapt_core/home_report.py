"""The home report: one building's flood exposure and the damage each
adaptation action avoids, in the format of the reports mailed to residents.

`build_report()` assembles the numbers from a results bundle; `render_pdf()`
turns them into a PDF with reportlab. The page (views/community/my_home.py)
shows the same numbers on screen.

Statistics follow the mailed reports: the "expected" damage is the median
(P50) of the 1,000 simulated futures and the range is P05 to P95. Flood
depths add the modeled sea-level rise (change in the median annual-maximum
water level since the first model year) to the present-day flood levels
stored in the location settings.
"""
import io
from datetime import date

import numpy as np
import pandas as pd

from adapt_core import config as C
from adapt_core.helpers import (ADD_STORY_LABEL, _elev_eligible_flag, _flag_true, _is_one_story_bldg)

ACTION_ORDER = ["No mitigation", "Raise Utilities", "WFP B", "Elevate", "WFP 1st"]
ACTION_SENTENCE = {
    "No mitigation":   "If you do nothing",
    "Raise Utilities": "If you elevate the utilities",
    "WFP B":           "If you wet-floodproof the basement",
    "Elevate":         "If you elevate the home",
    "WFP 1st":         "If you wet-floodproof the first floor",
}
ADD_STORY_SENTENCE = "If you add a story and move living space upstairs"
FLOOD_ROWS = [
    ("mhhw_ft",    "High tide (MHHW)"),
    ("monthly_ft", "Monthly flood"),
    ("pct10_ft",   "10% annual chance flood"),
    ("pct1_ft",    "1% annual chance flood"),
]
SCENARIO_WORD = {"50th-percentile": "median", "90th-percentile": "high (90th percentile)"}


def _f(v, default=None):
    try:
        v = float(v)
        return v if np.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def _level(df, year, col="P50"):
    if df is None or df.empty or col not in df.columns:
        return None
    i = (df["Year"] - year).abs().idxmin()
    return _f(df.loc[i, col])


def slr_ft(loc_entry, scenario, year):
    """Sea-level rise by `year` under `scenario`: change in the median
    annual-maximum water level since the first model year."""
    wl = (loc_entry or {}).get("water_levels") or {}
    df = wl.get(scenario)
    if df is None or df.empty:
        return None
    base = int(df["Year"].min())
    a, b = _level(df, year), _level(df, base)
    return (a - b) if (a is not None and b is not None) else None


def has_basement(row):
    occ = str(row.get("occupancy_type", "") or "").upper()
    fnd = str(row.get("foundation_type", "") or "").upper()
    if occ.endswith("WB") or fnd == "B":
        return True
    if occ.endswith("NB"):
        return False
    return None


def applicable_actions(row, mobile_raise_only=False):
    """Actions that apply to this building, in report order, with labels."""
    occ = str(row.get("occupancy_type", "") or "").upper()
    is_mobile = occ.startswith("RES2") or mobile_raise_only
    add_story = _flag_true(row.get("OneStoryAddStory"))
    one_story = (not add_story) and _is_one_story_bldg(row.get("SOID"), row.get("number_of_stories"))
    elev_ok = _elev_eligible_flag(row.get("ElevationEligible"))
    bsmt = has_basement(row)
    out = []
    for a in ACTION_ORDER:
        if a == "No mitigation":
            out.append((a, ACTION_SENTENCE[a])); continue
        if is_mobile and a != "Elevate":
            continue
        if a == "WFP 1st" and one_story:
            continue
        if a == "WFP B" and bsmt is False:
            continue
        if a == "Elevate" and not elev_ok:
            continue
        label = ADD_STORY_SENTENCE if (a == "WFP 1st" and add_story) else ACTION_SENTENCE[a]
        out.append((a, label))
    return out


def build_report(loc_entry, cfg, building_id, address_text, scenario="50th-percentile",
                 horizons=None, mobile_raise_only=False, fema=None):
    """Assemble every number the report needs. Returns a dict or None."""
    attrs = (loc_entry or {}).get("bldg_attrs")
    bl = (loc_entry or {}).get("buildings")
    if attrs is None or bl is None:
        return None
    rows = attrs[attrs["id"].astype(int) == int(building_id)]
    if rows.empty:
        return None
    row = rows.iloc[0]
    years_avail = sorted(int(y) for y in bl["TargetYear"].unique() if int(y) > 2025)
    if not horizons:
        horizons = [y for y in C.REPORT_HORIZON_YEARS if y in years_avail] or years_avail[:2]
    horizons = [int(y) for y in horizons]

    bfe = _f(cfg.get("bfe_ft"), _f((loc_entry or {}).get("bfe_ft"), 8.0))
    dfe = bfe + 2.0
    ground = _f(row.get("ground_elevation"))
    ffe = _f(row.get("FFE_ft"))
    bsmt = has_basement(row)
    lowest = ground if (bsmt and ground is not None) else ffe
    shortfall = (dfe - lowest) if lowest is not None else None

    # Sea-level rise by horizon, both scenarios, inches
    slr = {}
    for scn in ("50th-percentile", "90th-percentile"):
        slr[scn] = {y: slr_ft(loc_entry, scn, y) for y in horizons}

    # Flood depths above ground for the chosen scenario
    levels = (cfg.get("flood_levels") or {})
    depth_rows = []
    for key, label in FLOOD_ROWS:
        lv = _f(levels.get(key))
        if lv is None or lv <= 0:
            continue
        per_year = {}
        for y in horizons:
            rise = slr[scenario].get(y)
            wl = lv + (rise or 0.0)
            per_year[y] = {"wl_ft": wl, "above_ground_ft": (wl - ground) if ground is not None else None,
                           "above_ffe_ft": (wl - ffe) if ffe is not None else None}
        depth_rows.append({"key": key, "label": label, "present_ft": lv, "by_year": per_year})

    # Damage by action and horizon
    sub = bl[(bl["id"].astype(int) == int(building_id)) & (bl["SLR"] == scenario)]
    actions = applicable_actions(row, mobile_raise_only)
    damage = {}
    for y in horizons:
        dy = sub[sub["TargetYear"] == y]
        base = dy[dy["Action"] == "No mitigation"]
        base_p50 = _f(base["CumEAD_P50"].iloc[0]) if len(base) else None
        items = []
        for a, label in actions:
            r = dy[dy["Action"] == a]
            if r.empty:
                continue
            p50, p05, p95 = (_f(r["CumEAD_P50"].iloc[0]), _f(r["CumEAD_P05"].iloc[0]), _f(r["CumEAD_P95"].iloc[0]))
            if p50 is None:
                continue
            avoided = None
            if a != "No mitigation" and base_p50 is not None and base_p50 > 0:
                avoided = max(0.0, min(1.0, 1.0 - p50 / base_p50))
            items.append({"action": a, "label": label, "p50": p50, "p05": p05, "p95": p95, "avoided": avoided})
        damage[y] = items

    return {
        "location": cfg.get("name") or (loc_entry or {}).get("location_slug", ""),
        "state": cfg.get("state", ""),
        "address": address_text,
        "building_id": int(building_id),
        "generated": date.today().isoformat(),
        "scenario": scenario,
        "scenario_word": SCENARIO_WORD.get(scenario, scenario),
        "horizons": horizons,
        "stories": _f(row.get("number_of_stories")),
        "basement": bsmt,
        "occupancy": str(row.get("occupancy_type", "") or ""),
        "year_built": _f(row.get("year_built")),
        "area_sqft": _f(row.get("area")),
        "structure_value": _f(row.get("structure_value")),
        "ground_ft": ground, "ffe_ft": ffe, "lowest_ft": lowest, "lowest_is_basement": bool(bsmt and ground is not None),
        "bfe_ft": bfe, "dfe_ft": dfe, "shortfall_ft": shortfall,
        "dfe_status": str(row.get("DFE_Status", "") or ""),
        "fema": fema,
        "slr_in": {scn: {y: (v * 12.0 if v is not None else None) for y, v in d.items()} for scn, d in slr.items()},
        "depth_rows": depth_rows,
        "damage": damage,
        "contact": C.REPORT_CONTACT,
    }


# ---------------------------------------------------------------------------
# Text helpers shared by the page and the PDF
# ---------------------------------------------------------------------------
def money(v):
    if v is None:
        return "n/a"
    return f"${v:,.0f}"


def depth_phrase(d_ft):
    """'1.6 ft above the ground' / '5 inches lower than the ground'."""
    if d_ft is None:
        return "n/a"
    inches = d_ft * 12.0
    if d_ft < 0:
        if abs(inches) < 12:
            return f"{abs(inches):.0f} inches lower than the ground at your house (does not reach your house)"
        return f"{abs(d_ft):.1f} ft lower than the ground at your house (does not reach your house)"
    if inches < 12:
        return f"{inches:.0f} inches above the ground"
    return f"{d_ft:.1f} ft above the ground"


def inches_phrase(v_in):
    return "n/a" if v_in is None else f"{v_in:.0f} inches"


def intro_paragraphs(rep):
    h = rep["horizons"]
    y1 = h[0]
    y2 = h[1] if len(h) > 1 else None
    med, high = rep["slr_in"]["50th-percentile"], rep["slr_in"]["90th-percentile"]

    def _slr_line(word, d):
        parts = [f"by {y}: {inches_phrase(d.get(y))}" for y in h]
        return f"{word} sea level rise " + ", and ".join(parts)

    span = f"{y1 - 2025} or {y2 - 2025}" if y2 else f"{y1 - 2025}"
    return [
        "This report shows what flood damage your home is likely to face in the coming decades, and how "
        "much of the costs you can avoid with each home retrofit action. The numbers come from a "
        "building-by-building flood risk model that combines local sea level projections with thousands "
        "of simulated storms.",
        f"The flood risk is increasing every year partly because the sea level is rising. We are giving "
        f"you the damages for {rep['scenario_word']} sea level rise. The actual rise could be higher or "
        f"lower, resulting in greater or lesser damages.",
        _slr_line("Median", med) + ". " + _slr_line("High (90th percentile)", high) + ".",
        f"The future damages are an average over damages caused by tidal flooding and possible storms. "
        f"Depending on what storms actually happen in the next {span} years, the actual damages may be "
        f"lower or higher. We give you a range of damages that you can expect in addition to the "
        f"expected value.",
        "We tell you the water levels that you can expect in the future for the flood scenarios discussed "
        "in the workshop: monthly, 10% chance in any year, and 1% chance in any year. The 1% chance flood, "
        "commonly referred to as the 100-year flood, is the flood level on which FEMA bases flood insurance "
        "requirements. The current level of the 1% chance flood is the Base Flood Elevation (BFE) that you "
        "will see on FEMA websites and maps. In reality, the current level of the 1% chance flood is higher "
        "than FEMA's BFE because FEMA does not update their maps frequently and the level of the 1% chance "
        "flood rises every year.",
        "FEMA recommends that the lowest floor of your house should be 2 feet above BFE, or at the Design "
        "Flood Elevation (DFE). The flood insurance premium is much lower for homes that comply with this "
        "recommendation than for homes that do not. The lowest floor is the basement floor if you have a "
        "basement, and the first floor otherwise.",
        f"Please let us know if any of the information about your house is wrong. Contact {rep['contact']} "
        f"with any questions.",
    ]


def house_lines(rep):
    lines = [("Address", f"{rep['address']}")]
    if rep["stories"] is not None:
        lines.append(("Number of stories", f"{int(rep['stories'])}"))
    if rep["basement"] is not None:
        lines.append(("Basement", "Yes" if rep["basement"] else "No"))
    if rep["ground_ft"] is not None:
        lines.append(("Ground elevation", f"{rep['ground_ft']:.1f} ft above sea level"))
    if rep["ffe_ft"] is not None:
        lines.append(("First-floor elevation", f"{rep['ffe_ft']:.1f} ft above sea level"))
    if rep["lowest_ft"] is not None:
        note = " (basement at grade)" if rep["lowest_is_basement"] else ""
        lines.append(("Lowest-floor elevation", f"{rep['lowest_ft']:.1f} ft above sea level{note}"))
    lines.append(("Recommended level of lowest floor (DFE)", f"{rep['dfe_ft']:.1f} ft above sea level"))
    fema = rep.get("fema") or {}
    if fema.get("zone"):
        z = fema["zone"]
        if fema.get("subtype"):
            z += f" ({fema['subtype']})"
        lines.append(("FEMA flood zone", z))
    lines.append(("BFE used in this report", f"{rep['bfe_ft']:g} ft"))
    return lines


def shortfall_sentence(rep):
    s = rep.get("shortfall_ft")
    if s is None:
        return ""
    if s > 0.05:
        return (f"Your lowest floor sits {s:.1f} feet below the recommended level "
                f"({rep['dfe_ft']:.1f} ft above sea level).")
    return (f"Your lowest floor is at or above the recommended level "
            f"({rep['dfe_ft']:.1f} ft above sea level).")


def elevate_sentence(rep):
    s = rep.get("shortfall_ft")
    if s is None or s <= 0.05:
        return ""
    return (f"If you elevate the utilities or the home, it should be to at least {rep['dfe_ft']:.1f} ft above "
            f"sea level, or {s:.1f} ft above the ground.")


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------
def render_pdf(rep):
    """Return the report as PDF bytes."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import (HRFlowable, ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=0.9 * inch, rightMargin=0.9 * inch,
                            topMargin=0.8 * inch, bottomMargin=0.8 * inch,
                            title=f"Flood Damage and Adaptation Analysis - {rep['address']}",
                            author="ADAPT, Columbia Climate School")
    ss = getSampleStyleSheet()
    navy = colors.HexColor("#0f172a")
    sky = colors.HexColor("#0369a1")
    body = ParagraphStyle("body", parent=ss["BodyText"], fontName="Helvetica", fontSize=10, leading=14,
                          spaceAfter=6, alignment=TA_LEFT)
    small = ParagraphStyle("small", parent=body, fontSize=8.5, leading=11, textColor=colors.HexColor("#475569"))
    h1 = ParagraphStyle("h1", parent=ss["Title"], fontName="Helvetica-Bold", fontSize=17, leading=21,
                        textColor=navy, alignment=TA_LEFT, spaceAfter=2)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontName="Helvetica-Bold", fontSize=12.5, leading=16,
                        textColor=navy, spaceBefore=12, spaceAfter=4)
    sub = ParagraphStyle("sub", parent=body, fontSize=11.5, leading=15, textColor=sky, spaceAfter=0)
    ital = ParagraphStyle("ital", parent=body, fontName="Helvetica-Oblique", textColor=colors.HexColor("#334155"))
    bold_lead = ParagraphStyle("bl", parent=body, fontName="Helvetica-Bold", spaceBefore=4, spaceAfter=2)

    def bullets(items, style=body):
        return ListFlowable([ListItem(Paragraph(t, style), leftIndent=12) for t in items],
                            bulletType="bullet", start="•", leftIndent=14, bulletFontSize=8)

    story = [
        Paragraph("Flood Damage and Adaptation Analysis", h1),
        Paragraph(f"{rep['address']}", sub),
        Paragraph(f"NSI ID: {rep['building_id']} &nbsp;·&nbsp; {rep['location']}, {rep['state']} &nbsp;·&nbsp; "
                  f"{rep['scenario_word'].capitalize()} sea-level rise &nbsp;·&nbsp; generated {rep['generated']}", small),
        HRFlowable(width="100%", thickness=1.2, color=sky, spaceBefore=6, spaceAfter=8),
    ]
    for p in intro_paragraphs(rep):
        story.append(Paragraph(p, body))

    # 1. House information
    story += [Paragraph("1. House Information", h2),
              HRFlowable(width="100%", thickness=0.6, color=sky, spaceAfter=4)]
    story.append(bullets([f"<b>{k}:</b> {v}" for k, v in house_lines(rep)]))
    if shortfall_sentence(rep):
        story.append(Paragraph(shortfall_sentence(rep), body))

    # 2. Flood depths
    story += [Paragraph("2. Expected Future Flood Depth Above Ground", h2),
              HRFlowable(width="100%", thickness=0.6, color=sky, spaceAfter=4)]
    if rep["depth_rows"]:
        story.append(Paragraph(
            "How high the water is expected to rise during different flood scenarios relative to the "
            f"<b>ground</b> at your house, based on {rep['scenario_word']} sea-level rise predictions.", ital))
        for y in rep["horizons"]:
            story.append(Paragraph(f"By {y}", bold_lead))
            story.append(bullets([f"{r['label']}: {depth_phrase(r['by_year'][y]['above_ground_ft'])}"
                                  for r in rep["depth_rows"]]))
    else:
        story.append(Paragraph("Present-day flood levels for this community have not been entered yet, so "
                               "this section is not available.", ital))

    # 3+. Damage by action
    n = 3
    for y in rep["horizons"]:
        story += [Paragraph(f"{n}. Cumulative Damage Depending on Action, by {y}", h2),
                  HRFlowable(width="100%", thickness=0.6, color=sky, spaceAfter=4)]
        lead = (f"Expected flood damage from now through {y}, depending on the action that you take, based on "
                f"{rep['scenario_word']} sea-level rise. The expected value is the median of 1,000 simulated "
                f"futures; the range runs from the 5th to the 95th percentile.")
        if elevate_sentence(rep):
            lead += " " + elevate_sentence(rep)
        story.append(Paragraph(lead, ital))
        items = []
        for it in rep["damage"].get(y, []):
            line = f"{it['label']}: expected {money(it['p50'])}, range {money(it['p05'])} – {money(it['p95'])}"
            if it["avoided"] is not None:
                line += f"<br/>&nbsp;&nbsp;&nbsp;&nbsp;– Avoids {it['avoided'] * 100:.0f}% of the expected damage costs"
            items.append(line)
        story.append(bullets(items) if items else Paragraph("No results for this horizon.", ital))
        n += 1

    story += [Spacer(1, 10),
              HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#cbd5e1"), spaceAfter=4),
              Paragraph("ADAPT - Assessment of Damage and Adaptation Planning Tool. Center for Climate Systems "
                        "Research, The Climate School, Columbia University. Damage estimates use FEMA depth-damage "
                        "functions, National Structure Inventory building data checked by the project team, and "
                        "1,000 Monte Carlo simulations of annual maximum water levels with sea-level rise. They are "
                        "planning estimates, not appraisals.", small)]
    doc.build(story)
    return buf.getvalue()
