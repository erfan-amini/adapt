"""Address lookup for the home report.

The building inventory has coordinates but, for most buildings, no street
address. So a typed address is geocoded with free public services and the
result is snapped to the nearest building in the inventory:

    1. OpenStreetMap Nominatim: returns the building footprint when OSM has
       the address, which lands on the inventory point almost exactly.
    2. US Census Bureau geocoder: a fallback that interpolates along the
       street, usually within 10 to 20 m of the house.

Neither needs an API key. Results are cached for the session so a second
look-up of the same address makes no network call.

The FEMA National Flood Hazard Layer is queried the same way for the flood
zone at a point. Every call is optional: when a service cannot be reached
the caller gets None and the page says so.
"""
import math
import re

import numpy as np
import pandas as pd
import requests
import streamlit as st

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
FEMA_NFHL_URL = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query"
USER_AGENT = "ADAPT flood risk tool (Columbia Climate School; contact ea3246@columbia.edu)"
TIMEOUT = 8


def clean_address(text):
    """Collapse whitespace and stray punctuation in a typed address."""
    text = re.sub(r"\s+", " ", str(text or "")).strip(" ,.;")
    return text


def _with_place(address, place_hint):
    """Append the community and state when the user typed only a street address."""
    a = address.lower()
    hint = (place_hint or "").strip()
    if not hint:
        return address
    first = hint.split(",")[0].strip().lower()
    if first and first in a:
        return address
    return f"{address}, {hint}"


class _Unreachable(Exception):
    """A geocoder could not be reached. Raised inside the cached helpers so the
    failure is not cached (st.cache_data stores return values, not exceptions)."""


def geocode_nominatim(address):
    try:
        return _geocode_nominatim(address)
    except _Unreachable:
        return None


def geocode_census(address):
    try:
        return _geocode_census(address)
    except _Unreachable:
        return None


@st.cache_data(show_spinner=False, ttl=24 * 3600)
def _geocode_nominatim(address):
    """OpenStreetMap Nominatim: returns (lat, lon, label, kind) or None."""
    try:
        r = requests.get(
            NOMINATIM_URL,
            params={"q": address, "format": "json", "limit": 1, "addressdetails": 1, "countrycodes": "us"},
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        hits = r.json()
    except Exception as exc:
        raise _Unreachable(str(exc))
    if not hits:
        return None
    h = hits[0]
    kind = str(h.get("addresstype") or h.get("class") or "")
    # Only accept house-level matches; a street or town centroid would snap
    # to a random building.
    if kind not in ("building", "house", "residential", "apartments", "detached", "semidetached_house",
                    "terrace", "yes", "commercial", "retail", "industrial", "place"):
        if not (h.get("address") or {}).get("house_number"):
            return None
    try:
        return float(h["lat"]), float(h["lon"]), h.get("display_name", address), "OpenStreetMap"
    except (KeyError, TypeError, ValueError):
        return None


@st.cache_data(show_spinner=False, ttl=24 * 3600)
def _geocode_census(address):
    """US Census geocoder: returns (lat, lon, label, kind) or None."""
    try:
        r = requests.get(
            CENSUS_URL,
            params={"address": address, "benchmark": "Public_AR_Current", "format": "json"},
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        matches = (r.json().get("result") or {}).get("addressMatches") or []
    except Exception as exc:
        raise _Unreachable(str(exc))
    if not matches:
        return None
    m = matches[0]
    try:
        c = m["coordinates"]
        return float(c["y"]), float(c["x"]), m.get("matchedAddress", address), "US Census"
    except (KeyError, TypeError, ValueError):
        return None


def geocode(address, place_hint=""):
    """Geocode a typed address. Returns a dict or None.

    dict: lat, lon, label (the matched address), source.
    """
    address = clean_address(address)
    if not address:
        return None
    query = _with_place(address, place_hint)
    unreachable = 0
    for fn in (_geocode_nominatim, _geocode_census):
        try:
            hit = fn(query)
        except _Unreachable:
            unreachable += 1
            continue
        if hit:
            lat, lon, label, source = hit
            return {"lat": lat, "lon": lon, "label": label, "source": source, "query": query}
    if unreachable == 2:
        return {"error": "The address services could not be reached. Try again in a moment."}
    return None


_ABBR = {"road": "rd", "street": "st", "drive": "dr", "place": "pl", "avenue": "ave", "lane": "ln",
         "court": "ct", "boulevard": "blvd", "circle": "cir", "terrace": "ter", "highway": "hwy",
         "parkway": "pkwy", "north": "n", "south": "s", "east": "e", "west": "w"}


def normalize_address(text):
    """'109 Old Point Road' -> '109 old point rd' (for matching inventory addresses)."""
    s = re.sub(r"[^a-z0-9 ]", " ", str(text or "").lower())
    toks = [_ABBR.get(w, w) for w in s.split()]
    return " ".join(toks)


def match_inventory_address(attrs, address):
    """Buildings whose inventory address matches the typed address (house number
    and street). Returns a DataFrame (possibly empty) with distance_m = 0."""
    if attrs is None or "address" not in attrs.columns:
        return pd.DataFrame()
    q = normalize_address(address)
    if not q:
        return pd.DataFrame()
    q_num = q.split()[0] if q.split() and q.split()[0].isdigit() else None
    if q_num is None:
        return pd.DataFrame()
    q_street = " ".join(q.split()[1:])
    inv = attrs["address"].fillna("").astype(str).map(normalize_address)
    hits = []
    for idx, a in inv.items():
        if not a:
            continue
        toks = a.split()
        if toks[0] != q_num:
            continue
        street = " ".join(toks[1:])
        # the typed street may carry the town after it; accept a prefix match
        if street and (q_street.startswith(street) or street.startswith(q_street)):
            hits.append(idx)
    out = attrs.loc[hits].copy()
    out["distance_m"] = 0.0
    return out


def distance_m(lat1, lon1, lat2, lon2):
    """Great-circle distance in metres (vectorised on lat2/lon2)."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dl = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2) ** 2 + math.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(a))


def nearest_buildings(attrs, lat, lon, within_m, limit=3):
    """Buildings within `within_m` of a point, nearest first.

    attrs: the location's bldg_attrs frame (id, latitude, longitude, ...).
    Returns a DataFrame with a `distance_m` column, possibly empty.
    """
    if attrs is None or len(attrs) == 0:
        return pd.DataFrame()
    lat2 = pd.to_numeric(attrs["latitude"], errors="coerce").values.astype(float)
    lon2 = pd.to_numeric(attrs["longitude"], errors="coerce").values.astype(float)
    d = distance_m(lat, lon, lat2, lon2)
    out = attrs.copy()
    out["distance_m"] = d
    out = out[np.isfinite(d) & (d <= within_m)].sort_values("distance_m").head(limit)
    return out


def fema_flood_zone(lat, lon):
    try:
        return _fema_flood_zone(round(float(lat), 6), round(float(lon), 6))
    except _Unreachable:
        return None


@st.cache_data(show_spinner=False, ttl=7 * 24 * 3600)
def _fema_flood_zone(lat, lon):
    """FEMA NFHL flood zone at a point: dict(zone, subtype, static_bfe, sfha) or None."""
    try:
        r = requests.get(
            FEMA_NFHL_URL,
            params={
                "geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint", "inSR": "4326",
                "spatialRel": "esriSpatialRelIntersects",
                "outFields": "FLD_ZONE,ZONE_SUBTY,STATIC_BFE,SFHA_TF", "returnGeometry": "false", "f": "json",
            },
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        feats = r.json().get("features") or []
    except Exception as exc:
        raise _Unreachable(str(exc))
    if not feats:
        return {"zone": "Not in a mapped FEMA flood zone", "subtype": None, "static_bfe": None, "sfha": False}
    a = feats[0].get("attributes") or {}
    bfe = a.get("STATIC_BFE")
    try:
        bfe = float(bfe) if bfe is not None and float(bfe) > -9000 else None
    except (TypeError, ValueError):
        bfe = None
    return {"zone": a.get("FLD_ZONE"), "subtype": a.get("ZONE_SUBTY"), "static_bfe": bfe,
            "sfha": str(a.get("SFHA_TF", "")).upper() == "T"}
