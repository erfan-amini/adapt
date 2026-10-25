"""Paths, location defaults, module definitions and palette for ADAPT."""
import os

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(APP_ROOT, "data")
CURVES_DIR = os.path.join(DATA_DIR, "_curves")
ASSETS_DIR = os.path.join(APP_ROOT, "assets")
LOGO_PATH = os.path.join(ASSETS_DIR, "logo.png")
NSI_TOOL_HTML = os.path.join(ASSETS_DIR, "nsi_tool.html")
LOCATIONS_FILE = os.path.join(DATA_DIR, "locations.json")

# ---------------------------------------------------------------------------
# Model constants (mirror AAA___Main_v14.m)
# ---------------------------------------------------------------------------
ACTION_NAMES = ["No mitigation", "Raise Utilities", "WFP B", "Elevate", "WFP 1st"]
ACTION_CODES = [0, 1, 2, 4, 3]
ACTION_LABELS = {
    "No mitigation": "No mitigation",
    "Raise Utilities": "Elevate utilities",
    "WFP B": "Wet-floodproof basement",
    "Elevate": "Elevate the building",
    "WFP 1st": "Wet-floodproof first floor",
}
SCENARIOS = ["P50", "P90"]
SCENARIO_LABELS = ["50th-percentile", "90th-percentile"]
SCENARIO_PRETTY = {"50th-percentile": "Median SLR (50th pct)", "90th-percentile": "High-End SLR (90th pct)"}
TARGET_YEARS_DEFAULT = [2025, 2040, 2055, 2060, 2100]
PERCENTILES = [*range(1, 11), 25, 50, 75, *range(90, 100)]
FLOOD_ZONE_DEFAULT = "A"
UTIL_REPL_USD_DEFAULT = 20000.0
STORY_H_FT_DEFAULT = 10.0
Y_NOW = 2025

# Per-location defaults shipped with the app. The Developer module can edit
# these and add new locations; edits are stored in data/locations.json.
LOCATION_DEFAULTS = {
    "Shinnecock":  {"name": "Shinnecock", "bfe_ft": 9.0, "elev_min_shortfall_ft": 1.0,
                    "restrict_manuf": False, "mobile_homes_dominated": False,
                    "state": "NY", "blurb": (
                        "The Shinnecock Nation's territory sits on a low peninsula on the south shore of "
                        "Long Island, New York, bounded by Shinnecock Bay. Rising seas, storm surge and "
                        "shoreline erosion expose homes, roads and cultural sites to more frequent flooding.")},
    "MasticBeach": {"name": "Mastic Beach", "bfe_ft": 8.0, "elev_min_shortfall_ft": 0.0,
                    "restrict_manuf": False, "mobile_homes_dominated": False,
                    "state": "NY", "blurb": (
                        "Mastic Beach is a low-lying bayfront community on the south shore of Long Island, "
                        "New York, facing Moriches Bay. Thousands of homes sit within a few feet of the "
                        "tide line, so tidal flooding and storm surge already reach streets and yards.")},
    "Pamunkey":    {"name": "Pamunkey", "bfe_ft": 7.0, "elev_min_shortfall_ft": 0.0,
                    "restrict_manuf": True, "mobile_homes_dominated": True,
                    "state": "VA", "blurb": (
                        "The Pamunkey Indian Reservation sits on a low-lying 1,600-acre peninsula on a bend "
                        "of the Pamunkey River near West Point, Virginia, ringed by river and tidal marsh on "
                        "three sides. It faces tidal and storm flooding, stormwater and land subsidence, with "
                        "roughly 3 to 6 ft of sea-level rise projected by 2100.")},
}

# ---------------------------------------------------------------------------
# Modules (audience-facing sections of the app)
# ---------------------------------------------------------------------------
MODULES = {
    "community": {
        "title": "Community Members",
        "subtitle": "Residents, businesses and property owners",
        "icon": "🏠",
        "accent": "#0ea5e9",
        "blurb": ("See which buildings flood, how often, and what each adaptation option "
                  "would do for a home or business like yours."),
    },
    "planners": {
        "title": "Decision Makers",
        "subtitle": "City planners, agencies and community leaders",
        "icon": "🏛️",
        "accent": "#14b8a6",
        "blurb": ("Community-wide damage, flood and road maps under sea-level rise, and how "
                  "damage is distributed across the building stock."),
    },
    "developer": {
        "title": "Developer",
        "subtitle": "Datasets, curves and the damage model",
        "icon": "🛠️",
        "accent": "#f59e0b",
        "blurb": ("Manage building inventories and settings, review the depth-damage curves, "
                  "and run the damage and adaptation model."),
    },
}

# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------
NAVY = "#0f172a"
NAVY_2 = "#1e293b"
SKY = "#0ea5e9"
SKY_DARK = "#0284c7"
SLATE = "#64748b"
INK = "#0f172a"
PAPER = "#f8fafc"
LINE = "#e2e8f0"
