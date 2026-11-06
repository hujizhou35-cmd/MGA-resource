"""Attach the five published legacy native tests to exact SVG bytes only.

This is an evidence join, not an Illustrator test or the media-processing
pipeline. Original catalog files, formats and machine-validation states stay
unchanged. Consumers must keep the environment and importer limitations.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable
from urllib.parse import urlsplit

PROVIDER = "mga-legacy-illustrator-five"
COMPONENTS = {"cell_culture_dish", "human_liver", "bioreactor", "blood_syringe", "dna_double_helix"}
SEMANTIC_ALGORITHM = "sha256:legacy-evidence-v1:json-sort-keys-compact-utf8"


def _read(path):
    raw = Path(path).read_bytes()
    return json.loads(raw), raw


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _digest(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("A complete SHA-256 is required for native evidence")
    return value


def _semantic_projection(document):
    """Compare all item evidence, ignoring only explicit packaging additions.

The published manifest embeds the original five assets and evidence boundary.
JSON whitespace/newlines, collection timestamps, total-size summaries and the
package's own purpose do not alter those facts. Packaging adds archive paths,
license-file hashes and upload state; these are validated separately.
"""
    assets = copy.deepcopy(document["assets"])
    for asset in assets:
        asset.pop("license_notice_file", None)
        for file in asset["files"].values():
            for field in ("archive_member", "integrity_rechecked_for_this_package", "cloud_upload_status"):
                file.pop(field, None)
    boundary = copy.deepcopy(document["evidence_boundary"])
    boundary.pop("no_artwork_copied_or_uploaded", None)
    return {"schema_version": document["schema_version"], "evidence_boundary": boundary,
            "assets": sorted(assets, key=lambda item: item["name"])}


def _canonical_sha(document):
    return _sha(json.dumps(_semantic_projection(document), ensure_ascii=False,
                           sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _release_location(item, suffix):
    parsed = urlsplit(item.get("url", ""))
    if (parsed.scheme != "https" or parsed.hostname != "github.com"
            or parsed.query or parsed.fragment or not parsed.path.endswith(suffix)
            or not re.fullmatch(r"/[^/]+/[^/]+/releases/download/[^/]+/[^/]+", parsed.path)):
        raise ValueError("Native downloads must name a public GitHub Release asset")
    _digest(item.get("sha256"))
    if type(item.get("size_bytes")) is not int or item["size_bytes"] <= 0:
        raise ValueError("Native publication asset size is required")


def load_native_evidence(evidence_path, publication_path, manifest_path):
    """Validate small public metadata files; no remote or artwork I/O occurs."""
    evidence, evidence_raw = _read(evidence_path)
    publication, _ = _read(publication_path)
    manifest, manifest_raw = _read(manifest_path)
    for document in (evidence, publication, manifest):
        if document.get("schema_version") != 1:
            raise ValueError("Unsupported native-evidence metadata version")
    _release_location(publication["archive"], ".tar.gz")
    _release_location(publication["manifest"], ".manifest.json")
    if (_sha(manifest_raw) != publication["manifest"]["sha256"]
            or len(manifest_raw) != publication["manifest"]["size_bytes"]):
        raise ValueError("Native manifest does not match the published byte digest")
    if urlsplit(publication["archive"]["url"]).path.rsplit("/", 1)[0] != urlsplit(publication["manifest"]["url"]).path.rsplit("/", 1)[0]:
        raise ValueError("Native archive and manifest must belong to the same Release")
    verification = publication.get("verification", {})
    if not all(verification.get(key) is True for key in (
        "original_sha256_rechecked", "archive_members_verified", "github_remote_digests_match")):
        raise ValueError("Native publication lacks recorded integrity verification")
    semantic_sha = _canonical_sha(evidence)
    if semantic_sha != _canonical_sha(manifest):
        raise ValueError("Native manifest and historical evidence disagree semantically")
    if (len(evidence["assets"]) != 5 or {item["name"] for item in evidence["assets"]} != COMPONENTS
            or len(manifest["assets"]) != 5):
        raise ValueError("This evidence join is limited to the five existing components")
    boundary = manifest["evidence_boundary"]
    if boundary.get("stock_importer_not_covered") is not True or boundary.get("cross_document_is_not_cross_environment") is not True:
        raise ValueError("Historical environment limitations must remain explicit")
    binding = {
        "evidence_raw_sha256": _sha(evidence_raw),
        "manifest_recorded_evidence_raw_sha256": _digest(manifest.get("verification_metadata_sha256")),
        "raw_digest_matches": _sha(evidence_raw) == manifest["verification_metadata_sha256"],
        "semantic_sha256": semantic_sha, "semantic_digest_algorithm": SEMANTIC_ALGORITHM,
        "semantic_evidence_matches_published_manifest": True,
        "published_manifest": copy.deepcopy(publication["manifest"]),
        "published_archive": copy.deepcopy(publication["archive"]),
        "integrity_verification_basis": "recorded publication verification; no remote re-download in this join",
    }
    by_sha = {}
    for asset in manifest["assets"]:
        name = asset["name"]
        source, native = asset["files"]["source"], asset["files"]["native_ai"]
        history = asset["historical_validation"]
        if (history.get("illustrator_version") != "27.6.0" or history.get("mcp_server_version") != "1.10.3"
                or history.get("importer_variant") != "isolated patched copy"
                or history.get("other_environments_verified") is not False):
            raise ValueError("Legacy Illustrator/MCP/patch scope differs from the recorded evidence")
        for role, file, filename, fmt in (("source", source, "source.svg", "svg"),
                                          ("native_ai", native, "native.ai", "ai")):
            _digest(file.get("sha256"))
            expected_member = f"components/{name}/{filename}"
            if file.get("format") != fmt or file.get("archive_member") != expected_member:
                raise ValueError(f"Unexpected published {role} archive member")
            if type(file.get("size_bytes")) is not int or file["size_bytes"] <= 0:
                raise ValueError("Historical original file size is missing")
        digest = source["sha256"]
        if asset["catalog_match"].get("source_svg_sha256") != digest or digest in by_sha:
            raise ValueError("Ambiguous historical source SVG hash")
        license_file = asset.get("license_notice_file", {})
        if license_file.get("archive_member") != f"components/{name}/LICENSE.md":
            raise ValueError("Native license notice location is missing")
        _digest(license_file.get("sha256"))
        archive = {**copy.deepcopy(publication["archive"]), "member": native["archive_member"]}
        historical = {
            "evidence_provider": PROVIDER, "legacy_component": name,
            "match_method": "exact_original_svg_sha256_and_recorded_size",
            "applies_to_original_svg_sha256": digest, "native_ai_sha256": native["sha256"],
            **copy.deepcopy(history), "evidence_boundary": copy.deepcopy(boundary),
            "evidence_binding": copy.deepcopy(binding),
            "current_native_test_performed": False, "family_variants_covered": False,
        }
        native_variant = {
            "id": f"{PROVIDER}:{native['sha256']}", "evidence_provider": PROVIDER,
            "role": "historical_native_ai_derivative", "format": "ai",
            "sha256": native["sha256"], "size_bytes": native["size_bytes"], "archive": archive,
            "derived_from_original_svg_sha256": digest,
            "source_url": asset["source_url"], "author": asset.get("author"),
            "license_statement": asset.get("license_statement"), "license_evidence": asset.get("license_evidence", []),
            "attribution": asset.get("attribution"), "rights_basis": asset.get("rights_basis"),
            "license_notice": {**copy.deepcopy(license_file), "archive_url": publication["archive"]["url"]},
            "modifications": native.get("modifications"),
            "historical_native_validation": copy.deepcopy(historical),
            "validation": {"status": "not_processed_by_collection_media_pipeline",
                           "illustrator_editability": "historical_recorded_environment_only",
                           "current_native_test_performed": False,
                           "scientific_review": "not_reviewed", "visual_review": "not_reviewed"},
        }
        # File-level evidence carries the download too, so a later family merge
        # cannot lose it by selecting another record's top-level metadata.
        historical["native_ai_download"] = {"format": "ai", "sha256": native["sha256"],
                                             "size_bytes": native["size_bytes"], "archive": archive}
        by_sha[digest] = {"source_size": source["size_bytes"], "historical": historical,
                         "native_variant": native_variant}
    return by_sha, binding


def attach_native_evidence(records: Iterable[dict[str, Any]], evidence_path, publication_path, manifest_path):
    """Return deep-copied catalog records and a precise, non-acceptance report.

    Only exact SVG hash AND recorded byte-size matches receive file-level
    historical_native_validation. AI metadata lives in native_variants, never
    in files/formats or the collection's ordinary machine-validation counts.
    """
    evidence, binding = load_native_evidence(evidence_path, publication_path, manifest_path)
    output, matched, inconsistent, record_count = [], set(), [], 0
    for original in records:
        record = copy.deepcopy(original)
        variants = [item for item in record.get("native_variants", []) if item.get("evidence_provider") != PROVIDER]
        record_matches = set()
        for file in record.get("files", []):
            old = file.get("historical_native_validation")
            if isinstance(old, dict) and old.get("evidence_provider") == PROVIDER:
                file.pop("historical_native_validation")
            digest = file.get("sha256")
            if digest not in evidence or file.get("format") != "svg":
                continue
            entry = evidence[digest]
            if file.get("size_bytes") != entry["source_size"]:
                inconsistent.append({"record_id": record.get("id"), "sha256": digest,
                                     "reason": "Catalog byte size differs from the hash-bound legacy source"})
                continue
            file["historical_native_validation"] = copy.deepcopy(entry["historical"])
            if digest not in record_matches:
                variants.append(copy.deepcopy(entry["native_variant"]))
            record_matches.add(digest)
            matched.add(digest)
        if variants:
            record["native_variants"] = variants
        else:
            record.pop("native_variants", None)
        record_count += bool(record_matches)
        output.append(record)
    report = {"schema_version": 1, "evidence_provider": PROVIDER, "available_legacy_sources": len(evidence),
              "matched_original_svg_hashes": sorted(matched), "matched_unique_originals": len(matched),
              "records_with_exact_legacy_evidence": record_count,
              "unmatched_legacy_source_hashes": sorted(evidence.keys() - matched), "inconsistent_catalog_files": inconsistent,
              "evidence_binding": binding, "new_native_tests_performed": False,
              "whole_catalog_native_verified": False, "ordinary_media_validation_changed": False}
    return output, report
