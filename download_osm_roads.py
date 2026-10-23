"""Save OpenStreetMap roads for each ADAPT location so the app's Road maps
page works without reaching the public Overpass servers.

Run it on a computer with internet, in the folder that holds the app data:

    python download_osm_roads.py                 # every *_bldg_lookup.csv here
    python download_osm_roads.py Shinnecock      # one location (file prefix)

For each location it writes <slug>_osm_roads.json. Put that file next to
app.py (same folder as the other <slug>_* files) and redeploy. The app reads
the file first and only queries Overpass if the file is missing or does not
cover the map area.

The saved area is the map area the app uses at its widest zoom setting
(Zoom slider at 0.5) plus a margin, so every zoom setting is covered.
Needs: numpy, pandas, requests.
"""
import glob
import json
import math
import os
import sys
from datetime import date

import numpy as np
import pandas as pd
import requests

OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
)
BUFFER_M = 350.0       # same buffer the app's Road maps page uses
WIDEST_ZOOM = 0.5      # smallest value of the app's "Zoom (tighten)" slider
MARGIN = 1.1           # extra 10% on top of the widest view


def roi_from_lonlat(lon, lat, buffer_m=600.0, min_span_km=1.5, max_span_km=25.0):
    """Same robust bbox as the app: median-centred, MAD outlier rejection."""
    lon = np.asarray(lon, dtype=float)
    lat = np.asarray(lat, dtype=float)
    m = np.isfinite(lon) & np.isfinite(lat)
    lon, lat = lon[m], lat[m]
    clon, clat = float(np.median(lon)), float(np.median(lat))
    m_per_lat = 111320.0
    m_per_lon = 111320.0 * max(0.1, math.cos(math.radians(clat)))
    dist = np.hypot((lon - clon) * m_per_lon, (lat - clat) * m_per_lat)
    med = float(np.median(dist))
    mad = float(np.median(np.abs(dist - med)))
    keep = dist <= max(med + 5.0 * 1.4826 * mad, 2000.0)
    if not keep.any():
        keep = np.ones_like(dist, dtype=bool)
    lon_k, lat_k = lon[keep], lat[keep]
    half_lo, half_hi = 500.0 * min_span_km, 500.0 * max_span_km
    hx = min(max((lon_k.max() - lon_k.min()) * 0.5 * m_per_lon + buffer_m, half_lo), half_hi)
    hy = min(max((lat_k.max() - lat_k.min()) * 0.5 * m_per_lat + buffer_m, half_lo), half_hi)
    return (clon - hx / m_per_lon, clat - hy / m_per_lat, clon + hx / m_per_lon, clat + hy / m_per_lat)


def fetch_overpass(bbox, timeout=90):
    lon_min, lat_min, lon_max, lat_max = bbox
    query = (f"[out:json][timeout:{timeout}];"
             f'way["highway"]({lat_min},{lon_min},{lat_max},{lon_max});'
             f"out body geom;")
    headers = {"User-Agent": "ADAPT-FloodTool/1.0 (Columbia CCSR research app)"}
    errors = []
    for url in OVERPASS_ENDPOINTS:
        host = url.split("/")[2]
        try:
            resp = requests.post(url, data={"data": query}, headers=headers, timeout=(15, timeout + 30))
            resp.raise_for_status()
            raw = resp.json()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{host}: {exc}")
            continue
        if not raw.get("elements") and raw.get("remark"):
            errors.append(f"{host}: {raw.get('remark')}")
            continue
        return raw, host
    raise RuntimeError("No Overpass server answered:\n  " + "\n  ".join(errors))


def main():
    only = set(sys.argv[1:])
    lookups = sorted(glob.glob("*_bldg_lookup.csv"))
    if not lookups:
        sys.exit("No *_bldg_lookup.csv in this folder. Run the script where the app data is.")
    for path in lookups:
        slug = os.path.basename(path)[:-len("_bldg_lookup.csv")]
        if only and slug not in only:
            continue
        df = pd.read_csv(path)
        lon_col = "Longitude" if "Longitude" in df.columns else "longitude"
        lat_col = "Latitude" if "Latitude" in df.columns else "latitude"
        b = roi_from_lonlat(df[lon_col], df[lat_col], buffer_m=BUFFER_M)
        cx, cy = 0.5 * (b[0] + b[2]), 0.5 * (b[1] + b[3])
        f = MARGIN / WIDEST_ZOOM
        hw, hh = 0.5 * (b[2] - b[0]) * f, 0.5 * (b[3] - b[1]) * f
        bbox = [round(cx - hw, 5), round(cy - hh, 5), round(cx + hw, 5), round(cy + hh, 5)]
        print(f"{slug}: fetching roads for {bbox} ...", flush=True)
        raw, host = fetch_overpass(bbox)
        n = sum(1 for e in raw.get("elements", []) if e.get("type") == "way")
        out = f"{slug}_osm_roads.json"
        with open(out, "w", encoding="utf-8") as fh:
            json.dump({"bbox": bbox, "created": date.today().isoformat(), "source": host,
                       "overpass": raw}, fh)
        print(f"  wrote {out}: {n} road ways (from {host})")


if __name__ == "__main__":
    main()
