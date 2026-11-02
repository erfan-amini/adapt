"""Paths, location defaults, module definitions and palette for ADAPT."""
import os

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(APP_ROOT, "data")
CURVES_DIR = os.path.join(DATA_DIR, "_curves")
ASSETS_DIR = os.path.join(APP_ROOT, "assets")
LOGO_PATH = os.path.join(ASSETS_DIR, "logo.png")        # mark only (sidebar)
LOGO_FULL_CANDIDATES = [os.path.join(ASSETS_DIR, "logo_full.png"),
                        os.path.join(APP_ROOT, "logo.png")]   # mark + wordmark (home page)
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
SCENARIO_SHORT = {"50th-percentile": "Median SLR", "90th-percentile": "High-End SLR"}   # sidebar grid
TARGET_YEARS_DEFAULT = [2025, 2040, 2055, 2060, 2100]
PERCENTILES = [*range(1, 11), 25, 50, 75, *range(90, 100)]
FLOOD_ZONE_DEFAULT = "A"
UTIL_REPL_USD_DEFAULT = 20000.0
STORY_H_FT_DEFAULT = 10.0
Y_NOW = 2025

# Per-location defaults shipped with the app. The Developer module can edit
# these and add new locations; edits are stored in data/locations.json.
# "protected": the location's results are shown only after the access password
# (tribal partner data). The password comes from Streamlit secrets
# ([access] password) and falls back to the project's long-standing one.
LOCATION_DEFAULTS = {
    "Shinnecock":  {"name": "Shinnecock", "bfe_ft": 9.0, "elev_min_shortfall_ft": 1.0,
                    "restrict_manuf": False, "mobile_homes_dominated": False, "protected": True,
                    "state": "NY", "blurb": (
                        "The Shinnecock Nation's territory sits on a low peninsula on the south shore of "
                        "Long Island, New York, bounded by Shinnecock Bay. Rising seas, storm surge and "
                        "shoreline erosion expose homes, roads and cultural sites to more frequent flooding.")},
    "MasticBeach": {"name": "Mastic Beach", "bfe_ft": 8.0, "elev_min_shortfall_ft": 0.0,
                    "restrict_manuf": False, "mobile_homes_dominated": False, "protected": False,
                    "state": "NY",
                    # Present-day flood levels (ft NAVD88) used by the home report.
                    "flood_levels": {"monthly_ft": 3.7, "pct10_ft": 5.7, "pct1_ft": 8.5},
                    "blurb": (
                        "Mastic Beach is a low-lying bayfront community on the south shore of Long Island, "
                        "New York, facing Moriches Bay. Thousands of homes sit within a few feet of the "
                        "tide line, so tidal flooding and storm surge already reach streets and yards.")},
    "Pamunkey":    {"name": "Pamunkey", "bfe_ft": 7.0, "elev_min_shortfall_ft": 0.0,
                    "restrict_manuf": True, "mobile_homes_dominated": True, "protected": True,
                    "state": "VA",
                    "flood_levels": {"mhhw_ft": 2.37, "pct10_ft": 5.78, "pct1_ft": 7.38},
                    "blurb": (
                        "The Pamunkey Indian Reservation sits on a low-lying 1,600-acre peninsula on a bend "
                        "of the Pamunkey River near West Point, Virginia, ringed by river and tidal marsh on "
                        "three sides. It faces tidal and storm flooding, stormwater and land subsidence, with "
                        "roughly 3 to 6 ft of sea-level rise projected by 2100.")},
    # Gloucester County, VA. BFE 8 ft = the median BFE on the county's elevation
    # certificates (7 to 10 ft). Open to everyone, like Mastic Beach.
    # Present-day (2025) levels, ft NAVD88: MHHW from NOAA station 8637689
    # (Yorktown USCG, 1983-2001 epoch); 10% and 1% from the NACCS save point
    # 17719 curve plus Int-High sea-level rise 2020 to 2025 (see
    # tools/make_gloucester_inputs.py). No monthly level yet.
    "Gloucester":  {"name": "Gloucester County", "bfe_ft": 8.0, "elev_min_shortfall_ft": 0.0,
                    "restrict_manuf": False, "mobile_homes_dominated": False, "protected": False,
                    "state": "VA",
                    "flood_levels": {"mhhw_ft": 1.1, "pct10_ft": 5.1, "pct1_ft": 8.8},
                    "blurb": (
                        "Gloucester County lies on Virginia's Middle Peninsula between the York River, Mobjack "
                        "Bay and the Chesapeake Bay. Its low-lying necks, from Guinea to Ware Neck, face tidal "
                        "flooding, storm surge and some of the fastest relative sea-level rise on the US East "
                        "Coast.")},
}

# ---------------------------------------------------------------------------
# Home report (Households and Businesses > My home)
# ---------------------------------------------------------------------------
REPORT_CONTACT = "Erfan Amini (ea3246@columbia.edu)"
REPORT_HORIZON_YEARS = (2040, 2055)        # used when the results include them
ADDRESS_SNAP_M = 40.0                       # max distance from the geocoded point to a building
ADDRESS_CANDIDATES_M = 60.0                 # other buildings offered when the nearest is not certain
HEX_MIN_BUILDINGS = 2                       # neighborhood maps: hexagons with fewer buildings are hidden
HEX_DEFAULT_SIZE_M = 160                    # hexagon width (flat to flat), metres

# ---------------------------------------------------------------------------
# Modules (audience-facing sections of the app)
# ---------------------------------------------------------------------------
MODULES = {
    "community": {
        "title": "Households and Businesses",
        "subtitle": "A report for your home, and neighborhood flood-risk maps",
        "icon": "🏠",
        "accent": "#0ea5e9",
        "blurb": ("Enter your address for a flood damage and adaptation report for your home, "
                  "and see how risk varies across neighborhoods."),
    },
    "planners": {
        "title": "Community Planning",
        "subtitle": "Building-level maps, damage, flood and road analysis",
        "icon": "🏛️",
        "accent": "#14b8a6",
        "blurb": ("Community-wide damage, flood and road maps under sea-level rise, and how "
                  "damage is distributed across the building stock."),
    },
    "developer": {
        "title": "Developer",
        "subtitle": "Data, curves and model runs",
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
