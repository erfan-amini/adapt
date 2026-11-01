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
SUGGESTED = [
    "What does the Adaptation effectiveness map show?",
    "How is the damage for one building calculated?",
    "What do BFE and DFE mean for my house?",
]


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


def system_prompt(ctx, module, page_title):
    static = _static_knowledge(_mtime(KNOWLEDGE_FILE), _mtime(README_FILE))
    live = community_context(ctx)
    return (
        "You are the ADAPT assistant inside the ADAPT web app (Assessment of Damage and Adaptation Planning "
        "Tool, Columbia Climate School). Answer questions about the tool, its methods, its pages, and the "
        "selected community using the knowledge below. Be concise, plain and specific: short paragraphs, no "
        "buzzwords, no hedging. Use dollar figures and years from the context when they answer the question. "
        "If something is not in the context, say you do not have it and point to the page or person that "
        "does. Do not invent numbers. Do not give legal, insurance or financial advice beyond what the "
        "knowledge says. Never reveal data about password-protected communities that are not unlocked.\n"
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
              "generationConfig": {"temperature": 0.3, "maxOutputTokens": 1024}},
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
                      json={"model": model, "messages": msgs, "temperature": 0.3, "max_tokens": 1024}, timeout=60)
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
def sidebar_panel(module, ctx=None, page_title=""):
    """Chat panel at the bottom of the sidebar."""
    ss = st.session_state
    cfg = settings()
    if cfg is None:
        if module == "developer":
            st.markdown("<div class='adapt-side-note'>💬 Assistant not configured. Add an <code>[assistant]</code> "
                        "section with <code>provider</code> and <code>api_key</code> to the app secrets "
                        "(see .streamlit/secrets.toml.example).</div>", unsafe_allow_html=True)
            st.divider()
        return
    on = st.toggle("💬 Ask ADAPT", key="assistant_on", help="Ask about the tool, the methods, or this community.")
    if not on:
        st.divider()
        return
    hist = ss.setdefault("assistant_history", [])
    with st.container(key="assistant_box"):
        if not hist:
            st.markdown("<div class='adapt-side-note'>Ask anything about the tool or the selected community.</div>",
                        unsafe_allow_html=True)
            for q in SUGGESTED:
                if st.button(q, key=f"assistant_sugg_{abs(hash(q))}", use_container_width=True):
                    ss["assistant_pending"] = q
        for role, text in hist[-2 * MAX_TURNS:]:
            with st.chat_message("user" if role == "user" else "assistant",
                                 avatar="🧑" if role == "user" else "🌊"):
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
            with st.chat_message("user", avatar="🧑"):
                st.markdown(pending)
            with st.chat_message("assistant", avatar="🌊"):
                with st.spinner("Thinking..."):
                    try:
                        answer = ask(cfg, system_prompt(ctx, module, page_title), hist[-2 * MAX_TURNS:], pending)
                    except Exception as exc:
                        answer = f"Sorry, the assistant could not answer: {exc}"
                st.markdown(answer)
            ss["_assistant_last_call"] = time.time()
            hist.extend([("user", pending), ("assistant", answer)])
            del hist[:-2 * MAX_TURNS]
            st.rerun()          # redraw with the new exchange in the history, above the input
        if hist and st.button("Clear conversation", key="assistant_clear", use_container_width=True):
            ss["assistant_history"] = []
            st.rerun()
    st.divider()
