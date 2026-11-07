"""Bind explicit reviewer confirmations to unchanged frozen query results.

This command merges evidence. It does not choose artwork, make scientific or
license judgments, change search rankings, or weaken release acceptance gates.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_publish_pack import validate_remote_restore
from prepare_acceptance_evidence import validate_candidate


def require(ok, message):
    if not ok:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def merge(base, mapping, benchmark, manifest, documents, bindings):
    """documents contains (file-reference, parsed-document) pairs."""
    base_queries = {q["query_id"]: q for q in base["queries"]}
    requirements = {q["id"]: q for q in benchmark["queries"]}
    require(set(base_queries) == set(requirements), "Base query coverage changed")
    files = {f["sha256"]: f for f in manifest["files"]}
    updates, references = {}, []
    flags = ("source_evidence_checked", "license_checked", "species_matches", "original_download_checked")
    copied = ("pass", "selected_candidate_ids", "visual_evidence_notes", "gap_reason", "source_checks",
              "species_requirement", "pending_checks", *flags)
    for reference, document in documents:
        require(document.get("independent_from_metadata_matcher") is True
                and document.get("reviewer") and document.get("reviewed_at"), "Missing identified reviewer")
        require(all(document.get("bindings", {}).get(k) == v for k, v in bindings.items()), "Confirmation input binding changed")
        references.append({**reference, "reviewer": document["reviewer"], "reviewed_at": document["reviewed_at"]})
        for confirmation in document["queries"]:
            qid = confirmation["query_id"]
            require(qid in base_queries and qid not in updates, "Unknown or duplicate query confirmation")
            old, expected = base_queries[qid], requirements[qid]
            selected = confirmation.get("selected_candidate_ids", [])
            require(isinstance(selected, list) and len(selected) == len(set(selected)), "Duplicate selection")
            results = {r["candidate_id"]: r for r in old["result_judgments"]}
            require(all(cid in results and results[cid]["verdict"] == "relevant" for cid in selected),
                    "Selection is not an originally reviewed relevant result of this query")
            decision = confirmation.get("pass")
            require(decision is True or decision is False or decision is None, "Invalid decision")
            species = confirmation.get("species_requirement") or {}
            require(species.get("required_species") == expected.get("required_species"), "Species requirement changed")
            require(species.get("applicability") == ("required" if expected.get("required_species") else "not_required"),
                    "Species applicability changed")
            require(bool(species.get("basis")), "Species applicability needs an explicit basis")
            checks = confirmation.get("source_checks") or []
            by_candidate = {c["candidate_id"]: c for c in checks}
            require(len(by_candidate) == len(checks) and set(by_candidate) == set(selected), "Source checks must cover exactly the selections")
            for cid in selected:
                candidate, check = mapping["candidates"][cid], by_candidate[cid]
                item = validate_candidate(candidate, files)
                require(check["original_sha256"] == candidate["original_sha256"]
                        and check["preview_sha256"] == candidate["preview"]["sha256"]
                        and check["original_archive"] == candidate["original_archive"]
                        and check["preview_archive"] == candidate["preview"]["archive"], "Selected file evidence mismatch")
                records = check.get("source_records") or []
                require(bool(records), "Selected file needs exact source records")
                # Keep every frozen provenance field, including author and any
                # species/revision already recorded. Reviewers may append
                # separately evidenced annotations, but cannot rewrite it.
                require(all(any(all(record.get(k) == v for k, v in original.items())
                                and all(k in original or record.get(k) is None for k in ("author", "species"))
                                for original in item["provenance"])
                            and record.get("license") and record.get("attribution") for record in records),
                        "Source/license record does not match frozen provenance")
                pair = old.get("original_verification", {}).get("candidate_objects", {}).get(cid, [])
                require({(p["role"], p["sha256"]) for p in pair} ==
                        {("original", item["sha256"]), ("preview", item["preview"]["sha256"])},
                        "Missing exact restored original/preview association")
            if decision is True:
                require(old.get("visual_relevance_pass") is True and selected
                        and all(confirmation.get(f) is True for f in flags)
                        and confirmation.get("visual_evidence_notes") and not confirmation.get("pending_checks"),
                        "A formal pass needs explicit complete confirmation of an existing visual positive")
            elif decision is False:
                require(bool(confirmation.get("gap_reason")), "A negative decision needs a concrete gap")
            row = copy.deepcopy(old)
            row["prior_formal_state"] = {"pass": old["pass"], "selected_candidate_ids": old["selected_candidate_ids"]}
            row.update({key: copy.deepcopy(confirmation[key]) for key in copied if key in confirmation})
            row["formal_confirmation"] = references[-1]
            row["formal_acceptance_status"] = "confirmed" if decision is True else "specific_gap" if decision is False else "pending"
            require(row["result_judgments"] == old["result_judgments"], "Per-result visual observations changed")
            updates[qid] = row
    require(set(updates) == set(base_queries), "Incomplete formal confirmation coverage")
    output = copy.deepcopy(base)
    output["queries"] = [updates[q["query_id"]] for q in base["queries"]]
    output["reviewer"] = "Named source reviewers; explicit query confirmations merged without changing original visual observations"
    dates = [datetime.fromisoformat(ref["reviewed_at"]) for ref in references]
    output["reviewed_at"] = max(date.replace(tzinfo=timezone.utc) if date.tzinfo is None
                                else date.astimezone(timezone.utc) for date in dates).isoformat()
    output["formal_query_decisions_changed"] = True
    output["formal_confirmation_documents"] = references
    output["note"] = "Explicit confirmations use previously viewed exact files plus source, rights, species applicability and independent recovery evidence. No new search or image inspection is claimed by this merge."
    counts = Counter("pass" if row["pass"] is True else "fail" if row["pass"] is False else "pending" for row in output["queries"])
    audit = {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(), "input_bindings": bindings,
             "confirmation_documents": references, "queries": len(updates), "formal_decisions": dict(counts),
             "visual_positive_queries_unchanged": sum(q["visual_relevance_pass"] for q in output["queries"]),
             "result_positions_unchanged": sum(len(q["result_judgments"]) for q in output["queries"]),
             "rankings_and_per_result_visual_observations_unchanged": True,
             "new_artwork_downloads": 0, "new_visual_inspections": 0, "acceptance_thresholds_changed": False}
    return output, audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, default=ROOT / "build/current-acceptance-evidence-42312769493/judgments.json")
    parser.add_argument("--mapping", type=Path, default=ROOT / "build/current-acceptance-evidence-42312769493/mapping.json")
    parser.add_argument("--benchmark", type=Path, default=ROOT / "data/benchmark.json")
    parser.add_argument("--manifest", type=Path, default=ROOT / "build/cloud-recovery-20261107/frozen-42289434868-3.4.3.publish_manifest.json")
    parser.add_argument("--bundle", type=Path, default=ROOT / "build/cloud-recovery-20261107/build-evidence/qa-20261107-42289434868-3.4.3.bundle.json")
    parser.add_argument("--restore", type=Path, default=ROOT / "build/cloud-recovery-20261107/restore-evidence/qa-20261107-42289434868-3.4.3-42312769493-1.restore_evidence.json")
    parser.add_argument("--confirmation", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    read = lambda p: json.loads(p.read_bytes())
    manifest, bundle, restore = map(read, (args.manifest, args.bundle, args.restore))
    require(validate_remote_restore(restore, bundle, manifest, manifest_sha256=digest(args.manifest), bundle_sha256=digest(args.bundle)),
            "Independent restore failed")
    bindings = {"base_judgments_sha256": digest(args.base), "mapping_sha256": digest(args.mapping),
                "benchmark_sha256": digest(args.benchmark), "manifest_sha256": digest(args.manifest),
                "restore_evidence_sha256": digest(args.restore)}
    documents = []
    for path in args.confirmation:
        doc = read(path)
        for source in doc["source_documents"]:
            require(digest(ROOT / source["path"]) == source["sha256"], "Changed confirmation source: " + source["path"])
        documents.append(({"path": path.resolve().relative_to(ROOT).as_posix(), "sha256": digest(path)}, doc))
    merged, audit = merge(read(args.base), read(args.mapping), read(args.benchmark), manifest, documents, bindings)
    args.output.mkdir(parents=True, exist_ok=True)
    for name, value in (("judgments.json", merged), ("confirmation-audit.json", audit)):
        (args.output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(audit, ensure_ascii=False))


if __name__ == "__main__":
    main()
