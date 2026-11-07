"""Metadata mechanics only; synthetic files do not establish visual acceptance."""
import base64
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tarfile

import pytest

from mga_resources.catalog import finalize, search_page

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("replay_catalog_metadata", ROOT / "scripts/replay_catalog_metadata.py")
replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)
from diagnose_search import export_query_results


def h(value):
    return hashlib.sha256(value.encode()).hexdigest()


def fixture(tmp_path):
    file = {"sha256": h("mouse"), "size_bytes": 123, "format": "svg", "structure": "pure_vector",
        "url": "https://example.invalid/mouse.svg",
        "archive": {"url": "https://example.invalid/original.tar.gz", "member": "files/mouse.svg"},
        "preview": {"sha256": h("preview"), "archive": {"url": "https://example.invalid/preview.tar.gz", "member": "previews/mouse.png"}},
        "validation": {"status": "machine_checked_and_rendered"}}
    finalize([{"source": "fixture", "source_id": "1", "title": "Mouse", "author": "Fixture artist",
               "species": "mouse", "license": "CC0-1.0", "files": [file]}], tmp_path)
    page = search_page(tmp_path / "catalog.sqlite", "mouse", seed=replay.SEED)
    assert len(page["results"]) == 1
    rows = export_query_results(page)
    return {"input_catalog_sha256": replay.digest(tmp_path / "catalog.jsonl"), "ranking_version": page["bindings"]["ranking_version"],
        "queries": [{"id": "q", "exact_results": rows, "shared_ids": [row["family_id"] for row in rows],
            "selected_file_sha256": [row["selected_file"]["sha256"] for row in rows],
            "requirements": page["query_plan"]["requirements"], "search_bindings": page["bindings"]}]}


def test_real_small_index_export_binds_file_preview_source_and_canonical_candidate(tmp_path):
    diagnostic = fixture(tmp_path)
    result = replay.bind_candidates(tmp_path / "catalog.jsonl", diagnostic)
    row = result["queries"][0]["exact_results"][0]
    assert row["candidate_id"] == row["asset_id"] + ":file-0"
    assert row["preview"]["sha256"] == h("preview")
    assert row["selected_source"]["source_id"] == "1"


@pytest.mark.parametrize("tamper", ["preview", "source", "seed", "family", "license", "source_url", "author", "rights_record"])
def test_candidate_binding_rejects_stale_or_cross_file_metadata(tmp_path, tamper):
    diagnostic = fixture(tmp_path)
    row = diagnostic["queries"][0]["exact_results"][0]
    if tamper == "preview":
        row["preview"] = {**row["preview"], "sha256": h("wrong preview")}
    elif tamper == "source":
        row["selected_source"]["source_id"] = "unrelated alias"
    elif tamper == "seed":
        diagnostic["queries"][0]["search_bindings"]["seed"] = "different"
    elif tamper in {"license", "source_url", "author"}:
        row["selected_source"][tamper] = "forged source fact"
    elif tamper == "rights_record":
        row["selected_file"]["source_records"][0]["license"] = "forged license"
    else:
        row["family_id"] = "another-family"
    with pytest.raises(ValueError):
        replay.bind_candidates(tmp_path / "catalog.jsonl", diagnostic)


def old_result(name, rank):
    return {"rank": rank, "candidate_id": name + ":file-0", "family_id": name,
            "original_sha256": h(name), "preview_sha256": h(name + "preview"),
            "original_archive": {"url": "https://example.invalid/old.tar.gz", "member": name + ".svg"},
            "preview_archive": {"url": "https://example.invalid/old.tar.gz", "member": name + ".png"}}


def new_result(name, rank):
    old = old_result(name, rank)
    return {"rank": rank, "candidate_id": old["candidate_id"], "variant_id": name + "-variant",
        "family_id": name, "selected_file": {"sha256": old["original_sha256"], "archive": old["original_archive"]},
        "preview": {"sha256": old["preview_sha256"], "archive": old["preview_archive"]}}


def query():
    return {"id": "q", "query": "mouse", "expected_concepts": ["mouse"], "required_species": "mouse",
            "visual_requirements": "whole animal", "exclude_matches": ["mouse brain"], "category": "animals", "language": "en"}


def test_diff_distinguishes_new_removed_moved_and_changed_hard_requirements():
    old = {"queries": [{**query(), "requirements": {"species": ["mouse"]}}]}
    review = {"queries": [{"query_id": "q", "result_judgments": [old_result("a", 1), old_result("b", 2)]}]}
    current = {"queries": [{**query(), "requirements": {"species": ["mouse"], "view": ["lateral"]},
                             "exact_results": [new_result("c", 1), new_result("a", 2)]}]}
    diff = replay.compare_queries(old, review, current)
    row = diff["queries"][0]
    assert [r["family_id"] for r in row["added"]] == ["c"]
    assert [r["family_id"] for r in row["removed"]] == ["b"]
    assert [(r["old"]["rank"], r["new"]["rank"]) for r in row["moved"]] == [(1, 2)]
    assert row["requirements"]["changed"] is True
    assert row["formal_query_pass"] is None and row["visual_relevance_pass"] is None


def test_same_original_with_different_preview_is_not_visual_evidence_reuse():
    old = {"queries": [{**query(), "requirements": {}}]}
    review = {"queries": [{"query_id": "q", "result_judgments": [old_result("a", 1)]}]}
    candidate = new_result("a", 1)
    candidate["preview"]["sha256"] = h("changed preview")
    current = {"queries": [{**query(), "requirements": {}, "exact_results": [candidate]}]}
    row = replay.compare_queries(old, review, current)["queries"][0]
    assert len(row["added"]) == len(row["removed"]) == 1
    assert row["unchanged_exact_file_candidates"] == []


def baseline_fixture():
    baseline = {"input_catalog_sha256": "oldcat", "benchmark_sha256": "benchmark", "search_core_sha256": "core",
        "ranking_version": "version", "queries": [{**query(), "shared_ids": ["a"], "selected_file_sha256": [h("a")], "requirements": {}}]}
    review = {"bindings": {"catalog_sha256": "oldcat", "benchmark_sha256": "benchmark",
        "search_core_sha256": "oldcore", "diagnostic_sha256": "old-diagnostic"},
        "queries": [{"query_id": "q", "query": "mouse", "result_judgments": [old_result("a", 1)]}]}
    version = {"catalog_sha256": "oldcat", "benchmark_sha256": "benchmark", "search_core_sha256": "core", "ranking_version": "version",
        "original120_replay": {"seed": replay.SEED, "ordered_family_original_rank_lists_changed": 0,
            "hard_requirement_sets_changed": 0, "old_core_sha256": "oldcore", "result_positions": 1, "queries": 1,
            "replay_bindings": {"new_diagnostic_sha256": "baseline-hash", "old_diagnostic_sha256": "old-diagnostic"}}}
    return baseline, review, version, {"queries": [query()]}


@pytest.mark.parametrize("tamper", [None, "core", "benchmark", "rank", "original", "duplicate"])
def test_baseline_chain_requires_exact_original_requests_and_order(tamper):
    baseline, review, version, benchmark = baseline_fixture()
    if tamper == "core": baseline["search_core_sha256"] = "different"
    if tamper == "benchmark": baseline["queries"][0]["required_species"] = "rat"
    if tamper == "rank": review["queries"][0]["result_judgments"][0]["rank"] = 2
    if tamper == "original": review["queries"][0]["result_judgments"][0]["original_sha256"] = h("wrong")
    if tamper == "duplicate": baseline["queries"].append(copy.deepcopy(baseline["queries"][0]))
    def validate(): replay.validate_baseline(baseline, review, version, benchmark,
        baseline_sha="baseline-hash", benchmark_sha="benchmark", core_sha="core")
    if tamper:
        with pytest.raises(ValueError): validate()
    else: validate()


@pytest.mark.parametrize("name", ["originals.tar.gz", "preview.png", "../assembly-1-1.report.json", "assembly-1-1.internal-catalog.jsonl"])
def test_download_allowlist_rejects_artwork_before_network(monkeypatch, tmp_path, name):
    monkeypatch.setattr(replay, "gh", lambda *a: pytest.fail("Network must not be reached"))
    with pytest.raises(ValueError, match="Only exact"):
        replay.download_metadata(name, {}, tmp_path, "fixture/repo", "tag")


def test_download_verifies_digest_and_size(monkeypatch, tmp_path):
    name = "assembly-1-1.report.json"
    payload = b'{"metadata":true}\n'
    def fake_download(*args):
        assert args[:2] == ("release", "download")
        (tmp_path / name).write_bytes(payload)
    monkeypatch.setattr(replay, "gh", fake_download)
    asset = {"name": name, "digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
             "size": len(payload), "browser_download_url": "https://example.invalid/metadata.json"}
    _, receipt = replay.download_metadata(name, asset, tmp_path, "fixture/repo", "tag")
    assert receipt["kind"] == "metadata"
    asset["digest"] = "sha256:" + h("bad")
    with pytest.raises(ValueError, match="differs from GitHub"):
        replay.download_metadata(name, asset, tmp_path, "fixture/repo", "tag")


def test_metadata_package_cannot_contain_index_or_images(tmp_path, monkeypatch):
    monkeypatch.setattr(replay, "ROOT", tmp_path)
    output = tmp_path / "evidence"
    output.mkdir()
    (output / "summary.json").write_text('{}', encoding="utf-8")
    (output / "catalog.sqlite").write_bytes(b'not allowed')
    with pytest.raises(ValueError, match="Only generated JSON"):
        replay.package_metadata(output, tmp_path / "result.tar.gz")
    (output / "catalog.sqlite").unlink()
    receipt = replay.package_metadata(output, tmp_path / "result.tar.gz")
    with tarfile.open(tmp_path / "result.tar.gz") as archive:
        assert archive.getnames() == ["evidence/summary.json"]
    assert receipt["artwork_members"] == 0


def test_index_producer_must_use_identical_core_bytes(monkeypatch):
    code = b'export const RANKING_VERSION="version";'
    def fake_api(*args):
        if "actions/runs" in args[-1]:
            return json.dumps({"status": "completed", "conclusion": "success", "head_sha": "commit", "html_url": "https://example.invalid/run"})
        return json.dumps({"encoding": "base64", "content": base64.b64encode(code).decode()})
    monkeypatch.setattr(replay, "gh", fake_api)
    proof = replay.verify_producer("fixture/repo", "assembly-1-1", hashlib.sha256(code).hexdigest())
    assert proof["code_commit"] == "commit"
    with pytest.raises(ValueError, match="different search-core"):
        replay.verify_producer("fixture/repo", "assembly-1-1", h("changed code"))
