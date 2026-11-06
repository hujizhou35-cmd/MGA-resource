"""One public-scope consumer for existing exact-file visual-review decisions.

No title blacklist. Unknown attributes are not a rejection. Original collection
records remain internal evidence; public catalogs and download manifests consume
the same explicit, file-bound exclusions and pending-review decisions.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import copy
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
POLICY_VERSION = "medical-illustration-components-20261107-v1"


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _digest(value):
    return hashlib.sha256((json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()).hexdigest()


def _action(item):
    if item.get("disposition") == "exclude_from_v1":
        return "excluded"
    if (item.get("scope_status") in {"scope_pending", "pending_review"}
            or item.get("visual_verdict") == "scope_pending"):
        return "pending_review"
    if item.get("disposition") == "quarantine":
        return "excluded"
    if item.get("scope_status") in {"in_scope", "included", "visually_illustration"}:
        return "included"
    return None


def _dimension(item):
    if item.get("scope_status") in {"visually_illustration", "out_of_v1_visual_media_scope", "scope_pending"}:
        return "media_type"
    if item.get("scope_status") == "preview_layout_needs_review":
        return "preview_integrity"
    return "component_suitability"


def load_scope_policy(review_paths=None, release_assets=None):
    """Consume the same item/disposition contract as --file-review.

    When GitHub asset metadata is available, at least one exact cloud review
    evidence digest must match. Collector prefetch uses committed review bytes;
    assembly additionally verifies the referenced remote review asset digest.
    """
    if review_paths is None:
        paths = list((ROOT / "data/visual-reviews").glob("*.json"))
        registry = ROOT / "data/sources.json"
        if registry.is_file():
            config = json.loads(registry.read_text(encoding="utf-8"))
            for relative in config.get("public_scope_review_paths", []):
                path = (ROOT / relative).resolve()
                if not path.is_relative_to((ROOT / "data").resolve()):
                    raise ValueError("Scope review must be inside project data")
                paths.append(path)
        paths = sorted(set(paths))
    else:
        paths = sorted(review_paths)
    remote = {r.get("browser_download_url"): r for r in (release_assets or {}).values()}
    documents, rules = [], []
    for path in paths:
        path = Path(path)
        data = path.read_bytes()
        document = json.loads(data)
        selected = [item for item in document.get("items", []) if _action(item)]
        if not selected:
            continue
        if b"\r\n" in data:
            raise ValueError(f"Scope review must use repository LF bytes before hashing: {path}. Write with newline='\\n'; Git/cloud LF and Windows CRLF hashes differ.")
        uri = path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.name
        proof = {"uri": uri, "sha256": hashlib.sha256(data).hexdigest()}
        documents.append(proof)
        for item in selected:
            source = item.get("source") or document.get("source")
            sid = item.get("source_id")
            original = item.get("original_sha256") or item.get("sha256")
            preview = item.get("preview_sha256")
            reason = item.get("reason") or item.get("observations")
            reviewer = item.get("reviewed_by") or document.get("reviewer")
            date = item.get("reviewed_at") or document.get("reviewed_at")
            evidence = list(item.get("evidence") or [])
            sheet_url = item.get("review_sheet_url") or document.get("review_sheet_url")
            sheet_sha = item.get("review_sheet_sha256") or document.get("review_sheet_sha256")
            if sheet_url and sheet_sha:
                evidence.append({"uri": sheet_url, "sha256": sheet_sha})
            valid_evidence = [e for e in evidence if _text(e.get("uri")) and _sha(e.get("sha256"))]
            if (not _text(source) or not _text(sid) or not _sha(original) or not _sha(preview)
                    or not _text(reason) or not reviewer or not _text(date) or not valid_evidence):
                raise ValueError(f"Incomplete explicit scope review: {uri} / {sid}")
            if release_assets is not None and not any(
                remote.get(e["uri"], {}).get("digest") == "sha256:" + e["sha256"]
                for e in valid_evidence if e.get("digest_scope") != "archive_member"
            ):
                raise ValueError(f"Scope review cloud evidence digest mismatch: {uri} / {sid}")
            status = _action(item)
            rules.append({"decision_id": f"{path.stem}:{original[:16]}:{preview[:12]}",
                "source": source, "source_id": sid, "original_sha256": original,
                "preview_sha256": preview, "status": status,
                "scope_dimension": _dimension(item),
                "content_scope_reason": item.get("content_scope_reason"),
                "disposition": item.get("disposition"), "reason": reason,
                "reviewed_by": reviewer, "reviewed_at": date,
                "evidence": [proof, *valid_evidence], "upstream_file": item.get("upstream_file"),
                "text_content": item.get("text_content"),
                "original_archive": item.get("original_archive"),
                # A clipped preview does not prove bad source artwork. A new,
                # genuinely corrected preview may be reviewed independently.
                "preview_specific": item.get("disposition") == "quarantine" and status != "pending_review"})
    signature = _digest({"version": POLICY_VERSION, "documents": documents})
    by_sha = defaultdict(list)
    for rule in rules:
        by_sha[rule["original_sha256"]].append(rule)
    return {"version": POLICY_VERSION, "sha256": signature, "documents": documents,
            "rules": rules, "by_sha": dict(by_sha),
            "evidence_verification": "cloud_digest_and_committed_document" if release_assets is not None else "committed_document"}


def evaluate_public_scope(record, file, policy):
    """Return public eligibility without mutating record or license metadata."""
    rules = policy.get("by_sha", {}).get(file.get("sha256"), [])
    preview = (file.get("preview") or {}).get("sha256")
    rules = [r for r in rules if not r["preview_specific"] or r["preview_sha256"] == preview]
    if not rules:
        # Preserve an already bound explicit exclusion in an input catalog;
        # absence of this run's optional review file cannot silently unhide it.
        prior = file.get("public_scope") or {}
        if prior.get("public_allowed") is False and (not prior.get("preview_specific")
                or prior.get("evidence_preview_sha256") == preview):
            return copy.deepcopy(prior)
        return {"status": "unreviewed", "public_allowed": True,
                "policy_version": policy["version"], "policy_sha256": policy["sha256"]}
    # Later actual media-type review can resolve an earlier photographic doubt.
    # It cannot erase an independent component-suitability or preview exclusion.
    priority = {"included": 0, "pending_review": 1, "excluded": 2}
    dimensions = {}
    for r in rules:
        key = r["scope_dimension"]
        previous = dimensions.get(key)
        if previous is None or (r["reviewed_at"], priority[r["status"]], r["decision_id"]) > (
                previous["reviewed_at"], priority[previous["status"]], previous["decision_id"]):
            dimensions[key] = r
    rule = max(dimensions.values(), key=lambda r: (priority[r["status"]], r["reviewed_at"], r["decision_id"]))
    return {key: copy.deepcopy(rule[key]) for key in
            ("status", "decision_id", "disposition", "reason", "reviewed_by", "reviewed_at", "evidence", "scope_dimension", "content_scope_reason")} | {
        "public_allowed": rule["status"] == "included", "policy_version": policy["version"],
        "policy_sha256": policy["sha256"], "evidence_original_sha256": rule["original_sha256"],
        "evidence_preview_sha256": rule["preview_sha256"], "preview_specific": rule["preview_specific"],
        "reviewed_dimensions": {key: value["status"] for key, value in dimensions.items()}}


def partition_acquisition_descriptors(record, policy):
    """Skip a known excluded original only when its immutable content pin matches.

    Mutable source URLs, titles and preview problems never trigger prefetch
    skipping. The caller still stores the complete unmodified discovery.
    """
    allowed, excluded = [], []
    for descriptor in record.get("files", []):
        matched = []
        for rule in policy["rules"]:
            upstream = rule.get("upstream_file") or {}
            if (rule["disposition"] == "exclude_from_v1" and rule["source"] == record.get("source")
                    and rule["source_id"] == record.get("source_id")
                    and upstream.get("upstream_hash")
                    and all(descriptor.get(k) == upstream.get(k) for k in
                            ("url", "format", "archive_member", "upstream_hash", "upstream_size"))):
                matched.append(rule)
        if matched:
            rule = matched[-1]
            excluded.append({"descriptor": copy.deepcopy(descriptor), "decision_id": rule["decision_id"],
                             "reason": rule["reason"], "evidence": rule["evidence"],
                             "original_archive": rule.get("original_archive"),
                             "original_sha256": rule["original_sha256"], "policy_sha256": policy["sha256"]})
        else:
            allowed.append(descriptor)
    return allowed, excluded


def filter_public_records(records, policy):
    """Return a physically filtered copy and audit; originals remain untouched."""
    result, excluded, counts = [], [], Counter()
    input_count = 0
    for original in records:
        input_count += 1
        record = copy.deepcopy(original)
        retained = []
        for file in record.get("files", []):
            scope = evaluate_public_scope(record, file, policy)
            file["public_scope"] = scope
            text_rules = [r for r in policy.get("by_sha", {}).get(file.get("sha256"), []) if r.get("text_content")]
            if text_rules:
                # A later scope-only review often leaves lettering unknown.
                # It is not a retraction of an earlier exact-original observation.
                known = [r for r in text_rules if r["text_content"].get("status") in {"present", "absent"}]
                if known:
                    file["text_content"] = copy.deepcopy(max(known, key=lambda r: r["reviewed_at"])["text_content"])
                elif (file.get("text_content") or {}).get("status") not in {"present", "absent"}:
                    file["text_content"] = copy.deepcopy(max(text_rules, key=lambda r: r["reviewed_at"])["text_content"])
            else:
                file.setdefault("text_content", {"status": "unknown", "kinds": [], "cleanup_advice": "unknown",
                    "editable_text": "unknown", "basis": "No file-bound text-content review applied.", "evidence": []})
            counts[scope["status"]] += 1
            if scope["public_allowed"]:
                retained.append(file)
            else:
                excluded.append({"source": record.get("source"), "source_id": record.get("source_id"),
                    "source_index": record.get("source_index"), "asset_id": record.get("id"),
                    "sha256": file.get("sha256"), "preview_sha256": (file.get("preview") or {}).get("sha256"),
                    "archive": file.get("archive"), "public_scope": scope})
        if not retained:
            continue
        record.pop("catalog_visibility", None)
        record["files"] = retained
        hashes = {f.get("sha256") for f in retained}
        if "variants" in record:
            variants = []
            for variant in record["variants"]:
                variant["files"] = [f for f in variant.get("files", []) if f.get("sha256") in hashes]
                if variant["files"]:
                    variants.append(variant)
            record["variants"] = variants
        record["formats"] = sorted({f.get("format", "unknown") for f in retained})
        record["structures"] = sorted({f.get("structure", "unknown") for f in retained})
        result.append(record)
    report = {"policy_version": policy["version"], "policy_sha256": policy["sha256"],
              "review_documents": policy["documents"], "evidence_verification": policy["evidence_verification"],
              "internal_record_count": input_count, "public_record_count_before_dedup": len(result),
              "file_scope_counts": dict(counts), "excluded_files": excluded,
              "excluded_unique_originals": len({f["sha256"] for f in excluded}),
              "internal_records_preserved": True, "unknown_attributes_are_not_exclusions": True}
    return result, report


def write_internal_catalog(records, path):
    """Write diagnostic metadata separately; it must never back a public index."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps({**record, "catalog_visibility": "internal_diagnostic"},
                                    ensure_ascii=False, separators=(",", ":")) + "\n")
            count += 1
        if not count:
            stream.write("\n")
    return {"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "size_bytes": path.stat().st_size, "records": count, "visibility": "internal_diagnostic"}
