import os
import math
import re as _re
import numpy as np
import pandas as pd
import streamlit as st
import streamlit.components.v1 as _components
import plotly.express as px
import plotly.graph_objects as go
from types import SimpleNamespace


def render(ctx):
    render_depth_tool(ctx)


def render_depth_tool(ctx):
    available_years = ctx.available_years
    loc_entry = ctx.loc_entry
    selected_location = ctx.selected_location
    st.markdown(
        '<p class="tab-description">Flood depth at a single building. Pick a structure, enter the same '
        'flood levels used in the map pages, and the table reports the projected water level by year (with '
        'sea-level rise) three ways: depth above the <b>ground</b>, depth above the <b>first floor</b>, '
        'and the absolute <b>NAVD88</b> water surface. Positive = water above that reference; '
        'negative = below it (no flooding).</p>', unsafe_allow_html=True)
    _bd_attrs = loc_entry.get('bldg_attrs') if loc_entry else None
    if _bd_attrs is None or _bd_attrs.empty:
        st.info("No building inventory (NSI) is available for this location.")
    else:
        _bd_ids = _bd_attrs['id'].astype(int).tolist()

        def _bd_fmt(bid):
            _r = _bd_attrs[_bd_attrs['id'].astype(int) == int(bid)]
            if _r.empty:
                return str(bid)
            _r = _r.iloc[0]
            _occ = _r.get('occupancy_type', '')
            _addr = _r.get('address', '')
            _lbl = f"{int(bid)}"
            if isinstance(_occ, str) and _occ:
                _lbl += f" - {_occ}"
            if isinstance(_addr, str) and _addr:
                _lbl += f" - {_addr}"
            return _lbl

        _bd_default_id = 579513026
        _bd_idx = (_bd_ids.index(_bd_default_id)
                   if (selected_location == "Pamunkey" and _bd_default_id in _bd_ids) else 0)
        # Follow the building chosen on the damage tab (or clicked on the map).
        _sync_id = st.session_state.get('bldg_details_selectbox', st.session_state.get('selected_building_id'))
        try:
            if _sync_id is not None and int(_sync_id) in _bd_ids:
                _bd_idx = _bd_ids.index(int(_sync_id))
                # Keyed widgets keep their own state, so push the synced id
                # into the widget key whenever the chosen building changes.
                if st.session_state.get('_bd_last_sync') != int(_sync_id):
                    st.session_state['bd_id'] = int(_sync_id)
                    st.session_state['_bd_last_sync'] = int(_sync_id)
        except (TypeError, ValueError):
            pass
        _bd_sel = st.selectbox(
            "Building (NSI id)", options=_bd_ids, index=_bd_idx,
            format_func=_bd_fmt, key="bd_id",
        )
        _brow = _bd_attrs[_bd_attrs['id'].astype(int) == int(_bd_sel)].iloc[0]
        _ground = _brow.get('ground_elevation')
        _ffe = _brow.get('FFE_ft')
        _fh = _brow.get('foundation_height')

        _info_bits = []
        if pd.notna(_ground):
            _info_bits.append(f"Ground elevation **{float(_ground):.2f} ft NAVD88**")
        if pd.notna(_ffe):
            _info_bits.append(f"First-floor elevation (FFE) **{float(_ffe):.2f} ft NAVD88**")
        if pd.notna(_fh):
            _info_bits.append(f"Foundation height **{float(_fh):.1f} ft**")
        if isinstance(_brow.get('occupancy_type'), str) and _brow.get('occupancy_type'):
            _info_bits.append(f"Occupancy **{_brow.get('occupancy_type')}**")
        if _info_bits:
            st.caption(" · ".join(_info_bits))

        # --- water-level scaffolding (same model as the map tabs) ----------
        _bdwl = loc_entry.get('water_levels', {}) if loc_entry else {}
        _bdscn_keys = [k for k in _bdwl.keys() if not k.endswith('_mc')]
        _bdscn_label = {
            '50th-percentile': 'Intermediate-High SLR (50th pct)',
            '90th-percentile': 'High SLR (90th pct)',
        }
        _bdscn_pretty = lambda k: _bdscn_label.get(k, k)
        _bdref = '50th-percentile' if '50th-percentile' in _bdwl else (_bdscn_keys[0] if _bdscn_keys else None)
        _bdref_df = _bdwl.get(_bdref)
        _bdbase_year = int(_bdref_df['Year'].min()) if (_bdref_df is not None and not _bdref_df.empty) else 2025

        def _bdlvl(df, year, col):
            if df is None or df.empty or col not in df.columns:
                return None
            i = (df['Year'] - year).abs().idxmin()
            return float(df.loc[i, col])

        def _bdslr(scn_key, year):
            df = _bdwl.get(scn_key)
            a, b = _bdlvl(df, year, 'P50'), _bdlvl(df, _bdbase_year, 'P50')
            return (a - b) if (a is not None and b is not None) else 0.0

        _bdpf = {
            'annual': _bdlvl(_bdref_df, _bdbase_year, 'P50'),
            'ten':    _bdlvl(_bdref_df, _bdbase_year, 'P90'),
            'one':    _bdlvl(_bdref_df, _bdbase_year, 'P99'),
        }

        _bd_is_pam = (selected_location == "Pamunkey")
        if _bd_is_pam:
            _bddefs = [
                ("High tide (MHHW)",         2.37,          True),
                ("Monthly flood",            None,          False),
                ("Annual flood",             _bdpf['annual'], False),
                ("10% annual chance (10-yr storm)", 5.78,   True),
                ("1% annual chance (100-yr storm)",  7.38,   True),
            ]
        else:
            _bddefs = [
                ("High tide (MHHW)",         None,          False),
                ("Monthly flood",            None,          False),
                ("Annual flood",             _bdpf['annual'], _bdpf['annual'] is not None),
                ("10% annual chance (10-yr storm)", _bdpf['ten'], _bdpf['ten'] is not None),
                ("1% annual chance (100-yr storm)",  _bdpf['one'], _bdpf['one'] is not None),
            ]

        if _bdscn_keys:
            _bdscn = st.selectbox(
                "SLR scenario", options=_bdscn_keys, index=0,
                format_func=_bdscn_pretty, key="bd_scn",
            )
        else:
            _bdscn = None
            st.warning(
                "No Monte-Carlo water-level data for this location, so future columns won't add sea-level "
                "rise (they will equal the present-day level you enter)."
            )

        st.markdown("**Flood levels** (ft NAVD88) - tick the conditions to include and edit any value.")
        _bdrows = []
        for _lbl, _val, _on in _bddefs:
            _c0, _c1 = st.columns([0.42, 0.58])
            _inc = _c0.checkbox(_lbl, value=_on, key=f"bdf_inc_{_lbl}")
            _default = float(_val) if _val is not None else 0.0
            _lv = _c1.number_input(
                f"{_lbl} level (ft NAVD88)", value=_default, step=0.5, format="%.2f",
                key=f"bdf_val_{_lbl}", label_visibility="collapsed",
            )
            if _inc:
                _bdrows.append((_lbl, float(_lv)))

        if not _bdrows:
            st.info("Tick at least one flood condition.")
        else:
            _years = sorted(set([2026] + [int(y) for y in available_years]))

            def _ftin(v):
                if v is None or not np.isfinite(v):
                    return "-"
                return f"{v:.2f} ft ({v * 12:.1f} in)"

            def _build_table(ref):
                data = {}
                for _lbl, _base_lv in _bdrows:
                    cells = []
                    for _y in _years:
                        _wl = _base_lv + (_bdslr(_bdscn, _y) if _bdscn else 0.0)
                        if ref == 'navd88':
                            _v = _wl
                        elif ref == 'ground':
                            _v = (_wl - float(_ground)) if pd.notna(_ground) else None
                        else:  # ffe
                            _v = (_wl - float(_ffe)) if pd.notna(_ffe) else None
                        cells.append(_ftin(_v))
                    data[_lbl] = cells
                df = pd.DataFrame(data, index=[str(int(y)) for y in _years]).T
                df.index.name = "Flood condition"
                return df

            _scn_note = f" - {_bdscn_pretty(_bdscn)}" if _bdscn else ""
            st.caption(
                f"Columns are evaluation years; future years add the median sea-level rise{_scn_note} "
                f"to the present-day ({_bdbase_year}) level."
            )

            st.markdown("##### 1. Depth above ground level")
            if pd.notna(_ground):
                st.caption(f"Water level minus the building's ground elevation ({float(_ground):.2f} ft NAVD88).")
                st.dataframe(_build_table('ground'), use_container_width=True)
            else:
                st.info("This building has no ground elevation in the inventory.")

            st.markdown("##### 2. Depth above first-floor elevation (FFE)")
            if pd.notna(_ffe):
                st.caption(f"Water level minus the building's first-floor elevation ({float(_ffe):.2f} ft NAVD88).")
                st.dataframe(_build_table('ffe'), use_container_width=True)
            else:
                st.info("This building has no first-floor elevation in the inventory.")

            st.markdown("##### 3. Absolute water level (NAVD88)")
            st.caption("The projected still-water surface elevation itself (present-day level + sea-level rise).")
            st.dataframe(_build_table('navd88'), use_container_width=True)

