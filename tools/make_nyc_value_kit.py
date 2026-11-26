"""Build the neighborhood inputs for the NSI survey tool's NYC home-value button.

For New York City locations, the survey tool (Developer > NSI survey tool) can
estimate a home's structure and content value from public city data when the
inventory has none. It follows the MOCEJ Resilient Acquisitions "method 2"
(price of a similar home nearby = neighborhood price per sq ft x floor area),
then takes off the land and NSI's wear so the number sits on the same basis as
NSI (depreciated replacement value of the structure only):

    structure = price per sq ft x lot floor area x building's share of the lot's
                footprint x building share of the lot's market value x (1 - wear)
    content   = structure / 2   (the NSI rule for homes)

The button looks the building's tax lot up live on NYC Open Data. This script
prepares the two neighborhood inputs it cannot get live, and writes them to
data/_survey/<slug>_value_kit.json:

- Price per sq ft: median sale price / PLUTO building area of 1-3 family home
  sales in the neighborhood (NTA) over the last two calendar years
  (NYC Citywide Annualized Calendar Sales, w2pb-icbu; PLUTO, 64uk-42ks).
  Sales under $100,000 and lots with more than two buildings are dropped,
  and the top and bottom 1% of price per sq ft are trimmed.
- Wear: NSI's own depreciation for homes near each point, as
  1 - val_struct / fullrep, with each NSI home's year built from PLUTO. The
  tool takes the wear of the nearest NSI homes of similar age.
- Land share fallback: the median land share of tax class 1 lots in the area
  (NYC Finance Property Valuation and Assessment Data, 8y4t-faws), used when a
  lot has no market value on file.

Everything comes from public data; nothing from the MOCEJ workbook.
Rerun once a year, after the city publishes the new sales and values.

Usage:
    python tools/make_nyc_value_kit.py <slug> <NTA code> [points.csv]

points.csv (default data/_survey/<slug>_ground_3dep.csv) needs longitude and
latitude columns; their extent, padded by about 300 m, sets the area.
Example:
    python tools/make_nyc_value_kit.py hamiltonhowardbeach QN1003

Needs requests, pandas, numpy and geopandas.
"""
import datetime as dt
import io
import json
import os
import sys
import time

import geopandas as gpd
import numpy as np
import pandas as pd
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SODA = "https://data.cityofnewyork.us/resource/"
NSI = "https://nsi.sec.usace.army.mil/nsiapi/structures"
PAD_DEG = 0.003
HOME_CLASS = r"^(A|B|C0)"        # 1-3 family homes, as in MOCEJ method 2


def get(url, params, timeout=180):
    for i in range(4):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            return r
        except (requests.exceptions.ChunkedEncodingError, requests.exceptions.ConnectionError,
                requests.exceptions.ReadTimeout, requests.exceptions.HTTPError) as e:
            if i == 3:
                raise
            print(f"  retry {i + 1}: {type(e).__name__}", flush=True)
            time.sleep(3 * (i + 1))


def soda(ds, where, select, limit=50000, order=None):
    p = {"$where": where, "$select": select, "$limit": limit}
    if order:
        p["$order"] = order
    return pd.DataFrame(get(SODA + ds + ".json", p).json())


def pluto_by_bbl(bbls):
    out = []
    bbls = sorted(set(bbls))
    for i in range(0, len(bbls), 150):
        chunk = ",".join(bbls[i:i + 150])
        out.append(soda("64uk-42ks", f"bbl in({chunk})", "bbl,bldgarea,numbldgs"))
    return pd.concat(out, ignore_index=True)


def norm_bbl(s):
    return pd.to_numeric(s, errors="coerce").astype("Int64").astype(str)


def main(slug, nta, points_csv=None):
    points_csv = points_csv or os.path.join(ROOT, "data", "_survey", f"{slug}_ground_3dep.csv")
    pts = pd.read_csv(points_csv, usecols=["longitude", "latitude"]).dropna()
    x0, y0 = pts.longitude.min() - PAD_DEG, pts.latitude.min() - PAD_DEG
    x1, y1 = pts.longitude.max() + PAD_DEG, pts.latitude.max() + PAD_DEG
    today = dt.date.today()
    since = f"{today.year - 2}-01-01"

    # 1. Price per sq ft from recent 1-3 family sales in the neighborhood
    s = soda("w2pb-icbu",
             f"nta='{nta}' AND sale_date>='{since}' AND (starts_with(building_class_category,'01') OR "
             "starts_with(building_class_category,'02') OR starts_with(building_class_category,'03'))",
             "bbl,sale_price,sale_date")
    s["bbl"] = s.bbl.astype(str).str[:10]
    s["sale_price"] = pd.to_numeric(s.sale_price, errors="coerce")
    p = pluto_by_bbl(s.bbl.dropna().tolist())
    p["bbl"] = norm_bbl(p.bbl)
    for c in ["bldgarea", "numbldgs"]:
        p[c] = pd.to_numeric(p[c], errors="coerce")
    s = s.merge(p, on="bbl")
    s = s[(s.sale_price >= 100000) & (s.bldgarea >= 400) & (s.numbldgs <= 2)].copy()
    s["ppsf"] = s.sale_price / s.bldgarea
    lo, hi = s.ppsf.quantile([0.01, 0.99])
    s = s[s.ppsf.between(lo, hi)]
    ppsf = float(s.ppsf.median())
    print(f"price per sq ft: ${ppsf:.0f} from {len(s)} sales in {nta} since {since} "
          f"(last sale {s.sale_date.max()[:10]})")

    # 2. Lots in the area: zip codes, year built
    lots = soda("64uk-42ks",
                f"latitude between {y0} and {y1} AND longitude between {x0} and {x1}",
                "bbl,zipcode,bldgclass,yearbuilt")
    lots["bbl"] = norm_bbl(lots.bbl)
    lots["yearbuilt"] = pd.to_numeric(lots.yearbuilt, errors="coerce")
    zips = sorted(z for z in lots.zipcode.dropna().unique() if z)

    # 3. Land share fallback: tax class 1 lots, latest year on file
    d = []
    for z in zips:
        q = soda("8y4t-faws", f"zip_code='{z}' AND curtaxclass like '1%'", "year", limit=1, order="year DESC")
        if q.empty:
            continue
        yr = q.year.iloc[0]
        d.append(soda("8y4t-faws", f"zip_code='{z}' AND year='{yr}' AND period='1' AND curtaxclass like '1%'",
                      "parid,year,curmktland,curmkttot"))
    d = pd.concat(d, ignore_index=True)
    for c in ["curmktland", "curmkttot"]:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d[(d.curmkttot > 0) & d.parid.isin(set(lots.bbl))]
    land_median = float((d.curmktland / d.curmkttot).median())
    print(f"land share median: {land_median:.3f} over {len(d)} tax class 1 lots in the area, year {d.year.max()}")

    # 4. NSI wear for homes, with PLUTO year built through the NYC building footprints
    r = get(NSI, {"bbox": f"{x0},{y0},{x1},{y0},{x1},{y1},{x0},{y1},{x0},{y0}"})
    n = gpd.read_file(io.StringIO(r.text))
    n = n[n.occtype.str.startswith(("RES1", "RES3")) & (n.fullrep > 0) & (n.val_struct > 0)].copy()
    n["keep"] = n.val_struct / n.fullrep
    fp = soda("5zhs-2jue", f"within_box(the_geom,{y1},{x0},{y0},{x1}) AND feature_code='2100'",
              "the_geom,mappluto_bbl,base_bbl")
    from shapely.geometry import shape
    fp = gpd.GeoDataFrame(fp, geometry=[shape(g) for g in fp.the_geom], crs=4326)
    fp["bbl"] = fp.mappluto_bbl.fillna(fp.base_bbl).astype(str).str[:10]
    j = gpd.sjoin(n[["keep", "geometry"]].to_crs(2263), fp[["bbl", "geometry"]].to_crs(2263),
                  how="left", predicate="within")
    j = j[~j.index.duplicated()].merge(lots[["bbl", "yearbuilt", "bldgclass"]], on="bbl", how="left")
    j = j[j.bldgclass.astype(str).str.match(HOME_CLASS) & (j.yearbuilt > 1800)].copy()
    ll = gpd.GeoDataFrame(j, geometry="geometry", crs=2263).to_crs(4326)
    wear = [[round(g.y, 5), round(g.x, 5), int(yb), round(float(k), 4)]
            for g, yb, k in zip(ll.geometry, ll.yearbuilt, ll["keep"])]
    print(f"wear points: {len(wear)} NSI homes; NSI keeps a median {np.median([w[3] for w in wear]):.3f} of new cost")

    kit = {
        "loc": slug,
        "built": today.isoformat(),
        "method": "MOCEJ method 2 (neighborhood price per sq ft x floor area), less land and NSI wear",
        "ppsf": round(ppsf, 2),
        "sales": {"nta": nta, "from": since, "to": s.sale_date.max()[:10], "n": int(len(s)),
                  "source": "NYC Citywide Annualized Calendar Sales (w2pb-icbu), PLUTO (64uk-42ks)"},
        "land_median": round(land_median, 4),
        "land_source": f"NYC Finance property values (8y4t-faws), {d.year.max()}",
        "wear_source": "NSI 2026, 1 - val_struct / fullrep; year built from PLUTO",
        "wear": wear,
    }
    out = os.path.join(ROOT, "data", "_survey", f"{slug}_value_kit.json")
    with open(out, "w") as fh:
        json.dump(kit, fh, separators=(",", ":"))
    print(f"wrote {out} ({os.path.getsize(out) / 1024:.0f} KB)")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    main(*sys.argv[1:4])
