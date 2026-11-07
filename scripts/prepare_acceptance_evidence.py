"""Bridge existing frozen evidence without inventing formal query approvals.

Metadata only: no network, search, artwork rendering or original downloads.
This preparation does not run or weaken the final acceptance checker.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from build_publish_pack import validate_remote_restore
from merge_relevance_reviews import check_replay


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def file_sha(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while block := stream.read(1024 * 1024):
            result.update(block)
    return result.hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_candidate(candidate, manifest_files):
    item = manifest_files.get(candidate.get("original_sha256"), {})
    preview = candidate.get("preview") or {}
    require(item and candidate.get("asset_id") in item["asset_ids"]
            and candidate.get("family_id") in item["family_ids"]
            and candidate.get("type") == item["structure"]
            and preview.get("sha256") == item["preview"]["sha256"]
            and candidate.get("original_archive") == item["archive"]
            and preview.get("archive") == item["preview"]["archive"]
            and candidate.get("preview_status") == "viewable", "Candidate does not bind an allowed exact original/preview pair")
    return item


def prepare(*, mapping, merged, curated, negative, negative_raw, benchmark, manifest,
            bundle, restore, review_documents, replay, hashes, refs, generated_at=None):
    """Return new mapping, nonpromoted judgments and an explicit readiness audit."""
    require(validate_remote_restore(restore, bundle, manifest,
            manifest_sha256=hashes["publish_manifest_sha256"], bundle_sha256=hashes["bundle_index_sha256"]),
            "Independent restore validation failed")
    reuse = check_replay(replay, mapping)
    require(reuse["replayed_core_sha256"] == hashes["search_core_sha256"], "Replay is not for the current core")
    for key in ("catalog_sha256", "benchmark_sha256"):
        require(mapping["bindings"].get(key) == merged["bindings"].get(key) == hashes[key], "Frozen input binding mismatch: " + key)
    require(merged["bindings"].get("mapping_sha256") == hashes["source_mapping_sha256"], "Merged review mapping hash mismatch")
    require(merged["bindings"].get("candidate_manifest_raw_sha256") == hashes["publish_manifest_sha256"], "Merged review manifest mismatch")
    require(curated["catalog_binding"]["sha256"] == hashes["catalog_sha256"]
            and curated["manifest_binding"]["sha256"] == hashes["publish_manifest_sha256"], "Curated binding mismatch")
    for report in (negative, negative_raw):
        for key in ("catalog_sha256", "benchmark_sha256", "search_core_sha256"):
            require(report["bindings"].get(key) == hashes[key], "Negative replay binding mismatch: " + key)
    require(negative["bindings"].get("publish_manifest_sha256") == hashes["publish_manifest_sha256"], "Negative manifest mismatch")

    files = {f["sha256"]: f for f in manifest["files"]}
    objects = {obj["sha256"]: {"pack_url": part["url"], "pack_sha256": part["sha256"], **obj}
               for part in bundle["packs"] for obj in part["members"]}
    output_mapping = copy.deepcopy(mapping)
    candidates = output_mapping["candidates"]
    def add(key, candidate):
        validate_candidate(candidate, files)
        if key in candidates:
            old = candidates[key]
            require(all(old.get(k) == candidate.get(k) for k in
                    ("asset_id", "family_id", "original_sha256", "original_archive", "type"))
                    and old["preview"]["sha256"] == candidate["preview"]["sha256"]
                    and old["preview"]["archive"] == candidate["preview"]["archive"], "Conflicting candidate ID")
        else:
            candidates[key] = copy.deepcopy(candidate)
    for candidate in candidates.values():
        validate_candidate(candidate, files)
    for key, candidate in curated["mapping_candidates"].items():
        add(key, candidate)
    require(curated.get("independent_from_metadata_matcher") is True and curated.get("reviewer"), "Curated independent review missing")

    expected_negatives = {row["id"]: row for row in benchmark["negative_scenarios"]}
    raw_scenarios = {row["scenario"]["id"]: row for row in negative_raw["scenarios"]}
    reviewed_ids = [row["scenario_id"] for row in negative["negative_scenarios"]]
    require(len(raw_scenarios) == len(negative_raw["scenarios"]) == len(expected_negatives)
            and set(raw_scenarios) == set(expected_negatives)
            and len(reviewed_ids) == len(set(reviewed_ids)) == len(expected_negatives)
            and set(reviewed_ids) == set(expected_negatives), "Negative scenario identity coverage mismatch")
    raw_results = {}
    for scenario in negative_raw["scenarios"]:
        for row in scenario["raw_result"]["results"]:
            key = row["variant_id"]
            candidate = {"candidate_id": key, "asset_id": row["asset_id"], "family_id": row["family_id"],
                "title": row["title"], "original_sha256": row["selected_file"]["sha256"],
                "original_archive": row["selected_file"]["archive"], "preview": row["preview"],
                "type": row["selected_file"]["structure"], "preview_status": "viewable"}
            require(key not in raw_results or raw_results[key] == candidate,
                    "Conflicting raw variant binding across negative scenarios")
            raw_results[key] = candidate
    negative_rows = []
    require(negative.get("independent_from_metadata_matcher") is True, "Independent negative review missing")
    for row in negative["negative_scenarios"]:
        expected = expected_negatives[row["scenario_id"]]
        actual = raw_scenarios[row["scenario_id"]]
        require(actual["effective_request"] == row["request"], "Negative raw effective request differs")
        require([c["variant_id"] for c in actual["raw_result"]["results"]] == row["observed_candidate_ids"],
                "Negative raw result order or set differs from its reviewed scenario")
        require(row["request"]["query"] == expected["query"] and row["request"].get("filters", {}) == expected.get("filters", {}), "Negative request changed")
        reviewed = {c["candidate_id"]: c for c in row["candidates"]}
        require(set(reviewed) == set(row["observed_candidate_ids"]), "Negative observed/reviewed candidate set differs")
        for key in row["observed_candidate_ids"]:
            candidate = raw_results[key]
            seen = reviewed[key]
            require(candidate["original_sha256"] == seen["original_sha256"]
                    and candidate["preview"]["sha256"] == seen["preview_sha256"]
                    and candidate["original_archive"] == seen["original_archive"]
                    and candidate["preview"]["archive"] == seen["preview_archive"], "Negative exact-file review mismatch")
            add(key, candidate)
        negative_rows.append({"scenario_id": row["scenario_id"], "query": expected["query"],
            "filters": expected.get("filters", {}), "pass": row["pass"],
            "observed_candidate_ids": copy.deepcopy(row["observed_candidate_ids"]),
            "evidence_notes": row["evidence_notes"], "source_review": refs["negative"]})
    require(len(negative_rows) == len(expected_negatives), "Negative scenario coverage mismatch")

    restore_links = {}
    for key, candidate in candidates.items():
        item = validate_candidate(candidate, files)
        pair = []
        for role, f in (("original", item), ("preview", item["preview"])):
            obj = objects[f["sha256"]]
            require(obj["size_bytes"] == f["size_bytes"] and role in obj["roles"], "Restored object role or size mismatch")
            pair.append({"role": role, "sha256": f["sha256"], "size_bytes": f["size_bytes"],
                         "clean_member": obj["path"], "pack_url": obj["pack_url"], "pack_sha256": obj["pack_sha256"]})
        restore_links[key] = pair

    by_query = {row["id"]: row for row in mapping["queries"]}
    benchmark_queries = {row["id"]: row for row in benchmark["queries"]}
    require(set(by_query) == set(benchmark_queries), "Mapping query coverage mismatch")
    docs = {}
    for reference in merged["review_documents"]:
        doc = review_documents[reference["path"]]
        require(doc.get("independent_from_metadata_matcher") is True and doc.get("reviewer") and doc.get("reviewed_at"), "Original independent review identity missing")
        docs[reference["path"]] = {row.get("query_id") or row.get("id"): row for row in doc["queries"]}
    query_rows, pending = [], []
    for row in merged["queries"]:
        qid = row["query_id"]
        expected = by_query[qid]
        ids = [r["candidate_id"] for r in expected["candidates"]]
        require([r["candidate_id"] for r in row["result_judgments"]] == ids, "Reviewed result order changed")
        for result in row["result_judgments"]:
            candidate = candidates[result["candidate_id"]]
            require(result["original_sha256"] == candidate["original_sha256"]
                    and result["preview_sha256"] == candidate["preview"]["sha256"], "Query reviewed hash mismatch")
        source_query = docs[row["review_document"]["path"]][qid]
        result = copy.deepcopy(row)
        # Preserve the existing null/false formal decision and explicit choice.
        result.update(source_evidence_checked=source_query.get("source_evidence_checked"),
            license_checked=source_query.get("license_checked"), species_matches=source_query.get("species_matches"),
            original_download_checked=True if ids else None,
            visual_evidence_notes=source_query.get("visual_evidence_notes") or
                "Existing exact per-result visual observations are retained; this bridge performs no new visual judgment.",
            original_verification={"route": "independent_remote_restore_of_identical_bytes",
                "restore_evidence": refs["restore"], "bundle_index": refs["bundle"],
                "publish_manifest_sha256": hashes["publish_manifest_sha256"],
                "candidate_objects": {key: restore_links[key] for key in ids},
                "fresh_download_performed_by_this_bridge": False})
        require(result["pass"] == row["pass"] and result["selected_candidate_ids"] == row["selected_candidate_ids"], "Formal judgment was promoted")
        needs = []
        if row["pass"] is None:
            if not row["selected_candidate_ids"]: needs.append("explicit_selection_from_already_reviewed_relevant_candidates")
            for flag in ("source_evidence_checked", "license_checked", "species_matches"):
                if result[flag] is not True: needs.append(flag)
            needs.append("explicit_formal_query_decision")
        if needs:
            pending.append({"query_id": qid, "required_species": benchmark_queries[qid].get("required_species"),
                            "needs": needs, "new_visual_review_automatically_required": False})
        query_rows.append(result)
    require(len(query_rows) == len(by_query) and len({r["query_id"] for r in query_rows}) == len(by_query), "Merged query coverage mismatch")
    output_mapping["acceptance_bridge"] = {"source_mapping": refs["mapping"], "source_merged_review": refs["merged"],
        "current_core_replay": refs["replay"], "reuse": reuse, "query_rankings_unchanged": True}
    new_mapping_sha = hashlib.sha256(json_bytes(output_mapping)).hexdigest()
    at = generated_at or datetime.now(timezone.utc).isoformat()
    judgments = {"schema_version": 1, "benchmark_version": benchmark["benchmark_version"],
        "reviewer": "Evidence bridge; independent visual judgments retained from the named source reviewers",
        "reviewed_at": at, "independent_from_metadata_matcher": True,
        "independent_review_basis": copy.deepcopy(merged["review_documents"]),
        "bindings": {key: hashes[key] for key in ("catalog_sha256", "benchmark_sha256", "assembly_report_sha256", "publish_manifest_sha256")},
        "queries": query_rows, "curated": copy.deepcopy(curated["curated"]), "negative_scenarios": negative_rows,
        "formal_query_decisions_changed": False, "new_visual_judgments": 0,
        "note": "Original download checked means the exact immutable bytes were restored in the independently recorded cloud run; old review-event download flags and formal decisions remain unchanged."}
    judgments["bindings"]["mapping_sha256"] = new_mapping_sha
    readiness = {"schema_version": 1, "generated_at": at, "status": "bound_existing_evidence_formal_query_confirmation_pending",
        "bindings": {**judgments["bindings"], "search_core_sha256": hashes["search_core_sha256"],
                     "bundle_index_sha256": hashes["bundle_index_sha256"], "restore_evidence_sha256": hashes["restore_evidence_sha256"]},
        "current_ranking_version": reuse["replayed_ranking_version"], "input_proofs": refs,
        "counts": {"queries": len(query_rows), "result_positions": sum(len(r["result_judgments"]) for r in query_rows),
            "visual_positive_queries": sum(r["visual_relevance_pass"] for r in query_rows),
            "formal_pass_true": sum(r["pass"] is True for r in query_rows), "formal_pending": sum(r["pass"] is None for r in query_rows),
            "recorded_failed_queries": sum(r["pass"] is False for r in query_rows), "mapped_candidate_ids": len(candidates),
            "unique_mapped_originals": len({c["original_sha256"] for c in candidates.values()}),
            "curated_distinct_families": len({r["family_id"] for r in curated["curated"]}),
            "negative_scenarios_reused": len(negative_rows), "restored_exact_pairs": len(restore_links)},
        "pending_confirmations": pending, "pending_field_counts": dict(Counter(key for row in pending for key in row["needs"])),
        "restore_binding_passed": True, "actual_restoration_run": restore["restore_run"],
        "originals_downloaded_by_bridge": 0, "new_visual_judgments": 0, "acceptance_checker_executed": False,
        "overall_acceptance_passed": False, "thresholds_changed": False,
        "next_step": "An identified reviewer may finalize existing positive-query evidence and explicit selections; absent confirmation stays pending. Run accept_release once with these files to obtain an honest failure report. Visual100/120 is separate from formal approvals.",
        "limits": ["Independent restoration proves bytes, not species, licenses, medical relevance or native editability.",
                   "No pending pass is promoted; no missing species flag is manufactured, including queries with no species requirement.",
                   "The 48 curated selections and six negative judgments are reused, not newly adjudicated."]}
    return output_mapping, judgments, readiness


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    defaults = {
        "mapping": "build/frozen-review-42298772894/review/mapping.json",
        "merged": "data/relevance-reviews/frozen-3.4.1-merged-120.json",
        "curated": "data/curated-reviews/selected-object-components-42289434868.json",
        "negative": "data/diagnostics/final-negative-observation-42289434868-post-phrase-3.4.3.json",
        "negative_raw": "build/negative-final-42289434868/raw-negative-results-post-phrase-3.4.3.json",
        "benchmark": "data/benchmark.json", "assembly": "build/final-scope-baseline/assembly-42289434868-1.report.json",
        "manifest": "build/cloud-recovery-20261107/frozen-42289434868-3.4.3.publish_manifest.json",
        "bundle": "build/cloud-recovery-20261107/build-evidence/qa-20261107-42289434868-3.4.3.bundle.json",
        "restore": "build/cloud-recovery-20261107/restore-evidence/qa-20261107-42289434868-3.4.3-42312769493-1.restore_evidence.json",
        "replay": "build/search-filter-3.4.3/replay-diff.json",
        "source_readiness": "build/dispositions-final-scope-42289434868/acceptance-evidence-readiness.json",
        "review_summary": "build/frozen-review-42298772894/review/summary.json",
        "supplement": "build/search-filter-3.4.3/supplement/supplemental-evidence.json"}
    for name, path in defaults.items(): parser.add_argument("--" + name.replace("_", "-"), type=Path, default=ROOT / path)
    parser.add_argument("--output", type=Path, default=ROOT / "build/current-acceptance-evidence-42312769493")
    parser.add_argument("--readiness-output", type=Path, default=ROOT / "data/diagnostics/current-acceptance-readiness-42312769493.json")
    args = parser.parse_args()
    paths = {name: getattr(args, name) for name in defaults}
    loaded = {name: json.loads(path.read_bytes()) for name, path in paths.items()}
    def reference(path):
        path = Path(path).resolve()
        try: label = path.relative_to(ROOT).as_posix()
        except ValueError: label = str(path)
        return {"uri": label, "sha256": file_sha(path)}
    refs = {name: reference(path) for name, path in paths.items()}
    documents = {}
    for doc in loaded["merged"]["review_documents"]:
        path = ROOT / doc["path"]
        require(file_sha(path) == doc["sha256"], "Changed original visual-review document: " + doc["path"])
        documents[doc["path"]] = json.loads(path.read_bytes())
    hashes = {"catalog_sha256": loaded["manifest"]["catalog_sha256"], "benchmark_sha256": refs["benchmark"]["sha256"],
        "assembly_report_sha256": refs["assembly"]["sha256"], "source_mapping_sha256": refs["mapping"]["sha256"],
        "publish_manifest_sha256": refs["manifest"]["sha256"], "bundle_index_sha256": refs["bundle"]["sha256"],
        "restore_evidence_sha256": refs["restore"]["sha256"], "search_core_sha256": file_sha(ROOT / "mga_resources/search-core.mjs")}
    require(loaded["assembly"]["summary"]["catalog_sha256"] == hashes["catalog_sha256"], "Assembly catalog binding mismatch")
    mapping, judgments, readiness = prepare(**{key: loaded[key] for key in
        ("mapping", "merged", "curated", "negative", "negative_raw", "benchmark", "manifest", "replay")},
        bundle=loaded["bundle"], restore=loaded["restore"], review_documents=documents, hashes=hashes, refs=refs)
    args.output.mkdir(parents=True, exist_ok=True)
    outputs = {}
    for name, value in (("mapping", mapping), ("judgments", judgments), ("review-summary", loaded["review_summary"])):
        path = args.output / (name + ".json")
        path.write_bytes(json_bytes(value))
        outputs[name] = reference(path)
    readiness["outputs"] = outputs
    cli = list(loaded["source_readiness"]["ready_cli_arguments"])
    for flag, path in (("mapping", args.output / "mapping.json"), ("judgments", args.output / "judgments.json"),
        ("review-summary", args.output / "review-summary.json"), ("supplemental-evidence", args.supplement),
        ("bundle-index", args.bundle), ("restore-evidence", args.restore), ("output", args.output / "acceptance-report")):
        cli.extend(["--" + flag, str(path)])
    readiness["accept_release_cli_arguments"] = cli
    readiness["source_readiness_is_historical_preparation"] = True
    readiness["source_readiness_old_missing_flags_not_used"] = True
    args.readiness_output.parent.mkdir(parents=True, exist_ok=True)
    args.readiness_output.write_text(json.dumps(readiness, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"readiness": str(args.readiness_output), "counts": readiness["counts"],
                      "pending_field_counts": readiness["pending_field_counts"], "overall_acceptance_passed": False}))


if __name__ == "__main__":
    main()
