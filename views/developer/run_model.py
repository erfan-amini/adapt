"""Developer: run the damage and adaptation model and publish the results."""
import io
import os
import zipfile
import datetime as _dt

import numpy as np
import pandas as pd
import streamlit as st

from adapt_core import config as C
from adapt_core import ui
from adapt_core.bundle import bundle_folder_for_slug, bundle_status
from adapt_core.engine import (DamageCurves, RunSettings, run_location, write_bundle, bundle_files,
                               single_event_damage, ENGINE_VERSION, ACTION_NAMES)
from adapt_core.github_sync import github_config, publish_files
from adapt_core.helpers import fmt_money_short


@st.cache_resource(show_spinner=False)
def _curves():
    return DamageCurves(C.CURVES_DIR)


def _read_inputs(status):
    nsi = pd.read_excel(status["nsi"]["path"])
    w50 = pd.read_csv(status["wl_p50"]["path"])
    w90 = pd.read_csv(status["wl_p90"]["path"])
    return nsi, w50, w90


def _existing_totals(folder, slug):
    """Community No-mitigation P50 totals by (year, SLR) from the bundle on disk."""
    p = os.path.join(folder, f"{slug}_bldg_CumulativeDamage.csv")
    if not os.path.exists(p):
        return None, None
    try:
        d = pd.read_csv(p, usecols=["BuildingID", "TargetYear", "Action", "SLR", "P50"])
    except Exception:
        return None, None
    d = d[d["Action"] == "No mitigation"]
    tot = d.groupby(["TargetYear", "SLR"])["P50"].sum().rename("existing")
    return tot, int(d["BuildingID"].nunique())


def _qa_table(result, folder, slug):
    new = result.bldg_cumdmg
    new = new[new["Action"] == "No mitigation"].copy()
    new["TargetYear"] = new["TargetYear"].astype(int)
    tot = new.groupby(["TargetYear", "SLR"])["P50"].sum().rename("new")
    old, n_old = _existing_totals(folder, slug)
    if old is not None:
        old = old.copy()
        old.index = pd.MultiIndex.from_arrays([old.index.get_level_values(0).astype(int),
                                               old.index.get_level_values(1)], names=["TargetYear", "SLR"])
    tbl = pd.concat([tot, old], axis=1) if old is not None else tot.to_frame()
    tbl = tbl.reset_index()
    tbl["new"] = tbl["new"].map(fmt_money_short)
    if "existing" in tbl.columns:
        tbl["existing"] = tbl["existing"].map(lambda v: fmt_money_short(v) if pd.notna(v) else "-")
    tbl = tbl.rename(columns={"TargetYear": "Target year", "new": "New run: sum of building P50 (No mitigation)",
                              "existing": "Current bundle"})
    return tbl, n_old


def render(ctx):
    ss = ctx.ss
    ui.page_header(
        "Run the model",
        f"Runs the damage and adaptation engine ({ENGINE_VERSION}) on a location's inventory and Monte Carlo "
        "water levels, writes the result bundle the other modules read, and publishes it to GitHub so the "
        "deployed app picks it up.",
        module_key="developer", location=ctx.location_name,
    )
    reg = ctx.registry
    slugs = sorted(set(reg) | {e.get("location_slug") for e in ctx.data_store.values() if e.get("location_slug")})
    cur_slug = (ctx.loc_entry or {}).get("location_slug") if ctx.loc_entry else None
    slug = st.selectbox("Location", slugs, index=slugs.index(cur_slug) if cur_slug in slugs else 0,
                        format_func=lambda s: f"{reg.get(s, {}).get('name', s)}  ({s})", key="run_slug")
    cfg = reg.get(slug, {"name": slug, "bfe_ft": 8.0, "elev_min_shortfall_ft": 0.0})
    folder = bundle_folder_for_slug(slug)
    status = bundle_status(folder, slug)

    # ---- inputs check -----------------------------------------------------
    missing = [status[k]["file"] for k in ("nsi", "wl_p50", "wl_p90") if not status[k]["exists"]]
    curves_ok = all(os.path.exists(os.path.join(C.CURVES_DIR, f)) for f in
                    ("flBldgStructDmgFinal.csv", "flBldgStructDmgFn.csv", "flBldgContDmgFinal.csv", "flBldgContDmgFn.csv"))
    k1, k2, k3, k4 = st.columns(4)
    with k1:
        ui.kpi("Inventory", "ready" if status["nsi"]["exists"] else "missing", status["nsi"]["file"])
    with k2:
        ui.kpi("Water levels", "ready" if status["wl_p50"]["exists"] and status["wl_p90"]["exists"] else "missing",
               "P50 and P90 Monte Carlo CSVs")
    with k3:
        ui.kpi("Curve tables", "ready" if curves_ok else "missing", "FEMA/Hazus FAST")
    with k4:
        ui.kpi("Current results", "present" if status["metadata"]["exists"] else "none",
               f"written {_dt.datetime.fromtimestamp(status['metadata']['mtime']):%Y-%m-%d}" if status["metadata"]["exists"] else "")
    if missing or not curves_ok:
        st.error("Missing input(s): " + ", ".join(missing + ([] if curves_ok else ["curve tables"])) +
                 ". Add them on the Data and settings page.")
        return

    # ---- run settings -------------------------------------------------------
    st.subheader("Run settings")
    with st.form("run_form"):
        a, b, c, d = st.columns(4)
        bfe = a.number_input("BFE (ft NAVD88)", value=float(cfg.get("bfe_ft", 8.0)), step=0.5)
        short = b.number_input("Elevation shortfall (ft)", value=float(cfg.get("elev_min_shortfall_ft", 0.0)),
                               step=0.5, min_value=0.0)
        util = c.number_input("Utilities replacement cost ($)", value=float(C.UTIL_REPL_USD_DEFAULT), step=1000.0)
        story = d.number_input("Story height (ft)", value=float(C.STORY_H_FT_DEFAULT), step=0.5)
        e, f, g = st.columns([1, 1, 2])
        zone = e.selectbox("Flood zone (curve set)", ["A", "V", "Riverine"], index=0)
        occf = f.selectbox("Buildings to run", ["ALL", "RES", "NONRES"], index=0)
        years_txt = g.text_input("Target years", value=", ".join(str(y) for y in C.TARGET_YEARS_DEFAULT),
                                 help="Cumulative damage is reported from the first water-level year to each target year.")
        add_story = st.checkbox("One-story homes: score 'WFP 1st' as an add-a-story conversion (v13/v14 rule)", value=True)
        st.caption(f"Reporting flag RESTRICT_MANUF_LOC = {int(bool(cfg.get('restrict_manuf', False)))} "
                   "(the RES2/RES4 retrofit restriction itself is occupancy-driven and always applied).")
        go_ = st.form_submit_button("▶️ Run the model", type="primary")

    if go_:
        try:
            target_years = sorted({int(y.strip()) for y in years_txt.split(",") if y.strip()})
        except ValueError:
            st.error("Target years must be a comma-separated list of years.")
            return
        settings = RunSettings(location=slug, bfe_ft=float(bfe), elev_min_shortfall_ft=float(short),
                               restrict_manuf_loc=bool(cfg.get("restrict_manuf", False)),
                               one_story_add_story=bool(add_story), flood_zone=zone,
                               util_repl_usd=float(util), story_h_ft=float(story),
                               target_years=target_years, occupancy_filter=occf)
        bar = st.progress(0.0, text="Reading inputs…")
        try:
            nsi, w50, w90 = _read_inputs(status)
            curves = _curves()
            curves.warnings.clear()

            def _prog(i, n, msg):
                bar.progress(min(1.0, i / max(1, n)), text=msg)
            result = run_location(nsi, w50, w90, curves, settings, progress=_prog)
            bar.progress(1.0, text="Done")
        except Exception as e:
            bar.empty()
            st.exception(e)
            return
        ss["run_result"] = result
        ss["run_slug_done"] = slug
        ss["run_saved"] = False

    result = ss.get("run_result")
    if result is None or ss.get("run_slug_done") != slug:
        with st.expander("Check one building (single water level)"):
            _single_building_check(ctx, slug, cfg)
        return

    # ---- results ------------------------------------------------------------
    st.subheader("Run summary")
    for line in result.summary_lines():
        st.markdown(f"- {line}")
    for w in result.warnings:
        st.warning(w)
    if len(result.skipped):
        with st.expander(f"Skipped buildings ({len(result.skipped)})"):
            st.dataframe(result.skipped, use_container_width=True, hide_index=True)
    if len(result.occupancy_fallback):
        with st.expander(f"Buildings reclassified as RES1 ({len(result.occupancy_fallback)})"):
            st.dataframe(result.occupancy_fallback, use_container_width=True, hide_index=True)
    if len(result.missing_values):
        with st.expander(f"Buildings without structure or content value ({len(result.missing_values)}): kept in the "
                         "inventory, no damage estimate, excluded from community totals"):
            st.dataframe(result.missing_values, use_container_width=True, hide_index=True)

    st.subheader("Quality checks")
    tbl, n_old = _qa_table(result, folder, slug)
    if n_old is not None:
        st.caption(f"Current bundle on disk covers {n_old:,} buildings; this run covers {result.n_processed:,}.")
    st.dataframe(tbl, use_container_width=True, hide_index=True)
    lk = result.bldg_lookup
    q1, q2, q3, q4 = st.columns(4)
    with q1:
        ui.kpi("Under DFE", f"{int((lk['DFE_Status'] == 'Under DFE').sum()):,}", "first floor below BFE + 2 ft")
    with q2:
        ui.kpi("Elevation eligible", f"{int(lk['ElevationEligible'].sum()):,}", "FFE below the elevation threshold")
    with q3:
        ui.kpi("Add-a-story (WFP 1st)", f"{int(lk['OneStoryAddStory'].sum()):,}", "one-story homes")
    with q4:
        ui.kpi("Manufactured / temporary", f"{int(lk['ManufRestriction'].sum()):,}", "RES2 / RES4 restriction")

    with st.expander("Preview: per-building cumulative damage (first 200 rows)"):
        st.dataframe(result.bldg_cumdmg.head(200), use_container_width=True, hide_index=True)
    with st.expander("Preview: category totals"):
        st.dataframe(result.categories, use_container_width=True, hide_index=True)

    # ---- save / download / publish --------------------------------------------
    st.subheader("Save and publish")
    s1, s2, s3 = st.columns(3)
    with s1:
        if st.button("💾 Save results to this app", type="primary", key="run_save",
                     help=f"Writes the bundle to data/{slug}/ and replaces the current results. The pages reload automatically."):
            written = write_bundle(result, folder, slug)
            ss["run_saved"] = True
            st.success(f"Wrote {len(written)} files to data/{slug}/.")
            st.cache_data.clear()
    with s2:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name, text in bundle_files(result, slug).items():
                z.writestr(f"{slug}/{name}", text)
        st.download_button("⬇️ Download results (.zip)", data=buf.getvalue(),
                           file_name=f"{slug}_results_{_dt.datetime.now():%Y%m%d}.zip", key="run_zip")
    with s3:
        gcfg = github_config()
        if not gcfg["ready"]:
            st.button("☁️ Publish to GitHub", disabled=True, key="run_pub_disabled")
            st.caption("Add `[github] token`, `repo` and `branch` to Streamlit secrets to enable publishing.")
        else:
            include_inputs = st.checkbox("Include inputs (inventory, water levels, settings)", value=True, key="pub_inputs")
            if st.button("☁️ Publish to GitHub", key="run_pub", type="primary"):
                files = {f"data/{slug}/{n}": t for n, t in bundle_files(result, slug).items()}
                if include_inputs:
                    for k in ("nsi", "wl_p50", "wl_p90"):
                        with open(status[k]["path"], "rb") as fh:
                            files[f"data/{slug}/{status[k]['file']}"] = fh.read()
                    if os.path.exists(C.LOCATIONS_FILE):
                        with open(C.LOCATIONS_FILE, "rb") as fh:
                            files["data/locations.json"] = fh.read()
                pbar = st.progress(0.0, text="Publishing…")
                try:
                    sha, url = publish_files(
                        files, f"Update {slug} results ({ENGINE_VERSION}, {_dt.datetime.now():%Y-%m-%d})", gcfg,
                        progress=lambda i, n, m: pbar.progress(min(1.0, i / max(1, n)), text=m))
                    st.success(f"Published commit `{sha[:7]}` to {gcfg['repo']}@{gcfg['branch']}. "
                               f"Streamlit Cloud redeploys from it within a minute or two. [View commit]({url})")
                except Exception as e:
                    pbar.empty()
                    st.error(f"Publishing failed: {e}")

    with st.expander("Check one building (single water level)"):
        _single_building_check(ctx, slug, cfg)


def _single_building_check(ctx, slug, cfg):
    """The FFF estimator: one building, one water level, every action."""
    st.caption("Independent spot check of the damage transform for one building and one still-water level, "
               "the same arithmetic the batch run applies year by year.")
    c1, c2, c3, c4 = st.columns(4)
    occ = c1.text_input("Occupancy type", value="RES1-1SNB", key="sb_occ")
    ftype = c2.selectbox("Foundation type", ["B", "C", "S", "P", "I", "F", "W"], index=1, key="sb_ft",
                         help="B = basement, C = crawl space, S = slab, P = pier, I = pile, F = fill, W = solid wall")
    nst = c3.number_input("Stories", value=1, min_value=1, step=1, key="sb_nst")
    wl = c4.number_input("Water level (ft NAVD88)", value=float(cfg.get("bfe_ft", 8.0)) + 1.0, step=0.5, key="sb_wl")
    d1, d2, d3, d4 = st.columns(4)
    g = d1.number_input("Ground elevation (ft)", value=5.0, step=0.5, key="sb_g")
    fh = d2.number_input("Foundation height (ft)", value=1.5, step=0.5, key="sb_fh")
    sv = d3.number_input("Structure value ($)", value=250000.0, step=10000.0, key="sb_sv")
    cv = d4.number_input("Content value ($)", value=125000.0, step=10000.0, key="sb_cv")
    try:
        cur = _curves()
    except Exception as e:
        st.error(f"Curve tables unavailable: {e}")
        return
    rows = []
    for a in ACTION_NAMES:
        r = single_event_damage(occ, ftype, nst, g, fh, wl, a, sv, cv, cur, float(cfg.get("bfe_ft", 8.0)),
                                float(cfg.get("elev_min_shortfall_ft", 0.0)))
        if "error" in r:
            st.error(f"{r['error']} for SOID {r['soid']}")
            return
        rows.append({"Action": C.ACTION_LABELS.get(a, a), "Damage": fmt_money_short(r["total_usd"]),
                     "Saving vs no mitigation": fmt_money_short(r["baseline_usd"] - r["total_usd"])})
    st.caption(f"SOID {r['soid']} · FFE {r['ffe_ft']:.2f} ft · depth above first floor {r['depth_ft']:.2f} ft · "
               f"elevation eligible: {'yes' if r['elevation_eligible'] else 'no'}")
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
