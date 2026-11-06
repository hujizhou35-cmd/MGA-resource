"""Apply actual file/preview-bound visual scientific observations, not guesses."""
from __future__ import annotations

from collections import defaultdict
import copy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re

FIELDS = {"object", "species", "state", "view", "role", "depiction_kind"}
UNKNOWN = {"", "unknown", "unspecified", "not_reviewed", "not_verified", "none"}


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _location(value):
    if not isinstance(value, dict):
        return None
    url, member = value.get("url"), value.get("member")
    if (not _text(url) or not url.startswith("https://") or not _text(member)
            or "\\" in member or ":" in member or member.startswith("/")
            or any(p in {"", ".", ".."} for p in member.split("/"))):
        return None
    return url, member


def _key(file):
    preview = file.get("preview") or {}
    return file.get("sha256"), preview.get("sha256")


def _binding(file):
    return (*_key(file), _location(file.get("archive")), _location((file.get("preview") or {}).get("archive")))


def _values(value):
    if isinstance(value, dict):
        value = value.get("values", value.get("value"))
    values = value if isinstance(value, list) else [value]
    return [v.strip() for v in values if _text(v) and v.strip().lower() not in UNKNOWN]


def _canonical(field, value):
    value = re.sub(r"[\s_-]+", " ", value.strip().lower())
    common = {"mus musculus": "mouse", "mice": "mouse", "rattus norvegicus": "rat", "rats": "rat",
              "homo sapiens": "human", "humans": "human"}
    if field in {"object", "species"}:
        return common.get(value, value)
    if field == "view":
        return {"side": "lateral", "side view": "lateral", "lateral view": "lateral"}.get(value, value)
    if field == "role":
        return {"whole animal": "whole body", "whole organism": "whole body"}.get(value, value)
    return value


def _source_values(record, file, field):
    values = []
    origins = file.get("source_records") or []
    contexts = [file, *origins]
    if not origins and len(record.get("variants") or []) <= 1:
        contexts.append(record)
    for context in contexts:
        scientific = context.get("scientific") or {}
        values.extend(_values(scientific.get(field)))
        values.extend(_values(context.get(field)))
    return sorted(set(values))


def _asset_by_url(release_assets):
    return {row.get("browser_download_url"): row for row in release_assets.values()}


def _validate_document(document, remotes):
    for key in ("reviewer", "reviewed_at", "review_method", "review_sheet_url"):
        if not _text(document.get(key)):
            return "Review document lacks " + key
    if document.get("schema_version") != 1 or not _sha(document.get("review_sheet_sha256")):
        return "Review schema or sheet SHA-256 is missing"
    try:
        datetime.fromisoformat(document["reviewed_at"])
    except ValueError:
        return "Review date must be an ISO date or datetime"
    remote = remotes.get(document["review_sheet_url"], {})
    if remote.get("digest") != "sha256:" + document["review_sheet_sha256"]:
        return "Review sheet URL/SHA-256 does not match an immutable Release asset"
    return None


def _validate_item(item, remotes):
    if not _sha(item.get("original_sha256")) or not _sha(item.get("preview_sha256")):
        return "Original/preview SHA-256 missing"
    for key, prefix in (("original_archive", "files/"), ("preview_archive", "previews/")):
        location = _location(item.get(key))
        if not location or not location[1].startswith(prefix):
            return "Invalid " + key
        remote = remotes.get(location[0], {})
        if not str(remote.get("digest", "")).startswith("sha256:") or not _sha(remote["digest"][7:]):
            return "Archive URL has no verified Release digest"
    assertions = item.get("scientific_assertions")
    if (not isinstance(assertions, dict) or not assertions or not set(assertions) <= FIELDS
            or any(not _values(v) for v in assertions.values())):
        return "Scientific assertions must use supported nonempty fields"
    if (item.get("visual_verdict") not in {"usable_as_schematic_component", "usable", "pass"}
            or not _text(item.get("observations")) or type(item.get("tile")) is not int or item["tile"] < 1):
        return "Assertion lacks an individually observed usable tile/verdict"
    return None


def apply_scientific_reviews(records, review_paths, release_assets):
    """Return a copied catalog and audit report. Never fetch or alter artwork.

    An exact reviewed archive binding must exist among the selected records.
    Only then can another alias of the identical original AND preview hashes
    reuse that observation. A different preview or family variant cannot.
    Conflicts preserve existing values and block that field via a named list.
    """
    enriched = copy.deepcopy(list(records))
    files = defaultdict(list)
    anchors = set()
    for record in enriched:
        for file in record.get("files", []):
            key = _key(file)
            if all(_sha(v) for v in key):
                files[key].append((record, file))
                anchors.add(_binding(file))
    remotes = _asset_by_url(release_assets)
    claims = defaultdict(list)
    report = {"schema_version": 1, "method": "exact_original_preview_and_review_archive_anchor",
              "review_documents": [], "assertion_items": 0, "matched_assertion_items": 0,
              "unmatched_assertion_items": 0, "invalid_assertion_items": 0,
              "applied_unique_files": 0, "applied_file_entries": 0, "conflicting_unique_files": 0,
              "conflicting_fields": 0, "issues": [], "artwork_modified": False,
              "overall_scientific_acceptance": "not_established_by_limited_visual_attributes"}
    for path in sorted(map(Path, review_paths)):
        raw = path.read_bytes()
        document = json.loads(raw)
        label = "data/visual-reviews/" + path.name
        document_digest = hashlib.sha256(raw).hexdigest()
        review_items = [item for item in document.get("items", []) if "scientific_assertions" in item]
        if not review_items:
            continue
        report["review_documents"].append({"path": label, "sha256": document_digest,
                                            "assertion_items": len(review_items)})
        for item in review_items:
            report["assertion_items"] += 1
            key = item.get("original_sha256"), item.get("preview_sha256")
            issue = {"review_document": label, "original_sha256": key[0], "tile": item.get("tile")}
            # One review report may cover multiple actual contact sheets. An
            # explicit per-item sheet binding must be verified independently.
            bound_document = {**document, **{k: item[k] for k in ("review_sheet_url", "review_sheet_sha256") if k in item}}
            error = _validate_document(bound_document, remotes) or _validate_item(item, remotes)
            if error:
                report["invalid_assertion_items"] += 1
                report["issues"].append({**issue, "status": "invalid", "reason": error})
                continue
            binding = (*key, _location(item["original_archive"]), _location(item["preview_archive"]))
            if binding not in anchors:
                report["unmatched_assertion_items"] += 1
                report["issues"].append({**issue, "status": "unmatched", "reason": "Exact original, preview and both archive bindings are absent"})
                continue
            report["matched_assertion_items"] += 1
            evidence = {"kind": "manual_visual_review", "original_sha256": key[0], "preview_sha256": key[1],
                "original_archive": item["original_archive"], "preview_archive": item["preview_archive"],
                "reviewer": document["reviewer"], "reviewed_at": document["reviewed_at"],
                "review_method": document["review_method"], "evidence_uri": bound_document["review_sheet_url"],
                "evidence_sha256": bound_document["review_sheet_sha256"], "tile": item["tile"],
                "observations": item["observations"], "review_limits": document.get("review_limits", []),
                "review_document": {"path": label, "sha256": document_digest},
                "assertions": copy.deepcopy(item["scientific_assertions"])}
            claims[key].append(evidence)
    for key, reviews in claims.items():
        applied_any, conflict_any = False, False
        # A conflict in any alias of identical bytes is retained for the whole
        # exact-file group, preventing dedup order from selecting a false winner.
        conflicts = {}
        all_fields = set().union(*(r["assertions"].keys() for r in reviews))
        for field in sorted(all_fields):
            source_values = sorted(set(v for record, file in files[key] for v in _source_values(record, file, field)))
            review_values = sorted(set(v for r in reviews for v in _values(r["assertions"].get(field))))
            existing = {_canonical(field, v) for v in source_values}
            proposed = {_canonical(field, v) for v in review_values}
            distinct_reviews = {tuple(sorted(_canonical(field, v) for v in _values(r["assertions"][field]))) for r in reviews if field in r["assertions"]}
            prior_conflict = any(field in file.get("scientific_conflicts", []) for _, file in files[key])
            if (existing and existing != proposed) or len(distinct_reviews) > 1 or prior_conflict:
                conflicts[field] = {"existing_values": source_values, "review_values": review_values,
                                    "status": "pending_independent_review"}
        if conflicts:
            conflict_any = True
            report["conflicting_fields"] += len(conflicts)
        for record, file in files[key]:
            accepted_any = False
            for review in reviews:
                accepted = {field: copy.deepcopy(value) for field, value in review["assertions"].items() if field not in conflicts}
                scientific = file.setdefault("scientific", {})
                for field, value in accepted.items():
                    # Keep explicit source values when they already agree.
                    if not _values(scientific.get(field)):
                        scientific[field] = _values(value)[0] if field == "depiction_kind" else _values(value)
                evidence = {**copy.deepcopy(review), "accepted_assertions": accepted,
                            "conflicts": copy.deepcopy(conflicts),
                            "status": "partial_conflict" if accepted and conflicts else "conflict" if conflicts else "applied",
                            "applies_to_archive": copy.deepcopy(file.get("archive")),
                            "applies_to_preview_archive": copy.deepcopy(file["preview"].get("archive"))}
                evidence["binding_scope"] = "exact_reviewed_archive" if _binding(file) == (*key, _location(review["original_archive"]), _location(review["preview_archive"])) else "identical_original_and_preview_sha_with_observed_archive_anchor"
                stored = file.setdefault("scientific_review_evidence", [])
                if evidence not in stored:
                    stored.append(evidence)
                accepted_any = accepted_any or bool(accepted)
            if conflicts:
                file["scientific_conflicts"] = sorted(set(file.get("scientific_conflicts", [])) | set(conflicts))
            if accepted_any:
                report["applied_file_entries"] += 1
                applied_any = True
        report["applied_unique_files"] += int(applied_any)
        report["conflicting_unique_files"] += int(conflict_any)
    return enriched, report


def finalized_scientific_review_counts(records):
    """Report evidence actually surviving exact-file dedup, without reapplying."""
    files = [file for record in records for file in record.get("files", [])]
    return {"files_with_review_evidence": sum(bool(f.get("scientific_review_evidence")) for f in files),
            "files_with_accepted_assertions": sum(any(e.get("accepted_assertions") for e in f.get("scientific_review_evidence", [])) for f in files),
            "files_with_conflicts": sum(bool(f.get("scientific_conflicts")) for f in files),
            "unique_reviewed_originals": len({f["sha256"] for f in files if f.get("scientific_review_evidence")})}
