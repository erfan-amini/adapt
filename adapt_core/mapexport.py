"""Publication-quality map export (ported)."""
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

# ============================================================================
# SCIENTIFIC MAP EXPORT HELPERS
# ============================================================================
# These build a publication-quality version of the live map for download.
# The live map prioritizes interactivity (clicks, hover, no chrome). The
# export adds: a title block, a credits footer, a north arrow, and a
# geographically-correct scale bar (drawn in lon/lat coordinates so it
# stays accurate at the rendered zoom). PNG/PDF/SVG output goes through
# kaleido via Plotly's `fig.to_image`.

# Tile attributions for OSM / Carto basemaps. We display the appropriate
# string in the credits footer to comply with OpenStreetMap's attribution
# requirement (https://www.openstreetmap.org/copyright).
_BASEMAP_ATTRIB = {
    "open-street-map":  "© OpenStreetMap contributors",
    "Streets":          "© OpenStreetMap contributors",  # alias for the live-map radio
    "carto-positron":   "© OpenStreetMap contributors • © CARTO",
    "carto-darkmatter": "© OpenStreetMap contributors • © CARTO",
    "stamen-terrain":   "© OpenStreetMap contributors • Stamen Design",
    "stamen-toner":     "© OpenStreetMap contributors • Stamen Design",
    "Aerial":           "Tiles © Esri, Maxar, Earthstar Geographics",
    "white-bg":         "",
}


# ESRI World Imagery raster tile source - overlaid on a white-bg base
# to render aerial photography without needing a Mapbox access token.
# (Plotly's native "satellite" / "satellite-streets" styles do require
# a token; ESRI's public tiles do not, which keeps the app working out
# of the box for anyone running it locally.) An internet connection is
# required at render time; if the tile server is unreachable the map
# will simply show a white background and the building dots.
_ESRI_WORLD_IMAGERY_LAYER = {
    "below": "traces",
    "sourcetype": "raster",
    "sourceattribution": "Tiles © Esri",
    "source": [
        "https://server.arcgisonline.com/ArcGIS/rest/services/"
        "World_Imagery/MapServer/tile/{z}/{y}/{x}"
    ],
}


def _basemap_config(name):
    """Translate a user-facing basemap label to the (style, layers) pair
    that plotly's `mapbox` layout sub-dict expects.

    "Streets" → OpenStreetMap, no extra layers.
    "Aerial"  → white background with the ESRI World Imagery raster
                layer drawn beneath the data traces.
    Anything else is passed through as a literal plotly mapbox style.
    """
    if name == "Aerial":
        return "white-bg", [_ESRI_WORLD_IMAGERY_LAYER]
    if name == "Streets":
        return "open-street-map", []
    return name, []


def _nice_scalebar_meters(target_m):
    """Snap a desired scale-bar length (in meters) to a 1/2/5 × 10ⁿ value
    so the bar reads as a round number on the map. e.g. 1234 → 1000."""
    if target_m <= 0:
        return 100
    import math
    exp = math.floor(math.log10(target_m))
    base = 10 ** exp
    for mult in (1, 2, 5, 10):
        if mult * base >= target_m:
            return int(mult * base)
    return int(10 * base)


def build_publication_map_figure(fig_map, *, location, occupancy, target_year,
                                 scenario_label, map_view, df_map,
                                 width_px=2400, height_px=1800,
                                 mapbox_style="open-street-map",
                                 center_lat_override=None,
                                 center_lon_override=None,
                                 zoom_override=None):
    """Return a deep-copied version of ``fig_map`` styled for publication:
    embedded title, credits footer, north arrow, and a geographically-correct
    scale bar. The original interactive figure is left untouched.
    
    Parameters
    ----------
    fig_map : plotly.graph_objects.Figure
        The live map figure as built in the Map tab.
    location, occupancy, target_year, scenario_label, map_view : str
        Pieces used to build the title and subtitle.
    df_map : pandas.DataFrame
        The per-building map dataframe - used to recompute the bbox-based
        center and zoom independently of the live figure (so the export
        always frames the data the same way regardless of user pan/zoom).
    width_px, height_px : int
        Pixel dimensions of the exported image. Defaults are sized for an
        8 × 6 inch figure at 300 DPI.
    mapbox_style : str
        Plotly mapbox style. ``open-street-map`` is the default and ships
        OSM tiles for free; ``carto-positron`` and ``carto-darkmatter`` are
        also free. Other styles may require a Mapbox token.
    center_lat_override, center_lon_override, zoom_override : float | None
        If any of these is provided, override the bbox-fit framing. All
        three are optional and independent - if only some are passed,
        the rest still come from bbox-fit. This is the WYSIWYG knob: the
        export panel exposes these so users can match what they're looking
        at on the live map.
    """
    import copy
    import math
    from datetime import date as _date
    
    fig = copy.deepcopy(fig_map)
    
    # ----- Frame the data: bbox-fit center + zoom (don't trust live map) -----
    lats = df_map['latitude'].dropna().to_numpy()
    lons = df_map['longitude'].dropna().to_numpy()
    if len(lats) >= 2:
        lat_min, lat_max = float(np.nanmin(lats)), float(np.nanmax(lats))
        lon_min, lon_max = float(np.nanmin(lons)), float(np.nanmax(lons))
        center_lat = 0.5 * (lat_min + lat_max)
        center_lon = 0.5 * (lon_min + lon_max)
        # Padding so points don't kiss the frame
        lat_span = max((lat_max - lat_min) * 1.25, 1e-3)
        lon_span = max((lon_max - lon_min) * 1.25, 1e-3)
        # Web-Mercator zoom that just fits the longer span at this latitude.
        # For a viewport of width_px pixels: 360 / 2^zoom = visible_lon_span
        # (at the equator). Latitude span at zoom z covers
        # 360·cos(lat)/(2^z) × (height/width). We pick the more constraining.
        zoom_lon = math.log2(360.0 / lon_span)
        # Latitude span (from a horizontal-equivalent perspective): convert
        # lat span back to "equator-equivalent" longitude span via cos(lat)
        cos_lat = max(math.cos(math.radians(center_lat)), 1e-6)
        zoom_lat = math.log2(
            (360.0 / (lat_span * (width_px / height_px))) * cos_lat
        )
        zoom = float(min(zoom_lon, zoom_lat) - 0.4)  # small headroom
        zoom = max(min(zoom, 17.0), 4.0)
    else:
        center_lat = float(np.nanmean(lats)) if len(lats) else 40.86
        center_lon = float(np.nanmean(lons)) if len(lons) else -72.49
        zoom = 12.0
    
    # User-supplied overrides win when present (WYSIWYG path).
    if center_lat_override is not None:
        center_lat = float(center_lat_override)
    if center_lon_override is not None:
        center_lon = float(center_lon_override)
    if zoom_override is not None:
        zoom = float(zoom_override)

    # ----- Build the scale bar (paper-coord shapes anchored to the map area) -----
    # We previously drew the bar as a mapbox layer (geographic LineString),
    # but those only render reliably when an active basemap is loaded. To
    # guarantee the bar is visible across all basemap choices (including the
    # white-bg fallback), we render it as paper-coord shapes and compute the
    # geographic length that the bar represents at the chosen zoom.
    import math as _math
    EARTH_CIRC = 40_075_016.686  # meters
    cos_lat = max(_math.cos(_math.radians(center_lat)), 1e-6)
    # Width of the actual map drawing area (width minus left/right margins).
    # The annotations live in paper coords (0..1) over the FULL figure, so
    # to compute pixel widths we scale by full width.
    map_inner_w = max(width_px - 80, width_px * 0.5)  # margins l=40, r=40
    # m/px in the rendered figure at this zoom
    m_per_px = EARTH_CIRC * cos_lat / (256 * (2 ** zoom))
    visible_m_inner = m_per_px * map_inner_w
    # Pick a "nice" scale-bar length that occupies ~18% of the inner width
    target_m = visible_m_inner * 0.18
    bar_m = _nice_scalebar_meters(target_m)
    if bar_m >= 1000:
        scale_label = f"{bar_m/1000:g} km"
    else:
        scale_label = f"{bar_m} m"

    # Bar's pixel length and paper-coord left edge (5% in from left margin)
    bar_px = bar_m / m_per_px
    bar_paper_w = bar_px / width_px
    sb_x0_paper = 0.05  # 5% in from the left of the figure
    sb_x1_paper = sb_x0_paper + bar_paper_w
    sb_y_paper = 0.06   # 6% up from the bottom (above the credits footer)

    # Tick whisker height - fixed pixels, converted to paper-y units
    tick_h_paper_y = 8.0 / height_px

    # Halo rectangle (light bg behind the bar so it pops on busy basemaps)
    pad_x = bar_paper_w * 0.06
    pad_y = tick_h_paper_y * 3.5
    layout_shapes = [
        # White halo
        dict(type="rect", xref="paper", yref="paper",
             x0=sb_x0_paper - pad_x, x1=sb_x1_paper + pad_x,
             y0=sb_y_paper - pad_y, y1=sb_y_paper + pad_y,
             fillcolor="rgba(255,255,255,0.85)",
             line=dict(color="rgba(0,0,0,0.15)", width=0.5),
             layer="above"),
        # Main horizontal bar
        dict(type="line", xref="paper", yref="paper",
             x0=sb_x0_paper, x1=sb_x1_paper,
             y0=sb_y_paper, y1=sb_y_paper,
             line=dict(color="black", width=4),
             layer="above"),
        # Left tick whisker
        dict(type="line", xref="paper", yref="paper",
             x0=sb_x0_paper, x1=sb_x0_paper,
             y0=sb_y_paper - tick_h_paper_y, y1=sb_y_paper + tick_h_paper_y,
             line=dict(color="black", width=4),
             layer="above"),
        # Right tick whisker
        dict(type="line", xref="paper", yref="paper",
             x0=sb_x1_paper, x1=sb_x1_paper,
             y0=sb_y_paper - tick_h_paper_y, y1=sb_y_paper + tick_h_paper_y,
             line=dict(color="black", width=4),
             layer="above"),
    ]
    # Mapbox-layer scale bar removed - keeping mapbox.layers empty so the
    # bar renders identically regardless of basemap choice.
    mapbox_layers = []

    # Translate the "Aerial" pseudo-style into its real implementation
    # (white-bg background + ESRI World Imagery raster tile layer).
    # Doing this here means the rest of the export pipeline can keep
    # treating mapbox_style as a flat string. The export-time tile fetch
    # uses the same ESRI endpoint as the live map, so if the tiles fail
    # to load the export falls back to a plain white background and the
    # data markers stay readable.
    # NB: we remember the user-facing name in `attrib_key` BEFORE
    # overwriting `mapbox_style`, so the attribution lookup below still
    # resolves to the ESRI credit (white-bg by itself has no attribution
    # and would otherwise drop the basemap line from the export footer).
    attrib_key = mapbox_style
    if mapbox_style == "Aerial":
        mapbox_style = "white-bg"
        mapbox_layers = [_ESRI_WORLD_IMAGERY_LAYER]

    # ----- Title and footer in paper coordinates -----
    title_main = f"Building-level Flood Risk - {location}"
    if occupancy and occupancy != "All":
        title_main += f" ({occupancy})"
    title_sub = (f"Year {target_year}  •  {scenario_label}  •  "
                 f"{map_view}")
    today = _date.today().strftime("%B %Y")

    attrib = _BASEMAP_ATTRIB.get(attrib_key, "© OpenStreetMap contributors")
    # Skip the "Basemap:" segment when there's no tile attribution to show
    # (e.g. ``white-bg``), which avoids a dangling "Basemap:  •" in the footer.
    footer_parts = [
        "Data: National Structure Inventory  •  NACCS (USACE, 2015)  •  "
        "NYS Climate Impacts Assessment (2024)",
    ]
    if attrib:
        footer_parts.append(f"Basemap: {attrib}")
    footer_parts.append(f"Generated {today}")
    footer_lines = [
        ("ADAPT - Assessment of Damage and Adaptation Planning Tool   |   "
         "Center for Climate Systems Research, The Climate School, Columbia University"),
        "  •  ".join(footer_parts),
    ]

    annotations = [
        # Main title
        dict(text=f"<b>{title_main}</b>",
             xref="paper", yref="paper",
             x=0.5, y=1.03, xanchor="center", yanchor="bottom",
             showarrow=False,
             font=dict(family="Arial", size=24, color="#0f172a")),
        # Subtitle
        dict(text=title_sub,
             xref="paper", yref="paper",
             x=0.5, y=1.005, xanchor="center", yanchor="bottom",
             showarrow=False,
             font=dict(family="Arial", size=16, color="#475569")),
        # Footer line 1
        dict(text=footer_lines[0],
             xref="paper", yref="paper",
             x=0.5, y=-0.025, xanchor="center", yanchor="top",
             showarrow=False,
             font=dict(family="Arial", size=12, color="#334155")),
        # Footer line 2
        dict(text=footer_lines[1],
             xref="paper", yref="paper",
             x=0.5, y=-0.062, xanchor="center", yanchor="top",
             showarrow=False,
             font=dict(family="Arial", size=11, color="#64748b")),
        # Scale-bar label - centered above the bar at its midpoint
        dict(text=f"<b>{scale_label}</b>",
             xref="paper", yref="paper",
             x=(sb_x0_paper + sb_x1_paper) / 2,
             y=sb_y_paper + tick_h_paper_y * 4,
             xanchor="center", yanchor="bottom",
             showarrow=False,
             font=dict(family="Arial", size=14, color="#0f172a")),
        # North arrow - paper-coord glyph in the upper-right corner
        dict(text="<b>N</b><br>▲",
             xref="paper", yref="paper",
             x=0.96, y=0.96, xanchor="center", yanchor="top",
             showarrow=False,
             align="center",
             bgcolor="rgba(255,255,255,0.9)",
             bordercolor="rgba(0,0,0,0.3)", borderwidth=1, borderpad=6,
             font=dict(family="Arial Black", size=18, color="#0f172a")),
    ]

    # Existing legend - move it to a less-cluttered corner if possible,
    # and tighten its background so it reads well in print.
    legend_cfg = dict(
        yanchor="bottom", y=0.05, xanchor="right", x=0.98,
        bgcolor="rgba(255,255,255,0.92)",
        bordercolor="rgba(0,0,0,0.25)", borderwidth=1,
        font=dict(family="Arial", size=12, color="#0f172a"),
    )

    # Apply layout updates. Margins make room for title/footer.
    fig.update_layout(
        mapbox=dict(
            style=mapbox_style,
            center=dict(lat=center_lat, lon=center_lon),
            zoom=zoom,
            layers=mapbox_layers,
        ),
        margin=dict(l=40, r=40, t=80, b=140),
        width=width_px, height=height_px,
        paper_bgcolor="white",
        showlegend=True,
        legend=legend_cfg,
        annotations=annotations,
        shapes=layout_shapes,
        # Static images get a watermark-free, hover-free rendering
        hovermode=False,
    )
    return fig


def export_map_image(pub_fig, fmt="png", scale=2, allow_basemap_fallback=True):
    """Render a publication map figure to bytes via kaleido.

    Returns ``(bytes, mime_type, file_extension, info)``, where ``info`` is
    ``None`` on success, an error string on hard failure, or a short note
    when a basemap fallback was used (e.g. ``"basemap_fallback"``).

    Tile-based basemaps (open-street-map, carto-*) require live HTTP fetches
    during render; some tile providers refuse automated requests and return
    HTTP 403, which kaleido surfaces as ``"Map error."``. When
    ``allow_basemap_fallback`` is True we transparently retry with
    ``style="white-bg"`` so the user still gets a usable export, with the
    points/scale-bar/credits intact on a clean white canvas.
    """
    mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "webp": "image/webp", "svg": "image/svg+xml",
            "pdf": "application/pdf"}.get(fmt.lower(), "application/octet-stream")
    try:
        img_bytes = pub_fig.to_image(format=fmt, scale=scale)
        return img_bytes, mime, fmt.lower(), None
    except Exception as e:
        msg = str(e)
        msg_lc = msg.lower()
        # The two common failure modes have very different remedies, so we
        # detect them separately. Tile-render failures surface from kaleido
        # under several strings depending on version:
        #   * "Map error."         (modern Scattermap / map.* layout)
        #   * "Mapbox error."      (legacy Scattermapbox / mapbox.* layout)
        #   * "Error 525: …"       (kaleido v1's wrapper around either)
        is_map_error = (
            "map error" in msg_lc or
            "mapbox error" in msg_lc or
            "525" in msg
        )
        is_kaleido_missing = (
            ("kaleido" in msg_lc and "map" not in msg_lc) or
            "chrom" in msg_lc or
            isinstance(e, ImportError)
        )
        if is_map_error and allow_basemap_fallback:
            # Retry with white-bg so the user gets a usable export
            try:
                import copy
                fallback = copy.deepcopy(pub_fig)
                # Update both possible style keys (mapbox.* legacy and map.* new)
                if hasattr(fallback.layout, 'mapbox') and fallback.layout.mapbox:
                    fallback.update_layout(mapbox=dict(style="white-bg"))
                if hasattr(fallback.layout, 'map') and fallback.layout.map:
                    fallback.update_layout(map=dict(style="white-bg"))
                img_bytes = fallback.to_image(format=fmt, scale=scale)
                return img_bytes, mime, fmt.lower(), "basemap_fallback"
            except Exception as e2:
                return None, None, None, (
                    f"Export failed even after basemap fallback: {e2}"
                )
        if is_kaleido_missing:
            return None, None, None, (
                "Static-image export requires `kaleido` (>=1.0) and a Chrome "
                "browser. Install with `pip install -U kaleido` and run "
                "`plotly_get_chrome` once to download Chrome. Detail: " + msg
            )
        return None, None, None, msg


