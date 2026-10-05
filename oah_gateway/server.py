"""OAH Agent Gateway: an MCP server that gives any AI agent trusted, standards-based
access to OneAquaHealth One Health data (HL7 FHIR R4, OAH Implementation Guide).

Run:  python -m oah_gateway.server            (stdio, for Claude Desktop / any MCP client)
      python -m oah_gateway.server --http     (streamable HTTP on :8765)
"""
from __future__ import annotations

import sys

from mcp.server.fastmcp import FastMCP

from . import tools

INSTRUCTIONS = """You are connected to the OneAquaHealth (OAH) One Health data gateway: water quality,
air quality and population-health indicators from the OAH research cities, served from the official
HL7 Europe FHIR sandbox and the OAH FHIR Implementation Guide.

Rules for using this data:
1. Choose the most direct tool: use one_health_snapshot for a city summary; for city/domain trust or
   exclusion questions you MUST call one_health_snapshot before answering (list_indicators is optional
   inventory context, but has no record references or reasons); use get_indicator for one named
   indicator; and use check_record directly for a named or specific Observation. Use data_overview only
   when the available scope is unknown. Do not repeat a tool call with the same arguments.
2. Every value carries a trust verdict. Quote OK values; quote CAUTION values only with their caveat;
   never state a DO_NOT_USE value as fact (say it failed integrity checks instead).
3. Cite exact fhir_ref values returned by tools (e.g. Observation/Obs-...) for every number you state.
   Never abbreviate, invent, or use placeholder references. If the result has no reference for a claim,
   say that instead of fabricating one.
4. Use only reasons, counts, caveats, and relationships present in tool output. Never infer a failed
   schema check, missing provenance, correlation, or trend unless the gateway explicitly reports it.
   Treat latest_safe_values as latest-period examples, not an exhaustive list of usable Observations.
   For a record integrity explanation, include the actual published values behind the decisive reasons.
   When a trust_hint says an explanation is a hypothesis, explicitly call it "hypothesis only, not
   verified"; never upgrade it to a probable or confirmed correction, and never correct the source value.
5. The data is aggregated and observational. Indicators available for the same city are co-occurring
   evidence, not proof of association or causation; call out differing measurement periods when relevant.
6. As soon as a tool result contains the evidence needed, answer the user. Keep the final answer concise
   and judge-readable while preserving the decisive trust caveats and
   exact FHIR references.
7. Never record a citizen observation without the human's explicit confirmation (approve_draft)."""

mcp = FastMCP("oah-agent-gateway", instructions=INSTRUCTIONS)


@mcp.tool()
def data_overview() -> dict:
    """Start here. What OneAquaHealth data exists (cities, domains, counts), where it was read from
    (live sandbox or dated snapshot) and how much of it passes integrity checks."""
    return tools.data_overview()


@mcp.tool()
def list_indicators(city: str | None = None, domain: str | None = None) -> dict:
    """Inventory indicator names and aggregate trust counts only; this has no record-level FHIR references
    or exclusion reasons. For city trust/exclusion questions also call one_health_snapshot.
    city: e.g. 'Crete', 'Benevento', 'Oslo'. domain: 'water', 'air', 'health' or 'stream-assessment'."""
    return tools.list_indicators(city, domain)


@mcp.tool()
def get_indicator(city: str, code: str, include_unsafe: bool = True) -> dict:
    """All values of one indicator (by code, e.g. 'nitrate', 'pm10', 'obesity') in one city,
    each with its FHIR reference, statistics, context reference value and trust verdict."""
    return tools.get_indicator(city, code, include_unsafe)


@mcp.tool()
def one_health_snapshot(city: str) -> dict:
    """The One Health picture of a city in one call: latest safe water, air and population-health
    values, values above WHO/EU reference levels, records excluded as unsafe, and evidence limits."""
    return tools.one_health_snapshot(city)


@mcp.tool()
def check_record(observation_id: str) -> dict:
    """Full integrity report for one FHIR Observation (id or 'Observation/<id>'). For the signature
    Almyros 2013 water-temperature record use Observation/Obs-Almyros-TemperatureWater-2013."""
    return tools.check_record(observation_id)


@mcp.tool()
def explain_indicator(code: str) -> dict:
    """Official definition of an indicator code from the OAH FHIR Implementation Guide CodeSystem."""
    return tools.explain_indicator(code)


@mcp.tool()
def draft_citizen_observation(location_id: str, indicator_code: str, observed: str,
                              value_number: float | None = None, unit: str | None = None) -> dict:
    """Turn a citizen's stream report into a draft OAH-IG Observation + Provenance (AI recorded as
    assembler, citizen as author). Nothing is written until the citizen approves it.
    indicator_code: foam, hydrology, morophology, riparianVegetation, invasiveOrganisms, LandUse, waterTemperature."""
    return tools.draft_citizen_observation(location_id, indicator_code, observed, value_number, unit)


@mcp.tool()
def approve_draft(draft_id: str, confirmed_by_human: bool = False) -> dict:
    """Record a draft after the citizen has explicitly confirmed it. Set confirmed_by_human=True ONLY
    when the human said yes in this conversation."""
    return tools.approve_draft(draft_id, confirmed_by_human)


@mcp.resource("oah://guide")
def guide() -> str:
    """How agents should use this gateway."""
    return INSTRUCTIONS


def main() -> None:
    if "--http" in sys.argv:
        mcp.settings.port = 8765
        mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    main()
