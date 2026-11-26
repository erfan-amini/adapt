"""Sample USGS 3DEP 1 m bare-earth ground elevation at each building point.

The survey tool (Developer > Building attribute data) uses the output to flag
ground elevations that look wrong. Run it offline; it is not part of the app.

Usage:
    python tools/make_ground_3dep.py points.csv data/_survey/<slug>_ground_3dep.csv

points.csv needs the columns uid, longitude and latitude (WGS84), for example an
export of the location's Surveys_<slug> tab. ID is copied through when present.

How it works: the TNM Access API lists the 3DEP 1 m DEM tiles that cover the
points; each tile is a tiled GeoTIFF on S3, so only the window around the
points is read. Elevations are bilinear at the point, converted from metres to
feet, NAVD88 (the 3DEP vertical datum). When tiles from several lidar projects
overlap, the newest publication fills first.

Needs rasterio, pyproj and pandas (pip install rasterio pyproj pandas).
"""
import json
import sys
import urllib.parse
import urllib.request

import numpy as np
import pandas as pd
import rasterio
from pyproj import Transformer
from rasterio.windows import from_bounds

TNM = "https://tnmaccess.nationalmap.gov/api/v1/products"
DATASET = "Digital Elevation Model (DEM) 1 meter"
FT_PER_M = 1 / 0.3048


def list_tiles(lon, lat):
    bbox = f"{lon.min():.5f},{lat.min():.5f},{lon.max():.5f},{lat.max():.5f}"
    q = urllib.parse.urlencode({"datasets": DATASET, "bbox": bbox, "outputFormat": "JSON", "max": 100})
    with urllib.request.urlopen(f"{TNM}?{q}", timeout=120) as r:
        items = json.load(r).get("items", [])
    items = [i for i in items if str(i.get("downloadURL", "")).lower().endswith(".tif")]
    items.sort(key=lambda i: i.get("publicationDate", ""), reverse=True)
    return items


def sample(points):
    lon = points["longitude"].astype(float).to_numpy()
    lat = points["latitude"].astype(float).to_numpy()
    out = np.full(len(points), np.nan)
    src = np.array([""] * len(points), dtype=object)
    for item in list_tiles(lon, lat):
        with rasterio.open("/vsicurl/" + item["downloadURL"]) as r:
            x, y = Transformer.from_crs(4326, r.crs, always_xy=True).transform(lon, lat)
            b = r.bounds
            m = (x > b.left + 2) & (x < b.right - 2) & (y > b.bottom + 2) & (y < b.top - 2) & np.isnan(out)
            if not m.any():
                continue
            win = from_bounds(x[m].min() - 5, y[m].min() - 5, x[m].max() + 5, y[m].max() + 5,
                              r.transform).round_offsets().round_lengths()
            a = r.read(1, window=win).astype(float)
            a[a < -1000] = np.nan
            t = r.window_transform(win)
            c = (x[m] - t.c) / t.a - 0.5
            rr = (y[m] - t.f) / t.e - 0.5
            c0, r0 = np.floor(c).astype(int), np.floor(rr).astype(int)
            dc, dr = c - c0, rr - r0
            v = (a[r0, c0] * (1 - dc) * (1 - dr) + a[r0, c0 + 1] * dc * (1 - dr)
                 + a[r0 + 1, c0] * (1 - dc) * dr + a[r0 + 1, c0 + 1] * dc * dr)
            idx = np.where(m)[0]
            out[idx] = v * FT_PER_M
            src[idx] = item.get("title", "")
            print(f"{item.get('title')}: {int(np.isfinite(v).sum())} points")
    return out, src


def main(inp, outp):
    pts = pd.read_csv(inp, dtype=str)
    ft, src = sample(pts)
    res = pd.DataFrame({"uid": pts["uid"]})
    if "ID" in pts:
        res["ID"] = pts["ID"]
    res["longitude"] = pts["longitude"]
    res["latitude"] = pts["latitude"]
    res["ground_3dep_ft"] = np.round(ft, 2)
    res["dem_tile"] = src
    res.to_csv(outp, index=False)
    print(f"wrote {outp}: {np.isfinite(ft).sum()} of {len(ft)} points have a 3DEP value")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
