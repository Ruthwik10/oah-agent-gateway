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
    assert "average=198000" in rep["agent_guidance"]


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
    assert snap["domains"]["water"]["usable_observations"] == 21
    assert (snap["domains"]["water"]["usable_observations"]
            + snap["domains"]["water"]["excluded_as_unsafe"] == snap["domains"]["water"]["observations"])
    assert snap["domains"]["water"]["excluded_examples"][0]["fhir_ref"] == (
        "Observation/Obs-Almyros-TemperatureWater-2013"
    )
    assert "cannot establish" in snap["evidence_limits"]


def test_one_health_summary_keeps_reference_context():
    snap = tools.one_health_snapshot("Benevento")
    no2 = next(row for row in snap["domains"]["air"]["latest_safe_values"] if row["code"] == "no2")
    assert no2["reference"]["source"].startswith("WHO 2021")
    assert no2["fhir_refs"] and all(ref.startswith("Observation/") for ref in no2["fhir_refs"])


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


def test_nvidia_agent_loop_calls_gateway_tools(monkeypatch):
    """Drives the NVIDIA (OpenAI-compatible) tool loop with a fake client."""
    from types import SimpleNamespace as NS

    from oah_gateway import agent
    from oah_gateway.agent import run_agent_nvidia

    class FakeCompletions:
        def __init__(self):
            self.calls = []

        def create(self, **kw):
            self.calls.append(kw)
            if len(self.calls) == 1:
                tc = NS(id="call_1", type="function",
                        function=NS(name="one_health_snapshot", arguments='{"city": "Benevento"}'))
                msg = NS(content=None, tool_calls=[tc])
            else:
                assert kw["messages"][-1]["role"] == "tool"
                msg = NS(content="NO2 exceeded the WHO guideline [Observation/Obs-Benevento01-No2-2019].",
                         tool_calls=None)
            return NS(choices=[NS(message=msg)])

    fake = NS(chat=NS(completions=FakeCompletions()))
    trace = []
    answer, msgs = run_agent_nvidia("One Health summary for Benevento", [], trace, client=fake)
    assert "Observation/" in answer
    assert trace[0][0] == "one_health_snapshot" and "domains" in trace[0][2]
    sent = fake.chat.completions.calls[0]
    assert sent["tools"][0]["type"] == "function" and msgs[0]["role"] == "system"
    assert sent["model"] == "nvidia/nemotron-3-super-120b-a12b"
    assert sent["temperature"] == 1.0 and sent["top_p"] == 0.95
    assert sent["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}

    def provider_failure(*_args, **_kwargs):
        raise TimeoutError("simulated provider timeout")

    monkeypatch.setenv("NVIDIA_API_KEY", "test-placeholder")
    monkeypatch.setattr(agent, "run_agent_nvidia", provider_failure)
    safe_answer, safe_history = agent.run_agent("question", [], [])
    assert safe_answer == agent.AGENT_UNAVAILABLE_MESSAGE and safe_history == []


def test_streamlit_judge_view_renders_snapshot_and_trust_trace():
    from pathlib import Path

    from streamlit.testing.v1 import AppTest

    app = Path(__file__).resolve().parents[1] / "app.py"
    at = AppTest.from_file(str(app), default_timeout=45).run()
    assert not at.exception
    assert [(m.label, m.value) for m in at.metric] == [
        ("Observations", "450"), ("Official records", "385"), ("OK", "120"),
        ("CAUTION", "210"), ("DO NOT USE", "120"),
    ]
    assert at.radio[0].options == ["Crete", "Benevento"]

    at.selectbox[0].select("Obs-Almyros-TemperatureWater-2013").run()
    assert not at.exception
    rendered = "\n".join(element.value for element in at.markdown)
    assert "Trust Trace" in rendered
    assert "198,000" in rendered and "19.8" in rendered and "10,000×" in rendered
    assert "Observation/Obs-Almyros-TemperatureWater-2013" in rendered
    assert any("hypothesis only" in message.value.lower() for message in at.info)


def test_streamlit_agent_answer_shows_gateway_evidence():
    from pathlib import Path

    from streamlit.testing.v1 import AppTest

    app = Path(__file__).resolve().parents[1] / "app.py"
    at = AppTest.from_file(str(app), default_timeout=45).run()
    trace = [("one_health_snapshot", {"city": "Benevento"}, tools.one_health_snapshot("Benevento"))]
    at.session_state.chat = [
        ("user", "Benevento summary", None),
        ("assistant", "Evidence-based answer.", trace),
    ]
    at.run()
    assert not at.exception
    rendered = "\n".join(element.value for element in at.markdown)
    captions = "\n".join(element.value for element in at.caption)
    assert "Evidence panel" in rendered and "Caveats carried into the answer" in rendered
    assert "snapshot · 2026-09-30" in rendered
    assert "Question → agent → gateway tools → deterministic trust → FHIR evidence → answer" in captions
    assert "Observation/Obs-Benevento" in captions
