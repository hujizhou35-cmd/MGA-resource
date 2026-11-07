"""Tiny metadata fixtures; no images, network or new visual judgments."""
import copy
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("prepare_acceptance_evidence",
    Path(__file__).resolve().parents[1] / "scripts/prepare_acceptance_evidence.py")
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


@pytest.fixture
def inputs(monkeypatch):
    monkeypatch.setattr(bridge, "validate_remote_restore", lambda *a, **kw: True)
    monkeypatch.setattr(bridge, "check_replay", lambda *a: {
        "replayed_core_sha256": "c" * 64, "replayed_ranking_version": "shared-search-test"})
    archive = {"url": "https://example.invalid/source.tar.gz", "member": "original.svg"}
    preview = {"sha256": "b" * 64, "size_bytes": 9,
               "archive": {"url": archive["url"], "member": "preview.png"}}
    candidate = {"candidate_id": "f:file-0", "asset_id": "f", "family_id": "f", "title": "Fixture",
        "original_sha256": "a" * 64, "original_archive": archive, "preview": preview,
        "type": "pure_vector", "preview_status": "viewable"}
    mapping = {"bindings": {"catalog_sha256": "1" * 64, "benchmark_sha256": "2" * 64},
        "queries": [{"id": "positive", "candidates": [{"candidate_id": "f:file-0"}]}],
        "candidates": {"f:file-0": candidate}}
    judgment = {"query_id": "positive", "result_judgments": [{"candidate_id": "f:file-0",
        "original_sha256": "a" * 64, "preview_sha256": "b" * 64, "verdict": "relevant"}],
        "selected_candidate_ids": [], "pass": None, "visual_relevance_pass": True,
        "review_document": {"path": "review.json"}}
    file = {"sha256": "a" * 64, "size_bytes": 12, "asset_ids": ["f"], "family_ids": ["f"],
            "structure": "pure_vector", "archive": archive, "preview": preview}
    hashes = {"catalog_sha256": "1" * 64, "benchmark_sha256": "2" * 64,
        "source_mapping_sha256": "3" * 64, "publish_manifest_sha256": "4" * 64,
        "bundle_index_sha256": "5" * 64, "restore_evidence_sha256": "6" * 64,
        "assembly_report_sha256": "7" * 64, "search_core_sha256": "c" * 64}
    negative_binding = {k: hashes[k] for k in ("catalog_sha256", "benchmark_sha256", "search_core_sha256", "publish_manifest_sha256")}
    refs = {key: {"uri": key + ".json", "sha256": "0" * 64}
            for key in ("mapping", "merged", "replay", "negative", "restore", "bundle")}
    return {"mapping": mapping, "merged": {"bindings": {**mapping["bindings"],
        "mapping_sha256": hashes["source_mapping_sha256"], "candidate_manifest_raw_sha256": hashes["publish_manifest_sha256"]},
        "review_documents": [{"path": "review.json"}], "queries": [judgment]},
        "curated": {"catalog_binding": {"sha256": hashes["catalog_sha256"]},
            "manifest_binding": {"sha256": hashes["publish_manifest_sha256"]}, "mapping_candidates": {"f:file-0": copy.deepcopy(candidate)},
            "curated": [{"candidate_id": "f:file-0", "family_id": "f", "usable": True}],
            "independent_from_metadata_matcher": True, "reviewer": "Actual reviewer"},
        "negative": {"bindings": negative_binding, "independent_from_metadata_matcher": True,
            "negative_scenarios": [{"scenario_id": "absent", "request": {"query": "absent", "filters": {}},
                "candidates": [], "observed_candidate_ids": [], "pass": True, "evidence_notes": "Actually empty"}]},
        "negative_raw": {"bindings": negative_binding, "scenarios": [{"scenario": {"id": "absent"},
            "effective_request": {"query": "absent", "filters": {}}, "raw_result": {"results": []}}]},
        "benchmark": {"benchmark_version": "fixture", "queries": [{"id": "positive", "required_species": None}],
            "negative_scenarios": [{"id": "absent", "query": "absent"}]},
        "manifest": {"files": [file]}, "bundle": {"packs": [{"url": "https://example.invalid/clean.tar.gz", "sha256": "d" * 64,
            "members": [{"sha256": "a" * 64, "size_bytes": 12, "roles": ["original"], "path": "objects/a.svg"},
                        {"sha256": "b" * 64, "size_bytes": 9, "roles": ["preview"], "path": "objects/b.png"}]}]},
        "restore": {"restore_run": {"run_id": "2"}}, "review_documents": {"review.json": {
            "independent_from_metadata_matcher": True, "reviewer": "Actual reviewer", "reviewed_at": "2026-11-07",
            "queries": [{"query_id": "positive", "original_download_checked": False, "source_evidence_checked": True}]}},
        "replay": {}, "hashes": hashes, "refs": refs, "generated_at": "2026-11-07"}


def test_verified_restore_does_not_promote_formal_or_species_license_flags(inputs):
    original = copy.deepcopy(inputs)
    mapping, judgments, readiness = bridge.prepare(**inputs)
    row = judgments["queries"][0]
    assert row["pass"] is None and row["selected_candidate_ids"] == []
    assert row["original_download_checked"] is True
    assert row["source_evidence_checked"] is True
    assert row["license_checked"] is None and row["species_matches"] is None
    assert row["original_verification"]["fresh_download_performed_by_this_bridge"] is False
    assert inputs == original and mapping["queries"] == inputs["mapping"]["queries"]
    assert readiness["counts"]["formal_pass_true"] == 0
    assert readiness["pending_field_counts"]["species_matches"] == 1  # Even no-species-required stays explicit.
    assert judgments["bindings"]["mapping_sha256"] == bridge.hashlib.sha256(bridge.json_bytes(mapping)).hexdigest()


def test_explicit_selection_and_existing_confirmations_still_do_not_invent_pass(inputs):
    inputs["merged"]["queries"][0]["selected_candidate_ids"] = ["f:file-0"]
    inputs["review_documents"]["review.json"]["queries"][0].update(license_checked=True, species_matches=True)
    _, judgments, readiness = bridge.prepare(**inputs)
    assert judgments["queries"][0]["selected_candidate_ids"] == ["f:file-0"]
    assert judgments["queries"][0]["pass"] is None
    assert readiness["pending_confirmations"][0]["needs"] == ["explicit_formal_query_decision"]


@pytest.mark.parametrize("mutation", ["preview", "role", "size", "query_review_hash", "negative_query", "independent_review",
                                     "raw_request", "duplicate_raw_scenario", "raw_omitted_result"])
def test_binding_errors_fail_closed_before_outputs(inputs, mutation):
    if mutation == "preview": inputs["curated"]["mapping_candidates"]["f:file-0"]["preview"]["sha256"] = "e" * 64
    elif mutation == "role": inputs["bundle"]["packs"][0]["members"][0]["roles"] = ["preview"]
    elif mutation == "size": inputs["bundle"]["packs"][0]["members"][0]["size_bytes"] += 1
    elif mutation == "query_review_hash": inputs["merged"]["queries"][0]["result_judgments"][0]["original_sha256"] = "e" * 64
    elif mutation == "negative_query": inputs["negative"]["negative_scenarios"][0]["request"]["query"] = "changed"
    elif mutation == "raw_request": inputs["negative_raw"]["scenarios"][0]["effective_request"]["filters"] = {"source": "wrong"}
    elif mutation == "duplicate_raw_scenario": inputs["negative_raw"]["scenarios"].append(copy.deepcopy(inputs["negative_raw"]["scenarios"][0]))
    elif mutation == "raw_omitted_result": inputs["negative"]["negative_scenarios"][0]["observed_candidate_ids"] = ["not-actually-returned"]
    else: inputs["review_documents"]["review.json"]["independent_from_metadata_matcher"] = False
    with pytest.raises(ValueError):
        bridge.prepare(**inputs)


def test_restore_failure_cannot_mark_originals_verified(inputs, monkeypatch):
    monkeypatch.setattr(bridge, "validate_remote_restore", lambda *a, **kw: False)
    with pytest.raises(ValueError, match="restore"):
        bridge.prepare(**inputs)


def test_same_variant_id_cannot_borrow_another_negative_scenarios_file(inputs):
    c = inputs["mapping"]["candidates"]["f:file-0"]
    raw = {"variant_id": "shared-variant", "asset_id": c["asset_id"], "family_id": c["family_id"],
           "title": c["title"], "selected_file": {"sha256": c["original_sha256"],
           "archive": c["original_archive"], "structure": c["type"]}, "preview": c["preview"]}
    second = copy.deepcopy(inputs["negative_raw"]["scenarios"][0])
    second["scenario"]["id"] = "second"
    inputs["negative_raw"]["scenarios"][0]["raw_result"]["results"] = [raw]
    second["raw_result"]["results"] = [copy.deepcopy(raw)]
    second["raw_result"]["results"][0]["selected_file"]["sha256"] = "e" * 64
    inputs["negative_raw"]["scenarios"].append(second)
    inputs["benchmark"]["negative_scenarios"].append({"id": "second", "query": "absent"})
    other = copy.deepcopy(inputs["negative"]["negative_scenarios"][0]); other["scenario_id"] = "second"
    inputs["negative"]["negative_scenarios"].append(other)
    with pytest.raises(ValueError, match="Conflicting raw variant"):
        bridge.prepare(**inputs)
