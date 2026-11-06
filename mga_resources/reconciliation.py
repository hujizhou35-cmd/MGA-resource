"""Reuse verified GitHub originals across metadata-only discovery changes.

This module never reads artwork or trusts title/count similarity. The caller
must validate receipt JSON, records JSONL and cloud archive digests first.
Current discovery supplies metadata and rights; prior receipts supply bytes.
"""
from __future__ import annotations

import copy
import re
from collections import Counter
from urllib.parse import quote, urlsplit


HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
ALLOWED_LICENSES = {"CC0-1.0", "CC-BY-3.0", "CC-BY-4.0", "CC-BY-SA-3.0",
                    "CC-BY-SA-4.0", "MIT", "BSD-2-Clause", "BSD-3-Clause",
                    "Public Domain", "public domain", "PD"}
PENDING_RIGHTS = {"pending", "unknown", "unverified", "needs_review", "needs_rights_review", "denied", "prohibited", "hold"}


def receipt_evidence(receipt: dict, source_index: int) -> dict:
    """Compact provenance, called only AFTER assembler validation succeeds."""
    keys = ("source", "run_id", "repository", "release_tag", "discovery_sha256",
            "archive", "archive_bytes", "records_file", "records_sha256",
            "failures_file", "failures_sha256")
    result = {key: receipt.get(key) for key in keys}
    result.update(source_index=source_index, archive_sha256=receipt.get("sha256"),
                  receipt_file=receipt.get("_metadata_file"), receipt_sha256=receipt.get("_metadata_sha256"))
    return result


def _valid_proof(proof, source, digest, index) -> bool:
    if not isinstance(proof, dict) or (proof.get("source"), proof.get("discovery_sha256"), proof.get("source_index")) != (source, digest, index):
        return False
    if not all(HEX64.fullmatch(str(proof.get(key, ""))) for key in
               ("discovery_sha256", "archive_sha256", "records_sha256", "failures_sha256", "receipt_sha256")):
        return False
    if not all(isinstance(proof.get(key), str) and proof[key] for key in
               ("run_id", "repository", "release_tag", "archive", "records_file", "failures_file", "receipt_file")):
        return False
    return type(proof.get("archive_bytes")) is int and proof["archive_bytes"] > 0


def _safe_member(value) -> bool:
    return (isinstance(value, str) and bool(value) and not value.startswith("/")
            and "\\" not in value and ":" not in value
            and all(part not in {"", ".", ".."} for part in value.split("/")))


def _file_identity(descriptor, revision, repository):
    url = descriptor.get("url")
    if not isinstance(url, str) or descriptor.get("archive_member"):
        return None
    parsed = urlsplit(url)
    prefix = "/" + repository + "/" + revision + "/"
    if (parsed.scheme != "https" or parsed.netloc != "raw.githubusercontent.com"
            or parsed.query or parsed.fragment or not parsed.path.startswith(prefix)
            or not parsed.path[len(prefix):]):
        return None
    blob = descriptor.get("upstream_hash", "")
    if not isinstance(blob, str) or not blob.startswith("git-blob-sha1:") or not HEX40.fullmatch(blob[14:]):
        return None
    size = descriptor.get("upstream_size")
    if type(size) is not int or size <= 0:
        return None
    return url, blob, size


def _record_identity(record, source):
    revision = record.get("upstream_revision")
    if (record.get("source") != source["source_id"] or not record.get("source_id")
            or not isinstance(revision, str) or not HEX40.fullmatch(revision)):
        return None
    identities = [_file_identity(item, revision, source["repository"]) for item in record.get("files", [])]
    if not identities or any(item is None for item in identities) or len(set(identities)) != len(identities):
        return None
    return record["source_id"], revision, tuple(sorted(identities))


def _usable_files(record, proof) -> bool:
    expected_archive = (f"https://github.com/{proof['repository']}/releases/download/"
                        f"{quote(proof['release_tag'], safe='')}/{quote(proof['archive'], safe='')}")
    for item in record.get("files", []):
        if (item.get("validation", {}).get("status") not in {"machine_checked", "machine_checked_and_rendered"}
                or not HEX64.fullmatch(str(item.get("sha256", "")))
                or type(item.get("size_bytes")) is not int or item["size_bytes"] != item.get("upstream_size")):
            return False
        preview = item.get("preview")
        if (not isinstance(preview, dict) or not HEX64.fullmatch(str(preview.get("sha256", "")))
                or type(preview.get("size_bytes")) is not int or preview["size_bytes"] <= 0):
            return False
        for content, key in ((item, "storage_path"), (preview, "path")):
            archive, member = content.get("archive"), content.get(key)
            if (not _safe_member(member) or not isinstance(archive, dict)
                    or archive.get("url") != expected_archive or archive.get("member") != member):
                return False
    return bool(record.get("files"))


def _current_metadata(current, old, index):
    """Derived IDs/families/rights from old metadata are deliberately omitted."""
    record = copy.deepcopy(current)
    record["files"] = copy.deepcopy(old["files"])
    record["source_index"] = index
    if old.get("collection_run"):
        record["collection_run"] = old["collection_run"]
    return record


def reconcile_github_snapshot(source, current_inventory, current_digest, current_states, prior_snapshots):
    """Return (states, audit); prior snapshots contain digest and verified states.

    The caller has checked the complete current inventory against its GitHub
    release SHA-256 and count. Each old state carries `evidence` made only after
    validating its receipt, metadata files and cloud archive digest.
    """
    if source.get("adapter") not in {"bioicons", "github_tree"} or not source.get("repository"):
        raise ValueError("Reconciliation is restricted to version-pinned GitHub sources")
    if not HEX64.fullmatch(current_digest):
        raise ValueError("Current discovery SHA-256 is missing or invalid")
    source_id = source["source_id"]
    ids = [item.get("source_id") for item in current_inventory]
    if (any(not isinstance(sid, str) or not sid for sid in ids) or len(ids) != len(set(ids))
            or any(item.get("source") != source_id for item in current_inventory)):
        raise ValueError("Complete discovery has duplicate identities or a mismatched source")
    if any(type(index) is not int or not 0 <= index < len(current_inventory) for index in current_states):
        raise ValueError("Current attempt index is outside the complete inventory")
    states = copy.deepcopy(current_states)
    candidates = {}
    rejected = Counter()
    # Caller supplies older snapshots newest first. A later corrupt/failed
    # state for an identity also blocks falling further back to an old success.
    seen_old_ids = set()
    for snapshot in prior_snapshots:
        if snapshot.get("metadata_issues"):
            rejected["older_history_blocked_by_unverified_snapshot"] += 1
            break
        digest = snapshot["discovery_sha256"]
        for index, state in sorted(snapshot["states"].items()):
            record = state.get("record")
            sid = (record or {}).get("source_id") or state.get("source_id")
            if not sid or sid in seen_old_ids:
                continue
            seen_old_ids.add(sid)
            if state.get("status") != "acquired" or not record or state.get("failures") or state.get("issue"):
                rejected["prior_not_successful"] += 1
                continue
            proof = state.get("evidence")
            if not _valid_proof(proof, source_id, digest, index) or not _usable_files(record, proof):
                rejected["prior_evidence_or_media_incomplete"] += 1
                continue
            identity = _record_identity(record, source)
            if identity is None:
                rejected["prior_not_pinned_git_identity"] += 1
                continue
            candidates[identity] = (record, proof)
    decisions, reused_runs = [], Counter()
    reused_files = 0
    for index, current in enumerate(current_inventory):
        existing = states.get(index)
        # A corrupt attempted batch remains unresolved, even if current rights
        # would otherwise create a metadata-only held/excluded disposition.
        if existing is not None and existing.get("issue"):
            decisions.append({"index": index, "source_id": ids[index], "origin": "current_attempt", "status": existing["status"]})
            continue
        excluded = current.get("scope_status") == "excluded" and bool(str(current.get("exclusion_reason") or "").strip())
        held = (current.get("distribution_allowed") is False or current.get("license") not in ALLOWED_LICENSES
                or str(current.get("rights_status", "")).lower() in PENDING_RIGHTS)
        if excluded or held:
            status = "excluded" if excluded else "held"
            reason = current.get("exclusion_reason") if excluded else current.get("rights_note") or "Current discovery does not establish redistribution permission"
            state = {"source_id": ids[index], "status": status, "record": None, "failures": [],
                     "reconciliation": {"origin": "current_discovery_metadata", "fetch_attempted": False,
                                        "discovery_sha256": current_digest, "source_index": index, "reason": reason}}
            if excluded:
                state["exclusion"] = {"source_index": index, "source_id": ids[index], "reason": reason}
            if existing is not None:
                state["reconciliation"]["current_attempt_status"] = existing["status"]
                state["reconciliation"]["current_attempt_evidence"] = existing.get("evidence")
            states[index] = state
            decisions.append({"index": index, "source_id": ids[index], "origin": "current_discovery_metadata", "status": status, "fetch_attempted": False})
            continue
        if existing is not None:
            if existing.get("record") is not None:
                if existing["record"].get("source_id") != ids[index]:
                    raise ValueError("Current attempted record disagrees with complete inventory identity")
                existing["record"] = _current_metadata(current, existing["record"], index)
            decisions.append({"index": index, "source_id": ids[index], "origin": "current_attempt", "status": existing["status"]})
            continue
        identity = _record_identity(current, source)
        candidate = candidates.get(identity) if identity is not None else None
        if candidate is None:
            decisions.append({"index": index, "source_id": ids[index], "origin": "unresolved", "status": "missing",
                              "reason": "No verified success with the same source identity, pinned commit and complete file/blob/size set"})
            continue
        old, proof = candidate
        record = _current_metadata(current, old, index)
        record["collection_run"] = proof["run_id"]
        record["acquisition"] = {"discovery_sha256": current_digest, "source_index": index, "status": "acquired",
                                 "method": "reused_exact_pinned_github_blob", "fetch_attempted": False,
                                 "artifact_evidence": copy.deepcopy(proof)}
        states[index] = {"source_id": ids[index], "status": "acquired", "record": record, "failures": [],
                         "evidence": copy.deepcopy(proof), "reconciliation": copy.deepcopy(record["acquisition"])}
        reused_runs[proof["run_id"]] += 1
        reused_files += len(record["files"])
        decisions.append({"index": index, "source_id": ids[index], "origin": "reused_exact_pinned_github_blob", "status": "acquired",
                          "origin_run_id": proof["run_id"], "origin_source_index": proof["source_index"],
                          "origin_discovery_sha256": proof["discovery_sha256"], "origin_records_file": proof["records_file"],
                          "origin_records_sha256": proof["records_sha256"], "origin_archive": proof["archive"],
                          "origin_archive_sha256": proof["archive_sha256"],
                          "origin_receipt_file": proof["receipt_file"], "origin_receipt_sha256": proof["receipt_sha256"],
                          "fetch_attempted": False})
    audit = {"method": "same_commit_path_and_git_blob", "current_discovery_sha256": current_digest,
             "current_inventory_count": len(current_inventory), "reused_records": sum(reused_runs.values()),
             "reused_files": reused_files, "reused_from_runs": dict(sorted(reused_runs.items())),
             "metadata_dispositions": sum(d["origin"] == "current_discovery_metadata" for d in decisions),
             "unresolved_records": sum(d["origin"] == "unresolved" for d in decisions),
             "rejected_prior_candidates": dict(rejected), "decisions": decisions,
             "artwork_refetched": False, "visual_acceptance": "unchanged_pending"}
    return states, audit
