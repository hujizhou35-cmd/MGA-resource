"""Synthetic proof-binding mechanics; no fixture is a medical visual judgment."""
import copy

import pytest

from scripts.merge_relevance_reviews import check_replay, merge_reviews


def inputs():
    bindings = {"catalog_sha256": "c" * 64, "benchmark_sha256": "b" * 64,
                "candidate_manifest_raw_sha256": "f" * 64, "search_core_sha256": "d" * 64,
                "ranking_version": "old", "search_index_version": 9, "diagnostic_sha256": "e" * 64}
    archive = {"url": "https://example.invalid/collection.tar.gz", "member": "files/a.svg"}
    preview_archive = {**archive, "member": "previews/a.png"}
    candidate = {"family_id": "family", "original_sha256": "1" * 64, "original_archive": archive,
                 "preview": {"sha256": "2" * 64, "archive": preview_archive}}
    row = {"rank": 1, "family_id": "family", "candidate_id": "family:file-0"}
    queries = [{"id": "cell-" + lang, "query": "cell" if lang == "en" else "细胞",
                "category": "cells", "concept_id": "cell", "language": lang, "candidates": [row]}
               for lang in ("zh", "en")]
    mapping = {"bindings": bindings, "queries": queries, "candidates": {"family:file-0": candidate},
               "concepts": [{"id": "cell", "sheet": "concepts/cell.png", "sheet_metadata": {"sha256": "3" * 64},
                             "candidates": [{"candidate_id": "family:file-0"}]}]}
    benchmark = {"queries": queries, "concepts": [{"id": "cell", "category": "cells"}]}
    manifest = {"catalog_sha256": bindings["catalog_sha256"], "files": [{"sha256": "1" * 64,
                "family_ids": ["family"], "archive": archive, "preview": candidate["preview"]}]}
    proof = {"uri": "https://example.invalid/review.tar.gz", "sha256": "4" * 64,
             "member": "concepts/cell.png", "member_sha256": "3" * 64, "tile": 1}
    judgment = {"candidate_id": "family:file-0", "rank": 1, "original_sha256": "1" * 64,
                "preview_sha256": "2" * 64, "verdict": "relevant", "evidence_notes": "Synthetic fixture observation.", "evidence": proof}
    document = {"reviewer": "Fixture reviewer", "reviewed_at": "2026-11-07", "independent_from_metadata_matcher": True,
                "bindings": {**bindings, "mapping_sha256": "5" * 64}, "review_pack": proof,
                "queries": [{**q, "result_judgments": [copy.deepcopy(judgment)], "original_download_checked": False} for q in queries]}
    kwargs = {"mapping_sha256": "5" * 64, "benchmark_sha256": "b" * 64, "manifest_sha256": "f" * 64,
              "pack_sha256": "4" * 64, "member_hashes": {"mapping.json": "5" * 64, "concepts/cell.png": "3" * 64}}
    docs = [({"path": "fixture.json", "sha256": "6" * 64}, document)]
    return mapping, benchmark, manifest, docs, kwargs


def test_actual_visual_positive_never_becomes_formal_pass_or_requires_fresh_download():
    m, b, f, docs, kwargs = inputs()
    report = merge_reviews(m, b, f, docs, **kwargs)
    assert report["summary"]["visual_positive_queries"] == 2
    assert report["summary"]["formal_queries_passed"] is None
    assert all(q["pass"] is None and q["original_evidence_reuse_allowed"] for q in report["queries"])
    assert report["original_verification_policy"]["fresh_download_required_for_each_review"] is False


def test_partial_review_stays_partial_and_uncertain_never_counts_as_relevant():
    m, b, f, docs, kwargs = inputs()
    doc = docs[0][1]
    doc["queries"] = doc["queries"][:1]
    doc["queries"][0]["result_judgments"][0]["verdict"] = "uncertain"
    doc["queries"][0]["gap_reason"] = "Visible identity is unresolved in this fixture."
    report = merge_reviews(m, b, f, docs, **kwargs)
    assert report["summary"]["missing_queries"] == ["cell-en"]
    assert report["summary"]["visual_positive_queries"] == 0
    assert report["queries"][0]["pass"] is False


def test_concept_observation_format_is_joined_only_to_its_own_query():
    m, b, f, docs, kwargs = inputs()
    doc = docs[0][1]
    judgment = doc["queries"][0]["result_judgments"][0]
    doc["concepts"] = [{"concept_id": "cell", "items": [{**judgment, "relevance": "relevant",
                        "visual_observations": judgment["evidence_notes"], "sheet_evidence": judgment["evidence"]}]}]
    doc["query_observations"] = [{"query_id": q["id"], "results_in_original_order": [{
        "candidate_id": "family:file-0", "rank": 1, "original_sha256": "1" * 64, "preview_sha256": "2" * 64, "relevance": "relevant"}]} for q in doc["queries"]]
    for q in doc["queries"]:
        q.pop("result_judgments")
    assert merge_reviews(m, b, f, docs, **kwargs)["summary"]["result_positions_reviewed"] == 2


@pytest.mark.parametrize("tamper", ["original", "member", "rank", "missing_result", "duplicate_query", "mapping_pack"])
def test_wrong_binding_or_incomplete_top10_is_rejected(tamper):
    m, b, f, docs, kwargs = inputs()
    doc = docs[0][1]
    row = doc["queries"][0]["result_judgments"][0]
    if tamper == "original":
        row["original_sha256"] = "9" * 64
    elif tamper == "member":
        row["evidence"]["member"] = "review/concepts/cell.png"
    elif tamper == "rank":
        row["rank"] = 2
    elif tamper == "missing_result":
        doc["queries"][0]["result_judgments"] = []
    elif tamper == "duplicate_query":
        doc["queries"].append(copy.deepcopy(doc["queries"][0]))
    elif tamper == "mapping_pack":
        kwargs["member_hashes"]["mapping.json"] = "0" * 64
    with pytest.raises(ValueError):
        merge_reviews(m, b, f, docs, **kwargs)


def test_replay_reuse_checks_arrays_without_promoting_new_core_acceptance():
    m, _, _, _, _ = inputs()
    rows = [{"query_id": q["id"], "before": [{"rank": 1, "family_id": "family", "original_sha256": "1" * 64}],
             "after": [{"rank": 1, "family_id": "family", "original_sha256": "1" * 64}]} for q in m["queries"]]
    replay = {"bindings": {"catalog_sha256": "c" * 64, "benchmark_sha256": "b" * 64,
              "old_core_sha256": "d" * 64, "old_ranking_version": "old", "new_ranking_version": "new"}, "queries": rows}
    result = check_replay(replay, m)
    assert result["eligible_for_exact_observation_reuse"]
    assert result["new_version_supplemental_acceptance_claimed"] is False
    replay["query_lists_changed"] = 0  # A stated zero cannot override actual differences.
    rows[0]["after"][0]["original_sha256"] = "9" * 64
    with pytest.raises(ValueError, match="changed selected files"):
        check_replay(replay, m)
