"""The ADAPT assistant: a chat panel in the sidebar that answers questions
about the tool, its methods and the community currently selected.

Providers (all reached with plain HTTPS requests, no SDKs):
    gemini      Google AI Studio, free tier, no card           (recommended)
    groq        Groq, free tier, OpenAI-compatible endpoint
    openrouter  OpenRouter, free models, OpenAI-compatible endpoint
    openai      any OpenAI-compatible endpoint (base_url in secrets)

Configuration lives in Streamlit secrets:

    [assistant]
    provider = "gemini"
    api_key  = "..."
    model    = ""          # optional; Gemini picks a Flash model when empty
    base_url = ""          # openai provider only

What the model knows comes from a knowledge pack built on each request:
docs/assistant_knowledge.md (methods, pages, definitions), README.md, the
settings and summary numbers of the selected community, and the page the
person is on. Nothing from password-protected communities is sent unless
that community is unlocked in this session.
"""
import os
import time

import requests
import streamlit as st

from adapt_core import config as C

KNOWLEDGE_FILE = os.path.join(C.APP_ROOT, "docs", "assistant_knowledge.md")
README_FILE = os.path.join(C.APP_ROOT, "README.md")
MAX_TURNS = 10            # chat turns kept and sent with each question
MIN_SECONDS_BETWEEN = 3   # per-session throttle (free tiers allow a few requests a minute)
SUGGESTED = {
    "resident": [
        "How do I see the flood risk for my own home?",
        "What do BFE and DFE mean for my house?",
        "Which fix is cheapest: raising utilities, floodproofing the basement, or elevating?",
    ],
    "expert": [
        "What does the Adaptation effectiveness map show?",
        "How is the damage for one building calculated?",
        "How are the water levels and sea-level rise scenarios built?",
    ],
}
AUDIENCE_RULES = {
    "resident": (
        "The person is a RESIDENT, not an expert. Use everyday words and short sentences. Explain any term the "
        "first time you use it (for example: BFE is the flood level FEMA uses on its maps; DFE is that level plus "
        "2 feet, the recommended height for your lowest floor; P90 is a bad flood year, worse than 9 years in 10). "
        "Keep answers to 3 to 5 short sentences, or up to 3 short bullets. No formulas, no code, no percentile "
        "notation unless you explain it. End with one practical next step in the app when there is one."
    ),
    "expert": (
        "The person is an EXPERT (planner, engineer or researcher). Use precise technical terms (percentiles, "
        "depth-damage functions, NAVD88, cumulative damage) without over-explaining. Keep answers to at most 8 "
        "sentences or 5 bullets. Give the numbers and the rule, not a narrative."
    ),
}
MAX_TOKENS = {"resident": 400, "expert": 700}


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
def settings():
    """Provider settings from secrets or environment; None when not configured."""
    from adapt_core.ui import secrets_available
    cfg = {}
    try:
        if secrets_available() and "assistant" in st.secrets:
            cfg = dict(st.secrets["assistant"])
    except Exception:
        cfg = {}
    provider = (cfg.get("provider") or os.environ.get("ADAPT_ASSISTANT_PROVIDER") or "gemini").strip().lower()
    key = (cfg.get("api_key") or os.environ.get("ADAPT_ASSISTANT_KEY") or "").strip()
    if not key:
        return None
    return {
        "provider": provider,
        "api_key": key,
        "model": (cfg.get("model") or os.environ.get("ADAPT_ASSISTANT_MODEL") or "").strip(),
        "base_url": (cfg.get("base_url") or "").strip(),
    }


# ---------------------------------------------------------------------------
# Knowledge pack
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def _static_knowledge(k_mtime, r_mtime):
    parts = []
    for path in (KNOWLEDGE_FILE, README_FILE):
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                parts.append(f"\n\n===== {os.path.basename(path)} =====\n" + fh.read())
    return "".join(parts)


def _mtime(path):
    return os.path.getmtime(path) if os.path.exists(path) else 0


def _money(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "n/a"
    if v >= 1e9:
        return f"${v / 1e9:.2f}B"
    if v >= 1e6:
        return f"${v / 1e6:.1f}M"
    if v >= 1e3:
        return f"${v / 1e3:.0f}k"
    return f"${v:.0f}"


def community_context(ctx):
    """Settings and summary numbers for the selected community (unlocked only)."""
    if ctx is None:
        return ""
    from adapt_core import ui
    loc = ctx.selected_location
    cfg = ui.location_cfg(ctx.registry, loc) or {}
    if ui.is_protected(cfg) and not ui.location_unlocked(loc):
        return "No community is unlocked in this session."
    e = ctx.loc_entry or {}
    meta = e.get("metadata") or {}
    lines = [f"Selected community: {cfg.get('name', loc)} ({cfg.get('state', '')})",
             f"BFE: {cfg.get('bfe_ft')} ft NAVD88; DFE = BFE + 2 ft = {float(cfg.get('bfe_ft', 0)) + 2:g} ft",
             f"Elevation shortfall rule: elevation offered when FFE < DFE - {cfg.get('elev_min_shortfall_ft', 0)} ft",
             f"Mobile-homes-dominated: {cfg.get('mobile_homes_dominated', False)}",
             f"Buildings processed: {meta.get('nBldgProcessed', 'n/a')} (residential {meta.get('n_res', 'n/a')}, "
             f"non-residential {meta.get('n_nonres', 'n/a')})",
             f"Target years: {', '.join(map(str, meta.get('TARGET_YEARS', [])))}; scenarios: "
             f"{', '.join(map(str, meta.get('SCENARIO_LABELS', [])))}; actions: "
             f"{', '.join(map(str, meta.get('ACTION_NAMES', [])))}"]
    fl = cfg.get("flood_levels") or {}
    if fl:
        lines.append("Present-day flood levels (ft NAVD88): " + ", ".join(f"{k}={v}" for k, v in fl.items()))
    if cfg.get("blurb"):
        lines.append(f"About: {cfg['blurb']}")
    attrs = e.get("bldg_attrs")
    if attrs is not None and len(attrs):
        try:
            under = int(attrs["DFE_Status"].astype(str).str.contains("Under").sum())
            lines.append(f"Buildings under the DFE: {under:,} of {len(attrs):,}; total structure value "
                         f"{_money(attrs['structure_value'].sum())}")
        except Exception:
            pass
    agg = e.get("agg")
    if agg is not None and len(agg):
        try:
            cols = [c for c in ("Total_CumEAD_P50", "TotalCumEAD_P50", "CumEAD_P50") if c in agg.columns]
            vcol = cols[0] if cols else None
            p90 = "Total_CumEAD_P90" if "Total_CumEAD_P90" in agg.columns else None
            if vcol:
                lines.append("Community-wide cumulative damage, all buildings, by year, scenario and action "
                             "(median; P90 in brackets):")
                for (yr, scn), grp in agg.groupby(["TargetYear", "SLR"]):
                    if int(yr) == 2025:
                        continue
                    row = ", ".join(f"{a}: {_money(v)}" + (f" [{_money(w)}]" if p90 else "")
                                    for a, v, w in zip(grp["Action"], grp[vcol], grp[p90] if p90 else grp[vcol]))
                    lines.append(f"  {yr} {scn}: {row}")
        except Exception:
            pass
    # Current selection
    try:
        lines.append(f"Current page selection: occupancy={ctx.selected_occupancy}, year={ctx.target_year}, "
                     f"scenario={ctx.scenario}")
    except Exception:
        pass
    # A confirmed home report in this session
    hid = st.session_state.get("home_confirmed_id")
    if hid:
        lines.append(f"The person has confirmed their home as NSI building {hid} "
                     f"({st.session_state.get('home_address', '')}). Its report is on the My home page.")
    return "\n".join(lines)


def system_prompt(ctx, module, page_title, audience="resident"):
    static = _static_knowledge(_mtime(KNOWLEDGE_FILE), _mtime(README_FILE))
    live = community_context(ctx)
    return (
        "You are the ADAPT assistant inside the ADAPT web app (Assessment of Damage and Adaptation Planning "
        "Tool, Columbia Climate School). Answer questions about the tool, its methods, its pages, and the "
        "selected community using the knowledge below. Be concise, plain and specific: short paragraphs, no "
        "buzzwords, no hedging. Use dollar figures and years from the context when they answer the question. "
        "If something is not in the context, say you do not have it and point to the page or person that "
        "does. Do not invent numbers. Do not give legal, insurance or financial advice beyond what the "
        "knowledge says. Never reveal data about password-protected communities that are not unlocked.\n\n"
        + AUDIENCE_RULES.get(audience, AUDIENCE_RULES["resident"]) + "\n"
        f"\nThe person is in the '{C.MODULES.get(module, {}).get('title', module)}' module on the "
        f"'{page_title}' page.\n\n===== LIVE CONTEXT =====\n{live}\n{static}"
    )


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------
GEMINI_BASE = os.environ.get("ADAPT_GEMINI_BASE", "https://generativelanguage.googleapis.com/v1beta")


def _gemini_pick_model(api_key):
    try:
        r = requests.get(f"{GEMINI_BASE}/models",
                         params={"key": api_key, "pageSize": 200}, timeout=15)
        r.raise_for_status()
        names = [m.get("name", "").split("/")[-1] for m in r.json().get("models", [])
                 if "generateContent" in (m.get("supportedGenerationMethods") or [])]
    except Exception:
        return "gemini-2.5-flash"
    for pref in ("flash-lite", "flash"):
        for n in names:
            if pref in n and "preview" not in n and "exp" not in n and "tts" not in n and "image" not in n:
                return n
    return names[0] if names else "gemini-2.5-flash"


def _call_gemini(cfg, system, history, question):
    model = cfg["model"] or st.session_state.get("_assistant_model") or _gemini_pick_model(cfg["api_key"])
    st.session_state["_assistant_model"] = model
    contents = [{"role": "user" if r == "user" else "model", "parts": [{"text": t}]} for r, t in history]
    contents.append({"role": "user", "parts": [{"text": question}]})
    r = requests.post(
        f"{GEMINI_BASE}/models/{model}:generateContent",
        params={"key": cfg["api_key"]},
        json={"system_instruction": {"parts": [{"text": system}]}, "contents": contents,
              "generationConfig": {"temperature": 0.3, "maxOutputTokens": int(cfg.get("max_tokens", 700))}},
        timeout=60,
    )
    if r.status_code == 429:
        raise RuntimeError("The assistant's free quota is used up for now. Try again in a minute.")
    if r.status_code == 404:
        raise RuntimeError(f"Model '{model}' was not found. Set [assistant] model in the app secrets.")
    r.raise_for_status()
    data = r.json()
    try:
        return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"]).strip()
    except (KeyError, IndexError, TypeError):
        fb = (data.get("promptFeedback") or {}).get("blockReason")
        raise RuntimeError(f"No answer was returned{' (' + fb + ')' if fb else ''}.")


def _call_openai_compatible(cfg, system, history, question):
    provider = cfg["provider"]
    base = cfg["base_url"] or {"groq": "https://api.groq.com/openai/v1",
                               "openrouter": "https://openrouter.ai/api/v1"}.get(provider, "")
    if not base:
        raise RuntimeError("Set [assistant] base_url in the app secrets for this provider.")
    model = cfg["model"] or {"groq": "llama-3.3-70b-versatile",
                             "openrouter": "meta-llama/llama-3.3-70b-instruct:free"}.get(provider, "")
    msgs = [{"role": "system", "content": system}]
    msgs += [{"role": r, "content": t} for r, t in history]
    msgs.append({"role": "user", "content": question})
    headers = {"Authorization": f"Bearer {cfg['api_key']}"}
    if provider == "openrouter":
        headers["HTTP-Referer"] = "https://adapt2026.streamlit.app"
        headers["X-Title"] = "ADAPT flood risk tool"
    r = requests.post(f"{base.rstrip('/')}/chat/completions", headers=headers,
                      json={"model": model, "messages": msgs, "temperature": 0.3,
                            "max_tokens": int(cfg.get("max_tokens", 700))}, timeout=60)
    if r.status_code == 429:
        raise RuntimeError("The assistant's free quota is used up for now. Try again in a minute.")
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


def ask(cfg, system, history, question):
    if cfg["provider"] == "gemini":
        return _call_gemini(cfg, system, history, question)
    return _call_openai_compatible(cfg, system, history, question)


# ---------------------------------------------------------------------------
# Sidebar panel
# ---------------------------------------------------------------------------
# A small robot mark for the header and the assistant's avatar (inline SVG).
_ROBOT_SVG = (
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'>"
    "<defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'>"
    "<stop offset='0' stop-color='#38bdf8'/><stop offset='1' stop-color='#14b8a6'/></linearGradient></defs>"
    "<circle cx='32' cy='32' r='32' fill='url(#g)'/>"
    "<rect x='17' y='22' width='30' height='24' rx='8' fill='#0f172a'/>"
    "<rect x='29' y='12' width='6' height='8' rx='3' fill='#0f172a'/><circle cx='32' cy='11' r='4' fill='#0f172a'/>"
    "<circle cx='26' cy='33' r='4' fill='#7dd3fc'/><circle cx='38' cy='33' r='4' fill='#7dd3fc'/>"
    "<rect x='25' y='40' width='14' height='3' rx='1.5' fill='#7dd3fc'/>"
    "<rect x='11' y='29' width='5' height='10' rx='2.5' fill='#0f172a'/><rect x='48' y='29' width='5' height='10' rx='2.5' fill='#0f172a'/>"
    "</svg>"
)
_ROBOT_URI = "data:image/svg+xml;utf8," + _ROBOT_SVG.replace("#", "%23").replace("'", "%27").replace("<", "%3C").replace(">", "%3E")
_USER_SVG = (
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'><circle cx='32' cy='32' r='32' fill='#334155'/>"
    "<circle cx='32' cy='25' r='10' fill='#cbd5e1'/><path d='M14 54c2-11 9-16 18-16s16 5 18 16z' fill='#cbd5e1'/></svg>"
)
_USER_URI = "data:image/svg+xml;utf8," + _USER_SVG.replace("#", "%23").replace("'", "%27").replace("<", "%3C").replace(">", "%3E")

_PANEL_CSS = """
<style>
.st-key-assistant_box { background: linear-gradient(180deg, rgba(56,189,248,0.10), rgba(255,255,255,0.03));
  border: 1px solid rgba(125,211,252,0.28); border-radius: 16px; padding: 0.75rem 0.7rem 0.6rem 0.7rem;
  box-sizing: border-box; overflow: hidden; }
/* Streamlit gives every element a pixel width equal to the sidebar; inside a
   padded panel that overflows, so size them to the panel's content box */
.st-key-assistant_box [data-testid="stElementContainer"], .st-key-assistant_box [data-testid="stElementContainer"] > div,
.st-key-assistant_box [data-testid="stChatMessage"], .st-key-assistant_box [data-testid="stChatInput"],
.st-key-assistant_box [data-testid="stButton"], .st-key-assistant_box [data-testid="stMarkdown"],
.st-key-assistant_box [data-testid="stVerticalBlockBorderWrapper"], .st-key-assistant_box [data-testid="stVerticalBlock"] {
  width: 100% !important; max-width: 100% !important; min-width: 0 !important; }
.st-key-assistant_box .adapt-ai-head { display: flex; align-items: center; gap: 0.6rem; margin-bottom: 0.45rem; }
.st-key-assistant_box .adapt-ai-head img { width: 38px; height: 38px; flex: 0 0 38px; filter: drop-shadow(0 4px 10px rgba(56,189,248,0.35)); }
.st-key-assistant_box .adapt-ai-head .t { font-weight: 800; color: #f8fafc !important; font-size: 0.98rem; letter-spacing: 0.02em; line-height: 1.1; }
.st-key-assistant_box .adapt-ai-head .s { font-size: 0.7rem; color: #94a3b8 !important; line-height: 1.25; margin-top: 2px; }
.st-key-assistant_box .adapt-ai-hint { font-size: 0.72rem; color: #94a3b8 !important; margin: 0 0 0.35rem 0; }
.st-key-assistant_box .adapt-ai-ask { font-size: 0.86rem; font-weight: 600; color: #ffffff !important; margin: 0.1rem 0 0.45rem 0; }
/* suggested questions as chips that wrap inside the sidebar */
.st-key-assistant_box [data-testid="stButton"] button {
  width: 100%; min-height: 0; height: auto; padding: 0.38rem 0.6rem; border-radius: 11px; text-align: left;
  background: rgba(15,23,42,0.45); border: 1px solid rgba(125,211,252,0.35); color: #e0f2fe !important;
  white-space: normal !important; line-height: 1.25; box-shadow: none; }
.st-key-assistant_box [data-testid="stButton"] button:hover { background: rgba(56,189,248,0.18); border-color: #7dd3fc; }
.st-key-assistant_box [data-testid="stButton"] button p { font-size: 0.78rem !important; white-space: normal !important;
  text-align: left; color: #e0f2fe !important; margin: 0; }
.st-key-assistant_aud_row { display: flex; flex-direction: row; align-items: baseline; justify-content: space-between; }
.st-key-assistant_aud_row > div { width: auto !important; }
.st-key-assistant_aud_row .adapt-ai-hint { margin: 0; }
.st-key-assistant_aud_row button, .st-key-assistant_clear button { background: none !important; border: none !important;
  padding: 0 0.2rem !important; min-height: 0 !important; width: auto !important; }
.st-key-assistant_aud_row button p { color: #7dd3fc !important; font-size: 0.72rem !important; text-decoration: underline; }
.st-key-assistant_clear button p { color: #94a3b8 !important; font-size: 0.72rem !important; text-align: center !important; }
/* messages */
.st-key-assistant_box [data-testid="stChatMessage"] { background: rgba(255,255,255,0.06); border-radius: 12px;
  padding: 0.45rem 0.55rem; gap: 0.45rem; margin: 0.15rem 0; }
.st-key-assistant_box [data-testid="stChatMessage"] p, .st-key-assistant_box [data-testid="stChatMessage"] li {
  font-size: 0.82rem !important; line-height: 1.4; color: #e2e8f0 !important; }
.st-key-assistant_box [data-testid="stChatMessageAvatarUser"], .st-key-assistant_box [data-testid="stChatMessageAvatarAssistant"],
.st-key-assistant_box [data-testid="stChatMessage"] img { width: 26px !important; height: 26px !important; border-radius: 50%; }
.st-key-assistant_box [data-testid="stChatInput"] { border-radius: 12px; }
.st-key-assistant_box [data-testid="stChatInput"] > div { width: 100% !important; max-width: 100% !important; }
.st-key-assistant_box [data-testid="stChatInputTextArea"] { width: 100% !important; color: #0f172a !important;
  caret-color: #0f172a; -webkit-text-fill-color: #0f172a; background: #ffffff !important; }
.st-key-assistant_box [data-testid="stChatInputTextArea"]::placeholder { color: #64748b !important; -webkit-text-fill-color: #64748b; }
.st-key-assistant_box [data-testid="stChatInput"] > div { background: #ffffff !important; }
.st-key-assistant_box [data-testid="stChatInputSubmitButton"] svg { fill: #0369a1; }
.st-key-assistant_box [data-testid="stChatInput"] textarea { font-size: 0.82rem; }
.st-key-assistant_box [data-testid="stVerticalBlock"] { gap: 0.35rem; }
</style>
"""


def sidebar_panel(module, ctx=None, page_title=""):
    """The Ask ADAPT panel: always on, at the bottom of the sidebar."""
    ss = st.session_state
    cfg = settings()
    if cfg is None:
        if module == "developer":
            st.markdown("<div class='adapt-side-note'>💬 Assistant not configured. Add an <code>[assistant]</code> "
                        "section with <code>provider</code> and <code>api_key</code> to the app secrets "
                        "(see .streamlit/secrets.toml.example).</div>", unsafe_allow_html=True)
            st.divider()
        return
    hist = ss.setdefault("assistant_history", [])
    st.markdown(_PANEL_CSS, unsafe_allow_html=True)
    with st.container(key="assistant_box"):
        st.markdown(
            f"<div class='adapt-ai-head'><img src='{_ROBOT_URI}' alt=''/>"
            "<div><div class='t'>Ask ADAPT</div>"
            "<div class='s'>Your guide to the tool, the methods and this community</div></div></div>",
            unsafe_allow_html=True,
        )
        audience = ss.get("assistant_audience")
        if audience not in ("resident", "expert"):
            # First question: who is asking. It sets the tone and length of every answer.
            st.markdown("<div class='adapt-ai-ask'>First, so I can answer in the right way. Are you...</div>",
                        unsafe_allow_html=True)
            c1, c2 = st.columns(2)
            with c1:
                if st.button("🏠 A resident", key="assistant_aud_res", use_container_width=True):
                    ss["assistant_audience"] = "resident"
                    st.rerun()
            with c2:
                if st.button("🎓 An expert", key="assistant_aud_exp", use_container_width=True):
                    ss["assistant_audience"] = "expert"
                    st.rerun()
            st.divider()
            return
        with st.container(key="assistant_aud_row"):
            st.markdown(f"<div class='adapt-ai-hint'>Answering for {'a resident' if audience == 'resident' else 'an expert'}"
                        "</div>", unsafe_allow_html=True)
            if st.button("change", key="assistant_aud_change"):
                ss.pop("assistant_audience", None)
                ss["assistant_history"] = []
                st.rerun()
        if not hist:
            st.markdown("<div class='adapt-ai-hint'>Try one of these, or type your own question.</div>",
                        unsafe_allow_html=True)
            for q in SUGGESTED[audience]:
                if st.button(q, key=f"assistant_sugg_{abs(hash(q))}", use_container_width=True):
                    ss["assistant_pending"] = q
        for role, text in hist[-2 * MAX_TURNS:]:
            with st.chat_message("user" if role == "user" else "assistant",
                                 avatar=_USER_URI if role == "user" else _ROBOT_URI):
                st.markdown(text)
        q = st.chat_input("Ask a question", key="assistant_input")
        if q:
            ss["assistant_pending"] = q
        pending = ss.pop("assistant_pending", None)
        if pending:
            last = ss.get("_assistant_last_call", 0.0)
            wait = MIN_SECONDS_BETWEEN - (time.time() - last)
            if wait > 0:
                time.sleep(wait)
            with st.chat_message("user", avatar=_USER_URI):
                st.markdown(pending)
            with st.chat_message("assistant", avatar=_ROBOT_URI):
                with st.spinner("Thinking..."):
                    try:
                        cfg_a = dict(cfg, max_tokens=MAX_TOKENS[audience])
                        answer = ask(cfg_a, system_prompt(ctx, module, page_title, audience),
                                     hist[-2 * MAX_TURNS:], pending)
                    except Exception as exc:
                        answer = f"Sorry, the assistant could not answer: {exc}"
                st.markdown(answer)
            ss["_assistant_last_call"] = time.time()
            hist.extend([("user", pending), ("assistant", answer)])
            del hist[:-2 * MAX_TURNS]
            st.rerun()          # redraw with the new exchange in the history, above the input
        if hist and st.button("↺ Start a new conversation", key="assistant_clear", use_container_width=True):
            ss["assistant_history"] = []
            st.rerun()
    st.divider()
