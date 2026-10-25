"""Terrain, bathtub flooding, basemap tiles and OSM road classification
(ported from the legacy app.py; the `fdem` namespace is kept so view code
can keep calling fdem.<fn>)."""
import os
import re
import math
import glob
import numpy as np
import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from types import SimpleNamespace

import io
import json
import time

# ============================================================================
# FLOOD-MAP (BATHTUB) SUPPORT - inlined so the app is a single file.
# Terrain: USGS 3DEP 1/3 arc-second (~10 m), public-domain NAVD88 metres,
# Cloud-Optimized GeoTIFFs on AWS `prd-tnm`; only the ROI window is read via
# GDAL /vsicurl HTTP range requests. Bathtub model (no hydraulic connectivity).
# To use a self-hosted topobathy COG instead, set DEM_COG_OVERRIDE_URL.
# ============================================================================
DEM_COG_URL_TEMPLATE = (
    "https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/13/TIFF/"
    "current/{tile}/USGS_13_{tile}.tif"
)
DEM_COG_OVERRIDE_URL = None
FT_PER_M = 1.0 / 0.3048
WS_BINS_FT = [0, 1, 2, 3, 4, 5, 6]
WS_COLORS = [
    (74, 0, 130), (31, 143, 255), (0, 204, 204),
    (255, 235, 0), (255, 140, 0), (191, 13, 13),
]
# Blue-only ramp (light = shallow → dark = deep) for the road-flooding maps.
WS_COLORS_BLUE = [
    (198, 219, 239), (158, 202, 225), (107, 174, 214),
    (66, 146, 198), (33, 113, 181), (8, 69, 148),
]


def dem_tiles_for_bbox(bbox):
    """3DEP 1-degree tile name(s) covering a lon/lat bbox (lon negative).
    Tile `nA wB` covers latitude [A-1, A], longitude [-B, -(B-1)]."""
    lon_min, lat_min, lon_max, lat_max = bbox
    a_min = int(math.floor(lat_min)) + 1
    a_max = int(math.ceil(lat_max))
    b_min = int(math.floor(-lon_max)) + 1
    b_max = int(math.ceil(-lon_min))
    return [f"n{a:02d}w{b:03d}" for a in range(a_min, a_max + 1)
            for b in range(b_min, b_max + 1)]


def roi_from_lonlat(lon, lat, buffer_m=600.0, min_span_km=1.5, max_span_km=25.0):
    """Robust bbox: median-centred, MAD outlier rejection, clamped span."""
    lon = np.asarray(lon, dtype=float)
    lat = np.asarray(lat, dtype=float)
    m = np.isfinite(lon) & np.isfinite(lat)
    lon, lat = lon[m], lat[m]
    if lon.size == 0:
        raise ValueError("No finite coordinates to build a region of interest.")
    clon = float(np.median(lon))
    clat = float(np.median(lat))
    m_per_lat = 111320.0
    m_per_lon = 111320.0 * max(0.1, math.cos(math.radians(clat)))
    dx = (lon - clon) * m_per_lon
    dy = (lat - clat) * m_per_lat
    dist = np.hypot(dx, dy)
    med = float(np.median(dist))
    mad = float(np.median(np.abs(dist - med)))
    thr = max(med + 5.0 * 1.4826 * mad, 2000.0)
    keep = dist <= thr
    if not keep.any():
        keep = np.ones_like(dist, dtype=bool)
    lon_k, lat_k = lon[keep], lat[keep]
    half_lo = 500.0 * min_span_km
    half_hi = 500.0 * max_span_km
    hx_m = min(max((lon_k.max() - lon_k.min()) * 0.5 * m_per_lon + buffer_m, half_lo), half_hi)
    hy_m = min(max((lat_k.max() - lat_k.min()) * 0.5 * m_per_lat + buffer_m, half_lo), half_hi)
    dlon = hx_m / m_per_lon
    dlat = hy_m / m_per_lat
    return (clon - dlon, clat - dlat, clon + dlon, clat + dlat)


def maybe_swap_lonlat(lon, lat):
    """Swap if columns look transposed (US lon ~-65..-125, lat ~20..50)."""
    lon = np.asarray(lon, dtype=float)
    lat = np.asarray(lat, dtype=float)
    mlon = np.nanmedian(np.abs(lon))
    mlat = np.nanmedian(np.abs(lat))
    if np.isfinite(mlon) and np.isfinite(mlat) and mlon < 60.0 and mlat > 60.0:
        return lat, lon, True
    return lon, lat, False


def read_dem_roi(bbox, target_res_m=10.0):
    """Read the DEM over `bbox` onto a regular lon/lat grid (~target_res_m),
    fetching only the ROI window from remote COG(s) via GDAL /vsicurl.
    Returns (Z_m [row 0 = north, metres NAVD88, NaN=NoData], extent)."""
    lon_min, lat_min, lon_max, lat_max = bbox
    import rasterio
    from rasterio.vrt import WarpedVRT
    from rasterio.enums import Resampling
    from rasterio.transform import from_bounds as transform_from_bounds

    latc = 0.5 * (lat_min + lat_max)
    dlat = target_res_m / 111320.0
    dlon = target_res_m / (111320.0 * max(0.1, math.cos(math.radians(latc))))
    nlon = max(2, int(round((lon_max - lon_min) / dlon)))
    nlat = max(2, int(round((lat_max - lat_min) / dlat)))
    longest = max(nlon, nlat)
    if longest > 2500:
        shrink = longest / 2500.0
        nlon = max(2, int(nlon / shrink))
        nlat = max(2, int(nlat / shrink))

    dst_transform = transform_from_bounds(lon_min, lat_min, lon_max, lat_max, nlon, nlat)
    dst = np.full((nlat, nlon), np.nan, dtype="float32")
    if DEM_COG_OVERRIDE_URL:
        urls = [DEM_COG_OVERRIDE_URL]
    else:
        urls = [DEM_COG_URL_TEMPLATE.format(tile=t) for t in dem_tiles_for_bbox(bbox)]

    env = rasterio.Env(
        GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
        CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
        GDAL_HTTP_MULTIPLEX="YES",
        VSI_CACHE="TRUE",
    )
    any_ok = False
    errors = []
    with env:
        for url in urls:
            vsi = url if url.startswith("/vsicurl/") else "/vsicurl/" + url
            try:
                with rasterio.open(vsi) as src:
                    src_nodata = src.nodata
                    with WarpedVRT(src, crs="EPSG:4326", transform=dst_transform,
                                   width=nlon, height=nlat,
                                   resampling=Resampling.bilinear) as vrt:
                        arr = vrt.read(1).astype("float32")
                if src_nodata is not None:
                    arr[arr == src_nodata] = np.nan
                arr[arr <= -500.0] = np.nan
                fill = np.isnan(dst) & np.isfinite(arr)
                dst[fill] = arr[fill]
                any_ok = True
            except Exception as exc:                # noqa: BLE001
                errors.append(f"{vsi.split('/')[-1]}: {exc}")
    if not any_ok:
        raise RuntimeError(
            "Could not read any DEM tile for this area. Tried: %s. Errors: %s"
            % (", ".join(u.split("/")[-1] for u in urls), " | ".join(errors))
        )
    return dst, (lon_min, lat_min, lon_max, lat_max)


def bathtub_depth_ft(Z_m, wl_ft, mask_water=False):
    """Bathtub depth (ft) for a water level (ft NAVD88); NaN where dry.
    By default masks only NoData, so low-lying land below NAVD88 zero still
    floods. With mask_water=True, also hides Z<=0 (treats it as permanent
    open water) for a cleaner look on a land-only DEM."""
    wl_m = float(wl_ft) * 0.3048
    depth_m = wl_m - Z_m
    invalid = ~np.isfinite(Z_m)
    if mask_water:
        invalid = invalid | (Z_m <= 0.0)
    depth_m = np.where(invalid, np.nan, depth_m)
    depth_m = np.where(depth_m < 0.0, np.nan, depth_m)
    return depth_m.astype("float32") * FT_PER_M


def depth_to_rgba_data_uri(depth_ft):
    """Depth-in-feet array -> base64 PNG data URI (RGBA, discrete bins)."""
    from PIL import Image
    import io
    import base64
    h, w = depth_ft.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    n = len(WS_COLORS)
    for b, (r, g, bl) in enumerate(WS_COLORS):
        if b < n - 1:
            mm = (depth_ft >= WS_BINS_FT[b]) & (depth_ft < WS_BINS_FT[b + 1])
        else:
            mm = depth_ft >= WS_BINS_FT[b]
        rgba[mm, 0] = r
        rgba[mm, 1] = g
        rgba[mm, 2] = bl
        rgba[mm, 3] = 255
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def mapbox_zoom_for_bbox(extent, width_px=340, height_px=420, pad=1.06):
    """Approximate a Plotly mapbox zoom that frames the bbox in a grid panel.
    Web-Mercator: world is square (360 deg per 512 px tile at z0); latitude
    needs a cos(lat) term. width/height default to a 2-column panel."""
    lon_min, lat_min, lon_max, lat_max = extent
    latc = 0.5 * (lat_min + lat_max)
    lon_span = max((lon_max - lon_min) * pad, 1e-4)
    lat_span = max((lat_max - lat_min) * pad, 1e-4)
    z_lon = math.log2(360.0 / lon_span) + math.log2(max(width_px, 1) / 512.0)
    z_lat = (math.log2(360.0 / lat_span) + math.log2(max(height_px, 1) / 512.0)
             + math.log2(max(0.05, math.cos(math.radians(latc)))))
    return max(1.0, min(16.0, min(z_lon, z_lat)))


def legend_html(area_note=None):
    """Large, readable inline HTML legend for the discrete depth bins (feet)."""
    labels = ["0–1", "1–2", "2–3", "3–4", "4–5", "5+"]
    items = []
    for (r, g, b), lab in zip(WS_COLORS, labels):
        items.append(
            f'<span style="display:inline-flex;align-items:center;margin-right:24px;margin-bottom:8px;">'
            f'<span style="width:26px;height:26px;background:rgb({r},{g},{b});'
            f'display:inline-block;margin-right:9px;border:1px solid #888;border-radius:4px;"></span>'
            f'<span style="font-size:1.15rem;color:#1f2937;">{lab} ft</span></span>'
        )
    extra = ""
    if area_note:
        extra = (f'<div style="font-size:1.1rem;color:#374151;margin-top:4px;">{area_note}</div>')
    return (
        '<div style="margin:0.5rem 0 0.9rem;padding:0.6rem 0.8rem;background:#f8fafc;'
        'border:1px solid #e2e8f0;border-radius:8px;">'
        '<span style="font-size:1.4rem;font-weight:700;color:#0f172a;margin-right:16px;">Flood depth</span>'
        '<span style="display:inline-flex;flex-wrap:wrap;align-items:center;vertical-align:middle;">'
        + "".join(items) + "</span>" + extra + "</div>"
    )


def depth_to_rgba(depth_ft, alpha=217, colors=None):
    """Depth-in-feet array -> (H,W,4) uint8 RGBA (discrete bins; dry transparent).
    `colors` defaults to the multi-hue ramp; pass WS_COLORS_BLUE for blue-only."""
    cols = colors if colors is not None else WS_COLORS
    h, w = depth_ft.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    n = len(cols)
    for b, (r, g, bl) in enumerate(cols):
        if b < n - 1:
            mm = (depth_ft >= WS_BINS_FT[b]) & (depth_ft < WS_BINS_FT[b + 1])
        else:
            mm = depth_ft >= WS_BINS_FT[b]
        rgba[mm, 0] = r
        rgba[mm, 1] = g
        rgba[mm, 2] = bl
        rgba[mm, 3] = alpha
    return rgba


_TILE_PROVIDERS = {
    "OSM (color)": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    "Light": "https://basemaps.cartocdn.com/rastertiles/light_all/{z}/{x}/{y}.png",
    "Dark": "https://basemaps.cartocdn.com/rastertiles/dark_all/{z}/{x}/{y}.png",
}


def _lonlat_to_tilexy(lon, lat, z):
    n = 2.0 ** z
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n
    return x, y


def tile_zoom_for_bbox(bbox, target_px=1000, tile_px=256, max_zoom=18):
    """Integer slippy zoom so the bbox renders ~target_px wide."""
    lon_min, lat_min, lon_max, lat_max = bbox
    lon_span = max(lon_max - lon_min, 1e-6)
    z = math.log2(target_px * 360.0 / (tile_px * lon_span))
    return int(max(1, min(max_zoom, round(z))))


def fetch_basemap(bbox, zoom, provider_label, tile_px=256, max_tiles=40):
    """Fetch & stitch XYZ tiles for bbox at integer slippy zoom, cropped to bbox.
    Returns (rgb uint8 [H,W,3], bbox). Fetched SERVER-SIDE with a User-Agent,
    which avoids OpenStreetMap's browser 403 ('Referer required by tile policy')
    and uses no WebGL (so any number of maps can be shown). The zoom is reduced
    if needed so no more than `max_tiles` tiles are requested (courtesy/speed)."""
    import io as _io
    import requests
    from PIL import Image
    lon_min, lat_min, lon_max, lat_max = bbox
    url_tmpl = _TILE_PROVIDERS.get(provider_label, _TILE_PROVIDERS["OSM (color)"])
    # Keep the tile count modest by dropping a zoom level if the span is large.
    while zoom > 4:
        _x0, _y0 = _lonlat_to_tilexy(lon_min, lat_max, zoom)
        _x1, _y1 = _lonlat_to_tilexy(lon_max, lat_min, zoom)
        _c = int(math.floor(_x1)) - int(math.floor(_x0)) + 1
        _r = int(math.floor(_y1)) - int(math.floor(_y0)) + 1
        if _c * _r <= max_tiles:
            break
        zoom -= 1
    x0f, y0f = _lonlat_to_tilexy(lon_min, lat_max, zoom)   # NW corner -> top-left
    x1f, y1f = _lonlat_to_tilexy(lon_max, lat_min, zoom)   # SE corner -> bottom-right
    xt0, yt0 = int(math.floor(x0f)), int(math.floor(y0f))
    xt1, yt1 = int(math.floor(x1f)), int(math.floor(y1f))
    nmax = int(2 ** zoom) - 1
    xt0 = max(0, xt0); yt0 = max(0, yt0)
    xt1 = min(nmax, xt1); yt1 = min(nmax, yt1)
    cols = xt1 - xt0 + 1
    rows = yt1 - yt0 + 1
    mosaic = Image.new("RGB", (cols * tile_px, rows * tile_px), (235, 235, 235))
    headers = {"User-Agent": "ADAPT-FloodTool/1.0 (Columbia CCSR research app)"}
    for ix, xt in enumerate(range(xt0, xt1 + 1)):
        for iy, yt in enumerate(range(yt0, yt1 + 1)):
            try:
                resp = requests.get(url_tmpl.format(z=zoom, x=xt, y=yt),
                                    headers=headers, timeout=12)
                if resp.status_code == 200:
                    tile = Image.open(_io.BytesIO(resp.content)).convert("RGB")
                    mosaic.paste(tile, (ix * tile_px, iy * tile_px))
            except Exception:                              # noqa: BLE001
                pass
    left = (x0f - xt0) * tile_px
    top = (y0f - yt0) * tile_px
    right = (x1f - xt0) * tile_px
    bottom = (y1f - yt0) * tile_px
    crop = mosaic.crop((int(round(left)), int(round(top)),
                        max(int(round(right)), int(round(left)) + 1),
                        max(int(round(bottom)), int(round(top)) + 1)))
    return np.asarray(crop, dtype=np.uint8), (lon_min, lat_min, lon_max, lat_max)


def compose_flood_png(basemap_rgb, depth_ft):
    """Alpha-composite the flood overlay onto the basemap; return PNG bytes."""
    import io as _io
    from PIL import Image
    base = Image.fromarray(np.asarray(basemap_rgb, dtype=np.uint8), "RGB").convert("RGBA")
    overlay = Image.fromarray(depth_to_rgba(depth_ft, alpha=150), "RGBA").resize(base.size, Image.NEAREST)
    out = Image.alpha_composite(base, overlay).convert("RGB")
    buf = _io.BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue()


# Public Overpass servers, tried in order. The main one often refuses or
# rate-limits shared cloud hosts (e.g. Streamlit Community Cloud), so the app
# falls back to the mirrors.
OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
)


def _force_ipv4_requests():
    """Make requests/urllib3 resolve hosts to IPv4 only. Hosted containers
    often have no IPv6 route, which shows up as '[Errno 99] Cannot assign
    requested address' when a server also publishes an IPv6 address."""
    try:
        import urllib3.util.connection as _uc
        _uc.HAS_IPV6 = False
    except Exception:                                      # noqa: BLE001
        pass


def fetch_osm_roads_raw(bbox, timeout=60, endpoints=OVERPASS_ENDPOINTS):
    """Overpass JSON for the 'highway' ways in bbox. Tries each server in
    turn, then repeats over IPv4 only if any attempt failed to connect.
    Raises RuntimeError listing every server's failure if all fail."""
    import requests
    lon_min, lat_min, lon_max, lat_max = bbox
    query = (f"[out:json][timeout:{timeout}];"
             f'way["highway"]({lat_min},{lon_min},{lat_max},{lon_max});'
             f"out body geom;")
    import time as _time
    headers = {"User-Agent": "ADAPT-FloodTool/1.0 (Columbia CCSR research app)"}
    errors = []
    retry_ipv4 = False
    deadline = _time.monotonic() + 150.0           # give up after ~2.5 minutes in total
    for attempt in (0, 1):
        if attempt == 1:
            if not retry_ipv4:
                break
            _force_ipv4_requests()
        for url in endpoints:
            if _time.monotonic() > deadline:
                errors.append("stopped: no answer within 2.5 minutes")
                break
            host = url.split("/")[2]
            try:
                resp = requests.post(url, data={"data": query}, headers=headers,
                                     timeout=(8, 45))
            except requests.exceptions.ConnectionError as exc:
                # Retry over IPv4 only for the no-IPv6-route symptom.
                msg = str(exc)
                if ("Errno 99" in msg or "Cannot assign requested address" in msg
                        or "Errno 101" in msg or "Network is unreachable" in msg):
                    retry_ipv4 = True
                errors.append(f"{host}: {str(exc)[:160]}")
                continue
            except Exception as exc:                       # noqa: BLE001
                errors.append(f"{host}: {str(exc)[:160]}")
                continue
            if resp.status_code != 200:
                errors.append(f"{host}: HTTP {resp.status_code}")
                continue
            try:
                raw = resp.json()
            except ValueError:
                errors.append(f"{host}: reply was not JSON")
                continue
            # A busy server can answer 200 with a 'remark' and no data.
            if not raw.get("elements") and raw.get("remark"):
                errors.append(f"{host}: {str(raw.get('remark'))[:120]}")
                continue
            return raw
    raise RuntimeError("No Overpass server answered. " + " | ".join(errors[-len(endpoints):]))


def osm_roads_from_raw(raw):
    """Overpass JSON -> list of {'coords' Nx2 (lon,lat), 'name', 'ref', 'highway'}."""
    roads = []
    for el in raw.get("elements", []):
        if el.get("type") != "way" or "geometry" not in el:
            continue
        coords = np.array([[p["lon"], p["lat"]] for p in el["geometry"]], dtype=float)
        if len(coords) < 2:
            continue
        tags = el.get("tags", {})
        roads.append({
            "coords": coords,
            "name": tags.get("name", "") or "",
            "ref": tags.get("ref", "") or "",
            "highway": tags.get("highway", "unknown"),
        })
    return roads


def fetch_osm_roads(bbox, timeout=60):
    """Query Overpass for 'highway' ways in bbox (server-side).
    Returns a list of dicts: {'coords' Nx2 (lon,lat), 'name', 'ref', 'highway'}.
    Mirrors download_osm_roads_pamunkey.py but runs live for the map ROI."""
    return osm_roads_from_raw(fetch_osm_roads_raw(bbox, timeout=timeout))


def _way_touches_bbox(coords, bbox):
    """True if a polyline (Nx2 lon/lat) has a vertex inside bbox or a segment
    crossing it, i.e. the ways an Overpass bbox query returns."""
    lon_min, lat_min, lon_max, lat_max = bbox
    x, y = coords[:, 0], coords[:, 1]
    if x.max() < lon_min or x.min() > lon_max or y.max() < lat_min or y.min() > lat_max:
        return False
    if np.any((x >= lon_min) & (x <= lon_max) & (y >= lat_min) & (y <= lat_max)):
        return True
    # Liang-Barsky clip of every segment against the box.
    x0, y0, dx, dy = x[:-1], y[:-1], np.diff(x), np.diff(y)
    t0 = np.zeros_like(x0)
    t1 = np.ones_like(x0)
    ok = np.ones_like(x0, dtype=bool)
    for p, q in ((-dx, x0 - lon_min), (dx, lon_max - x0), (-dy, y0 - lat_min), (dy, lat_max - y0)):
        par = p == 0
        ok &= ~(par & (q < 0))
        with np.errstate(divide="ignore", invalid="ignore"):
            r = np.where(par, 0.0, q / np.where(par, 1.0, p))
        t0 = np.where(~par & (p < 0), np.maximum(t0, r), t0)
        t1 = np.where(~par & (p > 0), np.minimum(t1, r), t1)
    return bool(np.any(ok & (t0 <= t1)))


def load_local_osm_roads(path, bbox):
    """Roads from a saved Overpass file ({slug}_osm_roads.json, made by
    download_osm_roads.py), limited to the ways that touch bbox, the same set
    an Overpass query for bbox returns. Returns None if the file is missing,
    unreadable, or does not cover the whole bbox."""
    import json as _json
    try:
        with open(path, "r", encoding="utf-8") as fh:
            saved = _json.load(fh)
        fb = saved["bbox"]
    except Exception:                                      # noqa: BLE001
        return None
    lon_min, lat_min, lon_max, lat_max = bbox
    if not (fb[0] <= lon_min and fb[1] <= lat_min and fb[2] >= lon_max and fb[3] >= lat_max):
        return None
    return [r for r in osm_roads_from_raw(saved.get("overpass", {}))
            if _way_touches_bbox(r["coords"], bbox)]


def sample_dem_bilinear(Zm, ext, lons, lats):
    """Bilinear-sample the DEM (row 0 = north) at arrays of lon/lat; NaN off-grid."""
    lon_min, lat_min, lon_max, lat_max = ext
    nlat, nlon = Zm.shape
    fc = (np.asarray(lons, float) - lon_min) / max(lon_max - lon_min, 1e-9) * (nlon - 1)
    fr = (lat_max - np.asarray(lats, float)) / max(lat_max - lat_min, 1e-9) * (nlat - 1)
    out = np.full(fc.shape, np.nan, dtype=float)
    inb = (fc >= 0) & (fc <= nlon - 1) & (fr >= 0) & (fr <= nlat - 1)
    if inb.any():
        c0 = np.floor(fc[inb]).astype(int)
        r0 = np.floor(fr[inb]).astype(int)
        c1 = np.minimum(c0 + 1, nlon - 1)
        r1 = np.minimum(r0 + 1, nlat - 1)
        tx = fc[inb] - c0
        ty = fr[inb] - r0
        z00 = Zm[r0, c0]; z01 = Zm[r0, c1]; z10 = Zm[r1, c0]; z11 = Zm[r1, c1]
        top = z00 * (1 - tx) + z01 * tx
        bot = z10 * (1 - tx) + z11 * tx
        out[inb] = top * (1 - ty) + bot * ty
    return out


def _dilate_mask(mask, iters):
    """8-connectivity binary dilation by `iters` cells (numpy; no scipy)."""
    m = mask.copy()
    for _ in range(max(0, int(iters))):
        d = m.copy()
        d[1:, :] |= m[:-1, :]; d[:-1, :] |= m[1:, :]
        d[:, 1:] |= m[:, :-1]; d[:, :-1] |= m[:, 1:]
        d[1:, 1:] |= m[:-1, :-1]; d[:-1, :-1] |= m[1:, 1:]
        d[1:, :-1] |= m[:-1, 1:]; d[:-1, 1:] |= m[1:, :-1]
        m = d
    return m


def classify_roads(Zm, ext, roads, wl_ft, prox_m=30.0, sample_m=8.0):
    """Classify road samples as 0 dry / 1 proximate / 2 flooded for a water level
    (ft NAVD88). Open water (Z<=0) is excluded from the flood mask, matching the
    flood-map tab. Returns (segments, counts) where segments is a list of
    ((lon0,lat0),(lon1,lat1),status)."""
    nlat, nlon = Zm.shape
    lon_min, lat_min, lon_max, lat_max = ext
    wl_m = float(wl_ft) * 0.3048
    Zmask = np.where(np.isnan(Zm), 9999.0, Zm)
    flood = (wl_m - Zmask > 0) & (Zmask > 0)
    # proximity buffer in grid cells from the cell size
    dy_m = (lat_max - lat_min) * 111320.0 / max(nlat, 1)
    dx_m = (lon_max - lon_min) * 111320.0 * math.cos(math.radians(0.5 * (lat_min + lat_max))) / max(nlon, 1)
    prox_cells = max(1, int(math.ceil(prox_m / max(min(dx_m, dy_m), 1e-6))))
    prox = _dilate_mask(flood, prox_cells) & (~flood) & (Zmask > 0)

    def to_rc(lons, lats):
        c = np.clip(((lons - lon_min) / max(lon_max - lon_min, 1e-9) * (nlon - 1)).round().astype(int), 0, nlon - 1)
        r = np.clip(((lat_max - lats) / max(lat_max - lat_min, 1e-9) * (nlat - 1)).round().astype(int), 0, nlat - 1)
        return r, c

    segs = []
    nf = npx = nd = 0
    for rd in roads:
        coords = rd["coords"]
        lons, lats = coords[:, 0], coords[:, 1]
        # keep roads that intersect the ROI
        if not np.any((lons >= lon_min) & (lons <= lon_max) & (lats >= lat_min) & (lats <= lat_max)):
            continue
        dlat = np.diff(lats) * 111320.0
        dlon = np.diff(lons) * 111320.0 * math.cos(math.radians(float(np.mean(lats))))
        seglen = np.sqrt(dlat ** 2 + dlon ** 2)
        cum = np.concatenate([[0.0], np.cumsum(seglen)])
        total = float(cum[-1])
        if total < 1.0:
            continue
        n = max(int(total / sample_m), 2)
        sd = np.linspace(0.0, total, n)
        slat = np.interp(sd, cum, lats)
        slon = np.interp(sd, cum, lons)
        r, c = to_rc(slon, slat)
        status = np.zeros(n, dtype=int)
        status[prox[r, c]] = 1
        status[flood[r, c]] = 2
        for i in range(n - 1):
            s = int(max(status[i], status[i + 1]))
            segs.append(((float(slon[i]), float(slat[i])), (float(slon[i + 1]), float(slat[i + 1])), s))
            if s == 2:
                nf += 1
            elif s == 1:
                npx += 1
            else:
                nd += 1
    tot = nf + npx + nd
    counts = {
        "flood": nf, "prox": npx, "dry": nd, "total": tot,
        "pct_flood": 100.0 * nf / tot if tot else 0.0,
        "pct_prox": 100.0 * npx / tot if tot else 0.0,
        "pct_dry": 100.0 * nd / tot if tot else 0.0,
    }
    return segs, counts


def classify_roads_access(Zm, ext, roads, wl_ft, sample_m=8.0,
                          source="boundary", snap_dp=6, boundary_tol_m=35.0,
                          entry_points=None, entrance_reach_m=300.0):
    """Topological road-accessibility classifier (replaces the proximity buffer).

    A road is classified by whether you can still REACH it on dry roads, not by
    how close it sits to water. Status codes match classify_roads so the same
    renderer/legend plumbing works:

        0 = dry & accessible   (a dry path to the outside world still exists)
        1 = dry & INACCESSIBLE (dry itself, but every dry route out is severed
                                by flooding - e.g. both ends flooded, or one end
                                flooded and the other a dead-end, or stranded
                                behind a flooded road as part of a cut-off cluster)
        2 = flooded            (the road surface is below the water level)

    Method. OSM ways carry their topology as *shared vertex coordinates* at
    intersections (and bridges/overpasses share no node, so they don't connect).
    We rebuild the network graph by snapping coordinates, mark each span dry or
    flooded against the same bathtub mask as the Flood Maps tab, then ask a pure
    reachability question:

        a dry span is INACCESSIBLE  <=>  it is reachable from a SOURCE in the
        full network (flooded roads treated as passable) but NOT reachable once
        flooded roads are removed.

    The "full-network" baseline is the causality guard: it ensures we only blame
    flooding for what flooding actually severed, never roads that were already
    isolated in the raw OSM data (those are returned as status 0 and tallied in
    counts['predisc']).

    source: 'boundary' (default) treats roads leaving the map as exits to the
    wider world - the most defensible anchor when the map is a sub-area of a
    larger network. 'largest' instead treats the biggest dry component as the
    mainland; used automatically as a fallback if no boundary exits are found.

    entry_points: optional list of (lon, lat) gateways for a designated main
    entrance that should stay open even when the entrance road itself floods
    (e.g. a low causeway that is the site's only access and floods first). For
    each gateway we take the nearest network node, walk the CONTIGUOUS flooded
    blob outward from it up to entrance_reach_m (network metres) - capturing the
    flooded entrance and its flooded gateway intersection but not a separate
    interior flood - reconnect that blob, and treat the gateway as connected to
    the outside. The entrance road still renders red where flooded; it just no
    longer severs the whole site. Interior floods that form their own separate
    blobs keep stranding roads normally, so this never over-rescues.

    Returns (segments, counts) where segments is a list of
    ((lon0,lat0),(lon1,lat1),status), drawn at the same ~sample_m resolution as
    classify_roads so the map detail is unchanged.
    """
    from collections import defaultdict

    lon_min, lat_min, lon_max, lat_max = ext
    nlat, nlon = Zm.shape
    wl_m = float(wl_ft) * 0.3048
    Zmask = np.where(np.isnan(Zm), 9999.0, Zm)
    flood = (wl_m - Zmask > 0) & (Zmask > 0)          # same flood mask as the flood-map tab

    def flooded_fn(lons, lats):
        # nearest-cell lookup, matching classify_roads' to_rc()
        c = np.clip(((np.asarray(lons, float) - lon_min) / max(lon_max - lon_min, 1e-9)
                     * (nlon - 1)).round().astype(int), 0, nlon - 1)
        r = np.clip(((lat_max - np.asarray(lats, float)) / max(lat_max - lat_min, 1e-9)
                     * (nlat - 1)).round().astype(int), 0, nlat - 1)
        return flood[r, c]

    tol_lat = boundary_tol_m / 111320.0
    midlat = 0.5 * (lat_min + lat_max)
    tol_lon = boundary_tol_m / (111320.0 * max(math.cos(math.radians(midlat)), 1e-6))

    def nkey(lon, lat):
        return (round(lon, snap_dp), round(lat, snap_dp))

    def is_boundary(lon, lat):
        on_lr = ((abs(lon - lon_min) <= tol_lon or abs(lon - lon_max) <= tol_lon)
                 and (lat_min - tol_lat <= lat <= lat_max + tol_lat))
        on_tb = ((abs(lat - lat_min) <= tol_lat or abs(lat - lat_max) <= tol_lat)
                 and (lon_min - tol_lon <= lon <= lon_max + tol_lon))
        return on_lr or on_tb

    def densify(coords):
        """Sample ~every sample_m along a way, forcing the original OSM vertices in
        (so intersections snap) and reporting which samples are real vertices."""
        lons, lats = coords[:, 0], coords[:, 1]
        meanlat = float(np.mean(lats))
        dlat = np.diff(lats) * 111320.0
        dlon = np.diff(lons) * 111320.0 * math.cos(math.radians(meanlat))
        cum = np.concatenate([[0.0], np.cumsum(np.sqrt(dlat ** 2 + dlon ** 2))])
        total = float(cum[-1])
        if total < 1.0:
            return None
        fill = np.linspace(0.0, total, max(int(total / sample_m), 1) + 1)
        d = np.unique(np.concatenate([cum, fill]))
        slon = np.interp(d, cum, lons)
        slat = np.interp(d, cum, lats)
        return slon, slat, np.isin(d, cum)

    dry_edges = []          # (u, v, [subsegs])
    all_edges = []          # (u, v) for the baseline (flood-as-passable) graph
    flooded_subsegs = []
    flooded_uv = []         # (u, v) node keys of flooded spans, for the entrance walk
    node_xy = {}

    for rd in roads:
        coords = np.asarray(rd["coords"], dtype=float)
        if len(coords) < 2:
            continue
        if not np.any((coords[:, 0] >= lon_min) & (coords[:, 0] <= lon_max) &
                      (coords[:, 1] >= lat_min) & (coords[:, 1] <= lat_max)):
            continue
        dz = densify(coords)
        if dz is None:
            continue
        slon, slat, is_vtx = dz
        fl = np.asarray(flooded_fn(slon, slat), dtype=bool)
        n = len(slon)
        for i in range(n):
            node_xy.setdefault(nkey(slon[i], slat[i]), (slon[i], slat[i]))
        brk = np.zeros(n, dtype=bool)
        brk[0] = brk[-1] = True
        brk[is_vtx] = True                       # split at intersections
        trans = np.where(fl[:-1] != fl[1:])[0]   # split at dry/flood transitions
        brk[trans] = True
        brk[trans + 1] = True
        bidx = np.where(brk)[0]
        for a, b in zip(bidx[:-1], bidx[1:]):
            u = nkey(slon[a], slat[a]); v = nkey(slon[b], slat[b])
            if u == v:
                continue
            span_flooded = bool(fl[a:b + 1].any())
            subs = [((float(slon[j]), float(slat[j])),
                     (float(slon[j + 1]), float(slat[j + 1]))) for j in range(a, b)]
            all_edges.append((u, v))
            if span_flooded:
                flooded_subsegs.extend(subs)
                flooded_uv.append((u, v))
            else:
                dry_edges.append((u, v, subs))

    parent = {}

    def find(x):
        parent.setdefault(x, x)
        r = x
        while parent[r] != r:
            r = parent[r]
        while parent[x] != r:
            parent[x], x = r, parent[x]
        return r

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    parent_all = {}

    def find_a(x):
        parent_all.setdefault(x, x)
        r = x
        while parent_all[r] != r:
            r = parent_all[r]
        while parent_all[x] != r:
            parent_all[x], x = r, parent_all[x]
        return r

    def union_a(a, b):
        ra, rb = find_a(a), find_a(b)
        if ra != rb:
            parent_all[ra] = rb

    for u, v, _ in dry_edges:
        union(u, v)
    for u, v in all_edges:
        union_a(u, v)

    boundary_nodes = [k for k, (lo, la) in node_xy.items() if is_boundary(lo, la)]
    used_source = source
    if source == "boundary" and boundary_nodes:
        dry_src_roots = set(find(k) for k in boundary_nodes)
        all_src_roots = set(find_a(k) for k in boundary_nodes)
    else:
        used_source = "largest" if source == "boundary" else source
        comp_size = defaultdict(int)
        for u, v, _ in dry_edges:
            comp_size[find(u)] += 1
        main = max(comp_size, key=comp_size.get) if comp_size else None
        dry_src_roots = {main} if main is not None else set()
        all_src_roots = set()
        if main is not None:
            for u, v, _ in dry_edges:
                if find(u) == main:
                    all_src_roots.add(find_a(u)); break

    # Designated main-entrance exemption. Access can come through the entrance road
    # and its (often flooded) gateway intersection, so for each gateway we take the
    # nearest node, walk the contiguous flooded blob outward up to entrance_reach_m
    # (network metres) - capturing the flooded entrance + flooded gateway but not a
    # separate interior flood - reconnect that blob, and make the gateway a source.
    n_entry = 0
    if entry_points:
        import heapq
        cosl = math.cos(math.radians(midlat))

        def _mlen(a, b):
            return math.hypot((node_xy[a][0] - node_xy[b][0]) * 111320.0 * cosl,
                              (node_xy[a][1] - node_xy[b][1]) * 111320.0)

        fl_adj = defaultdict(list)
        for u, v in flooded_uv:
            L = _mlen(u, v)
            fl_adj[u].append((v, L)); fl_adj[v].append((u, L))
        for ep in entry_points:
            if not node_xy:
                break
            G = min(node_xy, key=lambda k: ((node_xy[k][0] - ep[0]) * cosl) ** 2
                    + (node_xy[k][1] - ep[1]) ** 2)
            reached = {G: 0.0}; pq = [(0.0, G)]
            while pq:
                d, nn = heapq.heappop(pq)
                if d > reached.get(nn, 1e18):
                    continue
                for w, L in fl_adj[nn]:
                    nd = d + L
                    if nd <= entrance_reach_m and nd < reached.get(w, 1e18):
                        reached[w] = nd; heapq.heappush(pq, (nd, w))
            for nn in reached:
                union(G, nn); union_a(G, nn)
            dry_src_roots.add(find(G)); all_src_roots.add(find_a(G)); n_entry += 1

    segs = []
    nf = na = ni = npd = 0
    for s in flooded_subsegs:
        segs.append((s[0], s[1], 2)); nf += 1
    for u, v, subs in dry_edges:
        if find(u) in dry_src_roots:
            status = 0; na += len(subs)
        elif find_a(u) in all_src_roots:
            status = 1; ni += len(subs)
        else:
            status = 0; npd += len(subs)        # isolated in raw data; not flood-caused
        for s in subs:
            segs.append((s[0], s[1], status))

    tot = nf + na + ni + npd
    counts = {
        "flood": nf, "inacc": ni, "dry": na, "predisc": npd, "total": tot,
        "pct_flood": 100.0 * nf / tot if tot else 0.0,
        "pct_inacc": 100.0 * ni / tot if tot else 0.0,
        "pct_dry": 100.0 * (na + npd) / tot if tot else 0.0,
        "source_used": used_source, "n_boundary_nodes": len(boundary_nodes),
        "n_entry": n_entry,
        # back-compat alias so any caller reading pct_prox still works
        "prox": ni, "pct_prox": 100.0 * ni / tot if tot else 0.0,
    }
    return segs, counts


def _entrance_nodes_edges(roads, snap_dp=6):
    """Flood-agnostic road graph for entrance detection: node_key -> (lon,lat),
    adjacency, a per-node 'on a main road' flag (from OSM highway class), and
    node degree. Main roads are the through-classes (primary/secondary/...)."""
    from collections import defaultdict
    MAIN = {"motorway", "trunk", "primary", "secondary", "tertiary",
            "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link"}

    def nk(lo, la):
        return (round(lo, snap_dp), round(la, snap_dp))

    adj = defaultdict(set); xy = {}; ismain = defaultdict(bool)
    for rd in roads:
        c = np.asarray(rd["coords"], dtype=float)
        if len(c) < 2:
            continue
        is_main = rd.get("highway", "") in MAIN
        for i in range(len(c) - 1):
            u = nk(*c[i]); v = nk(*c[i + 1])
            xy[u] = (float(c[i][0]), float(c[i][1]))
            xy[v] = (float(c[i + 1][0]), float(c[i + 1][1]))
            if u != v:
                adj[u].add(v); adj[v].add(u)
                if is_main:
                    ismain[u] = True; ismain[v] = True
    deg = {n: len(adj[n]) for n in adj}
    return xy, adj, ismain, deg


def detect_main_entrance(roads, ext, site_lonlat, snap_dp=6, boundary_tol_m=40.0):
    """Auto-detect the site's main entrance gateway: the site-side end of the road
    that connects the building cluster to the OUTSIDE WORLD. Returns [(lon, lat)] to
    pass as entry_points, or [] if it can't be determined.

    The connection to the outside is anchored on the building cluster's OWN
    component: a classified main road if one is present in that component, else the
    component's boundary exits (roads leaving the map). This is deliberately NOT
    anchored on the global classified main road - at sites like Pamunkey the
    reservation network is a separate OSM component whose nearest classified road
    (King William Rd) is ~1 km away and never shares a node, so a main-road anchor
    would find no path and return nothing. The gateway is the first junction
    (degree >= 3) reached after leaving the anchor on the path toward the cluster,
    i.e. where the single entrance corridor meets the local network - placing the
    exemption at the entrance, not deep inside the site."""
    from collections import deque
    xy, adj, ismain, deg = _entrance_nodes_edges(roads, snap_dp)
    if not xy:
        return []
    lon_min, lat_min, lon_max, lat_max = ext
    cosl = math.cos(math.radians(0.5 * (lat_min + lat_max)))
    tol = boundary_tol_m / 111320.0
    tolx = boundary_tol_m / (111320.0 * max(cosl, 1e-6))

    def d2(a, b):
        return ((a[0] - b[0]) * cosl) ** 2 + (a[1] - b[1]) ** 2

    def is_boundary(lo, la):
        return ((abs(lo - lon_min) <= tolx or abs(lo - lon_max) <= tolx)
                and lat_min - tol <= la <= lat_max + tol) or \
               ((abs(la - lat_min) <= tol or abs(la - lat_max) <= tol)
                and lon_min - tolx <= lo <= lon_max + tolx)

    # nearest node to the building centroid, and the component it belongs to
    N_site = min(xy, key=lambda n: d2(xy[n], site_lonlat))
    comp = {N_site}; dq = deque([N_site])
    while dq:
        u = dq.popleft()
        for w in adj[u]:
            if w not in comp:
                comp.add(w); dq.append(w)

    # anchors = how THIS component reaches the outside: a main road within the
    # component if present, else the component's boundary exits.
    main_in = [n for n in comp if ismain[n]]
    bexits = [n for n in comp if is_boundary(*xy[n])]
    anchors = main_in if main_in else bexits
    if not anchors:                              # component has no outside link
        anchors = ([n for n in xy if ismain[n]]
                   or [n for n, (lo, la) in xy.items() if is_boundary(lo, la)])
    if not anchors:
        return []
    anchors = set(anchors)

    pred = {}; seen = set(anchors); dq = deque(anchors)
    for a in anchors:
        pred[a] = None
    while dq:
        u = dq.popleft()
        for w in adj[u]:
            if w not in seen:
                seen.add(w); pred[w] = u; dq.append(w)
    if N_site not in seen:
        return []
    path = []; x = N_site
    while x is not None:
        path.append(x); x = pred[x]
    path.reverse()                               # anchor -> site
    gateway = None
    for n in path:
        if n in anchors:
            continue
        if deg.get(n, 0) >= 3:
            gateway = n; break
    if gateway is None:
        cands = [n for n in path if n not in anchors]
        gateway = min(cands, key=lambda n: d2(xy[n], site_lonlat)) if cands else N_site
    return [xy[gateway]]


def compose_road_png(basemap_rgb, depth_ft, segments, ext):
    """Basemap + flood-depth overlay + colored road segments -> PNG bytes.
    Segment colors: green dry/accessible, violet inaccessible (cut off by
    flooding), red flooded."""
    import io as _io
    from PIL import Image, ImageDraw
    base = Image.fromarray(np.asarray(basemap_rgb, dtype=np.uint8), "RGB").convert("RGBA")
    W, H = base.size
    overlay = Image.fromarray(depth_to_rgba(depth_ft, alpha=150, colors=WS_COLORS_BLUE), "RGBA").resize((W, H), Image.NEAREST)
    base = Image.alpha_composite(base, overlay)
    draw = ImageDraw.Draw(base)
    lon_min, lat_min, lon_max, lat_max = ext

    def px(lon, lat):
        x = (lon - lon_min) / max(lon_max - lon_min, 1e-9) * (W - 1)
        y = (lat_max - lat) / max(lat_max - lat_min, 1e-9) * (H - 1)
        return (x, y)

    colors = {0: (34, 139, 34, 235), 1: (138, 43, 226, 245), 2: (220, 20, 20, 250)}
    base_w = max(2, int(round(W / 450)))
    widths = {0: base_w, 1: base_w + 1, 2: base_w + 2}
    for status in (0, 1, 2):                       # dry, then inaccessible, then flooded on top
        col = colors[status]
        w = widths[status]
        for p0, p1, s in segments:
            if s != status:
                continue
            draw.line([px(*p0), px(*p1)], fill=col, width=w)
    out = base.convert("RGB")
    buf = _io.BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue()


# Road segment styles shared by the interactive road map: status -> (label,
# color, line width in screen px). Same colors as compose_road_png.
_ROAD_STYLES = {
    0: ("Dry (reachable)", "rgb(34,139,34)", 2.2),
    1: ("Dry but inaccessible", "rgb(138,43,226)", 3.0),
    2: ("Flooded", "rgb(220,20,20)", 3.6),
}


def compose_flood_jpeg(basemap_rgb, depth_ft, quality=88):
    """Basemap + flood-depth shading (no roads) -> JPEG bytes. The raster
    layer of the interactive road map; JPEG keeps it far smaller than PNG."""
    import io as _io
    from PIL import Image
    base = Image.fromarray(np.asarray(basemap_rgb, dtype=np.uint8), "RGB").convert("RGBA")
    W, H = base.size
    overlay = Image.fromarray(depth_to_rgba(depth_ft, alpha=150, colors=WS_COLORS_BLUE),
                              "RGBA").resize((W, H), Image.NEAREST)
    comp = Image.alpha_composite(base, overlay).convert("RGB")
    buf = _io.BytesIO()
    comp.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def road_map_figure(raster_jpeg, segments, ext, height_px=None, width_hint_px=1100):
    """Interactive road map (zoom in/out and pan) as a Plotly figure.

    `raster_jpeg` is the basemap + flood-depth shading from
    compose_flood_jpeg, placed under the axes in lon/lat; the roads are drawn
    on top as vector lines, so they stay sharp at any zoom. It uses plain
    Plotly axes (SVG), not a WebGL map, and the basemap is still fetched
    server-side. Drag a box to zoom into a section, double-click to reset;
    the toolbar has zoom in/out, pan and a PNG download.
    """
    import base64 as _b64
    src = "data:image/jpeg;base64," + _b64.b64encode(raster_jpeg).decode("ascii")

    lon_min, lat_min, lon_max, lat_max = ext
    fig = go.Figure()
    for status in (0, 1, 2):                       # dry, then inaccessible, then flooded on top
        xs, ys = [], []
        for p0, p1, s in segments:
            if s == status:
                xs.extend((p0[0], p1[0], None))
                ys.extend((p0[1], p1[1], None))
        if xs:
            name, col, w = _ROAD_STYLES[status]
            fig.add_trace(go.Scatter(
                x=xs, y=ys, mode="lines", line=dict(color=col, width=w),
                name=name, hoverinfo="name", showlegend=False))
    fig.add_layout_image(dict(
        source=src, xref="x", yref="y", x=lon_min, y=lat_max,
        sizex=lon_max - lon_min, sizey=lat_max - lat_min,
        sizing="stretch", layer="below"))

    # Keep ground distances true: one degree of latitude spans 1/cos(lat)
    # times the pixels of one degree of longitude.
    latc = 0.5 * (lat_min + lat_max)
    coslat = max(0.05, math.cos(math.radians(latc)))
    if height_px is None:
        aspect = (lat_max - lat_min) / max((lon_max - lon_min) * coslat, 1e-9)
        height_px = int(min(900, max(420, width_hint_px * aspect)))
    fig.update_xaxes(range=[lon_min, lon_max], visible=False, constrain="domain")
    fig.update_yaxes(range=[lat_min, lat_max], visible=False, scaleanchor="x",
                     scaleratio=1.0 / coslat, constrain="domain")
    fig.update_layout(
        height=height_px, margin=dict(l=0, r=0, t=0, b=0), dragmode="zoom",
        plot_bgcolor="white", paper_bgcolor="white", hovermode="closest",
    )
    return fig


def road_map_plotly_config(filename="road_map"):
    """Plotly config for road_map_figure: no wheel zoom (so the page still
    scrolls), zoom in/out + pan + reset in the toolbar, high-res PNG export."""
    return {
        "scrollZoom": False,
        "doubleClick": "reset",          # double-click always returns to the full map
        "displaylogo": False,
        "modeBarButtonsToRemove": ["select2d", "lasso2d", "autoScale2d"],
        "toImageButtonOptions": {"format": "png", "scale": 3, "filename": filename},
    }


# Namespace so the Flood Maps tab can keep calling fdem.<fn>(...).
fdem = SimpleNamespace(
    dem_tiles_for_bbox=dem_tiles_for_bbox,
    roi_from_lonlat=roi_from_lonlat,
    maybe_swap_lonlat=maybe_swap_lonlat,
    read_dem_roi=read_dem_roi,
    bathtub_depth_ft=bathtub_depth_ft,
    depth_to_rgba=depth_to_rgba,
    tile_zoom_for_bbox=tile_zoom_for_bbox,
    fetch_basemap=fetch_basemap,
    compose_flood_png=compose_flood_png,
    fetch_osm_roads=fetch_osm_roads,
    fetch_osm_roads_raw=fetch_osm_roads_raw,
    load_local_osm_roads=load_local_osm_roads,
    sample_dem_bilinear=sample_dem_bilinear,
    classify_roads=classify_roads,
    classify_roads_access=classify_roads_access,
    detect_main_entrance=detect_main_entrance,
    compose_road_png=compose_road_png,
    compose_flood_jpeg=compose_flood_jpeg,
    road_map_figure=road_map_figure,
    road_map_plotly_config=road_map_plotly_config,
    legend_html=legend_html,
)


@st.cache_data(show_spinner=False)
def _cached_dem_roi(bbox, res_m):
    """Cache wrapper around read_dem_roi (keyed on rounded bbox)."""
    return fdem.read_dem_roi(bbox, res_m)


@st.cache_data(show_spinner=False)
def _cached_basemap(bbox, zoom, provider_label):
    """Cache wrapper around fetch_basemap (one fetch per ROI/zoom/provider)."""
    return fdem.fetch_basemap(bbox, zoom, provider_label)


@st.cache_data(show_spinner=False)
def _cached_osm_roads(bbox):
    """Cache wrapper around fetch_osm_roads (one Overpass query per ROI)."""
    return fdem.fetch_osm_roads(bbox)


@st.cache_data(show_spinner=False)
def _cached_local_roads(path, mtime, bbox):
    """Cache wrapper around load_local_osm_roads (mtime in the key, so an
    updated roads file is picked up)."""
    return fdem.load_local_osm_roads(path, bbox)

