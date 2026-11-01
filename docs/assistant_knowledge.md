# ADAPT knowledge pack

This file is what the in-app assistant reads. Keep it factual and current.
Edit it when the methods or pages change.

## What ADAPT is

ADAPT (Assessment of Damage and Adaptation Planning Tool) is a decision-support
web app from the Center for Climate Systems Research, The Climate School,
Columbia University. It was co-developed with tribal nations and towns in New
York and Virginia under a NOAA-funded project (PI Malgosia Madajewicz; tool and
model by Erfan Amini, ea3246@columbia.edu). It estimates flood damage to every
building in a coastal community under sea-level rise and compares what each
adaptation measure would save, for households, businesses and community
planners. Live app: https://adapt2026.streamlit.app. Code:
https://github.com/erfan-amini/adapt.

Communities with results: Mastic Beach (NY, open to everyone), Shinnecock
Nation (NY, password protected), Pamunkey Indian Reservation (VA, password
protected). Protected results are shown only after the access password is
entered; the assistant never reveals them otherwise.

## Modules and pages

Landing page: pick a community, then open a module. Clicking the ADAPT logo
and name at the top of the sidebar returns to the landing page.

### Households and Businesses (for residents, businesses, property owners)
- My home: type a street address; the app geocodes it (OpenStreetMap
  Nominatim first, US Census Bureau geocoder as fallback, both free, no
  account), snaps it to the nearest building in the project inventory
  (within 40 m; nearest candidates within 60 m are offered), and asks the
  person to confirm the building on an aerial photo. Then it shows the home
  report: house information, expected future flood depth above the ground,
  and cumulative damage by action for 2040 and 2055 (or the available
  horizons), with a PDF download. One home at a time; the address is not
  stored.
- Neighborhood maps: flood risk summarized by hexagon (about 160 m across by
  default, adjustable). Hexagons with fewer than 2 buildings are hidden so
  no single home can be read off the map; the number of buildings in a hexagon is not shown. Measures: share of homes with
  expected damage, expected damage per home, where a low-cost fix is enough,
  where elevation is needed, flood frequency, share of homes below the DFE.

### Community Planning (for planners, agencies, community leaders)
- Community overview: three tabs. Summary (community-wide cumulative damage by strategy, under and above the DFE, trajectory over time, breakdowns, structure values), Trends (no-action damage across horizons for both sea-level rise scenarios with the P05 to P95 band, and every strategy per scenario), Damage distributions (how damage is distributed across buildings and how many buildings each measure protects).
- Dynamic maps: building-level maps with five views. Damage bins (P90
  damage with no action in fixed bins), Adaptation effectiveness (cheapest
  measure that works for each building), Flood occurrences (how many years
  the first floor floods through the horizon), Flood map (bathtub flooding
  on USGS 3DEP terrain for a chosen present-day flood level, high tide,
  monthly, annual, 10% or 1% annual chance, plus the modeled sea-level rise
  for the selected year and scenario; buildings colored dry, water on the
  lot, or first floor flooded), and Road map (OpenStreetMap roads flooded,
  dry but cut off, or dry and reachable at that level). Advanced options
  hold the basemap (Streets or Aerial), point size, a black ring around
  non-residential buildings, buildings with $0 damage, the effectiveness
  rule (statistic and cutoff), the elevation cutoff, the present-day flood
  levels, terrain detail, a sea-level-rise override, road options, the DFE
  status filter, and find-by-ID.
- Building-specific reports: one building by NSI id: attributes, annual flood exposure,
  flood depth by year, retrofit figures, damage trajectory under both
  sea-level-rise scenarios with a side-by-side table, and flood depth tables
  for flood levels the user enters.

### Developer (project team)
- Data and settings: per-community settings (BFE, elevation shortfall rule,
  mobile-homes flag, password protection, present-day flood levels for the
  home report, description), inventory upload or Google Sheet pull with
  column mapping, water-level upload, curve tables.
- NSI survey tool: the field survey tool for checking building attributes
  (Google Street View based).
- Fragility curves: the FEMA/Hazus depth-damage curves by occupancy.
- Run the model: runs the damage and adaptation model for a community, shows
  QA, missing values, saves the result bundle, zips it, and can publish it
  to GitHub so the live app redeploys.

## The model, in plain terms

- Buildings come from the National Structure Inventory (NSI), checked and
  corrected by the project team with street-view and field surveys:
  occupancy type (for example RES1 single-family, RES2 manufactured home,
  RES3 multi-family, COM commercial), number of stories, basement or not,
  foundation type and height, ground elevation, first-floor elevation
  (FFE = ground elevation + foundation height), structure value and content
  value (NSI depreciated replacement cost).
- Water levels: 1,000 Monte Carlo simulations of the annual maximum water
  level for every year from 2025 to 2100, under two sea-level-rise
  scenarios: median (50th percentile) and high (90th percentile) of the
  official projections (New York State projections for the New York sites).
  They include tides, storm surge and sea-level rise.
- Damage: FEMA Hazus depth-damage functions (FAST tables) give the share of
  structure and content value lost for a given water depth above the first
  floor (curve points from -4 to +24 ft). Damage per year = share x value.
  Cumulative damage through a horizon year (for example 2040) = the sum of
  yearly damages from 2025 through that year, for each of the 1,000
  simulated futures. Reported statistics are percentiles across those
  futures: the median (P50) is the typical outcome, P90 is a bad outcome
  exceeded in only 10% of futures, P05 to P95 is the range used in reports.
- Actions compared:
  - No mitigation: do nothing.
  - Raise Utilities: move utilities (furnace, water heater, electrical) above
    the DFE; removes up to $20,000 of damage per flood year.
  - WFP B (wet-floodproof the basement): basement contents and finishes are
    flood-tolerant; removes the damage that occurs at or below first-floor
    level. Only for buildings with a basement.
  - Elevate: raise the house so its first floor is at the DFE (BFE + 2 ft).
    Offered only when the first floor is below DFE minus the community's
    elevation shortfall (0 ft at Mastic Beach and Pamunkey, 1 ft at
    Shinnecock).
  - WFP 1st (wet-floodproof the first floor): for multi-story buildings,
    living space moves upstairs and the first floor is made flood-tolerant
    (removes damage up to one story height, 10 ft). For one-story homes this
    is scored as "add a story" where the building type allows it; otherwise
    it is not applicable.
  - Manufactured homes (RES2) get only Elevate (and the baseline); RES2 and
    RES4 get no wet-floodproofing. In mobile-home-dominated areas (Pamunkey)
    the pages show only raising homes.
- Elevation terms: BFE = FEMA Base Flood Elevation, the 1% annual chance
  flood level on FEMA maps (Mastic Beach 8 ft, Shinnecock 9 ft, Pamunkey
  7 ft NAVD88 in this project). DFE = Design Flood Elevation = BFE + 2 ft,
  the recommended level for the lowest floor. "Under DFE" means the first
  floor is below the DFE. Elevations are in feet above NAVD88 (about mean
  sea level).
- Adaptation effectiveness map rule: each building is colored by the
  cheapest measure that brings the chosen statistic (P90 by default, or the
  median) of its cumulative damage through the selected year to or below
  the chosen cutoff ($15k by default; other choices $0, $1k, $5k, $10k,
  $20k, 1/2/5/10% of structure value, or "removes 90% of the damage").
  Order: No damage, Raise Utilities, WFP Basement, Elevation, Residual
  damage (under DFE, first floor below the elevation cutoff, and even
  elevation misses the rule). Residual damage means the conversation moves
  to buyout, relocation or community-scale measures. Above-DFE buildings and
  Under-DFE buildings at or above the elevation cutoff that no cheap
  retrofit clears are not drawn.
- Home report statistics: "expected" damage is the median of the 1,000
  futures; the range is the 5th to 95th percentile; "avoids X%" compares the
  median with the median under no action. Flood depths in the report add
  the modeled sea-level rise (change in the median annual-maximum water
  level since 2025) to present-day flood levels stored per community
  (Mastic Beach: monthly flood 3.7 ft, 10% annual chance 5.7 ft, 1% annual
  chance 8.5 ft NAVD88). Sea-level rise at Mastic Beach under the median
  scenario is about 8 inches by 2040 and 16 inches by 2055; under the high
  scenario about 10 and 22 inches.
- Indicative retrofit costs used in the pages (from the project workshop
  table): Raise Utilities $5,000 to $40,000; wet-floodproof basement $8,500
  to $30,000; wet-floodproof first floor $10,000 to $100,000 or more;
  elevation $50 to $120 or more per square foot.
- Limitations: damages are planning estimates, not appraisals; building
  attributes may be wrong (the project welcomes corrections); the model does
  not include erosion, groundwater, rainfall flooding or wave action; costs
  of measures are indicative; flood insurance premiums are not modeled.

## Data files

Each community has a folder data/<Slug>/ with the inputs (NSI inventory
xlsx, Monte Carlo water levels for P50 and P90) and the results written by
the model (metadata, building lookup, per-building cumulative damage
percentiles by year, action and scenario, category counts, skipped
buildings). data/_curves/ holds the FEMA/Hazus curve tables.
data/locations.json holds per-community settings edited in the Developer
module. Model: adapt_core/engine.py, a validated port of the MATLAB script
AAA___Main_v14.m (all 23 percentiles of every action match to machine
precision).

## Common questions

- Why is my home green on the map but my report shows damage in the worse
  futures? The map rule uses one statistic and cutoff; the report shows the
  full range (P05 to P95). Hover a building to see both the median and P90.
- Why is elevation not listed for my home? Its first floor is already at or
  above the DFE (minus the community's shortfall), so elevation is not
  advised.
- Why is wet-floodproofing the basement missing? The building has no
  basement, or it is a manufactured home.
- What does "times flooded" mean? The number of years, from 2025 through the
  horizon, in which the annual maximum water level reaches the first floor,
  averaged over the 1,000 futures.
- Who do I contact? Erfan Amini, ea3246@columbia.edu, for corrections to a
  building or questions about the numbers.
