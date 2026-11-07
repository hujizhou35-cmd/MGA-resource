"""Audit exact-file scope across frozen metadata; never download artwork.

Missing downstream artifacts remain pending. This audit does not replace source,
scientific relevance, license, native-editability, or website acceptance.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from mga_resources.scope_policy import load_scope_policy, evaluate_public_scope
from build_publish_pack import validate_index, validate_remote_restore
from restore_equivalence import compare_manifests, validate_restore_equivalence


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def proof(path):
    path = Path(path)
    with path.open("rb") as stream:
        sha = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"path": str(path.resolve()), "sha256": sha, "size_bytes": path.stat().st_size}


def records(path):
    with Path(path).open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def audit(catalog, assembly, *, catalog_sha256, policy, internal=None,
          internal_sha256=None, index=None, manifest=None, manifest_sha256=None,
          bundle=None, bundle_sha256=None, restore=None, paired_review=None,
          restore_equivalence=None, restore_equivalence_sha256=None,
          restored_manifest=None, restored_manifest_sha256=None, restore_sha256=None):
    checks = []

    def check(name, passed, detail):
        checks.append({"check": name, "status": "pending" if passed is None else "passed" if passed else "failed", "detail": detail})

    public = defaultdict(list)
    stale = []
    forbidden = []
    dangling = []
    for record in catalog:
        family_hashes = {f.get("sha256") for f in record.get("files", [])}
        if record.get("catalog_visibility") == "internal_diagnostic":
            forbidden.append({"asset_id": record.get("id"), "reason": "internal record marker in public catalog"})
        for variant in record.get("variants", []):
            for file in variant.get("files", []):
                if file.get("sha256") not in family_hashes:
                    dangling.append({"asset_id": record.get("id"), "variant_id": variant.get("id"), "sha256": file.get("sha256")})
        for file in record.get("files", []):
            public[file.get("sha256")].append(file)
            scope = evaluate_public_scope(record, file, policy)
            if not scope["public_allowed"]:
                forbidden.append({"sha256": file.get("sha256"), "scope": scope})
            if (file.get("public_scope") or {}).get("policy_sha256") != policy["sha256"]:
                stale.append(file.get("sha256"))
            for origin in file.get("source_records", []):
                if (origin.get("public_scope") or {}).get("public_allowed") is False or origin.get("catalog_visibility") == "internal_diagnostic":
                    forbidden.append({"sha256": file.get("sha256"), "reason": "explicitly forbidden source context"})
    check("catalog.hash_binding", assembly.get("summary", {}).get("catalog_sha256") == catalog_sha256,
          "Actual public catalog bytes must match the assembly receipt.")
    check("catalog.current_policy", assembly.get("public_scope", {}).get("policy_sha256") == policy["sha256"] and not stale,
          {"current_policy_sha256": policy["sha256"], "stale_file_count": len(stale)})
    check("catalog.no_forbidden_files", not forbidden, forbidden)
    check("catalog.no_dangling_variant_files", not dangling, dangling)

    dispositions = []
    expected = set()
    component_pairs = {"excluded_nameplates": [], "retained_reviewed_reactome_components": []}
    if internal is None:
        check("internal.binding_and_exact_public_set", None, "Supply the assembly-bound internal-catalog.jsonl; no acquisition/exclusion totals inferred from the public subset.")
    else:
        binding = assembly.get("public_scope", {}).get("internal_catalog") or {}
        bound = binding.get("sha256") == internal_sha256 and binding.get("records") == len(internal)
        bound = bound and all(r.get("catalog_visibility") == "internal_diagnostic" for r in internal)
        for record in internal:
            for file in record.get("files", []):
                scope = evaluate_public_scope(record, file, policy)
                if scope["public_allowed"]:
                    expected.add(file.get("sha256"))
                else:
                    dispositions.append({"source": record.get("source"), "source_id": record.get("source_id"),
                        "title": record.get("title"), "sha256": file.get("sha256"),
                        "preview_sha256": (file.get("preview") or {}).get("sha256"),
                        "status": scope["status"], "dimension": scope.get("scope_dimension"),
                        "reason_code": scope.get("content_scope_reason") or scope.get("scope_dimension") or "unspecified",
                        "reason": scope.get("reason"), "decision_id": scope.get("decision_id")})
                group = ("excluded_nameplates" if scope.get("content_scope_reason") == "text_only_molecular_nameplate" and not scope["public_allowed"]
                         else "retained_reviewed_reactome_components" if record.get("source") == "reactome" and scope.get("status") == "included" else None)
                if group and len(component_pairs[group]) < 6:
                    component_pairs[group].append({"source_id": record.get("source_id"), "title": record.get("title"),
                        "sha256": file.get("sha256"), "expected_public": scope["public_allowed"],
                        "in_public_catalog": file.get("sha256") in public, "decision_id": scope.get("decision_id"),
                        "reason": scope.get("reason")})
        check("internal.binding_and_exact_public_set", bound and expected == set(public),
              {"internal_binding_valid": bound, "missing_allowed_originals": sorted(expected-set(public)),
               "unexpected_public_originals": sorted(set(public)-expected)})

    index_hashes = None
    if index is None:
        check("index.exact_public_file_set", None, "No frozen SQLite supplied; search exclusion has not been established by this audit.")
    else:
        with sqlite3.connect(Path(index).resolve().as_uri()+"?mode=ro", uri=True) as db:
            index_meta = dict(db.execute("SELECT key,value FROM index_metadata"))
            index_hashes = {r[0] for r in db.execute("SELECT DISTINCT file_sha256 FROM file_variants")}
            legacy_hashes = {f.get("sha256") for (value,) in db.execute("SELECT metadata FROM assets") for f in json.loads(value).get("files", [])}
            check("index.exact_public_file_set", index_meta.get("catalog_version") == catalog_sha256 and index_hashes == set(public) and legacy_hashes == set(public),
                  {"catalog_version": index_meta.get("catalog_version"), "index_version": db.execute("PRAGMA user_version").fetchone()[0],
                   "unique_variant_files": len(index_hashes), "legacy_unique_files": len(legacy_hashes),
                   "unexpected_variant_files": sorted(index_hashes-set(public)), "missing_variant_files": sorted(set(public)-index_hashes)})

    manifest_files = None
    if manifest is None:
        check("download_manifest.allowed_exact_variants", None, "No official download manifest supplied.")
    else:
        manifest_files = {f.get("sha256"): f for f in manifest.get("files", [])}
        errors = []
        for sha, file in manifest_files.items():
            if not evaluate_public_scope({}, file, policy)["public_allowed"]:
                errors.append({"sha256": sha, "reason": "excluded or pending"})
            if not any((f.get("preview") or {}).get("sha256") == (file.get("preview") or {}).get("sha256") for f in public.get(sha, [])):
                errors.append({"sha256": sha, "reason": "original/preview pair is absent from public catalog"})
        check("download_manifest.allowed_exact_variants", not errors and len(manifest_files) == len(manifest.get("files", [])) and manifest.get("catalog_sha256") == catalog_sha256, errors)
    if bundle is None or manifest is None:
        check("publish_bundle.exact_manifest_members", None, "Supply both clean bundle index and publication manifest. Raw collection archives are not clean publication packs.")
    else:
        try:
            if restore_equivalence is not None:
                compare_manifests(manifest, restored_manifest)
                validate_index(bundle, restored_manifest, bundle.get("repository"))
            else:
                validate_index(bundle, manifest, bundle.get("repository"))
            check("publish_bundle.exact_manifest_members", True, {"packs": len(bundle["packs"]), "members": bundle["artwork_object_count"]})
        except (ValueError, KeyError, TypeError) as error:
            check("publish_bundle.exact_manifest_members", False, str(error))
    restore_proof_type = "pending"
    if restore_equivalence is not None:
        try:
            from build_publish_pack import json_bytes
            if restore_equivalence_sha256 != hashlib.sha256(json_bytes(restore_equivalence)).hexdigest():
                raise ValueError("Equivalence evidence hash/canonical bytes are missing or mismatched")
            validate_restore_equivalence(restore_equivalence, manifest, restored_manifest, bundle, restore,
                new_manifest_sha256=manifest_sha256, restored_manifest_sha256=restored_manifest_sha256,
                bundle_sha256=bundle_sha256, restore_evidence_sha256=restore_sha256)
            restore_proof_type = "equivalence_non_fresh_artwork_read"
            check("publish_bundle.restore_equivalence", True,
                  {"proof_type": restore_proof_type, "metadata_run": restore_equivalence["metadata_run"],
                   "historical_build_run": restore["build_run"], "historical_restore_run": restore["restore_run"],
                   "current_pack_asset_metadata_checks": len(restore_equivalence["remote_pack_asset_checks"]),
                   "new_artwork_archive_reads": 0})
        except (ValueError, KeyError, TypeError) as error:
            check("publish_bundle.restore_equivalence", False, str(error))
    elif restore is None or bundle is None or manifest is None:
        check("publish_bundle.independent_restore", None, "Cloud restore receipt still required; a written rule or bundle plan is not a successful restore.")
    else:
        try:
            validate_remote_restore(restore, bundle, manifest,
                                    manifest_sha256=manifest_sha256, bundle_sha256=bundle_sha256)
            restore_proof_type = "independent_remote_restore"
            check("publish_bundle.independent_restore", True,
                  {"member_count": restore["artwork_member_count"], "build_run": restore["build_run"],
                   "restore_run": restore["restore_run"], "checked_at": restore["checked_at"]})
        except (ValueError, KeyError, TypeError) as error:
            legacy_or_build = not isinstance(restore, dict) or restore.get("schema_version") != 2 or restore.get("operation") != "independent_remote_restore"
            check("publish_bundle.independent_restore", None if legacy_or_build else False, str(error))

    paired = []
    for item in (paired_review or {}).get("items", []):
        sha = item["original_sha256"]
        wanted = item.get("scope_status") == "in_scope"
        row = {"tile": item.get("tile"), "title": item.get("title"), "sha256": sha, "expected_public": wanted,
               "catalog": sha in public, "index": None if index_hashes is None else sha in index_hashes,
               "download_manifest": None if manifest_files is None else sha in manifest_files}
        if wanted:
            row["expected_text_status"] = (item.get("text_content") or {}).get("status", "unknown")
            row["catalog_text_status"] = sorted({(file.get("text_content") or {}).get("status", "unknown") for file in public.get(sha, [])})
            row["text_attribute_matches"] = row["catalog_text_status"] == [row["expected_text_status"]]
        row["supplied_stages_match"] = all(value == wanted for key,value in row.items() if key in {"catalog", "index", "download_manifest"} and value is not None)
        paired.append(row)
    if paired_review:
        check("paired_commons.keep_01_to_04_exclude_05_06", len(paired)==6 and all(r["supplied_stages_match"] for r in paired), paired)
        check("paired_commons.observed_text_preserved", all(r.get("text_attribute_matches", True) for r in paired),
              [{k:r[k] for k in ("tile", "expected_text_status", "catalog_text_status", "text_attribute_matches")} for r in paired if r["expected_public"]])

    by_status = defaultdict(set)
    by_reason = defaultdict(set)
    by_source = defaultdict(lambda: defaultdict(set))
    for row in dispositions:
        by_status[row["status"]].add(row["sha256"])
        by_reason[row["reason_code"]].add(row["sha256"])
        by_source[row["source"]][row["status"]].add(row["sha256"])
    text_counts = Counter()
    for files in public.values():
        states = {(f.get("text_content") or {}).get("status", "unknown") for f in files}
        text_counts[next(iter(states)) if len(states)==1 else "conflict"] += 1
    failed = [c["check"] for c in checks if c["status"] == "failed"]
    pending = [c["check"] for c in checks if c["status"] == "pending"]
    return {"schema_version": 1, "audit_scope": "Exact file scope only; not full collection, scientific, skill-cleanup or deployment acceptance.",
        "checked_at": datetime.now(timezone.utc).isoformat(), "catalog_sha256": catalog_sha256,
        "policy_sha256": policy["sha256"], "scope_end_to_end_passed": not failed and not pending,
        "restore_proof_type": restore_proof_type,
        "restore_equivalence_sha256": restore_equivalence_sha256,
        "full_artwork_restore_proof_directly_binds_current_manifest": restore_proof_type == "independent_remote_restore",
        "failed_checks": failed, "pending_checks": pending, "checks": checks,
        "counts": {"public_families": len(catalog), "public_unique_originals": len(public),
            "nonpublic_unique_originals": len({r["sha256"] for r in dispositions}) if internal is not None else None,
            "nonpublic_by_status": {k:len(v) for k,v in by_status.items()},
            "nonpublic_by_reason": {k:len(v) for k,v in by_reason.items()},
            "nonpublic_by_source": {s:{k:len(v) for k,v in states.items()} for s,states in by_source.items()},
            "public_text_attribute_counts": dict(text_counts)},
        "count_note": "Counts use original SHA-256, not titles or merged families. A hash with different reviewed previews may occur in more than one status; status totals are not always additive.",
        "dispositions": dispositions, "paired_commons": paired, "reviewed_component_examples": component_pairs,
        "skill_cleanup": {"normative_requirements": "recorded separately in MGA-Designer selected-asset-adaptation.md", "bulk_cleanup_run": False, "automatic_cleanup_capability_tested": False}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--assembly-report", type=Path, required=True)
    parser.add_argument("--internal-catalog", type=Path)
    parser.add_argument("--index", type=Path)
    parser.add_argument("--publish-manifest", type=Path)
    parser.add_argument("--bundle-index", type=Path)
    parser.add_argument("--restore-evidence", type=Path)
    parser.add_argument("--restore-equivalence", type=Path)
    parser.add_argument("--restored-manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.restore_equivalence and not all((args.restored_manifest, args.bundle_index, args.restore_evidence, args.publish_manifest)):
        parser.error("Equivalence requires new/old manifests, historical bundle and historical restore evidence")
    if args.restored_manifest and not args.restore_equivalence:
        parser.error("--restored-manifest requires explicit --restore-equivalence")
    inputs = {key:proof(path) for key,path in vars(args).items() if path and key != "output"}
    report = audit(records(args.catalog), read_json(args.assembly_report),
        catalog_sha256=inputs["catalog"]["sha256"], policy=load_scope_policy(),
        internal=records(args.internal_catalog) if args.internal_catalog else None,
        internal_sha256=inputs.get("internal_catalog", {}).get("sha256"), index=args.index,
        manifest=read_json(args.publish_manifest) if args.publish_manifest else None,
        manifest_sha256=inputs.get("publish_manifest", {}).get("sha256"),
        bundle=read_json(args.bundle_index) if args.bundle_index else None,
        bundle_sha256=inputs.get("bundle_index", {}).get("sha256"),
        restore=read_json(args.restore_evidence) if args.restore_evidence else None,
        restore_equivalence=read_json(args.restore_equivalence) if args.restore_equivalence else None,
        restore_equivalence_sha256=inputs.get("restore_equivalence", {}).get("sha256"),
        restored_manifest=read_json(args.restored_manifest) if args.restored_manifest else None,
        restored_manifest_sha256=inputs.get("restored_manifest", {}).get("sha256"),
        restore_sha256=inputs.get("restore_evidence", {}).get("sha256"),
        paired_review=read_json(ROOT/"data/visual-reviews/commons-42260711756-component-scope.json"))
    report["input_proofs"] = inputs
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({k:report[k] for k in ("scope_end_to_end_passed", "failed_checks", "pending_checks", "counts")}, ensure_ascii=False))
    return 1 if report["failed_checks"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
