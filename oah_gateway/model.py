"""Normalisation of OAH FHIR resources into agent-friendly records, with trust labels.

FHIR validation checks shape. An AI agent also needs to know whether a value can
be true before it reasons with it. Every record this gateway returns carries:
  - where it came from (official OAH IG record, or written to the shared sandbox by a third party)
  - a deterministic trust verdict (OK / CAUTION / DO_NOT_USE) with human-readable reasons
  - the FHIR reference, so any claim can be traced back to the source resource
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path

IG_DIR = Path(__file__).resolve().parent.parent / "data" / "ig"
OAH_SYSTEM = "http://hl7.eu/fhir/ig/oah/CodeSystem/temporarySystem-oah-eu"
STATS_SYSTEM = "http://terminology.hl7.org/CodeSystem/observation-statistics"

# Location id prefix -> research city (from the IG examples)
CITY_BY_PREFIX = [
    ("Loc-Almyros", "Crete"), ("Loc-Giofyros", "Crete"), ("Almyros", "Crete"), ("Giofyros", "Crete"),
    ("Loc-Benevento", "Benevento"), ("Loc-Nordre-Aker", "Oslo"),
]

WATER_TEMP_CODES = {"waterTemperature", "703421000", "daily-mean-water-temperature",
                    "predicted-daily-mean-water-temperature"}
CITIZEN_CODES = {"morophology", "hydrology", "LandUse", "riparianVegetation", "invasiveOrganisms", "foam",
                 "macroinvertebreates", "macrophytes", "diatomes", "fishes", "fish"}

# Context references only. Rivers are not drinking water; these are shown so an agent can
# put a number in context, never presented as a legal limit for the stream itself.
REFERENCES = {
    # code: (value, unit, label)
    "nitrate": (50, "mg/L", "EU Drinking Water Directive 2020/2184 parametric value"),
    "nitrite": (0.5, "mg/L", "EU Drinking Water Directive 2020/2184 parametric value"),
    "ammonium": (0.5, "mg/L", "EU Drinking Water Directive 2020/2184 indicator value"),
    "chloride": (250, "mg/L", "EU Drinking Water Directive 2020/2184 indicator value"),
    "sulphate": (250, "mg/L", "EU Drinking Water Directive 2020/2184 indicator value"),
    "arsenic-dissolved": (10, "ug/L", "EU Drinking Water Directive 2020/2184 parametric value"),
    "cadmium-dissolved": (5, "ug/L", "EU Drinking Water Directive 2020/2184 parametric value"),
    "aluminium-dissolved": (200, "ug/L", "EU Drinking Water Directive 2020/2184 indicator value"),
    "pm10": (15, "ug/m3", "WHO 2021 air quality guideline, annual mean"),
    "pm2-5": (5, "ug/m3", "WHO 2021 air quality guideline, annual mean"),
    "no2": (10, "ug/m3", "WHO 2021 air quality guideline, annual mean"),
    "benzene": (5, "ug/m3", "EU Directive 2008/50/EC annual limit"),
}

UNIT_FACTORS = {("mg/L", "ug/L"): 1000.0, ("ug/L", "mg/L"): 0.001}


@lru_cache(maxsize=1)
def official_ids() -> frozenset[str]:
    p = IG_DIR / "official_ids.txt"
    return frozenset(p.read_text().split()) if p.exists() else frozenset()


@lru_cache(maxsize=1)
def ig_definitions() -> dict[str, dict]:
    """Parse the OAH CodeSystem FSH for code -> display/definition."""
    out: dict[str, dict] = {}
    p = IG_DIR / "oah-codeSystem.fsh"
    if not p.exists():
        return out
    import re
    for line in p.read_text(encoding="utf-8").splitlines():
        m = re.match(r'\s*\*\s*#(\S+)\s+"([^"]*)"\s*(?:"([^"]*)")?', line)
        if m:
            out[m.group(1)] = {"display": m.group(2), "definition": m.group(3) or m.group(2)}
    return out


def city_of(location_id: str | None) -> str:
    if not location_id:
        return "Unknown"
    for prefix, city in CITY_BY_PREFIX:
        if location_id.startswith(prefix):
            return city
    return "Community-written"


def ref_id(ref: dict | None) -> str | None:
    if not ref or "reference" not in ref:
        return None
    return ref["reference"].split("/")[-1]


def _decimals(x: float) -> int:
    s = repr(float(x))
    return 0 if s.endswith(".0") or "e" in s else len(s.split(".")[1])


def _tol(*vals: float) -> float:
    # half a unit of the least precise published decimal: rounding alone never fails a check
    return max(0.5 * 10 ** (-_decimals(v)) for v in vals)


@dataclass
class Trust:
    level: str = "OK"  # OK | CAUTION | DO_NOT_USE
    reasons: list[str] = field(default_factory=list)
    hint: str | None = None

    def flag(self, level: str, reason: str) -> None:
        order = {"OK": 0, "CAUTION": 1, "DO_NOT_USE": 2}
        if order[level] > order[self.level]:
            self.level = level
        self.reasons.append(reason)


@dataclass
class Record:
    id: str
    fhir_ref: str
    domain: str  # water | air | health | stream-assessment | other
    code: str
    indicator: str
    location_id: str | None
    location_name: str | None
    city: str
    cohort: str | None
    period: str
    unit: str | None
    value: float | str | None
    stats: dict[str, float]
    official: bool
    written_by: str
    trust: Trust
    reference: dict | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["trust"] = {"level": self.trust.level, "reasons": self.trust.reasons, "hint": self.trust.hint}
        return d


def domain_of(obs: dict) -> str:
    coding = (obs.get("code", {}).get("coding") or [{}])[0]
    code, system = coding.get("code", ""), coding.get("system", "")
    profiles = " ".join(obs.get("meta", {}).get("profile", []))
    if "air-parameters" in system:
        return "air"
    if "health-measure" in profiles or obs.get("focus"):
        return "health"
    if code in CITIZEN_CODES or "second-look" in system:
        return "stream-assessment"
    if code in WATER_TEMP_CODES or system == OAH_SYSTEM or "laboratory" in str(obs.get("category")):
        return "water"
    return "other"


def _period(obs: dict) -> str:
    if "effectivePeriod" in obs:
        p = obs["effectivePeriod"]
        s, e = p.get("start", "")[:10], p.get("end", "")[:10]
        if s[:4] == e[:4] and s.endswith("01-01") and e.endswith("12-31"):
            return s[:4]
        return f"{s}..{e}"
    return (obs.get("effectiveDateTime") or "")[:10]


def check(obs: dict, rec: Record) -> Trust:
    t = Trust()
    st = rec.stats
    code = rec.code
    # 1. origin
    if not rec.official:
        t.flag("CAUTION", f"Not part of the official OAH dataset: written to the shared sandbox by {rec.written_by}.")
    # 2. statistical identities that hold for any real data
    mn, mx, md, av, sd = (st.get(k) for k in ("minimum", "maximum", "median", "average", "std-dev"))
    if mn is not None and mx is not None and mn > mx + _tol(mn, mx):
        t.flag("DO_NOT_USE", f"Minimum ({mn}) is greater than maximum ({mx}).")
    for name, v in (("median", md), ("average", av)):
        if v is not None and mn is not None and mx is not None and not (mn - _tol(v, mn) <= v <= mx + _tol(v, mx)):
            t.flag("DO_NOT_USE", f"The {name} ({v}) lies outside its own range [{mn}, {mx}].")
    if sd is not None and sd < 0:
        t.flag("DO_NOT_USE", f"Standard deviation is negative ({sd}).")
    if av and md and min(abs(av), abs(md)) > 0:
        ratio = max(abs(av), abs(md)) / min(abs(av), abs(md))
        if ratio >= 100:
            t.flag("DO_NOT_USE", f"Average and median differ by a factor of {ratio:,.0f}: likely a scale/unit error.")
            import math
            exp = math.log10(ratio)
            if abs(exp - round(exp)) < 1e-6:
                t.hint = (f"Values differ by exactly 10^{round(exp)}, a pattern consistent with a lost decimal "
                          f"separator during conversion. Hypothesis only, not verified: report to the data owner "
                          f"rather than correcting it.")
    # 3. physical plausibility
    vals = [v for v in [*st.values(), rec.value] if isinstance(v, (int, float))]
    if code in WATER_TEMP_CODES and any(v > 45 or v < -2 for v in vals):
        t.flag("DO_NOT_USE", f"Water temperature outside what liquid stream water can be (-2..45 °C): {max(vals, key=abs)} °C.")
    if code.lower() == "ph" and any(v < 0 or v > 14 for v in vals):
        t.flag("DO_NOT_USE", f"pH outside 0-14: {max(vals, key=abs)}.")
    if rec.unit == "%" and any(v < 0 or v > 100 for v in vals):
        t.flag("DO_NOT_USE", "Percentage outside 0-100.")
    if rec.domain in ("water", "air") and code not in WATER_TEMP_CODES and any(v < 0 for v in vals):
        t.flag("DO_NOT_USE", "Negative concentration.")
    # 4. units and definitions
    if rec.domain in ("water", "air") and vals and not rec.unit:
        t.flag("CAUTION", "No UCUM unit on a numeric measurement.")
    if rec.domain == "health" and rec.unit == "%":
        d = ig_definitions().get(code, {}).get("definition", "")
        if "per 100.000" in d:
            t.flag("CAUTION", "IG definition says 'cases per 100,000 inhabitants' but the value is published in %. Read as a percentage of the cohort.")
    if not t.reasons:
        t.reasons.append("Passed all checks (origin, statistical identities, physical limits, units).")
    return t


def normalise(obs: dict, store) -> Record:
    coding = (obs.get("code", {}).get("coding") or [{}])[0]
    code = coding.get("code", "")
    loc_id = ref_id(obs.get("subject"))
    loc = store.get("Location", loc_id) if loc_id else None
    cohort = None
    if obs.get("focus"):
        gid = ref_id(obs["focus"][0])
        g = store.get("Group", gid) if gid else None
        cohort = gid
        if g:
            parts = []
            for c in g.get("characteristic", []):
                vr, vc = c.get("valueRange"), c.get("valueCodeableConcept")
                if vr:
                    parts.append(f"age {vr.get('low', {}).get('value', '')}-{vr.get('high', {}).get('value', '')}")
                elif vc:
                    parts.append((vc.get("coding") or [{}])[0].get("display") or vc.get("text", ""))
            cohort = f"{gid} ({', '.join(p for p in parts if p)})" if parts else gid
    stats: dict[str, float] = {}
    unit = None
    for comp in obs.get("component", []):
        cc = (comp.get("code", {}).get("coding") or [{}])[0]
        vq = comp.get("valueQuantity")
        if cc.get("system") == STATS_SYSTEM and vq and "value" in vq:
            stats[cc["code"]] = vq["value"]
            unit = unit or vq.get("code") or vq.get("unit")
    value: float | str | None = None
    if "valueQuantity" in obs:
        value = obs["valueQuantity"].get("value")
        unit = obs["valueQuantity"].get("code") or obs["valueQuantity"].get("unit")
    elif "valueCodeableConcept" in obs:
        vc = obs["valueCodeableConcept"]
        value = (vc.get("coding") or [{}])[0].get("display") or vc.get("text")
    elif "valueString" in obs:
        value = obs["valueString"]
    tags = obs.get("meta", {}).get("tag") or []
    official = obs["id"] in official_ids()
    written_by = "OneAquaHealth IG (official)" if official else (
        tags[0].get("code") if tags else "an unidentified third party")
    rec = Record(
        id=obs["id"], fhir_ref=f"Observation/{obs['id']}", domain=domain_of(obs), code=code,
        indicator=coding.get("display") or obs.get("code", {}).get("text") or code,
        location_id=loc_id, location_name=(loc or {}).get("name"),
        city=city_of(loc_id),
        cohort=cohort, period=_period(obs), unit=unit, value=value, stats=stats,
        official=official, written_by=written_by, trust=Trust(),
    )
    ref = REFERENCES.get(code)
    if ref:
        rv, ru, label = ref
        rec.reference = {"value": rv, "unit": ru, "source": label,
                         "note": "Context only; not a legal limit for surface water." if rec.domain == "water" else "Context only."}
        if unit and unit != ru and (ru, unit) in UNIT_FACTORS:
            rec.reference["value_in_record_unit"] = rv * UNIT_FACTORS[(ru, unit)]
    rec.trust = check(obs, rec)
    return rec


def headline_value(rec: Record) -> float | None:
    """The single number an agent should quote: the average, else median, else the value."""
    for k in ("average", "median"):
        if k in rec.stats:
            return rec.stats[k]
    return rec.value if isinstance(rec.value, (int, float)) else None
