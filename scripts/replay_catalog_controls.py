"""Cloud-only replay of fixed negative, appearance and adoption-history controls.

Existing observations/choices are evidence inputs, not newly generated judgments.
Only catalog/index/report JSON metadata is fetched; artwork is never accessed.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from mga_resources.catalog import search_page, search_policy_metadata
from assemble import gh, list_release_assets
from diagnose_search import resolve_index
from replay_catalog_metadata import digest, download_metadata, package_metadata, read, require, verify_producer, write

APPEARANCE = ROOT / "data/appearance-reviews/appearance-42289434868-1-v1.1.0-bound-review.json"
LEDGER = ROOT / "data/adoption-reviews/final-validation-ledger-42289434868.json"
BENCHMARK = ROOT / "data/benchmark.json"


def exact_pair(item, file, *, appearance=False):
    """Reject a review/choice borrowed from another file, preview or archive."""
    archive_key = "catalog_original_archive" if appearance else "original_archive"
    preview_key = "catalog_preview_archive" if appearance else "preview_archive"
    require(item["original_sha256"] == file["sha256"]
            and item["preview_sha256"] == file["preview"]["sha256"]
            and item[archive_key] == file["archive"]
            and item[preview_key] == file["preview"]["archive"], "Existing evidence has a different exact artwork pair")
    require(file.get("public_scope", {}).get("public_allowed") is True, "Control file is not in current public scope")


def control_files(catalog, appearance, ledger):
    reviewed = {item["original_sha256"]: item for case in appearance["appearance_cases"] for item in case["real_files"]}
    adopted = [item for project in ledger["projects"] for item in project["decision"]["selected_files"]]
    wanted = set(reviewed) | {x["original_sha256"] for x in adopted}
    files = {}
    with Path(catalog).open(encoding="utf-8") as stream:
        for line in stream:
            record = json.loads(line)
            for file in record["files"]:
                if file.get("sha256") in wanted:
                    require(file["sha256"] not in files, "Ambiguous current original identity")
                    files[file["sha256"]] = file
    require(set(files) == wanted, "An existing reviewed/adopted file is absent from the current catalog")
    for item in reviewed.values():
        exact_pair(item, files[item["original_sha256"]], appearance=True)
    for item in adopted:
        exact_pair(item, files[item["original_sha256"]])
    return files, reviewed


def negative_replay(db, benchmark, bindings):
    rows = []
    scenarios = benchmark["negative_scenarios"]
    require(len(scenarios) == 6 and len({x["id"] for x in scenarios}) == 6, "Preserve all six original negative controls")
    for scenario in scenarios:
        request = {"query": scenario["query"], "filters": scenario.get("filters", {}),
                   "limit": 10, "mode": "explore", "seed": "default"}
        page = search_page(db, **request)
        require(page["bindings"]["catalog_version"] == bindings["catalog_sha256"], "Negative replay catalog differs")
        rows.append({"scenario": copy.deepcopy(scenario), "effective_request": request, "raw_result": page})
    return {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
            "method": "Actual fixed query/filter replay; no visual or scientific verdict inferred from returned IDs.",
            "bindings": bindings, "scenarios": rows, "visual_acceptance_passed": False}


def appearance_replay(db, appearance, files, reviewed, bindings):
    observations = []
    for original, item in reviewed.items():
        file, analysis = files[original], files[original]["appearance"]
        require(analysis["classification_version"] == item["catalog_analysis_version"] == bindings["analysis_version"], "Appearance analysis version differs")
        require(analysis["binding"]["file_sha256"] == original
                and analysis["binding"]["preview_sha256"] == item["preview_sha256"], "Appearance is bound to a different file")
        color = analysis["color"]
        require(color["mode"] == item["measured_color_mode"] and color["palette_traits"] == item["measured_palette_traits"], "Reviewed appearance options changed")
        queries = [{"file_sha256": original}]
        if color["mode"] == "unknown":
            queries += [{"file_sha256": original, "color_mode": mode} for mode in ("color", "grayscale", "black_white")]
        else:
            queries += [{"file_sha256": original, "color_mode": color["mode"]}]
            queries += [{"file_sha256": original, "palette_traits": trait} for trait in color["palette_traits"]]
        style = next((r for r in appearance["reused_observations"]["two_cross_source_style_evidence_checks"] if r["file_sha256"] == original), None)
        if style:
            require(analysis["style"]["representation"]["values"] == style["representation"], "Reviewed representation changed")
            queries += [{"file_sha256": original, "representation": value} for value in style["representation"]]
        calls = []
        for filters in queries:
            page = search_page(db, "", filters=filters, seed="appearance-3.4.3-rebind")
            expected = not (color["mode"] == "unknown" and "color_mode" in filters)
            require(len(page["results"]) == int(expected), "Appearance filter returned unexpected eligibility")
            require(page["bindings"]["catalog_version"] == bindings["catalog_sha256"], "Appearance replay catalog differs")
            for row in page["results"]:
                require(row["selected_file"]["sha256"] == original and row["preview"] == file["preview"]
                        and row["download"] == file["archive"], "Appearance result refers to a different variant")
            calls.append({"filters": filters, "expected_hit": expected,
                "returned": [{"family_id": r["family_id"], "original_sha256": r["selected_file"]["sha256"],
                              "preview_sha256": r["preview"]["sha256"], "download": r["download"]} for r in page["results"]],
                "bindings": page["bindings"], "diagnostics": page["diagnostics"]})
        observations.append({"original_sha256": original, "preview_sha256": item["preview_sha256"],
            "title": item["title"], "source": item["source"], "appearance_metadata": analysis,
            "original_archive": file["archive"], "preview_archive": file["preview"]["archive"], "search_checks": calls})
    return {"schema_version": 1, "bindings": bindings,
            "original_review": {"uri": APPEARANCE.relative_to(ROOT).as_posix(), "sha256": digest(APPEARANCE)},
            "observation_reuse": "Old exact-file visual observations remain unchanged. These filter calls actually ran on the current index.",
            "files": observations, "originals_downloaded": 0, "new_visual_judgments": 0}


def snapshot(page):
    return {"bindings": page["bindings"], "qualified_families": page["diagnostics"]["qualified_families"],
        "results": [{"rank": i, "family_id": r["family_id"], "sha256": r["selected_file"]["sha256"],
                     "score": r["search_score"], "source": r["selected_source"], "science": r["selected_science"],
                     "pinned": r["pinned"]} for i, r in enumerate(page["results"], 1)]}


def signature(page):
    return [(r["family_id"], r["selected_file"]["sha256"], r["search_score"]) for r in page["results"]]


def history_replay(db, ledger, output, bindings):
    scenarios = []
    for kind, query in (("rich", "mouse"), ("scarce", "Naegleria gruberi")):
        prior, observations = [], []
        projects = [p for p in ledger["projects"] if p["case_id"] == kind]
        require(len(projects) == 5, "Preserve five existing exercise decisions per pool")
        for project in projects:
            request = {"query": query, "limit": 100, "seed": project["seed"]}
            baseline = search_page(db, **request)
            require(baseline["bindings"]["catalog_version"] == bindings["catalog_sha256"], "History replay catalog differs")
            browse = {"browsed": [dict(r, status="browsed") for r in prior], "trial_downloaded": []}
            history = {"adopted": list(prior)}
            browse_page = search_page(db, **request, history=browse)
            with_history = search_page(db, **request, history=history)
            require(signature(baseline) == signature(browse_page), "Browsing-only history changed ranking")
            old = {r["family_id"]: r for r in baseline["results"]}
            new = {r["family_id"]: r for r in with_history["results"]}
            require(set(old) == set(new) and len(old) == baseline["diagnostics"]["qualified_families"], "Adoption history changed scientific eligibility")
            for family in old:
                require(old[family]["selected_file"]["sha256"] == new[family]["selected_file"]["sha256"]
                        and old[family]["selected_science"] == new[family]["selected_science"], "History changed selected science or variant")
            chosen = project["decision"]["selected_files"][0]
            require(chosen["family_id"] in old, "An old exercise choice is absent from the new qualified pool")
            exact_pair(chosen, old[chosen["family_id"]]["selected_file"])
            deltas = [{"family_id": f, "prior_actual_adoption_count": sum(r["family_id"] == f for r in prior),
                       "score_without_history": old[f]["search_score"], "score_with_actual_adopted_history": new[f]["search_score"],
                       "observed_score_reduction": round(old[f]["search_score"] - new[f]["search_score"], 10)}
                      for f in sorted({r["family_id"] for r in prior})]
            observations.append({"project_id": project["project_id"], "seed": project["seed"],
                "input": {**request, "mode": "explore", "prior_adopted_history": history, "browse_only_counterfactual": browse},
                "no_history": snapshot(baseline), "browse_only": snapshot(browse_page), "actual_adopted_history": snapshot(with_history),
                "browse_only_changes_rank_or_score": False, "qualified_pool_or_science_changed": False,
                "top10_changed": signature(baseline)[:10] != signature(with_history)[:10],
                "score_deltas_for_prior_adopted_families": deltas, "fixed_actual_decision": chosen,
                "new_adoption_recorded_from_replay": False})
            prior.append({"project_id": project["project_id"], "family_id": chosen["family_id"],
                          "file_sha256": chosen["original_sha256"], "status": "adopted"})
        exact0 = search_page(db, query, limit=100, mode="exact", seed="actual-history-exact")
        exact1 = search_page(db, query, limit=100, mode="exact", seed="actual-history-exact", history={"adopted": prior})
        require(signature(exact0) == signature(exact1), "Exact mode was changed by history")
        raw = {"query": query, "projects": observations, "exact_mode_control": {"history": prior,
            "without_history": snapshot(exact0), "with_history": snapshot(exact1), "identical": True}}
        target = output / (kind + ".json")
        write(target, raw)
        scenarios.append({"pool_kind": kind, "eligible_families": len(old), "projects": len(projects),
            "projects_with_changed_top10_order_or_scores": sum(p["top10_changed"] for p in observations),
            "browsing_only_changes": 0, "scientific_or_eligible_pool_changes": 0, "exact_mode_history_effect": False,
            "evidence": {"uri": target.relative_to(output.parent).as_posix(), "sha256": digest(target)}})
    return {"schema_version": 1, "exercise": "validation_exercise", "bindings": {**bindings, "ledger_sha256": digest(LEDGER)},
        "status": "current_index_mechanism_observation_using_unchanged_prior_decisions_not_new_adoption",
        "original_decision_catalog_sha256": ledger["catalog_sha256"], "original_decision_ranking_version": ledger["ranking_version"],
        "method": "Original decisions remain fixed. Only earlier exercise choices enter each replay. No new user activity or research adoption is recorded.",
        "scenarios": scenarios, "images_downloaded": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-tag", default="acceptance-20261107")
    parser.add_argument("--assembly-prefix", required=True)
    parser.add_argument("--publish-tag", default="acceptance-20261107")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    if os.environ.get("GITHUB_ACTIONS") != "true":
        parser.error("Actual catalog controls run in cloud CI; local execution is limited to helper tests")
    require(re.fullmatch(r"assembly-[0-9]+-[0-9]+", args.assembly_prefix), "An exact assembly prefix is required")
    repository = os.environ["GITHUB_REPOSITORY"]
    run = os.environ["GITHUB_RUN_ID"] + "-" + os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    base = ROOT / "build/catalog-controls-replay" / run
    incoming, output = base / "input", base / "evidence"
    output.mkdir(parents=True, exist_ok=True)
    assets = list_release_assets(repository, args.release_tag)
    paths, receipts = {}, []
    for suffix in ("catalog.jsonl", "catalog.sqlite", "report.json"):
        name = args.assembly_prefix + "." + suffix
        paths[suffix], receipt = download_metadata(name, assets[name], incoming, repository, args.release_tag)
        receipts.append(receipt)
    assembly, appearance, ledger, benchmark = read(paths["report.json"]), read(APPEARANCE), read(LEDGER), read(BENCHMARK)
    catalog_sha, core_sha = digest(paths["catalog.jsonl"]), digest(ROOT / "mga_resources/search-core.mjs")
    require(assembly["summary"]["catalog_sha256"] == catalog_sha, "Assembly/catalog binding differs")
    producer = verify_producer(repository, args.assembly_prefix, core_sha)
    resolve_index(paths["catalog.jsonl"], output, paths["catalog.sqlite"])
    bindings = {"catalog_sha256": catalog_sha, "sqlite_sha256": digest(paths["catalog.sqlite"]),
        "assembly_report_sha256": digest(paths["report.json"]), "benchmark_sha256": digest(BENCHMARK),
        **search_policy_metadata(), "analysis_version": assembly["appearance"]["analysis_version"]}
    require(bindings["search_core_sha256"] == core_sha, "Current control policy hash differs")
    files, reviewed = control_files(paths["catalog.jsonl"], appearance, ledger)
    negative = negative_replay(paths["catalog.sqlite"], benchmark, bindings)
    write(output / "raw-negative-results.json", negative)
    appearance_results = appearance_replay(paths["catalog.sqlite"], appearance, files, reviewed, bindings)
    write(output / "appearance-filter-replay.json", appearance_results)
    history = history_replay(paths["catalog.sqlite"], ledger, output / "actual-history-replay", bindings)
    write(output / "actual-history-replay/summary.json", history)
    summary = {"schema_version": 1, "status": "metadata_controls_completed_not_collection_acceptance", "run_id": run,
        "code_commit": os.environ["GITHUB_SHA"], "bindings": bindings, "input_metadata_producer": producer, "inputs": receipts,
        "negative_scenarios": len(negative["scenarios"]), "negative_visual_verdicts_added": 0,
        "appearance_files": len(reviewed), "appearance_filter_calls": sum(len(x["search_checks"]) for x in appearance_results["files"]),
        "history_exercise_projects": sum(x["projects"] for x in history["scenarios"]),
        "original_adoption_ledger_sha256": digest(LEDGER), "adoption_records_added": 0,
        "original_archives_read": 0, "preview_archives_read": 0, "artwork_downloaded": 0, "overall_acceptance_passed": False}
    write(output / "summary.json", summary)
    pack = base / f"catalog-controls-replay-{run}.tar.gz"
    summary["metadata_package"] = {"name": pack.name, **package_metadata(output, pack)}
    require(pack.stat().st_size <= 32 * 1024 * 1024, "Bounded metadata package exceeds32 MiB")
    summary_path = base / f"catalog-controls-replay-{run}.summary.json"
    write(summary_path, summary)
    if args.publish:
        gh("release", "upload", args.publish_tag, "--repo", repository, str(pack), str(summary_path))
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
