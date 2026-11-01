# ADAPT

Assessment of Damage and Adaptation Planning Tool. Center for Climate Systems Research,
The Climate School, Columbia University.

ADAPT estimates flood damage to every building in a coastal community under
sea-level rise, and compares what each adaptation measure would save. The app
has three modules behind a landing page:

| Module | For | Pages |
|---|---|---|
| Households and Businesses | residents, businesses, property owners | My home (address lookup and home report with PDF), Neighborhood maps (hexagon summaries, no single home shown) |
| Community Planning | planners, agencies, community leaders | Community overview, Dynamic maps (damage bins, adaptation effectiveness, flood occurrences, flood map, road map), Building-level reports, Damage distributions |
| Developer | the project team | Data and settings, NSI survey tool, Fragility curves, Run the model |

The sidebar also holds **Ask ADAPT**, a chat assistant that answers questions about the
tool, the methods and the selected community. It needs a free API key in the secrets
(`[assistant]`, Google AI Studio recommended); without one the panel is hidden. What it
knows is `docs/assistant_knowledge.md`, this README, and the settings and summary
numbers of the selected community.

Address lookup (My home) geocodes the typed address with OpenStreetMap Nominatim, then
the US Census Bureau geocoder, and snaps it to the nearest inventory building within
40 m; the person confirms the building on an aerial photo. No key is needed. The home
report uses the median (P50) as the expected damage and P05 to P95 as the range, and
adds the modeled sea-level rise to present-day flood levels set per community in
Data and settings (`flood_levels`).

Access: locations flagged `protected` in `data/locations.json` (Shinnecock and Pamunkey by
default) show their results only after the access password, on every module. The
password is `[access] password` in Streamlit secrets, falling back to the project's
long-standing password. The Developer module is open unless `[developer] password` is set.

The damage and adaptation model runs inside the app (`adapt_core/engine.py`),
a validated port of the MATLAB pipeline `AAA___Main_v14.m`. MATLAB is no
longer needed.

## Layout

```
app.py                     entry point: navigation, sidebar, page runner
adapt_core/
  config.py                paths, constants, location defaults, modules, palette
  engine.py                damage and adaptation engine (port of AAA___Main_v14.m)
  bundle.py                result-bundle discovery and loading
  analysis.py              map data preparation and aggregation
  terrain.py               USGS 3DEP terrain, bathtub flooding, basemaps, OSM roads
  mapexport.py             publication-quality map export
  charts.py                shared Plotly builders
  fragility.py             FEMA/Hazus curve explorer
  ui.py                    theme, page header, cards, developer gate, location registry
  github_sync.py           publish files to the GitHub repo
  geocode.py               address geocoding (Nominatim, US Census), FEMA flood zone lookup
  home_report.py           the home report numbers and PDF
  assistant.py             the Ask ADAPT chat panel (Gemini, Groq, OpenRouter, OpenAI-compatible)
views/
  home.py                  landing page
  community/               my_home.py, neighborhood.py
  planners/                overview.py, dynamic_maps.py (+ _terrain_views.py for the flood and
                           road views), my_building.py, building_depth.py, distributions.py
  developer/               data.py, nsi_survey.py, curves.py, run_model.py
docs/assistant_knowledge.md  what the assistant knows; edit when methods or pages change
data/
  _curves/                 FEMA/Hazus FAST tables (shared)
  <Location>/              one folder per location (inputs + results, see below)
  locations.json           per-location settings edited in the Developer module
assets/                    logo.png, nsi_tool.html (field survey tool)
tests/                     test_engine_vs_matlab.py
```

### Per-location folder `data/<Slug>/`

Inputs

- `DDD___<Slug>___NSI.xlsx` building inventory (id, occupancy_type, number_of_stories,
  foundation_type, foundation_height, ground_elevation, structure_value, content_value,
  longitude, latitude, ...)
- `DDD___<Slug>_MC_annual_max_waterlevels_P50.csv` and `_P90.csv`: `Year`, `MC_0001` ... `MC_1000`
- `<Slug>_osm_roads.json` (optional) saved OpenStreetMap roads for the Road map view

Results (written by the engine)

- `<Slug>_metadata.csv`, `<Slug>_bldg_lookup.csv`, `<Slug>_bldg_CumulativeDamage.csv`,
  `<Slug>_CumulativeDamage_categories.csv`, `<Slug>_skipped_buildings.csv`,
  `<Slug>_occupancy_fallback.csv`, `<Slug>_missing_values.csv`

The legacy flat layout (all files next to `app.py`) is still read.

## Run locally

```
pip install -r requirements.txt
streamlit run app.py
```

Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` to change the
access password, gate the Developer module, or add a GitHub token so the Developer
module can publish results. On Streamlit Community Cloud paste the same contents into the
app's Secrets box.

## Running the model

Developer → Run the model. Inputs are read from `data/<Slug>/`, settings from
`data/locations.json`, curves from `data/_curves/`. The run writes the result
bundle, offers it as a zip, and can commit it to GitHub; Streamlit Cloud then
redeploys with the new numbers. Mastic Beach (6,362 buildings) takes about
80 s on a laptop and uses about 300 MB of memory.

Buildings without a structure or content value are kept in the inventory with
no damage estimate (as MATLAB did) but are excluded from community totals, so
the category totals no longer carry NaN.

## Validation

`python tests/test_engine_vs_matlab.py Pamunkey Shinnecock` compares the engine
with the MATLAB bundles: for every building with identical inputs, all 23
percentiles of every action match to machine precision.
