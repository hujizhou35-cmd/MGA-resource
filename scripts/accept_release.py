"""Evaluate a frozen publication subset using metadata and explicit review evidence.

No artwork is downloaded. A first run writes a candidate manifest and a readable
missing-evidence report; blank templates never grant acceptance.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mga_resources.catalog import TAXONOMY, redistribution_status

FROZEN_BENCHMARK_SHA256 = "ce0282afeb4afbcee8fdef1c6f937844ef569cabf16ba7909e22bf86b1096e92"
SHA = re.compile(r"^[0-9a-f]{64}$")


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def digest(value):
    return hashlib.sha256(json_bytes(value)).hexdigest()


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def sha(value):
    return isinstance(value, str) and SHA.fullmatch(value) is not None


def identity(value):
    return nonempty(value) or (isinstance(value, dict) and value.get("kind") in {"human", "agent"}
                               and nonempty(value.get("id")))


def archive_valid(value):
    if not isinstance(value, dict):
        return False
    url, member = value.get("url"), value.get("member")
    return (nonempty(url) and url.startswith("https://") and nonempty(member)
            and not member.startswith("/") and "\\" not in member and ":" not in member
            and all(part not in {"", ".", ".."} for part in member.split("/")))


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def evaluate_release(catalog, assembly, benchmark, mapping, judgments, *, hashes,
                     registry, decisions=None, selection_manifests=None,
                     source_reviews=(), native_evidence=None, review_summary=None,
                     restore_evidence=None):
    """Return (candidate publication manifest, check report); never infer review."""
    missing = []
    def need(condition, code, detail):
        if not condition:
            missing.append({"code": code, "detail": detail})
        return bool(condition)

    decisions = decisions or {}
    selection_manifests = selection_manifests or {}
    judgments = judgments or {}
    mapping = mapping or {}
    review_summary = review_summary or {}
    restore_evidence = restore_evidence or {}
    release = assembly.get("collection_release")
    need(nonempty(release), "assembly.release", "Assembly collection_release is missing.")
    need(assembly.get("summary", {}).get("catalog_sha256") == hashes["catalog_sha256"],
         "assembly.catalog_hash", "Assembly report does not bind the supplied catalog bytes.")
    need(hashes["benchmark_sha256"] == FROZEN_BENCHMARK_SHA256,
         "benchmark.frozen", "The supplied benchmark differs from the frozen 120-query version.")
    queries = benchmark.get("queries", [])
    concepts = benchmark.get("concepts", [])
    categories = set(TAXONOMY)
    need(len(queries) == 120 and len({q.get("id") for q in queries}) == 120
         and len(concepts) == 60 and len({c.get("id") for c in concepts}) == 60
         and Counter(q.get("language") for q in queries) == {"zh": 60, "en": 60}
         and Counter(c.get("category") for c in concepts) == {key: 5 for key in categories},
         "benchmark.structure", "Require the original 60 concepts, 12 categories and 120 bilingual queries.")
    snapshots = {item["source"]: item for item in assembly.get("source_snapshots", [])}
    excluded_sources, excluded_ids, excluded_hashes = set(), set(), set()
    covered_indices = defaultdict(set)
    valid_decisions = []

    if decisions:
        need(decisions.get("template_only") is False, "decisions.template", "Scope decisions are still a template.")
        need(decisions.get("release_id") == release and
             decisions.get("input_catalog_sha256") == hashes["catalog_sha256"] and
             decisions.get("benchmark_sha256") == hashes["benchmark_sha256"],
             "decisions.binding", "Scope decisions must bind this release, catalog and benchmark.")
    decisions_bound = (not decisions or (decisions.get("template_only") is False
                       and decisions.get("release_id") == release
                       and decisions.get("input_catalog_sha256") == hashes["catalog_sha256"]
                       and decisions.get("benchmark_sha256") == hashes["benchmark_sha256"]))
    for decision in decisions.get("decisions", []):
        label = decision.get("decision_id", "unnamed")
        target = decision.get("target") or {}
        source, kind = target.get("source"), target.get("kind")
        evidence = decision.get("evidence") or []
        valid = (decisions_bound and decision.get("review_state") == "decided"
                 and decision.get("disposition") in {"not_acquired", "exclude_from_v1", "quarantine"}
                 and nonempty(source) and kind in {"source", "items", "files"}
                 and nonempty(decision.get("reason")) and identity(decision.get("reviewed_by"))
                 and nonempty(decision.get("reviewed_at"))
                 and nonempty((decision.get("impact") or {}).get("description"))
                 and nonempty((decision.get("mitigation") or {}).get("decision"))
                 and any(nonempty(e.get("uri")) and sha(e.get("sha256")) for e in evidence))
        snapshot = snapshots.get(source, {})
        if snapshot.get("discovery_sha256"):
            valid = valid and target.get("discovery_sha256") == snapshot["discovery_sha256"]
        entries = []
        if kind != "source":
            reference = target.get("selection_manifest") or {}
            selected = selection_manifests.get(reference.get("sha256"))
            valid = valid and sha(reference.get("sha256")) and isinstance(selected, dict)
            if isinstance(selected, dict):
                valid = valid and selected.get("source") == source and selected.get("discovery_sha256") == target.get("discovery_sha256")
                entries = selected.get("items") or []
                valid = valid and bool(entries)
                for entry in entries:
                    if kind == "items":
                        valid = valid and nonempty(entry.get("source_id"))
                    else:
                        valid = valid and sha(entry.get("sha256"))
        if not need(valid, "decisions.incomplete", f"{label}: a decided, evidence-bound target, impact and mitigation are required."):
            continue
        valid_decisions.append(label)
        if kind == "source":
            excluded_sources.add(source)
        for entry in entries:
            if kind == "items":
                excluded_ids.add((source, entry["source_id"]))
                index = entry.get("source_index")
                if type(index) is int and index >= 0:
                    covered_indices[source].add(index)
            else:
                excluded_hashes.add(entry["sha256"])

    manifest_files = {}
    bad_files = []
    source_families = defaultdict(set)
    hash_to_record = {}
    for record in catalog:
        record_sources = set(record.get("sources") or [record.get("source")])
        origins = record.get("provenance") or [record]
        for item in record.get("files") or []:
            original_sha = item.get("sha256")
            file_origins = item.get("source_records") or origins
            eligible_origins = [origin for origin in file_origins
                                if origin.get("source") not in excluded_sources
                                and (origin.get("source"), origin.get("source_id")) not in excluded_ids]
            if original_sha in excluded_hashes or not eligible_origins:
                continue
            preview = item.get("preview") or {}
            rights = [origin for origin in eligible_origins
                      if redistribution_status(origin) == "license_recorded" and nonempty(origin.get("source_url"))]
            checked = (sha(original_sha) and bool(rights) and type(item.get("size_bytes")) is int
                       and item["size_bytes"] > 0 and archive_valid(item.get("archive"))
                       and item.get("validation", {}).get("status") in {"machine_checked", "machine_checked_and_rendered"}
                       and isinstance(preview, dict) and sha(preview.get("sha256"))
                       and type(preview.get("size_bytes")) is int and preview["size_bytes"] > 0
                       and archive_valid(preview.get("archive")))
            if not checked:
                bad_files.append({"asset_id": record.get("id"), "sha256": original_sha,
                                  "sources": sorted(record_sources), "reason": "Missing valid rights, original/preview hash, archive or machine check."})
                need(False, "files.unresolved", f"{record.get('id')} / {original_sha}: quarantine by a decided exact-file record, or repair missing file evidence.")
                continue
            family = record.get("family_id") or record["id"]
            allowed_sources = sorted({origin["source"] for origin in rights})
            entry = manifest_files.setdefault(original_sha, {
                "sha256": original_sha, "size_bytes": item["size_bytes"], "format": item.get("format"),
                "structure": item.get("structure"), "archive": item["archive"], "preview": preview,
                "asset_ids": [], "family_ids": [], "sources": [], "provenance": [],
            })
            entry["asset_ids"] = sorted(set(entry["asset_ids"]) | {record["id"]})
            entry["family_ids"] = sorted(set(entry["family_ids"]) | {family})
            entry["sources"] = sorted(set(entry["sources"]) | set(allowed_sources))
            known = {digest(origin) for origin in entry["provenance"]}
            entry["provenance"].extend(origin for origin in rights if digest(origin) not in known)
            hash_to_record[original_sha] = record
            for source in allowed_sources:
                source_families[source].add(family)

    manifest = {"schema_version": 1, "acceptance_report_required": True,
                "collection_release": release, "catalog_sha256": hashes["catalog_sha256"],
                "files": [manifest_files[key] for key in sorted(manifest_files)]}
    manifest_sha = hashlib.sha256(json_bytes(manifest)).hexdigest()
    need(bool(manifest_files), "files.empty", "The publication subset contains no eligible artwork.")
    required_bindings = {**hashes, "publish_manifest_sha256": manifest_sha}
    for key, value in required_bindings.items():
        need(judgments.get("bindings", {}).get(key) == value, "judgments.binding", f"Judgments must bind {key}={value}.")
    if decisions and valid_decisions:
        need(decisions.get("publish_manifest", {}).get("sha256") == manifest_sha,
             "decisions.publish_manifest", "Scope decisions do not bind the resulting publication manifest.")
    for key in ("catalog_sha256", "benchmark_sha256"):
        need(review_summary.get(key) == hashes[key], "review.binding", f"Review summary must bind this {key}.")
    need(review_summary.get("assembly_prefix") == f"assembly-{assembly.get('run_id')}",
         "review.assembly", "Review sheets must identify this exact assembly run.")

    unresolved_sources = []
    for source in registry:
        if not source.get("primary"):
            continue
        key = source["source_id"]
        snapshot = snapshots.get(key, {})
        if key in excluded_sources:
            continue
        unavailable = sum(snapshot.get(field, 0) or 0 for field in ("held_count", "failed_records", "unknown"))
        missing_indices = {index for start, end in snapshot.get("missing_ranges", []) for index in range(start, end)}
        represented = covered_indices[key]
        complete = snapshot.get("acquisition_complete") is True
        if not complete:
            discovered, acquired = snapshot.get("discovered"), snapshot.get("acquired")
            expected_missing = (discovered - acquired - (snapshot.get("excluded_count") or 0)
                                if type(discovered) is int and type(acquired) is int else None)
            complete = (sha(snapshot.get("discovery_sha256")) and expected_missing is not None
                        and expected_missing >= 0 and all(index < discovered for index in represented)
                        and missing_indices <= represented and len(represented) >= max(expected_missing, unavailable + len(missing_indices))
                        and not snapshot.get("metadata_issues"))
        if not complete:
            unresolved_sources.append(key)
            need(False, "sources.unresolved", f"{key}: acquisition gaps require a decided source/precise-item disposition.")
    # File machine failures must be repaired or explicitly quarantined above;
    # incomplete whole-source flags do not override a correctly scoped subset.

    independent = (judgments.get("independent_from_metadata_matcher") is True
                   and identity(judgments.get("reviewer")) and nonempty(judgments.get("reviewed_at")))
    need(independent, "review.independent", "An identified human/Agent must record independent visual review and its date.")
    version = benchmark.get("benchmark_version")
    need(mapping.get("benchmark_version") == version and judgments.get("benchmark_version") == version,
         "review.version", "Mapping and judgments must use this benchmark version.")
    candidates = mapping.get("candidates") or {}
    valid_candidates = {}
    for key, candidate in candidates.items():
        item = manifest_files.get(candidate.get("original_sha256"))
        preview = candidate.get("preview") or {}
        if (item and candidate.get("asset_id") in item["asset_ids"]
                and candidate.get("family_id") in item["family_ids"]
                and candidate.get("type") == item["structure"]
                and preview.get("sha256") == item["preview"]["sha256"]
                and candidate.get("original_archive") == item["archive"]
                and preview.get("archive") == item["preview"]["archive"]
                and candidate.get("preview_status") == "viewable"):
            valid_candidates[key] = candidate

    mapping_queries = {row.get("id"): row for row in mapping.get("queries", [])}
    judged_queries = judgments.get("queries") or []
    need(len(judged_queries) == 120 and len({q.get("query_id") for q in judged_queries}) == 120,
         "queries.count", "Provide exactly one judgment for each of the 120 frozen queries.")
    by_query = {row.get("query_id"): row for row in judged_queries}
    successes, judged, language_success = set(), 0, Counter()
    flags = ("source_evidence_checked", "original_download_checked", "license_checked", "species_matches")
    for query in queries:
        qid = query["id"]
        actual, judgment = mapping_queries.get(qid, {}), by_query.get(qid, {})
        rows = actual.get("candidates") or []
        mapping_ok = (all(actual.get(key) == query.get(key) for key in ("query", "language", "concept_id"))
                      and len(rows) <= 10 and [row.get("rank") for row in rows] == list(range(1, len(rows) + 1))
                      and len({row.get("family_id") for row in rows}) == len(rows))
        selected = judgment.get("selected_candidate_ids") or []
        selection_ok = bool(selected) and all(key in valid_candidates and key in {row.get("candidate_id") for row in rows} for key in selected)
        if judgment.get("pass") is True:
            ok = (independent and mapping_ok and selection_ok and nonempty(judgment.get("visual_evidence_notes"))
                  and all(judgment.get(key) is True for key in flags))
            need(ok, "queries.evidence", f"{qid}: a pass needs an allowed top-10 candidate and explicit visual/species/source/download/license evidence.")
            if ok:
                judged += 1
                successes.add(qid)
                language_success[query["language"]] += 1
        elif judgment.get("pass") is False and nonempty(judgment.get("gap_reason")) and mapping_ok and independent:
            judged += 1
        else:
            need(False, "queries.pending", f"{qid}: record an evidenced pass or a specific gap.")
    covered = defaultdict(set)
    for query in queries:
        if query["id"] in successes:
            covered[query["category"]].add(query["concept_id"])
    need(judged == 120, "queries.reviewed", f"Only {judged}/120 query judgments are complete.")
    need(len(successes) >= 108, "queries.threshold", f"Retrieval success is {len(successes)}/120; need 108.")
    for language in ("zh", "en"):
        need(language_success[language] >= 54, "queries.language", f"{language}: {language_success[language]}/60; need 54.")
    for category in sorted(categories):
        need(len(covered[category]) >= 4, "queries.category", f"{category}: {len(covered[category])}/5 concepts; need 4.")

    negatives = judgments.get("negative_scenarios") or []
    by_negative = {row.get("scenario_id"): row for row in negatives}
    need(len(negatives) == 6 and len(by_negative) == 6, "negatives.count", "All six frozen negative/format/species scenarios require review.")
    negative_passes = 0
    for expected in benchmark.get("negative_scenarios", []):
        row = by_negative.get(expected["id"], {})
        observed = row.get("observed_candidate_ids")
        ok = (independent and row.get("pass") is True and row.get("query") == expected["query"]
              and row.get("filters", {}) == expected.get("filters", {})
              and nonempty(row.get("evidence_notes")) and isinstance(observed, list)
              and all(key in valid_candidates for key in observed))
        if expected["id"] == "raster-is-allowed":
            ok = ok and bool(observed) and all(valid_candidates[key].get("type") == "raster" for key in observed if key in valid_candidates)
        if expected["id"] == "vector-honesty":
            ok = ok and all(valid_candidates[key].get("type") == "pure_vector" for key in (observed or []) if key in valid_candidates)
        need(ok, "negatives.pending", f"{expected['id']}: record the actual result and independent verdict.")
        negative_passes += bool(ok)

    sampled = defaultdict(set)
    for row in judgments.get("samples", []):
        candidate = valid_candidates.get(row.get("candidate_id"))
        if candidate and independent and nonempty(row.get("visual_evidence_notes")):
            if row.get("usable") is False:
                need(False, "visual.rejected_file", f"{row['candidate_id']}: a visually rejected file remains in the publication subset.")
            if row.get("usable") is True:
                item = manifest_files[candidate["original_sha256"]]
                for source in item["sources"]:
                    sampled[source].add(candidate["family_id"])
    for review in source_reviews:
        header_ok = (identity(review.get("reviewer")) and nonempty(review.get("reviewed_at"))
                     and nonempty(review.get("review_method")) and nonempty(review.get("review_sheet_url"))
                     and sha(review.get("review_sheet_sha256")))
        for row in review.get("items", []):
            item = manifest_files.get(row.get("original_sha256"))
            if (header_ok and item and row.get("preview_sha256") == item["preview"]["sha256"]
                    and row.get("original_archive") == item["archive"]
                    and row.get("preview_archive") == item["preview"]["archive"]
                    and nonempty(row.get("observations"))
                    and row.get("visual_verdict") in {"usable_as_general_illustration", "usable_with_limitations", "usable"}):
                source = review.get("source")
                if source in item["sources"]:
                    sampled[source].update(item["family_ids"])
    curated, curated_categories = set(), defaultdict(set)
    for row in judgments.get("curated", []):
        candidate = valid_candidates.get(row.get("candidate_id"))
        if (candidate and independent and row.get("usable") is True and row.get("source_evidence_checked") is True
                and nonempty(row.get("visual_evidence_notes"))):
            family = candidate["family_id"]
            curated.add(family)
            for source in manifest_files[candidate["original_sha256"]]["sources"]:
                sampled[source].add(family)
            for category in row.get("categories", []):
                if category in categories:
                    curated_categories[category].add(family)
    need(len(curated) >= 60, "visual.curated", f"Curated review covers {len(curated)}/60 distinct included families.")
    for category in sorted(categories):
        need(len(curated_categories[category]) >= 5, "visual.curated_category",
             f"{category}: {len(curated_categories[category])}/5 curated families.")
    primary = {item["source_id"] for item in registry if item.get("primary")}
    for source in sorted(primary & source_families.keys()):
        required = min(10, len(source_families[source]))
        need(len(sampled[source]) >= required, "visual.source_sample",
             f"{source}: {len(sampled[source])}/{required} included families have source sample reviews.")

    # A clean publication recovery pack is separately attested. A collection
    # checkpoint containing quarantined files is not automatically a public pack.
    expected_members = set(manifest_files) | {item["preview"]["sha256"] for item in manifest_files.values()}
    restored = restore_evidence.get("artwork_member_sha256", restore_evidence.get("member_sha256"))
    restore_ok = (restore_evidence.get("publish_manifest_sha256") == manifest_sha
                  and restore_evidence.get("passed") is True and restore_evidence.get("published_subset_only") is True
                  and identity(restore_evidence.get("checked_by")) and nonempty(restore_evidence.get("checked_at"))
                  and nonempty(restore_evidence.get("bundle_url")) and sha(restore_evidence.get("bundle_sha256"))
                  and isinstance(restored, list) and set(restored) == expected_members)
    need(restore_ok, "restore.pending", "Provide a hash-bound successful restore of a clean publication pack containing exactly the selected original/preview hashes.")

    native_matches = []
    for asset in (native_evidence or {}).get("assets", []):
        match = asset.get("catalog_match") or {}
        original = (asset.get("files") or {}).get("source", {})
        history = asset.get("historical_validation") or {}
        value = match.get("source_svg_sha256")
        if (value in manifest_files and original.get("sha256") == value
                and original.get("original_bytes_rechecked") is True
                and history.get("status") == "validated_in_recorded_environment_only"
                and nonempty(history.get("illustrator_version")) and nonempty(history.get("mcp_server_version"))
                and any(p.get("source") == match.get("source") and p.get("source_id") == match.get("source_id")
                        for p in manifest_files[value]["provenance"])):
            native_matches.append({"sha256": value, "status": "historical_recorded_environment_only",
                                   "environment": history, "fresh_environment_verified": False})
    report = {
        "schema_version": 1, "collection_release": release, "required_bindings": required_bindings,
        "publish_manifest_sha256": manifest_sha, "publish_files": len(manifest_files),
        "excluded_file_hashes": sorted(excluded_hashes), "excluded_sources": sorted(excluded_sources),
        "valid_scope_decisions": valid_decisions, "unresolved_sources": unresolved_sources,
        "unresolved_files": bad_files,
        "retrieval": {"reviewed": judged, "successful": len(successes), "languages": dict(language_success),
                      "category_concepts": {key: len(covered[key]) for key in sorted(categories)},
                      "negative_scenarios_passed": negative_passes},
        "visual": {"curated_families": len(curated), "source_samples": {key: len(value) for key, value in sampled.items()}},
        "native_evidence": native_matches, "native_default": "not_verified",
        "restore_passed": restore_ok, "missing": missing, "acceptance_passed": not missing,
        "website_gate": "open_for_implementation" if not missing else "closed_pending_evidence",
        "note": "This evaluator checks evidence consistency; it does not perform visual or Illustrator review.",
    }
    return manifest, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("assembly-report", "catalog", "mapping", "judgments"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--benchmark", type=Path, default=ROOT / "data/benchmark.json")
    parser.add_argument("--registry", type=Path, default=ROOT / "data/sources.json")
    parser.add_argument("--decisions", type=Path)
    parser.add_argument("--review-summary", type=Path)
    parser.add_argument("--source-review", type=Path, action="append", default=[])
    parser.add_argument("--native-evidence", type=Path)
    parser.add_argument("--restore-evidence", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    hashes = {key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in (
        ("assembly_report_sha256", args.assembly_report), ("catalog_sha256", args.catalog),
        ("benchmark_sha256", args.benchmark), ("mapping_sha256", args.mapping))}
    decisions = read_json(args.decisions) if args.decisions else {}
    selections = {}
    for decision in decisions.get("decisions", []):
        reference = (decision.get("target") or {}).get("selection_manifest") or {}
        value = reference.get("local_path") or reference.get("uri")
        if not value or "://" in value or not args.decisions:
            continue
        path = args.decisions.parent / value
        if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == reference.get("sha256"):
            selections[reference["sha256"]] = read_json(path)
    catalog = [json.loads(line) for line in args.catalog.read_text(encoding="utf-8").splitlines() if line.strip()]
    registry = read_json(args.registry)
    manifest, report = evaluate_release(catalog, read_json(args.assembly_report), read_json(args.benchmark),
        read_json(args.mapping), read_json(args.judgments), hashes=hashes,
        registry=registry["sources"] if isinstance(registry, dict) else registry,
        decisions=decisions, selection_manifests=selections,
        source_reviews=[read_json(path) for path in args.source_review],
        native_evidence=read_json(args.native_evidence) if args.native_evidence else None,
        review_summary=read_json(args.review_summary) if args.review_summary else None,
        restore_evidence=read_json(args.restore_evidence) if args.restore_evidence else None)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "publish_manifest.json").write_bytes(json_bytes(manifest))
    (args.output / "checkreport.json").write_bytes(json_bytes(report))
    lines = ["# Release acceptance", "", f"Status: {report['website_gate']}", "",
             f"Eligible originals: {report['publish_files']}; retrieval: {report['retrieval']['successful']}/120.", "",
             "Missing evidence:" if report["missing"] else "All required evidence is present."]
    lines.extend(f"- [{item['code']}] {item['detail']}" for item in report["missing"])
    (args.output / "checkreport.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"website_gate": report["website_gate"], "publish_files": report["publish_files"],
                      "missing_count": len(report["missing"]), "output": str(args.output)}, ensure_ascii=False))
    return 0 if report["acceptance_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

