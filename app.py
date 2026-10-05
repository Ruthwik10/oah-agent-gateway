"""OAH Agent Gateway: web console.

streamlit run app.py
"""
from __future__ import annotations

import asyncio
import json
import os

import pandas as pd
import streamlit as st

from oah_gateway import source, tools
from oah_gateway.server import mcp

st.set_page_config(page_title="OAH Agent Gateway", page_icon="💧", layout="wide")

st.markdown("""
<style>
.badge{display:inline-block;padding:2px 10px;border-radius:12px;font-size:0.8rem;font-weight:600;margin-right:6px}
.ok{background:#d9f2e3;color:#14532d}.caution{background:#fdf0cf;color:#713f12}.dnu{background:#fbdcdc;color:#7f1d1d}
.small{font-size:0.85rem;opacity:0.8}
</style>""", unsafe_allow_html=True)

BADGE = {"OK": "ok", "CAUTION": "caution", "DO_NOT_USE": "dnu"}


def badge(level: str) -> str:
    return f'<span class="badge {BADGE[level]}">{level.replace("_", " ")}</span>'


@st.cache_resource(show_spinner="Loading OneAquaHealth FHIR data…")
def load():
    tools.records()
    return tools.data_overview()


ov = load()
src = ov["source"]

st.title("💧 OAH Agent Gateway")
st.caption("Trusted, standards-based access to OneAquaHealth One Health data for AI agents and people. "
           "HL7 FHIR R4 · OAH Implementation Guide · Model Context Protocol")
c1, c2, c3, c4 = st.columns(4)
c1.metric("FHIR Observations", ov["observations"], f'{ov["official_observations"]} official')
c2.metric("OK", ov["trust_summary"].get("OK", 0))
c3.metric("Caution", ov["trust_summary"].get("CAUTION", 0))
c4.metric("Do not use", ov["trust_summary"].get("DO_NOT_USE", 0))
st.markdown(f'<div class="small">Source: <b>{src["mode"]}</b> · {src["server"]} · read {src["fetched_at"]} '
            f'{("· " + src["note"]) if src["note"] else ""}</div>', unsafe_allow_html=True)

tab_agent, tab_city, tab_record, tab_citizen, tab_connect = st.tabs(
    ["🤖 Ask an agent", "🌍 One Health by city", "🔎 Record inspector", "📝 Citizen report → FHIR", "🔌 Connect your agent"])

# ---------------------------------------------------------------- agent
from oah_gateway.agent import NVIDIA_MODEL, agent_provider, run_agent  # noqa: E402


with tab_agent:
    st.markdown("Ask in plain language. The agent can only answer from the gateway's tools, "
                "must cite a FHIR reference for every number, and is told never to quote values that failed integrity checks.")
    examples = ["Give me a One Health summary for Benevento: air quality and population health.",
                "Is the Almyros river in Crete safe to swim in, based on the water chemistry?",
                "Which Crete water records can I trust, and why are the others excluded?",
                "Compare obesity across age groups in Oslo."]
    provider = agent_provider()
    if provider is None:
        st.info("Set NVIDIA_API_KEY (from build.nvidia.com) to chat here. Without a key, use the other tabs, or "
                "connect Claude Desktop or any MCP client to the gateway (see 'Connect your agent').")
    else:
        st.caption(f"Agent model: {NVIDIA_MODEL if provider == 'nvidia' else os.environ.get('OAH_AGENT_MODEL', 'claude-sonnet-5-5')} "
                   f"via {'NVIDIA API (build.nvidia.com)' if provider == 'nvidia' else 'Anthropic API'}")
    if "chat" not in st.session_state:
        st.session_state.chat, st.session_state.msgs = [], []
    cols = st.columns(len(examples))
    clicked = None
    for col, ex in zip(cols, examples):
        if col.button(ex, width="stretch"):
            clicked = ex
    for role, text, trace in st.session_state.chat:
        with st.chat_message(role):
            if trace:
                with st.expander(f"🔧 {len(trace)} tool call(s): " + ", ".join(t[0] for t in trace)):
                    for name, args, out in trace:
                        st.markdown(f"**{name}** `{json.dumps(args)}`")
                        st.json(out, expanded=False)
            st.markdown(text)
    q = st.chat_input("Ask about water, air or population health in the OAH cities…") or clicked
    if q and agent_provider():
        with st.chat_message("user"):
            st.markdown(q)
        trace: list = []
        with st.chat_message("assistant"), st.spinner("Querying the FHIR gateway…"):
            answer, st.session_state.msgs = run_agent(q, st.session_state.msgs, trace)
        st.session_state.chat += [("user", q, None), ("assistant", answer, trace)]
        st.rerun()

# ---------------------------------------------------------------- city
with tab_city:
    cities = [c for c in ov["cities"] if c != "Community-written"]
    city = st.radio("City", cities, horizontal=True)
    snap = tools.one_health_snapshot(city)
    locs = []
    store = source.get_store()
    for r in tools.records():
        if r.city == city and r.location_id:
            loc = store.get("Location", r.location_id) or {}
            pos = loc.get("position")
            if pos:
                locs.append({"lat": pos["latitude"], "lon": pos["longitude"]})
    if locs:
        st.map(pd.DataFrame(locs).drop_duplicates(), zoom=9, size=200)
    for dom, d in snap["domains"].items():
        st.subheader(f"{dom.title()}  ·  {d['observations']} observations")
        if d["excluded_as_unsafe"]:
            st.error(f"{d['excluded_as_unsafe']} records excluded: they failed integrity checks "
                     f"(e.g. {d['excluded_examples'][0]['why']})")
        for a in d["above_reference"]:
            st.warning(f"**{a['indicator']} ({a['period']})**: {a['records_above']} of {a['of']} records above "
                       f"{a['reference']} {a['unit']} ({a['reference_source']}); max {a['max_value']}. {a['note']}")
        if d["latest_safe_values"]:
            df = pd.DataFrame(d["latest_safe_values"])[["indicator", "period", "mean_across_sites_or_cohorts",
                                                        "range", "unit", "records", "trust", "caveats"]]
            st.dataframe(df, width="stretch", hide_index=True)
    st.caption(snap["evidence_limits"])

# ---------------------------------------------------------------- record
with tab_record:
    recs = tools.records()
    level = st.segmented_control("Show", ["DO_NOT_USE", "CAUTION", "OK"], default="DO_NOT_USE")
    subset = [r for r in recs if r.trust.level == level]
    rid = st.selectbox(f"{len(subset)} observations", [r.id for r in subset])
    if rid:
        rep = tools.check_record(rid)
        st.markdown(badge(rep["trust"]["level"]) + f' <b>{rep["indicator"]}</b> · {rep["city"]} · '
                    f'{rep["location_name"] or rep["location_id"]} · {rep["period"]} · written by {rep["written_by"]}',
                    unsafe_allow_html=True)
        for reason in rep["trust"]["reasons"]:
            st.markdown(f"- {reason}")
        if rep["trust"].get("hint"):
            st.info(rep["trust"]["hint"])
        a, b = st.columns(2)
        a.markdown("**Values**")
        a.json({"stats": rep["stats"], "value": rep["value"], "unit": rep["unit"], "reference": rep["reference"]})
        b.markdown("**Source FHIR resource**")
        b.json(source.get_store().get("Observation", rid), expanded=False)

# ---------------------------------------------------------------- citizen
with tab_citizen:
    st.markdown("A citizen describes what they saw. The gateway drafts an **ObservationIndicatorsOah** resource and a "
                "**Provenance** that records the citizen as author and the AI as assembler. Nothing is recorded until "
                "the citizen confirms.")
    store = source.get_store()
    loc_opts = {f'{l.get("name")} ({l["id"]})': l["id"] for l in store.all("Location")
                if l["id"].startswith("Loc-") and l.get("position")}
    loc_label = st.selectbox("Site", list(loc_opts))
    ind = st.selectbox("What did you observe?", list(tools.CITIZEN_INDICATORS),
                       format_func=lambda c: tools.CITIZEN_INDICATORS[c])
    text = st.text_area("Describe it", "White foam along the bank below the outfall, with a sewage smell.")
    num = None
    if ind == "waterTemperature":
        num = st.number_input("Water temperature (°C)", -2.0, 45.0, 14.0)
    if st.button("Draft FHIR observation"):
        st.session_state.draft = tools.draft_citizen_observation(loc_opts[loc_label], ind, text, num,
                                                                 "Cel" if num is not None else None)
    d = st.session_state.get("draft")
    if d and "draft_id" in d:
        a, b = st.columns(2)
        a.markdown("**Observation** (OAH IG profile)")
        a.json(d["observation"])
        b.markdown("**Provenance** (who did what)")
        b.json(d["provenance"])
        ok = st.checkbox("I confirm this describes what I observed.")
        if st.button("Record observation", disabled=not ok):
            st.json(tools.approve_draft(d["draft_id"], confirmed_by_human=ok))

# ---------------------------------------------------------------- connect
with tab_connect:
    st.markdown("Any MCP-capable agent (Claude Desktop, IDE agents, custom agents) can use the same tools.")
    st.code(json.dumps({"mcpServers": {"oah-gateway": {
        "command": "python", "args": ["-m", "oah_gateway.server"],
        "cwd": "/path/to/oah-agent-gateway"}}}, indent=2), language="json")
    st.markdown("Remote agents: `python -m oah_gateway.server --http` serves MCP over HTTP at `http://localhost:8765/mcp`.")
    st.markdown("**Tools published to agents**")
    for t in asyncio.run(mcp.list_tools()):
        st.markdown(f"- `{t.name}`: {t.description.splitlines()[0]}")
