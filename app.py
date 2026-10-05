"""OAH Agent Gateway: judge-facing web console.

Run with reproducible demo data:
    OAH_SOURCE=snapshot streamlit run app.py
"""
from __future__ import annotations

import asyncio
import html
import json
import os
import re
from pathlib import Path

import pandas as pd
import streamlit as st

from oah_gateway import source, tools
from oah_gateway.server import mcp

st.set_page_config(page_title="OAH Agent Gateway", page_icon="💧", layout="wide")

st.markdown(
    """
<style>
:root { --oah-blue:#0b4f6c; --oah-teal:#087f8c; --oah-ink:#102a43; --oah-soft:#eef8fa; }
.block-container {padding-top:1.5rem; padding-bottom:3rem; max-width:1280px}
.hero {background:linear-gradient(125deg,#073b4c 0%,#0b6e75 58%,#14919b 100%);color:white;
       padding:2.1rem 2.4rem;border-radius:22px;margin-bottom:1rem;box-shadow:0 14px 34px rgba(7,59,76,.18)}
.hero-kicker {font-size:.78rem;letter-spacing:.12em;text-transform:uppercase;font-weight:700;opacity:.8}
.hero h1 {font-size:2.65rem;line-height:1.05;margin:.35rem 0 .25rem;color:white}
.hero h2 {font-size:1.25rem;margin:0 0 .8rem;color:#d7f5f5;font-weight:600}
.hero p {max-width:920px;font-size:1.04rem;line-height:1.55;margin:0;color:#effcfc}
.thesis {border:1px solid #b7dde2;background:linear-gradient(100deg,#f3fbfc,#ffffff);color:#243b53;padding:1rem 1.2rem;
         border-radius:16px;margin:.75rem 0 1rem}
.thesis-title {font-size:1.3rem;font-weight:800;color:var(--oah-ink);margin-bottom:.25rem}
.thesis-ref {font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.82rem;color:#486581}
.demo-banner {background:#e5f7ed;border:1px solid #9dd7b2;color:#174f2a;padding:.65rem 1rem;
              border-radius:12px;margin:.5rem 0 1rem;font-size:.9rem}
.badge {display:inline-block;padding:3px 10px;border-radius:999px;font-size:.75rem;font-weight:750;margin-right:5px}
.ok {background:#d9f2e3;color:#14532d}.caution {background:#fff0c2;color:#713f12}.dnu {background:#fbdcdc;color:#7f1d1d}
.arch {display:grid;grid-template-columns:1.05fr auto 1fr auto 1.5fr auto 1.1fr;gap:.55rem;align-items:stretch;margin:.75rem 0 1rem}
.arch-box {border:1px solid #c9dce2;border-radius:14px;padding:.8rem;background:white;color:#243b53;text-align:center;
           display:flex;flex-direction:column;justify-content:center;min-height:82px}
.arch-box strong {color:#0b4f6c}.arch-box.gateway {border:2px solid #14919b;background:#eefbfb}
.arch-arrow {display:flex;align-items:center;color:#087f8c;font-size:1.35rem;font-weight:800}
.platform-grid {display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.8rem;margin:.7rem 0 1rem}
.platform-card {border:1px solid #b7dbe0;border-radius:14px;padding:1rem 1.1rem;background:#f7fcfc;color:#243b53}
.platform-card strong {display:block;color:#0b4f6c;font-size:1.02rem;margin-bottom:.25rem}
.platform-flow {font-family:ui-monospace,SFMono-Regular,Menlo,monospace;color:#087f8c;font-weight:750;margin-bottom:.4rem}
.principle-grid {display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:.65rem;margin:.65rem 0 1rem}
.principle {border:1px solid #d9e2ec;border-radius:12px;padding:.8rem;background:white;color:#486581;font-size:.88rem}
.principle strong {display:block;color:#102a43;margin-bottom:.25rem}
.demo-grid {display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.7rem;margin:.4rem 0}
.demo-step {border:1px solid #c9dce2;border-radius:13px;padding:.85rem;background:#fff;color:#486581}
.demo-step strong {display:block;color:#0b4f6c;margin-bottom:.3rem}
.trust-grid {display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.7rem;margin:.6rem 0}
.trust-cell {border:1px solid #d9e2ec;border-radius:12px;padding:.8rem;background:#fff;min-height:82px}
.trust-label {font-size:.72rem;text-transform:uppercase;letter-spacing:.06em;color:#627d98;font-weight:700}
.trust-value {font-size:1rem;color:#102a43;font-weight:700;margin-top:.28rem;overflow-wrap:anywhere}
.trust-fail {color:#9b1c1c}.trust-pass {color:#166534}.trust-neutral {color:#334e68}
.section-note {color:#486581;font-size:.9rem;margin-top:-.35rem}
.step-label {display:inline-block;background:#0b6e75;color:white;border-radius:999px;padding:2px 9px;
             font-size:.72rem;font-weight:750;margin-right:.35rem}
.role-card {border:1px solid #c9dce2;border-radius:12px;padding:.7rem .9rem;background:#f8fbfc;color:#243b53}
.flow-line {font-size:1.05rem;text-align:center;padding:.95rem;border:1px solid #c9dce2;border-radius:14px;background:#f8fbfc;color:#243b53}
div[data-testid="stMetric"] {background:#f8fbfc;border:1px solid #d9e2ec;padding:.72rem .85rem;border-radius:14px}
div[data-testid="stMetric"] * {color:#102a43}
div[data-testid="stExpander"] {border-color:#d9e2ec}
@media(max-width:900px){.arch{grid-template-columns:1fr}.arch-arrow{justify-content:center;transform:rotate(90deg)}
                         .platform-grid,.principle-grid,.demo-grid,.trust-grid{grid-template-columns:1fr}.hero h1{font-size:2rem}}
</style>
""",
    unsafe_allow_html=True,
)

BADGE = {"OK": "ok", "CAUTION": "caution", "DO_NOT_USE": "dnu"}
TRUST_LEVELS = set(BADGE)


def badge(level: str) -> str:
    css = BADGE.get(level, "caution")
    return f'<span class="badge {css}">{html.escape(level.replace("_", " "))}</span>'


def fmt_number(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:,.3f}".rstrip("0").rstrip(".")
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def display_unit(unit: str | None) -> str:
    return {"Cel": "°C", "ug/m3": "µg/m³"}.get(unit or "", unit or "")


def unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(x for x in items if x))


@st.cache_resource(show_spinner="Loading OneAquaHealth FHIR data…")
def load():
    tools.records()
    return tools.data_overview()


def evidence_from_trace(trace: list) -> dict:
    """Extract display-only evidence already present in gateway tool results."""
    evidence = {"tools": [], "sources": [], "trust": [], "caveats": [], "refs": [], "excluded": 0}

    def walk(node) -> None:
        if isinstance(node, dict):
            src = node.get("source")
            if isinstance(src, dict) and src.get("mode"):
                date = str(src.get("fetched_at", ""))[:10]
                evidence["sources"].append(f"{src['mode']} · {date}" if date else src["mode"])
            for key, value in node.items():
                if key == "fhir_ref" and isinstance(value, str):
                    evidence["refs"].append(value)
                elif key == "fhir_refs" and isinstance(value, list):
                    evidence["refs"].extend(x for x in value if isinstance(x, str))
                elif key == "trust":
                    if isinstance(value, str) and value in TRUST_LEVELS:
                        evidence["trust"].append(value)
                    elif isinstance(value, list):
                        evidence["trust"].extend(x for x in value if x in TRUST_LEVELS)
                    elif isinstance(value, dict) and value.get("level") in TRUST_LEVELS:
                        evidence["trust"].append(value["level"])
                elif key in {"caveats", "trust_reasons", "reasons"} and isinstance(value, list):
                    evidence["caveats"].extend(str(x) for x in value)
                elif key == "evidence_limits" and isinstance(value, str):
                    evidence["caveats"].append(value)
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for name, _args, output in trace:
        evidence["tools"].append(name)
        if isinstance(output, dict):
            if isinstance(output.get("domains"), dict):
                evidence["excluded"] += sum(
                    int(d.get("excluded_as_unsafe", 0)) for d in output["domains"].values()
                )
            elif isinstance(output.get("do_not_use"), int):
                evidence["excluded"] += output["do_not_use"]
            elif isinstance(output.get("trust"), dict) and output["trust"].get("level") == "DO_NOT_USE":
                evidence["excluded"] += 1
        walk(output)
    for key in ("tools", "sources", "trust", "caveats", "refs"):
        evidence[key] = unique(evidence[key])
    return evidence


def render_evidence_panel(trace: list) -> None:
    ev = evidence_from_trace(trace)
    with st.container(border=True):
        st.markdown("##### Evidence panel")
        st.caption("Question → agent → gateway tools → deterministic trust → FHIR evidence → answer")
        a, b, c = st.columns(3)
        a.markdown("**Gateway tools**")
        a.write(", ".join(f"`{x}`" for x in ev["tools"]) or "—")
        b.markdown("**Data source**")
        b.write(" · ".join(ev["sources"]) or "Reported by tool result")
        c.markdown("**Trust represented**")
        c.markdown("".join(badge(x) for x in ev["trust"]) or "No record verdict returned", unsafe_allow_html=True)
        if ev["excluded"]:
            st.error(f"{ev['excluded']} unsafe record(s) were excluded from factual summaries.")
        if ev["caveats"]:
            st.markdown("**Caveats carried into the answer**")
            for caveat in ev["caveats"][:4]:
                st.markdown(f"- {caveat}")
        if ev["refs"]:
            st.markdown("**FHIR evidence**")
            st.caption(" · ".join(ev["refs"][:12]) + (f" · +{len(ev['refs']) - 12} more" if len(ev["refs"]) > 12 else ""))
            st.markdown("**Answer → evidence → source record**")
            for ref in ev["refs"][:12]:
                if not ref.startswith("Observation/"):
                    st.caption(ref)
                    continue
                report = tools.check_record(ref)
                raw = source.get_store().get("Observation", ref.split("/", 1)[1]) or {}
                if "error" in report:
                    st.caption(f"{ref} · source record unavailable in the selected data source")
                    continue
                stats = report.get("stats") or {}
                value = report.get("value")
                if value is None:
                    value = stats.get("average")
                unit = display_unit(report.get("unit"))
                level = report["trust"]["level"]
                with st.expander(f"{ref} · {report['indicator']} · {level.replace('_', ' ')}"):
                    left, middle, right = st.columns(3)
                    left.markdown(f"**Indicator**  \n{report['indicator']}")
                    middle.markdown(f"**Published value**  \n{fmt_number(value)} {unit}" if value is not None else "**Published value**  \nSee source JSON")
                    right.markdown(f"**Trust verdict**  \n{badge(level)}", unsafe_allow_html=True)
                    st.caption(
                        f"{report['city']} · {report.get('location_name') or report.get('location_id')} · "
                        f"{report['period']} · {report.get('written_by')}"
                    )
                    st.markdown("**Deterministic trust reasons / caveats**")
                    for reason in report["trust"]["reasons"]:
                        st.markdown(f"- {reason}")
                    context = report.get("reference")
                    if context:
                        st.caption(
                            f"Reference context: {fmt_number(context.get('value_in_record_unit', context.get('value')))} "
                            f"{display_unit(context.get('unit'))} · {context.get('source')} · {context.get('note')}"
                        )
                    source_info = report.get("source") or {}
                    st.caption(
                        f"Source: {source_info.get('mode', 'selected source')} · "
                        f"{str(source_info.get('fetched_at', ''))[:10]} · {source_info.get('server', '')}"
                    )
                    st.markdown("**Raw FHIR JSON**")
                    st.json(raw, expanded=False)


def render_raw_trace(trace: list) -> None:
    with st.expander(f"Detailed gateway trace · {len(trace)} tool call(s)"):
        for name, args, output in trace:
            st.markdown(f"**{name}** `{json.dumps(args)}`")
            st.json(output, expanded=False)


def render_indicator(row: dict) -> None:
    value = fmt_number(row.get("mean_across_sites_or_cohorts"))
    raw_unit = row.get("unit") or ""
    unit = display_unit(raw_unit)
    trust = row.get("trust") or []
    refs = row.get("fhir_refs") or []
    with st.container(border=True):
        st.markdown(f"**{row['indicator']}**")
        st.markdown(f"### {value} {unit}")
        st.caption(f"Latest safe aggregate · {row['period']} · {row['records']} record(s) · range {row.get('range')}")
        st.markdown("".join(badge(x) for x in trust), unsafe_allow_html=True)
        for caveat in (row.get("caveats") or [])[:2]:
            st.warning(caveat)
        reference = row.get("reference")
        if reference:
            ref_value = reference.get("value_in_record_unit", reference.get("value"))
            ref_unit = unit if "value_in_record_unit" in reference else display_unit(reference.get("unit"))
            st.caption(f"Reference context: {fmt_number(ref_value)} {ref_unit} · {reference['source']} · {reference['note']}")
        if refs:
            st.caption("FHIR: " + " · ".join(refs[:3]) + (f" · +{len(refs) - 3} more" if len(refs) > 3 else ""))


def render_domain(domain: str, data: dict) -> None:
    label = {"water": "Water", "air": "Air", "health": "Community health", "stream-assessment": "Stream assessment"}.get(
        domain, domain.title()
    )
    st.markdown(f"#### {label} · {data['observations']} observations")
    if data["excluded_as_unsafe"]:
        st.error(f"{data['excluded_as_unsafe']} record(s) excluded by deterministic integrity checks.")
        with st.expander("Excluded FHIR evidence"):
            for item in data["excluded_examples"]:
                st.markdown(f"- `{item['fhir_ref']}` — {item['why']}")
    for alert in data["above_reference"]:
        st.warning(
            f"**{alert['indicator']} ({alert['period']})** — {alert['records_above']} of {alert['of']} records above "
            f"{alert['reference']} {alert['unit']} ({alert['reference_source']}); maximum {alert['max_value']}. "
            f"{alert['note']}"
        )
    for row in data["latest_safe_values"]:
        render_indicator(row)


def render_trust_trace(report: dict, raw: dict) -> None:
    reasons = report["trust"]["reasons"]
    stats = report.get("stats") or {}
    # Presentation-only grouping of reasons emitted by model.check(); this never computes a verdict.
    statistical = [r for r in reasons if any(x in r.lower() for x in ("median", "average", "minimum", "maximum", "standard deviation", "range"))]
    physical = [r for r in reasons if any(x in r.lower() for x in ("water temperature outside", "ph outside", "percentage outside", "negative concentration"))]
    ratio_reason = next((r for r in reasons if "differ by a factor" in r), "")
    ratio_match = re.search(r"factor of ([\d,]+)", ratio_reason)
    ratio = f"{ratio_match.group(1)}×" if ratio_match else "Not reported"
    structured = raw.get("resourceType") == "Observation"
    profile = (raw.get("meta", {}).get("profile") or ["FHIR Observation"])[0].split("/")[-1]
    unit = display_unit(report.get("unit"))

    st.markdown("### Trust Trace")
    st.caption("A presentation of the existing deterministic trust result. The UI and AI cannot alter this verdict.")
    cells = [
        ("FHIR structure", f"Structured FHIR Observation · {profile}" if structured else "Structure not available", "trust-pass" if structured else "trust-neutral"),
        ("Origin", "Official OneAquaHealth IG instance" if report.get("official") else f"Third-party/shared sandbox · {report.get('written_by')}", "trust-pass" if report.get("official") else "trust-neutral"),
        ("FHIR reference", report["fhir_ref"], "trust-neutral"),
        ("Published mean", f"{fmt_number(stats.get('average'))} {unit}", "trust-neutral"),
        ("Published median", f"{fmt_number(stats.get('median'))} {unit}", "trust-neutral"),
        ("Average / median", ratio, "trust-fail" if ratio_match else "trust-neutral"),
        ("Physical plausibility", "FAILED" if physical else "No failure reported", "trust-fail" if physical else "trust-pass"),
        ("Statistical consistency", "FAILED" if statistical else "No failure reported", "trust-fail" if statistical else "trust-pass"),
        ("Deterministic verdict", report["trust"]["level"].replace("_", " "), "trust-fail" if report["trust"]["level"] == "DO_NOT_USE" else "trust-neutral"),
    ]
    cards = "".join(
        f'<div class="trust-cell"><div class="trust-label">{html.escape(label)}</div>'
        f'<div class="trust-value {css}">{html.escape(value)}</div></div>'
        for label, value, css in cells
    )
    st.markdown(f'<div class="trust-grid">{cards}</div>', unsafe_allow_html=True)
    st.markdown(f"### {badge(report['trust']['level'])}", unsafe_allow_html=True)
    st.markdown("**Deterministic reasons**")
    for reason in reasons:
        st.markdown(f"- {reason}")
    if report["trust"].get("hint"):
        st.info(
            "Pattern is consistent with a lost decimal separator. This is a hypothesis only. "
            "The gateway never silently corrects source data."
        )


ov = load()
src = ov["source"]
snapshot_date = str(src.get("fetched_at", ""))[:10] or "unknown"
hero_record = tools.check_record("Obs-Almyros-TemperatureWater-2013")
hero_mean = fmt_number((hero_record.get("stats") or {}).get("average"))
hero_median = fmt_number((hero_record.get("stats") or {}).get("median"))

# ---------------------------------------------------------------- landing / thesis
st.markdown(
    """
<div class="hero">
  <div class="hero-kicker">IEEE OneAquaHealth Global Hackathon · Track 7</div>
  <h1>OAH Agent Gateway</h1>
  <h2>Trusted One Health interoperability for AI agents</h2>
  <p>Query environmental and population-health data through the official OneAquaHealth FHIR standard,
  automatically detect unsafe evidence, and turn citizen observations back into standards-compliant FHIR
  with human approval.</p>
</div>
""",
    unsafe_allow_html=True,
)

st.markdown(
    f"""
<div class="thesis">
  <div class="thesis-title">FHIR-valid ≠ trustworthy</div>
  <div>An official, structurally valid OAH FHIR Observation publishes a mean water temperature of
  <strong>{hero_mean} °C</strong> beside a median of <strong>{hero_median} °C</strong>. Structural interoperability
  gets data to an agent; deterministic integrity checks decide whether the agent may safely use it.</div>
  <div class="thesis-ref">{html.escape(hero_record.get('fhir_ref', 'Observation unavailable'))}</div>
</div>
""",
    unsafe_allow_html=True,
)

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Observations", ov["observations"])
m2.metric("Official records", ov["official_observations"])
m3.metric("OK", ov["trust_summary"].get("OK", 0))
m4.metric("CAUTION", ov["trust_summary"].get("CAUTION", 0))
m5.metric("DO NOT USE", ov["trust_summary"].get("DO_NOT_USE", 0))

if src["mode"] == "snapshot":
    st.markdown(
        f'<div class="demo-banner"><strong>Reproducible demo</strong> · bundled OneAquaHealth snapshot from '
        f'{html.escape(snapshot_date)}. The public FHIR sandbox remains supported through live/auto mode.<br>'
        f'<strong>Data source:</strong> OneAquaHealth / HL7 Europe · <strong>Snapshot date:</strong> {html.escape(snapshot_date)}</div>',
        unsafe_allow_html=True,
    )
else:
    st.caption(f"Data source: OneAquaHealth / HL7 Europe · live FHIR sandbox · fetched {src['fetched_at']}")

st.markdown("#### One interoperable safety layer")
st.markdown(
    """
<div class="arch">
  <div class="arch-box"><strong>People & systems</strong><span>Citizen · Researcher · Official · External agent</span></div>
  <div class="arch-arrow">→</div>
  <div class="arch-box"><strong>Agent interface</strong><span>Web agent or MCP client</span></div>
  <div class="arch-arrow">→</div>
  <div class="arch-box gateway"><strong>OAH Agent Gateway</strong><span>Query tools · deterministic trust · IG terminology · citizen FHIR drafting</span></div>
  <div class="arch-arrow">→</div>
  <div class="arch-box"><strong>OneAquaHealth FHIR</strong><span>Read environmental + population health<br>Write only after human approval</span></div>
</div>
""",
    unsafe_allow_html=True,
)

st.markdown("#### Bidirectional interoperability")
st.markdown(
    """
<div class="platform-grid">
  <div class="platform-card">
    <strong>Safe consumption</strong>
    <div class="platform-flow">OAH FHIR → Trusted Agent Access</div>
    Existing standardized evidence passes through deterministic trust checks before an AI agent or MCP client uses it.
  </div>
  <div class="platform-card">
    <strong>Standards-based contribution</strong>
    <div class="platform-flow">Citizen Observation → Human-approved FHIR</div>
    AI assists with structure and Provenance; a human explicitly authorizes the resulting FHIR Bundle or write.
  </div>
</div>
<p class="section-note">The gateway supports both safe consumption of existing standardized data and standards-based contribution of new citizen observations.</p>
""",
    unsafe_allow_html=True,
)

st.markdown("#### Why this is different from a generic AI chatbot")
st.markdown(
    """
<div class="principle-grid">
  <div class="principle"><strong>Standards-native</strong>Works over OneAquaHealth FHIR resources and IG terminology.</div>
  <div class="principle"><strong>Deterministic trust</strong>AI does not decide whether evidence is usable.</div>
  <div class="principle"><strong>Source-preserving</strong>Answers retain FHIR references and caveats.</div>
  <div class="principle"><strong>Controlled write-back</strong>AI can draft, but humans authorize writes.</div>
</div>
""",
    unsafe_allow_html=True,
)

with st.expander("Try the 3-minute demo"):
    st.markdown(
        """
<div class="demo-grid">
  <div class="demo-step"><strong>1 · Catch bad FHIR evidence</strong>Open <b>Record inspector</b> and select <code>Obs-Almyros-TemperatureWater-2013</code>.</div>
  <div class="demo-step"><strong>2 · Ask a One Health question</strong>In <b>Ask an agent</b>, use the Benevento example to combine air and population-health evidence.</div>
  <div class="demo-step"><strong>3 · Create a citizen FHIR record</strong>Open <b>Citizen report → FHIR</b>, draft a report, inspect Provenance, then confirm it.</div>
</div>
""",
        unsafe_allow_html=True,
    )
    st.code("Give me a One Health summary for Benevento: air quality and population health.", language=None)

with st.expander("Standards & safety architecture"):
    st.markdown(
        "**Standards:** `HL7 FHIR R4` · `OneAquaHealth FHIR IG` · `ObservationIndicatorsOah` · "
        "`ObservationHealthMeasureOah` · `GroupOah` · `Provenance` · `UCUM` · `observation-statistics` · `MCP`"
    )
    s1, s2, s3, s4 = st.columns(4)
    s1.markdown("**Deterministic trust**  \nAI cannot decide record validity.")
    s2.markdown("**Evidence preservation**  \nAnswers retain FHIR references.")
    s3.markdown("**Human-in-the-loop write-back**  \nCitizen data needs explicit approval.")
    s4.markdown("**No patient-level data**  \nPopulation health is aggregated.")
    st.markdown("**Reusable evidence contract**")
    st.markdown(
        "1. Cite FHIR evidence for factual numbers.  \n"
        "2. Distinguish `OK`, `CAUTION`, and `DO_NOT_USE`.  \n"
        "3. Never state `DO_NOT_USE` data as trusted fact.  \n"
        "4. Carry every `CAUTION` caveat into the answer.  \n"
        "5. Do not infer causation from observational One Health data."
    )

tab_agent, tab_city, tab_record, tab_citizen, tab_connect = st.tabs(
    ["🤖 Ask an agent", "🌍 One Health by city", "🔎 Record inspector", "📝 Citizen report → FHIR", "🔌 Connect your agent"]
)

# ---------------------------------------------------------------- agent
from oah_gateway.agent import NVIDIA_MODEL, agent_provider, run_agent  # noqa: E402

with tab_agent:
    st.subheader("Ask with evidence, not just prose")
    st.markdown(
        "The agent answers only through gateway tools. Every number must retain a FHIR reference, "
        "CAUTION values carry their caveat, and DO_NOT_USE values cannot be stated as facts."
    )
    examples = [
        "Give me a One Health summary for Benevento: air quality and population health.",
        "Is the Almyros river in Crete safe to swim in, based on the water chemistry?",
        "Which Crete water records can I trust, and why are the others excluded?",
        "Compare obesity across age groups in Oslo.",
    ]
    provider = agent_provider()
    if provider is None:
        st.info(
            "Set NVIDIA_API_KEY (from build.nvidia.com) to chat here. Without a key, use the deterministic "
            "exploration tabs or connect any MCP client to the same gateway."
        )
    else:
        model = NVIDIA_MODEL if provider == "nvidia" else os.environ.get("OAH_AGENT_MODEL", "claude-sonnet-5-5")
        st.caption(f"Agent model: {model} via {'NVIDIA API (build.nvidia.com)' if provider == 'nvidia' else 'Anthropic API'}")
    if "chat" not in st.session_state:
        st.session_state.chat, st.session_state.msgs = [], []
    cols = st.columns(len(examples))
    clicked = None
    for col, example in zip(cols, examples):
        if col.button(example, width="stretch"):
            clicked = example
    for role, text, trace in st.session_state.chat:
        with st.chat_message(role):
            st.markdown(text)
            if trace:
                render_evidence_panel(trace)
                render_raw_trace(trace)
    question = st.chat_input("Ask about water, air or population health in the OAH cities…") or clicked
    if question and provider:
        with st.chat_message("user"):
            st.markdown(question)
        trace: list = []
        with st.chat_message("assistant"), st.spinner("Querying the FHIR gateway…"):
            answer, st.session_state.msgs = run_agent(question, st.session_state.msgs, trace)
        st.session_state.chat += [("user", question, None), ("assistant", answer, trace)]
        st.rerun()

# ---------------------------------------------------------------- city
with tab_city:
    st.subheader("One interoperable view of environment and population health")
    city_order = [c for c in ("Crete", "Benevento") if c in ov["cities"]]
    city = st.radio("City", city_order, horizontal=True)
    snapshot = tools.one_health_snapshot(city)
    locations = []
    store = source.get_store()
    for record in tools.records():
        if record.city == city and record.location_id:
            location = store.get("Location", record.location_id) or {}
            position = location.get("position")
            if position:
                locations.append({"lat": position["latitude"], "lon": position["longitude"]})
    if locations:
        st.map(pd.DataFrame(locations).drop_duplicates(), zoom=9, size=180)

    env_col, health_col = st.columns(2, gap="large")
    with env_col:
        st.markdown("### Environment")
        st.markdown('<div class="section-note">Water, air, and stream evidence from the same FHIR layer.</div>', unsafe_allow_html=True)
        for domain in ("water", "air", "stream-assessment"):
            if domain in snapshot["domains"]:
                render_domain(domain, snapshot["domains"][domain])
    with health_col:
        st.markdown("### Population Health")
        st.markdown('<div class="section-note">Aggregated cohort indicators—never patient-level records.</div>', unsafe_allow_html=True)
        if "health" in snapshot["domains"]:
            render_domain("health", snapshot["domains"]["health"])
        else:
            st.info("No population-health indicators are available for this city in the selected source.")
    st.warning("**Evidence limit:** Aggregated observational indicators show co-occurrence, not causation.")

# ---------------------------------------------------------------- record
with tab_record:
    st.subheader("Inspect how a trust verdict was produced")
    records = tools.records()
    level = st.segmented_control("Show", ["DO_NOT_USE", "CAUTION", "OK"], default="DO_NOT_USE")
    subset = [r for r in records if r.trust.level == level]
    record_id = st.selectbox(f"{len(subset)} observations", [r.id for r in subset])
    if record_id:
        report = tools.check_record(record_id)
        raw = source.get_store().get("Observation", record_id) or {}
        st.markdown(
            badge(report["trust"]["level"])
            + f' <b>{html.escape(report["indicator"])}</b> · {html.escape(report["city"])} · '
            + f'{html.escape(str(report["location_name"] or report["location_id"]))} · {html.escape(report["period"])}',
            unsafe_allow_html=True,
        )
        render_trust_trace(report, raw)
        st.markdown("#### Published values")
        st.json({"stats": report["stats"], "value": report["value"], "unit": report["unit"], "reference": report["reference"]})
        st.markdown("#### Raw FHIR JSON")
        st.json(raw, expanded=False)

# ---------------------------------------------------------------- citizen
with tab_citizen:
    st.subheader("Citizen observation → human-approved FHIR")
    st.info("AI assistance structures the report. Human authorization controls whether anything may be written.")
    st.markdown('<span class="step-label">1</span> **Citizen observation**', unsafe_allow_html=True)
    st.caption("A person describes what they directly observed at a known OAH location.")
    store = source.get_store()
    location_options = {
        f'{location.get("name")} ({location["id"]})': location["id"]
        for location in store.all("Location")
        if location["id"].startswith("Loc-") and location.get("position")
    }
    location_label = st.selectbox("Site", list(location_options))
    indicator = st.selectbox(
        "What did you observe?", list(tools.CITIZEN_INDICATORS), format_func=lambda code: tools.CITIZEN_INDICATORS[code]
    )
    description = st.text_area("Describe it", "White foam along the bank below the outfall, with a sewage smell.")
    number = None
    if indicator == "waterTemperature":
        number = st.number_input("Water temperature (°C)", -2.0, 45.0, 14.0)
    if st.button("Draft FHIR observation"):
        st.session_state.draft = tools.draft_citizen_observation(
            location_options[location_label], indicator, description, number, "Cel" if number is not None else None
        )
        st.session_state.pop("approval", None)

    draft = st.session_state.get("draft")
    if draft and "draft_id" in draft:
        st.divider()
        st.markdown('<span class="step-label">2</span> **AI-assisted structured draft**', unsafe_allow_html=True)
        st.caption("The gateway assembles an ObservationIndicatorsOah resource; it does not authorize a write.")
        st.json(draft["observation"], expanded=False)

        st.markdown('<span class="step-label">3</span> **Provenance**', unsafe_allow_html=True)
        p1, p2, p3 = st.columns(3)
        p1.markdown('<div class="role-card"><strong>Citizen = author</strong><br>The person remains the source of the observation.</div>', unsafe_allow_html=True)
        p2.markdown('<div class="role-card"><strong>OAH Agent Gateway = assembler</strong><br>AI structures the report without impersonating its author.</div>', unsafe_allow_html=True)
        p3.markdown('<div class="role-card"><strong>Human = approval authority</strong><br>Only explicit confirmation can release the output.</div>', unsafe_allow_html=True)
        st.caption(
            "The gateway assists with structuring; it does not impersonate the citizen. Provenance stays attached "
            "to the generated Observation, and no write occurs without explicit human confirmation."
        )
        with st.expander("View Provenance JSON"):
            st.json(draft["provenance"])

        st.markdown('<span class="step-label">4</span> **Human confirmation**', unsafe_allow_html=True)
        st.warning("Nothing is written before explicit human approval.")
        confirmed = st.checkbox("I confirm this describes what I observed.", key=f"confirm_{draft['draft_id']}")
        if st.button("Record observation", disabled=not confirmed):
            st.session_state.approval = tools.approve_draft(draft["draft_id"], confirmed_by_human=confirmed)

        approval = st.session_state.get("approval")
        if approval:
            st.markdown('<span class="step-label">5</span> **Standards-based output**', unsafe_allow_html=True)
            if approval.get("status") == "approved_not_written":
                st.success("Bundle generated. Nothing was POSTed to the public FHIR server.")
                st.caption(approval["reason"])
                st.json(approval["bundle"], expanded=False)
            elif approval.get("status") == "written":
                st.success("Human-approved Observation and Provenance were written to the configured FHIR server.")
                st.json(approval)
            else:
                st.error(approval.get("error", "The approved output could not be generated."))

# ---------------------------------------------------------------- connect
with tab_connect:
    st.subheader("Bring trusted One Health evidence to any agent")
    st.markdown(
        '<div class="flow-line"><strong>Any MCP-capable agent</strong> &nbsp;→&nbsp; '
        '<strong>OAH Agent Gateway</strong> &nbsp;→&nbsp; <strong>Official OneAquaHealth FHIR IG</strong> '
        '&nbsp;→&nbsp; <strong>Environmental + community-health evidence</strong></div>',
        unsafe_allow_html=True,
    )
    st.info("The same gateway tools power this web console and external MCP agents. MCP is the connection mechanism; trusted FHIR evidence is the product.")
    stdio_col, http_col = st.columns(2)
    with stdio_col:
        st.markdown("#### Local MCP · stdio")
        checkout = str(Path(__file__).resolve().parent)
        st.code(
            json.dumps(
                {"mcpServers": {"oah-gateway": {"command": "python", "args": ["-m", "oah_gateway.server"], "cwd": checkout}}},
                indent=2,
            ),
            language="json",
        )
    with http_col:
        st.markdown("#### Remote MCP · streamable HTTP")
        st.code("python -m oah_gateway.server --http", language="bash")
        st.code("http://localhost:8765/mcp", language=None)
        st.caption("Use `OAH_SOURCE=snapshot` for the reproducible demo or live/auto for the public sandbox.")

    st.markdown("### Bring your own OAH-compatible FHIR server")
    st.code("OAH_FHIR_BASE=https://your-server.example/fhir", language="bash")
    st.markdown(
        "Point the gateway at another server implementing the relevant OneAquaHealth Implementation Guide "
        "profiles, resources, and terminology to keep the same Python tool and MCP interface. This is an "
        "extensibility path—not a claim of certified compatibility with arbitrary FHIR servers."
    )
    st.caption(
        "Streamlit is the showcase UI. `oah_gateway/tools.py` is the shared source of truth, MCP exposes the "
        "same capabilities to external agents, and `OAH_FHIR_BASE` selects the compatible FHIR endpoint."
    )

    st.markdown("#### Eight gateway tools")
    published_tools = asyncio.run(mcp.list_tools())
    left, right = st.columns(2)
    for index, tool in enumerate(published_tools):
        with (left if index % 2 == 0 else right):
            with st.container(border=True):
                st.markdown(f"**`{tool.name}`**")
                st.caption(tool.description.splitlines()[0])
