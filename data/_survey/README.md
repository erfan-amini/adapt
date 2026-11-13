# Survey support data

Files here feed the NSI survey tool (Developer > NSI survey tool). They are not
model inputs, and the app does not list this folder as a location.

## `<slug>_ground_3dep.csv`

USGS 3DEP 1 m bare-earth ground elevation at each building point, in ft NAVD88.
`<slug>` is the survey location key (for example `hamiltonhowardbeach`). Rows join
to the survey sheet on `uid`. Made with `tools/make_ground_3dep.py`.

When the file exists for the selected location, the survey tool flags each
building's ground elevation against it:

- red flag: the value is one of the placeholders in the CIRF inventory
  (0, 0.33 or 3.82 ft)
- purple flag: the value is more than 4 ft below 3DEP
- yellow flag: the value is 2 to 4 ft below 3DEP

Values within 2 ft of 3DEP, values above it, and blank values get no flag.

### hamiltonhowardbeach_ground_3dep.csv

- Source: 3DEP 1 m DEM tile x59y451, lidar project NY_CMPG_2013 (acquired 2014),
  bilinear at the building point. A spot check against the USGS point query
  service (EPQS) agreed within 0.03 ft.
- Points: the 5,577 rows of `Surveys_hamiltonhowardbeach` as of 2026-11-13.
  Points added later have no cached value; the tool's USGS button still looks
  them up live.
- At these buildings 3DEP ground has a median of 8.6 ft (middle half 7.3 to
  9.6 ft; 5th to 95th percentile 5.2 to 10.8 ft).
