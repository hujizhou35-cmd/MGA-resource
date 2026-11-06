"""Compare shared search and the retained baseline using metadata only.

No artwork is fetched and no relevance/visual acceptance is inferred from IDs,
tags, overlap or the existence of ten search results.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import statistics
import sqlite3
from contextlib import closing
from collections import defaultdict
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mga_resources.catalog import _search_core, rebuild_search_index, search_legacy, search_page


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def candidate_coverage(queries):
    """Describe missing candidates, never infer visual/relevance acceptance."""
    categories=defaultdict(list)
    concepts=defaultdict(list)
    for query in queries:
        categories[query["category"]].append(query)
        concepts[query["concept_id"]].append(query)
    missing=[]
    for concept_id,rows in sorted(concepts.items()):
        absent=[row for row in rows if not row["shared_ids"]]
        if not absent:
            continue
        reasons=[]
        for row in absent:
            pushed=row["diagnostics"]["sqlite_pushdown"]
            rejected=pushed["rejections_by_condition_may_overlap"]
            unknown={key:value for key,value in rejected.items() if key.startswith("unknown:") and value}
            reasons.append({"language":row["language"],"query":row["query"],
                            "text_matched_variants":pushed["text_matched_variants"],
                            "unknown_conditions":unknown,
                            "known_or_unknown_filter_rejections_may_overlap":{key:value for key,value in rejected.items() if key.startswith("hard_filter:") and value},
                            "interpretation":"no_text_candidates_needs_source_or_vocabulary_review" if not pushed["text_matched_variants"] else "has_text_candidates_but_scientific_or_appearance_evidence_unknown" if unknown else "has_text_candidates_but_hard_conditions_not_satisfied"})
        missing.append({"concept_id":concept_id,"category":rows[0]["category"],"missing_languages":[row["language"] for row in absent],"both_languages_empty":len(absent)==len(rows),"observations":reasons})
    return {"status":"candidate_availability_only_not_relevance_acceptance",
            "categories":{category:{"total_queries":len(rows),"nonempty_queries":sum(bool(row["shared_ids"]) for row in rows),
                "total_concepts":len({row["concept_id"] for row in rows}),
                "concepts_nonempty_in_both_languages":sum(all(row["shared_ids"] for row in group) for key,group in concepts.items() if group[0]["category"]==category),
                "independent_relevance_accepted":None} for category,rows in sorted(categories.items())},
            "concepts_with_empty_queries":missing}


def resolve_index(catalog_path, output, index_path=None):
    """Reuse only an exact catalog/current-policy index; never rewrite it."""
    started = time.perf_counter()
    if index_path is None:
        db = output / "catalog.sqlite"
        catalog_sha = rebuild_search_index(catalog_path, db)
        return db, catalog_sha, {"reused": False, "build_seconds": time.perf_counter()-started,
                                 "validation_seconds": None}
    db = Path(index_path).resolve()
    catalog_sha = digest(catalog_path)
    with closing(sqlite3.connect(db.as_uri()+"?mode=ro", uri=True)) as connection:
        metadata = dict(connection.execute("SELECT key, value FROM index_metadata"))
        if metadata.get("catalog_version") != catalog_sha:
            raise ValueError("Reused index does not bind this exact catalog")
        if connection.execute("PRAGMA user_version").fetchone()[0] != 9:
            raise ValueError("Reused index must have the current v9 schema")
    expected_ranking = _search_core({"action": "prepare", "records": []})["ranking_version"]
    if metadata.get("ranking_version") != expected_ranking:
        raise ValueError("Reused index ranking version differs from the current shared core")
    return db, catalog_sha, {"reused": True, "build_seconds": None,
                            "validation_seconds": time.perf_counter()-started}


def diagnose(catalog_path, benchmark_path, output, index_path=None):
    output.mkdir(parents=True, exist_ok=True)
    db, catalog_sha, index_info = resolve_index(catalog_path, output, index_path)
    with closing(sqlite3.connect(db.resolve().as_uri()+"?mode=ro", uri=True)) as connection:
        summary={"catalog_sha256":catalog_sha,
                 "logical_families":connection.execute("SELECT count(*) FROM assets").fetchone()[0],
                 "unique_original_files":connection.execute("SELECT count(DISTINCT file_sha256) FROM file_variants").fetchone()[0],
                 "ranking_version":connection.execute("SELECT value FROM index_metadata WHERE key='ranking_version'").fetchone()[0],
                 "search_index_version":connection.execute("PRAGMA user_version").fetchone()[0]}
    print(f"{'Reused verified' if index_info['reused'] else 'Rebuilt'} index of {summary['logical_families']} families", flush=True)
    benchmark = json.loads(benchmark_path.read_text(encoding="utf-8"))
    queries = []
    for index, query in enumerate(benchmark["queries"]):
        start = time.perf_counter()
        legacy = search_legacy(db, query["query"])
        legacy_ms = (time.perf_counter() - start) * 1000
        start = time.perf_counter()
        page = search_page(db, query["query"], seed="diagnostic-fixed-project")
        new_ms = (time.perf_counter() - start) * 1000
        old_ids, new_ids = [row["id"] for row in legacy], [row["family_id"] for row in page["results"]]
        queries.append({**query, "legacy_ids": old_ids, "shared_ids": new_ids,
                        "selected_file_sha256": [row["selected_file"]["sha256"] for row in page["results"]],
                        "top10_family_overlap": len(set(old_ids) & set(new_ids)),
                        "legacy_milliseconds": legacy_ms, "shared_adapter_milliseconds": new_ms,
                        "shared_core_milliseconds": page["adapter_metrics"]["core_milliseconds"],
                        "adapter_metrics": page["adapter_metrics"],
                        "diagnostics": page["diagnostics"], "requirements": page["query_plan"]["requirements"],
                        "independent_relevance_judgment": None})
        if (index + 1) % 20 == 0:
            print(f"Compared {index + 1}/{len(benchmark['queries'])} fixed queries", flush=True)
    broad = []
    for query in ("cell", "mouse", "liver", "", "彩色小鼠"):
        start = time.perf_counter()
        page = search_page(db, query, seed="wide-query")
        broad.append({"query": query, "adapter_milliseconds": (time.perf_counter()-start)*1000,
                      "core_milliseconds": page["adapter_metrics"]["core_milliseconds"],
                      "adapter_metrics": page["adapter_metrics"],
                      "selected": [{"title": r["title"], "source": r["selected_source"], "file_sha256": r["selected_file"]["sha256"], "science": r["selected_science"]} for r in page["results"]],
                      "result_count": len(page["results"]), "diagnostics": page["diagnostics"]})
    rotation = []
    for query in ("mouse", "cell", "liver", "unobtainium"):
        runs=[]
        for seed in ("project-a", "project-b", "project-c", "project-d", "project-e"):
            page=search_page(db,query,seed=seed)
            runs.append({"seed":seed,"families":[r["family_id"] for r in page["results"]],"first_candidate":page["results"][0]["family_id"] if page["results"] else None})
        sets=[set(run["families"]) for run in runs]
        overlaps=[len(sets[i]&sets[j]) for i in range(len(sets)) for j in range(i+1,len(sets))]
        rotation.append({"query":query,"runs":runs,"union_top10_families":len(set.union(*sets)),
                         "mean_pairwise_overlap":statistics.mean(overlaps),
                         "unique_first_candidates":len({run["first_candidate"] for run in runs if run["first_candidate"]}),
                         "final_adoptions_observed":False})
    report={"schema_version":1,"status":"diagnostic_not_acceptance","input_catalog_sha256":digest(catalog_path),
            "rebuilt_catalog_sha256":None if index_info["reused"] else summary["catalog_sha256"],
            "index_catalog_sha256":summary["catalog_sha256"],"benchmark_sha256":digest(benchmark_path),
            "search_core_sha256":digest(ROOT / "mga_resources/search-core.mjs"),
            "ranking_version":summary["ranking_version"],"search_index_version":summary["search_index_version"],
            "logical_families":summary["logical_families"],"unique_original_files":summary["unique_original_files"],
            "sqlite_bytes":db.stat().st_size,"sqlite_sha256":digest(db),
            "index_reused":index_info["reused"],"index_path":str(db),
            "index_build_seconds":index_info["build_seconds"],
            "index_validation_seconds":index_info["validation_seconds"],
            "queries":queries,"broad_queries":broad,"rotation":rotation,
            "summary":{"queries":len(queries),"legacy_nonempty":sum(bool(q["legacy_ids"]) for q in queries),
                       "shared_nonempty":sum(bool(q["shared_ids"]) for q in queries),
                       "median_adapter_milliseconds":statistics.median(q["shared_adapter_milliseconds"] for q in queries),
                       "median_core_milliseconds":statistics.median(q["shared_core_milliseconds"] for q in queries)},
            "limitations":["Input metadata may predate appearance backfill; unknown appearance does not satisfy a hard filter.",
                           "A nonempty list, metadata overlap, or seed coverage is not an independent medical-relevance judgment.",
                           "Rotation records candidates only; actual final selections require project adoption records.",
                           "Node local timings are not Cloudflare CPU measurements. Broad-query payload/cost must be addressed before Worker deployment.",
                           "No benchmark IDs or expected asset lists are used in query/ranking logic."]}
    (output / "search-diagnostic.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    gaps={"bindings":{key:report[key] for key in ("input_catalog_sha256","benchmark_sha256","search_core_sha256","ranking_version","search_index_version")},**candidate_coverage(queries)}
    (output / "retrieval-gaps.json").write_text(json.dumps(gaps,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report["summary"],ensure_ascii=False),flush=True)
    return report


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog",type=Path,required=True)
    parser.add_argument("--benchmark",type=Path,default=ROOT/"data/benchmark.json")
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--index",type=Path,help="Reuse an existing v9 index bound to the exact catalog and current shared ranking version")
    args=parser.parse_args()
    diagnose(args.catalog,args.benchmark,args.output,args.index)
