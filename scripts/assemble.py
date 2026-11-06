"""Build the global catalog from cloud checkpoints, without fetching artwork packs."""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mga_resources.catalog import finalize, search
from mga_resources.reconciliation import receipt_evidence, reconcile_github_snapshot


def gh(*args):
    result = subprocess.run(["gh", *args], text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr)
    return result.stdout


def read_lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def list_release_assets(repository, tag):
    release = json.loads(gh("api", f"repos/{repository}/releases/tags/{quote(tag, safe='')}"))
    pages = json.loads(gh("api", "--paginate", "--slurp",
                         f"repos/{repository}/releases/{release['id']}/assets?per_page=100"))
    return {item["name"]: item for page in pages for item in page}


def _archive_links(record, receipt, asset=None):
    expected_url = (asset or {}).get("browser_download_url") or (
        f"https://github.com/{receipt.get('repository')}/releases/download/"
        f"{receipt.get('release_tag')}/{receipt['archive']}")
    for item in record.get("files", []):
        for content, path_key in ((item, "storage_path"), (item.get("preview"), "path")):
            if not isinstance(content, dict):
                continue
            location = content.get("archive")
            # A missing preview is a machine-validation issue. A supplied
            # archive link, however, must point to this verified checkpoint.
            if content is not item and location is None:
                continue
            member = content.get(path_key)
            if (not isinstance(location, dict) or location.get("url") != expected_url
                    or not isinstance(member, str) or not member
                    or location.get("member") != member
                    or member.startswith("/") or "\\" in member or ":" in member
                    or any(part in {"", ".", ".."} for part in member.split("/"))):
                raise ValueError("Artwork or preview archive link disagrees with its checkpoint")


def _read_checked(incoming, filename, expected_hash, *, jsonl=False):
    if not isinstance(filename, str) or not filename or Path(filename).name != filename or "\\" in filename:
        raise ValueError("Checkpoint metadata must use a release asset filename")
    path = incoming / filename
    if not path.is_file():
        raise ValueError(f"Missing checkpoint metadata: {filename}")
    if not expected_hash or hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
        raise ValueError(f"Metadata SHA-256 missing or mismatched: {filename}")
    return read_lines(path) if jsonl else json.loads(path.read_text(encoding="utf-8"))


def _priority(item):
    # A smaller current inventory supersedes a larger older inventory.
    return (item.get("started_at", ""), item.get("run_id", ""), item.get("range_end", -1))


def _ranges(indices):
    result = []
    for index in sorted(indices):
        if result and result[-1][1] == index:
            result[-1][1] += 1
        else:
            result.append([index, index + 1])
    return result


def _index(item, start, end, key="source_index"):
    value = item.get(key)
    if type(value) is not int or not start <= value < end:
        raise ValueError(f"Missing or out-of-range {key}: {value!r}")
    return value


def _machine_checked(record):
    files = record.get("files") or []
    for item in files:
        preview = item.get("preview")
        if (item.get("validation", {}).get("status") not in {"machine_checked", "machine_checked_and_rendered"}
                or not item.get("sha256") or not item.get("archive")
                or not isinstance(preview, dict) or not preview.get("archive")):
            return False
    return bool(files)


def _summarize_states(states, summary):
    """Summarize attempted and explicitly reconciled states without new receipts."""
    summary = dict(summary)
    source, digest, discovered = summary["source"], summary["discovery_sha256"], summary["discovered"]
    records = []
    for index, state in sorted(states.items()):
        if state["record"] is not None:
            record = dict(state["record"])
            record["acquisition"] = {**record.get("acquisition", {}), "discovery_sha256": digest,
                                     "source_index": index, "status": state["status"]}
            if state.get("evidence") and "artifact_evidence" not in record["acquisition"]:
                record["acquisition"]["artifact_evidence"] = state["evidence"]
            records.append(record)
    counts = {name: sum(state["status"] == name for state in states.values())
              for name in ("acquired", "held", "failed", "excluded", "unknown")}
    excluded = [state["exclusion"] for _, state in sorted(states.items()) if state.get("exclusion")]
    missing = set(range(discovered)) - states.keys()
    issues = sorted(set(summary.get("metadata_issues", [])) | {s["issue"] for s in states.values() if s.get("issue")})
    inventory_processed = not missing and not issues and summary["inventory_evidence_available"]
    in_scope = discovered - counts["excluded"]
    complete = inventory_processed and counts["acquired"] == in_scope
    unchecked = sum(not _machine_checked(record) for record in records)
    machine_complete = complete and unchecked == 0
    summary.update(status="machine_complete_pending_visual" if machine_complete else "incomplete",
                   processed=len(states), downloaded_records=len(records), **counts,
                   held_count=counts["held"], failed_records=counts["failed"],
                   excluded_count=counts["excluded"], excluded_records=excluded,
                   in_scope_discovered=in_scope, coverage_ranges=_ranges(states), missing_ranges=_ranges(missing),
                   source_inventory_processed=inventory_processed, acquisition_complete=complete,
                   machine_validation_complete=machine_complete, machine_validation_pending_records=unchecked,
                   collection_complete=False, metadata_issues=issues)
    return records, summary


def _snapshot(source, digest, reports, receipts, incoming, release_assets=None):
    evidence = reports + receipts
    latest = max(evidence, key=_priority)
    counts = {item["discovered"] for item in evidence if type(item.get("discovered")) is int}
    issues, historical_issues = [], []
    if len(counts) != 1 or any(number < 0 for number in counts):
        issues.append("Discovery count is missing or inconsistent within this snapshot")
    discovered = max(counts, default=0)
    states = {}
    # The source index is a durable identity within a discovery hash. Later
    # overlapping attempts replace earlier outcomes; separate offset runs merge.
    for receipt in sorted(receipts, key=lambda value: (_priority(value), value.get("records_file", ""))):
        start, end = receipt.get("range_start"), receipt.get("range_end")
        if (type(start) is not int or type(end) is not int
                or not 0 <= start <= end <= discovered):
            issues.append(f"Invalid checkpoint range in run {receipt.get('run_id')}")
            continue
        outcomes = {index: {"status": "unknown", "record": None, "failures": []}
                    for index in range(start, end)}
        try:
            archive = receipt.get("archive")
            asset = None
            if not archive or not re.fullmatch(r"[0-9a-f]{64}", receipt.get("sha256", "")):
                raise ValueError("Checkpoint lacks an archive name or SHA-256")
            if release_assets is not None:
                asset = release_assets.get(archive)
                if asset is None or asset.get("size") != receipt.get("archive_bytes"):
                    raise ValueError(f"Archive is missing or has a different size: {archive}")
                if asset.get("digest") != "sha256:" + receipt["sha256"]:
                    raise ValueError(f"Cloud archive SHA-256 disagrees with its receipt: {archive}")
                discovery_name = f"{source}-{receipt['run_id']}.discovery.jsonl"
                if release_assets.get(discovery_name, {}).get("digest") != "sha256:" + digest:
                    raise ValueError(f"Cloud discovery SHA-256 disagrees with its receipt: {discovery_name}")
            batch = _read_checked(incoming, receipt.get("records_file"), receipt.get("records_sha256"), jsonl=True)
            failures = _read_checked(incoming, receipt.get("failures_file"), receipt.get("failures_sha256"))
            if not isinstance(failures, list) or len(batch) != receipt.get("records") or len(failures) != receipt.get("failures"):
                raise ValueError("Checkpoint metadata count disagrees with its receipt")
            for record in batch:
                index = _index(record, start, end)
                if record.get("source") != source or outcomes[index]["record"] is not None:
                    raise ValueError("Duplicate index or mismatched source in checkpoint")
                _archive_links(record, receipt, asset)
                outcomes[index]["record"] = record
            for failure in failures:
                index = _index(failure, start, end)
                outcomes[index]["failures"].append(failure)
            excluded_details = {_index(item, start, end): item for item in receipt.get("excluded_records", [])}
            dispositions = receipt.get("dispositions", [])
            disposition_indices = set()
            for item in dispositions:
                index = _index(item, start, end, "index")
                if index in disposition_indices:
                    raise ValueError("Duplicate disposition index")
                disposition_indices.add(index)
                status = item.get("status")
                if status not in {"acquired", "held", "failed", "excluded"}:
                    raise ValueError(f"Unknown disposition status: {status}")
                record = outcomes[index]["record"]
                if record is not None and record.get("source_id") != item.get("source_id"):
                    raise ValueError("Artwork identity disagrees with its disposition")
                if status == "acquired" and (not record or not record.get("files") or outcomes[index]["failures"]):
                    raise ValueError("Acquired disposition disagrees with records or failures")
                if status == "held" and record:
                    raise ValueError("Held artwork also appears in distributable records")
                if status == "excluded":
                    if record or outcomes[index]["failures"]:
                        raise ValueError("Excluded artwork also appears in acquired records or download failures")
                    detail = excluded_details.get(index, {})
                    reason = item.get("reason") or detail.get("reason")
                    if not isinstance(reason, str) or not reason.strip():
                        # An unexplained exclusion cannot reduce the acquisition
                        # denominator or turn a missing artwork into a pass.
                        outcomes[index]["status"] = "unknown"
                        continue
                    outcomes[index]["exclusion"] = {"source_index": index,
                        "source_id": item.get("source_id") or detail.get("source_id"), "reason": reason.strip()}
                outcomes[index]["status"] = status
                outcomes[index]["source_id"] = item.get("source_id")
            if disposition_indices != set(range(start, end)):
                raise ValueError("Disposition indices do not cover the checkpoint range")
            # Evidence is attached only after the entire receipt, records,
            # failure list and cloud archive identity have passed validation.
            reusable_metadata = release_assets is not None and all(
                release_assets.get(name, {}).get("digest") == "sha256:" + str(digest_value)
                for name, digest_value in (
                    (receipt.get("_metadata_file"), receipt.get("_metadata_sha256")),
                    (receipt.get("records_file"), receipt.get("records_sha256")),
                    (receipt.get("failures_file"), receipt.get("failures_sha256")),
                ))
            for index, outcome in outcomes.items():
                if reusable_metadata:
                    outcome["evidence"] = receipt_evidence(receipt, index)
        except (ValueError, OSError, TypeError) as exc:
            issue = f"{receipt.get('records_file', receipt.get('run_id'))}: {exc}"
            historical_issues.append(issue)
            # A corrupt newer batch must not silently reuse older successes.
            outcomes = {index: {"status": "unknown", "record": None, "failures": [], "issue": issue}
                        for index in range(start, end)}
        states.update(outcomes)

    issues += sorted({state["issue"] for state in states.values() if state.get("issue")})
    inventory_evidence = bool(receipts or any(
        report.get("status") == "final" and report.get("processed") == 0 and report.get("fatal") is None
        for report in reports))
    summary = {
        "source": source, "discovery_sha256": digest, "started_at": latest.get("started_at"),
        "run_ids": sorted({item.get("run_id") for item in evidence if item.get("run_id")}),
        "discovered": discovered, "inventory_evidence_available": inventory_evidence,
        "metadata_issues": issues,
        "historical_metadata_issues": historical_issues,
        "fatal_runs": [{"run_id": report.get("run_id"), "fatal": report["fatal"]}
                       for report in reports if report.get("fatal")],
        "visual_acceptance": "pending", "illustrator_verified": False,
    }
    records, summary = _summarize_states(states, summary)
    return records, summary, states


def _complete_discovery(incoming, source, summary, release_assets):
    """The complete JSONL, actual SHA, cloud digest, count and IDs all agree."""
    digest = summary["discovery_sha256"]
    for run_id in reversed(summary["run_ids"]):
        filename = f"{source}-{run_id}.discovery.jsonl"
        asset = release_assets.get(filename)
        if not asset or asset.get("digest") != "sha256:" + digest:
            continue
        rows = _read_checked(incoming, filename, digest, jsonl=True)
        if asset.get("size") is not None and asset["size"] != (incoming / filename).stat().st_size:
            raise ValueError("Complete discovery byte size disagrees with GitHub release")
        if len(rows) != summary["discovered"]:
            raise ValueError("Complete discovery count disagrees with checkpoint inventory")
        ids = [row.get("source_id") for row in rows]
        if (any(row.get("source") != source for row in rows)
                or any(not isinstance(sid, str) or not sid for sid in ids) or len(set(ids)) != len(ids)):
            raise ValueError("Complete discovery contains duplicate IDs or a mismatched source")
        return rows, {"file": filename, "sha256": digest, "run_id": run_id,
                      "size_bytes": (incoming / filename).stat().st_size, "cloud_digest_verified": True}
    raise ValueError("No complete discovery asset matches the current snapshot")


def _integrated_source_summary(config, inventories, records):
    """An existing primary source may be delivered through a pinned parent.

    Its exact expected ID set is checked, not inferred from a matching count.
    This creates a coverage summary only; no artwork records are duplicated.
    """
    parent = config["integrated_via"]
    scope = config["integrated_scope"]
    inventory, proof = inventories.get(parent, ([], None))
    expected = {r["source_id"] for r in inventory
                if r.get("upstream_revision") == scope["pinned_commit"]
                and len(r["source_id"].split("/")) >= 4
                and r["source_id"].split("/")[2] in scope["author_directories"]}
    expected_digest = hashlib.sha256("\n".join(sorted(expected)).encode()).hexdigest()
    verified = (proof is not None and len(expected) == scope["expected_records"]
                and expected_digest == scope["source_ids_sha256"])
    acquired = {r["source_id"]: r for r in records if r.get("source") == parent
                and r.get("source_id") in expected and r.get("upstream_revision") == scope["pinned_commit"]
                and r.get("acquisition", {}).get("status") == "acquired"}
    complete = verified and acquired.keys() == expected
    machine_complete = complete and all(_machine_checked(record) for record in acquired.values())
    return {"source": config["source_id"], "integrated_via": parent,
            "status": "machine_complete_pending_visual" if machine_complete else "integrated_pending_acquisition",
            "catalog_uses": "reference_existing_parent_records", "expected_records": scope["expected_records"],
            "discovered": len(expected) if proof else None, "downloaded_records": len(acquired),
            "scope_identity_verified": verified, "scope_source_ids_sha256": expected_digest,
            "inventory_evidence": proof, "missing_source_ids": sorted(expected - acquired.keys()) if verified else [],
            "source_inventory_processed": complete, "acquisition_complete": complete,
            "machine_validation_complete": machine_complete, "collection_complete": False,
            "visual_acceptance": "pending", "illustrator_verified": False}


def assemble_snapshots(incoming: Path, registry: list, release_assets=None):
    """Pure metadata aggregation, also used by offline synthetic regression tests."""
    groups = defaultdict(lambda: {"reports": [], "receipts": []})
    legacy = []
    for pattern, kind in (("*.report.json", "reports"), ("*.receipt.json", "receipts")):
        for path in sorted(incoming.glob(pattern)):
            item = json.loads(path.read_text(encoding="utf-8"))
            item["_metadata_file"] = path.name
            item["_metadata_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            source, digest = item.get("source"), item.get("discovery_sha256")
            if not source:
                continue
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or not item.get("started_at"):
                legacy.append({"source": source, "run_id": item.get("run_id"), "metadata_file": path.name,
                               "status": "legacy_pending", "reason": "No verifiable discovery snapshot/range contract"})
                continue
            groups[(source, digest)][kind].append(item)
    by_source = defaultdict(list)
    for (source, digest), group in sorted(groups.items()):
        by_source[source].append(_snapshot(source, digest, group["reports"], group["receipts"], incoming, release_assets))
    records, summaries, verified_inventories = [], [], {}
    for source, versions in sorted(by_source.items()):
        versions.sort(key=lambda pair: (pair[1]["started_at"], pair[1]["discovery_sha256"]), reverse=True)
        current_records, current, current_states = versions[0]
        source_config = next((item for item in registry if item["source_id"] == source), {})
        if (release_assets is not None and source_config.get("adapter") in {"bioicons", "github_tree"}
                and source_config.get("repository")):
            try:
                inventory, inventory_evidence = _complete_discovery(incoming, source, current, release_assets)
                verified_inventories[source] = (inventory, inventory_evidence)
                prior = [{"discovery_sha256": summary["discovery_sha256"], "states": states,
                          "metadata_issues": summary["metadata_issues"]}
                         for _, summary, states in versions[1:]]
                current_states, reconciliation = reconcile_github_snapshot(
                    source_config, inventory, current["discovery_sha256"], current_states, prior)
                current["inventory_evidence_available"] = True
                current_records, current = _summarize_states(current_states, current)
                current["metadata_reconciliation"] = {**reconciliation, "inventory_evidence": inventory_evidence}
            except (ValueError, TypeError, OSError) as exc:
                current["metadata_issues"].append(f"Metadata reconciliation unavailable: {exc}")
                current_records, current = _summarize_states(current_states, current)
        fallback = next((summary for _, summary, _ in versions[1:] if summary["machine_validation_complete"]), None)
        current["older_complete_fallback"] = fallback
        current["catalog_uses"] = ("latest_metadata_with_verified_original_reuse" if current.get("metadata_reconciliation", {}).get("reused_records")
                                   else "latest_snapshot_only")
        records.extend(current_records)
        summaries.append(current)
    chosen = {item["source"]: item for item in summaries}
    for config in registry:
        if config.get("integrated_via") and config.get("integrated_scope"):
            summary = _integrated_source_summary(config, verified_inventories, records)
            summaries.append(summary)
            chosen[config["source_id"]] = summary
    for source in sorted({item["source"] for item in legacy} - chosen.keys()):
        summary = {"source": source, "status": "legacy_pending", "acquisition_complete": False,
                   "machine_validation_complete": False, "collection_complete": False,
                   "visual_acceptance": "pending", "catalog_uses": "none"}
        summaries.append(summary)
        chosen[source] = summary
    outstanding = [item["source_id"] for item in registry if item.get("primary")
                   and not chosen.get(item["source_id"], {}).get("machine_validation_complete", False)]
    return records, sorted(summaries, key=lambda item: item["source"]), outstanding, legacy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    if os.environ.get("GITHUB_ACTIONS") != "true":
        parser.error("Assemble the collection on GitHub Actions; local full-library acquisition is disabled")
    run_id = os.environ["GITHUB_RUN_ID"] + "-" + os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    base = ROOT / "build" / "assembly" / run_id
    incoming, output = base / "metadata", base / "catalog"
    incoming.mkdir(parents=True, exist_ok=True)
    repository = os.environ["GITHUB_REPOSITORY"]
    assets = list_release_assets(repository, args.release_tag)
    # Fetch metadata only; no tar, original image, preview or review sheet.
    selected = sorted(name for name in assets if not name.startswith("assembly-") and name.endswith(
        (".report.json", ".receipt.json", ".records.jsonl", ".failures.json", ".discovery.jsonl")))
    for start in range(0, len(selected), 100):
        flags = [argument for name in selected[start:start + 100] for argument in ("--pattern", name)]
        gh("release", "download", args.release_tag, *flags, "--dir", str(incoming), "--clobber")
    for name in selected:
        downloaded_digest = "sha256:" + hashlib.sha256((incoming / name).read_bytes()).hexdigest()
        if assets[name].get("digest") != downloaded_digest:
            raise RuntimeError(f"Downloaded metadata SHA-256 disagrees with GitHub: {name}")
    registry = json.loads((ROOT / "data/sources.json").read_text(encoding="utf-8"))["sources"]
    records, acquisition, outstanding, legacy = assemble_snapshots(incoming, registry, assets)
    enrichments = []
    arcadia_records = [record for record in records if record.get("source") == "arcadia"]
    if arcadia_records:
        from mga_resources.archive_metadata import load_arcadia_metadata, enrich_arcadia
        from mga_resources.http import HttpClient
        source = next(item for item in registry if item["source_id"] == "arcadia")
        metadata = load_arcadia_metadata(source, HttpClient())
        for record in arcadia_records:
            member = next((item["archive_member"] for item in record.get("files", [])
                           if item.get("archive_member")), record["source_id"])
            enrich_arcadia(record, member, source, metadata)
        enrichments.append({"source": "arcadia", "metadata_url": metadata["url"],
                            "metadata_revision": metadata["revision"], "records": len(arcadia_records),
                            "matched": sum(record.get("metadata_status") == "matched_official_gallery"
                                           for record in arcadia_records),
                            "artwork_refetched": False})
    from mga_resources.native_evidence import attach_native_evidence
    publication_path = ROOT / "data/legacy-publication.json"
    publication = json.loads(publication_path.read_text(encoding="utf-8"))
    manifest_name = publication["manifest"]["url"].rsplit("/", 1)[-1]
    for key in ("manifest", "archive"):
        expected = publication[key]
        remote = assets.get(expected["url"].rsplit("/", 1)[-1], {})
        if remote.get("digest") != "sha256:" + expected["sha256"] or remote.get("size") != expected["size_bytes"]:
            raise RuntimeError(f"Legacy cloud {key} integrity disagrees with recorded publication")
    gh("release", "download", args.release_tag, "--pattern", manifest_name, "--dir", str(incoming), "--clobber")
    records, native_evidence_report = attach_native_evidence(
        records, ROOT / "data/legacy-illustrator-verification.json", publication_path, incoming / manifest_name)
    from mga_resources.scientific_reviews import apply_scientific_reviews, finalized_scientific_review_counts
    records, scientific_review_report = apply_scientific_reviews(
        records, (ROOT / "data/visual-reviews").glob("*.json"), assets)
    summary = finalize(records, output)
    scientific_review_report["retained_after_finalize"] = finalized_scientific_review_counts(
        read_lines(output / "catalog.jsonl"))
    benchmark = json.loads((ROOT / "data/benchmark.json").read_text(encoding="utf-8"))
    results = []
    for query in benchmark["queries"]:
        matches = search(output / "catalog.sqlite", query["query"], limit=10)
        results.append({**query, "candidates": matches, "relevance_review": "pending",
                        "pass": None, "note": "Text retrieval is not proof of visual or scientific suitability."})
    (output / "retrieval-candidates.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    report = {"assembled_at": datetime.now(timezone.utc).isoformat(), "run_id": run_id,
              "collection_release": args.release_tag, "summary": summary,
              "source_snapshots": acquisition, "legacy_checkpoints": legacy,
              "metadata_enrichments": enrichments,
              "historical_native_evidence": native_evidence_report,
              "scientific_visual_reviews": scientific_review_report,
              "outstanding_primary_sources": outstanding,
              "benchmark_queries": len(results), "visually_judged_queries": 0,
              "retrieval_pass_rate": None, "acceptance_passed": False,
              "website_gate": "closed_pending_collection_and_acceptance"}
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    uploads = []
    for path in output.iterdir():
        target = base / f"assembly-{run_id}.{path.name}"
        path.replace(target)
        uploads.append(target)
    if args.publish:
        gh("release", "upload", args.release_tag, *map(str, uploads))
    print(json.dumps(report, ensure_ascii=False), flush=True)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
            stream.write("## Global cloud catalog\n\n")
            stream.write(f"Sources selected: {len(acquisition)}. Retrieval queries prepared: {len(results)}.\n\n")
            stream.write(f"Outstanding primary sources: {', '.join(outstanding) or 'none'}.\n\n")
            stream.write("Visual judgments are pending. The website implementation gate remains closed.\n")


if __name__ == "__main__":
    main()

