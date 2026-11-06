"""Check appearance/diversity evidence and derive descriptive reuse metrics.

These are evidence-consistency checks, not a substitute for running retrieval or
actually reviewing artwork. There is deliberately no invented diversity score.
"""
from collections import Counter
import hashlib
from itertools import combinations
from pathlib import Path
import re

FUNCTIONAL_CASES = {
    "variant_before_family", "hard_vs_preference", "unknown_is_not_match",
    "stable_replay", "pagination_no_duplicate", "pins_stable",
    "adopted_history_only", "modes_scientific_constraints", "no_curated_boost",
}
APPEARANCE_CASES = {
    "transparent_rgb", "small_object_white_background", "pale_color",
    "single_hue_line", "grayscale_shading", "black_white_accent",
    "enclosed_white_structure", "cross_source_style",
}


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _evidence(value):
    return isinstance(value, dict) and _text(value.get("uri")) and _sha(value.get("sha256"))


def reuse_metrics(projects):
    """Calculate from observed lists, including final adoption separately."""
    result = {"projects": len(projects)}
    for field in ("old_top_family_ids", "new_top_family_ids", "trial_downloaded_family_ids", "adopted_family_ids"):
        sets = [set(row.get(field, [])) for row in projects]
        counts = Counter(family for values in sets for family in values)
        pairs = [len(a & b) / len(a | b) if a | b else 0.0 for a, b in combinations(sets, 2)]
        result[field] = {"distinct_families": len(counts), "total_project_family_choices": sum(counts.values()),
                         "maximum_project_reuse": max(counts.values(), default=0),
                         "mean_pairwise_jaccard": sum(pairs) / len(pairs) if pairs else None}
    return result


def evaluate_supplement(evidence, *, catalog_sha256, benchmark_sha256, manifest_files):
    evidence = evidence or {}
    missing = []
    def need(ok, code, detail):
        if not ok:
            missing.append({"code": "supplement." + code, "detail": detail})
        return bool(ok)

    bindings = evidence.get("bindings") or {}
    core_path = Path(__file__).with_name("search-core.mjs")
    required = {"catalog_sha256": catalog_sha256, "benchmark_sha256": benchmark_sha256,
                "search_core_sha256": hashlib.sha256(core_path.read_bytes()).hexdigest()}
    need(evidence.get("schema_version") == 1 and all(bindings.get(k) == v for k, v in required.items())
         and _text(bindings.get("ranking_version")) and _text(bindings.get("analysis_version")),
         "binding", "Appearance/diversity report must bind this catalog, benchmark, shared search core and analysis/ranking versions.")
    need(_text(evidence.get("reviewer")) and _text(evidence.get("generated_at")),
         "review", "Identify who reviewed the supplemental observations and when.")
    cases = {row.get("id"): row for row in evidence.get("functional_cases", [])}
    for case_id in sorted(FUNCTIONAL_CASES):
        row = cases.get(case_id, {})
        need(row.get("passed") is True and isinstance(row.get("input"), dict)
             and isinstance(row.get("observed"), (list, dict)) and bool(row.get("observed"))
             and _evidence(row.get("evidence")) and _text(row.get("notes")),
             "functional_case", f"Missing observed input/output and bound evidence for {case_id}.")

    appearance = {row.get("id"): row for row in evidence.get("appearance_cases", [])}
    actual_sources = set()
    actual_files = set()
    for case_id in sorted(APPEARANCE_CASES):
        row = appearance.get(case_id, {})
        valid_actual = []
        for item in row.get("real_files", []):
            original = manifest_files.get(item.get("original_sha256"))
            if (original and item.get("preview_sha256") == original.get("preview", {}).get("sha256")
                    and _text(item.get("observed")) and _text(item.get("reviewer"))):
                valid_actual.append(item)
                actual_files.add(item["original_sha256"])
                actual_sources.update(original.get("sources", []))
        need(row.get("passed") is True and _evidence(row.get("evidence"))
             and _text(row.get("expected")) and _text(row.get("observed")) and _text(row.get("notes"))
             and bool(valid_actual), "appearance_case", f"Real file/preview-bound observation is required for {case_id}; synthetic examples alone are insufficient.")
        if case_id == "cross_source_style":
            sources = {s for item in valid_actual for s in manifest_files[item["original_sha256"]].get("sources", [])}
            need(len(sources) >= 2, "cross_source_style", "Compare actually reviewed style across at least two sources.")

    scenarios = []
    observed_kinds = set()
    for row in evidence.get("diversity_scenarios", []):
        projects = row.get("projects", [])
        pool = set(row.get("eligible_family_ids", []))
        ids = [p.get("project_id") for p in projects]
        seeds = [p.get("seed") for p in projects]
        valid = (_text(row.get("case_id")) and row.get("pool_kind") in {"rich", "scarce"}
                 and isinstance(row.get("query"), dict) and _text(row.get("review_notes"))
                 and _evidence(row.get("evidence")) and bool(pool) and len(projects) >= 2
                 and all(_text(value) for value in ids) and len(set(ids)) == len(ids)
                 and all(isinstance(value, (str, int)) for value in seeds) and len(set(seeds)) >= 2)
        for project in projects:
            for key in ("old_top_family_ids", "new_top_family_ids", "trial_downloaded_family_ids", "adopted_family_ids"):
                values = project.get(key)
                valid = valid and isinstance(values, list) and len(values) == len(set(values))
                # Old search may have scientifically invalid results: preserve
                # those as the baseline rather than deleting them for parity.
                if key != "old_top_family_ids":
                    valid = valid and isinstance(values, list) and set(values) <= pool
            valid = valid and bool(project.get("adopted_family_ids")) and _evidence(project.get("ledger_evidence"))
        if need(valid, "diversity_scenario", f"{row.get('case_id')}: provide qualified pool, different project seeds, actual adoption ledgers and old/new result lists."):
            observed_kinds.add(row["pool_kind"])
            scenarios.append({"case_id": row["case_id"], "pool_kind": row["pool_kind"],
                              "eligible_families": len(pool), "metrics": reuse_metrics(projects),
                              "review_notes": row["review_notes"]})
    need(observed_kinds == {"rich", "scarce"}, "rich_and_scarce", "Observe both rich and scarce qualified pools; legitimate scarce-object reuse is allowed.")
    return {"required_bindings": required, "appearance_reviewed_files": len(actual_files),
            "appearance_reviewed_sources": sorted(actual_sources), "diversity_observations": scenarios,
            "note": "Metrics describe observed runs and final adoption; no arbitrary diversity pass score is inferred.",
            "missing": missing}
