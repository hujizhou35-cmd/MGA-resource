#!/usr/bin/env python3
"""Bind actual curated observations to unchanged files; never infer visual approval."""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def audit(review: dict, manifest: dict, documents: dict[str, tuple[str, dict]]) -> dict:
    """`documents` supplies actual bytes' hashes and parsed exact review documents."""
    errors = []
    counts = defaultdict(set)
    families, originals, candidate_ids = set(), set(), set()
    files = {row["sha256"]: row for row in manifest["files"]}
    candidates = review.get("mapping_candidates", {})
    if review.get("catalog_binding", {}).get("sha256") != manifest.get("catalog_sha256"):
        errors.append("catalog_binding_mismatch")
    if review.get("independent_from_metadata_matcher") is not True or not review.get("reviewer"):
        errors.append("identified_independent_review_missing")
    for row in review.get("curated", []):
        key = row.get("candidate_id")
        prefix = str(key) + ": "
        candidate = candidates.get(key, {})
        f = files.get(row.get("original_sha256"))
        if not f:
            errors.append(prefix + "original_not_in_manifest")
            continue
        expected = {
            "original_sha256": f["sha256"],
            "original_archive": f["archive"],
            "preview_sha256": f["preview"]["sha256"],
            "preview_archive": f["preview"]["archive"],
            "structure": f["structure"],
            "format": f["format"],
        }
        for field, value in expected.items():
            if row.get(field) != value:
                errors.append(prefix + field + "_mismatch")
        if (candidate.get("candidate_id") != key or candidate.get("original_sha256") != f["sha256"]
                or candidate.get("original_archive") != f["archive"]
                or candidate.get("preview", {}).get("sha256") != f["preview"]["sha256"]
                or candidate.get("preview", {}).get("archive") != f["preview"]["archive"]
                or candidate.get("asset_id") not in f["asset_ids"]
                or candidate.get("family_id") not in f["family_ids"]
                or candidate.get("family_id") != row.get("family_id")
                or candidate.get("asset_id") != row.get("asset_id")
                or candidate.get("type") != f["structure"]
                or candidate.get("preview_status") != "viewable"):
            errors.append(prefix + "mapping_candidate_mismatch")
        if key in candidate_ids or f["sha256"] in originals or row["family_id"] in families:
            errors.append(prefix + "duplicate_selected_family_or_original")
        if (row.get("usable") is not True or row.get("source_evidence_checked") is not True
                or not row.get("visual_evidence_notes") or not row.get("recommended_use")
                or not row.get("limitations") or not row.get("category_evidence", {}).get("observed")):
            errors.append(prefix + "scoped_observation_or_limit_missing")
        source_records = row.get("source_records", [])
        if not source_records or any(source not in f.get("provenance", []) for source in source_records):
            errors.append(prefix + "file_level_source_record_mismatch")
        binding = row.get("exact_prior_review", {})
        doc_hash, doc = documents.get(binding.get("path"), (None, {}))
        try:
            pointer = binding["json_pointer"].split("/")
            assert pointer[:2] == ["", "items"] and len(pointer) == 3
            evidence = doc["items"][int(pointer[2])]
        except (KeyError, ValueError, IndexError, AssertionError):
            evidence = {}
        if (doc_hash != binding.get("sha256") or not evidence
                or any(evidence.get(field) != value for field, value in expected.items()
                       if field not in {"structure", "format"})
                or evidence.get("observations") != binding.get("observations")
                or not str(evidence.get("visual_verdict", "")).startswith("usable")
                or not any(source.get("source_id") == evidence.get("source_id")
                           and source.get("source_url") == evidence.get("source_url") for source in source_records)):
            errors.append(prefix + "exact_prior_review_mismatch")
        sheet = row.get("viewed_sheet", {})
        if (sheet.get("uri") != doc.get("review_sheet_url")
                or sheet.get("sha256") != doc.get("review_sheet_sha256")
                or sheet.get("tile") != evidence.get("tile")):
            errors.append(prefix + "viewed_sheet_mismatch")
        families.add(row["family_id"])
        originals.add(f["sha256"])
        candidate_ids.add(key)
        for category in row.get("categories", []):
            counts[category].add(row["family_id"])
    for category, value in review.get("category_counts", {}).items():
        if value != len(counts[category]):
            errors.append(category + ": recorded_count_mismatch")
    if review.get("distinct_families") != len(families):
        errors.append("distinct_families_count_mismatch")
    complete = len(families) >= 60 and len(review.get("category_counts", {})) == 12 and all(
        len(counts[c]) >= 5 for c in review.get("category_counts", {}))
    return {
        "binding_passed": not errors,
        "errors": errors,
        "distinct_originals": len(originals),
        "distinct_families": len(families),
        "category_counts": {c: len(counts[c]) for c in review.get("category_counts", {})},
        "missing_to_five": {c: 5-len(counts[c]) for c in review.get("category_counts", {}) if len(counts[c]) < 5},
        "full_curated_coverage_requirement_passed": not errors and complete,
        "full_release_acceptance_claimed": False,
        "note": "Binding checks validate recorded evidence only. They do not manufacture visual review, native editing, overlay/composition or scientific accuracy acceptance.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    review, manifest = read(args.review), read(args.manifest)
    documents = {}
    for row in review.get("curated", []):
        path = row["exact_prior_review"]["path"]
        if path not in documents:
            documents[path] = (digest(args.root/path), read(args.root/path))
    report = audit(review, manifest, documents)
    if digest(args.manifest) != review.get("manifest_binding", {}).get("sha256"):
        report["errors"].append("manifest_byte_hash_mismatch")
    catalog = args.root/review["catalog_binding"]["path"]
    if digest(catalog) != manifest.get("catalog_sha256"):
        report["errors"].append("catalog_byte_hash_mismatch")
    for sheet in review.get("reviewed_sheets", []):
        if digest(args.root/sheet["local_path"]) != sheet["sha256"]:
            report["errors"].append(sheet["local_path"]+": viewed_sheet_byte_hash_mismatch")
    report["binding_passed"] = not report["errors"]
    report["full_curated_coverage_requirement_passed"] &= report["binding_passed"]
    report.update({"review_sha256": digest(args.review), "manifest_sha256": digest(args.manifest),
                   "catalog_sha256": manifest["catalog_sha256"]})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8", newline="\n")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["binding_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
