"""Shared UI: theme CSS, page header, module sidebar, settings row, cards,
footer, developer gate and the location registry."""
import os
import json
import html as _html

import streamlit as st

from adapt_core import config as C


def secrets_available():
    """True when a secrets.toml exists (avoids the FileNotFoundError that
    st.secrets raises on a plain local checkout)."""
    try:
        from streamlit.runtime.secrets import secrets_singleton
        return bool(secrets_singleton.load_if_toml_exists())
    except Exception:
        return False

# ---------------------------------------------------------------------------
# Location registry: defaults from config, overridden by data/locations.json
# ---------------------------------------------------------------------------
def load_location_registry():
    reg = {k: dict(v) for k, v in C.LOCATION_DEFAULTS.items()}
    if os.path.exists(C.LOCATIONS_FILE):
        try:
            with open(C.LOCATIONS_FILE, "r", encoding="utf-8") as fh:
                user = json.load(fh)
            for slug, cfg in user.items():
                reg.setdefault(slug, {"name": slug})
                reg[slug].update(cfg)
        except Exception as e:                     # a bad file must not kill the app
            st.warning(f"Could not read {os.path.basename(C.LOCATIONS_FILE)}: {e}")
    return reg


def save_location_registry(reg):
    os.makedirs(os.path.dirname(C.LOCATIONS_FILE), exist_ok=True)
    with open(C.LOCATIONS_FILE, "w", encoding="utf-8") as fh:
        json.dump(reg, fh, indent=2)


def location_cfg(reg, slug_or_name):
    """Look a location up by slug or display name."""
    if slug_or_name in reg:
        return reg[slug_or_name]
    for slug, cfg in reg.items():
        if cfg.get("name") == slug_or_name:
            return cfg
    return None


def slugify(name):
    return "".join(ch for ch in str(name).title() if ch.isalnum())


# ---------------------------------------------------------------------------
# Theme CSS
# ---------------------------------------------------------------------------
_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

html, body, [class*="css"], .stMarkdown, .stText, button, input, select, textarea {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}
.block-container { padding-top: 1.1rem; padding-bottom: 2rem; max-width: 1400px; }
[data-testid="stDecoration"] { display: none; }
.stAppDeployButton { display: none; }
h1, h2, h3 { letter-spacing: -0.01em; }
h2 { font-size: 1.55rem !important; }
h3 { font-size: 1.25rem !important; }
[data-testid="stSubheader"] { font-size: 1.35rem !important; }

/* ---- sidebar ---- */
section[data-testid="stSidebar"] {
    background: linear-gradient(180deg, %(navy)s 0%%, %(navy2)s 100%%) !important;
    border-right: 1px solid rgba(148,163,184,0.18);
    min-width: 268px !important; max-width: 320px !important;
}
section[data-testid="stSidebar"] > div,
section[data-testid="stSidebar"] [data-testid="stSidebarContent"],
section[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] { background: transparent !important; }
section[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] { padding-top: 1.1rem; }
section[data-testid="stSidebar"] * { color: #e2e8f0; }
section[data-testid="stSidebar"] [data-testid="stImage"] img { border-radius: 12px; }
section[data-testid="stSidebar"] .stSelectbox label p,
section[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p {
    color: #cbd5e1 !important; font-weight: 600 !important; font-size: 0.78rem !important;
    text-transform: uppercase; letter-spacing: 0.06em;
}
section[data-testid="stSidebar"] .stSelectbox > div > div {
    background: rgba(255,255,255,0.06) !important; border: 1px solid rgba(148,163,184,0.35) !important;
    color: #f8fafc !important;
}
section[data-testid="stSidebar"] .stSelectbox svg { fill: #cbd5e1; }
section[data-testid="stSidebar"] hr { border-color: rgba(148,163,184,0.22); margin: 0.8rem 0; }

/* page links in the sidebar */
section[data-testid="stSidebar"] [data-testid="stPageLink"] a,
section[data-testid="stSidebar"] [data-testid="stPageLink-NavLink"] {
    display: flex; align-items: center; width: 100%%; padding: 0.55rem 0.75rem; margin: 2px 0;
    border-radius: 10px; color: #e2e8f0 !important; font-weight: 600; font-size: 0.93rem;
    text-decoration: none; transition: background-color .15s ease, transform .15s ease;
}
section[data-testid="stSidebar"] [data-testid="stPageLink"] a:hover,
section[data-testid="stSidebar"] [data-testid="stPageLink-NavLink"]:hover {
    background: rgba(148,163,184,0.16); transform: translateX(3px);
}
section[data-testid="stSidebar"] [data-testid="stPageLink"] a[aria-current="page"],
section[data-testid="stSidebar"] [data-testid="stPageLink-NavLink"][aria-current="page"] {
    background: linear-gradient(135deg, %(accent)s 0%%, %(accent_dark)s 100%%);
    box-shadow: 0 6px 16px rgba(14,165,233,0.35);
}
section[data-testid="stSidebar"] [data-testid="stPageLink"] a p,
section[data-testid="stSidebar"] [data-testid="stPageLink-NavLink"] p { color: #ffffff !important; }

.adapt-brand { display: flex; flex-direction: column; gap: 2px; padding: 0 0.2rem 0.6rem 0.2rem; }
.adapt-brand .word { font-size: 1.55rem; font-weight: 800; color: #38bdf8; letter-spacing: 0.5px; line-height: 1; }
.adapt-brand .sub { font-size: 0.66rem; color: #cbd5e1; font-weight: 500; line-height: 1.3; }
.adapt-brand-row { display: flex; align-items: center; gap: 0.75rem; padding: 0.1rem 0.15rem 0.9rem 0.15rem;
    margin-bottom: 0.4rem; border-bottom: 1px solid rgba(148,163,184,0.22); }
.adapt-brand-row .tile { width: 54px; height: 54px; flex: 0 0 54px; border-radius: 14px; background: #ffffff;
    display: flex; align-items: center; justify-content: center; box-shadow: 0 4px 14px rgba(0,0,0,0.25); }
.adapt-brand-row .tile img { width: 42px; height: 42px; object-fit: contain; }
.adapt-brand-row .word { font-size: 1.45rem; font-weight: 800; letter-spacing: 0.06em; color: #ffffff !important; line-height: 1; }
.adapt-brand-row .sub { font-size: 0.66rem; color: #94a3b8 !important; line-height: 1.3; margin-top: 4px; }
.adapt-module-tag {
    display: inline-flex; align-items: center; gap: 6px; margin: 0.4rem 0 0.4rem 0;
    padding: 0.28rem 0.6rem; border-radius: 999px; font-size: 0.72rem; font-weight: 700;
    letter-spacing: 0.06em; text-transform: uppercase; color: #0f172a; background: %(accent)s;
}
.adapt-side-note { font-size: 0.74rem; color: #94a3b8 !important; line-height: 1.4; padding: 0 0.2rem; }

/* ---- page header ---- */
.adapt-header { margin: 0 0 0.9rem 0; }
.adapt-header .eyebrow {
    display: inline-flex; align-items: center; gap: 8px; font-size: 0.74rem; font-weight: 700;
    letter-spacing: 0.08em; text-transform: uppercase; color: %(accent_dark)s; margin-bottom: 0.25rem;
}
.adapt-header .eyebrow .loc { color: #475569; font-weight: 600; letter-spacing: 0.02em; text-transform: none; }
.adapt-header h1 { font-size: 1.85rem; font-weight: 800; color: #0f172a; margin: 0 0 0.35rem 0; line-height: 1.15; }
.adapt-header .lede { font-size: 1.0rem; color: #475569; line-height: 1.5; max-width: 980px; margin: 0; }
.adapt-header .lede b { color: #0f172a; }

/* ---- cards ---- */
.adapt-card {
    background: #ffffff; border: 1px solid %(line)s; border-radius: 14px; padding: 1.1rem 1.2rem;
    box-shadow: 0 1px 2px rgba(15,23,42,0.04); height: 100%%;
}
.adapt-card.accent { border-top: 4px solid %(accent)s; }
.adapt-card h3 { margin: 0.1rem 0 0.3rem 0; font-size: 1.15rem !important; color: #0f172a; }
.adapt-card .sub { font-size: 0.86rem; color: #64748b; margin-bottom: 0.5rem; }
.adapt-card p { font-size: 0.95rem; color: #334155; line-height: 1.5; margin: 0 0 0.6rem 0; }
.adapt-card ul { margin: 0 0 0.6rem 1.1rem; padding: 0; color: #334155; font-size: 0.92rem; line-height: 1.55; }
.adapt-card .icon { font-size: 1.7rem; line-height: 1; margin-bottom: 0.45rem; }

.adapt-kpi {
    background: #ffffff; border: 1px solid %(line)s; border-radius: 12px; padding: 0.85rem 1rem;
}
.adapt-kpi .label { font-size: 0.74rem; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; color: #64748b; }
.adapt-kpi .value { font-size: 1.55rem; font-weight: 800; color: #0f172a; line-height: 1.2; margin-top: 0.15rem; }
.adapt-kpi .delta { font-size: 0.82rem; color: #475569; margin-top: 0.15rem; }

.adapt-note {
    background: #eff6ff; border-left: 4px solid %(accent)s; border-radius: 8px;
    padding: 0.7rem 0.95rem; margin: 0.1rem 0 0.9rem 0; color: #0f172a; font-size: 0.93rem; line-height: 1.5;
}
.tab-description {
    font-size: 1.0rem; color: #475569; margin-bottom: 1rem; padding: 0.55rem 0.8rem;
    background-color: #f8fafc; border-radius: 0.4rem; border-left: 3px solid %(accent)s;
}
.metric-card { background-color: #f8fafc; padding: 1rem; border-radius: 0.5rem; border: 1px solid #e2e8f0; }

/* ---- widgets ---- */
.stButton > button {
    padding-top: 0.3rem !important; padding-bottom: 0.3rem !important; min-height: 0 !important;
    line-height: 1.3 !important; white-space: nowrap !important; border-radius: 9px !important;
}
.stButton > button p { white-space: nowrap !important; margin: 0 !important; }
.stTabs [data-baseweb="tab-list"] button [data-testid="stMarkdownContainer"] p { font-size: 1.0rem !important; font-weight: 600; }
.stTabs [data-baseweb="tab-list"] button { padding: 0.5rem 1.1rem; }
[data-testid="stPlotlyChart"] .xtick text, [data-testid="stPlotlyChart"] .ytick text,
[data-testid="stPlotlyChart"] .x2tick text, [data-testid="stPlotlyChart"] .y2tick text,
[data-testid="stPlotlyChart"] text.xtitle, [data-testid="stPlotlyChart"] text.ytitle,
[data-testid="stPlotlyChart"] .g-xtitle text, [data-testid="stPlotlyChart"] .g-ytitle text { fill: #000000 !important; }
[data-testid="stImage"] { margin-top: -0.3rem; margin-bottom: -0.3rem; }
[data-testid="stForm"] { border: 1px solid %(line)s; border-radius: 14px; padding: 1rem 1rem 0.4rem 1rem; }
.adapt-card.accent + div [data-testid="stForm"], .adapt-card.accent ~ div [data-testid="stForm"] { margin-top: 0.5rem; }

/* ---- landing page ---- */
.adapt-hero.light {
    display: flex; align-items: center; gap: 2.2rem; padding: 1.5rem 2.4rem 1.5rem 2.6rem; margin-top: 0.3rem;
    border-radius: 22px; border: 1px solid %(line)s; color: #0f172a;
    background: radial-gradient(900px 320px at 100%% 0%%, rgba(14,165,233,0.16), transparent 60%%),
                radial-gradient(700px 300px at 0%% 100%%, rgba(20,184,166,0.14), transparent 55%%),
                linear-gradient(135deg, #ffffff 0%%, #f1f7fd 100%%);
    box-shadow: 0 10px 30px rgba(15,23,42,0.06);
}
.adapt-hero.light .hero-text { flex: 1 1 62%%; min-width: 0; }
.adapt-hero.light .hero-logo { flex: 0 0 auto; display: flex; justify-content: flex-end; }
.adapt-hero.light .hero-logo img { height: 198px; width: auto; display: block; }
.adapt-hero.light .kicker { font-size: 0.74rem; font-weight: 700; letter-spacing: 0.14em; text-transform: uppercase;
    color: %(accent_dark)s; margin-bottom: 0.6rem; }
.adapt-hero.light h1 { font-size: 2.15rem; font-weight: 800; line-height: 1.12; margin: 0 0 0.6rem 0; color: #0f172a;
    letter-spacing: -0.02em; max-width: 760px; }
.adapt-hero.light p { font-size: 1.04rem; line-height: 1.55; color: #475569; margin: 0; max-width: 640px; }
.adapt-siteline { font-size: 0.95rem; color: #475569; padding-bottom: 0.55rem; }
.adapt-siteline b { color: #0f172a; font-weight: 700; }
.adapt-lock { display: inline-flex; align-items: center; gap: 6px; font-size: 0.82rem; color: #92400e;
    background: #fef3c7; border-radius: 999px; padding: 0.3rem 0.7rem; margin-bottom: 0.45rem; }

/* module cards: the whole card is one click (an invisible page link covers it) */
[class*="st-key-go_"] { position: relative; }
[class*="st-key-go_"] [data-testid="stElementContainer"]:has([data-testid="stPageLink"]) {
    position: absolute; inset: 0; z-index: 3; margin: 0;
}
[class*="st-key-go_"] [data-testid="stPageLink"], [class*="st-key-go_"] [data-testid="stPageLink"] > div,
[class*="st-key-go_"] [data-testid="stPageLink"] a, [class*="st-key-go_"] [data-testid="stPageLink-NavLink"] {
    display: block; width: 100%%; height: 100%%; opacity: 0; cursor: pointer; margin: 0; padding: 0; min-height: 100%%;
}
.adapt-module {
    background: #ffffff; border: 1px solid %(line)s; border-radius: 18px; padding: 1.25rem 1.3rem 1.1rem 1.3rem;
    box-shadow: 0 1px 2px rgba(15,23,42,0.04); transition: transform .15s ease, box-shadow .15s ease, border-color .15s ease;
    min-height: 196px; margin: 0.3rem 0 0 0; position: relative;
}
[class*="st-key-go_"]:hover .adapt-module { transform: translateY(-3px); box-shadow: 0 14px 34px rgba(15,23,42,0.12); }
.adapt-module .icon { width: 50px; height: 50px; border-radius: 14px; display: flex; align-items: center;
    justify-content: center; font-size: 1.55rem; margin-bottom: 0.95rem; }
.adapt-module h3 { margin: 0 0 0.25rem 0; font-size: 1.22rem !important; color: #0f172a; }
.adapt-module .who { font-size: 0.86rem; color: #64748b; line-height: 1.45; }
.adapt-module .go { position: absolute; right: 1.2rem; bottom: 1.0rem; font-size: 0.86rem; font-weight: 700;
    display: inline-flex; align-items: center; gap: 4px; }
[class*="st-key-go_"]:hover .adapt-module .go span { transform: translateX(3px); }
.adapt-module .go span { display: inline-block; transition: transform .15s ease; }

.adapt-footer { text-align: center; padding: 1.3rem 0 0.4rem 0; color: #64748b; font-size: 0.9rem; line-height: 1.55; }
.adapt-footer .org { font-weight: 600; color: #334155; }
.adapt-footer .lic { font-size: 0.8rem; color: #94a3b8; margin-top: 0.35rem; }
</style>
"""


def inject_css(accent=C.SKY, accent_dark=C.SKY_DARK):
    st.markdown(_CSS % {"navy": C.NAVY, "navy2": C.NAVY_2, "accent": accent,
                        "accent_dark": accent_dark, "line": C.LINE}, unsafe_allow_html=True)


@st.cache_data(show_spinner=False)
def image_data_uri(path, mtime, max_px=480, reverse=False):
    """PNG data URI of an image, downscaled so the page stays light. `mtime`
    is only part of the cache key, so a replaced file is picked up.
    reverse=True turns the logo's dark (navy) parts near-white for use on a
    dark background; the blue and teal parts are left as they are."""
    import base64
    import io
    import numpy as np
    from PIL import Image
    im = Image.open(path).convert("RGBA")
    im.thumbnail((max_px, max_px), Image.LANCZOS)
    if reverse:
        a = np.array(im).astype(float)
        t = np.clip((140.0 - a[..., :3].max(axis=2)) / 35.0, 0, 1)[..., None]
        a[..., :3] = a[..., :3] * (1 - t) + np.array([248.0, 250.0, 252.0]) * t
        im = Image.fromarray(a.astype(np.uint8))
    bbox = im.getbbox()
    if bbox:
        im = im.crop(bbox)
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def first_existing(paths):
    return next((p for p in paths if os.path.exists(p)), None)


def sidebar_brand():
    """Sidebar header: the ADAPT mark on a light tile, with the name beside it."""
    mark = ""
    if os.path.exists(C.LOGO_PATH):
        mark = f"<img src='{image_data_uri(C.LOGO_PATH, os.path.getmtime(C.LOGO_PATH), 160)}' alt=''/>"
    st.markdown(
        "<div class='adapt-brand-row'>"
        f"<div class='tile'>{mark}</div>"
        "<div class='txt'><div class='word'>ADAPT</div>"
        "<div class='sub'>Assessment of Damage and Adaptation Planning Tool</div></div></div>",
        unsafe_allow_html=True,
    )


def dismiss_first_load_page_not_found():
    """Streamlit 1.40 quirk: on the first session after the server starts,
    opening a page URL directly (for example /maps) shows a "Page not found"
    dialog even though the page then renders, because the server only learns
    about st.navigation pages after its first script run. On a session's
    first run, close that one dialog if it appears. Nothing else is touched."""
    ss = st.session_state
    if ss.get("_pnf_guard_done"):
        return
    ss["_pnf_guard_done"] = True
    import streamlit.components.v1 as components
    components.html(
        """<script>
        (function () {
          try {
            var doc = window.parent.document, t0 = Date.now();
            var iv = setInterval(function () {
              doc.querySelectorAll('[role="dialog"]').forEach(function (d) {
                if ((d.innerText || '').indexOf('Page not found') === 0) {
                  var b = d.querySelector('button[aria-label="Close"]');
                  if (b) { b.click(); }
                }
              });
              if (Date.now() - t0 > 10000) { clearInterval(iv); }
            }, 120);
          } catch (e) {}
        })();
        </script>""",
        height=0,
    )


def hide_sidebar():
    """Landing page only: hide the sidebar and its toggle so the page reads as
    a single clean canvas. Module pages render the sidebar normally."""
    st.markdown(
        "<style>"
        "section[data-testid='stSidebar'], [data-testid='stSidebarCollapsedControl'],"
        "[data-testid='collapsedControl'] { display: none !important; }"
        ".block-container { max-width: 1240px; padding-top: 0.4rem; padding-bottom: 0.6rem; }"
        ".adapt-footer { padding: 0.5rem 0 0 0; font-size: 0.8rem; line-height: 1.4; }"
        ".adapt-footer div { display: inline; margin: 0 0.45rem; }"
        "hr { margin: 0.6rem 0 0.2rem 0 !important; }"
        "</style>",
        unsafe_allow_html=True,
    )


def landing_dark():
    """Landing page only: dark canvas and hero; the location picker and the
    module cards stay light so the choices stand out."""
    st.markdown(
        """<style>
        .stApp {
            background:
              radial-gradient(1100px 520px at 88% -8%, rgba(14,165,233,0.20), transparent 60%),
              radial-gradient(900px 480px at -6% 105%, rgba(20,184,166,0.16), transparent 58%),
              linear-gradient(160deg, #0b1220 0%, #0f172a 55%, #111c33 100%) !important;
        }
        header[data-testid="stHeader"] { background: transparent !important; }
        header[data-testid="stHeader"] button, header[data-testid="stHeader"] [data-testid="stMainMenu"] { color: #cbd5e1 !important; }
        /* hero: a light card on the dark canvas, so the original logo can sit in it */
        .adapt-hero.dark {
            display: flex; align-items: center; gap: 2rem; padding: 1.05rem 2rem 1.05rem 2.2rem; margin-top: 0.1rem;
            border-radius: 20px; border: none; color: #0f172a;
            background: radial-gradient(800px 300px at 100% 0%, rgba(14,165,233,0.14), transparent 60%),
                        radial-gradient(600px 260px at 0% 100%, rgba(20,184,166,0.12), transparent 55%),
                        linear-gradient(135deg, #ffffff 0%, #f1f7fd 100%);
            box-shadow: 0 18px 48px rgba(0,0,0,0.35);
        }
        .adapt-hero.dark .hero-text { flex: 1 1 62%; min-width: 0; }
        .adapt-hero.dark .hero-logo { flex: 0 0 auto; display: flex; justify-content: flex-end; }
        .adapt-hero.dark .hero-logo img { height: 148px; width: auto; display: block; }
        .adapt-hero.dark .kicker { font-size: 0.7rem; font-weight: 700; letter-spacing: 0.14em; text-transform: uppercase;
            color: #0284c7; margin-bottom: 0.45rem; }
        .adapt-hero.dark h1 { font-size: 1.85rem; font-weight: 800; line-height: 1.12; margin: 0 0 0.45rem 0;
            color: #0f172a; letter-spacing: -0.02em; max-width: 720px; padding: 0; text-wrap: balance; }
        .adapt-hero.dark p { font-size: 0.98rem; line-height: 1.5; color: #475569; margin: 0; max-width: 660px; text-wrap: pretty; }
        /* tighter vertical rhythm on the landing page */
        .stApp .block-container { padding-top: 1.2rem !important; padding-bottom: 1.2rem !important;
            min-height: 100vh; display: flex; flex-direction: column; justify-content: center; }
        .adapt-hero.dark { margin-bottom: 0.35rem; }
        .adapt-module .go { top: 1.05rem !important; bottom: auto !important; right: 1.15rem !important; }
        .stApp [data-testid="stVerticalBlock"] { gap: 0.7rem; }
        .adapt-module { min-height: 150px !important; padding: 0.95rem 1.15rem 0.9rem 1.15rem !important; }
        .adapt-module .icon { width: 42px !important; height: 42px !important; font-size: 1.3rem !important;
            border-radius: 12px !important; margin-bottom: 0.65rem !important; }
        .adapt-module h3 { font-size: 1.12rem !important; }
        /* labels and text on the dark canvas */
        .stApp [data-testid="stWidgetLabel"] p { color: #cbd5e1 !important; font-weight: 600; }
        .adapt-siteline { color: #94a3b8 !important; }
        .adapt-siteline b { color: #f8fafc !important; }
        /* light choices: location picker, password form, module cards */
        .stApp .stSelectbox > div > div { background: #ffffff !important; color: #0f172a !important;
            border: 1px solid rgba(255,255,255,0.0) !important; box-shadow: 0 6px 18px rgba(0,0,0,0.25); }
        .stApp [data-testid="stForm"] { background: #ffffff; border: none !important; box-shadow: 0 10px 28px rgba(0,0,0,0.3); }
        .stApp [data-testid="stForm"] [data-testid="stWidgetLabel"] p { color: #334155 !important; }
        .st-key-gate_inline [data-testid="stForm"] { background: transparent !important; box-shadow: none !important;
            padding: 0 !important; border: none !important; }
        .st-key-gate_inline input { background: #ffffff !important; color: #0f172a !important; }
        .st-key-gate_inline [data-testid="stTextInputRootElement"] { box-shadow: 0 6px 18px rgba(0,0,0,0.25); border-radius: 8px; }
        .adapt-module { border: 1px solid rgba(255,255,255,0.0) !important; box-shadow: 0 10px 30px rgba(0,0,0,0.30) !important; }
        [class*="st-key-go_"]:hover .adapt-module { box-shadow: 0 18px 44px rgba(0,0,0,0.45) !important; }
        /* footer */
        .stApp hr { border-color: rgba(148,163,184,0.18) !important; }
        .adapt-footer, .adapt-footer div { color: #94a3b8 !important; }
        .adapt-footer .org { color: #cbd5e1 !important; }
        .adapt-footer .lic { color: #64748b !important; }
        </style>""",
        unsafe_allow_html=True,
    )


def _darken(hex_color, f=0.82):
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return "#%02x%02x%02x" % (int(r * f), int(g * f), int(b * f))


def module_accent(module_key):
    acc = C.MODULES.get(module_key, {}).get("accent", C.SKY)
    return acc, _darken(acc)


# ---------------------------------------------------------------------------
# Page chrome
# ---------------------------------------------------------------------------
def page_header(title, lede=None, module_key=None, location=None):
    eyebrow = ""
    if module_key:
        m = C.MODULES[module_key]
        eyebrow += f"<span>{m['icon']} {_html.escape(m['title'])}</span>"
    if location:
        eyebrow += f"<span class='loc'>· 📍 {_html.escape(str(location))}</span>"
    lede_html = f"<p class='lede'>{lede}</p>" if lede else ""
    st.markdown(
        f"<div class='adapt-header'>"
        f"<div class='eyebrow'>{eyebrow}</div>"
        f"<h1>{_html.escape(title)}</h1>{lede_html}</div>",
        unsafe_allow_html=True,
    )


def note(text):
    st.markdown(f"<div class='adapt-note'>{text}</div>", unsafe_allow_html=True)


def kpi(label, value, delta=None):
    d = f"<div class='delta'>{delta}</div>" if delta else ""
    st.markdown(f"<div class='adapt-kpi'><div class='label'>{_html.escape(label)}</div>"
                f"<div class='value'>{value}</div>{d}</div>", unsafe_allow_html=True)


def card(title, body_html, icon=None, sub=None, accent=False):
    ic = f"<div class='icon'>{icon}</div>" if icon else ""
    sb = f"<div class='sub'>{_html.escape(sub)}</div>" if sub else ""
    st.markdown(f"<div class='adapt-card{' accent' if accent else ''}'>{ic}<h3>{_html.escape(title)}</h3>{sb}{body_html}</div>",
                unsafe_allow_html=True)


def footer():
    st.divider()
    st.markdown(
        "<div class='adapt-footer'><div class='org'>Center for Climate Systems Research · "
        "The Climate School · Columbia University</div>"
        "<div>ADAPT: Assessment of Damage and Adaptation Planning Tool</div>"
        "<div class='lic'>© 2025 Erfan Amini. All rights reserved.</div></div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Access gate for protected locations
# ---------------------------------------------------------------------------
LEGACY_PASSWORD = "NY2026VA"


def access_password():
    """Password that unlocks protected locations: Streamlit secrets
    ([access] password) or the ACCESS_PASSWORD environment variable, falling
    back to the project's long-standing password."""
    try:
        if secrets_available():
            if "access" in st.secrets and "password" in st.secrets["access"]:
                return str(st.secrets["access"]["password"])
            if "access_password" in st.secrets:
                return str(st.secrets["access_password"])
    except Exception:
        pass
    return os.environ.get("ACCESS_PASSWORD") or LEGACY_PASSWORD


def is_protected(cfg):
    return bool((cfg or {}).get("protected", False))


def location_unlocked(location_name):
    return location_name in st.session_state.get("unlocked_locations", set())


def location_gate(location_name, cfg, compact=False):
    """Return True when the location may be shown. Otherwise render the
    password form (a card, or a compact inline version) and return False."""
    if not is_protected(cfg) or location_unlocked(location_name):
        return True
    pw = access_password()
    form_key = f"gate_{'c' if compact else 'f'}_{slugify(location_name)}"
    if compact:
        # One row: password field + Unlock. A wrong password shows a toast so
        # the layout does not grow.
        with st.form(form_key, clear_on_submit=True, border=False):
            a, b = st.columns([0.68, 0.32], vertical_alignment="bottom")
            entered = a.text_input("Access password", type="password", label_visibility="collapsed",
                                   placeholder=f"🔒 Password for {location_name} results")
            ok = b.form_submit_button("Unlock", type="primary", use_container_width=True)
        if ok:
            if entered == pw:
                st.session_state.setdefault("unlocked_locations", set()).add(location_name)
                st.rerun()
            else:
                st.toast("Incorrect password.", icon="🔒")
        return False
    st.markdown("<div style='height:1rem'></div>", unsafe_allow_html=True)
    _, container, _ = st.columns([1, 1.25, 1])
    with container:
        st.markdown(
            "<div class='adapt-card accent'><div class='icon'>🔒</div>"
            f"<h3>{_html.escape(str(location_name))} results are password protected</h3>"
            "<p>Results for this community are shared with its partners. Enter the access "
            "password to open them, or pick another location.</p></div>",
            unsafe_allow_html=True,
        )
        with st.form(form_key, clear_on_submit=True):
            entered = st.text_input("Access password", type="password")
            ok = st.form_submit_button("Unlock", type="primary", use_container_width=True)
        if ok:
            if entered == pw:
                st.session_state.setdefault("unlocked_locations", set()).add(location_name)
                st.rerun()
            else:
                st.error("Incorrect password.")
    return False


# ---------------------------------------------------------------------------
# Developer gate (optional: only when [developer] password is set in secrets)
# ---------------------------------------------------------------------------
def developer_password():
    """Password from .streamlit/secrets.toml ([developer] password = "...") or
    the DEVELOPER_PASSWORD environment variable. Falls back to the legacy
    password when neither is set, with a visible warning."""
    try:
        if not secrets_available():
            raise FileNotFoundError
        if "developer" in st.secrets and "password" in st.secrets["developer"]:
            return str(st.secrets["developer"]["password"]), True
        if "developer_password" in st.secrets:
            return str(st.secrets["developer_password"]), True
    except Exception:
        pass
    env = os.environ.get("DEVELOPER_PASSWORD")
    if env:
        return env, True
    return None, False


def developer_gate():
    """The Developer module is open unless a developer password is configured
    in secrets (or DEVELOPER_PASSWORD). Returns True when the caller may render
    developer content."""
    pw, from_secrets = developer_password()
    if not from_secrets or st.session_state.get("dev_unlocked"):
        return True
    st.markdown("<div style='height:1.2rem'></div>", unsafe_allow_html=True)
    c1, c2, c3 = st.columns([1, 1.3, 1])
    with c2:
        st.markdown(
            "<div class='adapt-card accent'><div class='icon'>🛠️</div>"
            "<h3>Developer access</h3>"
            "<p>This module edits inventories and settings and runs the damage model. "
            "Enter the developer password to continue.</p></div>",
            unsafe_allow_html=True,
        )
        with st.form("dev_login", clear_on_submit=True):
            entered = st.text_input("Password", type="password")
            ok = st.form_submit_button("Unlock", type="primary", use_container_width=True)
        if ok:
            if entered == pw:
                st.session_state["dev_unlocked"] = True
                st.rerun()
            else:
                st.error("Incorrect password.")
    return False
