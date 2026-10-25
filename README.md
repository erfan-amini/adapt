# ADAPT

Assessment of Damage and Adaptation Planning Tool. Center for Climate Systems Research,
The Climate School, Columbia University.

ADAPT estimates flood damage to every building in a coastal community under
sea-level rise, and compares what each adaptation measure would save. The app
has three modules behind a landing page:

| Module | For | Pages |
|---|---|---|
| Community Members | residents, businesses, property owners | Dynamic maps, My building |
| Decision Makers | planners, agencies, community leaders | Community overview, Flood maps, Road maps, Damage distributions |
| Developer (password) | the project team | Data and settings, NSI survey tool, Fragility curves, Run the model |

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
views/
  home.py                  landing page
  community/               dynamic_maps.py, my_building.py, building_depth.py
  planners/                overview.py, flood_maps.py, road_maps.py, distributions.py
  developer/               data.py, nsi_survey.py, curves.py, run_model.py
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
- `<Slug>_osm_roads.json` (optional) saved OpenStreetMap roads for the Road maps page

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

Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and set the
developer password and, optionally, a GitHub token so the Developer module can
publish results. On Streamlit Community Cloud paste the same contents into the
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
