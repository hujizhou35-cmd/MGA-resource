"""Run a separately frozen component benchmark in cloud CI, using metadata only.

The shared search core receives only the explicit request. Task/role IDs and
expected scientific/use requirements are review context, never ranking inputs.
This runner records candidates; it does not award query or composition passes.
"""
from __future__ import annotations

import argparse
import copy
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from mga_resources.catalog import search_page
from assemble import gh, list_release_assets
from diagnose_search import export_query_results, resolve_index
from replay_catalog_metadata import digest, download_metadata, package_metadata, read, require, verify_producer, write

REQUEST_FIELDS = ("query", "filters", "limit", "mode", "seed")
REVIEW_FIELDS = ("expected_concepts", "required_species", "visual_requirements", "exclude_matches")
EXTRA_FILE_FIELDS = ("validation", "width", "height", "dimensions", "transparency", "text_content")
MAX_CHILD_QUERIES = 1200


def request_hash(request):
    data = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(data).hexdigest()


def validate_benchmark(benchmark):
    """Validate the frozen graph without executing queries or inspecting assets."""
    require(benchmark.get("schema_version") == 1 and benchmark.get("frozen") is True,
            "Component benchmark must be explicitly frozen before execution")
    require(isinstance(benchmark.get("benchmark_version"), str) and benchmark["benchmark_version"].strip(),
            "A separate component benchmark version is required")
    require(isinstance(benchmark.get("frozen_at"), str) and benchmark["frozen_at"].strip(),
            "Frozen benchmark needs its recorded freeze time")
    datetime.fromisoformat(benchmark["frozen_at"].replace("Z", "+00:00"))
    tasks, queries = benchmark.get("tasks"), benchmark.get("queries")
    require(isinstance(tasks, list) and 1 <= len(tasks) <= 60, "Expected 1 to 60 component tasks")
    require(isinstance(queries, list) and 1 <= len(queries) <= MAX_CHILD_QUERIES,
            "Bounded flat component queries are required")
    by_task, roles = {}, {}
    identifier = lambda value: isinstance(value, str) and re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,127}", value)
    for task in tasks:
        tid = task.get("task_id")
        require(identifier(tid) and tid not in by_task, "Unknown, unsafe or duplicate task ID")
        require(task.get("category") and isinstance(task.get("required_roles"), list)
                and task["required_roles"], "Task category and required roles are required")
        by_task[tid] = task
        for role in task["required_roles"]:
            cid = role.get("component_id")
            require(identifier(cid) and (tid, cid) not in roles, "Unsafe or duplicate required role")
            require(all(key in role for key in REVIEW_FIELDS), "Required role lacks explicit review requirements")
            roles[tid, cid] = role
    seen, coverage, seeds = set(), set(), set()
    for query in queries:
        qid, tid, cid, language = (query.get(key) for key in ("query_id", "task_id", "component_id", "language"))
        require(identifier(qid) and qid not in seen, "Unsafe or duplicate child query ID")
        require((tid, cid) in roles and language in {"zh", "en"}, "Query points to an unknown role/language")
        require((tid, cid, language) not in coverage, "Duplicate required role/language query")
        require(isinstance(query.get("query"), str) and 0 < len(query["query"].strip()) <= 2000,
                "Each child query needs explicit bounded request text")
        require(isinstance(query.get("filters"), dict), "Explicit request filters are required")
        require(type(query.get("limit")) is int and query["limit"] == 10
                and query.get("mode") == "explore" and isinstance(query.get("seed"), str)
                and 0 < len(query["seed"]) <= 200, "Requests must freeze limit 10, explore mode and a seed")
        review = query.get("review_requirements")
        require(isinstance(review, dict) and all(key in review and review[key] == roles[tid, cid][key]
                for key in REVIEW_FIELDS), "Child review requirements differ from their required role")
        seen.add(qid); coverage.add((tid, cid, language)); seeds.add(query["seed"])
    require(coverage == {(tid, cid, language) for tid, cid in roles for language in ("zh", "en")},
            "Every required role must have exactly one query in each language")
    require(len(seeds) == 1, "The initial benchmark must use one explicitly frozen seed")
    return by_task


def execute_queries(database, benchmark, bindings, *, search_fn=None):
    """Keep query identities even when request text repeats across roles/filters."""
    by_task = validate_benchmark(benchmark)
    search_fn = search_fn or search_page
    rows = []
    for index, query in enumerate(benchmark["queries"]):
        request = {key: copy.deepcopy(query[key]) for key in REQUEST_FIELDS}
        page = search_fn(database, **copy.deepcopy(request))
        actual = page["bindings"]
        require(actual["catalog_version"] == bindings["catalog_sha256"]
                and actual["ranking_version"] == bindings["ranking_version"]
                and actual["seed"] == request["seed"] and actual["mode"] == request["mode"]
                and actual["query_plan"] == page["query_plan"], "Returned search binding differs from the frozen request")
        results = export_query_results(page)
        require(len(results) <= request["limit"] and len({r["family_id"] for r in results}) == len(results),
                "A returned page exceeds top-10 or duplicates a family")
        for result, original in zip(results, page["results"]):
            result["selected_file"].update({key: copy.deepcopy(original["selected_file"][key])
                for key in EXTRA_FILE_FIELDS if key in original["selected_file"]})
            # Classification is a derived core field, kept separately from the
            # exact catalog file data that will be verified below.
            result["derived_content_type"] = copy.deepcopy(original["selected_file"].get("content_type"))
        rows.append({"query_id": query["query_id"], "task_id": query["task_id"],
            "component_id": query["component_id"], "role_id": query["component_id"],
            "language": query["language"], "category": by_task[query["task_id"]]["category"],
            "request": request, "request_sha256": request_hash(request),
            "review_requirements": copy.deepcopy(query["review_requirements"]),
            "search_bindings": copy.deepcopy(actual), "query_plan": copy.deepcopy(page["query_plan"]),
            "diagnostics": copy.deepcopy(page["diagnostics"]), "adapter_metrics": copy.deepcopy(page["adapter_metrics"]),
            "next_cursor": page.get("next_cursor"), "exact_results": results,
            "component_use_pass": None, "new_visual_inspections": 0})
        if (index + 1) % 20 == 0:
            print(f"Recorded {index + 1}/{len(benchmark['queries'])} frozen child queries", flush=True)
    return {"schema_version": 1, "status": "component_metadata_results_not_visual_acceptance",
        "benchmark_version": benchmark["benchmark_version"], "bindings": copy.deepcopy(bindings),
        "tasks": copy.deepcopy(benchmark["tasks"]), "queries": rows,
        "role_id_semantics": "role_id is an output alias of component_id; neither ID is sent to a search filter.",
        "ranking_input_policy": "Only explicit query, filters, limit, mode and seed enter the shared search core. Expected concepts and use requirements are review context only.",
        "new_visual_inspections": 0, "composition_use_pass": None, "acceptance_passed": False}


def bind_exact_candidates(catalog_path, raw):
    """Verify every returned slot against catalog bytes, then collect a delta-review map."""
    wanted = {(row["family_id"], row["selected_file"]["sha256"])
              for query in raw["queries"] for row in query["exact_results"]}
    found = {}
    with Path(catalog_path).open(encoding="utf8") as stream:
        for line in stream:
            if not line.strip():
                continue
            record = json.loads(line)
            for index, file in enumerate(record["files"]):
                key = record["family_id"], file.get("sha256")
                if key in wanted:
                    require(key not in found, "Ambiguous exact family/file catalog slot")
                    found[key] = (record["id"], index, file)
    require(set(found) == wanted, "Returned file is absent from the exact catalog")
    candidates = {}
    for query in raw["queries"]:
        rows = query["exact_results"]
        require([row["rank"] for row in rows] == list(range(1, len(rows) + 1)) and len(rows) <= 10,
                "Incomplete or changed ordered top-10 sequence")
        for row in rows:
            asset, index, file = found[row["family_id"], row["selected_file"]["sha256"]]
            require(row["asset_id"] == asset and all(file.get(key) == value for key, value in row["selected_file"].items()),
                    "Returned selected-file metadata differs from the catalog")
            require(row["preview"] == file.get("preview") and row["download"] == file.get("archive")
                    and file.get("public_scope", {}).get("public_allowed") is not False,
                    "Selected preview/download/public scope differs")
            source = row["selected_source"]
            keys = ["source", "source_id", "attribution"] + [k for k in ("license", "author", "source_url") if k in source]
            require(any(all(origin.get(k) == source.get(k) for k in keys) for origin in file.get("source_records", [])),
                    "Selected source is not provenance of this exact file")
            cid = f"{asset}:file-{index}"
            row["candidate_id"] = cid
            if cid not in candidates:
                candidates[cid] = {"candidate_id": cid, "asset_id": asset, "family_id": row["family_id"],
                    "original_sha256": file["sha256"], "original_archive": copy.deepcopy(file.get("archive")),
                    "preview": copy.deepcopy(file.get("preview")), "format": file.get("format"),
                    "structure": file.get("structure"), "file_source_records": copy.deepcopy(file.get("source_records", [])),
                    "validation": copy.deepcopy(file.get("validation")), "scientific": copy.deepcopy(file.get("scientific")),
                    "scientific_review_evidence": copy.deepcopy(file.get("scientific_review_evidence")),
                    "public_scope": copy.deepcopy(file.get("public_scope")), "text_content": copy.deepcopy(file.get("text_content")),
                    "occurrences": [], "existing_observation_reuse": "not_assessed", "component_use_pass": None}
            candidates[cid]["occurrences"].append({"query_id": query["query_id"], "task_id": query["task_id"],
                "component_id": query["component_id"], "language": query["language"], "rank": row["rank"],
                "variant_id": row["variant_id"], "selected_source": copy.deepcopy(source),
                "selected_science": copy.deepcopy(row["selected_science"])})
    return {"schema_version": 1, "bindings": copy.deepcopy(raw["bindings"]), "candidates": candidates,
        "status": "exact_candidate_map_for_delta_review_planning_only", "new_visual_inspections": 0,
        "note": "Old observations may be referenced only after matching original/preview hashes and both archives; each new role and composition use still requires its own judgment."}


def frozen_input(path, expected_sha):
    path = path.resolve()
    require(path.is_relative_to((ROOT / "data").resolve()) and path.name != "benchmark.json"
            and path.suffix == ".json" and path.stat().st_size <= 2 * 1024 * 1024,
            "Use a bounded, separate committed component benchmark under data/")
    require(re.fullmatch(r"[a-f0-9]{64}", expected_sha or "") and digest(path) == expected_sha,
            "Component benchmark differs from the explicitly frozen SHA")
    document = read(path)
    validate_benchmark(document)
    return document


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", type=Path, default=Path("data/component-benchmark.json"))
    parser.add_argument("--benchmark-sha256", required=True)
    parser.add_argument("--release-tag", default="acceptance-20261107")
    parser.add_argument("--assembly-prefix", required=True)
    parser.add_argument("--publish-tag", default="acceptance-20261107")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    if os.environ.get("GITHUB_ACTIONS") != "true":
        parser.error("Real component retrieval is cloud-only; local execution is limited to tiny helper tests")
    require(re.fullmatch(r"assembly-[0-9]+-[0-9]+", args.assembly_prefix), "Use an exact assembly prefix")
    benchmark_path = (ROOT / args.benchmark).resolve()
    benchmark = frozen_input(benchmark_path, args.benchmark_sha256)
    legacy_path = ROOT / "data/benchmark.json"
    legacy_sha = digest(legacy_path)
    repository = os.environ["GITHUB_REPOSITORY"]
    run = os.environ["GITHUB_RUN_ID"] + "-" + os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    base = ROOT / "build/component-benchmark" / run
    incoming, output = base / "input", base / "evidence"
    require(not output.exists(), "Refusing to overwrite previous component evidence")
    output.mkdir(parents=True)
    assets = list_release_assets(repository, args.release_tag)
    paths, receipts = {}, []
    for suffix in ("catalog.jsonl", "catalog.sqlite", "report.json"):
        name = args.assembly_prefix + "." + suffix
        paths[suffix], receipt = download_metadata(name, assets[name], incoming, repository, args.release_tag)
        receipts.append(receipt)
    assembly = read(paths["report.json"])
    catalog_sha, core_sha = digest(paths["catalog.jsonl"]), digest(ROOT / "mga_resources/search-core.mjs")
    require(assembly["run_id"] == args.assembly_prefix.removeprefix("assembly-")
            and assembly["summary"]["catalog_sha256"] == catalog_sha, "Assembly/catalog/prefix binding differs")
    producer = verify_producer(repository, args.assembly_prefix, core_sha)
    resolve_index(paths["catalog.jsonl"], output, paths["catalog.sqlite"])
    with closing(sqlite3.connect(paths["catalog.sqlite"].resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        require(connection.execute("PRAGMA quick_check").fetchone()[0] == "ok", "Input SQLite integrity failed")
        metadata = dict(connection.execute("SELECT key, value FROM index_metadata"))
        version = connection.execute("PRAGMA user_version").fetchone()[0]
    bindings = {"component_benchmark_sha256": digest(benchmark_path), "legacy_benchmark_sha256": legacy_sha,
        "catalog_sha256": catalog_sha, "sqlite_sha256": digest(paths["catalog.sqlite"]),
        "assembly_report_sha256": digest(paths["report.json"]), "search_core_sha256": core_sha,
        "ranking_version": metadata["ranking_version"], "search_index_version": version}
    bindings.update({key: metadata[key] for key in
        ("search_vocabulary_sha256", "search_cli_sha256", "search_policy_sha256") if key in metadata})
    raw = execute_queries(paths["catalog.sqlite"], benchmark, bindings)
    raw["metadata_run"] = {"repository": repository, "run_id": os.environ["GITHUB_RUN_ID"],
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", "1"), "commit_sha": os.environ["GITHUB_SHA"],
        "workflow_ref": os.environ.get("GITHUB_WORKFLOW_REF"),
        "url": f"https://github.com/{repository}/actions/runs/{os.environ['GITHUB_RUN_ID']}"}
    candidates = bind_exact_candidates(paths["catalog.jsonl"], raw)
    require(digest(legacy_path) == legacy_sha and digest(benchmark_path) == args.benchmark_sha256,
            "Benchmark input changed while replaying")
    write(output / "raw-component-results.json", raw)
    candidates["raw_results_sha256"] = digest(output / "raw-component-results.json")
    write(output / "candidates.json", candidates)
    (output / "component-benchmark.json").write_bytes(benchmark_path.read_bytes())
    summary = {"schema_version": 1, "status": "component_metadata_replay_completed_not_visual_acceptance",
        "run_id": run, "code_commit": os.environ["GITHUB_SHA"], "generated_at": datetime.now(timezone.utc).isoformat(),
        "assembly_prefix": args.assembly_prefix, "release_tag": args.release_tag, "publish_tag": args.publish_tag,
        "benchmark_version": benchmark["benchmark_version"], "bindings": bindings, "inputs": receipts,
        "input_metadata_producer": producer, "tasks": len(benchmark["tasks"]),
        "task_language_units": len(benchmark["tasks"]) * 2, "child_queries": len(raw["queries"]),
        "result_positions": sum(len(q["exact_results"]) for q in raw["queries"]),
        "nonempty_child_queries": sum(bool(q["exact_results"]) for q in raw["queries"]),
        "unique_selected_files": len(candidates["candidates"]),
        "raw_results_sha256": digest(output / "raw-component-results.json"),
        "candidate_mapping_sha256": digest(output / "candidates.json"),
        "original_archive_reads": 0, "preview_archive_reads": 0, "new_visual_inspections": 0,
        "legacy_queries_executed": 0, "legacy_results_modified": False,
        "component_relevance_score": None, "composition_use_pass": None, "acceptance_passed": False}
    write(output / "summary.json", summary)
    pack = base / f"component-benchmark-{run}.tar.gz"
    summary["metadata_package"] = {"name": pack.name, **package_metadata(output, pack)}
    published_summary = base / f"component-benchmark-{run}.summary.json"
    write(published_summary, summary)
    if args.publish:
        existing = list_release_assets(repository, args.publish_tag)
        require(pack.name not in existing and published_summary.name not in existing, "Refusing to overwrite an existing Release asset")
        gh("release", "upload", args.publish_tag, str(pack), str(published_summary), "--repo", repository)
        published = list_release_assets(repository, args.publish_tag)
        proof = []
        for path in (pack, published_summary):
            row = published[path.name]
            require(row.get("digest") == "sha256:" + digest(path) and row.get("size") == path.stat().st_size,
                    "Published component evidence digest/size differs")
            proof.append({"name": path.name, "url": row["browser_download_url"], "github_asset_id": row["id"],
                "sha256": digest(path), "size_bytes": path.stat().st_size, "github_digest_verified": True})
        write(base / "publication-receipt.json", {"assets": proof})
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
