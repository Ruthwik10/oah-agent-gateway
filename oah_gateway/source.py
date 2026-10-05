"""Data access for the OneAquaHealth FHIR sandbox.

Reads live from the official HL7 Europe sandbox, and falls back to a bundled,
dated snapshot when the sandbox is unreachable (it had a week-long DNS outage
during the hackathon). Every response says which source it came from.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

SANDBOX_BASE = os.environ.get(
    "OAH_FHIR_BASE", "https://sandbox.hl7europe.eu/oneaquahealth/fhir"
)
SNAPSHOT_DIR = Path(__file__).resolve().parent.parent / "data" / "snapshot"
RESOURCE_TYPES = ["Location", "Observation", "Group", "Organization", "Library", "Provenance", "Device"]


@dataclass
class SourceInfo:
    mode: str  # "live" or "snapshot"
    base: str
    fetched_at: str
    note: str = ""


@dataclass
class Store:
    resources: dict[str, dict[str, dict]] = field(default_factory=dict)  # type -> id -> resource
    info: SourceInfo | None = None

    def get(self, rtype: str, rid: str) -> dict | None:
        return self.resources.get(rtype, {}).get(rid)

    def all(self, rtype: str) -> list[dict]:
        return list(self.resources.get(rtype, {}).values())


def _fetch_all_live(client: httpx.Client, rtype: str, max_pages: int = 40) -> list[dict]:
    out: list[dict] = []
    url: str | None = f"{SANDBOX_BASE}/{rtype}?_count=200"
    pages = 0
    while url and pages < max_pages:
        r = client.get(url, headers={"Accept": "application/fhir+json"})
        r.raise_for_status()
        bundle = r.json()
        out.extend(e["resource"] for e in bundle.get("entry", []) if "resource" in e)
        nxt = next((l["url"] for l in bundle.get("link", []) if l.get("relation") == "next"), None)
        # never follow paging links to another host
        url = nxt if nxt and nxt.startswith(SANDBOX_BASE.split("/oneaquahealth")[0]) else None
        pages += 1
    return out


def _load_snapshot() -> Store:
    store = Store()
    for rtype in RESOURCE_TYPES:
        p = SNAPSHOT_DIR / f"{rtype}.ndjson"
        if not p.exists():
            continue
        store.resources[rtype] = {}
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                res = json.loads(line)
                store.resources[rtype][res["id"]] = res
    fetched = "2026-09-30T09:08:55Z"
    try:
        fetched = json.loads((SNAPSHOT_DIR / "manifest.json").read_text()).get("fetched_at", fetched)
    except Exception:
        pass
    store.info = SourceInfo("snapshot", SANDBOX_BASE, fetched,
                            "Bundled dated copy of the OAH sandbox (organizer-approved).")
    return store


def _load_live(timeout: float = 15.0) -> Store:
    store = Store()
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        for rtype in RESOURCE_TYPES:
            store.resources[rtype] = {r["id"]: r for r in _fetch_all_live(client, rtype)}
    store.info = SourceInfo("live", SANDBOX_BASE, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    return store


_STORE: Store | None = None


def get_store(refresh: bool = False) -> Store:
    """Return the data store. OAH_SOURCE = auto (default) | live | snapshot."""
    global _STORE
    if _STORE is not None and not refresh:
        return _STORE
    mode = os.environ.get("OAH_SOURCE", "auto").lower()
    if mode == "snapshot":
        _STORE = _load_snapshot()
    elif mode == "live":
        _STORE = _load_live()
    else:
        try:
            _STORE = _load_live()
            if not _STORE.all("Observation"):
                raise RuntimeError("live sandbox returned no observations")
        except Exception as exc:  # network down, DNS, proxy...
            _STORE = _load_snapshot()
            _STORE.info.note += f" Live sandbox unreachable ({type(exc).__name__}); using snapshot."
    return _STORE


def post_resource(resource: dict) -> dict:
    """Write one resource to the sandbox. Only called after explicit human approval."""
    rtype = resource["resourceType"]
    with httpx.Client(timeout=20.0) as client:
        r = client.post(f"{SANDBOX_BASE}/{rtype}", json=resource,
                        headers={"Content-Type": "application/fhir+json"})
        r.raise_for_status()
        return r.json()
