"""Render previously observed search results in cloud CI without searching again."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from assemble import list_release_assets
from backfill_appearance import download_checked, gh
from build_review import build_review, descriptors


def frozen_search(records, benchmark, plan):
    """Validate file identity and replay exact order, never recompute ranking."""
    if plan.get("benchmark_version") != benchmark.get("benchmark_version"):
        raise ValueError("Frozen benchmark version differs")
    expected = {row["id"]: row for row in benchmark["queries"]}
    queries = plan.get("queries", [])
    if len(queries) != len(expected) or {row["id"] for row in queries} != set(expected):
        raise ValueError("Frozen queries do not cover the exact benchmark")
    by_id = {record["id"]: record for record in records}
    actual = {row["candidate_id"]: row for record in records for row in descriptors(record)}
    candidates = plan.get("candidates", {})
    for key, row in candidates.items():
        if key not in actual or any(row.get(field) != actual[key].get(field) for field in
                ("candidate_id", "asset_id", "family_id", "original_sha256", "original_archive", "preview", "preview_key", "type")):
            raise ValueError("Frozen candidate differs from exact catalog file")
    returned = {}
    for query in queries:
        if any(query.get(key) != value for key, value in expected[query["id"]].items()):
            raise ValueError("Frozen query differs from benchmark")
        rows = query.get("candidates", [])
        if (len(rows) > 10 or [row.get("rank") for row in rows] != list(range(1, len(rows) + 1))
                or len({row.get("family_id") for row in rows}) != len(rows)):
            raise ValueError("Frozen query ranks or families are invalid")
        selected = []
        for row in rows:
            candidate = candidates.get(row.get("candidate_id"), {})
            if not candidate or any(row.get(key) != candidate.get(key) for key in ("asset_id", "family_id")):
                raise ValueError("Frozen result is absent from candidate mapping")
            record = by_id[candidate["asset_id"]]
            file = next(file for file in record["files"] if file["sha256"] == candidate["original_sha256"])
            selected.append({**record, "selected_file": file})
        if query["query"] in returned and returned[query["query"]] != selected:
            raise ValueError("One query text has conflicting frozen results")
        returned[query["query"]] = selected

    def replay(_database, query, limit=10):
        if query not in returned or limit != 10:
            raise ValueError("Only the frozen query and limit may be replayed")
        return returned[query]
    return replay


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--assembly-prefix", required=True)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    if os.environ.get("GITHUB_ACTIONS") != "true":
        parser.error("Archived previews are extracted only in cloud CI")
    plan_path = (ROOT / args.plan).resolve()
    if (not plan_path.is_relative_to((ROOT / "data/review-plans").resolve())
            or plan_path.stat().st_size > 8 * 1024 * 1024
            or not re.fullmatch(r"assembly-[0-9]+-[0-9]+", args.assembly_prefix)):
        parser.error("Use a bounded committed plan and exact assembly prefix")
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    benchmark_path = ROOT / "data/benchmark.json"
    if plan.get("bindings", {}).get("benchmark_sha256") != digest(benchmark_path):
        raise ValueError("Frozen plan does not bind the original benchmark")
    repository = os.environ["GITHUB_REPOSITORY"]
    run = os.environ["GITHUB_RUN_ID"] + "-" + os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    base = ROOT / "build/frozen-review" / run
    incoming, output = base / "metadata", base / "review"
    incoming.mkdir(parents=True, exist_ok=True)
    assets = list_release_assets(repository, args.release_tag)
    name = args.assembly_prefix + ".catalog.jsonl"
    catalog = download_checked(name, assets[name], incoming, repository, args.release_tag)
    if plan.get("bindings", {}).get("catalog_sha256") != digest(catalog):
        raise ValueError("Frozen plan does not bind the downloaded catalog")
    records = [json.loads(line) for line in catalog.read_text(encoding="utf-8").splitlines() if line.strip()]
    benchmark = json.loads(benchmark_path.read_text(encoding="utf-8"))
    replay = frozen_search(records, benchmark, plan)
    archive_manifest = {row["browser_download_url"]: row for key, row in assets.items() if key.endswith(".tar.gz")}
    summary = build_review(records, None, benchmark, output, sample_limit=0,
        seed="frozen-observation-no-new-search", search_fn=replay, archive_manifest=archive_manifest,
        font_path="/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
    summary.update({"bindings": plan["bindings"], "frozen_plan_sha256": digest(plan_path),
        "assembly_prefix": args.assembly_prefix, "catalog_sha256": digest(catalog),
        "benchmark_sha256": digest(benchmark_path), "tool_commit": os.environ.get("GITHUB_SHA"),
        "run_id": run, "searches_executed": 0, "sample_limit": 0})
    mapping_path = output / "mapping.json"
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    mapping["bindings"] = plan["bindings"]
    mapping["frozen_plan_sha256"] = digest(plan_path)
    mapping_path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    archive = base / ("frozen-review-" + run + ".tar.gz")
    with tarfile.open(archive, "w:gz") as tar:
        for path in sorted(output.rglob("*")):
            if path.is_file():
                tar.add(path, arcname=path.relative_to(output).as_posix())
    if archive.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("Bounded review pack exceeds 32 MiB")
    summary.update({"bundle": archive.name, "bundle_bytes": archive.stat().st_size, "bundle_sha256": digest(archive)})
    report = base / ("frozen-review-" + run + ".summary.json")
    report.write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    if args.publish:
        gh("release", "upload", args.release_tag, str(archive), str(report), "--repo", repository)
    print(json.dumps({key: summary[key] for key in ("query_count", "unique_candidate_files", "candidates_without_preview", "bundle", "bundle_bytes")}))


if __name__ == "__main__":
    main()
