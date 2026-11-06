"""Merge actual per-result visual reviews; never grant formal release acceptance.

Reads frozen mappings, exact preview evidence and existing reviewer documents.
It neither searches nor downloads artwork, changes judgments, or requires a
fresh original download when previously verified exact-file evidence exists.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tarfile


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def check_replay(replay, mapping):
    """Verify actual ordered arrays, not an asserted zero-difference boolean."""
    bindings = replay.get("bindings", {})
    base = mapping["bindings"]
    for key in ("catalog_sha256", "benchmark_sha256"):
        require(bindings.get(key) == base[key], f"Replay {key} mismatch")
    require(bindings.get("old_core_sha256") == base["search_core_sha256"], "Replay old core mismatch")
    require(bindings.get("old_ranking_version") == base["ranking_version"], "Replay old ranking mismatch")
    rows = replay.get("queries", [])
    by_id = {row["query_id"]: row for row in rows}
    require(len(rows) == len(by_id) == len(mapping["queries"]), "Replay query count/duplicates mismatch")
    for query in mapping["queries"]:
        expected = [{"rank": row["rank"], "family_id": row["family_id"],
                     "original_sha256": mapping["candidates"][row["candidate_id"]]["original_sha256"]}
                    for row in query["candidates"]]
        replay_row = by_id.get(query["id"], {})
        require(replay_row.get("before") == expected, f"Replay old list mismatch: {query['id']}")
        require(replay_row.get("after") == expected, f"Replay changed selected files/ranks: {query['id']}")
    return {"eligible_for_exact_observation_reuse": True,
            "review_ranking_version": base["ranking_version"],
            "replayed_ranking_version": bindings.get("new_ranking_version"),
            "replayed_core_sha256": bindings.get("new_core_sha256"),
            "queries_compared": len(rows), "changed_ordered_lists": 0,
            "new_version_supplemental_acceptance_claimed": False,
            "new_version_formal_acceptance_claimed": False}


def merge_reviews(mapping, benchmark, manifest, documents, *, mapping_sha256,
                  benchmark_sha256, manifest_sha256, pack_sha256, member_hashes,
                  replay=None):
    base = mapping["bindings"]
    require(base["benchmark_sha256"] == benchmark_sha256, "Benchmark bytes differ from frozen mapping")
    require(base["candidate_manifest_raw_sha256"] == manifest_sha256, "Candidate manifest bytes differ")
    require(base["catalog_sha256"] == manifest.get("catalog_sha256"), "Manifest catalog mismatch")
    require(member_hashes.get("mapping.json") == mapping_sha256, "Actual pack does not contain this exact mapping")
    expected_queries = {row["id"]: row for row in benchmark["queries"]}
    mapped = {row["id"]: row for row in mapping["queries"]}
    require(len(mapped) == len(mapping["queries"]) == len(expected_queries), "Mapping query coverage/duplicates mismatch")
    files = {row["sha256"]: row for row in manifest["files"]}
    concepts = {row["id"]: row for row in mapping["concepts"]}
    normalized, input_refs = {}, []
    for reference, doc in documents:
        require(doc.get("reviewer") and doc.get("reviewed_at"), f"Reviewer/date missing: {reference['path']}")
        require(doc.get("independent_from_metadata_matcher") is True, f"Independent visual-review declaration missing: {reference['path']}")
        for key in ("catalog_sha256", "benchmark_sha256", "search_core_sha256", "ranking_version", "search_index_version", "diagnostic_sha256", "candidate_manifest_raw_sha256"):
            require(doc.get("bindings", {}).get(key) == base[key], f"Review {key} mismatch: {reference['path']}")
        recorded_mapping = (doc["bindings"].get("mapping_sha256") or doc["bindings"].get("cloud_mapping_sha256")
                            or doc.get("review_evidence", {}).get("mapping_sha256"))
        require(recorded_mapping is None or recorded_mapping == mapping_sha256, f"Review mapping SHA mismatch: {reference['path']}")
        pack = doc.get("review_pack") or doc.get("preview_artifact") or doc.get("review_evidence", {}).get("pack", {})
        require(pack.get("sha256") == pack_sha256, f"Review pack SHA mismatch: {reference['path']}")
        pack_uri = pack.get("uri") or pack.get("url")
        require(bool(pack_uri), "Review pack URI is missing")
        input_refs.append({**reference, "reviewer": doc["reviewer"], "reviewed_at": doc["reviewed_at"]})
        observations = {row["query_id"]: row for row in doc.get("query_observations", [])}
        concept_items = {(c["concept_id"], item["candidate_id"]): item
                         for c in doc.get("concepts", []) for item in c.get("items", [])}
        for query in doc.get("queries", []):
            qid = query.get("query_id") or query.get("id")
            require(qid in mapped and qid not in normalized, f"Unknown or duplicate reviewed query: {qid}")
            expected, original = mapped[qid], expected_queries[qid]
            for key in ("query", "language", "concept_id", "category"):
                require(query.get(key) == original.get(key) == expected.get(key), f"Query {key} mismatch: {qid}")
            if "candidates" in query:
                require(query["candidates"] == expected["candidates"], f"Original ranks changed: {qid}")
            raw = query.get("result_judgments")
            if raw is None and qid in observations:
                raw = []
                for observed in observations[qid]["results_in_original_order"]:
                    item = concept_items.get((original["concept_id"], observed["candidate_id"]), {})
                    require(item.get("relevance") == observed.get("relevance"), f"Observation verdict conflicts: {qid}")
                    raw.append({**item, **observed, "verdict": item.get("relevance"),
                                "evidence_notes": item.get("visual_observations"), "evidence": item.get("sheet_evidence")})
            require(isinstance(raw, list), f"Actual result judgments missing: {qid}")
            require([row.get("candidate_id") for row in raw] == [row["candidate_id"] for row in expected["candidates"]], f"Result judgment order/coverage mismatch: {qid}")
            results = []
            for actual, judgment in zip(expected["candidates"], raw):
                cid = actual["candidate_id"]
                candidate = mapping["candidates"][cid]
                file = files.get(candidate["original_sha256"], {})
                require(candidate["family_id"] in file.get("family_ids", []), f"File/family not in manifest: {cid}")
                require(candidate["original_archive"] == file.get("archive") and candidate["preview"]["archive"] == file.get("preview", {}).get("archive")
                        and candidate["preview"]["sha256"] == file["preview"]["sha256"], f"Manifest archive/preview mismatch: {cid}")
                require(judgment.get("original_sha256") == candidate["original_sha256"] and judgment.get("preview_sha256") == candidate["preview"]["sha256"], f"Reviewed file SHA mismatch: {cid}")
                require(judgment.get("rank", actual["rank"]) == actual["rank"], f"Reviewed rank mismatch: {cid}")
                require(judgment.get("verdict") in {"relevant", "irrelevant", "uncertain"} and judgment.get("evidence_notes"), f"Empty/invalid actual judgment: {cid}")
                sheet = concepts[original["concept_id"]]
                tile = next(i for i, row in enumerate(sheet["candidates"], 1) if row["candidate_id"] == cid)
                evidence = judgment.get("evidence") or judgment.get("sheet_evidence")
                if evidence is None:
                    source_sheet = judgment.get("sheet", {})
                    require(source_sheet.get("displayed_and_inspected") is True, f"Sheet inspection not recorded: {cid}")
                    evidence = {"uri": pack_uri, "sha256": pack_sha256,
                                "member": source_sheet.get("pack_member"), "member_sha256": source_sheet.get("sha256"), "tile": tile}
                require(evidence.get("uri") == pack_uri and evidence.get("sha256") == pack_sha256, f"Sheet pack evidence mismatch: {cid}")
                require(evidence.get("member") == sheet["sheet"] and evidence.get("member_sha256") == sheet["sheet_metadata"]["sha256"], f"Sheet identity mismatch: {cid}")
                require(member_hashes.get(evidence["member"]) == evidence["member_sha256"] and evidence.get("tile") == tile, f"Actual packed sheet/tile mismatch: {cid}")
                results.append({"candidate_id": cid, "rank": actual["rank"], "family_id": actual["family_id"],
                                "original_sha256": candidate["original_sha256"], "preview_sha256": candidate["preview"]["sha256"],
                                "original_archive": candidate["original_archive"], "preview_archive": candidate["preview"]["archive"],
                                "verdict": judgment["verdict"], "evidence_notes": judgment["evidence_notes"],
                                "evidence": evidence, "review_document": reference})
            relevant = [row["candidate_id"] for row in results if row["verdict"] == "relevant"]
            chosen = query.get("selected_candidate_ids", [])
            require(isinstance(chosen, list) and len(chosen) == len(set(chosen)) and set(chosen) <= set(relevant), f"Explicit selection is not relevant: {qid}")
            if not relevant:
                require(query.get("gap_reason"), f"Specific gap explanation missing: {qid}")
            normalized[qid] = {"query_id": qid, "concept_id": original["concept_id"], "category": original["category"],
                               "language": original["language"], "query": original["query"],
                               "result_judgments": results, "visual_relevance_pass": bool(relevant),
                               "relevant_candidate_ids": relevant, "selected_candidate_ids": chosen, "gap_reason": query.get("gap_reason"),
                               "pass": None if relevant else False, "formal_acceptance_status": "not_evaluated_by_visual_merge",
                               "review_document": reference, "reviewer": doc["reviewer"],
                               "fresh_original_download_claimed_in_review": query.get("original_download_checked") is True,
                               "original_evidence_reuse_allowed": True}
    ordered = [normalized[qid] for qid in expected_queries if qid in normalized]
    missing = [qid for qid in expected_queries if qid not in normalized]
    concept_rows, categories = [], defaultdict(lambda: {"reviewed_queries": 0, "visual_positive_queries": 0, "concepts_positive_both_languages": 0})
    for concept in benchmark["concepts"]:
        qs = [row for row in ordered if row["concept_id"] == concept["id"]]
        complete = {row["language"] for row in qs} == {"zh", "en"}
        positive = complete and all(row["visual_relevance_pass"] for row in qs)
        concept_rows.append({"concept_id": concept["id"], "category": concept["category"], "reviewed_languages": [row["language"] for row in qs], "visual_positive_both_languages": positive, "complete": complete})
        counts = categories[concept["category"]]
        counts["reviewed_queries"] += len(qs)
        counts["visual_positive_queries"] += sum(row["visual_relevance_pass"] for row in qs)
        counts["concepts_positive_both_languages"] += positive
    languages = {language: {"reviewed_queries": sum(row["language"] == language for row in ordered),
                            "visual_positive_queries": sum(row["language"] == language and row["visual_relevance_pass"] for row in ordered)}
                 for language in sorted({row["language"] for row in expected_queries.values()})}
    return {"schema_version": 1, "status": "visual_review_merged_formal_acceptance_pending",
            "generated_at": datetime.now(timezone.utc).isoformat(), "bindings": {**base, "mapping_sha256": mapping_sha256},
            "review_documents": input_refs, "queries": ordered, "concepts": concept_rows,
            "summary": {"expected_queries": len(expected_queries), "reviewed_queries": len(ordered), "missing_queries": missing,
                        "result_positions_reviewed": sum(len(row["result_judgments"]) for row in ordered),
                        "visual_positive_queries": sum(row["visual_relevance_pass"] for row in ordered),
                        "result_position_verdicts": dict(Counter(result["verdict"] for row in ordered for result in row["result_judgments"])),
                        "by_category": dict(categories), "by_language": languages,
                        "formal_queries_passed": None, "formal_acceptance_passed": False},
            "replay_reuse": check_replay(replay, mapping) if replay else None,
            "original_verification_policy": {"fresh_download_required_for_each_review": False,
                "accepted_evidence_routes": ["Previously verified collection original bytes/hash plus exact archive member and immutable archive digest", "Later clean-package restore of the same exact original bytes/hash"],
                "status": "evidence_join_pending_separate_verifier", "fresh_download_false_does_not_invalidate_prior_evidence": True},
            "limitations": ["Visual-positive is not formal query acceptance or permission to publish.", "Original retrieval, rights, native-editing and clean-restore evidence are separate checks; existing exact-file evidence may be reused.", "The historical review remains bound to its original core. Identical replayed lists permit observation reuse, not a new-version supplemental acceptance claim."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("mapping", "benchmark", "manifest", "review-pack", "output"):
        parser.add_argument("--" + flag, type=Path, required=True)
    parser.add_argument("--review", type=Path, action="append", required=True)
    parser.add_argument("--replay-diff", type=Path)
    args = parser.parse_args()
    with tarfile.open(args.review_pack, "r:gz") as archive:
        members = {member.name: hashlib.sha256(archive.extractfile(member).read()).hexdigest()
                   for member in archive if member.isfile() and (member.name.startswith("concepts/") or member.name == "mapping.json")}
    documents = [({"path": path.as_posix(), "sha256": digest(path)}, read(path)) for path in args.review]
    report = merge_reviews(read(args.mapping), read(args.benchmark), read(args.manifest), documents,
                           mapping_sha256=digest(args.mapping), benchmark_sha256=digest(args.benchmark),
                           manifest_sha256=digest(args.manifest), pack_sha256=digest(args.review_pack), member_hashes=members,
                           replay=read(args.replay_diff) if args.replay_diff else None)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(report["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
