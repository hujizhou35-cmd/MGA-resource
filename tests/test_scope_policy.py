"""Scope eligibility tests use metadata fixtures, not claimed artwork approval."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

from mga_resources.scope_policy import (ROOT, load_scope_policy, evaluate_public_scope,
    partition_acquisition_descriptors, filter_public_records, write_internal_catalog)


def review(tmp_path, *, status="out_of_scope", disposition="exclude_from_v1", date="2026-11-07", name="scope"):
    item = {"source": "example", "source_id": "one", "original_sha256": "a" * 64,
            "preview_sha256": "b" * 64, "reason": "Actual fixture decision", "scope_status": status,
            "disposition": disposition, "reviewed_by": "Synthetic reviewer", "reviewed_at": date,
            "evidence": [{"uri": "https://example.invalid/review.png", "sha256": "c" * 64}],
            "upstream_file": {"url": "https://example.invalid/a.svg", "format": "svg",
                              "upstream_hash": "sha1:" + "d" * 40, "upstream_size": 8}}
    path = tmp_path / (name + ".json")
    path.write_text(json.dumps({"items": [item]}), encoding="utf-8")
    return path


def record():
    return {"source": "example", "source_id": "one", "id": "id-one", "title": "Synthetic object",
            "files": [{"sha256": "a" * 64, "format": "svg", "structure": "pure_vector",
                       "preview": {"sha256": "b" * 64}, "archive": {"url": "https://example.invalid/pack.tar.gz", "member": "files/a.svg"}}]}


def test_exact_file_exclusion_removes_whole_empty_family_but_not_internal(tmp_path):
    policy = load_scope_policy([review(tmp_path)])
    original = record();before = copy.deepcopy(original)
    public, audit = filter_public_records([original], policy)
    assert public == [] and audit["excluded_unique_originals"] == 1
    assert audit["excluded_files"][0]["sha256"] == "a" * 64
    assert original == before
    path = tmp_path / "internal.jsonl"
    proof = write_internal_catalog([original], path)
    saved = json.loads(path.read_text())
    assert saved["catalog_visibility"] == "internal_diagnostic" and saved["files"] == original["files"]
    assert proof["records"] == 1


def test_unknown_attributes_and_template_title_are_not_automatic_rejection():
    policy = load_scope_policy([])
    item = record();item["title"] = "CONSORT network labels table unknown"
    public, audit = filter_public_records([item], policy)
    assert len(public) == 1 and not audit["excluded_files"]
    assert public[0]["files"][0]["public_scope"]["status"] == "unreviewed"
    assert public[0]["files"][0]["text_content"]["status"] == "unknown"


def test_partial_family_keeps_other_file_and_scrubs_variant_reference(tmp_path):
    policy = load_scope_policy([review(tmp_path)])
    item = record();good = copy.deepcopy(item["files"][0]);good["sha256"] = "e" * 64
    item["files"].append(good);item["catalog_visibility"] = "internal_diagnostic"
    item["variants"] = [{"id": "bad", "files": [item["files"][0]]}, {"id": "good", "files": [good]}]
    public, _ = filter_public_records([item], policy)
    assert len(public[0]["files"]) == 1 and public[0]["files"][0]["sha256"] == "e" * 64
    assert [v["id"] for v in public[0]["variants"]] == ["good"]
    assert "catalog_visibility" not in public[0]


def test_same_original_hash_cannot_reenter_through_another_source(tmp_path):
    policy = load_scope_policy([review(tmp_path)])
    item = record();item.update(source="alias", source_id="different")
    assert filter_public_records([item], policy)[0] == []


def test_preview_problem_does_not_claim_bad_original_or_block_corrected_preview(tmp_path):
    policy = load_scope_policy([review(tmp_path, status="preview_layout_needs_review", disposition="quarantine")])
    item = record();file = item["files"][0]
    prior = evaluate_public_scope(item, file, policy)
    assert not prior["public_allowed"] and prior["preview_specific"]
    file["preview"]["sha256"] = "f" * 64;file["public_scope"] = prior
    assert evaluate_public_scope(item, file, policy)["public_allowed"]


def test_later_media_review_resolves_doubt_without_overriding_content_exclusion(tmp_path):
    old = review(tmp_path, status="scope_pending", disposition=None, date="2026-11-06T17:00:00Z", name="old")
    new = review(tmp_path, status="visually_illustration", disposition=None, date="2026-11-06T18:00:00Z", name="new")
    item = record()
    assert evaluate_public_scope(item, item["files"][0], load_scope_policy([old, new]))["public_allowed"]
    content = review(tmp_path, date="2026-11-06T16:00:00Z", name="content")
    scope = evaluate_public_scope(item, item["files"][0], load_scope_policy([old, new, content]))
    assert not scope["public_allowed"] and scope["reviewed_dimensions"]["media_type"] == "included"
    assert scope["reviewed_dimensions"]["component_suitability"] == "excluded"


def test_prefetch_skip_requires_full_immutable_descriptor_identity(tmp_path):
    path = review(tmp_path);policy = load_scope_policy([path]);item = record()
    item["files"] = [json.loads(path.read_text())["items"][0]["upstream_file"]]
    allowed, excluded = partition_acquisition_descriptors(item, policy)
    assert allowed == [] and excluded[0]["original_sha256"] == "a" * 64
    item["files"][0].pop("upstream_hash")
    assert partition_acquisition_descriptors(item, policy)[0] == item["files"]


def test_cloud_evidence_digest_mismatch_fails(tmp_path):
    path = review(tmp_path)
    with pytest.raises(ValueError, match="digest mismatch"):
        load_scope_policy([path], {})
    assets = {"review.png": {"browser_download_url": "https://example.invalid/review.png", "digest": "sha256:" + "c" * 64}}
    assert load_scope_policy([path], assets)["evidence_verification"] == "cloud_digest_and_committed_document"


def test_commons_keeps_four_labeled_objects_and_excludes_two_finished_figures():
    path = ROOT / "data/visual-reviews/commons-42260711756-component-scope.json"
    policy = load_scope_policy([path]);reviewed = json.loads(path.read_text(encoding="utf-8"))["items"]
    records = [{"source": "commons", "source_id": x["source_id"], "id": x["id"], "title": x["title"],
                "files": [{"sha256": x["original_sha256"], "preview": {"sha256": x["preview_sha256"]},
                           "format": "jpg" if x["tile"] in {3, 4} else "svg", "archive": x["original_archive"]}]}
               for x in reviewed]
    public, audit = filter_public_records(records, policy)
    assert len(public) == 4 and audit["excluded_unique_originals"] == 2
    assert [r["files"][0]["text_content"]["status"] for r in public] == ["absent", "present", "present", "present"]
    assert all(r["files"][0]["text_content"]["editable_text"] == "unknown" for r in public)
    assert {f["sha256"] for f in audit["excluded_files"]} == {
        "dbb739dc66c2fed7c692556534a59d659162e148608c4eb018488fabf30d8ebe",
        "ebb9a9bd3b53c90cccf842971ca582f91b685cfc4c7d6250e4ddf43083c80190"}


def _script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_publish_pack_rejects_exact_excluded_file_before_reading_archive(tmp_path):
    policy = load_scope_policy([review(tmp_path)])
    with pytest.raises(ValueError, match="scope-excluded"):
        _script("build_publish_pack").plan_manifest({"files": record()["files"]}, scope_policy=policy)


def test_acceptance_defense_rejects_dirty_public_catalog(tmp_path):
    spec = importlib.util.spec_from_file_location("fixture_scope", ROOT / "tests/test_accept_release.py")
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    args = module.fixture();policy = load_scope_policy([review(tmp_path)])
    args["catalog"][0]["files"][0]["sha256"] = "a" * 64
    args["catalog"][0]["files"][0]["preview"]["sha256"] = "b" * 64
    args["scope_policy"] = policy
    manifest, report = _script("accept_release").evaluate_release(**args)
    assert "a" * 64 not in {f["sha256"] for f in manifest["files"]}
    assert any(x["code"] == "scope.forbidden_public_file" for x in report["missing"])
    assert not report["acceptance_passed"]
