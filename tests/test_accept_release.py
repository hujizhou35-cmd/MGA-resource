"""Synthetic metadata exercises gates; these tests are not real visual approval."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from mga_resources.supplemental_acceptance import APPEARANCE_CASES, FUNCTIONAL_CASES

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("accept_release", ROOT / "scripts/accept_release.py")
accept = importlib.util.module_from_spec(spec)
spec.loader.exec_module(accept)


def h(value):
    return hashlib.sha256(value.encode()).hexdigest()


def fixture():
    benchmark_path = ROOT / "data/benchmark.json"
    benchmark = json.loads(benchmark_path.read_text(encoding="utf-8"))
    catalog, candidates = [], {}
    by_concept = {}
    archive = "https://github.com/fixture/library/releases/download/test/pack.tar.gz"
    for index, concept in enumerate(benchmark["concepts"]):
        asset_id, family = f"asset-{index}", f"family-{index}"
        original_sha, preview_sha = h(f"original-{index}"), h(f"preview-{index}")
        file = {"sha256": original_sha, "size_bytes": 100, "format": "png" if index == 0 else "svg",
                "structure": "raster" if index == 0 else "pure_vector",
                "archive": {"url": archive, "member": f"files/{index}.svg"},
                "preview": {"sha256": preview_sha, "size_bytes": 50,
                            "archive": {"url": archive, "member": f"previews/{index}.png"}},
                "validation": {"status": "machine_checked_and_rendered"}}
        record = {"id": asset_id, "family_id": family, "source": "example", "source_id": str(index),
                  "license": "CC0-1.0", "author": "Fixture", "source_url": f"https://example.invalid/{index}",
                  "files": [file]}
        catalog.append(record)
        candidate_id = asset_id + ":file-0"
        by_concept[concept["id"]] = candidate_id
        candidates[candidate_id] = {"candidate_id": candidate_id, "asset_id": asset_id, "family_id": family,
                                    "source": "example", "sources": ["example"], "original_sha256": original_sha,
                                    "original_archive": file["archive"], "preview": file["preview"],
                                    "preview_status": "viewable", "type": file["structure"]}
    # A second synthetic source enables the independent cross-source style case.
    catalog[-1]["source"] = "second-example"
    candidates["asset-59:file-0"].update(source="second-example", sources=["second-example"])
    mapping = {"benchmark_version": benchmark["benchmark_version"], "candidates": candidates, "queries": []}
    judgments = {"benchmark_version": benchmark["benchmark_version"], "reviewer": "Synthetic independent reviewer",
                 "reviewed_at": "2026-11-06", "independent_from_metadata_matcher": True,
                 "queries": [], "samples": [], "curated": [], "negative_scenarios": []}
    for query in benchmark["queries"]:
        key = by_concept[query["concept_id"]]
        mapping["queries"].append({**query, "candidates": [{"rank": 1, "candidate_id": key,
            "asset_id": candidates[key]["asset_id"], "family_id": candidates[key]["family_id"]}]})
        judgments["queries"].append({"query_id": query["id"], "pass": True, "selected_candidate_ids": [key],
            "visual_evidence_notes": "Synthetic fixture: concept and species reviewed.",
            "source_evidence_checked": True, "original_download_checked": True,
            "license_checked": True, "species_matches": True})
    for concept in benchmark["concepts"]:
        key = by_concept[concept["id"]]
        judgments["curated"].append({"candidate_id": key, "usable": True, "source_evidence_checked": True,
            "visual_evidence_notes": "Synthetic fixture: viewed complete illustration.", "categories": [concept["category"]]})
    for scenario in benchmark["negative_scenarios"]:
        judgments["negative_scenarios"].append({"scenario_id": scenario["id"], "query": scenario["query"],
            "filters": scenario.get("filters", {}), "pass": True, "evidence_notes": "Synthetic observed behavior.",
            "observed_candidate_ids": ["asset-0:file-0"] if scenario["id"] == "raster-is-allowed" else []})
    hashes = {"catalog_sha256": h("catalog"), "benchmark_sha256": hashlib.sha256(benchmark_path.read_bytes()).hexdigest(),
              "assembly_report_sha256": h("assembly"), "mapping_sha256": h("mapping")}
    assembly = {"collection_release": "test", "run_id": "1-1", "summary": {"catalog_sha256": hashes["catalog_sha256"]},
                "source_snapshots": [{"source": "example", "discovery_sha256": h("discovery"), "acquisition_complete": True}]}
    args = {"catalog": catalog, "assembly": assembly, "benchmark": benchmark, "mapping": mapping, "judgments": judgments,
            "hashes": hashes, "registry": [{"source_id": "example", "primary": True}],
            "review_summary": {"catalog_sha256": hashes["catalog_sha256"], "benchmark_sha256": hashes["benchmark_sha256"],
                               "assembly_prefix": "assembly-1-1"}}
    manifest, report = accept.evaluate_release(**args)
    judgments["bindings"] = report["required_bindings"]
    args["restore_evidence"] = {"publish_manifest_sha256": report["publish_manifest_sha256"],
        "passed": True, "published_subset_only": True, "checked_by": "Synthetic restore test",
        "checked_at": "2026-11-06", "bundle_url": archive, "bundle_sha256": h("bundle"),
        "member_sha256": sorted({entry["sha256"] for entry in manifest["files"]} |
                               {entry["preview"]["sha256"] for entry in manifest["files"]})}
    proof = {"uri": "https://example.invalid/synthetic-evidence.json", "sha256": h("synthetic evidence")}
    real_files = [{"original_sha256": row["files"][0]["sha256"],
                   "preview_sha256": row["files"][0]["preview"]["sha256"],
                   "observed": "Synthetic fixture observation", "reviewer": "Synthetic independent reviewer"}
                  for row in catalog[-2:]]
    args["supplemental_evidence"] = {
        "schema_version": 1, "generated_at": "2026-11-07", "reviewer": "Synthetic independent reviewer",
        "bindings": {"catalog_sha256": hashes["catalog_sha256"], "benchmark_sha256": hashes["benchmark_sha256"],
                     "search_core_sha256": hashlib.sha256((ROOT / "mga_resources/search-core.mjs").read_bytes()).hexdigest(),
                     "ranking_version": "synthetic-test", "analysis_version": "synthetic-test"},
        "functional_cases": [{"id": key, "passed": True, "input": {"synthetic": True},
                              "observed": {"fixture_result": "expected"}, "evidence": proof, "notes": "Synthetic fixture only."}
                             for key in FUNCTIONAL_CASES],
        "appearance_cases": [{"id": key, "passed": True, "expected": "Synthetic expected appearance",
                              "observed": "Synthetic observed appearance", "evidence": proof,
                              "notes": "Synthetic test does not approve real artwork.", "real_files": real_files}
                             for key in APPEARANCE_CASES],
        "diversity_scenarios": [{"case_id": kind, "pool_kind": kind, "eligible_family_ids": ["family-58", "family-59"] if kind == "rich" else ["family-58"],
            "query": {"synthetic": True}, "review_notes": "Synthetic controlled project adoption exercise.", "evidence": proof,
            "projects": [{"project_id": str(i), "seed": i, "old_top_family_ids": ["family-58"],
                "new_top_family_ids": ["family-59" if kind == "rich" and i else "family-58"],
                "trial_downloaded_family_ids": ["family-58"],
                "adopted_family_ids": ["family-59" if kind == "rich" and i else "family-58"], "ledger_evidence": proof} for i in range(2)]}
            for kind in ("rich", "scarce")],
    }
    return args


def codes(report):
    return {item["code"] for item in report["missing"]}


def test_full_synthetic_evidence_opens_gate_without_claiming_native_verification():
    manifest, report = accept.evaluate_release(**fixture())
    assert report["acceptance_passed"], report["missing"]
    assert report["website_gate"] == "open_for_implementation"
    assert report["retrieval"]["successful"] == 120
    assert len(manifest["files"]) == 60
    assert report["native_default"] == "not_verified" and report["native_evidence"] == []


def test_old_acceptance_alone_does_not_satisfy_supplemental_requirements():
    args = fixture()
    args.pop("supplemental_evidence")
    _, report = accept.evaluate_release(**args)
    assert not report["acceptance_passed"]
    assert report["retrieval"]["successful"] == 120
    assert {"supplement.binding", "supplement.appearance_case", "supplement.rich_and_scarce"} <= codes(report)


def test_supplement_rejects_changed_file_and_catalog_and_separates_adoption_metrics():
    args = fixture()
    extra = args["supplemental_evidence"]
    extra["bindings"]["catalog_sha256"] = h("wrong catalog")
    extra["appearance_cases"][0]["real_files"] = [{"original_sha256": h("unseen")}]
    _, report = accept.evaluate_release(**args)
    assert {"supplement.binding", "supplement.appearance_case"} <= codes(report)
    metric = report["supplemental"]["diversity_observations"][0]["metrics"]
    assert metric["trial_downloaded_family_ids"]["distinct_families"] == 1
    assert metric["adopted_family_ids"]["distinct_families"] == 2
    assert metric["old_top_family_ids"]["maximum_project_reuse"] == 2
    assert metric["new_top_family_ids"]["maximum_project_reuse"] == 1


def test_blank_judgments_and_missing_restore_stay_pending():
    args = fixture()
    args["judgments"] = {"reviewer": None, "queries": []}
    args["restore_evidence"] = None
    _, report = accept.evaluate_release(**args)
    assert not report["acceptance_passed"]
    assert {"queries.count", "review.independent", "restore.pending", "visual.curated"} <= codes(report)


def test_exact_threshold_passes_but_one_more_failure_cannot_be_waived():
    args = fixture()
    # Lose one concept in each of six categories: 108 total, 54 per language,
    # four concepts remain in those categories. Curated review remains intact.
    failed = {args["benchmark"]["concepts"][index * 5]["id"] for index in range(6)}
    by_id = {query["id"]: query for query in args["benchmark"]["queries"]}
    for row in args["judgments"]["queries"]:
        if by_id[row["query_id"]]["concept_id"] in failed:
            row.update({"pass": False, "selected_candidate_ids": [], "gap_reason": "Synthetic genuine coverage gap."})
    _, report = accept.evaluate_release(**args)
    assert report["retrieval"]["successful"] == 108
    assert report["acceptance_passed"], report["missing"]
    next(row for row in args["judgments"]["queries"] if row["pass"]).update(
        {"pass": False, "gap_reason": "Another genuine gap."})
    _, report = accept.evaluate_release(**args)
    assert report["retrieval"]["successful"] == 107
    assert not report["acceptance_passed"]
    assert "queries.threshold" in codes(report)


def test_version_binding_and_changed_benchmark_cannot_pass():
    args = fixture()
    args["judgments"]["bindings"]["mapping_sha256"] = h("another mapping")
    args["hashes"]["benchmark_sha256"] = h("easier benchmark")
    _, report = accept.evaluate_release(**args)
    assert {"judgments.binding", "benchmark.frozen", "review.binding"} <= codes(report)
    assert not report["acceptance_passed"]


def decided_source(args, source):
    return {"template_only": False, "release_id": "test", "input_catalog_sha256": args["hashes"]["catalog_sha256"],
        "benchmark_sha256": args["hashes"]["benchmark_sha256"],
        "publish_manifest": {"sha256": args["judgments"]["bindings"]["publish_manifest_sha256"]},
        "decisions": [{"decision_id": "unavailable", "review_state": "decided",
            "target": {"kind": "source", "source": source, "discovery_sha256": None},
            "disposition": "not_acquired", "reason": "Synthetic documented access denial.",
            "reviewed_by": {"kind": "agent", "id": "independent-fixture"}, "reviewed_at": "2026-11-06",
            "evidence": [{"uri": "https://example.invalid/denial.json", "sha256": h("denial")}],
            "impact": {"description": "Inventory unavailable; overlap assessed by the unchanged benchmark."},
            "mitigation": {"decision": "Use independent sources and pass the full original benchmark."}}]}


def test_evidenced_source_deferral_does_not_permanently_close_gate():
    args = fixture()
    args["registry"].append({"source_id": "unavailable", "primary": True})
    _, report = accept.evaluate_release(**args)
    assert "sources.unresolved" in codes(report)
    args["decisions"] = decided_source(args, "unavailable")
    _, report = accept.evaluate_release(**args)
    assert report["acceptance_passed"], report["missing"]
    args["decisions"]["template_only"] = True
    _, report = accept.evaluate_release(**args)
    assert not report["acceptance_passed"]
    assert "decisions.template" in codes(report)


def test_quarantined_candidate_cannot_remain_a_retrieval_success():
    args = fixture()
    selected = {"source": "example", "discovery_sha256": h("discovery"),
                "items": [{"sha256": args["catalog"][0]["files"][0]["sha256"]}]}
    selection_hash = accept.digest(selected)
    decision = decided_source(args, "example")
    decision["decisions"][0].update({"disposition": "quarantine", "target": {"kind": "files", "source": "example",
        "discovery_sha256": h("discovery"), "selection_manifest": {"sha256": selection_hash, "uri": "quarantine.json"}}})
    args.update(decisions=decision, selection_manifests={selection_hash: selected})
    manifest, report = accept.evaluate_release(**args)
    assert len(manifest["files"]) == 59
    assert report["retrieval"]["successful"] == 118
    assert "queries.evidence" in codes(report)
    assert "visual.curated" in codes(report)
    assert not report["acceptance_passed"]


def test_invalid_preview_and_missing_rights_are_not_published():
    args = fixture()
    args["catalog"][0]["files"][0]["preview"]["sha256"] = None
    args["catalog"][1]["license"] = None
    manifest, report = accept.evaluate_release(**args)
    assert len(manifest["files"]) == 58
    assert "files.unresolved" in codes(report)
    assert not report["acceptance_passed"]


def test_negative_scenarios_need_real_recorded_verdicts():
    args = fixture()
    args["judgments"]["negative_scenarios"][0]["pass"] = None
    _, report = accept.evaluate_release(**args)
    assert report["retrieval"]["negative_scenarios_passed"] == 5
    assert "negatives.pending" in codes(report)
    assert not report["acceptance_passed"]


def test_legacy_native_claim_matches_only_exact_original_and_environment():
    args = fixture()
    original = args["catalog"][1]["files"][0]["sha256"]
    args["native_evidence"] = {"assets": [{"catalog_match": {"source": "example", "source_id": "1",
        "source_svg_sha256": original}, "files": {"source": {"sha256": original, "original_bytes_rechecked": True}},
        "historical_validation": {"status": "validated_in_recorded_environment_only", "illustrator_version": "27.6",
                                 "mcp_server_version": "1.10.3", "other_environments_verified": False}}]}
    _, report = accept.evaluate_release(**args)
    assert len(report["native_evidence"]) == 1
    assert report["native_evidence"][0]["fresh_environment_verified"] is False
    args["native_evidence"]["assets"][0]["catalog_match"]["source_svg_sha256"] = h("changed bytes")
    _, report = accept.evaluate_release(**args)
    assert report["native_evidence"] == []


def test_restored_pack_must_not_include_quarantined_or_unlisted_bytes():
    args = fixture()
    args["restore_evidence"]["member_sha256"].append(h("unlisted bad file"))
    _, report = accept.evaluate_release(**args)
    assert not report["restore_passed"]
    assert "restore.pending" in codes(report)


def test_candidate_cannot_invent_family_or_real_media_type():
    args = fixture()
    args["mapping"]["candidates"]["asset-0:file-0"]["type"] = "pure_vector"
    args["mapping"]["candidates"]["asset-1:file-0"]["family_id"] = "invented-extra-family"
    _, report = accept.evaluate_release(**args)
    assert report["retrieval"]["successful"] == 116
    assert report["visual"]["curated_families"] == 58
    assert not report["acceptance_passed"]


def test_unselected_top_ten_result_must_also_belong_to_publication_subset():
    args = fixture()
    args["mapping"]["queries"][0]["candidates"].append({"rank": 2,
        "candidate_id": "quarantined:file-0", "asset_id": "quarantined", "family_id": "quarantined-family"})
    _, report = accept.evaluate_release(**args)
    assert report["retrieval"]["successful"] == 119
    assert "queries.evidence" in codes(report)
    assert not report["acceptance_passed"]


def test_incomplete_source_without_accounting_is_not_silently_complete():
    args = fixture()
    args["assembly"]["source_snapshots"][0] = {"source": "example", "discovery_sha256": h("discovery"),
        "acquisition_complete": False}
    _, report = accept.evaluate_release(**args)
    assert report["unresolved_sources"] == ["example"]
    assert not report["acceptance_passed"]


def test_existing_source_sample_format_requires_ten_included_families():
    args = fixture()
    args["judgments"]["curated"] = []
    review = {"reviewer": "Synthetic direct visual reviewer", "reviewed_at": "2026-11-06",
              "review_method": "Directly viewed actual review sheet", "source": "example",
              "review_sheet_url": "https://example.invalid/sheet.png", "review_sheet_sha256": h("sheet"), "items": []}
    for record in args["catalog"][:10]:
        file = record["files"][0]
        review["items"].append({"original_sha256": file["sha256"], "preview_sha256": file["preview"]["sha256"],
            "original_archive": file["archive"], "preview_archive": file["preview"]["archive"],
            "visual_verdict": "usable_as_general_illustration", "observations": "Synthetic visible complete outline."})
    args["source_reviews"] = [review]
    _, report = accept.evaluate_release(**args)
    assert "visual.source_sample" not in codes(report)
    assert "visual.curated" in codes(report)
    review["items"].pop()
    _, report = accept.evaluate_release(**args)
    assert "visual.source_sample" in codes(report)


def test_cli_outputs_manifest_and_readable_missing_list_with_no_network(tmp_path):
    args = fixture()
    for name in ("assembly", "mapping", "judgments"):
        (tmp_path / f"{name}.json").write_bytes(accept.json_bytes(args[name] if name != "judgments" else {}))
    catalog_path = tmp_path / "catalog.jsonl"
    catalog_path.write_bytes(b"".join(accept.json_bytes(row) for row in args["catalog"]))
    output = tmp_path / "result"
    run = subprocess.run([sys.executable, str(ROOT / "scripts/accept_release.py"),
        "--assembly-report", str(tmp_path / "assembly.json"), "--catalog", str(catalog_path),
        "--mapping", str(tmp_path / "mapping.json"), "--judgments", str(tmp_path / "judgments.json"),
        "--output", str(output)], text=True, capture_output=True)
    assert run.returncode == 2, run.stderr
    report = json.loads((output / "checkreport.json").read_text(encoding="utf-8"))
    assert not report["acceptance_passed"] and report["missing"]
    assert "Missing evidence:" in (output / "checkreport.md").read_text(encoding="utf-8")
    assert hashlib.sha256((output / "publish_manifest.json").read_bytes()).hexdigest() == report["publish_manifest_sha256"]

