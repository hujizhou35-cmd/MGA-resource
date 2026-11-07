import copy
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("merge_query_confirmations",
    Path(__file__).resolve().parents[1] / "scripts/merge_query_confirmations.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def inputs():
    archive = {"url": "https://example.invalid/source.tar.gz", "member": "source.svg"}
    preview_archive = {"url": archive["url"], "member": "preview.png"}
    provenance = {"source": "fixture", "source_id": "one", "source_url": "https://example.invalid/one",
                  "url": "https://example.invalid/source.svg", "author": "Recorded artist",
                  "license": "CC-BY-4.0", "attribution": "Recorded artist / CC BY 4.0"}
    candidate = {"asset_id": "asset", "family_id": "family", "type": "pure_vector", "preview_status": "viewable",
                 "original_sha256": "a", "original_archive": archive,
                 "preview": {"sha256": "b", "archive": preview_archive}}
    query = {"query_id": "query", "pass": None, "selected_candidate_ids": [], "visual_relevance_pass": True,
             "result_judgments": [{"candidate_id": "candidate", "verdict": "relevant", "evidence_notes": "Actually viewed"}],
             "original_verification": {"candidate_objects": {"candidate": [
                 {"role": "original", "sha256": "a"}, {"role": "preview", "sha256": "b"}]}}}
    check = {"candidate_id": "candidate", "original_sha256": "a", "preview_sha256": "b",
             "original_archive": archive, "preview_archive": preview_archive, "source_records": [provenance]}
    confirmation = {"query_id": "query", "pass": True, "selected_candidate_ids": ["candidate"],
        "visual_evidence_notes": "Explicit reviewer selection from the existing relevant observation",
        "source_evidence_checked": True, "license_checked": True, "species_matches": True, "original_download_checked": True,
        "species_requirement": {"required_species": None, "applicability": "not_required", "basis": "No species required; no species is inferred."},
        "source_checks": [check], "pending_checks": [], "gap_reason": None}
    doc = {"reviewer": "Reviewer", "reviewed_at": "2026-11-07", "independent_from_metadata_matcher": True,
           "bindings": {"bound": "value"}, "queries": [confirmation]}
    return {"base": {"queries": [query]}, "mapping": {"candidates": {"candidate": candidate}},
        "benchmark": {"queries": [{"id": "query", "required_species": None}]},
        "manifest": {"files": [{"sha256": "a", "asset_ids": ["asset"], "family_ids": ["family"],
            "structure": "pure_vector", "archive": archive, "preview": candidate["preview"], "provenance": [copy.deepcopy(provenance)]}]},
        "documents": [({"path": "confirmation.json", "sha256": "fixed"}, doc)], "bindings": {"bound": "value"}}


def test_explicit_confirmation_preserves_original_observations_and_base(inputs):
    before = copy.deepcopy(inputs)
    merged, audit = module.merge(**inputs)
    assert merged["queries"][0]["pass"] is True
    assert merged["queries"][0]["result_judgments"] == before["base"]["queries"][0]["result_judgments"]
    assert inputs == before
    assert audit["formal_decisions"] == {"pass": 1}
    assert merged["queries"][0]["species_requirement"]["applicability"] == "not_required"


@pytest.mark.parametrize("mutation", ["unreviewed_selection", "preview", "license", "author", "species_requirement",
    "applicability", "missing_flag", "visual_negative", "binding", "duplicate", "missing_query", "missing_restore"])
def test_invalid_confirmation_cannot_be_promoted(inputs, mutation):
    doc = inputs["documents"][0][1]; row = doc["queries"][0]
    if mutation == "unreviewed_selection": row["selected_candidate_ids"] = ["different-query-candidate"]
    elif mutation == "preview": row["source_checks"][0]["preview_sha256"] = "wrong"
    elif mutation in {"license", "author"}: row["source_checks"][0]["source_records"][0][mutation] = "invented"
    elif mutation == "species_requirement": row["species_requirement"]["required_species"] = "human"
    elif mutation == "applicability": row["species_requirement"]["applicability"] = "required"
    elif mutation == "missing_flag": row["license_checked"] = None
    elif mutation == "visual_negative": inputs["base"]["queries"][0]["visual_relevance_pass"] = False
    elif mutation == "binding": doc["bindings"]["bound"] = "different"
    elif mutation == "duplicate": doc["queries"].append(copy.deepcopy(row))
    elif mutation == "missing_query": doc["queries"] = []
    else: inputs["base"]["queries"][0]["original_verification"]["candidate_objects"] = {}
    with pytest.raises(ValueError):
        module.merge(**inputs)


def test_pending_use_case_does_not_become_pass_from_verified_bytes(inputs):
    row = inputs["documents"][0][1]["queries"][0]
    row["pass"] = None
    row["pending_checks"] = ["The query asks for a layer; only an individual cell is evidenced."]
    merged, audit = module.merge(**inputs)
    assert merged["queries"][0]["pass"] is None
    assert audit["formal_decisions"] == {"pending": 1}
