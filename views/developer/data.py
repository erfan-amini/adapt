"""Developer: locations, per-location settings, and the input files the model reads."""
import io
import os
import re
import datetime as _dt

import numpy as np
import pandas as pd
import requests
import streamlit as st

from adapt_core import config as C
from adapt_core import ui
from adapt_core.bundle import bundle_folder_for_slug, bundle_status
from adapt_core.engine import DamageCurves, nsi_to_soid

NSI_REQUIRED = ["id", "occupancy_type", "number_of_stories", "foundation_type", "foundation_height",
                "ground_elevation", "structure_value", "content_value"]
NSI_OPTIONAL = ["building_type", "area", "year_built", "address", "longitude", "latitude"]
NSI_ORDER = ["ID", "occupancy_type", "building_type", "number_of_stories", "area", "foundation_type",
             "foundation_height", "year_built", "ground_elevation", "address", "longitude", "latitude",
             "structure_value", "content_value"]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _fmt_size(n):
    if not n:
        return "-"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:,.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _fmt_time(ts):
    return _dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "-"


def _all_slugs(ctx):
    slugs = set(ctx.registry.keys())
    for name, entry in ctx.data_store.items():
        slugs.add(entry.get("location_slug", ui.slugify(name)))
    if os.path.isdir(C.DATA_DIR):
        for d in os.listdir(C.DATA_DIR):
            if os.path.isdir(os.path.join(C.DATA_DIR, d)) and not d.startswith(("_", ".")):
                slugs.add(d)
    return sorted(slugs)


def _read_nsi_upload(upload):
    name = upload.name.lower()
    if name.endswith((".xlsx", ".xls")):
        return pd.read_excel(upload)
    return pd.read_csv(upload)


def validate_nsi(df, curves=None):
    """Normalise column names to the NSI schema and report problems.
    Returns (clean_df, report dict)."""
    rep = {"errors": [], "warnings": [], "info": []}
    d = df.copy()
    d.columns = [str(c).strip() for c in d.columns]
    lower = {c.lower(): c for c in d.columns}
    missing = [c for c in NSI_REQUIRED if c not in lower]
    if missing:
        rep["errors"].append(f"Missing required column(s): {', '.join(missing)}")
        return None, rep
    rename = {lower[c]: ("ID" if c == "id" else c) for c in NSI_REQUIRED + NSI_OPTIONAL if c in lower}
    d = d.rename(columns=rename)
    for c in ["number_of_stories", "foundation_height", "ground_elevation", "structure_value", "content_value",
              "longitude", "latitude", "area", "year_built"]:
        if c in d.columns:
            d[c] = pd.to_numeric(d[c], errors="coerce")
    n = len(d)
    if d["ID"].isna().any():
        rep["errors"].append(f"{int(d['ID'].isna().sum())} row(s) have no id.")
    dup = d["ID"].duplicated().sum()
    if dup:
        rep["errors"].append(f"{int(dup)} duplicate id(s).")
    bad = (~np.isfinite(d["foundation_height"]) | ~np.isfinite(d["ground_elevation"]) |
           ~np.isfinite(d["number_of_stories"]) | (d["number_of_stories"] <= 0))
    if bad.any():
        rep["warnings"].append(f"{int(bad.sum())} row(s) have missing stories, foundation height or ground "
                               "elevation and will be skipped (invalid_data).")
    badv = ~np.isfinite(d["structure_value"]) | ~np.isfinite(d["content_value"])
    if badv.any():
        rep["warnings"].append(f"{int(badv.sum())} row(s) have missing structure or content value and will be "
                               "skipped (invalid_value).")
    if "longitude" not in d.columns or "latitude" not in d.columns:
        rep["warnings"].append("No longitude/latitude columns: maps will not work for this location.")
    occ = d["occupancy_type"].astype(str).str.strip()
    n_res = int(occ.str.upper().str.startswith("RES").sum())
    rep["info"].append(f"{n:,} buildings: {n_res:,} residential, {n - n_res:,} non-residential.")
    fb = 0
    nocurve = {}
    for o, f, s in zip(occ, d["foundation_type"].astype(str), d["number_of_stories"].fillna(1)):
        soid, used_fb = nsi_to_soid(o, f, s)
        fb += int(used_fb)
        if curves is not None:
            sc, cc, sid, cid = curves.get(soid, C.FLOOD_ZONE_DEFAULT)
            if sc is None:
                nocurve[soid] = nocurve.get(soid, 0) + 1
    if fb:
        rep["warnings"].append(f"{fb} building(s) have an unknown occupancy type and will be scored as RES1 "
                               "(occupancy fallback).")
    if nocurve:
        rep["warnings"].append("No damage curve for SOID(s) " +
                               ", ".join(f"{k} ({v})" for k, v in nocurve.items()) + "; these are skipped.")
    cols = [c for c in NSI_ORDER if c in d.columns] + [c for c in d.columns if c not in NSI_ORDER]
    return d[cols], rep


def _show_report(rep):
    for e in rep["errors"]:
        st.error(e)
    for w in rep["warnings"]:
        st.warning(w)
    for i in rep["info"]:
        st.info(i)


def _save_nsi(df, folder, slug):
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"DDD___{slug}___NSI.xlsx")
    if os.path.exists(path):
        bak = os.path.join(folder, f"DDD___{slug}___NSI.backup_{_dt.datetime.now():%Y%m%d_%H%M%S}.xlsx")
        os.replace(path, bak)
    df.to_excel(path, index=False, sheet_name="Sheet1")
    return path


def validate_wl(df, label):
    rep = {"errors": [], "warnings": [], "info": []}
    cols = [str(c).strip() for c in df.columns]
    df.columns = cols
    ycol = next((c for c in cols if c.lower() == "year"), None)
    if ycol is None:
        rep["errors"].append(f"{label}: no 'Year' column.")
        return None, rep
    mc = [c for c in cols if c != ycol]
    years = pd.to_numeric(df[ycol], errors="coerce")
    if years.isna().any():
        rep["errors"].append(f"{label}: non-numeric years.")
    arr = df[mc].apply(pd.to_numeric, errors="coerce")
    if arr.isna().any().any():
        rep["warnings"].append(f"{label}: {int(arr.isna().sum().sum())} missing water-level cell(s).")
    rep["info"].append(f"{label}: {len(df)} years ({int(years.min())}..{int(years.max())}) x {len(mc)} Monte Carlo runs; "
                       f"median annual max {np.nanmedian(arr.to_numpy()):.2f} ft NAVD88.")
    out = pd.concat([years.astype(int).rename("Year"), arr], axis=1)
    return out, rep


@st.cache_resource(show_spinner=False)
def _curves_or_none():
    try:
        return DamageCurves(C.CURVES_DIR)
    except Exception:
        return None


def _survey_sheet_url():
    """The survey workbook URL the embedded NSI tool writes to (read from
    assets/nsi_tool.html so the two stay in sync)."""
    try:
        with open(C.NSI_TOOL_HTML, "r", encoding="utf-8") as fh:
            m = re.search(r"https://docs\.google\.com/spreadsheets/d/[A-Za-z0-9_-]+[^\s\"']*", fh.read())
        return m.group(0) if m else ""
    except Exception:
        return ""


def _sheet_csv_url(url_or_id, gid):
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", url_or_id)
    sid = m.group(1) if m else url_or_id.strip()
    g = re.search(r"[#&?]gid=(\d+)", url_or_id)
    gid = gid or (g.group(1) if g else "0")
    return f"https://docs.google.com/spreadsheets/d/{sid}/export?format=csv&gid={gid}"


# ---------------------------------------------------------------------------
# page
# ---------------------------------------------------------------------------
def render(ctx):
    ss = ctx.ss
    ui.page_header(
        "Data and settings",
        "Manage the building inventories, water levels and per-location settings the damage model reads. "
        "Files live under <code>data/&lt;Location&gt;/</code>; the app reloads a bundle automatically when any of "
        "its files change.",
        module_key="developer", location=ctx.location_name,
    )
    st.info("Streamlit Community Cloud resets its disk on every reboot. Files saved here last until then; "
            "use **Run the model → Publish to GitHub** to make inputs and results permanent.", icon="💾")

    reg = ctx.registry
    slugs = _all_slugs(ctx)

    # ---- which location to manage -------------------------------------
    cur_slug = (ctx.loc_entry or {}).get("location_slug") if ctx.loc_entry else None
    if cur_slug not in slugs and slugs:
        cur_slug = slugs[0]
    c1, c2 = st.columns([0.45, 0.55])
    with c1:
        slug = st.selectbox("Location to manage", slugs, index=slugs.index(cur_slug) if cur_slug in slugs else 0,
                            format_func=lambda s: f"{reg.get(s, {}).get('name', s)}  ({s})", key="dev_slug")
    folder = bundle_folder_for_slug(slug)
    cfg = dict(reg.get(slug, {"name": slug}))

    with c2:
        with st.expander("➕ Add a new location"):
            with st.form("add_loc"):
                new_name = st.text_input("Display name", placeholder="e.g. Howard Beach")
                new_slug = st.text_input("Folder slug (letters and digits)", placeholder="e.g. HowardBeach")
                nb, ns = st.columns(2)
                new_bfe = nb.number_input("BFE (ft NAVD88)", value=8.0, step=0.5)
                new_short = ns.number_input("Elevation shortfall (ft)", value=0.0, step=0.5, min_value=0.0)
                ok = st.form_submit_button("Create location", type="primary")
            if ok:
                s_ = new_slug.strip() or ui.slugify(new_name)
                if not new_name.strip() or not re.fullmatch(r"[A-Za-z0-9]+", s_):
                    st.error("Give a display name and a slug made of letters and digits only.")
                elif s_ in reg:
                    st.error(f"Slug {s_} already exists.")
                else:
                    reg[s_] = {"name": new_name.strip(), "bfe_ft": float(new_bfe),
                               "elev_min_shortfall_ft": float(new_short), "restrict_manuf": False,
                               "mobile_homes_dominated": False, "protected": False, "state": "", "blurb": ""}
                    ui.save_location_registry(reg)
                    os.makedirs(bundle_folder_for_slug(s_), exist_ok=True)
                    st.success(f"Created {new_name} ({s_}). Upload its inventory and water levels below.")
                    st.rerun()

    # ---- settings -------------------------------------------------------
    st.subheader(f"Settings for {cfg.get('name', slug)}")
    with st.form("loc_settings"):
        a, b, c, d = st.columns([1.4, 1, 1, 1])
        name = a.text_input("Display name", value=cfg.get("name", slug))
        bfe = b.number_input("BFE (ft NAVD88)", value=float(cfg.get("bfe_ft", 8.0)), step=0.5,
                             help="Base flood elevation. The design flood elevation (DFE) is BFE + 2 ft and is the "
                                  "elevation target.")
        short = c.number_input("Elevation shortfall (ft)", value=float(cfg.get("elev_min_shortfall_ft", 0.0)),
                               step=0.5, min_value=0.0,
                               help="Elevation is offered only when FFE < DFE - shortfall. 0 = any building under "
                                    "the DFE; 1 = buildings within 1 ft of the DFE are not advised to elevate.")
        state = d.text_input("State", value=cfg.get("state", ""))
        e, f, g = st.columns(3)
        mobile = e.checkbox("Mobile-homes-dominated area (default for the pages' toggle)",
                            value=bool(cfg.get("mobile_homes_dominated", False)))
        restrict = f.checkbox("Listed in RESTRICT_MANUF_LOCATIONS (reporting flag only)",
                              value=bool(cfg.get("restrict_manuf", False)),
                              help="Since v12 the RES2/RES4 retrofit restriction is occupancy-driven everywhere; "
                                   "this flag is written to the metadata for transparency.")
        protected = g.checkbox("Password protected (results shown only after the access password)",
                               value=bool(cfg.get("protected", False)),
                               help="The access password comes from Streamlit secrets ([access] password) and "
                                    "falls back to the project's long-standing password.")
        blurb = st.text_area("Short description shown on the Home and Overview pages", value=cfg.get("blurb", ""),
                             height=90)
        saved = st.form_submit_button("Save settings", type="primary")
    if saved:
        reg[slug] = {**cfg, "name": name.strip() or slug, "bfe_ft": float(bfe), "elev_min_shortfall_ft": float(short),
                     "state": state.strip(), "mobile_homes_dominated": bool(mobile), "restrict_manuf": bool(restrict),
                     "protected": bool(protected), "blurb": blurb.strip()}
        ui.save_location_registry(reg)
        st.success("Settings saved to data/locations.json. They apply to the next model run.")

    # ---- file status ----------------------------------------------------
    st.subheader("Files")
    status = bundle_status(folder, slug)
    labels = {"nsi": "Building inventory (NSI)", "wl_p50": "Water levels P50 (median SLR)",
              "wl_p90": "Water levels P90 (high-end SLR)", "metadata": "Results: metadata",
              "lookup": "Results: building lookup", "cumdmg": "Results: per-building cumulative damage",
              "categories": "Results: category totals", "skipped": "Results: skipped buildings",
              "roads": "OpenStreetMap roads (Road maps page)"}
    rows = [{"File": labels[k], "Name": v["file"], "Present": "✅" if v["exists"] else "❌",
             "Size": _fmt_size(v["size"]), "Modified": _fmt_time(v["mtime"])} for k, v in status.items()]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    inputs_ok = all(status[k]["exists"] for k in ("nsi", "wl_p50", "wl_p90"))
    results_ok = all(status[k]["exists"] for k in ("metadata", "lookup", "cumdmg", "categories"))
    if inputs_ok and not results_ok:
        st.warning("Inputs are present but there are no results yet. Go to **Run the model**.")
    elif inputs_ok and results_ok:
        newer = max(status[k]["mtime"] for k in ("nsi", "wl_p50", "wl_p90")) > status["metadata"]["mtime"]
        if newer:
            st.warning("An input file is newer than the results. Re-run the model so the pages show current numbers.")
        else:
            st.success("Inputs and results are present and the results are up to date.")

    # ---- building inventory ---------------------------------------------
    st.subheader("Building inventory (NSI)")
    curves = _curves_or_none()
    t_up, t_sheet, t_edit = st.tabs(["⬆️ Upload a file", "📄 Pull from Google Sheet", "✏️ View and edit"])

    with t_up:
        st.caption("Accepted: .xlsx or .csv with columns " + ", ".join(NSI_REQUIRED) +
                   " (plus optional " + ", ".join(NSI_OPTIONAL) + "). Column names are matched case-insensitively.")
        up = st.file_uploader("Inventory file", type=["xlsx", "xls", "csv"], key="nsi_upload")
        if up is not None:
            try:
                raw = _read_nsi_upload(up)
            except Exception as e:
                st.error(f"Could not read the file: {e}")
                raw = None
            if raw is not None:
                clean, rep = validate_nsi(raw, curves)
                _show_report(rep)
                if clean is not None:
                    st.dataframe(clean.head(25), use_container_width=True, hide_index=True)
                    if st.button(f"Save as DDD___{slug}___NSI.xlsx", type="primary", key="nsi_save_upload"):
                        p = _save_nsi(clean, folder, slug)
                        st.success(f"Saved {os.path.relpath(p, C.APP_ROOT)} ({len(clean):,} rows). "
                                   "Any previous file was kept as a backup next to it. Re-run the model to update results.")

    with t_sheet:
        st.caption("The sheet must be shared as 'Anyone with the link can view'. Paste its URL (the gid in the URL "
                   "selects the tab) and map its columns to the NSI schema.")
        sheet_url = st.text_input("Google Sheet URL or ID", key="sheet_url", value=_survey_sheet_url(),
                                  placeholder="https://docs.google.com/spreadsheets/d/.../edit#gid=0")
        gid = st.text_input("Tab gid (optional, overrides the URL)", key="sheet_gid", placeholder="e.g. 162864560")
        if st.button("Fetch sheet", key="sheet_fetch") and sheet_url.strip():
            try:
                r = requests.get(_sheet_csv_url(sheet_url, gid.strip()), timeout=30)
                r.raise_for_status()
                ss["_sheet_df"] = pd.read_csv(io.StringIO(r.text))
                st.success(f"Fetched {len(ss['_sheet_df']):,} rows x {ss['_sheet_df'].shape[1]} columns.")
            except Exception as e:
                st.error(f"Could not fetch the sheet: {e}")
        sdf = ss.get("_sheet_df")
        if sdf is not None:
            st.dataframe(sdf.head(10), use_container_width=True, hide_index=True)
            st.markdown("**Column mapping** (sheet column → NSI column)")
            scols = ["(none)"] + list(sdf.columns)
            mapping = {}
            grid = st.columns(4)
            for i, target in enumerate(NSI_REQUIRED + NSI_OPTIONAL):
                guess = next((c for c in sdf.columns if str(c).strip().lower().replace(" ", "_") == target), "(none)")
                if guess == "(none)" and target == "id":
                    guess = next((c for c in sdf.columns if str(c).strip().lower() in ("building_id", "bldg_id", "nsi_id", "fd_id")), "(none)")
                with grid[i % 4]:
                    mapping[target] = st.selectbox(target, scols, index=scols.index(guess), key=f"map_{target}")
            if st.button("Build inventory from sheet", key="sheet_build", type="primary"):
                picked = {t: c for t, c in mapping.items() if c != "(none)"}
                built = pd.DataFrame({t: sdf[c] for t, c in picked.items()})
                clean, rep = validate_nsi(built, curves)
                _show_report(rep)
                if clean is not None:
                    ss["_sheet_clean"] = clean
            if ss.get("_sheet_clean") is not None:
                st.dataframe(ss["_sheet_clean"].head(25), use_container_width=True, hide_index=True)
                if st.button(f"Save as DDD___{slug}___NSI.xlsx", type="primary", key="nsi_save_sheet"):
                    p = _save_nsi(ss["_sheet_clean"], folder, slug)
                    st.success(f"Saved {os.path.relpath(p, C.APP_ROOT)} ({len(ss['_sheet_clean']):,} rows). "
                               "Re-run the model to update results.")

    with t_edit:
        nsi_path = status["nsi"]["path"]
        if not status["nsi"]["exists"]:
            st.info("No inventory file for this location yet.")
        else:
            cur = pd.read_excel(nsi_path)
            st.caption(f"{len(cur):,} rows. Edit cells, add or delete rows, then save. Large inventories "
                       "(thousands of rows) are easier to edit in Excel and re-uploaded.")
            edited = st.data_editor(cur, use_container_width=True, num_rows="dynamic", key="nsi_editor", height=420)
            if st.button("Save edited inventory", type="primary", key="nsi_save_edit"):
                clean, rep = validate_nsi(edited, curves)
                _show_report(rep)
                if clean is not None and not rep["errors"]:
                    p = _save_nsi(clean, folder, slug)
                    st.success(f"Saved {os.path.relpath(p, C.APP_ROOT)}. Re-run the model to update results.")
            st.download_button("Download current inventory (.xlsx)", data=open(nsi_path, "rb").read(),
                               file_name=os.path.basename(nsi_path), key="nsi_dl")

    # ---- water levels -----------------------------------------------------
    st.subheader("Monte Carlo water levels")
    st.caption("One CSV per sea-level rise scenario: a `Year` column followed by one column per Monte Carlo "
               "realization (MC_0001 ... MC_1000) of the annual maximum water level in ft NAVD88.")
    w1, w2 = st.columns(2)
    for col, key, label in ((w1, "wl_p50", "P50 (median SLR)"), (w2, "wl_p90", "P90 (high-end SLR)")):
        with col:
            upw = st.file_uploader(f"Water levels {label}", type=["csv"], key=f"up_{key}")
            if upw is not None:
                try:
                    dfw, rep = validate_wl(pd.read_csv(upw), label)
                except Exception as e:
                    dfw, rep = None, {"errors": [str(e)], "warnings": [], "info": []}
                _show_report(rep)
                if dfw is not None and not rep["errors"]:
                    if st.button(f"Save {status[key]['file']}", key=f"save_{key}", type="primary"):
                        os.makedirs(folder, exist_ok=True)
                        dfw.to_csv(status[key]["path"], index=False)
                        st.success(f"Saved {status[key]['file']}.")

    # ---- curve tables -----------------------------------------------------
    with st.expander("Depth-damage curve tables (FEMA/Hazus FAST)"):
        st.caption(f"Shared by every location. Stored in `{os.path.relpath(C.CURVES_DIR, C.APP_ROOT)}/`. "
                   "Replace a table only with a file of the same layout.")
        for fname in ("flBldgStructDmgFinal.csv", "flBldgStructDmgFn.csv", "flBldgContDmgFinal.csv", "flBldgContDmgFn.csv"):
            p = os.path.join(C.CURVES_DIR, fname)
            cA, cB = st.columns([0.5, 0.5])
            cA.markdown(f"`{fname}` {'✅' if os.path.exists(p) else '❌'} "
                        f"{_fmt_size(os.path.getsize(p)) if os.path.exists(p) else ''}")
            upc = cB.file_uploader(f"Replace {fname}", type=["csv"], key=f"up_{fname}", label_visibility="collapsed")
            if upc is not None:
                try:
                    test = pd.read_csv(upc)
                    need = {"SOccupId"} if "Final" in fname else {"Occupancy", "p0", "p24"}
                    if not need.issubset(set(test.columns)):
                        st.error(f"{fname}: expected columns {sorted(need)}.")
                    else:
                        os.makedirs(C.CURVES_DIR, exist_ok=True)
                        test.to_csv(p, index=False)
                        _curves_or_none.clear()
                        st.success(f"Replaced {fname} ({len(test)} rows).")
                except Exception as e:
                    st.error(f"{fname}: {e}")
