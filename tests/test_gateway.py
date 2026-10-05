import os

os.environ["OAH_SOURCE"] = "snapshot"  # deterministic, offline

from oah_gateway import tools  # noqa: E402
from oah_gateway.model import ig_definitions  # noqa: E402


def test_official_records_identified():
    ov = tools.data_overview()
    assert ov["observations"] == 450
    assert ov["official_observations"] == 385


def test_impossible_water_temperature_is_blocked():
    rep = tools.check_record("Obs-Almyros-TemperatureWater-2013")
    assert rep["trust"]["level"] == "DO_NOT_USE"
    assert any("outside its own range" in r for r in rep["trust"]["reasons"])
    assert "10^4" in rep["trust"]["hint"]


def test_clean_air_record_passes():
    rep = tools.check_record("Observation/Obs-Benevento01-No2-2019")
    assert rep["trust"]["level"] == "OK"


def test_pm25_above_pm10_is_flagged():
    rep = tools.check_record("Obs-Benevento01-Pm25-2018")
    assert rep["trust"]["level"] == "CAUTION"
    assert any("PM2.5" in r and "PM10" in r for r in rep["trust"]["reasons"])


def test_third_party_writes_are_labelled():
    community = [r for r in tools.records() if not r.official]
    assert community and all(r.trust.level != "OK" for r in community)


def test_snapshot_never_returns_unsafe_values_as_latest():
    snap = tools.one_health_snapshot("Crete")
    for row in snap["domains"]["water"]["latest_safe_values"]:
        assert "DO_NOT_USE" not in row["trust"]
    assert snap["domains"]["water"]["excluded_as_unsafe"] > 0
    assert "cannot establish" in snap["evidence_limits"]


def test_every_value_is_traceable_to_fhir():
    res = tools.get_indicator("Benevento", "pm10")
    assert res["n"] > 0
    assert all(v["fhir_ref"].startswith("Observation/") for v in res["values"])


def test_ig_codesystem_loaded():
    assert ig_definitions()["foam"]["display"] == "Foam/colour/smell"
    assert tools.explain_indicator("hydrology")["code"] == "hydrology"


def test_citizen_draft_conforms_to_oah_profile_and_needs_human():
    d = tools.draft_citizen_observation("Loc-Almyros", "foam", "white foam below the outfall")
    obs = d["observation"]
    assert obs["meta"]["profile"] == ["http://hl7.eu/fhir/ig/oah/StructureDefinition/observation-indicators-oah"]
    # ObservationIndicatorsOah: status fixed final; code, subject(Location), effective, performer required
    assert obs["status"] == "final"
    assert obs["subject"]["reference"] == "Location/Loc-Almyros"
    assert obs["effectiveDateTime"] and obs["performer"] and obs["code"]["coding"][0]["code"] == "foam"
    roles = [a["type"]["coding"][0]["code"] for a in d["provenance"]["agent"]]
    assert roles == ["author", "assembler"]
    assert "error" in tools.approve_draft(d["draft_id"])  # refused without confirmation
    out = tools.approve_draft(d["draft_id"], confirmed_by_human=True)
    assert out["status"] == "approved_not_written"  # writes disabled by default


def test_write_bundle_is_valid_fhir_r4():
    from fhir.resources.R4B.bundle import Bundle
    d = tools.draft_citizen_observation("Loc-Almyros", "waterTemperature", "warm, cloudy", 18.5, "Cel")
    Bundle.model_validate(tools.approve_draft(d["draft_id"], confirmed_by_human=True)["bundle"])
