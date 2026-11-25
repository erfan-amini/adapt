"""Build the ADAPT model inputs for Hamilton Beach and Howard Beach (Queens, NYC).

Sources
- data/HamiltonHowardBeach/source/HHB_All_Buildings_2026-11-05.xlsx
    Kytt MacManus's provisional building sample (CIRF, 5 Nov 2026): 5,577 NYC
    building footprints with PLUTO attributes first and NSI 2026 values filling
    gaps, in the 14-field ADAPT inventory layout. 1,109 buildings have blank
    fields (974 garages and 135 others); the engine lists them as skipped.
    The address column is dropped here.
- data/HamiltonHowardBeach/source/HHB_ground_3dep_2026-11-05.csv
    USGS 3DEP 1 m bare-earth ground (NY CMPG 2013 tile, ft NAVD88) at each
    building point (tools/make_ground_3dep.py). It replaces the NSI ground
    elevation, which sits a median 4.5 ft below the lidar ground in this area.

Water levels (DDD___HamiltonHowardBeach_MC_annual_max_waterlevels_P50/P90.csv)
- Recurrence curve: USACE NACCS save point 12011 (40.6471 N, 73.8352 W), CC
  Base, Post96RT, expected value (mean), return periods 1 to 10,000 years. The
  values are in ft NAVD88 (NAVD88 = MSL(1983-2001) - 0.119 m, NOAA VDatum) and
  refer to the 1983-2001 mean sea level (mid-point about 1992). The 1-, 10-,
  100-, 1,000- and 10,000-year values are the printed ones; the other eight
  were read off the marker centres of the same plot (0.03 ft per pixel; the
  printed points read back within 0.02 ft).
- Shift to 2025: +0.15 m (0.49 ft), the observed rise of mean sea level from
  the 1983-2001 epoch to 2025 at the nearest NOAA gauges (Philip Orton).
- Sea-level rise after 2025: NYS Part 490 (2024), New York City region,
  inches relative to 1995-2014 (centre 2004.5). A decade value is placed at
  the middle of the decade (2030s -> 2034.5, 2050s -> 2054.5, 2080s ->
  2084.5) and 2100 at 2100, with linear interpolation between them. The rise
  added in year Y is S(Y) - S(2025). P50 file = Medium (50th percentile),
  P90 file = High (90th percentile).
- Sampling: 1,000 simulations for every year 2025 to 2100; each annual maximum
  is drawn from the curve (linear in log10 of the return period, extrapolated
  beyond 10,000 years) and shifted by that year's rise. The same random draws
  serve both scenarios, so the two files differ by sea-level rise only.

Present-day (2025) levels for the home report, ft NAVD88
- Daily high tide (MHHW): NOAA 8517201 North Channel Bridge, MHHW - MSL =
  0.949 m, on the 2025 mean sea level (+0.10 ft NAVD88).
- Monthly high tide (MMHW): 1.19 m above mean sea level (Orton et al. 2019,
  Inwood), on the 2025 mean sea level.
- 10% and 1% annual chance: the NACCS curve shifted to 2025.

Run:  python tools/make_hhb_inputs.py
"""
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SLUG = "HamiltonHowardBeach"
OUT = os.path.join(ROOT, "data", SLUG)
SRC = os.path.join(OUT, "source")
INV_SRC = os.path.join(SRC, "HHB_All_Buildings_2026-11-05.xlsx")
GROUND_SRC = os.path.join(SRC, "HHB_ground_3dep_2026-11-05.csv")

M_TO_FT = 1.0 / 0.3048
YEARS = list(range(2025, 2101))
N_MC = 1000
SEED = 20261125

# NACCS save point 12011, mean, ft NAVD88, 1983-2001 MSL epoch
NACCS_SP12011 = {1: 4.82, 2: 5.56, 5: 6.46, 10: 7.20, 20: 7.95, 50: 9.08, 100: 10.31,
                 200: 11.82, 500: 13.77, 1000: 15.24, 2000: 16.79, 5000: 18.95, 10000: 20.38}
RISE_EPOCH_TO_2025_FT = 0.15 * M_TO_FT

# NYS Part 490 (2024), NYC, inches above 1995-2014: the table in the location's
# settings (adapt_core/config.py), so the model inputs and the app use one source.
sys.path.insert(0, ROOT)
from adapt_core import config as C                     # noqa: E402
SLR_TABLE = C.LOCATION_DEFAULTS[SLUG]["slr"]
NYS_SLR_IN = {k: SLR_TABLE[k] for k in ("P50", "P90")}  # Medium, High

# Tides at NOAA 8517201 and Inwood (m above mean sea level), 2025 mean sea level
MSL_EPOCH_NAVD88_M = 0.925 - 1.044            # NOAA 8517201: MSL - NAVD88 = -0.119 m
MHHW_ABOVE_MSL_M = 1.874 - 0.925              # 0.949 m
MMHW_ABOVE_MSL_M = 1.19                       # Orton et al. 2019, Inwood


def nys_rise_since_2025_ft(year, key):
    return C.slr_table_ft({"slr": SLR_TABLE}, key, year)


def curve():
    t = np.array(sorted(NACCS_SP12011), dtype=float)
    z = np.array([NACCS_SP12011[k] for k in sorted(NACCS_SP12011)]) + RISE_EPOCH_TO_2025_FT
    return np.log10(t), z                         # 2025 condition, ft NAVD88


def sample_annual_max(log_t_curve, wl_ft, u):
    """Annual maximum for uniform draws u: P(X <= x(T)) = 1 - 1/T, so
    T = 1 / (1 - u). Linear in log10(T); extrapolated past the last point."""
    log_t = -np.log10(1.0 - u)
    out = np.interp(log_t, log_t_curve, wl_ft)
    hi = log_t > log_t_curve[-1]
    if hi.any():
        slope = (wl_ft[-1] - wl_ft[-2]) / (log_t_curve[-1] - log_t_curve[-2])
        out[hi] = wl_ft[-1] + slope * (log_t[hi] - log_t_curve[-1])
    return out


def build_water_levels():
    log_t, wl = curve()
    rng = np.random.default_rng(SEED)
    base = sample_annual_max(log_t, wl, rng.random((len(YEARS), N_MC)))
    files = {}
    for key in NYS_SLR_IN:
        rise = np.array([nys_rise_since_2025_ft(y, key) for y in YEARS])
        df = pd.DataFrame(np.round(base + rise[:, None], 4), columns=[f"MC_{i:04d}" for i in range(1, N_MC + 1)])
        df.insert(0, "Year", YEARS)
        path = os.path.join(OUT, f"DDD___{SLUG}_MC_annual_max_waterlevels_{key}.csv")
        df.to_csv(path, index=False)
        files[key] = (path, df)
    msl2025_ft = (MSL_EPOCH_NAVD88_M * M_TO_FT) + RISE_EPOCH_TO_2025_FT
    present = {"mhhw_ft": msl2025_ft + MHHW_ABOVE_MSL_M * M_TO_FT,
               "monthly_ft": msl2025_ft + MMHW_ABOVE_MSL_M * M_TO_FT,
               "pct10_ft": float(np.interp(1.0, log_t, wl)),
               "pct1_ft": float(np.interp(2.0, log_t, wl))}
    return files, present


def build_inventory():
    a = pd.read_excel(INV_SRC)
    g = pd.read_csv(GROUND_SRC, usecols=["ID", "ground_3dep_ft"])
    a = a.merge(g, on="ID", how="left", validate="one_to_one")
    if a["ground_3dep_ft"].isna().any():
        raise ValueError(f"{int(a['ground_3dep_ft'].isna().sum())} buildings have no 3DEP ground value.")
    inv = pd.DataFrame({
        "ID": a["ID"].astype("int64"),
        "occupancy_type": a["occupancy_type"],
        "building_type": a["building_type"],
        "number_of_stories": a["number_of_stories"],
        "area": a["area"].round(1),
        "foundation_type": a["foundation_type"],
        "foundation_height": a["foundation_height"],
        "year_built": a["year_built"],
        "ground_elevation": a["ground_3dep_ft"].round(2),
        "ground_elevation_nsi": a["ground_elevation"].round(2),
        "address": np.nan,
        "longitude": a["longitude"],
        "latitude": a["latitude"],
        "structure_value": a["structure_value"].round(2),
        "content_value": a["content_value"].round(2),
    })
    path = os.path.join(OUT, f"DDD___{SLUG}___NSI.xlsx")
    inv.to_excel(path, index=False)
    return path, inv


def main():
    os.makedirs(OUT, exist_ok=True)
    files, present = build_water_levels()
    for key, (path, df) in files.items():
        v = df.iloc[:, 1:].values
        print(f"{os.path.basename(path)}: {df.shape[0]} years x {df.shape[1] - 1} sims; "
              f"2025 median {np.median(v[0]):.2f} ft, P90 {np.quantile(v[0], 0.9):.2f}; "
              f"2055 median {np.median(v[30]):.2f}; 2100 median {np.median(v[-1]):.2f} ft")
    print("present-day (2025) levels, ft NAVD88:", {k: round(v, 1) for k, v in present.items()})
    path, inv = build_inventory()
    complete = inv[["foundation_height", "structure_value", "content_value"]].notna().all(axis=1)
    print(f"{os.path.basename(path)}: {len(inv)} buildings, {int(complete.sum())} with complete attributes; "
          f"3DEP ground median {inv['ground_elevation'].median():.2f} ft "
          f"(NSI {inv['ground_elevation_nsi'].median():.2f} ft)")


if __name__ == "__main__":
    sys.exit(main())
