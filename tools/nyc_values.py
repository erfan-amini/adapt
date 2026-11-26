"""NYC tax-lot attributes for building points, from public NYC Open Data.

For each building point this finds its tax lot (through the NYC building
footprints) and returns what the city records say about the lot:

    bbl          tax lot number (borough, block, lot)
    fp_share     this building's share of the principal-building footprint area on the lot
    bldgclass    NYC building class (A, B, C0 = 1-3 family homes)
    bldgarea     floor area of all buildings on the lot, sq ft (PLUTO)
    yearbuilt    year built (PLUTO)
    bsmtcode     basement code (PLUTO): 0 none; 1 full above grade; 2 full below
                 grade; 3 partial above grade; 4 partial below grade; 5 unknown
    bldg_share   building share of the lot's market value, 1 - land / total
                 (NYC Finance, latest year on file)
    value_year   year of those market values

It also computes the home value the survey tool's NYC button gives (MOCEJ
method 2, less land and NSI wear), with the neighborhood inputs from
data/_survey/<slug>_value_kit.json (tools/make_nyc_value_kit.py):

    structure = price per sq ft x bldgarea x fp_share x bldg_share x NSI kept share
    content   = structure / 2

Sources: NYC building footprints (5zhs-2jue), PLUTO (64uk-42ks), NYC Finance
property valuation (8y4t-faws). Public data only.

Usage (saves a lot table for a set of points):
    python tools/nyc_values.py <points.csv> <out.csv>
points.csv needs ID, longitude and latitude columns.

Needs requests, pandas, numpy, geopandas and shapely.
"""
import sys
import time

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
from shapely.geometry import shape

SODA = "https://data.cityofnewyork.us/resource/"
HOME_CLASS = r"^(A|B|C0)"
PAD_DEG = 0.002


def _get(url, params, timeout=180):
    for i in range(4):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            return r
        except (requests.exceptions.ChunkedEncodingError, requests.exceptions.ConnectionError,
                requests.exceptions.ReadTimeout, requests.exceptions.HTTPError):
            if i == 3:
                raise
            time.sleep(3 * (i + 1))


def _soda(ds, where, select, limit=50000, order=None):
    p = {"$where": where, "$select": select, "$limit": limit}
    if order:
        p["$order"] = order
    return pd.DataFrame(_get(SODA + ds + ".json", p).json())


def _chunks(seq, n=150):
    seq = sorted(set(seq))
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def lot_table(points):
    """points: DataFrame with ID, longitude, latitude. Returns one row per ID."""
    x0, y0 = points.longitude.min() - PAD_DEG, points.latitude.min() - PAD_DEG
    x1, y1 = points.longitude.max() + PAD_DEG, points.latitude.max() + PAD_DEG

    fp = _soda("5zhs-2jue", f"within_box(the_geom,{y1},{x0},{y0},{x1}) AND feature_code='2100'",
               "the_geom,mappluto_bbl,base_bbl,shape_area")
    fp = gpd.GeoDataFrame(fp, geometry=[shape(g) for g in fp.the_geom], crs=4326).to_crs(2263)
    fp["bbl"] = fp.mappluto_bbl.fillna(fp.base_bbl).astype(str).str[:10]
    fp["shape_area"] = pd.to_numeric(fp.shape_area, errors="coerce")
    fp["fp_share"] = fp.shape_area / fp.groupby("bbl").shape_area.transform("sum")

    g = gpd.GeoDataFrame(points[["ID"]].copy(), crs=4326,
                         geometry=gpd.points_from_xy(points.longitude, points.latitude)).to_crs(2263)
    j = gpd.sjoin_nearest(g, fp[["bbl", "fp_share", "geometry"]], how="left", max_distance=15)
    j = pd.DataFrame(j[~j.index.duplicated()].drop(columns=["geometry", "index_right"]))
    bbls = [b for b in j.bbl.dropna().unique() if b.isdigit() and len(b) == 10]

    pl = pd.concat([_soda("64uk-42ks", f"bbl in({','.join(c)})", "bbl,bldgclass,bldgarea,yearbuilt,bsmtcode")
                    for c in _chunks(bbls)], ignore_index=True)
    pl["bbl"] = pd.to_numeric(pl.bbl, errors="coerce").astype("Int64").astype(str)
    for c in ["bldgarea", "yearbuilt"]:
        pl[c] = pd.to_numeric(pl[c], errors="coerce")
    pl["bsmtcode"] = pd.to_numeric(pl.bsmtcode, errors="coerce").astype("Int64")

    yr = _soda("8y4t-faws", f"parid in('{bbls[0]}')", "year", limit=1, order="year DESC").year.iloc[0]
    dv = pd.concat([_soda("8y4t-faws", f"year='{yr}' AND parid in({','.join(repr(b) for b in c)})",
                          "parid,period,curmktland,curmkttot")
                    for c in _chunks(bbls)], ignore_index=True)
    for c in ["curmktland", "curmkttot", "period"]:
        dv[c] = pd.to_numeric(dv[c], errors="coerce")
    dv = dv[dv.curmkttot > 0].sort_values("period").drop_duplicates("parid", keep="last")
    dv["bldg_share"] = 1 - dv.curmktland / dv.curmkttot
    dv["value_year"] = yr

    out = (j.merge(pl, on="bbl", how="left")
            .merge(dv[["parid", "bldg_share", "value_year"]], left_on="bbl", right_on="parid", how="left")
            .drop(columns="parid"))
    return out


def nsi_keep(kit, lat, lng, year):
    """NSI kept share after wear: median of the 9 nearby NSI homes closest in age
    (homes built before 1975 count as 1975). Same rule as the survey tool."""
    w = np.asarray(kit["wear"], dtype=float)
    kx, ky = 111320 * np.cos(np.radians(lat)), 110540
    d = np.hypot((w[:, 1] - lng) * kx, (w[:, 0] - lat) * ky)
    ok = d <= 800
    if ok.sum() < 3:
        return np.nan
    dy = np.abs(np.maximum(w[ok, 2], 1975) - max(year, 1975)) if year and year > 1800 else 0
    score = dy + d[ok] / 150
    top = np.sort(w[ok, 3][np.argsort(score, kind="stable")[:9]])
    return float(top[len(top) // 2])


def method2_values(lots, points, kit):
    """Structure and content value (MOCEJ method 2, less land and NSI wear) for
    1-3 family homes; NaN elsewhere. lots from lot_table, points with ID, longitude, latitude."""
    m = points[["ID", "longitude", "latitude"]].merge(lots, on="ID", how="left")
    home = m.bldgclass.astype(str).str.match(HOME_CLASS) & (m.bldgarea > 0)
    share = m.bldg_share.fillna(1 - kit["land_median"])
    keep = np.array([nsi_keep(kit, la, lo, yb) if h else np.nan
                     for la, lo, yb, h in zip(m.latitude, m.longitude, m.yearbuilt.fillna(0), home)])
    structure = np.where(home, kit["ppsf"] * m.bldgarea * m.fp_share.fillna(1) * share * keep, np.nan)
    return pd.DataFrame({"ID": m.ID, "structure_m2": np.round(structure, 2),
                         "content_m2": np.round(structure / 2, 2)})


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    pts = pd.read_csv(sys.argv[1], usecols=["ID", "longitude", "latitude"])
    t = lot_table(pts)
    t.to_csv(sys.argv[2], index=False)
    print(f"{len(t)} points, {t.bbl.notna().sum()} on a tax lot, {t.bsmtcode.notna().sum()} with a PLUTO basement code, "
          f"{t.bldg_share.notna().sum()} with city market values ({t.value_year.dropna().iloc[0] if t.value_year.notna().any() else '-'})")
