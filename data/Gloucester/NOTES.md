# Gloucester County, VA: model inputs

Built by `tools/make_gloucester_inputs.py` from the files Molly Mitchell
(VIMS) sent in October and November 2026 (`source/`). Re-run the script
after changing a source file or a setting at the top of it.

## Water levels (`DDD___Gloucester_MC_annual_max_waterlevels_P50/P90.csv`)

- Recurrence curve: NACCS save point 17719 (37.3265 N, 76.3947 W), mean
  water level for return periods 1 to 10,000 years, metres above MSL,
  converted to NAVD88 with the save point's factor (NAVD88 = MSL - 0.084 m)
  and to feet.
- Sea-level rise: NOAA 2017 scenarios at Sewells Point. P50 file =
  Intermediate-High, P90 file = High, the same pairing as Pamunkey. Rise is
  taken relative to 2020: following Molly's spreadsheet, the NACCS curve is
  treated as the 2020 condition.
- Sampling: 1,000 simulations for every year 2025 to 2100; each annual
  maximum is drawn from the recurrence curve (linear in log return period,
  extrapolated beyond 10,000 years) and shifted by that year's rise. The same
  random draws (seed 20261101) serve both scenarios, so the two files differ
  by sea-level rise only.
- Check: in 2025 the median annual maximum is 3.5 ft NAVD88 (the 2-year
  level), the 90th percentile 5.2 ft (10-year level 5.1 ft).

Note: the two "Recurrance with SLR" sheets in Molly's workbook are labelled
Int-High and High but contain the Intermediate and Int-High increments. They
are not used; the scenarios are computed from the "2017 NOAA SLR" sheet.

## Inventory (`DDD___Gloucester___NSI.xlsx`)

- NSI points for the county with ground elevation at or below 20 ft NAVD88
  (5,129 of 18,151), minus 64 with no occupancy type: 5,065 buildings.
  Buildings above 20 ft cannot be reached by water in any scenario through
  2100 (the highest sampled level is about 24 ft).
- Elevation certificates (567 buildings in this set): ground elevation =
  lowest adjacent grade (LAG), foundation height = certificate finished
  first-floor elevation minus LAG, so the model's first-floor elevation is
  the surveyed one. Year built from the certificate's parcel record where
  present. Street addresses are kept for these buildings only.
- All other attributes are the NSI values (occupancy, stories, foundation
  type and height, area, values, coordinates).

## Settings (adapt_core/config.py)

- BFE 8 ft NAVD88 (median of the certificates' BFEs, which range 7 to 10 ft);
  DFE 10 ft; elevation shortfall 0.
- Present-day flood levels for the home report: MHHW 1.1 ft (NOAA station
  8637689, Yorktown USCG, 1983-2001 epoch), 10% 5.1 ft and 1% 8.8 ft (NACCS
  plus Int-High rise 2020 to 2025). No monthly level yet.
- Protected (password) until the county has seen the results.

## Open items

- Confirm with Molly: NACCS as the 2020 condition (vs. the 1983-2001 MSL
  epoch, about 0.4 ft lower), the scenario pairing, and the datum factor.
- A monthly flood level and a present-day MHHW for Gloucester Point.
- Certificate foundation codes (Crawl, Piers, Full, Slab) are not used; the
  NSI foundation type decides basement eligibility.
