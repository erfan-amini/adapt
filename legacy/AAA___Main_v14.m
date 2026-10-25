% ========================================================================
% Date: 2025-10-12 (Updated: 2026-10 — v14)
% Author: Erfan Amini (CCSR, Columbia University)
%
% v14 changes:
%   - ONE-STORY TEST NOW FOLLOWS THE DAMAGE CURVE. For RES1 the story
%     digit of the FAST curve comes from the occupancy suffix (-1S/-2S/-3S)
%     first and from number_of_stories only when there is no suffix. v13
%     decided "one-story" from number_of_stories alone, so a building whose
%     suffix and story count disagree (e.g. RES1-2SNB with
%     number_of_stories = 1) got the add-a-story conversion on a 2-story
%     curve, and the reverse case fell back to the old transform on a
%     1-story curve. is_one_story_fn now takes the SOID: for RES1 SOIDs
%     (R1..) the story digit decides (R11x = one-story, split level R1S x
%     is not); every other occupancy keeps round(number_of_stories) <= 1.
%   - ADD-A-STORY IS RESIDENTIAL ONLY. The conversion is limited to RES1
%     (single-family) and RES3 (multi-family) SOIDs, i.e. homes
%     (can_add_story_fn). One-story non-residential buildings (shops,
%     warehouses, farm buildings, churches, offices, schools) and one-story
%     RES5/RES6 get baseline for WFP 1st: there is no upper floor to move
%     to, and adding a story is not a retrofit we model for them. RES2/RES4
%     keep the manufactured/temporary restriction. Multi-story buildings of
%     every class keep the original WFP 1st transform. The
%     OneStoryAddStory lookup column is true only where the conversion was
%     actually applied.
%   - ELEVATION THRESHOLD IS SET PER LOCATION. ELEV_MIN_SHORTFALL_FT moved
%     from one global constant into LOC_CONFIG (field ElevMinShortfallFT):
%     Shinnecock 1 ft, MasticBeach 0 ft, Pamunkey 0 ft. The value used in
%     each run is still written to <LOC>_metadata.csv. LOC_CONFIG is now
%     built by the local function location_config(), so the embedded FFF
%     estimator reads the same table.
%   - FFF DEFAULTS FOLLOW LOC_CONFIG. New FFF option 'Location' (default
%     'Shinnecock'). When 'BFE_FT_NAVD88' or 'ElevMinShortfallFT' is not
%     passed, FFF takes it from location_config() for that location, so a
%     call without those options matches the batch run. Explicit values
%     still override.
%   - Comments that still described the v12 "FFE < BFE" (2 ft) rule now
%     describe the per-location threshold.
%
% v13 changes:
%   - ONE-STORY BUILDINGS: "WFP 1st" (abandon the first floor) is now
%     computed as an ADD-A-STORY CONVERSION instead of the generic
%     "subtract damage up to one story height" transform. A one-story
%     building has no upper floor to live on, so abandoning its first
%     floor only makes sense if a new story is built on top and the living
%     space and contents move up (FEMA P-312, 3rd ed., p. 3-18; FEMA
%     second-story conversion fact sheet, 2019). The old transform also
%     produced an artifact: the one-story no-basement curve (R11N)
%     saturates below the 10 ft story height (100% by 5 ft), so baseline
%     minus damage-at-H was zero at EVERY depth, i.e. a free, total
%     protection. (R11B does not saturate, 55% at 10 ft and 69% at 24 ft;
%     there the old transform was zero only up to 10 ft of water.)
%   - The conversion is scored with the same transform as Elevate, using
%     the new living floor as the target: FFE_new = FFE + STORY_H_FT.
%     Damage = curve(WL - FFE_new) - damage-at-0 (basement filled /
%     threshold damage removed), zero when water stays below FFE_new.
%     The abandoned ground floor is treated as wet floodproofed, exactly
%     as WFP 1st already treats the lower floor of multi-story buildings
%     (no damage below one story height). Structure and content values
%     are unchanged: the living area is relocated, not enlarged.
%     For a home with a basement this also removes all basement damage,
%     the same as Elevate, so the conversion assumes the basement is
%     filled or wet floodproofed and the utilities move up to the new
%     floor.
%   - One-story is decided by the NSI number_of_stories column
%     (round(n_st) <= 1). [Superseded in v14: for RES1 the SOID story
%     digit decides, and the conversion is limited to RES1/RES3.]
%   - New switch ONE_STORY_ADD_STORY (default true). Set it to false to
%     exclude one-story buildings from WFP 1st instead (action returns
%     baseline). The RES2/RES4 restriction still overrides both modes:
%     a mobile home or temporary lodging cannot add a story.
%   - Transparency: new per-building column OneStoryAddStory in
%     <LOC>_bldg_lookup.csv (true when that building's WFP 1st result is
%     the add-a-story conversion) and new metadata key
%     ONE_STORY_ADD_STORY in <LOC>_metadata.csv. The embedded FFF
%     estimator applies the same rule (option 'OneStoryAddStory').
%   - ACTION_NAMES are unchanged ("WFP 1st" label kept) so the CSV join
%     keys used by the dashboard do not break.
%
% v12 changes:
%   - SINGLE-FILE BUILD. The per-building estimator formerly maintained
%     as a separate file (FFF___Damage_Estimator.m) is now embedded as a
%     local function at the bottom of this script, together with its
%     curve helpers (calc_damage_pcts, zone_to_column, interp_damage_pct).
%     The embedded FFF no longer carries its own SOID converter; it calls
%     this script's convert_NSI_to_SOID, so the occupancy->SOID logic now
%     lives in exactly ONE place (this resolves the v11 "mirror the same
%     change in FFF" maintenance burden). Because a script's local
%     functions are only callable from within this file, an OFF-by-default
%     example block (RUN_FFF_EXAMPLE) shows the call signature; if you
%     drive FFF from a separate per-building figure script, either fold
%     that driver into this file or keep a thin standalone wrapper.
%   - RES2 (manufactured / mobile homes): "Raise Utilities" no longer
%     mitigates damage. A mobile home has no upper floor to relocate
%     utilities to, so the only retrofit that physically makes sense is
%     elevating the whole structure. Under the manufactured/temporary
%     restriction, RES2 now has Raise Utilities, WFP Basement, and WFP
%     1st Floor ALL forced to baseline — leaving Elevate as the sole
%     action with any benefit. RES4 (temporary lodging) is unchanged from
%     v11 (WFP B / WFP 1st baseline; Raise Utilities still allowed, since
%     a multi-floor lodging can relocate utilities). The RES2-only
%     suppression is gated on the same RESTRICT_MANUF_LOCATIONS switch as
%     the existing WFP suppression; flip is_manuf_home gating to universal
%     if you want it everywhere.
%   - Elevation is no longer suggested for homes already at or above BFE.
%     Previously the Elevate action delivered benefit for any home below
%     the DFE (= BFE+2), including homes only 1-2 ft under the DFE. A lift
%     that small does not justify the (largely fixed) cost of elevating a
%     structure (cf. FEMA P-312's ~2-3 ft practicality floor and the
%     Zarekarizi et al. 2020 convention of treating <3 ft lifts as
%     impractical). The new rule: Elevate provides benefit only when
%     FFE < DFE - ELEV_MIN_SHORTFALL_FT. With ELEV_MIN_SHORTFALL_FT = 2
%     and DFE = BFE+2, that threshold is exactly BFE — i.e. homes at or
%     above BFE are not advised to elevate (Elevate returns baseline).
%     The elevation TARGET is still the DFE (BFE+2); only the eligibility
%     gate changed. Per-building eligibility is surfaced as the new
%     ElevationEligible column in <LOC>_bldg_lookup.csv, and the threshold
%     is recorded in <LOC>_metadata.csv (ELEV_MIN_SHORTFALL_FT). Note the
%     DFE-status buckets (RES_UnderDFE etc.) are unchanged: a home in
%     [BFE, BFE+2) is still "Under DFE" for reporting, it just no longer
%     receives an Elevate benefit.
%
% Local edits (2026-06):
%   - Added 2060 as a target year: TARGET_YEARS / TARGET_YEAR_LABELS are
%     now [2025 2040 2055 2060 2100], so every cumulative-damage output
%     (per-building, category, metadata) now includes a 2060 row that the
%     downstream dashboard can plot. 2060 sits within the existing MC
%     water-level coverage (2100 was already a target), so no input change
%     is required.
%   - Removed the WestPoint location entirely (dropped from LOC_CONFIG and
%     from the CUSTOM_LOCATIONS example comment). Available locations are
%     now Shinnecock, MasticBeach, and Pamunkey — three choices. The
%     DEFAULT-mode banner now reports numel(ALL_LOCATIONS) instead of a
%     hardcoded count.
%
% v11 changes:
%   - Unknown NSI occupancy types are no longer dropped. Instead of
%     returning an empty SOID and skipping the building, convert_NSI_to_SOID
%     now falls back to a RES1 classification (using the building's
%     foundation_type for basement and number_of_stories for the story
%     digit) and returns a second output, `fallback_used`, that flags the
%     substitution. The same change is mirrored in FFF___Damage_Estimator
%     so that FFF, which previously errored, also keeps running.
%   - Buildings that triggered the RES1 fallback are tracked in a new
%     per-building boolean array bldg_occupancy_fallback (analogous to
%     bldg_restrict_manuf), surfaced as the OccupancyFallback column in
%     <LOC>_bldg_lookup.csv, and written to a dedicated per-location log:
%         <LOC>_occupancy_fallback.csv
%     with columns BuildingID, NSI_row, OriginalOccupancy, AssignedSOID,
%     FallbackOccupancyType. The existing <LOC>_skipped_buildings.csv now
%     only contains genuinely skipped rows (invalid_data, no_damage_curve
%     for a known SOID with no curve in the database).
%   - Fallback'd buildings are routed into the RES aggregate bucket
%     (Damages_RES_underDFE / Damages_RES_aboveDFE) and counted in n_res,
%     matching the RES1 damage curves they are scored against. The
%     OccupancyGroup column of <LOC>_bldg_lookup.csv also reports them as
%     "RES". The OccupancyType column still preserves the ORIGINAL
%     (unknown) NSI string, so a real RES building and a fallback'd one
%     remain distinguishable via the OccupancyFallback column.
%
% v10 changes:
%   - PATCH mode removed. The script now supports only two run modes:
%     DEFAULT (all locations, all occupancy types) and CUSTOM (subset of
%     locations and/or RES vs NONRES filter).
%   - The patch-only helpers (run_patch_mode, cumDmg_from_Dmg,
%     add_to_category, subtract_from_category, assign_id, assign_occupancy)
%     and the per-building characteristic fields previously kept solely
%     to support PATCH reconstruction (bldg_n_st, bldg_f_type,
%     bldg_f_h_ft) have been removed.
%   - The manufactured / temporary lodging retrofit restriction switch
%     introduced alongside PATCH stays. RES2 (manufactured homes) and
%     RES4 (temporary lodgings) in locations listed in
%     RESTRICT_MANUF_LOCATIONS get their WFP Basement and WFP 1st Floor
%     actions forced to baseline (no retrofit benefit). Raise Utilities
%     and Elevate are unaffected; Elevate already returns baseline for
%     FFE >= BFE+2, so above-DFE manufactured homes correctly end up with
%     Raise Utilities as the only retrofit with any real benefit.
%   - "Potential" target-year label dropped; the 2025 column is labeled
%     "2025" like every other target year.
%   - Terminology: replaced "In/Out of floodplain" with "Under/Above DFE"
%     consistently in display strings, CSV column header (DFE_Status),
%     CSV category labels (RES_UnderDFE, RES_AboveDFE, NONRES_UnderDFE,
%     NONRES_AboveDFE), aggregate-array names, and internal variable
%     names (dfe_status, bldg_DFEstatus, DFE_FT). The threshold is the
%     DFE (= BFE+2); the old "floodplain" framing was colloquial.
%   - Action indices are now resolved by NAME inside
%     compute_building_damages_vectorized via a new action_index() helper.
%     Reordering ACTION_NAMES no longer silently shifts which slice of
%     Dmg corresponds to which retrofit; mistyped action names fail
%     loudly with an actionable error. The function signature changed
%     accordingly: it now takes ACTION_NAMES instead of nAc.
%   - EAD vs cumulative-damage language cleaned up. "EAD" is reserved
%     for damage in a given year; the multi-year cumulative quantity is
%     described as "the sum of MC annual damages from the start of
%     years_all up to the target year" without the prior "NOT an
%     actuarial EAD" disclaimer.
%   - The .mat output is no longer written. MC_Results is assembled in
%     memory only for mc_to_pct + save_results_csvs. Without PATCH, the
%     .mat had only post-hoc-analysis value (raw 1000-MC per-building
%     replicas, year-by-year aggregates, ALL/RES/NONRES roll-ups);
%     those are now CSV-summarised at percentile resolution only.
%
% Elevation TARGET is **BFE + 2** (the DFE) in this script; the same is
% enforced in the embedded FFF estimator. As of v12 the elevation
% ELIGIBILITY gate is separate from the target: a home is only advised to
% elevate when FFE < DFE - ELEV_MIN_SHORTFALL_FT. Since v14 the shortfall
% is set per location in LOC_CONFIG (Shinnecock 1 ft, MasticBeach 0 ft,
% Pamunkey 0 ft). Homes at or above that threshold keep their baseline
% damage for the Elevate action.
%
% Output layout (one shared folder for ALL locations, files prefixed by
% location name):
%
%     <LOC>_metadata.csv
%         Location, BFE, counts, action/scenario labels, percentile list,
%         RESTRICT_MANUF_LOC flag.
%     <LOC>_bldg_lookup.csv
%         One row per building: BuildingID, Longitude, Latitude,
%         OccupancyType, OccupancyGroup, SOID, StructureValue,
%         ContentValue, GroundElevation_ft, FFE_ft, DFE_Status,
%         ManufRestriction, OccupancyFallback, ElevationEligible,
%         OneStoryAddStory.
%     <LOC>_bldg_CumulativeDamage.csv
%         Per-building cumulative-damage percentiles (BuildingID x
%         TargetYear x Action x SLR + 23 percentile columns). The
%         cumulative damage at a target year is the SUM of MC annual
%         damages from the start of years_all up to that year — i.e.
%         a distribution (across MC annual-max trajectories) of multi-
%         year cumulative loss.
%     <LOC>_CumulativeDamage_categories.csv
%         Aggregate cumulative-damage percentiles for the four occupancy
%         x DFE-status sub-categories (RES_UnderDFE, RES_AboveDFE,
%         NONRES_UnderDFE, NONRES_AboveDFE).
%
% Percentile column set (heavy on tails): P01..P10, P25, P50, P75,
% P90..P99 — 23 columns total.
% ========================================================================

clc; clear all; close all;

%% ======================== USER SETTINGS =================================
% RUN_MODE:
%   'DEFAULT'  -> Runs ALL locations, ALL occupancy types
%   'CUSTOM'   -> Runs only the location(s) and occupancy filter below
RUN_MODE = 'CUSTOM';

% --- CUSTOM mode settings (only used when RUN_MODE = 'CUSTOM') ----------
CUSTOM_LOCATIONS      = {'MasticBeach'};   % e.g. {'Shinnecock'} or {'Shinnecock','MasticBeach'}
CUSTOM_OCCUPANCY      = 'ALL';            % 'RES', 'NONRES', or 'ALL'

% --- Manufactured/temporary retrofit restriction ("the switch") ---------
% When this restriction is active for a location, RES2 (manufactured /
% mobile homes) and RES4 (temporary lodgings) are treated specially:
%
%   RES2 (mobile homes): the ONLY retrofit that physically makes sense is
%     Elevate. A mobile home has no second floor to relocate utilities to,
%     and wet-floodproofing the shell is not meaningful. So Raise
%     Utilities, WFP Basement, and WFP 1st Floor are ALL forced to
%     baseline (no benefit). Elevate is the sole beneficial action — and,
%     per the v12 elevation rule, only when the home's FFE is below the
%     elevation-eligibility threshold (FFE < DFE - ElevMinShortfallFT,
%     set per location in LOC_CONFIG).
%
%   RES4 (temporary lodgings): WFP Basement and WFP 1st Floor are forced
%     to baseline (wet-floodproofing the shell is not meaningful), but
%     Raise Utilities still applies, since a multi-floor lodging can
%     relocate utilities to an upper floor.
%
% All other occupancy types use the standard methodology regardless of
% this switch.
%
% The list applies in both DEFAULT and CUSTOM modes; in CUSTOM mode it is
% naturally filtered to the CUSTOM_LOCATIONS actually being run. Empty
% cell array = OFF everywhere. The per-building flag actually applied is
% stored in MC_Results.bldg_restrict_manuf and copied into
% <LOC>_bldg_lookup.csv as column "ManufRestriction" for transparency.
RESTRICT_MANUF_LOCATIONS  = {'Pamunkey'};       % e.g. {'Pamunkey'} or {'Pamunkey','Shinnecock'}

% --- Test mode (individual building plots) -------------------------------
TEST_MODE         = false;
NSI_ROWS_TEST     = [1 4 6 7 11];

% ======================== LOCATION CONFIGURATION =========================
% BFE (ft NAVD88) and elevation-eligibility shortfall (ft) per location.
% The table lives in the local function location_config() at the bottom
% of this file, so the embedded FFF estimator reads the same values (v14).
LOC_CONFIG = location_config();

ALL_LOCATIONS = {'Shinnecock', 'MasticBeach', 'Pamunkey'};

% Validate RESTRICT_MANUF_LOCATIONS: every name must be a real LOC_CONFIG
% key. Catching typos here is much better than silently failing to apply
% the restriction at run time.
if ~isempty(RESTRICT_MANUF_LOCATIONS)
    bad = RESTRICT_MANUF_LOCATIONS(~ismember(RESTRICT_MANUF_LOCATIONS, ALL_LOCATIONS));
    if ~isempty(bad)
        error(['[CONFIG] RESTRICT_MANUF_LOCATIONS contains unknown ', ...
               'location name(s): %s. Valid locations: %s.'], ...
               strjoin(bad, ', '), strjoin(ALL_LOCATIONS, ', '));
    end
end

% ======================== COMMON CONSTANTS ===============================
FLOOD_ZONE        = 'A';
UTIL_REPL_USD     = 20000;
STORY_H_FT        = 10;
Y_NOW             = 2025;
NSI_SHEET         = 'Sheet1';

% --- Elevation eligibility floor ----------------------------------------
% Minimum shortfall below the DFE (in feet) required before elevation is
% advised. A home is recommended to elevate only when its FFE is at least
% this far below the DFE, i.e. FFE < DFE - ELEV_MIN_SHORTFALL_FT.
%
% v14: set PER LOCATION in location_config() (field ElevMinShortfallFT)
% and read into ELEV_MIN_SHORTFALL_FT at the top of the location loop.
%   0 -> elevation is offered to ANY home below the DFE (elev_eligible is
%        exactly the "Under DFE" set).
%   1 -> homes within 1 ft of the DFE are not advised to elevate.
%   2 -> cutoff at the BFE (the v12 default).

% --- One-story buildings under "WFP 1st" (abandon first floor) ---------
% true  -> one-story buildings get an ADD-A-STORY conversion: a new story
%          is built on top, living space and contents move up one story
%          (new living floor = FFE + STORY_H_FT), and the ground floor is
%          abandoned and wet floodproofed. Scored with the Elevate
%          transform at that new floor height.
% false -> one-story buildings are excluded from WFP 1st (baseline, no
%          benefit), since there is no upper floor to move to.
% Multi-story buildings are unaffected by this switch. RES2/RES4 keep
% WFP 1st at baseline in both modes (manufactured/temporary restriction).
% v14: only homes (RES1 and RES3 SOIDs) can add a story. One-story
% non-residential and RES5/RES6 buildings get baseline for WFP 1st in
% both modes. For RES1, "one-story" is read from the SOID story digit.
ONE_STORY_ADD_STORY = true;

% NOTE: ACTIONS and ACTION_NAMES are positionally aligned (column-by-column)
% — ACTIONS(i) is the numeric code for ACTION_NAMES(i). The numeric codes
% are NOT in ascending order: WFP B is code 2 but printed before code 4
% (Elevate) so that the dashboard's left-to-right reading order
% ("No mitigation -> Raise Utilities -> WFP B -> Elevate -> WFP 1st")
% groups WFP options sensibly. Downstream code MUST use ACTION_NAMES (text)
% as the join key — never assume the numeric code corresponds to the
% column index, since the model loop iterates by index 1..nAc and stores
% results in that order.
ACTIONS           = [0 1 2 4 3];
ACTION_NAMES      = ["No mitigation","Raise Utilities","WFP B","Elevate","WFP 1st"];

% TARGET_YEARS is a numeric list used to slice years_all for cumulative
% damage. TARGET_YEAR_LABELS is a parallel STRING vector that becomes the
% TargetYear column value in the CSV outputs. Each label is the year
% itself; the first entry is the cumulative damage from the start of
% years_all up to that year (typically Y_NOW = today).
TARGET_YEARS        = [2025 2040 2055 2060 2100];
TARGET_YEAR_LABELS  = ["2025", "2040", "2055", "2060", "2100"];
if numel(TARGET_YEARS) ~= numel(TARGET_YEAR_LABELS)
    error('TARGET_YEARS and TARGET_YEAR_LABELS must have the same length.');
end

SCENARIOS         = ["P50" "P90"];
SCENARIO_LABELS   = ["50th-percentile" "90th-percentile"];


nAc = numel(ACTIONS);
nSc = numel(SCENARIOS);
nTY = numel(TARGET_YEARS);

% ======================== OUTPUT DIRECTORY ================================
% Single shared folder for ALL locations. Files are prefixed with the
% location name so they sort cleanly in any browser. Files written per
% location:
%   <LOC>_metadata.csv          - location/BFE/scenario labels + percentile list
%   <LOC>_bldg_lookup.csv       - BuildingID, Longitude, Latitude, FFE_ft,
%                                 DFE_Status (one row per building)
%   <LOC>_bldg_CumulativeDamage.csv       - per-building cumulative damage percentiles
%   <LOC>_CumulativeDamage_categories.csv - aggregate cumulative damage percentiles for
%                                 the 4 occupancy x DFE-status sub-categories
OUT_DIR = 'RRR Results';

out_dir_for = @(~) OUT_DIR;

% ======================== DETERMINE LOCATIONS TO RUN =====================
if strcmp(RUN_MODE, 'DEFAULT')
    locations_to_run = ALL_LOCATIONS;
    occupancy_to_run = 'ALL';
    fprintf('============================================================\n');
    fprintf('[INFO] RUN_MODE = DEFAULT: Running ALL %d locations, ALL buildings\n', numel(ALL_LOCATIONS));
    fprintf('============================================================\n');
elseif strcmp(RUN_MODE, 'CUSTOM')
    locations_to_run = CUSTOM_LOCATIONS;
    occupancy_to_run = CUSTOM_OCCUPANCY;
    fprintf('============================================================\n');
    fprintf('[INFO] RUN_MODE = CUSTOM: %s, Occupancy=%s\n', ...
        strjoin(locations_to_run, ', '), occupancy_to_run);
    fprintf('============================================================\n');
else
    error('[CONFIG] Unknown RUN_MODE: %s. Valid options: DEFAULT, CUSTOM.', RUN_MODE);
end

% ======================== LOAD DAMAGE CURVES ONCE ========================
fprintf('[INFO] Loading damage curve tables...\n');
DamageCurves = load_all_damage_curves();
fprintf('[INFO] Damage curves loaded successfully.\n\n');

% ============ OPTIONAL: single-building check via embedded FFF ===========
% The per-building estimator FFF___Damage_Estimator is now a local
% function in this file (bottom of the script). Because a script's local
% functions are only callable from within the same file, this guarded
% block shows the call signature, including the v12-v14 options:
%   'Location'             (default 'Shinnecock') - sets the BFE and the
%                          elevation floor from location_config() (v14)
%   'ElevMinShortfallFT'   (default: from 'Location') - elevation floor
%   'ApplyManufRestriction'(default false) - RES2/RES4 retrofit restriction
% Leave RUN_FFF_EXAMPLE = false for normal batch runs.
RUN_FFF_EXAMPLE = false;
if RUN_FFF_EXAMPLE
    % Example: a RES2 mobile home, FFE = 6.5 ft, BFE = 7 (Pamunkey),
    % evaluated at a 9 ft water level under the Elevate action (4).
    ex = FFF___Damage_Estimator(1, 'C', 'RES2', 5.0, 1.5, 9.0, 4, ...
                                120000, 60000, ...
                                'Location', 'Pamunkey', ...
                                'ApplyManufRestriction', true, ...
                                'OneStoryAddStory', ONE_STORY_ADD_STORY);
    fprintf('[FFF example] action=%s  total=$%.0f  elev_eligible=%d  manuf_restricted=%d\n', ...
        ex.Analysis_Case, ex.Total_Dmg_USD, ex.Elevation_Eligible, ex.ManufRestriction_Applied);

    % Example: a one-story RES1 home on a crawlspace, FFE = 6.5 ft,
    % BFE = 8 (Mastic Beach), 12 ft water level, WFP 1st action (3).
    % With ONE_STORY_ADD_STORY = true the new living floor is at 16.5 ft,
    % so this event causes no damage.
    ex1 = FFF___Damage_Estimator(1, 'C', 'RES1-1SNB', 5.0, 1.5, 12.0, 3, ...
                                 250000, 125000, ...
                                 'Location', 'MasticBeach', ...
                                 'StoryHeightFT', STORY_H_FT, ...
                                 'OneStoryAddStory', ONE_STORY_ADD_STORY);
    fprintf('[FFF example] action=%s  total=$%.0f  one_story_add_story=%d\n', ...
        ex1.Analysis_Case, ex1.Total_Dmg_USD, ex1.OneStoryAddStory_Applied);
end

%% ======================== MAIN LOCATION LOOP ============================
for loc_idx = 1:numel(locations_to_run)
    LOCATION = locations_to_run{loc_idx};
    
    % Validate location
    if ~isfield(LOC_CONFIG, LOCATION)
        fprintf('[WARNING] Unknown location: %s. Skipping.\n', LOCATION);
        continue;
    end
    
    BFE_FT_NAVD88 = LOC_CONFIG.(LOCATION).BFE;
    ELEV_MIN_SHORTFALL_FT = LOC_CONFIG.(LOCATION).ElevMinShortfallFT;   % v14: per location
    OCCUPANCY_FILTER = occupancy_to_run;
    
    % Is the manufactured/temporary retrofit restriction active here?
    restrict_manuf_this_loc = ismember(LOCATION, RESTRICT_MANUF_LOCATIONS);
    
    % Per-location output directory
    OUT_DIR = out_dir_for(LOCATION);
    if ~exist(OUT_DIR, 'dir'); mkdir(OUT_DIR); end
    
    fprintf('\n============================================================\n');
    fprintf('[INFO] PROCESSING: %s (BFE = %d ft, Occupancy = %s)\n', ...
        LOCATION, BFE_FT_NAVD88, OCCUPANCY_FILTER);
    % The manufactured/temporary retrofit restriction is now occupancy-driven
    % (applied to every RES2/RES4 building regardless of location, to match the
    % app). RESTRICT_MANUF_LOCATIONS is retained only for reporting below.
    fprintf('[INFO] Manufactured/temporary retrofit restriction: occupancy-driven\n');
    fprintf('       (RES2 & RES4: WFP B and WFP 1st forced to baseline;\n');
    fprintf('        RES2 additionally: Raise Utilities forced to baseline.)\n');
    if restrict_manuf_this_loc
        fprintf('       [%s is listed in RESTRICT_MANUF_LOCATIONS.]\n', LOCATION);
    end
    fprintf('============================================================\n');
    
    % -------------------- Construct file paths ---------------------------
    NSI_FILE    = sprintf('DDD___%s___NSI.xlsx', LOCATION);
    MC_FILE_P50 = sprintf('DDD___%s_MC_annual_max_waterlevels_P50.csv', LOCATION);
    MC_FILE_P90 = sprintf('DDD___%s_MC_annual_max_waterlevels_P90.csv', LOCATION);
    
    % Check that input files exist
    if ~isfile(NSI_FILE)
        fprintf('[WARNING] NSI file not found: %s. Skipping location.\n', NSI_FILE);
        continue;
    end
    if ~isfile(MC_FILE_P50) || ~isfile(MC_FILE_P90)
        fprintf('[WARNING] MC water level files not found for %s. Skipping.\n', LOCATION);
        continue;
    end
    
    fprintf('[INFO] Input NSI file: %s\n', NSI_FILE);
    fprintf('[INFO] Input MC files: %s, %s\n', MC_FILE_P50, MC_FILE_P90);
    fprintf('[INFO] Output directory: %s\n', OUT_DIR);
    
    %% -------------------- LOAD INPUT DATASETS ---------------------------
    T = readtable(NSI_FILE, 'Sheet', NSI_SHEET);
    T.Properties.VariableNames = lower(T.Properties.VariableNames);

    % Required column up front: 'id' (the bldg_lookup CSV needs real IDs).
    % Fail fast with a clear message rather than fabricating synthetic IDs.
    if ~ismember('id', T.Properties.VariableNames)
        error(['[INPUT] NSI file %s is missing the required `id` column. ', ...
               'Add an `id` column with stable building identifiers and ', ...
               're-run.'], NSI_FILE);
    end

    % -------------------- OCCUPANCY FILTER -------------------------------
    occupancy_mask = true(height(T), 1);

    if strcmp(OCCUPANCY_FILTER, 'RES')
        for i = 1:height(T)
            occ = char(string(T.occupancy_type(i)));   % robust to cell/string/categorical
            if ~startsWith(upper(strtrim(occ)), 'RES')
                occupancy_mask(i) = false;
            end
        end
        fprintf('[INFO] Filtering for RESIDENTIAL occupancy types only.\n');
    elseif strcmp(OCCUPANCY_FILTER, 'NONRES')
        for i = 1:height(T)
            occ = char(string(T.occupancy_type(i)));
            if startsWith(upper(strtrim(occ)), 'RES')
                occupancy_mask(i) = false;
            end
        end
        fprintf('[INFO] Filtering for NON-RESIDENTIAL occupancy types only.\n');
    else
        fprintf('[INFO] Including ALL occupancy types.\n');
    end
    
    valid_occ_indices = find(occupancy_mask);
    fprintf('[INFO] Occupancy filter: %d buildings included, %d filtered out.\n', ...
        numel(valid_occ_indices), sum(~occupancy_mask));
    
    % Determine rows to process
    if TEST_MODE
        rows_to_process = intersect(NSI_ROWS_TEST, valid_occ_indices);
        rows_to_process = rows_to_process(rows_to_process >= 1 & rows_to_process <= height(T));
        rows_to_plot = rows_to_process;
    else
        rows_to_process = valid_occ_indices(:)';
        rows_to_plot = intersect(NSI_ROWS_TEST, valid_occ_indices);
        rows_to_plot = rows_to_plot(rows_to_plot >= 1 & rows_to_plot <= height(T));
    end
    nB = numel(rows_to_process);
    
    if nB == 0
        fprintf('[WARNING] No buildings to process for %s. Skipping.\n', LOCATION);
        continue;
    end
    fprintf('[INFO] Buildings to process: %d\n', nB);
    
    % -------------------- Precompute FFE & DFE status --------------------
    FFE_all = T.ground_elevation + T.foundation_height;
    DFE_FT = BFE_FT_NAVD88 + 2;
    dfe_status_all = strings(height(T), 1);
    dfe_status_all(FFE_all < DFE_FT) = "Under DFE";
    dfe_status_all(FFE_all >= DFE_FT) = "Above DFE";
    fprintf('[INFO] DFE = %.2f ft NAVD88 (FFE < DFE => Under DFE)\n', DFE_FT);

    % Elevation-eligibility threshold: homes with FFE at or above this are
    % NOT advised to elevate (Elevate returns baseline). = BFE when
    % ELEV_MIN_SHORTFALL_FT = 2. Computed once per location.
    ELEV_ELIG_FFE_MAX = DFE_FT - ELEV_MIN_SHORTFALL_FT;
    elev_eligible_all = FFE_all < ELEV_ELIG_FFE_MAX;
    fprintf(['[INFO] Elevation advised only for FFE < %.2f ft NAVD88 ', ...
             '(DFE - %.2f ft); FFE >= that => no elevation benefit.\n'], ...
             ELEV_ELIG_FFE_MAX, ELEV_MIN_SHORTFALL_FT);
    
    % -------------------- Classify RES / NONRES per row ------------------
    is_res_all = false(height(T), 1);
    for i = 1:height(T)
        is_res_all(i) = startsWith(upper(strtrim(char(string(T.occupancy_type(i))))), 'RES');
    end

    % -------------------- Load MC water levels ---------------------------
    MC_P50 = readtable(MC_FILE_P50); MC_P90 = readtable(MC_FILE_P90);
    years_all = MC_P50.Year;
    nYears = numel(years_all);
    nMC = width(MC_P50) - 1;
    WL_P50 = table2array(MC_P50(:, 2:end));
    WL_P90 = table2array(MC_P90(:, 2:end));
    fprintf('[INFO] MC water levels: %d years x %d MC runs (range %d..%d)\n', ...
        nYears, nMC, min(years_all), max(years_all));

    % -------------------- Validate target-year coverage ------------------
    % If the MC time series ends before the latest TARGET_YEAR, the
    % cumulative-damage sum for that target would silently truncate, which
    % is a real source of misreading. Be loud about it.
    max_mc_yr = max(years_all);
    short_targets = TARGET_YEARS(TARGET_YEARS > max_mc_yr);
    if ~isempty(short_targets)
        error(['[INPUT] %s: MC water-level series ends at %d, but ', ...
               'TARGET_YEARS includes %s (beyond MC coverage). Either ', ...
               'extend the MC files or trim TARGET_YEARS.'], ...
               LOCATION, max_mc_yr, ...
               strjoin(arrayfun(@(y) sprintf('%d', y), short_targets, ...
                                'UniformOutput', false), ', '));
    end
    if min(years_all) > Y_NOW
        warning(['[INPUT] %s: MC series starts at %d, after Y_NOW=%d. ', ...
                 'The first target-year row may not represent the true ', ...
                 'present-day exposure.'], LOCATION, min(years_all), Y_NOW);
    end
    
    %% -------------------- PREP OUTPUT CONTAINERS ------------------------
    % Aggregate damages split by occupancy AND DFE status
    Damages_agg         = zeros(nYears, nMC, nAc, nSc);  % ALL
    Damages_RES_underDFE    = zeros(nYears, nMC, nAc, nSc);
    Damages_RES_aboveDFE   = zeros(nYears, nMC, nAc, nSc);
    Damages_NONRES_underDFE = zeros(nYears, nMC, nAc, nSc);
    Damages_NONRES_aboveDFE= zeros(nYears, nMC, nAc, nSc);
    
    % Individual building storage (for TEST_MODE plots only)
    IndivBldg = struct();
    for k = 1:numel(rows_to_plot)
        ri = rows_to_plot(k);
        IndivBldg(k).row = ri;
        IndivBldg(k).Damages = zeros(nYears, nMC, nAc, nSc);
    end
    
    % Per-building results (fast arrays)
    maxBldgs = nB;
    bldg_NSI_rows = zeros(maxBldgs, 1);
    bldg_CumulativeDamage_MC = zeros(maxBldgs, nTY, nSc, nAc, nMC);  % full 1000 MC replicas
    bldg_FFE      = zeros(maxBldgs, 1);
    bldg_DFEstatus = strings(maxBldgs, 1);
    bldg_SOID     = strings(maxBldgs, 1);
    bldg_restrict_manuf = false(maxBldgs, 1);   % whether the RES2/RES4 retrofit
                                                % restriction was applied to
                                                % this building (true => Dmg
                                                % for WFP B & WFP 1st were
                                                % overridden to baseline)
    bldg_occupancy_fallback = false(maxBldgs, 1);  % whether the NSI occupancy
                                                   % was unknown and the
                                                   % building was reclassified
                                                   % as RES1 by convert_NSI_to_SOID
    bldg_elev_eligible = false(maxBldgs, 1);   % whether elevation is advised
                                               % for this building
                                               % (FFE < DFE - ELEV_MIN_SHORTFALL_FT).
                                               % When false, the Elevate action
                                               % returns baseline (no benefit).
    bldg_one_story_addstory = false(maxBldgs, 1);  % whether this building's
                                                   % WFP 1st result is the
                                                   % one-story add-a-story
                                                   % conversion (v13)
    nBldgProcessed = 0;
    
    %% -------------------- MAIN EVALUATION LOOP --------------------------
    fprintf('[INFO] Computing damages (vectorized)...\n');
    tic;
    
    % Throttled progress print: emit every PROGRESS_EVERY buildings,
    % plus the first/last. For 4 locations x thousands of buildings the
    % old per-building line was multi-MB of console output.
    PROGRESS_EVERY = 500;

    % Skipped-building log. Each row: ID, NSI_row, occupancy, SOID, reason.
    skipped_log = cell(0, 5);

    % Occupancy-fallback log. Each row: ID, NSI_row, original_occupancy,
    % assigned SOID (after RES1 fallback), fallback occupancy label.
    % Distinct from skipped_log because these buildings ARE processed —
    % just under a substituted classification.
    fallback_log = cell(0, 5);

    for idx = 1:nB
        ri = rows_to_process(idx);

        occ        = char(string(T.occupancy_type(ri)));
        n_st       = T.number_of_stories(ri);
        f_type     = char(string(T.foundation_type(ri)));
        f_h_ft     = T.foundation_height(ri);
        gnd_ft     = T.ground_elevation(ri);
        struct_val = T.structure_value(ri);
        cont_val   = T.content_value(ri);
        FFE        = FFE_all(ri);
        dfe_status  = dfe_status_all(ri);
        is_res     = is_res_all(ri);
        elev_eligible = elev_eligible_all(ri);   % FFE < DFE - ELEV_MIN_SHORTFALL_FT

        thisID = string(T.id(ri));   % `id` column is required (validated upfront)

        % Throttled progress line
        if idx == 1 || idx == nB || mod(idx, PROGRESS_EVERY) == 0
            fprintf('  Building %d/%d (ID=%s, Occ=%s)\n', idx, nB, thisID, occ);
        end

        % Skip invalid buildings
        if ~isfinite(f_h_ft) || ~isfinite(gnd_ft) || ~isfinite(n_st) || n_st <= 0
            skipped_log(end+1, :) = {char(thisID), ri, occ, '', 'invalid_data'}; %#ok<AGROW>
            continue;
        end
        
        % Individual storage check
        if isempty(rows_to_plot) || ~isfield(IndivBldg, 'row')
            store_indiv = false;
        else
            indiv_idx = find([IndivBldg.row] == ri, 1);
            store_indiv = ~isempty(indiv_idx);
            if store_indiv
                IndivBldg(indiv_idx).ID = thisID;
                IndivBldg(indiv_idx).FFE = FFE;
                IndivBldg(indiv_idx).dfe_status = dfe_status;
            end
        end
        
        % Get SOID and damage curves. convert_NSI_to_SOID returns a
        % second output: true if the NSI occupancy was unknown and a
        % RES1 fallback SOID was substituted. We capture and log that
        % so the substitution is fully transparent downstream.
        [soid, occ_fallback_used] = convert_NSI_to_SOID(occ, f_type, n_st);
        if occ_fallback_used
            fallback_log(end+1, :) = {char(thisID), ri, occ, soid, 'RES1'}; %#ok<AGROW>
        end
        [curve_depths, struct_curve, cont_curve] = get_damage_curves(DamageCurves, soid, FLOOD_ZONE);
        
        if isempty(struct_curve)
            skipped_log(end+1, :) = {char(thisID), ri, occ, soid, 'no_damage_curve'}; %#ok<AGROW>
            continue;
        end

        % Decide whether the manufactured/temporary retrofit restriction
        % applies to this building. This is now driven purely by occupancy
        % (RES2 / RES4), NOT by a location switch, so the MATLAB pipeline
        % matches the app, which drops these retrofits for every RES2/RES4
        % building regardless of site. Only RES2 and RES4 are ever affected.
        % is_manuf_home isolates RES2 (mobile homes) specifically — for
        % those, Raise Utilities is also suppressed, since a mobile home has
        % no upper floor to relocate utilities to. (restrict_manuf_this_loc
        % is retained for reporting via MC_Results.RESTRICT_MANUF_LOC but no
        % longer gates the restriction.)
        apply_manuf_restriction = is_manuf_or_temp(occ);
        is_manuf_home = is_manuf_home_fn(occ);   % RES2 only

        % Determine basement status from the NSI's foundation_type column.
        % A "true basement" requires the explicit basement indicator in
        % the NSI (foundation_type == 'B'). This is independent of
        % occupancy type: a building's basement is recorded in the NSI's
        % basement/foundation column, not inferred from RES1-2SWB-style
        % suffixes in the occupancy code. WFP Basement is gated on this
        % flag inside compute_building_damages_vectorized — buildings on
        % slab, crawl space, or pier get WFP B == baseline (a no-op),
        % since you cannot wet-floodproof a basement that does not exist.
        has_basement = strcmpi(strtrim(string(f_type)), "B");

        % One-story buildings (v13/v14): WFP 1st becomes an add-a-story
        % conversion for homes (RES1/RES3 SOIDs) when ONE_STORY_ADD_STORY
        % is on, and baseline otherwise. v14: for RES1 the SOID story
        % digit decides (the same digit that picked the FAST curve); other
        % classes use the NSI story count, validated (finite, > 0) above.
        is_one_story = is_one_story_fn(n_st, soid);
        add_story_ok = ONE_STORY_ADD_STORY && can_add_story_fn(soid);

        % Compute all damages vectorized
        Dmg_bldg = compute_building_damages_vectorized(...
            WL_P50, WL_P90, FFE, BFE_FT_NAVD88, STORY_H_FT, UTIL_REPL_USD, ...
            struct_val, cont_val, curve_depths, struct_curve, cont_curve, ...
            dfe_status, ACTION_NAMES, apply_manuf_restriction, has_basement, ...
            is_manuf_home, ELEV_MIN_SHORTFALL_FT, ...
            is_one_story, add_story_ok);
        
        % Store per-building results
        nBldgProcessed = nBldgProcessed + 1;
        bi = nBldgProcessed;
        bldg_NSI_rows(bi) = ri;
        bldg_FFE(bi) = FFE;
        bldg_DFEstatus(bi) = dfe_status;
        bldg_SOID(bi) = string(soid);
        bldg_restrict_manuf(bi) = apply_manuf_restriction;
        bldg_occupancy_fallback(bi) = occ_fallback_used;
        bldg_elev_eligible(bi) = elev_eligible;
        % The RES2/RES4 restriction overrides the conversion (WFP 1st is
        % forced to baseline afterwards), so the flag is false for them.
        bldg_one_story_addstory(bi) = is_one_story && add_story_ok ...
                                      && ~apply_manuf_restriction;

        for ti = 1:nTY
            yr_mask = years_all <= TARGET_YEARS(ti);
            for si = 1:nSc
                for ai = 1:nAc
                    dmg_slice = Dmg_bldg(yr_mask, :, ai, si);
                    cumDmg_vals = sum(dmg_slice, 1);
                    bldg_CumulativeDamage_MC(bi, ti, si, ai, :) = cumDmg_vals;  % all MC replicas
                end
            end
        end
        
        % Accumulate into ALL aggregate
        Damages_agg = Damages_agg + Dmg_bldg;
        
        % Accumulate into RES/NONRES x FP splits.
        % A fallback'd building (NSI occupancy unknown -> reclassified as
        % RES1) is treated as RES here too, for consistency with the RES1
        % damage curves it's actually being scored against. Without this,
        % its damages would be RES1-shaped but reported in the NONRES
        % aggregate, which is misleading.
        if is_res || occ_fallback_used
            if dfe_status == "Under DFE"
                Damages_RES_underDFE = Damages_RES_underDFE + Dmg_bldg;
            else
                Damages_RES_aboveDFE = Damages_RES_aboveDFE + Dmg_bldg;
            end
        else
            if dfe_status == "Under DFE"
                Damages_NONRES_underDFE = Damages_NONRES_underDFE + Dmg_bldg;
            else
                Damages_NONRES_aboveDFE = Damages_NONRES_aboveDFE + Dmg_bldg;
            end
        end
        
        % Store for individual plot if needed
        if store_indiv
            IndivBldg(indiv_idx).Damages = Dmg_bldg;
        end
    end
    
    elapsed_time = toc;
    fprintf('[INFO] %s: Damage calculation completed in %.2f seconds.\n', LOCATION, elapsed_time);
    
    %% -------------------- CUMULATIVE DAMAGE TO TARGET YEARS -------------
    % CumulativeDamage_X(ti, :, :, :) is the sum of MC annual damages
    % from start of years_all up to TARGET_YEARS(ti); it is therefore a
    % distribution (across MC annual-max trajectories) of multi-year
    % cumulative loss, stratified by occupancy x DFE status.
    CumulativeDamage_ALL          = zeros(nTY, nMC, nAc, nSc);
    CumulativeDamage_RES_underDFE     = zeros(nTY, nMC, nAc, nSc);
    CumulativeDamage_RES_aboveDFE    = zeros(nTY, nMC, nAc, nSc);
    CumulativeDamage_NONRES_underDFE  = zeros(nTY, nMC, nAc, nSc);
    CumulativeDamage_NONRES_aboveDFE = zeros(nTY, nMC, nAc, nSc);
    
    for ti = 1:nTY
        yr_mask = years_all <= TARGET_YEARS(ti);
        CumulativeDamage_ALL(ti, :, :, :)          = sum(Damages_agg(yr_mask, :, :, :), 1);
        CumulativeDamage_RES_underDFE(ti, :, :, :)     = sum(Damages_RES_underDFE(yr_mask, :, :, :), 1);
        CumulativeDamage_RES_aboveDFE(ti, :, :, :)    = sum(Damages_RES_aboveDFE(yr_mask, :, :, :), 1);
        CumulativeDamage_NONRES_underDFE(ti, :, :, :)  = sum(Damages_NONRES_underDFE(yr_mask, :, :, :), 1);
        CumulativeDamage_NONRES_aboveDFE(ti, :, :, :) = sum(Damages_NONRES_aboveDFE(yr_mask, :, :, :), 1);
    end
    
    % Combined RES and NONRES (both DFE statuses)
    CumulativeDamage_RES    = CumulativeDamage_RES_underDFE + CumulativeDamage_RES_aboveDFE;
    CumulativeDamage_NONRES = CumulativeDamage_NONRES_underDFE + CumulativeDamage_NONRES_aboveDFE;
    
    %% -------------------- SAVE RESULTS ----------------------------------
    % Trim arrays
    bldg_NSI_rows = bldg_NSI_rows(1:nBldgProcessed);
    bldg_FFE      = bldg_FFE(1:nBldgProcessed);
    bldg_DFEstatus = bldg_DFEstatus(1:nBldgProcessed);
    bldg_SOID     = bldg_SOID(1:nBldgProcessed);
    bldg_restrict_manuf = bldg_restrict_manuf(1:nBldgProcessed);
    bldg_occupancy_fallback = bldg_occupancy_fallback(1:nBldgProcessed);
    bldg_elev_eligible = bldg_elev_eligible(1:nBldgProcessed);
    bldg_one_story_addstory = bldg_one_story_addstory(1:nBldgProcessed);
    bldg_CumulativeDamage_MC = bldg_CumulativeDamage_MC(1:nBldgProcessed, :, :, :, :);

    % ----- Skipped-buildings CSV + console summary -----
    if ~exist(OUT_DIR, 'dir'); mkdir(OUT_DIR); end
    if ~isempty(skipped_log)
        skipped_tbl = cell2table(skipped_log, ...
            'VariableNames', {'BuildingID','NSI_row','OccupancyType','SOID','Reason'});
        skippedPath = fullfile(OUT_DIR, sprintf('%s_skipped_buildings.csv', LOCATION));
        writetable(skipped_tbl, skippedPath);
        % Reason breakdown
        reasons = skipped_log(:, 5);
        [u_reasons, ~, ic] = unique(reasons);
        counts = accumarray(ic, 1);
        reason_summary = strjoin(arrayfun(@(k) sprintf('%s=%d', ...
            u_reasons{k}, counts(k)), 1:numel(u_reasons), ...
            'UniformOutput', false), ', ');
        fprintf('[INFO] %s: %d building(s) skipped (%s). Wrote %s_skipped_buildings.csv\n', ...
            LOCATION, size(skipped_log, 1), reason_summary, LOCATION);
    else
        fprintf('[INFO] %s: no buildings skipped.\n', LOCATION);
    end

    % ----- Occupancy-fallback CSV + console summary -----
    % Buildings whose NSI occupancy did not match any known prefix and
    % were reclassified as RES1. These ARE included in all downstream
    % aggregates and per-building outputs; the CSV exists for auditing.
    if ~isempty(fallback_log)
        fallback_tbl = cell2table(fallback_log, ...
            'VariableNames', {'BuildingID','NSI_row','OriginalOccupancy', ...
                              'AssignedSOID','FallbackOccupancyType'});
        fallbackPath = fullfile(OUT_DIR, sprintf('%s_occupancy_fallback.csv', LOCATION));
        writetable(fallback_tbl, fallbackPath);
        fprintf(['[INFO] %s: %d building(s) reclassified as RES1 ', ...
                 '(unknown NSI occupancy). Wrote %s_occupancy_fallback.csv\n'], ...
            LOCATION, size(fallback_log, 1), LOCATION);
    else
        fprintf('[INFO] %s: no occupancy fallbacks.\n', LOCATION);
    end

    fprintf('[INFO] Buildings processed: %d\n', nBldgProcessed);
    
    % Count RES vs NONRES. A fallback'd building (NSI occupancy unknown
    % -> reclassified as RES1) is counted as RES here for consistency
    % with the RES1 damage curves it was scored against.
    is_res_for_bldg = is_res_all(bldg_NSI_rows) | bldg_occupancy_fallback(:);
    n_res = sum(is_res_for_bldg);
    n_nonres = nBldgProcessed - n_res;
    n_underDFE  = sum(bldg_DFEstatus == "Under DFE");
    n_aboveDFE = sum(bldg_DFEstatus == "Above DFE");
    fprintf('[INFO] RES: %d, NONRES: %d | Under DFE: %d, Above DFE: %d\n', ...
        n_res, n_nonres, n_underDFE, n_aboveDFE);
    if ONE_STORY_ADD_STORY
        fprintf(['[INFO] WFP 1st scored as add-a-story conversion for %d ', ...
                 'one-story home(s), RES1/RES3 (new living floor = FFE + %g ft).\n'], ...
            sum(bldg_one_story_addstory), STORY_H_FT);
    else
        fprintf('[INFO] One-story buildings excluded from WFP 1st (baseline).\n');
    end

    % ================== PACKAGE MC_RESULTS FOR WORKBOOK ===================
    % Per-building characteristics used by save_results_csvs to populate
    % the <LOC>_bldg_lookup.csv columns.
    bldg_struct_val = T.structure_value(bldg_NSI_rows);
    bldg_cont_val   = T.content_value(bldg_NSI_rows);
    bldg_gnd_ft     = T.ground_elevation(bldg_NSI_rows);

    % Full NSI rows for every processed building (lat/lon/address/year_built/
    % area/etc.). Used by save_results_csvs to populate the slim
    % <LOC>_bldg_lookup.csv output.
    bldg_NSI_table  = T(bldg_NSI_rows, :);

    % MC_Results holds the in-memory RAW MC arrays. mc_to_pct reduces
    % these to a curated 23-percentile set before save_results_csvs
    % writes the four per-location CSVs. The struct is not persisted
    % to disk.
    MC_Results = struct();
    MC_Results.years_all          = years_all;
    MC_Results.WL_P50             = WL_P50;             % nYears x nMC
    MC_Results.WL_P90             = WL_P90;             % nYears x nMC
    MC_Results.Damages_agg        = Damages_agg;        % nYears x nMC x nAc x nSc
    MC_Results.CumulativeDamage_ALL         = CumulativeDamage_ALL;         % nTY    x nMC x nAc x nSc
    MC_Results.CumulativeDamage_RES         = CumulativeDamage_RES;
    MC_Results.CumulativeDamage_NONRES      = CumulativeDamage_NONRES;
    MC_Results.CumulativeDamage_RES_underDFE    = CumulativeDamage_RES_underDFE;
    MC_Results.CumulativeDamage_RES_aboveDFE   = CumulativeDamage_RES_aboveDFE;
    MC_Results.CumulativeDamage_NONRES_underDFE = CumulativeDamage_NONRES_underDFE;
    MC_Results.CumulativeDamage_NONRES_aboveDFE= CumulativeDamage_NONRES_aboveDFE;
    MC_Results.ACTION_NAMES       = ACTION_NAMES;
    MC_Results.ACTIONS            = ACTIONS;
    MC_Results.SCENARIOS          = SCENARIOS;
    MC_Results.SCENARIO_LABELS    = SCENARIO_LABELS;
    MC_Results.TARGET_YEARS       = TARGET_YEARS;
    MC_Results.TARGET_YEAR_LABELS = TARGET_YEAR_LABELS;
    MC_Results.LOCATION           = LOCATION;
    MC_Results.BFE_FT_NAVD88      = BFE_FT_NAVD88;
    MC_Results.nBldgProcessed     = nBldgProcessed;
    MC_Results.n_res              = n_res;
    MC_Results.n_nonres           = n_nonres;
    MC_Results.n_occupancy_fallback = sum(bldg_occupancy_fallback);
    MC_Results.bldg_CumulativeDamage_MC     = bldg_CumulativeDamage_MC;     % nBldg x nTY x nSc x nAc x nMC
    MC_Results.bldg_NSI_rows      = bldg_NSI_rows;
    MC_Results.bldg_IDs           = T.id(bldg_NSI_rows);
    MC_Results.bldg_occupancy     = T.occupancy_type(bldg_NSI_rows);
    MC_Results.bldg_FFE           = bldg_FFE;
    MC_Results.bldg_DFEstatus      = bldg_DFEstatus;
    MC_Results.bldg_SOID          = bldg_SOID;
    MC_Results.bldg_restrict_manuf = bldg_restrict_manuf;
    MC_Results.bldg_occupancy_fallback = bldg_occupancy_fallback;
    MC_Results.bldg_elev_eligible = bldg_elev_eligible;
    MC_Results.ELEV_MIN_SHORTFALL_FT = ELEV_MIN_SHORTFALL_FT;
    MC_Results.bldg_one_story_addstory = bldg_one_story_addstory;
    MC_Results.ONE_STORY_ADD_STORY = ONE_STORY_ADD_STORY;
    MC_Results.RESTRICT_MANUF_LOC = restrict_manuf_this_loc;
    MC_Results.bldg_struct_val    = bldg_struct_val;
    MC_Results.bldg_cont_val      = bldg_cont_val;
    MC_Results.bldg_gnd_ft        = bldg_gnd_ft;
    MC_Results.bldg_NSI_table     = bldg_NSI_table;     % full NSI rows (incl. lat/lon/address/...)

    % ================== WRITE PERCENTILE CSVs =============================
    % NOTE: MC_Results is assembled in memory only — it is no longer saved
    % to a .mat file. The four CSVs below are the persistent outputs. If
    % offline access to the raw 1000-MC per-building tensor or year-by-year
    % aggregates is later needed, re-enable the save() call here.
    MC_Results_pct = mc_to_pct(MC_Results);
    save_results_csvs(OUT_DIR, LOCATION, MC_Results_pct);

    fprintf('[DONE] %s complete. Results in %s/\n\n', LOCATION, OUT_DIR);

end  % end location loop

fprintf('============================================================\n');
fprintf('[DONE] All locations processed. Outputs in: %s/\n', OUT_DIR);
fprintf('============================================================\n');

%% ========================= HELPER FUNCTIONS =============================

%% ========================= VECTORIZED DAMAGE FUNCTIONS ==================

function DamageCurves = load_all_damage_curves()
    DamageCurves = struct();
    DamageCurves.StructMap = readtable('flBldgStructDmgFinal.csv');
    DamageCurves.StructFn  = readtable('flBldgStructDmgFn.csv');
    DamageCurves.ContMap   = readtable('flBldgContDmgFinal.csv');
    DamageCurves.ContFn    = readtable('flBldgContDmgFn.csv');
    
    if iscell(DamageCurves.StructMap.SOccupId)
        DamageCurves.StructMap.SOccupId = cellfun(@strtrim, DamageCurves.StructMap.SOccupId, 'UniformOutput', false);
    end
    if iscell(DamageCurves.ContMap.SOccupId)
        DamageCurves.ContMap.SOccupId = cellfun(@strtrim, DamageCurves.ContMap.SOccupId, 'UniformOutput', false);
    end
    
    DamageCurves.depth_points = -4:1:24;  % 29 points
    DamageCurves.col_names = cell(1, 29);
    for i = 1:29
        d = DamageCurves.depth_points(i);
        if d < 0
            DamageCurves.col_names{i} = sprintf('m%d', abs(d));
        else
            DamageCurves.col_names{i} = sprintf('p%d', d);
        end
    end
end

function [curve_depths, struct_curve, cont_curve] = get_damage_curves(DC, soid, flood_zone)
    curve_depths = DC.depth_points;
    struct_curve = [];
    cont_curve = [];
    
    z = upper(string(strtrim(flood_zone)));
    if z == "A" || z == "AE" || z == "AH" || z == "AO" || contains(z, "COASTAL A")
        hazCol = 'HazardCA';
    elseif z == "V" || z == "VE" || contains(z, "COASTAL V")
        hazCol = 'HazardCV';
    elseif z == "R" || contains(z, "RIVERINE")
        hazCol = 'HazardR';
    else
        hazCol = 'HazardCA';
    end
    
    soid = strtrim(char(soid));
    
    % Structure curve
    rowsS = strcmp(DC.StructMap.SOccupId, soid) & (DC.StructMap.(hazCol) == 1);
    if any(rowsS)
        struct_fn_id = DC.StructMap.BldgDmgFnId(find(rowsS, 1));
        fn_row = DC.StructFn.BldgDmgFnId == struct_fn_id;
        if any(fn_row)
            struct_curve = zeros(1, 29);
            for i = 1:29
                col = DC.col_names{i};
                if ismember(col, DC.StructFn.Properties.VariableNames)
                    struct_curve(i) = DC.StructFn{fn_row, col};
                end
            end
        end
    end
    
    % Content curve
    rowsC = strcmp(DC.ContMap.SOccupId, soid) & (DC.ContMap.(hazCol) == 1);
    if any(rowsC)
        cont_fn_id = DC.ContMap.ContDmgFnId(find(rowsC, 1));
        fn_row = DC.ContFn.ContDmgFnId == cont_fn_id;
        if any(fn_row)
            cont_curve = zeros(1, 29);
            for i = 1:29
                col = DC.col_names{i};
                if ismember(col, DC.ContFn.Properties.VariableNames)
                    cont_curve(i) = DC.ContFn{fn_row, col};
                end
            end
        end
    end
    
    if isempty(cont_curve) && ~isempty(struct_curve)
        % Structure curve was found but content curve was not. Fall back
        % to a zero content curve so the model can still run, but be loud
        % about it — silently zeroing content damages can underestimate
        % total damages substantially.
        warning('get_damage_curves:NoContentCurve', ...
            ['No content damage curve found for SOID=%s, hazard=%s. ', ...
             'Content damages will be zeroed for this building. ', ...
             'Add a row to flBldgContDmgFinal.csv if this is wrong.'], ...
            soid, hazCol);
        cont_curve = zeros(1, 29);
    end
end

function Dmg = compute_building_damages_vectorized(WL_P50, WL_P90, FFE, BFE, H, util_cost, ...
                                                    struct_val, cont_val, ...
                                                    curve_depths, struct_curve, cont_curve, ...
                                                    dfe_status, ACTION_NAMES, ...
                                                    apply_manuf_restriction, has_basement, ...
                                                    is_manuf_home, elev_min_shortfall_ft, ...
                                                    is_one_story, one_story_add_story)
    % is_one_story / one_story_add_story (optional, v13): control the WFP
    % 1st action for one-story buildings. When is_one_story is true and
    % one_story_add_story is true, WFP 1st is an add-a-story conversion:
    % the living floor moves up to FFE + H and is scored with the Elevate
    % transform at that height. When is_one_story is true and
    % one_story_add_story is false, WFP 1st returns baseline (excluded).
    % Multi-story buildings keep the original WFP 1st transform.
    % v14: the caller passes one_story_add_story = ONE_STORY_ADD_STORY &&
    % can_add_story_fn(soid), i.e. true only for homes (RES1/RES3), and
    % is_one_story from is_one_story_fn(n_st, soid).
    %
    % apply_manuf_restriction (optional, default false): when true, the
    % WFP Basement and WFP 1st Floor actions are forced to equal the
    % baseline. Used for RES2/RES4 buildings in locations where the
    % manufactured/temporary retrofit restriction is active. For RES2
    % (is_manuf_home == true) the Raise Utilities action is additionally
    % forced to baseline, leaving Elevate as the only beneficial action.
    % The Elevate action is gated by the elevation-eligibility rule below
    % (FFE < DFE - elev_min_shortfall_ft), which already returns baseline
    % for homes at or above that threshold.
    %
    % ACTION_NAMES is required (the function looks up which slice of the
    % 3rd dimension corresponds to each named action). Position of an
    % action in ACTION_NAMES no longer matters as long as the five
    % expected names are present.
    % has_basement (optional, default false): when true, the WFP Basement
    % action subtracts dmg_at_0 from baseline damage (the documented
    % "remove basement-level damage" transformation). When false, WFP B
    % is a no-op: damage equals baseline for every event, on the principle
    % that you cannot wet-floodproof a basement that does not exist. The
    % caller is expected to derive this from the NSI's basement column
    % (foundation_type == 'B'), which is the authoritative source for
    % "does this building have a true basement". Defaulting to false here
    % is conservative — if a caller forgets to pass the flag, WFP B
    % returns baseline rather than silently applying the subtraction.
    %
    % NOTE on the off-by-one: this function declares 15 parameters
    % (WL_P50 .. has_basement). ACTION_NAMES is the 13th parameter,
    % apply_manuf_restriction is the 14th, and has_basement is the 15th.
    % Earlier defensive checks used `nargin < 14` / `nargin < 15`, which
    % was a leftover from when the function carried 15 parameters with
    % apply_manuf_restriction as the trailing one; the second check was
    % then unconditionally true and silently reset apply_manuf_restriction
    % to false on every call, defeating the RES2/RES4 manufactured/
    % temporary-housing retrofit safeguard.
    if nargin < 13
        error('compute_building_damages_vectorized: ACTION_NAMES is required.');
    end
    if nargin < 14 || isempty(apply_manuf_restriction)
        apply_manuf_restriction = false;
    end
    if nargin < 15 || isempty(has_basement)
        has_basement = false;
    end
    % is_manuf_home (optional, default false): true only for RES2 (mobile /
    % manufactured homes). When the manufactured/temporary restriction is
    % active AND this is true, the Raise Utilities action is forced to
    % baseline in addition to WFP B and WFP 1st — a mobile home has no
    % upper floor to relocate utilities to, so elevation is its only
    % beneficial retrofit. RES4 (temporary lodging) leaves is_manuf_home
    % false, so Raise Utilities still applies to it.
    if nargin < 16 || isempty(is_manuf_home)
        is_manuf_home = false;
    end
    % elev_min_shortfall_ft (optional, default 2): minimum shortfall below
    % the DFE required for the Elevate action to deliver any benefit. The
    % Elevate eligibility gate is FFE < (BFE + 2) - elev_min_shortfall_ft.
    % With the default 2, that is FFE < BFE, i.e. homes at or above BFE
    % get baseline for Elevate (no benefit). Set to 0 to elevate any home
    % below the DFE (the pre-v12 behaviour).
    if nargin < 17 || isempty(elev_min_shortfall_ft)
        elev_min_shortfall_ft = 2;
    end
    % is_one_story (optional, default false): false keeps the original
    % multi-story WFP 1st transform. The caller passes the SOID-based flag
    % for every building (v14). one_story_add_story (optional, default
    % true): whether THIS building may add a story (v14: the script passes
    % ONE_STORY_ADD_STORY && can_add_story_fn(soid)).
    if nargin < 18 || isempty(is_one_story)
        is_one_story = false;
    end
    if nargin < 19 || isempty(one_story_add_story)
        one_story_add_story = true;
    end

    % NOTE (v12): dfe_status is retained in the signature for call
    % compatibility and reporting, but it no longer gates the Elevate
    % action. Elevation eligibility is now decided by elev_eligible
    % (FFE < DFE - elev_min_shortfall_ft), computed below. The DFE-status
    % buckets are still derived from dfe_status by the caller.

    % Resolve action indices by NAME, not position. This makes the math
    % below independent of the order of ACTION_NAMES — reorder ACTION_NAMES
    % freely without re-touching this function.
    nAc = numel(ACTION_NAMES);
    ix_baseline = action_index(ACTION_NAMES, "No mitigation");
    ix_raise    = action_index(ACTION_NAMES, "Raise Utilities");
    ix_wfp_b    = action_index(ACTION_NAMES, "WFP B");
    ix_elev     = action_index(ACTION_NAMES, "Elevate");
    ix_wfp_1    = action_index(ACTION_NAMES, "WFP 1st");

    [nYears, nMC] = size(WL_P50);
    nSc = 2;
    Dmg = zeros(nYears, nMC, nAc, nSc);

    FFE_elevated = BFE + 2;

    % Elevation-eligibility gate (v12): only advise elevation when the
    % current FFE is more than elev_min_shortfall_ft below the DFE. The
    % batch run passes the per-location value from LOC_CONFIG (v14); the
    % function default of 2 ft would give FFE < BFE. FFE and BFE are
    % scalars, so this is resolved once for the building.
    elev_eligible = FFE < (FFE_elevated - elev_min_shortfall_ft);

    struct_pct_at_0 = interp1(curve_depths, struct_curve, 0, 'linear');
    cont_pct_at_0   = interp1(curve_depths, cont_curve,   0, 'linear');
    dmg_at_0 = (struct_pct_at_0 / 100) * struct_val + (cont_pct_at_0 / 100) * cont_val;

    struct_pct_at_H = interp1(curve_depths, struct_curve, H, 'linear');
    cont_pct_at_H   = interp1(curve_depths, cont_curve,   H, 'linear');
    dmg_at_H = (struct_pct_at_H / 100) * struct_val + (cont_pct_at_H / 100) * cont_val;

    for si = 1:nSc
        if si == 1
            WL = WL_P50;
        else
            WL = WL_P90;
        end

        % Baseline depths
        depths_baseline = WL - FFE;
        depths_clamped = max(-4, min(24, depths_baseline));

        struct_pct = interp1(curve_depths, struct_curve, depths_clamped, 'linear');
        cont_pct   = interp1(curve_depths, cont_curve,   depths_clamped, 'linear');

        struct_pct(depths_baseline <= -4) = 0;
        cont_pct(depths_baseline <= -4)   = 0;
        struct_pct(depths_baseline >= 24) = struct_curve(end);
        cont_pct(depths_baseline >= 24)   = cont_curve(end);

        struct_pct = max(0, min(100, struct_pct));
        cont_pct   = max(0, min(100, cont_pct));

        dmg_baseline = (struct_pct / 100) .* struct_val + (cont_pct / 100) .* cont_val;

        % No mitigation (baseline)
        Dmg(:, :, ix_baseline, si) = dmg_baseline;

        % Raise Utilities
        Dmg(:, :, ix_raise, si) = max(0, dmg_baseline - util_cost);

        % WFP Basement — only physically meaningful when the building has
        % a true basement (foundation_type == 'B' in the NSI). For every
        % other foundation type (slab, crawl, pier) the action is a no-op
        % because there is no basement to wet-floodproof; the documented
        % "subtract dmg_at_0" transformation would otherwise just delete
        % whatever the FAST curve's p0 value happens to be, which for
        % no-basement SOIDs is a representation of threshold first-floor
        % damage rather than basement contribution.
        if has_basement
            dmg_wfp_b = dmg_baseline - dmg_at_0;
            dmg_wfp_b(depths_baseline <= 0) = 0;
            Dmg(:, :, ix_wfp_b, si) = max(0, dmg_wfp_b);
        else
            Dmg(:, :, ix_wfp_b, si) = dmg_baseline;
        end

        % Elevate to BFE+2 — only beneficial when the home is elevation-
        % eligible (FFE more than elev_min_shortfall_ft below the DFE).
        % Homes at or above DFE - elev_min_shortfall_ft are not advised
        % to elevate.
        if elev_eligible
            depths_elevated = WL - FFE_elevated;
            depths_elev_clamped = max(-4, min(24, depths_elevated));

            struct_pct_elev = interp1(curve_depths, struct_curve, depths_elev_clamped, 'linear');
            cont_pct_elev   = interp1(curve_depths, cont_curve,   depths_elev_clamped, 'linear');

            struct_pct_elev(depths_elevated <= -4) = 0;
            cont_pct_elev(depths_elevated <= -4)   = 0;
            struct_pct_elev(depths_elevated >= 24) = struct_curve(end);
            cont_pct_elev(depths_elevated >= 24)   = cont_curve(end);

            struct_pct_elev = max(0, min(100, struct_pct_elev));
            cont_pct_elev   = max(0, min(100, cont_pct_elev));

            dmg_elevated = (struct_pct_elev / 100) .* struct_val + (cont_pct_elev / 100) .* cont_val;

            dmg_action_elev = dmg_elevated - dmg_at_0;
            dmg_action_elev(depths_elevated <= 0) = 0;
            Dmg(:, :, ix_elev, si) = max(0, dmg_action_elev);
        else
            % Not elevation-eligible (shortfall below the DFE is not more
            % than elev_min_shortfall_ft): no benefit -> baseline.
            Dmg(:, :, ix_elev, si) = dmg_baseline;
        end

        % WFP 1st Floor (abandon the first floor)
        if is_one_story && one_story_add_story
            % One-story add-a-story conversion (v13). A new story is built
            % on top and the living space and contents move up one story
            % height, so the new living floor sits at FFE + H. The
            % abandoned ground floor is wet floodproofed (no damage below
            % H, as for multi-story WFP 1st). Scored with the Elevate
            % transform at the new floor: curve(WL - FFE_new) minus
            % damage-at-0, and zero while water stays below FFE_new.
            FFE_conv = FFE + H;
            depths_conv = WL - FFE_conv;
            depths_conv_clamped = max(-4, min(24, depths_conv));

            struct_pct_conv = interp1(curve_depths, struct_curve, depths_conv_clamped, 'linear');
            cont_pct_conv   = interp1(curve_depths, cont_curve,   depths_conv_clamped, 'linear');

            struct_pct_conv(depths_conv <= -4) = 0;
            cont_pct_conv(depths_conv <= -4)   = 0;
            struct_pct_conv(depths_conv >= 24) = struct_curve(end);
            cont_pct_conv(depths_conv >= 24)   = cont_curve(end);

            struct_pct_conv = max(0, min(100, struct_pct_conv));
            cont_pct_conv   = max(0, min(100, cont_pct_conv));

            dmg_conv = (struct_pct_conv / 100) .* struct_val + (cont_pct_conv / 100) .* cont_val;

            dmg_action_conv = dmg_conv - dmg_at_0;
            dmg_action_conv(depths_conv <= 0) = 0;
            Dmg(:, :, ix_wfp_1, si) = max(0, dmg_action_conv);
        elseif is_one_story
            % One-story building that cannot add a story (switch off, or
            % not a home, v14): there is no upper floor to move to, so WFP
            % 1st gives no benefit.
            Dmg(:, :, ix_wfp_1, si) = dmg_baseline;
        else
            % Multi-story building: remove the damage up to one story
            % height (the abandoned, wet-floodproofed first floor).
            dmg_wfp_1 = dmg_baseline - dmg_at_H;
            dmg_wfp_1(depths_baseline <= H) = 0;
            Dmg(:, :, ix_wfp_1, si) = max(0, dmg_wfp_1);
        end
    end

    % --- Manufactured/temporary retrofit restriction --------------------
    % For RES2 / RES4 in flagged locations, wet floodproofing the
    % structure shell is not physically meaningful, so WFP Basement and
    % WFP 1st Floor are forced equal to the baseline -- i.e. zero
    % retrofit benefit.
    %
    % For RES2 specifically (is_manuf_home), Raise Utilities is ALSO
    % forced to baseline (v12): a mobile home has no upper floor to
    % relocate utilities to, so elevation is the only retrofit that makes
    % physical sense. RES4 (temporary lodging) keeps Raise Utilities,
    % since a multi-floor lodging can move utilities upstairs.
    %
    % The Elevate action is intentionally left alone here: it is already
    % gated by the elevation-eligibility rule (baseline for
    % FFE >= DFE - elev_min_shortfall_ft), which is exactly the "do not
    % advise elevating homes near the DFE" requirement.
    %
    % This override runs after the one-story add-a-story conversion (v13),
    % so a one-story RES2/RES4 still gets baseline for WFP 1st: a mobile
    % home or temporary lodging cannot add a story.
    if apply_manuf_restriction
        Dmg(:, :, ix_wfp_b, :) = Dmg(:, :, ix_baseline, :);   % WFP B   -> baseline
        Dmg(:, :, ix_wfp_1, :) = Dmg(:, :, ix_baseline, :);   % WFP 1st -> baseline
        if is_manuf_home
            Dmg(:, :, ix_raise, :) = Dmg(:, :, ix_baseline, :);  % RES2: Raise Utilities -> baseline
        end
    end
end

function ix = action_index(ACTION_NAMES, target_name)
% ACTION_INDEX  Return the position of target_name in ACTION_NAMES.
% Errors if not found (with a list of what IS available, to make
% mistyped action names fail loudly with an actionable message).
    ix = find(string(ACTION_NAMES) == string(target_name), 1);
    if isempty(ix)
        error(['compute_building_damages_vectorized: required action ', ...
               '"%s" not found in ACTION_NAMES. ACTION_NAMES = {%s}.'], ...
               target_name, strjoin(cellstr(string(ACTION_NAMES(:))), ', '));
    end
end

function [soid, fallback_used] = convert_NSI_to_SOID(nsi_occupancy, foundation_type_code, num_stories)
    % Returns:
    %   soid           - FAST SOID string (e.g. 'R11N', 'C1LB')
    %   fallback_used  - true if the NSI occupancy did not match any known
    %                    prefix and we substituted a RES1 classification
    %                    (caller is expected to log this for transparency).
    %                    false on a normal match.
    fallback_used = false;
    if isstring(nsi_occupancy) || ischar(nsi_occupancy)
        nsi_occupancy = char(nsi_occupancy);
    else
        nsi_occupancy = char(string(nsi_occupancy));
    end
    nsi_occupancy = upper(strtrim(nsi_occupancy));
    
    fcode = upper(string(foundation_type_code));
    if fcode == "B" || contains(nsi_occupancy, 'WB', 'IgnoreCase', true)
        basement = 'B';
    else
        basement = 'N';
    end
    
    if num_stories <= 2, height_cat = 'L';
    elseif num_stories <= 5, height_cat = 'M';
    else, height_cat = 'H';
    end
    
    % RES1
    if startsWith(nsi_occupancy, 'RES1')
        base = 'R1';
        if contains(nsi_occupancy, '-1S'), stories = '1';
        elseif contains(nsi_occupancy, '-2S'), stories = '2';
        elseif contains(nsi_occupancy, '-3S'), stories = '3';
        elseif contains(nsi_occupancy, 'SL'), stories = 'S';
        else, stories = num2str(min(3, max(1, round(num_stories))));
        end
        soid = [base, stories, basement]; return;
    end
    % RES2
    if startsWith(nsi_occupancy, 'RES2')
        soid = ['R21', basement]; return;
    end
    % RES3
    if startsWith(nsi_occupancy, 'RES3')
        if contains(nsi_occupancy,'RES3A') || contains(nsi_occupancy,'3A'), base='R3A';
        elseif contains(nsi_occupancy,'RES3B') || contains(nsi_occupancy,'3B'), base='R3B';
        elseif contains(nsi_occupancy,'RES3C') || contains(nsi_occupancy,'3C'), base='R3C';
        elseif contains(nsi_occupancy,'RES3D') || contains(nsi_occupancy,'3D'), base='R3D';
        elseif contains(nsi_occupancy,'RES3E') || contains(nsi_occupancy,'3E'), base='R3E';
        elseif contains(nsi_occupancy,'RES3F') || contains(nsi_occupancy,'3F'), base='R3F';
        else, base='R3A';
        end
        if num_stories <= 2, stories='1'; elseif num_stories <= 4, stories='3'; else, stories='5'; end
        soid = [base, stories, basement]; return;
    end
    % RES4-6
    if startsWith(nsi_occupancy, 'RES4'), soid = ['R4', height_cat, basement]; return; end
    if startsWith(nsi_occupancy, 'RES5'), soid = ['R5', height_cat, basement]; return; end
    if startsWith(nsi_occupancy, 'RES6'), soid = ['R6', height_cat, basement]; return; end
    
    % COM (check COM10 before COM1)
    if startsWith(nsi_occupancy, 'COM10'), soid = ['C10', height_cat, basement]; return; end
    if startsWith(nsi_occupancy, 'COM1'), soid = ['C1', height_cat, basement]; return; end
    if startsWith(nsi_occupancy, 'COM2'), soid = ['C2', height_cat, basement]; return; end
    if startsWith(nsi_occupancy, 'COM3'), soid = ['C3', height_cat, basement]; return; end
    if startsWith(nsi_occupancy, 'COM4'), soid = ['C4', height_cat, basement]; return; end
    if startsWith(nsi_occupancy, 'COM5'), soid = ['C5', height_cat, basement]; return; end
    if startsWith(nsi_occupancy, 'COM6'), soid = ['C6', height_cat, basement]; return; end
    if startsWith(nsi_occupancy, 'COM7'), soid = ['C7', height_cat, basement]; return; end
    if startsWith(nsi_occupancy, 'COM8'), soid = ['C8', height_cat, basement]; return; end
    if startsWith(nsi_occupancy, 'COM9'), soid = ['C9', height_cat, basement]; return; end
    
    % IND
    if startsWith(nsi_occupancy, 'IND1'), soid = ['I1', height_cat, basement]; return; end
    if startsWith(nsi_occupancy, 'IND2'), soid = ['I2', height_cat, basement]; return; end
    if startsWith(nsi_occupancy, 'IND3'), soid = ['I3', height_cat, basement]; return; end
    if startsWith(nsi_occupancy, 'IND4'), soid = ['I4', height_cat, basement]; return; end
    if startsWith(nsi_occupancy, 'IND5'), soid = ['I5', height_cat, basement]; return; end
    if startsWith(nsi_occupancy, 'IND6'), soid = ['I6', height_cat, basement]; return; end
    
    % AGR
    if startsWith(nsi_occupancy, 'AGR1') || startsWith(nsi_occupancy, 'AGR')
        soid = ['A1', height_cat, basement]; return;
    end
    % GOV
    if startsWith(nsi_occupancy, 'GOV1'), soid = ['G1', height_cat, basement]; return; end
    if startsWith(nsi_occupancy, 'GOV2'), soid = ['G2', height_cat, basement]; return; end
    % EDU
    if startsWith(nsi_occupancy, 'EDU1'), soid = ['E1', height_cat, basement]; return; end
    if startsWith(nsi_occupancy, 'EDU2'), soid = ['E2', height_cat, basement]; return; end
    % REL
    if startsWith(nsi_occupancy, 'REL1') || startsWith(nsi_occupancy, 'REL')
        soid = ['RE1', height_cat, basement]; return;
    end
    
    % Fallback: unknown / blank occupancy type.
    % Instead of returning an empty SOID and skipping the building, we
    % reclassify it as RES1 using the same logic as the RES1 branch above
    % but without the '-1S' / '-2S' / '-3S' / 'SL' suffix matching (those
    % are RES1-specific tokens that aren't meaningful here). The story
    % digit is derived from num_stories, clamped to 1..3 so the RES1
    % SOID set always resolves to a valid curve. The basement flag is
    % the same as for any other occupancy. The caller receives
    % fallback_used = true so the substitution can be logged.
    warning('convert_NSI_to_SOID:fallbackToRES1', ...
        ['Unknown occupancy type "%s" — reclassified as RES1 with ', ...
         '%d story/stories, basement=%s. Building will be processed; ', ...
         'see <LOC>_occupancy_fallback.csv for the full list.'], ...
        nsi_occupancy, min(3, max(1, round(num_stories))), basement);
    fallback_used = true;
    stories = num2str(min(3, max(1, round(num_stories))));
    soid = ['R1', stories, basement];
end


function tf = is_manuf_or_temp(occ_char)
% IS_MANUF_OR_TEMP  True if NSI occupancy is manufactured housing (RES2)
% or temporary lodging (RES4). Robust to char / string / cell / categorical.
% Matches the prefix rule used elsewhere in this file (e.g. startsWith
% 'RES2' covers any RES2 sub-type such as 'RES2A'; same for 'RES4').
    if isempty(occ_char)
        tf = false; return;
    end
    if iscell(occ_char)
        occ_char = occ_char{1};
    end
    if isstring(occ_char) || ischar(occ_char)
        occ_str = char(occ_char);
    else
        occ_str = char(string(occ_char));
    end
    occ_str = upper(strtrim(occ_str));
    tf = startsWith(occ_str, 'RES2') || startsWith(occ_str, 'RES4');
end

function tf = is_manuf_home_fn(occ_char)
% IS_MANUF_HOME_FN  True if NSI occupancy is manufactured / mobile housing
% (RES2) specifically — NOT temporary lodging (RES4). Used to apply the
% RES2-only Raise Utilities suppression (a mobile home has no upper floor
% to relocate utilities to). Robust to char / string / cell / categorical.
    if isempty(occ_char)
        tf = false; return;
    end
    if iscell(occ_char)
        occ_char = occ_char{1};
    end
    if isstring(occ_char) || ischar(occ_char)
        occ_str = char(occ_char);
    else
        occ_str = char(string(occ_char));
    end
    occ_str = upper(strtrim(occ_str));
    tf = startsWith(occ_str, 'RES2');
end

function tf = is_one_story_fn(n_st, soid)
% IS_ONE_STORY_FN  True if the building is one-story.
% v14: when a SOID is given and it is a RES1 SOID (R1 + story digit +
% basement flag), the story digit decides: R11x is one-story, R12x/R13x
% and split level R1Sx are not. This is the digit that picked the FAST
% curve, which convert_NSI_to_SOID takes from the -1S/-2S/-3S suffix
% before falling back to number_of_stories, so the flag can no longer
% disagree with the curve. RES1-fallback SOIDs (unknown occupancy) follow
% the same rule. Every other class uses round(n_st) <= 1. Non-finite or
% non-positive counts return false (those rows are skipped upstream as
% invalid_data).
    if nargin > 1 && ~isempty(soid)
        s = strtrim(char(string(soid)));
        if numel(s) >= 3 && startsWith(s, 'R1')
            tf = (s(3) == '1');
            return;
        end
    end
    if isempty(n_st) || ~isnumeric(n_st) || ~isfinite(n_st(1)) || n_st(1) <= 0
        tf = false; return;
    end
    tf = round(n_st(1)) <= 1;
end

function tf = can_add_story_fn(soid)
% CAN_ADD_STORY_FN  True if a one-story building of this SOID may be
% scored with the add-a-story conversion under WFP 1st (v14). Limited to
% homes: RES1 single-family (R1..) and RES3 multi-family (R3..) SOIDs.
% RES2/RES4 are also blocked by the manufactured/temporary restriction;
% RES5/RES6 and all non-residential classes return false, so their
% one-story buildings get baseline for WFP 1st. ('RE1' is REL, not RES1.)
    s = strtrim(char(string(soid)));
    tf = startsWith(s, 'R1') || startsWith(s, 'R3');
end

function C = location_config()
% LOCATION_CONFIG  Per-location settings shared by the batch run and the
% embedded FFF estimator (v14).
%   BFE                - base flood elevation, ft NAVD88 (DFE = BFE + 2)
%   ElevMinShortfallFT - elevation is advised only when
%                        FFE < DFE - ElevMinShortfallFT
    C = struct();
    C.Shinnecock  = struct('BFE', 9, 'ElevMinShortfallFT', 1);
    C.MasticBeach = struct('BFE', 8, 'ElevMinShortfallFT', 0);
    C.Pamunkey    = struct('BFE', 7, 'ElevMinShortfallFT', 0);
end

%% ========================= PERCENTILE CSV OUTPUTS ======================
% The percentile snapshot is written as plain CSVs (one per topic, one
% set per location, all into the shared RRR Results/ folder). Pure MATLAB
% (writetable) — no Excel installation required.
%
% Files emitted per location (see save_results_csvs for details):
%   <LOC>_metadata.csv, <LOC>_bldg_lookup.csv,
%   <LOC>_bldg_CumulativeDamage.csv, <LOC>_CumulativeDamage_categories.csv

function save_results_csvs(out_dir, location, R)
% SAVE_RESULTS_CSVS  Write the slim GitHub-bound CSVs for one location.
% Inputs are the percentile-form MC_Results struct (see mc_to_pct).
%
% Files written into out_dir, prefixed by the location name:
%   <LOC>_metadata.csv           - location/BFE/scenario labels +
%                                  percentile list (key/value layout)
%   <LOC>_bldg_lookup.csv        - one row per building, columns:
%                                  BuildingID, Longitude, Latitude,
%                                  FFE_ft, DFE_Status
%   <LOC>_bldg_CumulativeDamage.csv        - per-building cumulative damage percentiles
%                                  (BuildingID x TargetYear x Action x SLR
%                                   + 23 percentile columns)
%   <LOC>_CumulativeDamage_categories.csv  - aggregate cumulative damage percentiles
%                                  for the four occupancy x DFE-status
%                                  sub-categories (RES_UnderDFE, RES_AboveDFE,
%                                  NONRES_UnderDFE, NONRES_AboveDFE)
%
% Not emitted as CSVs by design (kept GitHub-friendly): Damages_agg
% (the year-by-year aggregate damage timeline), WL_P50/WL_P90 (raw
% water-level inputs — these already exist as the DDD___*.csv input
% files), the full bldg_NSI_table (all original NSI columns), and the
% ALL/RES/NONRES roll-up categories. The four CSVs cover the typical
% downstream use case.

    if ~exist(out_dir, 'dir'); mkdir(out_dir); end

    pcts    = pct_set();
    pct_hdr = arrayfun(@(p) sprintf('P%02d', p), pcts, 'UniformOutput', false);
    nPct    = numel(pcts);
    nBldg   = R.nBldgProcessed;
    nAc     = numel(R.ACTION_NAMES);
    nSc     = numel(R.SCENARIOS);
    nTY     = numel(R.TARGET_YEARS);

    % Display labels for the TargetYear column. Fall back to numeric
    % strings if the caller's MC_Results struct doesn't carry
    % TARGET_YEAR_LABELS for any reason.
    if isfield(R, 'TARGET_YEAR_LABELS') && numel(R.TARGET_YEAR_LABELS) == nTY
        ty_labels = string(R.TARGET_YEAR_LABELS(:));
    else
        ty_labels = arrayfun(@(y) sprintf('%d', y), R.TARGET_YEARS(:), ...
                             'UniformOutput', false);
        ty_labels = string(ty_labels);
    end

    t0 = tic;

    % ----- 1. Metadata --------------------------------------------------
    t = tic;
    meta_keys = {'LOCATION'; 'BFE_FT_NAVD88'; 'nBldgProcessed'; ...
                 'n_res'; 'n_nonres'; 'n_occupancy_fallback'; ...
                 'TARGET_YEARS'; 'TARGET_YEAR_LABELS'; ...
                 'ACTIONS'; 'ACTION_NAMES'; 'SCENARIOS'; ...
                 'SCENARIO_LABELS'; 'PERCENTILES'; 'RESTRICT_MANUF_LOC'; ...
                 'ELEV_MIN_SHORTFALL_FT'; 'ONE_STORY_ADD_STORY'};
    if isfield(R, 'ELEV_MIN_SHORTFALL_FT')
        elev_short_str = sprintf('%g', R.ELEV_MIN_SHORTFALL_FT);
    else
        elev_short_str = '2';
    end
    if isfield(R, 'ONE_STORY_ADD_STORY')
        one_story_str = sprintf('%d', logical(R.ONE_STORY_ADD_STORY));
    else
        one_story_str = '0';
    end
    if isfield(R, 'RESTRICT_MANUF_LOC')
        restrict_loc_str = sprintf('%d', logical(R.RESTRICT_MANUF_LOC));
    else
        restrict_loc_str = '0';
    end
    if isfield(R, 'n_occupancy_fallback')
        n_fb_str = sprintf('%d', R.n_occupancy_fallback);
    else
        n_fb_str = '0';
    end
    meta_vals = { ...
        char(string(R.LOCATION)); ...
        sprintf('%g', R.BFE_FT_NAVD88); ...
        sprintf('%d', nBldg); ...
        sprintf('%d', R.n_res); ...
        sprintf('%d', R.n_nonres); ...
        n_fb_str; ...
        strjoin(arrayfun(@(x) sprintf('%g', x), R.TARGET_YEARS, ...
                         'UniformOutput', false), ', '); ...
        strjoin(cellstr(ty_labels),                    ', '); ...
        strjoin(arrayfun(@(x) sprintf('%g', double(x)), R.ACTIONS, ...
                         'UniformOutput', false), ', '); ...
        strjoin(cellstr(string(R.ACTION_NAMES(:))),    ', '); ...
        strjoin(cellstr(string(R.SCENARIOS(:))),       ', '); ...
        strjoin(cellstr(string(R.SCENARIO_LABELS(:))), ', '); ...
        strjoin(pct_hdr, ', '); ...
        restrict_loc_str; ...
        elev_short_str; ...
        one_story_str};
    meta_tbl = table(meta_keys, meta_vals, 'VariableNames', {'Key', 'Value'});
    metaPath = fullfile(out_dir, sprintf('%s_metadata.csv', location));
    writetable(meta_tbl, metaPath);
    fprintf('[CSV ]   %s_metadata.csv             %.2fs\n', location, toc(t));

    % ----- 2. bldg_lookup (per-building reference / join key) ----------
    % Includes everything an app or dashboard typically needs to filter,
    % map, group, or sanity-check per-building damage results without
    % having to load the full NSI input file.
    t = tic;
    has_nsi = isfield(R, 'bldg_NSI_table') && ~isempty(R.bldg_NSI_table);
    if has_nsi
        Tn = R.bldg_NSI_table;
        nsi_vars = Tn.Properties.VariableNames;
    else
        Tn = [];   % unused
        nsi_vars = {};
    end

    if has_nsi && ismember('id', nsi_vars)
        id_col = Tn.id;
    else
        id_col = R.bldg_IDs(:);
    end
    if has_nsi && ismember('longitude', nsi_vars)
        lon_col = Tn.longitude;
    else
        lon_col = nan(nBldg, 1);
    end
    if has_nsi && ismember('latitude', nsi_vars)
        lat_col = Tn.latitude;
    else
        lat_col = nan(nBldg, 1);
    end
    if has_nsi && ismember('occupancy_type', nsi_vars)
        occ_col = string(Tn.occupancy_type);
    else
        occ_col = string(R.bldg_occupancy);
    end

    % Occupancy-fallback flag per building (true => NSI occupancy was
    % unknown and the building was reclassified as RES1). Defensive
    % fallback to false for older MC_Results structs that don't carry
    % the field. Read this BEFORE computing OccupancyGroup, because
    % fallback'd buildings are counted as RES regardless of their
    % original (unknown) occupancy string.
    if isfield(R, 'bldg_occupancy_fallback') && numel(R.bldg_occupancy_fallback) == nBldg
        occfallback_col = logical(R.bldg_occupancy_fallback(:));
    else
        occfallback_col = false(nBldg, 1);
    end

    grp_col = strings(nBldg, 1);
    % A fallback'd building is treated as RES here, matching how the main
    % loop accumulates its damages (RES_underDFE / RES_aboveDFE buckets)
    % and how n_res / n_nonres are counted. The OccupancyType column
    % above still carries the ORIGINAL (unknown) string, so nothing is
    % lost — the user can always distinguish a real RES from a fallback
    % via the OccupancyFallback column.
    is_res_lookup = startsWith(upper(strtrim(occ_col(:))), "RES") | occfallback_col;
    grp_col(is_res_lookup)  = "RES";
    grp_col(~is_res_lookup) = "NONRES";
    if isfield(R, 'bldg_SOID') && numel(R.bldg_SOID) == nBldg
        soid_col = string(R.bldg_SOID(:));
    else
        soid_col = strings(nBldg, 1);
    end

    % Manufactured/temporary retrofit restriction flag per building.
    % Defensive: if the field is missing for any reason, emit "false"
    % for every row so the column always exists.
    if isfield(R, 'bldg_restrict_manuf') && numel(R.bldg_restrict_manuf) == nBldg
        restrict_col = logical(R.bldg_restrict_manuf(:));
    else
        restrict_col = false(nBldg, 1);
    end

    % Elevation-eligibility flag per building (true => elevation advised,
    % i.e. FFE < DFE - ELEV_MIN_SHORTFALL_FT). When false, the Elevate
    % action returns baseline. Defensive fallback to true (eligible) for
    % older MC_Results structs without the field, so the column always
    % exists without silently flipping any building to "not advised".
    if isfield(R, 'bldg_elev_eligible') && numel(R.bldg_elev_eligible) == nBldg
        elev_elig_col = logical(R.bldg_elev_eligible(:));
    else
        elev_elig_col = true(nBldg, 1);
    end

    % One-story add-a-story flag per building (v13): true => this
    % building's WFP 1st result is the add-a-story conversion (living floor
    % moved up to FFE + story height). Defensive fallback to false for
    % older MC_Results structs without the field.
    if isfield(R, 'bldg_one_story_addstory') && numel(R.bldg_one_story_addstory) == nBldg
        addstory_col = logical(R.bldg_one_story_addstory(:));
    else
        addstory_col = false(nBldg, 1);
    end

    look_tbl = table(id_col(:), lon_col(:), lat_col(:), ...
                     occ_col(:), grp_col(:), soid_col(:), ...
                     R.bldg_struct_val(:), R.bldg_cont_val(:), ...
                     R.bldg_gnd_ft(:), R.bldg_FFE(:), ...
                     string(R.bldg_DFEstatus(:)), restrict_col, ...
                     occfallback_col, elev_elig_col, addstory_col, ...
                     'VariableNames', ...
                     {'BuildingID','Longitude','Latitude', ...
                      'OccupancyType','OccupancyGroup','SOID', ...
                      'StructureValue','ContentValue', ...
                      'GroundElevation_ft','FFE_ft','DFE_Status', ...
                      'ManufRestriction','OccupancyFallback','ElevationEligible', ...
                      'OneStoryAddStory'});
    lookPath = fullfile(out_dir, sprintf('%s_bldg_lookup.csv', location));
    writetable(look_tbl, lookPath);
    fprintf('[CSV ]   %s_bldg_lookup.csv          (%d rows)   %.2fs\n', ...
        location, nBldg, toc(t));

    % ----- 3. CumulativeDamage_categories (4 occupancy x DFE-status categories) --
    t = tic;
    cat_labels = {'RES_UnderDFE','RES_AboveDFE','NONRES_UnderDFE','NONRES_AboveDFE'};
    cat_fields = {'CumulativeDamage_RES_underDFE','CumulativeDamage_RES_aboveDFE', ...
                  'CumulativeDamage_NONRES_underDFE','CumulativeDamage_NONRES_aboveDFE'};
    nCat = numel(cat_fields);
    nRC  = nCat * nTY * nAc * nSc;
    cat_col = cell(nRC, 1);
    ty_col  = strings(nRC, 1);
    act_col = cell(nRC, 1);
    slr_col = cell(nRC, 1);
    ce_vals = zeros(nRC, nPct);
    r = 0;
    for c = 1:nCat
        CumArr = R.(cat_fields{c});   % nTY x nPct x nAc x nSc
        for ti = 1:nTY
            for ai = 1:nAc
                for si = 1:nSc
                    r = r + 1;
                    cat_col{r}   = cat_labels{c};
                    ty_col(r)    = ty_labels(ti);
                    act_col{r}   = char(string(R.ACTION_NAMES(ai)));
                    slr_col{r}   = char(string(R.SCENARIO_LABELS(si)));
                    ce_vals(r,:) = squeeze(CumArr(ti, :, ai, si));
                end
            end
        end
    end
    ce_tbl = build_pct_table({'Category','TargetYear','Action','SLR'}, ...
                             {cat_col, ty_col, act_col, slr_col}, ...
                             pct_hdr, ce_vals);
    cePath = fullfile(out_dir, sprintf('%s_CumulativeDamage_categories.csv', location));
    writetable(ce_tbl, cePath);
    fprintf('[CSV ]   %s_CumulativeDamage_categories.csv    (%d rows)   %.2fs\n', ...
        location, nRC, toc(t));

    % ----- 4. bldg_CumulativeDamage (per-building x TargetYear x Action x SLR) ----
    t = tic;
    nRB = nBldg * nTY * nAc * nSc;
    bid_col  = cell(nRB, 1);
    bty_col  = strings(nRB, 1);
    bact_col = cell(nRB, 1);
    bslr_col = cell(nRB, 1);
    bc_vals  = zeros(nRB, nPct);
    r = 0;
    for bi = 1:nBldg
        if iscell(R.bldg_IDs), bid = R.bldg_IDs{bi}; else, bid = R.bldg_IDs(bi); end
        bid_str = char(string(bid));
        for ti = 1:nTY
            for ai = 1:nAc
                for si = 1:nSc
                    r = r + 1;
                    bid_col{r}    = bid_str;
                    bty_col(r)    = ty_labels(ti);
                    bact_col{r}   = char(string(R.ACTION_NAMES(ai)));
                    bslr_col{r}   = char(string(R.SCENARIO_LABELS(si)));
                    bc_vals(r, :) = squeeze(R.bldg_CumulativeDamage(bi, ti, si, ai, :))';
                end
            end
        end
    end
    bc_tbl = build_pct_table({'BuildingID','TargetYear','Action','SLR'}, ...
                             {bid_col, bty_col, bact_col, bslr_col}, ...
                             pct_hdr, bc_vals);
    bcPath = fullfile(out_dir, sprintf('%s_bldg_CumulativeDamage.csv', location));
    writetable(bc_tbl, bcPath);
    fprintf('[CSV ]   %s_bldg_CumulativeDamage.csv          (%d rows)   %.2fs\n', ...
        location, nRB, toc(t));

    fprintf('[CSV ] All CSVs for %s written in %.1fs total.\n', ...
        location, toc(t0));
end

function tbl = build_pct_table(meta_names, meta_cols, pct_hdr, pct_vals)
% BUILD_PCT_TABLE  Helper: assemble a MATLAB table from N meta columns
% (mixed types) plus M numeric percentile columns, all sharing the same
% row count. Used for the bldg_CumulativeDamage and CumulativeDamage_categories CSVs.
    nMeta = numel(meta_names);
    if numel(pct_hdr) ~= size(pct_vals, 2)
        error('build_pct_table: pct_hdr/pct_vals column count mismatch.');
    end
    var_names = [meta_names, pct_hdr];
    args = cell(1, nMeta + numel(pct_hdr));
    for i = 1:nMeta
        args{i} = meta_cols{i};
    end
    for j = 1:numel(pct_hdr)
        args{nMeta + j} = pct_vals(:, j);
    end
    tbl = table(args{:}, 'VariableNames', var_names);
end

function R_pct = mc_to_pct(R)
% MC_TO_PCT  Return a copy of MC_Results with the MC dimension reduced to
% a curated 23-column percentile set (see pct_set): P01..P10, P25, P50,
% P75, P90..P99 — heavy on the tails. Metadata and building-characteristic
% fields are passed through unchanged. bldg_CumulativeDamage_MC is renamed to
% bldg_CumulativeDamage to match the CSV column layout.
    PCTS = pct_set();
    R_pct = R;                                            % copy all scalars/metadata
    R_pct.WL_P50              = prctile(R.WL_P50,              PCTS, 2);
    R_pct.WL_P90              = prctile(R.WL_P90,              PCTS, 2);
    R_pct.Damages_agg         = prctile(R.Damages_agg,         PCTS, 2);
    R_pct.CumulativeDamage_ALL          = prctile(R.CumulativeDamage_ALL,          PCTS, 2);
    R_pct.CumulativeDamage_RES          = prctile(R.CumulativeDamage_RES,          PCTS, 2);
    R_pct.CumulativeDamage_NONRES       = prctile(R.CumulativeDamage_NONRES,       PCTS, 2);
    R_pct.CumulativeDamage_RES_underDFE     = prctile(R.CumulativeDamage_RES_underDFE,     PCTS, 2);
    R_pct.CumulativeDamage_RES_aboveDFE    = prctile(R.CumulativeDamage_RES_aboveDFE,    PCTS, 2);
    R_pct.CumulativeDamage_NONRES_underDFE  = prctile(R.CumulativeDamage_NONRES_underDFE,  PCTS, 2);
    R_pct.CumulativeDamage_NONRES_aboveDFE = prctile(R.CumulativeDamage_NONRES_aboveDFE, PCTS, 2);
    R_pct.bldg_CumulativeDamage         = prctile(R.bldg_CumulativeDamage_MC,      PCTS, 5);
    if isfield(R_pct, 'bldg_CumulativeDamage_MC')
        R_pct = rmfield(R_pct, 'bldg_CumulativeDamage_MC');
    end
end

function pcts = pct_set()
% PCT_SET  The trimmed 23-percentile set saved to the GitHub-bound CSVs.
% Heavy tails (P01..P10, P90..P99) plus the quartiles + median.
% Change here in one place to re-tune CSV file size.
    pcts = [1:10, 25, 50, 75, 90:99];
end

%% ========================= EMBEDDED PER-BUILDING ESTIMATOR ==============
% FFF___Damage_Estimator — single-building, single-water-level, single-
% action FAST damage estimator. Formerly a standalone file; embedded here
% in v12 as a local function so the occupancy->SOID logic and the damage
% model live in ONE place. It reuses this script's convert_NSI_to_SOID and
% therefore no longer carries a private SOID converter.
%
% v12 behaviour (kept in lock-step with compute_building_damages_vectorized):
%   - Elevate (action 4) is only beneficial when the current FFE is below
%     the elevation-eligibility threshold: FFE < (BFE+2) - ElevMinShortfallFT.
%     Homes at or above that threshold get baseline for the Elevate
%     action. (v14: the default threshold comes from 'Location'.)
%   - Manufactured/temporary retrofit restriction (opt 'ApplyManufRestriction'):
%       * RES2 (mobile homes): Raise Utilities, WFP Basement, WFP 1st Floor
%         all return baseline (only Elevate can help).
%       * RES4 (temporary lodging): WFP Basement and WFP 1st Floor return
%         baseline; Raise Utilities still applies.
%
% v13 behaviour (kept in lock-step with compute_building_damages_vectorized):
%   - WFP 1st (action 3) for a one-story building (round(N_stories) <= 1):
%       * 'OneStoryAddStory' true (default): add-a-story conversion. The
%         living floor moves to FFE + StoryHeightFT and the Elevate
%         transform is applied at that height (curve(depth) - curve(0),
%         zero while water stays below the new floor).
%       * 'OneStoryAddStory' false: baseline (excluded).
%     The manufactured/temporary restriction, when applied, still wins.
%
% v14 behaviour (kept in lock-step with the batch run):
%   - One-story test: is_one_story_fn(N_stories, soid), so for RES1 the
%     SOID story digit decides. The add-a-story conversion applies only
%     to RES1/RES3 SOIDs (can_add_story_fn); other one-story buildings get
%     baseline for WFP 1st.
%   - 'Location' (default 'Shinnecock') sets the default BFE and
%     ElevMinShortfallFT from location_config(). Passing 'BFE_FT_NAVD88'
%     or 'ElevMinShortfallFT' explicitly still overrides.
%
% Usage (name-value options after the 9 positional args):
%   out = FFF___Damage_Estimator(N_stories, F_type, occupancy_type, ...
%             ground_elevation_ft, foundation_height_ft, ...
%             WATER_LEVEL_FT_NAVD88, ANALYSIS_SWITCH, ...
%             structure_value_usd, content_value_usd, ...
%             'Location', 'Pamunkey', ...
%             'ApplyManufRestriction', true, 'OneStoryAddStory', true);
%
% ANALYSIS_SWITCH: 0 Baseline | 1 Raise Utilities | 2 WFP Basement |
%                  3 WFP 1st Floor | 4 Elevate (to BFE+2)
function out = FFF___Damage_Estimator(N_stories, F_type, occupancy_type, ground_elevation_ft, foundation_height_ft, WATER_LEVEL_FT_NAVD88, ANALYSIS_SWITCH, structure_value_usd, content_value_usd, varargin)

% --------------------- Options (name-value) ------------------------------
p = inputParser; p.CaseSensitive = false;
p.addParameter('StructMapFile', 'flBldgStructDmgFinal.csv', @(s)ischar(s)||isstring(s));
p.addParameter('StructFnFile',  'flBldgStructDmgFn.csv',   @(s)ischar(s)||isstring(s));
p.addParameter('ContMapFile',   'flBldgContDmgFinal.csv',  @(s)ischar(s)||isstring(s));
p.addParameter('ContFnFile',    'flBldgContDmgFn.csv',     @(s)ischar(s)||isstring(s));
p.addParameter('FloodZone', 'A', @(s)ischar(s)||isstring(s));
p.addParameter('UtilitiesReplCostUSD', 20000, @(x)isnumeric(x)&&isscalar(x)&&x>=0);
p.addParameter('StoryHeightFT', 10, @(x)isnumeric(x)&&isscalar(x)&&x>0);
% v14: empty default -> taken from location_config() for 'Location'.
p.addParameter('BFE_FT_NAVD88', [], @(x)isempty(x)||(isnumeric(x)&&isscalar(x)));
% v12 options:
p.addParameter('ElevMinShortfallFT', [], @(x)isempty(x)||(isnumeric(x)&&isscalar(x)&&x>=0));
p.addParameter('ApplyManufRestriction', false, @(x)islogical(x)||(isnumeric(x)&&isscalar(x)));
% v13 option:
p.addParameter('OneStoryAddStory', true, @(x)islogical(x)||(isnumeric(x)&&isscalar(x)));
% v14 option:
p.addParameter('Location', 'Shinnecock', @(s)ischar(s)||isstring(s));
p.parse(varargin{:}); opt = p.Results;
apply_manuf_restriction = logical(opt.ApplyManufRestriction);
one_story_add_story     = logical(opt.OneStoryAddStory);

% --------------------- Location defaults (v14) ---------------------------
LOCCFG   = location_config();
loc_name = char(opt.Location);
if ~isfield(LOCCFG, loc_name)
    error('FFF___Damage_Estimator: unknown Location "%s". Valid: %s.', ...
          loc_name, strjoin(fieldnames(LOCCFG)', ', '));
end
if isempty(opt.BFE_FT_NAVD88)
    opt.BFE_FT_NAVD88 = LOCCFG.(loc_name).BFE;
end
if isempty(opt.ElevMinShortfallFT)
    opt.ElevMinShortfallFT = LOCCFG.(loc_name).ElevMinShortfallFT;
end

% --------------------- One-time unit reminder ----------------------------
persistent UNITS_MSG_PRINTED
if isempty(UNITS_MSG_PRINTED) || ~UNITS_MSG_PRINTED
    fprintf('[FFF___Damage_Estimator] Reminder: all elevations/heights & water level inputs are in **feet** (NAVD88).\n');
    UNITS_MSG_PRINTED = true;
end

% --------------------- Structure and Content values from NSI -------------
if ~isfinite(structure_value_usd) || structure_value_usd < 0
    error('Invalid structure_value_usd. Must be a finite non-negative number.');
end
if ~isfinite(content_value_usd) || content_value_usd < 0
    error('Invalid content_value_usd. Must be a finite non-negative number.');
end
STRUCTURE_VALUE_USD = structure_value_usd;
CONTENT_VALUE_USD   = content_value_usd;

% --------------------- Geometry & baseline depth -------------------------
FFE_current = ground_elevation_ft + foundation_height_ft;
flood_depth_baseline_ft = WATER_LEVEL_FT_NAVD88 - FFE_current;

% --------------------- Occupancy class flags (v12) -----------------------
occ_up   = upper(strtrim(char(string(occupancy_type))));
is_manuf = startsWith(occ_up, 'RES2');   % mobile / manufactured home
is_temp  = startsWith(occ_up, 'RES4');   % temporary lodging

% --------------------- Elevation eligibility (v12) -----------------------
target_FFE       = opt.BFE_FT_NAVD88 + 2;                       % DFE (elevation target)
elev_eligible    = FFE_current < (target_FFE - opt.ElevMinShortfallFT);

% --------------------- Build SOID (shared converter) ---------------------
% Reuses this script's convert_NSI_to_SOID (single source of truth).
[soid, occupancy_fallback_used] = convert_NSI_to_SOID(occupancy_type, F_type, N_stories);

% --------------------- One-story flag (v13/v14) --------------------------
% Same rules as the batch model; needs the SOID, so it comes after it.
is_one_story = is_one_story_fn(N_stories, soid);
add_story_ok = can_add_story_fn(soid);         % homes only (RES1/RES3)
one_story_addstory_applied = false;            % set true in case 3 if used

% --------------------- Load mapping & curve tables -----------------------
try
    StructMap = readtable(opt.StructMapFile);
    StructFn  = readtable(opt.StructFnFile);
    ContMap   = readtable(opt.ContMapFile);
    ContFn    = readtable(opt.ContFnFile);
catch ME
    error('Failed to read mapping/function CSVs: %s', ME.message);
end

% --------------------- Main logic per action -----------------------------
analysis_case_name = "Unknown"; effective_depth = NaN; struct_pct = 0; cont_pct = 0;

switch ANALYSIS_SWITCH
    case 0  % Baseline
        analysis_case_name = "Baseline";
        effective_depth = flood_depth_baseline_ft;
        [struct_pct, cont_pct, ~, ~] = calc_damage_pcts(soid, opt.FloodZone, effective_depth, StructMap, StructFn, ContMap, ContFn);

    case 1  % Raise Utilities — % unchanged; $ deduction applied below
        analysis_case_name = "Raise Utilities";
        effective_depth = flood_depth_baseline_ft;
        [struct_pct, cont_pct, ~, ~] = calc_damage_pcts(soid, opt.FloodZone, effective_depth, StructMap, StructFn, ContMap, ContFn);

    case 2  % Wet Floodproofing — Basement (subtract portion up to 0 ft)
        analysis_case_name = "WetFP Basement";
        effective_depth = flood_depth_baseline_ft;
        if apply_manuf_restriction && (is_manuf || is_temp)
            % Manufactured/temporary shell: wet-floodproofing not meaningful
            % -> baseline (v12, matches AAA).
            [struct_pct, cont_pct, ~, ~] = calc_damage_pcts(soid, opt.FloodZone, effective_depth, StructMap, StructFn, ContMap, ContFn);
        else
            has_basement = strcmpi(strtrim(string(F_type)), "B");
            if ~has_basement
                [struct_pct, cont_pct, ~, ~] = calc_damage_pcts(soid, opt.FloodZone, effective_depth, StructMap, StructFn, ContMap, ContFn);
            elseif effective_depth <= 0
                struct_pct = 0; cont_pct = 0;
            else
                [pct_at_depth_s, pct_at_depth_c, sId, cId] = calc_damage_pcts(soid, opt.FloodZone, effective_depth, StructMap, StructFn, ContMap, ContFn);
                pct_at_p0_s = interp_damage_pct(sId, 0, StructFn, 'BldgDmgFnId');
                pct_at_p0_c = interp_damage_pct(cId, 0, ContFn,  'ContDmgFnId');
                struct_pct = pct_at_depth_s - pct_at_p0_s;
                cont_pct   = pct_at_depth_c - pct_at_p0_c;
            end
        end

    case 3  % Wet Floodproofing — First Floor (subtract portion up to one story)
        analysis_case_name = "WetFP 1st";
        effective_depth = flood_depth_baseline_ft;
        if apply_manuf_restriction && (is_manuf || is_temp)
            [struct_pct, cont_pct, ~, ~] = calc_damage_pcts(soid, opt.FloodZone, effective_depth, StructMap, StructFn, ContMap, ContFn);
        elseif is_one_story && one_story_add_story && add_story_ok
            % One-story add-a-story conversion (v13): new living floor at
            % FFE + H, scored with the Elevate transform at that height.
            % v14: homes only (RES1/RES3 SOIDs).
            one_story_addstory_applied = true;
            H = opt.StoryHeightFT;
            effective_depth = WATER_LEVEL_FT_NAVD88 - (FFE_current + H);
            if effective_depth <= 0
                struct_pct = 0; cont_pct = 0;
            else
                [pct_at_depth_s, pct_at_depth_c, sId, cId] = calc_damage_pcts(soid, opt.FloodZone, effective_depth, StructMap, StructFn, ContMap, ContFn);
                pct_at_p0_s = interp_damage_pct(sId, 0, StructFn, 'BldgDmgFnId');
                pct_at_p0_c = interp_damage_pct(cId, 0, ContFn,  'ContDmgFnId');
                struct_pct = pct_at_depth_s - pct_at_p0_s;
                cont_pct   = pct_at_depth_c - pct_at_p0_c;
            end
        elseif is_one_story
            % One-story that cannot add a story (switch off, or not a home,
            % v14): no upper floor -> baseline.
            [struct_pct, cont_pct, ~, ~] = calc_damage_pcts(soid, opt.FloodZone, effective_depth, StructMap, StructFn, ContMap, ContFn);
        else
            H = opt.StoryHeightFT;
            if effective_depth <= H
                struct_pct = 0; cont_pct = 0;
            else
                [pct_at_depth_s, pct_at_depth_c, sId, cId] = calc_damage_pcts(soid, opt.FloodZone, effective_depth, StructMap, StructFn, ContMap, ContFn);
                pct_at_H_s = interp_damage_pct(sId, H, StructFn, 'BldgDmgFnId');
                pct_at_H_c = interp_damage_pct(cId, H, ContFn,  'ContDmgFnId');
                struct_pct = pct_at_depth_s - pct_at_H_s;
                cont_pct   = pct_at_depth_c - pct_at_H_c;
            end
        end

    case 4  % Elevate House — to BFE + 2, only if elevation-eligible (v12)
        analysis_case_name = "Elevate House";
        if elev_eligible
            % Depth measured from the new (elevated) FFE.
            effective_depth = WATER_LEVEL_FT_NAVD88 - target_FFE;
            if effective_depth <= 0
                struct_pct = 0; cont_pct = 0;
            else
                [pct_at_depth_s, pct_at_depth_c, sId, cId] = calc_damage_pcts(soid, opt.FloodZone, effective_depth, StructMap, StructFn, ContMap, ContFn);
                pct_at_p0_s = interp_damage_pct(sId, 0, StructFn, 'BldgDmgFnId');
                pct_at_p0_c = interp_damage_pct(cId, 0, ContFn,  'ContDmgFnId');
                struct_pct = pct_at_depth_s - pct_at_p0_s;
                cont_pct   = pct_at_depth_c - pct_at_p0_c;
            end
        else
            % Not elevation-eligible (FFE >= DFE - ElevMinShortfallFT):
            % baseline, no benefit.
            effective_depth = flood_depth_baseline_ft;
            [struct_pct, cont_pct, ~, ~] = calc_damage_pcts(soid, opt.FloodZone, effective_depth, StructMap, StructFn, ContMap, ContFn);
        end

    otherwise
        error('ANALYSIS_SWITCH must be 0..4.');
end

% Clamp percentages to [0,100]
struct_pct = max(0, min(100, struct_pct));
cont_pct   = max(0, min(100, cont_pct));

% Dollarize
struct_usd = (struct_pct/100) * STRUCTURE_VALUE_USD;
cont_usd   = (cont_pct/100)   * CONTENT_VALUE_USD;
total_usd  = struct_usd + cont_usd;

% Action 1 utilities deduction — apply UNLESS this is a RES2 mobile home
% under the manufactured/temporary restriction (no upper floor to relocate
% utilities to => no benefit) (v12).
if ANALYSIS_SWITCH == 1 && ~(apply_manuf_restriction && is_manuf)
    saved = min(opt.UtilitiesReplCostUSD, total_usd);
    total_usd = max(0, total_usd - saved);
end

% Package outputs
out = struct();
out.Analysis_Case          = string(analysis_case_name);
out.Effective_Depth_ft     = effective_depth;
out.Struct_Dmg_Pct         = struct_pct;
out.Struct_Dmg_USD         = struct_usd;
out.Content_Dmg_Pct        = cont_pct;
out.Content_Dmg_USD        = cont_usd;
out.Total_Dmg_USD          = total_usd;
out.StructureValue_USD     = STRUCTURE_VALUE_USD;
out.ContentValue_USD       = CONTENT_VALUE_USD;
out.Occupancy_Fallback     = logical(occupancy_fallback_used);
out.Elevation_Eligible     = logical(elev_eligible);          % v12: FFE < DFE - shortfall
out.ManufRestriction_Applied = logical(apply_manuf_restriction && (is_manuf || is_temp)); % v12
out.OneStoryAddStory_Applied = logical(one_story_addstory_applied);                    % v13

end % ====== end FFF___Damage_Estimator ======

% ------------------------- FFF curve helpers -----------------------------
function [struct_pct, content_pct, struct_fn_id, content_fn_id] = calc_damage_pcts(soid, flood_zone, depth, struct_map, struct_fn, cont_map, cont_fn)
    soid = strtrim(char(soid));
    if iscell(struct_map.SOccupId)
        struct_map.SOccupId = cellfun(@strtrim, struct_map.SOccupId, 'UniformOutput', false);
    else
        struct_map.SOccupId = strtrim(struct_map.SOccupId);
    end
    if iscell(cont_map.SOccupId)
        cont_map.SOccupId = cellfun(@strtrim, cont_map.SOccupId, 'UniformOutput', false);
    else
        cont_map.SOccupId = strtrim(cont_map.SOccupId);
    end

    [colName, ~] = zone_to_column(flood_zone);

    struct_pct = 0; struct_fn_id = 0;
    rowsS = strcmp(struct_map.SOccupId, soid) & (struct_map.(colName) == 1);
    if any(rowsS)
        struct_fn_id = struct_map.BldgDmgFnId(find(rowsS,1));
        if any(struct_fn_id == struct_fn.BldgDmgFnId)
            struct_pct = interp_damage_pct(struct_fn_id, depth, struct_fn, 'BldgDmgFnId');
        end
    end

    content_pct = 0; content_fn_id = 0;
    rowsC = strcmp(cont_map.SOccupId, soid) & (cont_map.(colName) == 1);
    if any(rowsC)
        content_fn_id = cont_map.ContDmgFnId(find(rowsC,1));
        if any(content_fn_id == cont_fn.ContDmgFnId)
            content_pct = interp_damage_pct(content_fn_id, depth, cont_fn, 'ContDmgFnId');
        end
    end
end

function [colName, normZone] = zone_to_column(flood_zone)
    z = upper(string(strtrim(flood_zone)));
    if z=="A" || z=="AE" || z=="AH" || z=="AO" || contains(z,"COASTAL A")
        colName = 'HazardCA'; normZone = 'A/Coastal A';
    elseif z=="V" || z=="VE" || contains(z,"COASTAL V")
        colName = 'HazardCV'; normZone = 'V/Coastal V';
    elseif z=="R" || contains(z,"RIVERINE")
        colName = 'HazardR';  normZone = 'Riverine';
    else
        colName = 'HazardCA'; normZone = 'A/Coastal A';
    end
end

function damage_pct = interp_damage_pct(dmg_fn_id, flood_depth, dmg_functions, id_column)
    % Linear interpolation on FAST damage curves with columns m4..m1, p0..p24
    if isempty(dmg_fn_id) || ~isfinite(dmg_fn_id)
        damage_pct = 0; return;
    end
    rowmask = (dmg_functions.(id_column) == dmg_fn_id);
    if ~any(rowmask)
        damage_pct = 0; return;
    end
    F = dmg_functions(rowmask, :);

    if flood_depth < -4, damage_pct = 0; return; end
    if flood_depth > 24, damage_pct = F.p24(1); return; end

    lower_depth = floor(flood_depth); upper_depth = ceil(flood_depth);
    if lower_depth < 0, lower_col = sprintf('m%d', abs(lower_depth)); else, lower_col = sprintf('p%d', lower_depth); end
    if upper_depth < 0, upper_col = sprintf('m%d', abs(upper_depth)); else, upper_col = sprintf('p%d', upper_depth); end

    if ~ismember(lower_col, F.Properties.VariableNames) || ~ismember(upper_col, F.Properties.VariableNames)
        allCols = F.Properties.VariableNames;
        if any(strcmp(allCols, lower_col)), lower_val = F.(lower_col)(1); else, lower_val = F.p0(1); end
        if any(strcmp(allCols, upper_col)), upper_val = F.(upper_col)(1); else, upper_val = lower_val; end
    else
        lower_val = F.(lower_col)(1);
        upper_val = F.(upper_col)(1);
    end

    if lower_depth == upper_depth
        damage_pct = lower_val; return;
    else
        frac = flood_depth - lower_depth;
        damage_pct = lower_val + frac * (upper_val - lower_val);
    end
end
