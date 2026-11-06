"""Cloud preview review for finished information products; keywords only nominate."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import os
from pathlib import Path
import re
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from review_releases import add_release_options, load_review_inputs, release_bindings
from backfill_appearance import download_checked, gh, sha
from build_scope_review import build_review, describe_review_files, write_json, MAX_PACK_BYTES, MAX_FILES

NOMINATION_VERSION = "content-scope-nominations-1"
RULES = {
    "flow_or_protocol": r"\b(flow.?chart|flow diagram|consort|prisma|protocol|workflow|work flow|pipeline|decision tree)\b",
    "study_design": r"\b(cohort|study design|experimental design|research design|clinical trial|randomi[sz]\w*|recruitment|allocation|timeline|study overview)\b",
    "possible_results": r"\b(results?|plots?|heat.?map|kaplan|survival curve|bar chart|pie chart|scatter|volcano|boxplot|charts?|histogram|venn|statistic\w*|regression|pca|umap|tsne)\b",
    "possible_finished_document": r"\b(tables?|templates?|graphical abstract|infographic|poster|manuscript|publication|figures?|letters?|reports?|documents?|certificates?)\b",
    "text_computing": r"\b(code|coding|script|programming|command|terminal|algorithm|software|computer screen)\b",
    "broad_diagram": r"\b(diagram|model|scheme|schematic|process|pathway|network|analysis|data|mechanism|signaling|signalling|circuit|cascade)\b",
    "multilingual": r"流程|队列|研究设计|实验设计|研究结果|结果图|统计图|表格|模板|模式图|フローチャート|模式図|研究計画",
}


def nomination(record):
    # Do not read URLs/extensions as content. Variant/source titles can contain
    # information lost by family-level deduplication, so preserve those labels.
    contexts = [record, *record.get("variants", [])]
    for file in record.get("files", []):
        contexts.extend(file.get("source_records") or [])
    titles = sorted({str(r.get("title") or "") for r in contexts})
    text = " | ".join(titles)
    matches = {key: sorted(set(re.findall(pattern, text, re.I))) for key, pattern in RULES.items()}
    matches = {key: values for key, values in matches.items() if values}
    if not text.strip() or (record.get("source") in {"scidraw", "scisvg", "commons", "wikimedia-commons", "openclipart"}
                            and all(len(title.strip()) <= 3 for title in titles)):
        matches["weak_title_requires_visual_check"] = titles
    return {"rules": matches, "titles_checked": titles} if matches else None


def descriptors(record, unreviewable=None):
    # Reuse the exact SHA/member validation and descriptor contract. Each input
    # file is deliberately nominated as a one-file record, regardless of format.
    result = {}
    for file in record.get("files", []):
        if not (file.get("preview") or {}).get("sha256"):
            if unreviewable is not None:
                unreviewable.append({"asset_id": record["id"], "original_sha256": file.get("sha256"),
                                     "status": "pending_no_verified_preview"})
            continue
        rows = describe_review_files({**record, "files": [file]})
        for key, row in rows.items():
            row["source_records"] = [{k: v for k, v in origin.items() if k in {"source", "source_id", "title", "source_url"}}
                                     for origin in file.get("source_records", [])]
            result[key] = row
    return result


def select_candidates(records, *, offset=0, limit=1000, controls_per_source=2, source_filter=None, full_source=False):
    if offset < 0 or not 1 <= limit <= MAX_FILES or not 0 <= controls_per_source <= 5:
        raise ValueError("Use bounded positive range and 0-5 controls per source")
    if full_source and not source_filter:
        raise ValueError("Full-source review requires an explicit source filter")
    nominees, controls, reactome = [], defaultdict(list), defaultdict(list)
    scanned_records = scanned_files = 0
    for record in records:
        if source_filter and record.get("source") != source_filter:
            continue
        scanned_records += 1
        scanned_files += len(record.get("files", []))
        reason = ({"rules": {"explicit_full_source_review": [source_filter]}, "titles_checked": [record.get("title")]}
                  if full_source else nomination(record))
        if reason:
            nominees.append((record, {"kind": "metadata_nomination_only", **reason}))
        else:
            source = record.get("source", "unknown")
            controls[source].append(record)
            controls[source].sort(key=lambda r: sha((str(r.get("id")) + ":content-scope-control-v1").encode()))
            del controls[source][controls_per_source:]
            if record.get("source") == "reactome":
                categories = set(record.get("tags") or ["uncategorized"])
                for category in categories:
                    reactome[str(category)].append(record)
                    reactome[str(category)].sort(key=lambda r: sha((r["id"] + ":reactome-content-v1").encode()))
                    del reactome[str(category)][2:]
    control_rows = []
    for source, rows in sorted(controls.items()):
        # Frozen deterministic controls inspect possible misses; no coverage claim.
        rows = sorted(rows, key=lambda r: sha((str(r.get("id")) + ":content-scope-control-v1").encode()))
        for record in rows[:controls_per_source]:
            control_rows.append((record, {"kind": "deterministic_unmatched_control", "rules": {}, "titles_checked": [record.get("title")]}))
    # Real Reactome category representatives include short text/symbol names
    # that generic figure keywords miss. This is nomination, never exclusion.
    represented = {record["id"] for record, _ in nominees + control_rows}
    reactome_rows = []
    for category, rows in sorted(reactome.items()):
        for record in sorted(rows, key=lambda r: sha((r["id"] + ":reactome-content-v1").encode()))[:2]:
            if record["id"] not in represented:
                represented.add(record["id"])
                reactome_rows.append((record, {"kind": "reactome_category_representative", "rules": {"source_category": [category]}, "titles_checked": [record.get("title")]}))
    control_rows.extend(reactome_rows)
    all_candidates, unreviewable = {}, []
    for record, reason in sorted(nominees + control_rows, key=lambda value: value[0]["id"]):
        for key, row in descriptors(record, unreviewable).items():
            row["nomination"] = reason
            row["selection_reason"] = reason["kind"]
            row["contains_text"] = "unknown"
            row["potential_cleanup"] = "unknown"
            all_candidates[key] = row
    ordered = sorted(all_candidates, key=lambda k: (all_candidates[k]["source"], all_candidates[k]["asset_id"], k))
    chosen = ordered[offset:offset + limit]
    report = {"nomination_version": NOMINATION_VERSION, "metadata_records_scanned": scanned_records,
              "metadata_files_scanned": scanned_files, "metadata_nomination_records": len(nominees),
              "control_records": len(control_rows), "reactome_category_representatives": len(reactome_rows), "total_candidate_files": len(ordered), "selected_files": len(chosen),
              "offset": offset, "limit": limit, "remaining_after_range": max(0, len(ordered) - offset - len(chosen)),
              "ordered_candidate_ids_sha256": sha(("\n".join(ordered) + "\n").encode()),
              "source_filter": source_filter, "full_source_review": full_source, "unreviewable_files": unreviewable,
              "selected_source_counts": dict(Counter(all_candidates[k]["source"] for k in chosen)),
              "selection_is_a_scope_verdict": False, "unmatched_records_are_not_automatically_accepted": True,
              "review_rule": "Review actual content. Labels alone do not exclude object illustrations. Tables, text-box workflow templates, complete research designs and paper result figures are outside v1. Pure molecular nameplates consisting only of text plus a colored oval/rounded box are excluded; structured receptors/cells/mechanistic components are retained. Removing text while retaining an independent object/experiment scene is a useful but non-mechanical check."}
    return {key: all_candidates[key] for key in chosen}, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-tag", required=True)
    add_release_options(parser)
    parser.add_argument("--assembly-prefix", required=True)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--source-filter")
    parser.add_argument("--full-source", action="store_true")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    if os.environ.get("GITHUB_ACTIONS") != "true":
        parser.error("Real archived previews are read only in cloud CI")
    if not re.fullmatch(r"assembly-[0-9]+-[0-9]+", args.assembly_prefix):
        parser.error("Use exact assembly-RUN-ATTEMPT")
    repository = os.environ["GITHUB_REPOSITORY"]
    run = os.environ["GITHUB_RUN_ID"] + "-" + os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    base = ROOT / "build" / "content-scope-review" / run
    incoming, output = base / "metadata", base / "review"
    incoming.mkdir(parents=True, exist_ok=True)
    assets, manifest, archive_tags = load_review_inputs(repository, args.release_tag, args.archive_release_tag)
    catalog = download_checked(args.assembly_prefix + ".catalog.jsonl", assets[args.assembly_prefix + ".catalog.jsonl"], incoming, repository, args.release_tag)
    report = download_checked(args.assembly_prefix + ".report.json", assets[args.assembly_prefix + ".report.json"], incoming, repository, args.release_tag)
    catalog_digest = sha(catalog.read_bytes())
    declared_digest = json.loads(report.read_text(encoding="utf-8")).get("summary", {}).get("catalog_sha256")
    if declared_digest and declared_digest != catalog_digest:
        raise ValueError("Assembly report does not bind this catalog")
    with catalog.open(encoding="utf-8") as stream:
        selected, nomination_report = select_candidates((json.loads(line) for line in stream if line.strip()), offset=args.offset, limit=args.limit,
                                                       source_filter=args.source_filter, full_source=args.full_source)
    if not selected:
        raise ValueError("Selected candidate range is empty")
    mapping = build_review([], manifest, output, selected_candidates=selected,
        heading="Content scope review / labels do not exclude objects; keywords only nominate",
        font_path="/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
    mapping["nomination_report"] = nomination_report
    mapping["bindings"] = {"assembly_prefix": args.assembly_prefix, "catalog_sha256": catalog_digest, "assembly_report_sha256": sha(report.read_bytes()),
                           **release_bindings(args, archive_tags)}
    write_json(output / "mapping.json", mapping)
    write_json(output / "judgments.template.json", {"schema_version": 1, "reviewer": None,
        "items": [{"tile_id": t["tile_id"], "file_ids": t["file_ids"], "scope_verdict": None,
                   "observations": None, "text_content": {"status": "unknown", "kinds": [], "cleanup_advice": "unknown", "editable_text": "unknown"}}
                  for t in mapping["tiles"]]})
    name = "content-scope-review-" + run
    archive = base / (name + ".tar.gz")
    with tarfile.open(archive, "w:gz") as tar:
        for path in sorted(output.iterdir()):
            tar.add(path, arcname=path.name)
    if archive.stat().st_size > MAX_PACK_BYTES:
        raise ValueError("Review pack exceeds 32 MiB; use a smaller explicit candidate range")
    summary = {**nomination_report, "bindings": mapping["bindings"], "tile_count": mapping["tile_count"],
        "sheets": mapping["sheets"], "preview_errors": mapping["preview_errors"], "scope_acceptance_passed": False,
        "archive": {"name": archive.name, "size_bytes": archive.stat().st_size, "sha256": sha(archive.read_bytes())}}
    summary_path = base / (name + ".summary.json")
    write_json(summary_path, summary)
    if args.publish:
        gh("release", "upload", args.publish_tag or args.release_tag, str(archive), str(summary_path), "--repo", repository)
    print(json.dumps({k: summary[k] for k in ("total_candidate_files", "selected_files", "tile_count", "preview_errors", "archive")}))


if __name__ == "__main__":
    main()
