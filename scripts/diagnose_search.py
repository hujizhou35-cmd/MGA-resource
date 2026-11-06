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
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mga_resources.catalog import finalize, search_legacy, search_page


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def diagnose(catalog_path, benchmark_path, output):
    output.mkdir(parents=True, exist_ok=True)
    records = [json.loads(line) for line in catalog_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    started = time.perf_counter()
    summary = finalize(records, output / "rebuilt")
    index_seconds = time.perf_counter() - started
    print(f"Rebuilt {summary['logical_families']} families in {index_seconds:.2f}s", flush=True)
    db = output / "rebuilt/catalog.sqlite"
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
            "rebuilt_catalog_sha256":summary["catalog_sha256"],"benchmark_sha256":digest(benchmark_path),
            "logical_families":summary["logical_families"],"unique_original_files":summary["unique_original_files"],
            "index_build_seconds":index_seconds,"queries":queries,"broad_queries":broad,"rotation":rotation,
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
    print(json.dumps(report["summary"],ensure_ascii=False),flush=True)
    return report


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog",type=Path,required=True)
    parser.add_argument("--benchmark",type=Path,default=ROOT/"data/benchmark.json")
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    diagnose(args.catalog,args.benchmark,args.output)
