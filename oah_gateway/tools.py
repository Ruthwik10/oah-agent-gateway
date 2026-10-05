"""The gateway's tools. Plain functions returning JSON-able dicts, shared by the MCP
server, the web UI and the tests, so every surface gives the same answers."""
from __future__ import annotations

import os
import time
import uuid
from collections import Counter, defaultdict

from . import source
from .model import Record, headline_value, ig_definitions, normalise

_RECORDS: list[Record] | None = None
_DRAFTS: dict[str, dict] = {}

EVIDENCE_NOTE = ("These are aggregated, observational indicators. They can show co-occurrence across "
                 "places and periods; they cannot establish that a water or air condition caused a health outcome.")


def records(refresh: bool = False) -> list[Record]:
    global _RECORDS
    if _RECORDS is None or refresh:
        store = source.get_store(refresh=refresh)
        _RECORDS = [normalise(o, store) for o in store.all("Observation")]
        _cross_record_checks(_RECORDS)
    return _RECORDS


def _cross_record_checks(recs: list[Record]) -> None:
    """Checks that need two records: PM2.5 is a fraction of PM10, so for the same site and
    period PM2.5 cannot exceed PM10."""
    idx = {(r.location_id, r.period, r.code): r for r in recs}
    for (loc, period, code), pm25 in idx.items():
        if code != "pm2-5":
            continue
        pm10 = idx.get((loc, period, "pm10"))
        a, b = headline_value(pm25), headline_value(pm10) if pm10 else None
        if a is not None and b is not None and a > b:
            msg = (f"PM2.5 ({a}) exceeds PM10 ({b}) at the same site and period, but PM2.5 is part of PM10. "
                   f"See {pm10.fhir_ref} and {pm25.fhir_ref}.")
            pm25.trust.flag("CAUTION", msg)
            pm10.trust.flag("CAUTION", msg)
            for r in (pm25, pm10):
                if r.trust.reasons[0].startswith("Passed all checks"):
                    r.trust.reasons.pop(0)


def _source() -> dict:
    info = source.get_store().info
    return {"mode": info.mode, "server": info.base, "fetched_at": info.fetched_at, "note": info.note}


def _brief(r: Record) -> dict:
    return {
        "fhir_ref": r.fhir_ref, "indicator": r.indicator, "code": r.code, "period": r.period,
        "location": r.location_name or r.location_id, "cohort": r.cohort,
        "value": headline_value(r) if headline_value(r) is not None else r.value,
        "unit": r.unit, "stats": r.stats or None,
        "trust": r.trust.level, "trust_reasons": r.trust.reasons, "trust_hint": r.trust.hint,
        "reference": r.reference,
    }


def data_overview() -> dict:
    """What data exists: cities, domains, counts, and how much of it is safe to use."""
    recs = records()
    by_city: dict[str, Counter] = defaultdict(Counter)
    trust = Counter(r.trust.level for r in recs)
    for r in recs:
        by_city[r.city][r.domain] += 1
    return {
        "source": _source(),
        "observations": len(recs),
        "official_observations": sum(r.official for r in recs),
        "trust_summary": dict(trust),
        "cities": {c: dict(d) for c, d in sorted(by_city.items())},
        "how_to_use": "Call list_indicators(city) next, then get_indicator(city, code). OK values can be quoted; "
                      "CAUTION values only with their caveat; never quote DO_NOT_USE values as facts. "
                      "Cite the fhir_ref of every number.",
    }


def list_indicators(city: str | None = None, domain: str | None = None) -> dict:
    """Indicators available, optionally filtered by city and domain (water, air, health, stream-assessment)."""
    rows: dict[tuple, dict] = {}
    for r in records():
        if city and r.city.lower() != city.lower():
            continue
        if domain and r.domain != domain:
            continue
        k = (r.city, r.domain, r.code)
        row = rows.setdefault(k, {"city": r.city, "domain": r.domain, "code": r.code, "indicator": r.indicator,
                                  "unit": r.unit, "n": 0, "periods": set(), "trust": Counter()})
        row["n"] += 1
        row["periods"].add(r.period)
        row["trust"][r.trust.level] += 1
    out = []
    for row in sorted(rows.values(), key=lambda x: (x["city"], x["domain"], x["code"])):
        ps = sorted(row.pop("periods"))
        row["periods"] = f"{ps[0]}..{ps[-1]}" if len(ps) > 1 else ps[0]
        row["trust"] = dict(row["trust"])
        out.append(row)
    return {"source": _source(), "count": len(out), "indicators": out}


def get_indicator(city: str, code: str, include_unsafe: bool = True) -> dict:
    """All values of one indicator in one city, each with its FHIR reference and trust verdict."""
    rs = [r for r in records() if r.city.lower() == city.lower() and r.code.lower() == code.lower()]
    if not rs:
        return {"error": f"No '{code}' observations for '{city}'. Use list_indicators('{city}').", "source": _source()}
    rs.sort(key=lambda r: (r.location_id or "", r.cohort or "", r.period))
    items = [_brief(r) for r in rs if include_unsafe or r.trust.level != "DO_NOT_USE"]
    usable = [r for r in rs if r.trust.level != "DO_NOT_USE"]
    defn = ig_definitions().get(code, {})
    return {
        "source": _source(), "city": city, "code": code, "indicator": rs[0].indicator,
        "ig_definition": defn.get("definition"),
        "n": len(rs), "usable": len(usable), "do_not_use": len(rs) - len(usable),
        "values": items,
        "agent_guidance": ("Some values failed integrity checks; do not quote them. "
                           if len(usable) < len(rs) else "") + "Cite fhir_ref for every number you state.",
    }


def one_health_snapshot(city: str) -> dict:
    """Water, air and population-health picture for a city in one call, with safe values only,
    references for context, and an explicit statement of what the evidence can and cannot support."""
    rs = [r for r in records() if r.city.lower() == city.lower()]
    if not rs:
        return {"error": f"Unknown city '{city}'.", "available": sorted({r.city for r in records()})}
    out: dict = {"source": _source(), "city": city, "domains": {}}
    for dom in ("water", "air", "health", "stream-assessment"):
        dr = [r for r in rs if r.domain == dom]
        if not dr:
            continue
        safe = [r for r in dr if r.trust.level != "DO_NOT_USE"]
        latest_period: dict[str, str] = {}
        for r in safe:
            if r.period > latest_period.get(r.code, ""):
                latest_period[r.code] = r.period
        flagged = [r for r in dr if r.trust.level == "DO_NOT_USE"]
        summary, above = [], []
        for code, period in sorted(latest_period.items()):
            group = [r for r in safe if r.code == code and r.period == period]
            vals = [headline_value(r) for r in group if headline_value(r) is not None]
            r0 = group[0]
            row = {"indicator": r0.indicator, "code": code, "period": period, "unit": r0.unit,
                   "records": len(group),
                   "mean_across_sites_or_cohorts": round(sum(vals) / len(vals), 3) if vals else None,
                   "range": [min(vals), max(vals)] if vals else None,
                   "trust": sorted({r.trust.level for r in group}),
                   "caveats": sorted({x for r in group for x in r.trust.reasons if not x.startswith("Passed")})[:3],
                   "fhir_refs": [r.fhir_ref for r in group][:6]}
            summary.append(row)
            ref = r0.reference
            if ref and vals:
                rv = ref.get("value_in_record_unit") or (ref["value"] if (r0.unit or "") == ref["unit"] else None)
                if rv is not None:
                    over = [r for r in group if (headline_value(r) or 0) > rv]
                    if over:
                        above.append({"indicator": r0.indicator, "period": period, "unit": r0.unit,
                                      "reference": rv, "reference_source": ref["source"], "note": ref["note"],
                                      "records_above": len(over), "of": len(group),
                                      "max_value": max(headline_value(r) for r in over),
                                      "fhir_refs": [r.fhir_ref for r in over][:6]})
        out["domains"][dom] = {
            "observations": len(dr), "excluded_as_unsafe": len(flagged),
            "excluded_examples": [{"fhir_ref": r.fhir_ref, "why": r.trust.reasons[0]} for r in flagged[:3]],
            "latest_safe_values": summary[:30],
            "above_reference": above,
        }
    out["evidence_limits"] = EVIDENCE_NOTE
    return out


def check_record(observation_id: str) -> dict:
    """Full trust report for one Observation: origin, every check, and the raw values."""
    oid = observation_id.split("/")[-1]
    for r in records():
        if r.id == oid:
            d = r.to_dict()
            d["source"] = _source()
            return d
    return {"error": f"Observation/{oid} not found."}


def explain_indicator(code: str) -> dict:
    """The official OAH IG definition of an indicator code."""
    d = ig_definitions().get(code)
    if not d:
        close = [c for c in ig_definitions() if code.lower() in c.lower()][:10]
        return {"error": f"'{code}' is not in the OAH CodeSystem.", "did_you_mean": close}
    return {"code": code, "system": "http://hl7.eu/fhir/ig/oah/CodeSystem/temporarySystem-oah-eu", **d}


# ---------- human-in-the-loop write-back -------------------------------------------------

CITIZEN_INDICATORS = {"foam": "Foam/colour/smell", "hydrology": "Hydrology of the stream",
                      "morophology": "Morphology of the streams", "riparianVegetation": "Riparian vegetation",
                      "invasiveOrganisms": "Invasive invertebrate, plants and fish", "LandUse": "Land use in the margins",
                      "waterTemperature": "Water temperature"}


def draft_citizen_observation(location_id: str, indicator_code: str, observed: str,
                              value_number: float | None = None, unit: str | None = None,
                              observer_label: str = "Citizen scientist (anonymous)",
                              drafted_by_ai: bool = True) -> dict:
    """Draft an IG-conformant ObservationIndicatorsOah + Provenance from a citizen report.
    Nothing is written: the draft must be approved by the citizen with approve_draft()."""
    if indicator_code not in CITIZEN_INDICATORS:
        return {"error": "Unsupported indicator for citizen drafts.", "supported": CITIZEN_INDICATORS}
    store = source.get_store()
    if not store.get("Location", location_id):
        return {"error": f"Location/{location_id} not found. Use data_overview() / list_indicators()."}
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    did = uuid.uuid4().hex[:12]
    obs: dict = {
        "resourceType": "Observation",
        "meta": {"profile": ["http://hl7.eu/fhir/ig/oah/StructureDefinition/observation-indicators-oah"],
                 "tag": [{"system": "https://github.com/oah-agent-gateway", "code": "oah-agent-gateway"}]},
        "status": "final",  # the profile fixes status=final, so drafts stay local until approved
        "code": {"coding": [{"system": "http://hl7.eu/fhir/ig/oah/CodeSystem/temporarySystem-oah-eu",
                             "code": indicator_code, "display": CITIZEN_INDICATORS[indicator_code]}]},
        "subject": {"reference": f"Location/{location_id}"},
        "effectiveDateTime": now,
        "performer": [{"display": observer_label}],
    }
    if value_number is not None:
        obs["valueQuantity"] = {"value": value_number, "unit": unit or "", "system": "http://unitsofmeasure.org",
                                "code": unit or ""}
    else:
        obs["valueCodeableConcept"] = {"text": observed}
    obs["note"] = [{"text": f"Citizen report: {observed}"}]
    agents = [{"type": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/provenance-participant-type",
                                    "code": "author"}]}, "who": {"display": observer_label}}]
    if drafted_by_ai:
        agents.append({"type": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/provenance-participant-type",
                                            "code": "assembler"}]},
                       "who": {"display": "OAH Agent Gateway (AI-assisted drafting)"}})
    obs_urn = f"urn:uuid:{uuid.uuid4()}"
    prov = {"resourceType": "Provenance", "target": [{"reference": obs_urn}], "recorded": now, "agent": agents,
            "activity": {"text": "AI-drafted, citizen-approved stream observation"}}
    _DRAFTS[did] = {"observation": obs, "provenance": prov, "obs_urn": obs_urn, "status": "awaiting_human_approval"}
    return {"draft_id": did, "status": "awaiting_human_approval", "observation": obs, "provenance": prov,
            "next_step": "Show this to the citizen. Only call approve_draft(draft_id, confirmed_by_human=True) "
                         "after they explicitly confirm it is correct."}


def approve_draft(draft_id: str, confirmed_by_human: bool = False) -> dict:
    """Write an approved draft to the sandbox. Requires explicit human confirmation and
    OAH_ALLOW_WRITE=1 on the server; otherwise returns the validated bundle without writing."""
    d = _DRAFTS.get(draft_id)
    if not d:
        return {"error": "Unknown draft_id."}
    if not confirmed_by_human:
        return {"error": "Refused: a human must confirm the observation before it is recorded."}
    if os.environ.get("OAH_ALLOW_WRITE") != "1":
        d["status"] = "approved_not_written"
        return {"status": "approved_not_written",
                "reason": "Server write access is disabled (set OAH_ALLOW_WRITE=1 to enable).",
                "bundle": {"resourceType": "Bundle", "type": "transaction",
                           "entry": [{"fullUrl": d["obs_urn"], "resource": d["observation"],
                                      "request": {"method": "POST", "url": "Observation"}},
                                     {"fullUrl": f"urn:uuid:{uuid.uuid4()}", "resource": d["provenance"],
                                      "request": {"method": "POST", "url": "Provenance"}}]}}
    created = source.post_resource(d["observation"])
    d["provenance"]["target"] = [{"reference": f"Observation/{created['id']}"}]
    prov = source.post_resource(d["provenance"])
    d["status"] = "written"
    return {"status": "written", "observation": f"Observation/{created['id']}", "provenance": f"Provenance/{prov['id']}"}
