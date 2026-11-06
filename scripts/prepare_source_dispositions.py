#!/usr/bin/env python3
"""Prepare evidence-bound source/file dispositions; never grant release acceptance.

Only JSON metadata may be fetched. Original artwork and archive bytes are never
downloaded by this tool. Re-run against the final assembly before acceptance.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from build_publish_pack import GitHubStore
from accept_release import evaluate_release, json_bytes
from mga_resources.scope_policy import load_scope_policy, evaluate_public_scope


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def descriptor_key(file):
    return file.get("url"), file.get("archive_member"), file.get("format")


class Metadata:
    """Read exact GitHub-digest-bound metadata, optionally fetch missing metadata."""
    def __init__(self, assets, roots, *, repository, tag, cache, fetch=False):
        if assets and isinstance(assets[0], list):
            assets = [row for page in assets for row in page]
        self.assets = {row["name"]: row for row in assets}
        if len(self.assets) != len(assets):
            raise ValueError("Duplicate release asset names")
        self.paths = defaultdict(list)
        for root in roots:
            for path in Path(root).rglob("*"):
                if path.is_file() and self.allowed(path.name):
                    self.paths[path.name].append(path)
        self.cache, self.fetch = Path(cache), fetch
        self.store = GitHubStore(repository)
        self.store.cache[tag] = self.assets
        self.used = {}
        self.ignored = []
        self.bytes_by_sha = {}

    @staticmethod
    def allowed(name):
        return name.endswith((".report.json", ".receipt.json", ".failures.json", ".discovery.jsonl"))

    def proof(self, name):
        row = self.assets[name]
        return {"uri": row["browser_download_url"], **self.store.proof(row["browser_download_url"])}

    def get(self, name, expected=None, *, lines=False):
        if not self.allowed(name):
            raise ValueError("Only report/receipt/failure/discovery metadata may be read")
        proof = self.proof(name)
        if expected and expected != proof["sha256"]:
            raise ValueError(f"Release digest disagrees with referenced metadata: {name}")
        data = self.bytes_by_sha.get(proof["sha256"])
        candidates = list(self.paths.get(name, []))
        if data is None:
            for alias, asset in self.assets.items():
                if alias != name and self.allowed(alias) and asset.get("digest") == "sha256:" + proof["sha256"]:
                    candidates.extend(self.paths.get(alias, []))
        for path in candidates if data is None else []:
            if path.stat().st_size == proof["size_bytes"]:
                candidate = path.read_bytes()
                if digest(candidate) == proof["sha256"]:
                    data = candidate
                    break
        if data is None:
            if not self.fetch:
                raise FileNotFoundError(f"Missing exact metadata bytes: {name}")
            data = self.store.metadata(proof["uri"], proof["sha256"])
            self.cache.mkdir(parents=True, exist_ok=True)
            path = self.cache / name
            path.write_bytes(data)
            self.paths[name].append(path)
        self.used[name] = proof
        self.bytes_by_sha[proof["sha256"]] = data
        value = [json.loads(line) for line in data.splitlines() if line.strip()] if lines else json.loads(data)
        return value, proof

    def inventory(self, source, sha, count=None):
        names = sorted(name for name, row in self.assets.items()
                       if name.startswith(source + "-") and name.endswith(".discovery.jsonl")
                       and row.get("digest") == "sha256:" + sha)
        if not names:
            raise ValueError(f"No GitHub-bound complete inventory for {source}: {sha}")
        # Prefer already cached identical bytes over fetching another run's copy.
        names.sort(key=lambda name: not bool(self.paths.get(name)))
        rows, proof = self.get(names[0], sha, lines=True)
        ids = [row.get("source_id") for row in rows]
        if ((count is not None and len(rows) != count) or len(set(ids)) != len(ids)
                or any(not isinstance(sid, str) or not sid for sid in ids)
                or any(row.get("source") != source for row in rows)):
            raise ValueError(f"Inventory count/source/unique identity mismatch: {source}")
        for row in rows:
            keys = [descriptor_key(file) for file in row.get("files", [])]
            if len(set(keys)) != len(keys):
                raise ValueError("Duplicate inventory file descriptor")
        return rows, proof

    def batches(self, source, current_sha=None, *, historical=False):
        """An original receipt may be authenticated inside its complete report.

        This records the containing report hash and exact embedded batch identity;
        it does not claim to have downloaded a separate receipt file.
        """
        out = []
        for name in sorted(self.assets):
            if not name.startswith(source + "-") or not name.endswith(".report.json"):
                continue
            report, proof = self.get(name)
            if report.get("source") != source:
                raise ValueError("Report source mismatch")
            if current_sha and report.get("discovery_sha256") != current_sha and not historical:
                continue
            for batch in report.get("batches", []):
                if not all(batch.get(key) for key in ("discovery_sha256", "records_sha256", "failures_sha256", "archive", "sha256")):
                    self.ignored.append({"reason": "Legacy batch lacks cryptographic descriptor evidence; not used.",
                                         "report": proof, "batch": batch.get("records_file")})
                    continue
                if batch.get("source") != source or batch.get("run_id") != report.get("run_id"):
                    raise ValueError("Embedded receipt source/run mismatch")
                if batch.get("discovery_sha256") != report.get("discovery_sha256"):
                    raise ValueError("Embedded receipt discovery mismatch")
                archive = self.proof(batch["archive"])
                if (archive["sha256"] != batch.get("sha256")
                        or archive["size_bytes"] != batch.get("archive_bytes")):
                    raise ValueError("Receipt archive proof disagrees with GitHub")
                # No archive is downloaded. Its exact remote digest/size is the
                # same proof required by the assembly which supplied the catalog.
                failures, failure_proof = self.get(batch["failures_file"], batch["failures_sha256"])
                if not isinstance(failures, list) or len(failures) != batch.get("failures"):
                    raise ValueError("Receipt failure count mismatch")
                out.append({"receipt": batch, "failures": failures, "evidence": failure_proof,
                            "receipt_evidence": {**proof, "embedded_batch": batch["records_file"]},
                            "archive_evidence": archive})
        return sorted(out, key=lambda b: (b["receipt"].get("started_at", ""),
                                         b["receipt"].get("run_id", ""),
                                         b["receipt"].get("range_start", 0)))


def acquired_files(catalog):
    """Keep origin URLs; a deduplicated file's top-level URL can belong elsewhere."""
    by_origin = defaultdict(list)
    for record in catalog:
        for file in record.get("files", []):
            for origin in file.get("source_records") or [record]:
                source, sid = origin.get("source"), origin.get("source_id")
                copy = dict(file)
                copy["url"] = origin.get("url") or file.get("url")
                # Older merged catalogs do not preserve a member per origin.
                # When it differs from the top-level URL, do not guess a member.
                if copy["url"] != file.get("url"):
                    copy["archive_member"] = origin.get("archive_member")
                by_origin[source, sid].append(copy)
    return by_origin


def pinned_equivalent(old, current):
    """Only immutable Git bytes permit evidence reuse across discovery hashes."""
    if old.get("source_id") != current.get("source_id") or old.get("source") != current.get("source"):
        return False
    oldfiles, newfiles = old.get("files", []), current.get("files", [])
    if not oldfiles or len(oldfiles) != len(newfiles):
        return False
    def stable(file):
        url = file.get("url", "")
        blob = file.get("upstream_hash")
        return (bool(re.fullmatch(r"https://raw\.githubusercontent\.com/[^/]+/[^/]+/[0-9a-f]{40}/.+", url))
                and isinstance(blob, str) and bool(re.fullmatch(r"git-blob-sha1:[0-9a-f]{40}", blob)))
    return (all(stable(f) for f in oldfiles + newfiles)
            and sorted((descriptor_key(f), f["upstream_hash"]) for f in oldfiles)
            == sorted((descriptor_key(f), f["upstream_hash"]) for f in newfiles))


def latest_attempts(source, inventory, current_sha, batches, old_inventories):
    """Validate indices/identities before using any outcome, including old runs."""
    attempts = {}
    current_ids = {row["source_id"]: index for index, row in enumerate(inventory)}
    for batch in batches:
        receipt = batch["receipt"]
        old_sha = receipt.get("discovery_sha256")
        rows = inventory if old_sha == current_sha else old_inventories.get(old_sha)
        if rows is None:
            continue
        start, end = receipt.get("range_start"), receipt.get("range_end")
        if (type(start) is not int or type(end) is not int
                or not 0 <= start <= end <= len(rows) or receipt.get("discovered") != len(rows)):
            raise ValueError("Invalid original receipt range/count")
        errors = defaultdict(list)
        for failure in batch["failures"]:
            idx = failure.get("source_index")
            if (type(idx) is not int or not start <= idx < end or failure.get("source") != source
                    or failure.get("source_id") != rows[idx]["source_id"]):
                raise ValueError("Failure index/source ID not bound to original inventory")
            errors[idx].append(failure)
        seen = set()
        for disposition in receipt.get("dispositions", []):
            idx = disposition.get("index")
            if (type(idx) is not int or not start <= idx < end or idx in seen
                    or disposition.get("source_id") != rows[idx]["source_id"]):
                raise ValueError("Receipt disposition identity mismatch")
            seen.add(idx)
            target = current_ids.get(rows[idx]["source_id"])
            if target is None or (old_sha != current_sha and not pinned_equivalent(rows[idx], inventory[target])):
                continue
            attempts[target] = {**batch, "failures": errors[idx], "status": disposition.get("status"),
                                "original_source_index": idx, "original_discovery_sha256": old_sha,
                                "evidence_reuse": "same_inventory" if old_sha == current_sha else "same_pinned_git_blob"}
    return attempts


def objective_failure(failure):
    text = failure.get("error", "")
    kind = failure.get("error_type", "")
    if re.search(r"HTTP\s+(401|403|404|410|451)\b", text):
        return "not_acquired"
    if kind == "MediaValidationError":
        # Mechanical incompatibility is sufficient for quarantine. It is not a
        # claim that an unsupported structure is malicious or scientifically wrong.
        return "quarantine"
    if re.search(r"empty (source|file|response)|source (is|was) empty|No bytes", text, re.I):
        return "quarantine"
    return None  # Timeouts/429/5xx and unidentified problems remain pending.


def prepare_source(source, snapshot, inventory, available, attempts, inventory_proof,
                   *, public_available=None, scope_policy=None, scope_evidence=None):
    groups = defaultdict(list)
    pending = []
    for index, item in enumerate(inventory):
        sid = item["source_id"]
        files = available.get((source, sid), [])
        public_files = files if public_available is None else public_available.get((source, sid), [])
        acquired_keys = {descriptor_key(file) for file in files}
        missing = [f for f in item.get("files", []) if descriptor_key(f) not in acquired_keys]
        common = {"source_id": sid, "source_index": index, "title": item.get("title")}
        attempt = attempts.get(index)
        if files and not public_files:
            scopes = [evaluate_public_scope(item, file, scope_policy) for file in files] if scope_policy else []
            if scopes and all(not value["public_allowed"] for value in scopes):
                groups["items", "exclude_from_v1"].append({**common,
                    "reason": "All acquired originals of this exact source item are excluded or deferred by file-bound scope review; no public counterpart remains.",
                    "evidence": scope_evidence or scopes[0]["evidence"][0],
                    "acquired_original_sha256": sorted({f["sha256"] for f in files}),
                    "scope_decisions": scopes,
                    "unacquired_descriptors": [{"url": f.get("url"), "format": f.get("format"),
                                                "upstream_archive_member": f.get("archive_member")} for f in missing],
                    "source_download_requested": False})
            else:
                pending.append({**common,
                    "reason": "Internal acquired files are absent from the public catalog without matching scope evidence; inspect assembly before deciding.",
                    "acquired_original_sha256": sorted({f["sha256"] for f in files})})
            continue
        if not item.get("files"):
            if item.get("scope_status") == "excluded":
                continue  # Already recorded by the collector; do not double count.
            image_types = set(item.get("upstream_image_types") or [])
            if source == "nih-bioart" and image_types and image_types <= {"Swatch", "Templates", "Brush"}:
                groups["items", "exclude_from_v1"].append({**common,
                    "reason": "Official preset/document type has no supported SVG/PNG illustration descriptor.",
                    "upstream_image_types": sorted(image_types), "evidence": inventory_proof,
                    "fetch_attempted": False})
            else:
                pending.append({**common, "reason": "No artwork descriptors; source scope requires review."})
            continue
        if not missing:
            continue
        if attempt and attempt["status"] == "acquired":
            pending.append({**common, "reason": "A later verified receipt acquired this item; refresh assembly before deciding.",
                            "evidence": attempt["receipt_evidence"]})
            continue
        resolved = []
        for file in missing:
            row = {**common, "url": file.get("url"), "format": file.get("format"),
                   "upstream_archive_member": file.get("archive_member")}
            candidates = []
            if attempt:
                for failure in attempt["failures"]:
                    if failure.get("url") != file.get("url"):
                        continue
                    if "upstream_archive_member" in failure and failure["upstream_archive_member"] != file.get("archive_member"):
                        continue
                    if failure.get("format") and failure["format"] != file.get("format"):
                        continue
                    # Legacy rows lack member/format. A URL shared by two absent
                    # ZIP members cannot identify which member failed.
                    ambiguous = [f for f in missing if f.get("url") == failure.get("url")
                                 and (not failure.get("format") or f.get("format") == failure["format"])]
                    if "upstream_archive_member" not in failure and len(ambiguous) != 1:
                        continue
                    candidates.append(failure)
            failure = candidates[-1] if candidates else None
            disposition = objective_failure(failure) if failure else None
            if not disposition:
                pending.append({**row, "reason": failure.get("error") if failure else "No unambiguous failed descriptor evidence; not inferred from counts.",
                                "evidence": attempt["evidence"] if attempt else inventory_proof})
                continue
            row.update({"reason": failure["error"], "error_type": failure.get("error_type"),
                        "evidence": attempt["evidence"], "receipt_evidence": attempt["receipt_evidence"],
                        "archive_evidence": attempt["archive_evidence"], "failure_run_id": attempt["receipt"]["run_id"],
                        "failure_discovery_sha256": attempt["original_discovery_sha256"],
                        "evidence_reuse": attempt["evidence_reuse"]})
            if files:
                support = sorted(public_files, key=lambda f: (f.get("validation", {}).get("status") != "machine_checked_and_rendered", f["sha256"]))[0]
                row["available_file_sha256"] = support["sha256"]
                row["available_file_archive"] = support.get("archive")
                row["retained_file_sha256"] = sorted({f["sha256"] for f in public_files})
                groups["unavailable_variants", disposition].append(row)
            else:
                resolved.append(row)
        if not files and resolved:
            if len(resolved) == len(missing):
                groups["items", "quarantine"].append({**common,
                    "reason": "No usable original was acquired; exact failed descriptors are recorded below.",
                    "evidence": resolved[0]["evidence"], "unavailable_files": resolved})
            else:
                pending.append({**common, "reason": "Some descriptors remain unresolved; whole-item quarantine withheld.",
                                "objective_failed_descriptors": resolved})
    return groups, pending


def review_files(reviews, available, snapshots):
    groups = defaultdict(list)
    pending = []
    for review, proof in reviews:
        for item in review.get("items", []):
            source, sid = item.get("source", review.get("source")), item.get("source_id")
            sha = item.get("sha256") or item.get("original_sha256")
            found = [f for f in available.get((source, sid), []) if f.get("sha256") == sha]
            if not found:
                pending.append({"source": source, "source_id": sid, "sha256": sha,
                                "reason": "Review file identity is absent from this assembly."})
                continue
            action = item.get("disposition")
            if action not in {"quarantine", "exclude_from_v1"}:
                # A scope_pending observation is not silently converted into a
                # claim that the original is a photograph or fails scientific QA.
                if item.get("visual_verdict") == "scope_pending" or action == "scope_pending":
                    pending.append({"source": source, "source_id": sid, "sha256": sha,
                                    "reason": item.get("reason") or item.get("observations"), "evidence": proof,
                                    "state": "scope_pending"})
                continue
            if not item.get("reason") or not item.get("reviewed_by") or not item.get("reviewed_at"):
                raise ValueError("Exact-file exclusion requires reason and named review")
            if source not in snapshots:
                raise ValueError("Reviewed source absent from assembly")
            groups[source, "files", action].append({"source_id": sid, "sha256": sha,
                "reason": item["reason"], "evidence": proof,
                "reviewed_by": item["reviewed_by"], "reviewed_at": item["reviewed_at"],
                "scope_status": item.get("scope_status"), "supporting_evidence": item.get("evidence", [])})
    return groups, pending


def failed_previews(available, snapshots, catalog_proof):
    groups = defaultdict(list)
    seen = set()
    for (source, sid), files in available.items():
        if source not in snapshots:
            continue
        for file in files:
            if file.get("validation", {}).get("status") != "preview_failed" or file["sha256"] in seen:
                continue
            seen.add(file["sha256"])
            groups[source, "files", "quarantine"].append({"source_id": sid, "sha256": file["sha256"],
                "reason": "Stored original has an explicitly failed preview; retain original archive and defer publication until faithful rendering is verified.",
                "validation_findings": file.get("validation", {}).get("findings", []),
                "archive": file.get("archive"), "evidence": catalog_proof,
                "source_download_requested": False})
    return groups


def make_documents(groups, snapshots, bindings, *, reviewed_at, reviewed_by, historical=None):
    decisions = {"schema_version": 1, "template_only": False, **bindings, "decisions": [],
                 "preparation_status": "bound_to_input_assembly_only", "final_acceptance_claimed": False}
    selections = {}
    for (source, kind, disposition), items in sorted(groups.items()):
        if not items:
            continue
        name = f"{source}.{kind}.{disposition}.json"
        selection = {"schema_version": 1, "source": source,
                     "discovery_sha256": snapshots[source]["discovery_sha256"], "items": items}
        sha = digest(json_bytes(selection))
        selections[name] = selection
        decisions["decisions"].append({"decision_id": f"{source}-{kind}-{disposition}",
            "review_state": "decided", "target": {"kind": kind, "source": source,
                "discovery_sha256": selection["discovery_sha256"],
                "selection_manifest": {"uri": "selections/" + name, "local_path": "selections/" + name, "sha256": sha}},
            "disposition": disposition, "reason": "Exact evidence-bound source dispositions; per-file reasons remain in the selection manifest.",
            "reviewed_by": reviewed_by, "reviewed_at": reviewed_at,
            "evidence": [{"uri": "selections/" + name, "sha256": sha}],
            "impact": {"selected_entries": len(items), "description":
                "Only listed unavailable variants are accounted for; successful counterpart files remain eligible."
                if kind == "unavailable_variants" else "Only the exact listed item/file identities are excluded from this publication subset."},
            "mitigation": {"decision": "Retain failure and review evidence. Revisit after an upstream corrected original or faithful local processing becomes available; validate remaining concept coverage against the unchanged 120-query benchmark."}})
    if historical:
        decisions["decisions"].append(historical)
    return decisions, selections


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("catalog", "assembly-report", "assets", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--metadata-dir", type=Path, action="append", default=[])
    parser.add_argument("--internal-catalog", type=Path,
                        help="Required with a scope-filtered assembly; exact full diagnostic catalog, never a public index input")
    parser.add_argument("--fetch-metadata", action="store_true")
    parser.add_argument("--source", action="append", default=[])
    parser.add_argument("--file-review", type=Path, action="append", default=[])
    parser.add_argument("--benchmark", type=Path, default=ROOT / "data/benchmark.json")
    parser.add_argument("--registry", type=Path, default=ROOT / "data/sources.json")
    parser.add_argument("--repository", default="hujizhou35-cmd/MGA-resource")
    parser.add_argument("--reviewed-by", default="Codex ingestion_adapters")
    args = parser.parse_args(argv)
    catalog_bytes = args.catalog.read_bytes()
    catalog = [json.loads(line) for line in catalog_bytes.splitlines() if line.strip()]
    assembly = read_json(args.assembly_report)
    sha = digest(catalog_bytes)
    if assembly.get("summary", {}).get("catalog_sha256") != sha:
        raise ValueError("Assembly report does not bind the supplied catalog bytes")
    hashes = {"catalog_sha256": sha, "assembly_report_sha256": digest(args.assembly_report.read_bytes()),
              "benchmark_sha256": digest(args.benchmark.read_bytes()), "mapping_sha256": digest(json_bytes({}))}
    snapshots = {row["source"]: row for row in assembly["source_snapshots"]}
    registry = read_json(args.registry)["sources"]
    inputs = Metadata(read_json(args.assets), args.metadata_dir, repository=args.repository,
                      tag=assembly["collection_release"], cache=args.output / "input-metadata", fetch=args.fetch_metadata)
    assembly_proofs = {}
    for path, actual_hash in ((args.catalog, sha), (args.assembly_report, hashes["assembly_report_sha256"])):
        proof = inputs.proof(path.name)
        if proof["sha256"] != actual_hash or proof["size_bytes"] != path.stat().st_size:
            raise ValueError("Assembly input bytes disagree with GitHub digest/size")
        assembly_proofs[path.name] = proof
    internal = catalog
    internal_binding = assembly.get("public_scope", {}).get("internal_catalog")
    if internal_binding:
        if not args.internal_catalog:
            raise ValueError("Scope-filtered assembly requires --internal-catalog to distinguish scope exclusions from missing acquisition")
        data = args.internal_catalog.read_bytes()
        internal = [json.loads(line) for line in data.splitlines() if line.strip()]
        proof = inputs.proof(args.internal_catalog.name)
        if (digest(data) != internal_binding.get("sha256") or digest(data) != proof["sha256"]
                or len(data) != internal_binding.get("size_bytes") or len(data) != proof["size_bytes"]
                or len(internal) != internal_binding.get("records")
                or any(r.get("catalog_visibility") != "internal_diagnostic" for r in internal)):
            raise ValueError("Internal catalog identity/count/hash does not match the assembly and GitHub")
        assembly_proofs[args.internal_catalog.name] = proof
        hashes["internal_catalog_sha256"] = digest(data)
    elif args.internal_catalog:
        raise ValueError("Assembly report does not bind an internal catalog")
    available = acquired_files(internal)
    public_available = acquired_files(catalog)
    scope_policy = load_scope_policy()
    groups, pending, inventory_proofs = defaultdict(list), [], {}
    sources = args.source or [s for s in ("bioicons", "nih-bioart", "reactome", "scidraw", "togo") if s in snapshots]
    for source in sources:
        snapshot = snapshots[source]
        rows, proof = inputs.inventory(source, snapshot["discovery_sha256"], snapshot["discovered"])
        inventory_proofs[source] = proof
        batches = inputs.batches(source, snapshot["discovery_sha256"], historical=source == "bioicons")
        old = {}
        if source == "bioicons":
            for batch in batches:
                oldsha = batch["receipt"]["discovery_sha256"]
                if oldsha != snapshot["discovery_sha256"] and oldsha not in old:
                    old[oldsha] = inputs.inventory(source, oldsha)[0]
        attempts = latest_attempts(source, rows, snapshot["discovery_sha256"], batches, old)
        selected, unresolved = prepare_source(source, snapshot, rows, available, attempts, proof,
            public_available=public_available, scope_policy=scope_policy,
            scope_evidence=assembly_proofs[args.assembly_report.name])
        for (kind, action), items in selected.items():
            groups[source, kind, action].extend(items)
        pending.extend({"source": source, **row} for row in unresolved)
    review_inputs = [(read_json(path), {"uri": path.resolve().as_uri(), "sha256": digest(path.read_bytes())}) for path in args.file_review]
    reviewed, unresolved = review_files(review_inputs, available, snapshots)
    for key, rows in reviewed.items():
        groups[key].extend(rows)
    pending.extend(unresolved)
    for key, rows in failed_previews(available, snapshots, assembly_proofs[args.catalog.name]).items():
        groups[key].extend(rows)
    # This is a recorded historical denial, not a fresh network observation.
    historical = None
    bio = next((s for s in registry if s["source_id"] == "biocartoon"), {})
    now = datetime.now(timezone.utc).isoformat()
    reviewer = {"kind": "agent", "id": args.reviewed_by}
    if bio.get("status") == "blocked_access":
        historical = {"decision_id": "biocartoon-historical-recorded-denial", "review_state": "decided",
            "target": {"kind": "source", "source": "biocartoon", "discovery_sha256": None},
            "observed_state": "historical_recorded_denial", "disposition": "not_acquired",
            "reason": "Existing registry records catalog HTTP 403. No request or bypass was performed by this preparation.",
            "reviewed_by": reviewer, "reviewed_at": now,
            "evidence": [{"uri": args.registry.resolve().as_uri(), "sha256": digest(args.registry.read_bytes()),
                          "kind": "historical_registry_record", "source_id": "biocartoon"}],
            "impact": {"inventory_count": None, "description": "Unacquired aggregate catalog; unique content and quantity remain unknown."},
            "mitigation": {"decision": "Use independently acquired original libraries and the unchanged benchmark to test coverage. Revisit only with new official access evidence."}}
    decisions, selections = make_documents(groups, snapshots,
        {"release_id": assembly["collection_release"], "input_catalog_sha256": sha,
         "benchmark_sha256": hashes["benchmark_sha256"]}, reviewed_at=now, reviewed_by=reviewer, historical=historical)
    by_sha = {digest(json_bytes(value)): value for value in selections.values()}
    # Reuse the acceptance checker's exact publication projection. Empty review
    # inputs deliberately cannot produce acceptance; no visual judgments invented.
    manifest, check = evaluate_release(catalog, assembly, read_json(args.benchmark), {}, {},
        hashes=hashes, registry=registry, decisions=decisions, selection_manifests=by_sha)
    decisions["publish_manifest"] = {"uri": "candidate_publish_manifest.json", "sha256": digest(json_bytes(manifest))}
    invalid = [row for row in check["missing"] if row["code"] in {
        "decisions.incomplete", "decisions.binding", "benchmark.frozen", "scope.policy_binding"}]
    if invalid:
        raise ValueError(f"Generated decisions failed contract: {invalid}")
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "selections").mkdir(exist_ok=True)
    for name, value in selections.items():
        (args.output / "selections" / name).write_bytes(json_bytes(value))
    (args.output / "decisions.json").write_bytes(json_bytes(decisions))
    (args.output / "candidate_publish_manifest.json").write_bytes(json_bytes(manifest))
    report = {"schema_version": 1, "final_acceptance_claimed": False,
              "preparation_status": "bound_to_input_assembly_only", "bindings": hashes,
              "input_assets_metadata_sha256": digest(args.assets.read_bytes()), "inventory_proofs": inventory_proofs,
              "assembly_evidence": assembly_proofs,
              "metadata_evidence": inputs.used, "selected": {"/".join(k): len(v) for k, v in groups.items()},
              "ignored_legacy_metadata": inputs.ignored,
              "pending_count": len(pending), "pending": pending,
              "note": "Re-run after latest sources/Togo assembly. Counts describe dispositions, not scientific or visual acceptance; benchmark is unchanged."}
    (args.output / "preparation_report.json").write_bytes(json_bytes(report))
    print(json.dumps({"selected": report["selected"], "pending": len(pending), "final_acceptance_claimed": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
