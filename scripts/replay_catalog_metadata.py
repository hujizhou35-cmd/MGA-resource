"""Cloud-only search replay using catalog metadata, never artwork archives.

An existing, immutable baseline diagnostic supplies the old parsed requirements.
Committed per-result reviews supply the old exact preview/archive bindings.
This tool records differences and behavior; it never assigns visual acceptance.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from assemble import gh, list_release_assets
from diagnose_search import diagnose, resolve_index
from validate_search_behavior import validate as validate_behavior

BASELINE_REVIEW = ROOT / "data/relevance-reviews/frozen-3.4.1-merged-120.json"
VERSION_EVIDENCE = ROOT / "data/diagnostics/search-filter-3.4.3-replay.json"
BENCHMARK = ROOT / "data/benchmark.json"
SEED = "diagnostic-fixed-project"
REQUIREMENT_FIELDS = ("query", "expected_concepts", "required_species", "visual_requirements", "exclude_matches")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while block := stream.read(1024 * 1024):
            value.update(block)
    return value.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def download_metadata(name, asset, destination, repository, tag):
    """Only these metadata filenames can reach the remote download command."""
    allowed = re.fullmatch(r"assembly-[0-9]+-[0-9]+\.(catalog\.jsonl|catalog\.sqlite|report\.json)", name)
    baseline = re.fullmatch(r"search-baseline-[0-9]+-3\.4\.3\.search-diagnostic\.json", name)
    require(bool(allowed or baseline), "Only exact catalog/index/report or baseline diagnostic metadata may be downloaded")
    require(asset.get("name") == name and re.fullmatch(r"sha256:[a-f0-9]{64}", asset.get("digest", "")), "Metadata asset identity/digest missing")
    require(type(asset.get("size")) is int and 0 < asset["size"] < 1024 * 1024 * 1024, "Metadata asset size is invalid")
    destination.mkdir(parents=True, exist_ok=True)
    gh("release", "download", tag, "--repo", repository, "--pattern", name, "--dir", str(destination), "--clobber")
    path = destination / name
    require(path.stat().st_size == asset["size"] and "sha256:" + digest(path) == asset["digest"], "Downloaded metadata differs from GitHub digest/size")
    return path, {"name": name, "url": asset["browser_download_url"], "release_tag": tag,
                  "sha256": digest(path), "size_bytes": path.stat().st_size, "kind": "metadata"}


def unique_queries(rows, key):
    result = {row[key]: row for row in rows}
    require(len(result) == len(rows), "Duplicate query identity")
    return result


def verify_producer(repository, prefix, core_sha):
    """The index-producing workflow must have used these exact shared rules."""
    run_id = prefix.split("-")[1]
    run = json.loads(gh("api", f"repos/{repository}/actions/runs/{run_id}"))
    require(run.get("status") == "completed" and run.get("conclusion") == "success", "Input metadata producer did not finish successfully")
    commit = run["head_sha"]
    content = json.loads(gh("api", f"repos/{repository}/contents/mga_resources/search-core.mjs?ref={commit}"))
    require(content.get("encoding") == "base64", "Producer core bytes unavailable")
    producer_sha = hashlib.sha256(base64.b64decode(content["content"])).hexdigest()
    require(producer_sha == core_sha, "Index-producing workflow used different search-core bytes")
    return {"run_id": run_id, "url": run["html_url"], "code_commit": commit, "search_core_sha256": producer_sha}


def validate_baseline(baseline, review, version, benchmark, *, baseline_sha, benchmark_sha, core_sha):
    """Verify the published old diagnostic and its committed visual-review chain."""
    replay = version["original120_replay"]
    require(baseline_sha == replay["replay_bindings"]["new_diagnostic_sha256"], "Baseline diagnostic is not the recorded original replay")
    require(baseline["input_catalog_sha256"] == review["bindings"]["catalog_sha256"] == version["catalog_sha256"], "Baseline catalog chain differs")
    require(baseline["benchmark_sha256"] == review["bindings"]["benchmark_sha256"] == version["benchmark_sha256"] == benchmark_sha, "Original benchmark changed")
    require(baseline["search_core_sha256"] == version["search_core_sha256"] == core_sha, "Search core differs from the fixed baseline policy")
    require(baseline["ranking_version"] == version["ranking_version"], "Baseline ranking version differs")
    require(review["bindings"]["search_core_sha256"] == replay["old_core_sha256"]
            and review["bindings"]["diagnostic_sha256"] == replay["replay_bindings"]["old_diagnostic_sha256"], "Original visual-review replay chain differs")
    require(replay["ordered_family_original_rank_lists_changed"] == replay["hard_requirement_sets_changed"] == 0
            and replay["seed"] == SEED, "Historical replay does not establish identity")
    old = unique_queries(baseline["queries"], "id")
    observed = unique_queries(review["queries"], "query_id")
    fixed = unique_queries(benchmark["queries"], "id")
    require(set(old) == set(observed) == set(fixed), "Baseline query coverage differs")
    positions = 0
    for qid, query in fixed.items():
        require(all(old[qid].get(key) == query.get(key) for key in REQUIREMENT_FIELDS), "Baseline request/requirements changed: " + qid)
        require(observed[qid]["query"] == query["query"], "Reviewed request differs: " + qid)
        rows = observed[qid]["result_judgments"]
        require([row["rank"] for row in rows] == list(range(1, len(rows) + 1)), "Old ranks are not complete and ordered")
        require([row["family_id"] for row in rows] == old[qid]["shared_ids"]
                and [row["original_sha256"] for row in rows] == old[qid]["selected_file_sha256"], "Old diagnostic and per-result review differ: " + qid)
        positions += len(rows)
    require(positions == replay["result_positions"] and len(old) == replay["queries"], "Historical replay counts differ")


def bind_candidates(catalog_path, diagnostic):
    """Bind each returned variant to the exact current family/file/preview data."""
    wanted = {(row["family_id"], row["selected_file"]["sha256"])
              for query in diagnostic["queries"] for row in query["exact_results"]}
    found = {}
    with Path(catalog_path).open(encoding="utf-8") as stream:
        for line in stream:
            record = json.loads(line)
            for index, file in enumerate(record["files"]):
                key = record["family_id"], file.get("sha256")
                if key in wanted:
                    require(key not in found, "Ambiguous family/original binding")
                    found[key] = (record["id"], index, file)
    require(set(found) == wanted, "Returned candidate absent from current catalog")
    for query in diagnostic["queries"]:
        bindings = query["search_bindings"]
        require(bindings["catalog_version"] == diagnostic["input_catalog_sha256"]
                and bindings["ranking_version"] == diagnostic["ranking_version"] and bindings["seed"] == SEED,
                "Returned query version/seed binding differs")
        require(bindings["query_plan"]["requirements"] == query["requirements"], "Returned hard requirements differ")
        rows = query["exact_results"]
        require([r["rank"] for r in rows] == list(range(1, len(rows) + 1)) and len(rows) <= 10,
                "New ranks are not a complete top-10 sequence")
        require(len({r["family_id"] for r in rows}) == len(rows), "Duplicate family in returned page")
        require([r["family_id"] for r in rows] == query["shared_ids"]
                and [r["selected_file"]["sha256"] for r in rows] == query["selected_file_sha256"], "Exported results differ from diagnostic IDs")
        for row in rows:
            asset_id, index, file = found[(row["family_id"], row["selected_file"]["sha256"])]
            require(row["asset_id"] == asset_id and all(file.get(k) == value for k, value in row["selected_file"].items()), "Selected-file metadata differs from current catalog")
            require(file.get("public_scope", {}).get("public_allowed") is not False
                    and file.get("preview") == row["preview"] and file.get("archive") == row["download"], "Selected preview/download/scope differs")
            source = row["selected_source"]
            source_keys = ["source", "source_id", "attribution"]
            # Current core returns three identity fields; the full exact-file
            # rights records were checked above. Validate any explicit extra
            # source facts as well, rather than silently ignoring a forged one.
            source_keys.extend(key for key in ("license", "source_url", "author") if key in source)
            require(any(all(origin.get(key) == source.get(key) for key in source_keys)
                        for origin in file.get("source_records", [])), "Selected source is not a provenance record of this exact file")
            row["candidate_id"] = f"{asset_id}:file-{index}"
    return diagnostic


def identity(row):
    return row["family_id"], row["original_sha256"], row["preview_sha256"]


def compact_result(row, *, old=False):
    if old:
        return {key: row[key] for key in ("rank", "candidate_id", "family_id", "original_sha256", "preview_sha256", "original_archive", "preview_archive")}
    return {"rank": row["rank"], "candidate_id": row["candidate_id"], "variant_id": row["variant_id"],
        "family_id": row["family_id"], "original_sha256": row["selected_file"]["sha256"],
        "preview_sha256": row["preview"]["sha256"], "original_archive": row["selected_file"]["archive"],
        "preview_archive": row["preview"]["archive"]}


def compare_queries(baseline, review, current):
    old = unique_queries(baseline["queries"], "id")
    observed = unique_queries(review["queries"], "query_id")
    new = unique_queries(current["queries"], "id")
    require(set(old) == set(observed) == set(new), "Replay query coverage differs")
    changes, counts = [], Counter()
    for qid, query in new.items():
        require(all(old[qid].get(k) == query.get(k) for k in REQUIREMENT_FIELDS), "Fixed benchmark requirement fields changed: " + qid)
        before = [compact_result(row, old=True) for row in observed[qid]["result_judgments"]]
        after = [compact_result(row) for row in query["exact_results"]]
        left, right = {identity(row): row for row in before}, {identity(row): row for row in after}
        require(len(left) == len(before) and len(right) == len(after), "Duplicate exact candidate identity")
        added = [row for row in after if identity(row) not in left]
        removed = [row for row in before if identity(row) not in right]
        moved = [{"old": left[identity(row)], "new": row} for row in after
                 if identity(row) in left and row["rank"] != left[identity(row)]["rank"]]
        rebound = [{"old": left[identity(row)], "new": row} for row in after if identity(row) in left
                   and any(row[k] != left[identity(row)][k] for k in ("candidate_id", "original_archive", "preview_archive"))]
        hard_changed = old[qid]["requirements"] != query["requirements"]
        changed = bool(added or removed or moved or rebound or hard_changed)
        counts.update(queries_changed=int(changed), queries_unchanged=int(not changed), added=len(added),
                      removed=len(removed), moved=len(moved), binding_changes=len(rebound), requirements_changed=int(hard_changed))
        changes.append({"query_id": qid, "query": query["query"], "category": query["category"], "language": query["language"],
            "old": before, "new": after, "added": added, "removed": removed, "moved": moved, "binding_changes": rebound,
            "requirements": {"benchmark": {k: query.get(k) for k in REQUIREMENT_FIELDS},
                             "old_parsed": old[qid]["requirements"], "new_parsed": query["requirements"], "changed": hard_changed},
            "unchanged_exact_file_candidates": [row["candidate_id"] for row in after if identity(row) in left
                and all(row[k] == left[identity(row)][k] for k in ("original_archive", "preview_archive"))],
            "visual_relevance_pass": None, "formal_query_pass": None})
    return {"schema_version": 1, "status": "actual_metadata_replay_diff_not_acceptance", "seed": SEED,
            "queries": changes, "summary": {"queries": len(changes), **counts},
            "note": "Old visual observations remain separately bound. Matching bytes or a moved rank do not grant a new formal query pass; newly returned files require query-specific review."}


def package_metadata(directory, destination):
    files = sorted(path for path in directory.rglob("*") if path.is_file())
    require(files and all(path.suffix == ".json" and not path.is_symlink() for path in files), "Only generated JSON evidence may enter the output package")
    require(sum(path.stat().st_size for path in files) < 64 * 1024 * 1024, "Generated metadata evidence exceeds the bounded package size")
    with tarfile.open(destination, "w:gz") as archive:
        for path in files:
            # Behavior evidence URIs are repository-relative; preserve that
            # layout so an extracted metadata pack resolves the same proofs.
            member = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path.relative_to(directory)
            archive.add(path, arcname=member.as_posix(), recursive=False)
    return {"sha256": digest(destination), "size_bytes": destination.stat().st_size, "members": len(files), "artwork_members": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--assembly-prefix", required=True)
    parser.add_argument("--baseline-release-tag", default="acceptance-20261107")
    parser.add_argument("--baseline-asset", default="search-baseline-42289434868-3.4.3.search-diagnostic.json")
    parser.add_argument("--publish-tag", default="acceptance-20261107")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    if os.environ.get("GITHUB_ACTIONS") != "true":
        parser.error("Real catalog replay runs in cloud CI; local use is limited to pure helper tests")
    require(re.fullmatch(r"assembly-[0-9]+-[0-9]+", args.assembly_prefix), "Use an exact assembly prefix")
    repository = os.environ["GITHUB_REPOSITORY"]
    run_id = os.environ["GITHUB_RUN_ID"] + "-" + os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    base = ROOT / "build/search-metadata-replay" / run_id
    incoming, output = base / "input", base / "evidence"
    output.mkdir(parents=True, exist_ok=True)
    assets = list_release_assets(repository, args.release_tag)
    paths, receipts = {}, []
    for suffix in ("catalog.jsonl", "catalog.sqlite", "report.json"):
        name = args.assembly_prefix + "." + suffix
        paths[suffix], receipt = download_metadata(name, assets[name], incoming, repository, args.release_tag)
        receipts.append(receipt)
    baseline_assets = assets if args.baseline_release_tag == args.release_tag else list_release_assets(repository, args.baseline_release_tag)
    baseline_path, receipt = download_metadata(args.baseline_asset, baseline_assets[args.baseline_asset], incoming, repository, args.baseline_release_tag)
    receipts.append(receipt)
    assembly, baseline, review, version, benchmark = read(paths["report.json"]), read(baseline_path), read(BASELINE_REVIEW), read(VERSION_EVIDENCE), read(BENCHMARK)
    catalog_sha, core_sha, benchmark_sha = digest(paths["catalog.jsonl"]), digest(ROOT / "mga_resources/search-core.mjs"), digest(BENCHMARK)
    require(assembly["run_id"] == args.assembly_prefix.removeprefix("assembly-") and assembly["summary"]["catalog_sha256"] == catalog_sha, "Input report/catalog/prefix binding differs")
    require(len(benchmark["queries"]) == 120, "The original 120-query benchmark must be preserved")
    validate_baseline(baseline, review, version, benchmark, baseline_sha=digest(baseline_path), benchmark_sha=benchmark_sha, core_sha=core_sha)
    producer = verify_producer(repository, args.assembly_prefix, core_sha)
    resolve_index(paths["catalog.jsonl"], output, paths["catalog.sqlite"])
    with closing(sqlite3.connect(paths["catalog.sqlite"].resolve().as_uri() + "?mode=ro", uri=True)) as db:
        require(db.execute("PRAGMA quick_check").fetchone()[0] == "ok", "Input SQLite integrity check failed")
    diagnostic = diagnose(paths["catalog.jsonl"], BENCHMARK, output / "diagnostic", paths["catalog.sqlite"], include_results=True)
    require(diagnostic["search_core_sha256"] == core_sha and diagnostic["sqlite_sha256"] == digest(paths["catalog.sqlite"]), "Replay core/index binding changed")
    bind_candidates(paths["catalog.jsonl"], diagnostic)
    write(output / "diagnostic/search-diagnostic.json", diagnostic)
    differences = compare_queries(baseline, review, diagnostic)
    differences["bindings"] = {"old_catalog_sha256": baseline["input_catalog_sha256"], "new_catalog_sha256": catalog_sha,
        "benchmark_sha256": benchmark_sha, "search_core_sha256": core_sha, "ranking_version": diagnostic["ranking_version"],
        "baseline_diagnostic_sha256": digest(baseline_path), "baseline_visual_review_sha256": digest(BASELINE_REVIEW),
        "historical_zero_difference_evidence_sha256": digest(VERSION_EVIDENCE),
        "current_diagnostic_sha256": digest(output / "diagnostic/search-diagnostic.json")}
    write(output / "original120-diff.json", differences)
    # Do not retag the old adoption ledger. Candidate exercises remain unadopted.
    behavior = validate_behavior(paths["catalog.jsonl"], paths["report.json"], output / "behavior", index_path=paths["catalog.sqlite"])
    functional = {row["id"]: row["passed"] for row in behavior["functional_cases"]}
    required_cases = {"variant_before_family", "hard_vs_preference", "unknown_is_not_match", "stable_replay",
                      "pagination_no_duplicate", "pins_stable", "adopted_history_only", "modes_scientific_constraints", "no_curated_boost"}
    complete = set(functional) == required_cases and all(functional.values())
    summary = {"schema_version": 1, "status": "metadata_replay_completed_not_visual_acceptance", "run_id": run_id,
        "code_commit": os.environ.get("GITHUB_SHA"), "generated_at": datetime.now(timezone.utc).isoformat(),
        "assembly_prefix": args.assembly_prefix, "release_tag": args.release_tag, "publish_tag": args.publish_tag,
        "bindings": differences["bindings"], "inputs": receipts, "original120_diff": differences["summary"],
        "input_metadata_producer": producer,
        "functional_cases": functional, "functional_checks_complete_and_passed": complete,
        "candidate_pools": {row["pool_kind"]: len(row["eligible_family_ids"]) for row in behavior["diversity_scenarios"]},
        "actual_adoption_reuse": "pending_exact_file_and_new_pool_confirmation; old ledger left unchanged",
        "original_archive_reads": 0, "preview_archive_reads": 0, "new_visual_judgments": False,
        "original120_relevance_score": None, "acceptance_passed": False}
    write(output / "summary.json", summary)
    pack = base / f"search-metadata-replay-{run_id}.tar.gz"
    pack_receipt = package_metadata(output, pack)
    summary["metadata_package"] = {"name": pack.name, **pack_receipt}
    published_summary = base / f"search-metadata-replay-{run_id}.summary.json"
    write(published_summary, summary)
    if args.publish:
        gh("release", "upload", args.publish_tag, "--repo", repository, str(pack), str(published_summary))
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    if not complete:
        raise SystemExit("Metadata evidence published; one or more functional checks are missing or failed")


if __name__ == "__main__":
    main()
