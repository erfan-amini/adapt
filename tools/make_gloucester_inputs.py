"""Build the ADAPT model inputs for Gloucester County, VA.

Sources (data/Gloucester/source/, from Molly Mitchell, VIMS, Oct/Nov 2026)
- Gloucester_Recurrance_1.xlsx
    'NACCS data'      : NACCS save point 17719 (37.3265 N, 76.3947 W), annual
                        recurrence water levels, metres relative to MSL,
                        ARI 1 to 10000 years (mean and upper 95% CI).
                        Conversion factor NAVD88 to MSL: -0.084 m, i.e. a
                        level X m above MSL is X - 0.084 m above NAVD88
                        (NAVD88 sits above the 1983-2001 MSL in this region;
                        Yorktown USCG station 8637689: MSL = NAVD88 - 0.29 ft).
    '2017 NOAA SLR'   : NOAA 2017 scenarios at Sewells Point, ft and m NAVD88.
- NSI_EleCert_Gloucester_attributes.csv
    Attribute table of Molly's shapefile NSI_EleCert_Gloucester.shp: the NSI
    points for Gloucester County (18,151) with the Hampton Roads elevation
    certificate fields appended for 637 of them (all elevations NAVD88 ft).

Outputs (data/Gloucester/)
- DDD___Gloucester___NSI.xlsx                       building inventory
- DDD___Gloucester_MC_annual_max_waterlevels_P50.csv  Int-High scenario
- DDD___Gloucester_MC_annual_max_waterlevels_P90.csv  High scenario

Method for the water levels (the same stochastic approach as the other
sites): for every year 2025 to 2100 and each of 1,000 simulations, draw an
annual exceedance probability and read the annual maximum off the NACCS
recurrence curve (linear in log10 of the return period, extrapolated beyond
10,000 years), then add the sea-level rise of the year relative to 2020.
Following Molly's spreadsheet, the NACCS curve is taken as the 2020
condition. The same random draws are used for both scenarios, so the
difference between the P50 and P90 files is sea-level rise only.

Scenario mapping (the app's names): P50 = NOAA 2017 Intermediate-High,
P90 = NOAA 2017 High, as for Pamunkey. Note that the two 'Recurrance with
SLR' sheets in the workbook are labelled Int-High and High but hold the
Intermediate and Int-High increments; they are not used here.

Run:  python tools/make_gloucester_inputs.py
"""
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "data", "Gloucester", "source")
OUT = os.path.join(ROOT, "data", "Gloucester")
SLUG = "Gloucester"

M_TO_FT = 1.0 / 0.3048
MSL_TO_NAVD88_M = -0.084          # NACCS conversion factor (see docstring)
BASE_YEAR = 2020                  # the NACCS curve is treated as this year's condition
YEARS = list(range(2025, 2101))
N_MC = 1000
SEED = 20261101
SCENARIOS = {"P50": "Int-High", "P90": "High"}
MAX_GROUND_FT = 20.0              # inventory scope: buildings with ground elevation at or below this
NACCS_STAT = "mean"               # 'mean' or 'upper95'


def read_naccs(xlsx):
    raw = pd.read_excel(xlsx, sheet_name="NACCS data", header=None)
    hdr = raw.index[raw.iloc[:, 0].astype(str).str.contains("ARI", na=False)][0]
    tbl = raw.iloc[hdr + 1:, :3].dropna(subset=[0]).copy()
    tbl.columns = ["ari", "mean", "upper95"]
    tbl = tbl[pd.to_numeric(tbl["ari"], errors="coerce").notna()].astype(float)
    return tbl.sort_values("ari").reset_index(drop=True)


def read_noaa_slr_ft(xlsx):
    raw = pd.read_excel(xlsx, sheet_name="2017 NOAA SLR", header=None)
    hdr = raw.index[raw.iloc[:, 0].astype(str).str.strip().eq("Year")][0]
    tbl = raw.iloc[hdr + 1:, :7].copy()
    tbl.columns = ["Year", "Low", "Int-Low", "Intermediate", "Int-High", "High", "Extreme"]
    tbl = tbl.dropna(subset=["Year"]).astype(float)
    tbl["Year"] = tbl["Year"].astype(int)
    return tbl.set_index("Year")


def curve_ft_navd88(naccs, stat=NACCS_STAT):
    ari = naccs["ari"].values
    wl_m = naccs[stat].values + MSL_TO_NAVD88_M
    return np.log10(ari), wl_m * M_TO_FT


def sample_annual_max(log_ari, wl_ft, u):
    """Annual maximum for uniform draws u: P(X <= x(T)) = 1 - 1/T, so
    T = 1 / (1 - u). Linear in log10(T); extrapolated past the last point."""
    log_t = -np.log10(1.0 - u)
    out = np.interp(log_t, log_ari, wl_ft)
    hi = log_t > log_ari[-1]
    if hi.any():
        slope = (wl_ft[-1] - wl_ft[-2]) / (log_ari[-1] - log_ari[-2])
        out[hi] = wl_ft[-1] + slope * (log_t[hi] - log_ari[-1])
    return out


def build_water_levels(xlsx):
    naccs = read_naccs(xlsx)
    slr = read_noaa_slr_ft(xlsx)
    log_ari, wl_ft = curve_ft_navd88(naccs)
    rng = np.random.default_rng(SEED)
    u = rng.random((len(YEARS), N_MC))
    base = sample_annual_max(log_ari, wl_ft, u)           # years x sims, 2020 condition
    files = {}
    for key, scn in SCENARIOS.items():
        rise = np.array([slr.loc[y, scn] - slr.loc[BASE_YEAR, scn] for y in YEARS])
        wl = base + rise[:, None]
        df = pd.DataFrame(np.round(wl, 4), columns=[f"MC_{i:04d}" for i in range(1, N_MC + 1)])
        df.insert(0, "Year", YEARS)
        path = os.path.join(OUT, f"DDD___{SLUG}_MC_annual_max_waterlevels_{key}.csv")
        df.to_csv(path, index=False)
        files[key] = (path, df)
    present = {k: float(np.interp(np.log10(t), log_ari, wl_ft) + (slr.loc[2025, "Int-High"] - slr.loc[BASE_YEAR, "Int-High"]))
               for k, t in (("annual_ft", 1), ("pct10_ft", 10), ("pct1_ft", 100))}
    return files, present


def build_inventory(csv):
    a = pd.read_csv(csv, low_memory=False)
    a = a[a["ground_elv"] <= MAX_GROUND_FT].copy()
    a = a[a["occtype"].astype(str).str.strip().ne("") & (a["num_story"] > 0)].copy()
    ec = a["FFE"] > 0                                     # elevation certificate present
    # Elevation certificate: first floor from the certificate. Ground = lowest
    # adjacent grade, foundation height = certificate FFE - LAG, so FFE in the
    # model equals the surveyed finished first floor elevation (NAVD88).
    ground = a["ground_elv"].where(~ec, a["LAG"])
    found_ht = a["found_ht"].where(~ec, (a["FFE"] - a["LAG"]).clip(lower=0.0))
    year_built = a["med_yr_blt"].astype(float)
    ec_year = pd.to_numeric(a["RESYRBLT"], errors="coerce")
    year_built = year_built.where(~(ec & ec_year.notna()), ec_year)
    inv = pd.DataFrame({
        "ID": a["fd_id"].astype(int),
        "occupancy_type": a["occtype"].astype(str).str.strip(),
        "building_type": a["bldgtype"].astype(str).str.strip(),
        "number_of_stories": a["num_story"].astype(int),
        "area": a["sqft"].astype(float),
        "foundation_type": a["found_type"].astype(str).str.strip(),
        "foundation_height": found_ht.astype(float).round(3),
        "year_built": year_built,
        "ground_elevation": ground.astype(float).round(4),
        "address": a["ADDRESS"].where(ec & a["ADDRESS"].astype(str).str.strip().ne(""), np.nan),
        "longitude": a["x"].astype(float),
        "latitude": a["y"].astype(float),
        "structure_value": a["val_struct"].astype(float),
        "content_value": a["val_cont"].astype(float),
    })
    inv = inv.sort_values("ID").reset_index(drop=True)
    path = os.path.join(OUT, f"DDD___{SLUG}___NSI.xlsx")
    inv.to_excel(path, index=False)
    return path, inv, int(ec.sum())


def main():
    os.makedirs(OUT, exist_ok=True)
    xlsx = os.path.join(SRC, "Gloucester_Recurrance_1.xlsx")
    csv = os.path.join(SRC, "NSI_EleCert_Gloucester_attributes.csv")
    files, present = build_water_levels(xlsx)
    for key, (path, df) in files.items():
        v = df.iloc[:, 1:].values
        print(f"{os.path.basename(path)}: {df.shape[0]} years x {df.shape[1] - 1} sims; "
              f"2025 median {np.median(v[0]):.2f} ft, P90 {np.quantile(v[0], 0.9):.2f}, max {v[0].max():.2f}; "
              f"2100 median {np.median(v[-1]):.2f} ft")
    print("present-day (2025) levels, ft NAVD88:", {k: round(v, 2) for k, v in present.items()})
    path, inv, n_ec = build_inventory(csv)
    print(f"{os.path.basename(path)}: {len(inv)} buildings (ground <= {MAX_GROUND_FT:g} ft), "
          f"{n_ec} with elevation certificates; occupancy: "
          f"{inv['occupancy_type'].str[:4].value_counts().head(6).to_dict()}")


if __name__ == "__main__":
    sys.exit(main())
