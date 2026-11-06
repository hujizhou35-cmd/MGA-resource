"""Build bounded visual-review candidates in cloud CI; never grant acceptance."""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import closing
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import sys
import tarfile
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mga_resources.catalog import search

MAX_PREVIEWS = 1600
MAX_PREVIEW_BYTES = 2 * 1024 * 1024
MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
MAX_TRANSFER_BYTES = 4 * 1024 * 1024 * 1024
MAX_SAMPLE_SHEETS = 12
SAMPLE_PER_SHEET = 20
CELL_W, CELL_H, HEADER_H = 330, 360, 150


def _json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _label(value):
    if isinstance(value, list):
        return ", ".join(str(part) for part in value) or "unknown"
    return str(value) if value not in (None, "") else "unknown"


def _preview_key(preview):
    archive = preview.get("archive") or {}
    url, member = archive.get("url", ""), archive.get("member", "")
    if not re.fullmatch(r"https://github\.com/[^/]+/[^/]+/releases/download/[^/]+/[^/]+\.tar\.gz", url):
        raise ValueError("Preview must name a public GitHub Release tar.gz")
    parts = PurePosixPath(member).parts
    if not parts or parts[0] != "previews" or ".." in parts or "\\" in member or not member.endswith(".png"):
        raise ValueError("Preview member must be a safe previews/*.png path")
    digest = preview.get("sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Preview SHA-256 is required")
    return url, member, digest


def descriptors(record):
    """Describe files separately, so sample strata retain format/style variants."""
    result = []
    for index, item in enumerate(record.get("files") or []):
        preview = item.get("preview")
        if not isinstance(preview, dict):
            continue
        try:
            key = _preview_key(preview)
            error = None
        except ValueError as exc:
            key, error = None, str(exc)
        result.append({
            "candidate_id": f"{record['id']}:file-{index}", "asset_id": record["id"],
            "family_id": record.get("family_id", record["id"]), "title": record.get("title"),
            "source": record.get("source"), "sources": record.get("sources") or [record.get("source")],
            "source_id": record.get("source_id"), "source_url": record.get("source_url"),
            "provenance": record.get("provenance", []), "file_source_records": item.get("source_records", []),
            "species": record.get("species"), "style": item.get("style") or record.get("style"),
            "type": item.get("structure", "unknown"), "format": item.get("format", "unknown"),
            "license": record.get("license"), "license_url": record.get("license_url"),
            "attribution": record.get("attribution"), "distribution_status": record.get("distribution_status"),
            "original_sha256": item.get("sha256"), "original_archive": item.get("archive"),
            "preview": preview, "preview_key": list(key) if key else None, "preview_error": error,
        })
    if not result:
        result.append({"candidate_id": record["id"] + ":missing", "asset_id": record["id"],
                       "family_id": record.get("family_id", record["id"]), "title": record.get("title"),
                       "source": record.get("source"), "sources": record.get("sources") or [record.get("source")],
                       "source_id": record.get("source_id"), "species": record.get("species"),
                       "style": record.get("style"), "type": "unknown", "format": "unknown",
                       "preview_key": None, "preview_error": "No archived preview"})
    return result


def stratified_sample(records, *, limit=240, seed="mga-review-v1"):
    """Deterministic round-robin across source/style/structure, never popularity."""
    if not 0 <= limit <= MAX_SAMPLE_SHEETS * SAMPLE_PER_SHEET:
        raise ValueError("Sample limit must be between 0 and 240")
    groups = defaultdict(list)
    candidates = {}
    for record in records:
        for item in descriptors(record):
            candidates[item["candidate_id"]] = item
            for source in item["sources"]:
                group = (_label(source), _label(item["style"]), item["type"])
                groups[group].append(item["candidate_id"])
    rank = lambda value: hashlib.sha256((seed + "\0" + value).encode()).hexdigest()
    for ids in groups.values():
        ids.sort(key=rank, reverse=True)
    selected, seen = [], set()
    # Hash ordering makes overflow reproducible without favoring alphabetically
    # early sources. Coverage of strata that do not fit is reported explicitly.
    order = sorted(groups, key=lambda key: rank(json.dumps(key)))
    while len(selected) < limit:
        progressed = False
        for group in order:
            while groups[group] and groups[group][-1] in seen:
                groups[group].pop()
            if groups[group]:
                key = groups[group].pop()
                seen.add(key)
                selected.append(candidates[key])
                progressed = True
                if len(selected) == limit:
                    break
        if not progressed:
            break
    coverage = []
    for group in sorted(groups):
        matches = [item["candidate_id"] for item in selected if group[0] in [_label(x) for x in item["sources"]]
                   and _label(item["style"]) == group[1] and item["type"] == group[2]]
        population = sum(group[0] in [_label(x) for x in item["sources"]]
                         and _label(item["style"]) == group[1] and item["type"] == group[2]
                         for item in candidates.values())
        coverage.append({"source": group[0], "style": group[1], "type": group[2],
                         "population_files": population, "selected_candidate_ids": matches})
    return selected, coverage


def plan_review(records, database, benchmark, *, sample_limit=240, seed="mga-review-v1", search_fn=search):
    concepts = benchmark["concepts"]
    if not 1 <= len(concepts) <= 60 or len(benchmark["queries"]) > 120:
        raise ValueError("Review is bounded to 60 concepts and 120 queries")
    concept_ids = [item["id"] for item in concepts]
    if len(set(concept_ids)) != len(concept_ids):
        raise ValueError("Concept IDs must be unique")
    for value in concept_ids:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,95}", value):
            raise ValueError("Unsafe concept ID")
    by_id = {item["id"]: item for item in records}
    queries, by_concept, candidates = [], defaultdict(dict), {}
    seen_queries, seen_languages = set(), set()
    for query in benchmark["queries"]:
        if query["id"] in seen_queries or query["concept_id"] not in concept_ids:
            raise ValueError("Duplicate query or unknown concept")
        language_key = (query["concept_id"], query["language"])
        if query["language"] not in {"zh", "en"} or language_key in seen_languages:
            raise ValueError("Each concept supports one Chinese and one English query")
        seen_queries.add(query["id"])
        seen_languages.add(language_key)
        retrieved = search_fn(database, query["query"], limit=10)
        rows = []
        for rank, record in enumerate(retrieved[:10], 1):
            asset_id = record["id"]
            if asset_id not in by_id:
                raise ValueError("Search index and finalized catalog disagree")
            choices = descriptors(by_id[asset_id])
            candidate = next((item for item in choices if item["preview_key"]), choices[0])
            key = candidate["candidate_id"]
            candidates[key] = candidate
            rows.append({"rank": rank, "asset_id": asset_id, "candidate_id": key,
                         "family_id": candidate["family_id"], "source": candidate["source"]})
            by_concept[query["concept_id"]].setdefault(key, []).append(
                {"query_id": query["id"], "language": query["language"], "rank": rank})
        queries.append({**query, "candidates": rows, "visual_judgment": None})
    concept_rows = []
    for concept in concepts:
        rows = [{"candidate_id": key, "rankings": ranks} for key, ranks in by_concept[concept["id"]].items()]
        if len(rows) > 20:
            raise ValueError("A concept must have at most two top-10 query lists")
        concept_rows.append({**concept, "sheet": f"concepts/{concept['id']}.png", "candidates": rows})
    sample, strata = stratified_sample(records, limit=sample_limit, seed=seed)
    candidates.update({item["candidate_id"]: item for item in sample})
    return {"schema_version": 1, "benchmark_version": benchmark.get("benchmark_version"),
            "status": "candidates_only_pending_independent_visual_review", "seed": seed,
            "queries": queries, "concepts": concept_rows, "candidates": candidates,
            "sample_candidate_ids": [item["candidate_id"] for item in sample], "sample_strata": strata,
            "limitations": ["Retrieval ranking is not visual acceptance.",
                            "Unknown species/style remain unknown; metadata labels need visual checking.",
                            "A family representative preview does not prove every original variant usable.",
                            "Samples do not prove whole-library visual quality or Illustrator editability."]}


class _HashedReader:
    def __init__(self, stream, budget):
        self.stream, self.budget = stream, budget
        self.digest, self.bytes_read = hashlib.sha256(), 0

    def read(self, size=-1):
        data = self.stream.read(size)
        self.bytes_read += len(data)
        self.budget[0] += len(data)
        if self.bytes_read > MAX_ARCHIVE_BYTES or self.budget[0] > MAX_TRANSFER_BYTES:
            raise ValueError("Cloud preview transfer byte limit exceeded")
        self.digest.update(data)
        return data


def fetch_previews(candidates, *, opener=None, archive_manifest=None):
    """Read each archive once, only decode selected PNG members, keep no originals."""
    wanted = defaultdict(dict)
    for item in candidates.values():
        if item["preview_key"]:
            url, member, digest = item["preview_key"]
            prior = wanted[url].get(member)
            if prior and prior != digest:
                raise ValueError("Conflicting hashes for one preview member")
            wanted[url][member] = digest
    if sum(len(items) for items in wanted.values()) > MAX_PREVIEWS:
        raise ValueError("Too many selected previews")
    if archive_manifest is not None:
        for url in wanted:
            if url not in archive_manifest:
                raise ValueError("Preview archive is absent from the selected Release")
    opener = opener or (lambda url: urlopen(Request(url, headers={"User-Agent": "MGA-resource-review/1"}), timeout=60))
    images, errors, transfers = {}, {}, []
    budget = [0]
    for url, members in sorted(wanted.items()):
        reader, local = None, {}
        try:
            if budget[0] >= MAX_TRANSFER_BYTES:
                raise ValueError("Cloud preview transfer byte limit exceeded")
            with closing(opener(url)) as stream:
                reader = _HashedReader(stream, budget)
                with tarfile.open(fileobj=reader, mode="r|gz") as archive:
                    encountered = set()
                    for entry in archive:
                        if entry.name not in members:
                            continue
                        if entry.name in encountered:
                            raise ValueError("Duplicate selected preview member in archive")
                        encountered.add(entry.name)
                        if not entry.isfile() or not 0 < entry.size <= MAX_PREVIEW_BYTES:
                            raise ValueError("Selected preview is not a bounded regular file")
                        with archive.extractfile(entry) as source:
                            data = source.read(MAX_PREVIEW_BYTES + 1)
                        key = (url, entry.name, members[entry.name])
                        if hashlib.sha256(data).hexdigest() != members[entry.name]:
                            raise ValueError("Selected preview SHA-256 mismatch")
                        with Image.open(io.BytesIO(data)) as image:
                            if image.format != "PNG" or image.width * image.height > 2048 * 2048:
                                raise ValueError("Selected preview is not a bounded PNG")
                            image.load()
                            thumb = image.convert("RGBA")
                            thumb.thumbnail((CELL_W - 20, 235), Image.Resampling.LANCZOS)
                            local[key] = thumb
                while reader.read(1024 * 1024):
                    pass
                if archive_manifest is not None:
                    manifest = archive_manifest[url]
                    if reader.bytes_read != manifest["size"] or "sha256:" + reader.digest.hexdigest() != manifest["digest"]:
                        raise ValueError("Release archive size or SHA-256 mismatch")
                images.update(local)
                for member, digest in members.items():
                    key = (url, member, digest)
                    if key not in local:
                        errors[key] = "Selected preview member missing from archive"
            status, error = "read_once", None
        except (OSError, ValueError, tarfile.TarError, EOFError) as exc:
            status, error = "failed", str(exc)
            for member, digest in members.items():
                errors[(url, member, digest)] = error
        transfers.append({"archive_url": url, "selected_members": len(members), "status": status,
                          "error": error, "compressed_bytes_read": reader.bytes_read if reader else 0})
    return images, errors, transfers


def _font_path(explicit=None):
    choices = [explicit] if explicit else [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simsun.ttc",
    ]
    for path in choices:
        if path and Path(path).is_file():
            return str(path)
    raise ValueError("A CJK-capable font is required for bilingual review labels (use --font)")


def render_sheet(path, title, subtitles, rows, candidates, images, *, font_path):
    if len(rows) > 20:
        raise ValueError("A review sheet is limited to 20 candidates")
    width, height = 5 * CELL_W, HEADER_H + max(1, math.ceil(len(rows) / 5)) * CELL_H
    sheet = Image.new("RGB", (width, height), "#f2f5f7")
    draw = ImageDraw.Draw(sheet)
    font, small = ImageFont.truetype(font_path, 21), ImageFont.truetype(font_path, 14)

    def text(x, y, value, max_width, use_font=small, fill="#15232b"):
        value = str(value).replace("\n", " ")
        while value and draw.textlength(value, font=use_font) > max_width:
            value = value[:-2] if value.endswith("…") else value[:-1] + "…"
        draw.text((x, y), value, font=use_font, fill=fill)

    text(15, 10, title, width - 30, font)
    for index, line in enumerate(subtitles[:4]):
        text(15, 45 + index * 24, line, width - 30)
    if not rows:
        text(25, HEADER_H + 25, "No retrieval candidates / 未检索到候选；仍需记录缺口", width - 50, font)
    positions = []
    for index, row in enumerate(rows):
        item = candidates[row["candidate_id"]]
        x, y = (index % 5) * CELL_W, HEADER_H + (index // 5) * CELL_H
        draw.rectangle((x + 5, y + 5, x + CELL_W - 5, y + CELL_H - 5), fill="white", outline="#ced6dc")
        key = tuple(item["preview_key"]) if item["preview_key"] else None
        preview = images.get(key)
        if preview:
            # Small checkerboard makes pale/transparent outlines inspectable.
            for cy in range(y + 10, y + 245, 16):
                for cx in range(x + 10, x + CELL_W - 10, 16):
                    draw.rectangle((cx, cy, min(cx + 15, x + CELL_W - 11), min(cy + 15, y + 244)),
                                   fill="#e7edf0" if ((cx - x) // 16 + (cy - y) // 16) % 2 else "#ffffff")
            sheet.paste(preview, (x + (CELL_W - preview.width) // 2, y + 12 + (230 - preview.height) // 2), preview)
        else:
            text(x + 15, y + 105, "Preview unavailable / 预览缺失", CELL_W - 30, fill="#a42020")
        ranks = ", ".join(f"{r['language']} #{r['rank']}" for r in row.get("rankings", [])) or "stratified sample / 分层抽样"
        labels = [f"{index + 1}. {ranks}", item.get("title") or "untitled", item["asset_id"],
                  f"{_label(item.get('source'))} | species: {_label(item.get('species'))}",
                  f"{item['type']} / {item['format']} | style: {_label(item.get('style'))}"]
        for offset, line in enumerate(labels):
            text(x + 12, y + 250 + offset * 20, line, CELL_W - 24)
        positions.append({"tile": index + 1, "candidate_id": item["candidate_id"],
                          "bounds_xywh": [x, y, CELL_W, CELL_H], "rankings": row.get("rankings", [])})
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path, optimize=True)
    return {"width": width, "height": height, "tiles": positions,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "size_bytes": path.stat().st_size}


def build_review(records, database, benchmark, output, *, font_path=None, sample_limit=240,
                 seed="mga-review-v1", opener=None, archive_manifest=None, search_fn=search):
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError("Review output must be empty to prevent stale sheets")
    font_path = _font_path(font_path)
    plan = plan_review(records, database, benchmark, sample_limit=sample_limit, seed=seed, search_fn=search_fn)
    images, errors, transfers = fetch_previews(plan["candidates"], opener=opener, archive_manifest=archive_manifest)
    for item in plan["candidates"].values():
        key = tuple(item["preview_key"]) if item["preview_key"] else None
        item["preview_status"] = "viewable" if key in images else "unavailable"
        if key in errors:
            item["preview_error"] = errors[key]
    output.mkdir(parents=True, exist_ok=True)
    for concept in plan["concepts"]:
        query_labels = [f"{item['language']}: {item['query']}" for item in plan["queries"] if item["concept_id"] == concept["id"]]
        subtitles = [*query_labels, f"Required species: {_label(concept.get('required_species'))}; {concept.get('visual_requirements', '')}",
                     "候选待独立视觉判断 / Candidates only; no acceptance granted"]
        concept["sheet_metadata"] = render_sheet(output / concept["sheet"],
            f"{concept['id']} | {concept['label_zh']} / {concept['label_en']}", subtitles,
            concept["candidates"], plan["candidates"], images, font_path=font_path)
    plan["sample_sheets"] = []
    ids = plan["sample_candidate_ids"]
    for start in range(0, len(ids), SAMPLE_PER_SHEET):
        path = f"samples/sample-{start // SAMPLE_PER_SHEET + 1:02d}.png"
        rows = [{"candidate_id": key} for key in ids[start:start + SAMPLE_PER_SHEET]]
        metadata = render_sheet(output / path, "全库分层抽样 / Library stratified sample",
                                [f"seed: {seed}; source × declared style × actual media structure",
                                 "Missing metadata stays unknown. Samples are not whole-library approval."],
                                rows, plan["candidates"], images, font_path=font_path)
        plan["sample_sheets"].append({"sheet": path, **metadata})
    judgments = {"schema_version": 1, "benchmark_version": plan["benchmark_version"], "reviewer": None,
                 "reviewed_at": None, "independent_from_metadata_matcher": None,
                 "instructions": benchmark.get("judging", {}), "acceptance_passed": None,
                 "queries": [{"query_id": query["id"], "query": query["query"], "language": query["language"],
                              "concept_id": query["concept_id"], "pass": None, "selected_candidate_ids": [],
                              "visual_evidence_notes": None, "source_evidence_checked": None,
                              "original_download_checked": None, "license_checked": None,
                              "species_matches": None, "gap_reason": None} for query in plan["queries"]],
                 "samples": [{"candidate_id": key, "usable": None, "visual_evidence_notes": None,
                              "issues": []} for key in ids]}
    summary = {"schema_version": 1, "status": plan["status"], "concept_sheets": len(plan["concepts"]),
               "sample_sheets": len(plan["sample_sheets"]), "query_count": len(plan["queries"]),
               "queries_without_candidates": [q["id"] for q in plan["queries"] if not q["candidates"]],
               "unique_candidate_files": len(plan["candidates"]), "unique_previews_loaded": len(images),
               "candidates_without_preview": sum(x["preview_status"] != "viewable" for x in plan["candidates"].values()),
               "sample_strata": len(plan["sample_strata"]),
               "unsampled_strata": [x for x in plan["sample_strata"] if not x["selected_candidate_ids"]],
               "archive_reads": transfers, "compressed_bytes_read": sum(x["compressed_bytes_read"] for x in transfers),
               "original_files_persisted": 0, "visually_judged_queries": 0, "retrieval_pass_rate": None,
               "acceptance_passed": False, "illustrator_verified": False,
               "limits": {"preview_files": MAX_PREVIEWS, "png_sheets": 72, "sheet_width": 1650, "sheet_height": 1590}}
    _json(output / "mapping.json", plan)
    _json(output / "judgments.blank.json", judgments)
    _json(output / "summary.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--assembly-prefix", required=True, help="Exact assembly-RUN-ATTEMPT prefix")
    parser.add_argument("--sample-limit", type=int, default=240)
    parser.add_argument("--font")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    if os.environ.get("GITHUB_ACTIONS") != "true":
        parser.error("Bulk review preparation runs only on GitHub Actions; local tests use tiny synthetic archives")
    if not re.fullmatch(r"assembly-[0-9]+-[0-9]+", args.assembly_prefix):
        parser.error("Expected exact assembly-RUN-ATTEMPT prefix")
    sys.path.insert(0, str(ROOT / "scripts"))
    from assemble import gh, list_release_assets
    repository = os.environ["GITHUB_REPOSITORY"]
    run_id = os.environ["GITHUB_RUN_ID"] + "-" + os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    base = ROOT / "build" / "visual-review" / run_id
    incoming, output = base / "metadata", base / "review"
    incoming.mkdir(parents=True, exist_ok=True)
    assets = list_release_assets(repository, args.release_tag)
    paths = {}
    for extension in ("catalog.jsonl", "catalog.sqlite"):
        filename = f"{args.assembly_prefix}.{extension}"
        if filename not in assets:
            raise ValueError(f"Assembly asset missing: {filename}")
        gh("release", "download", args.release_tag, "--pattern", filename, "--dir", str(incoming), "--clobber")
        path = incoming / filename
        if "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest() != assets[filename].get("digest"):
            raise ValueError(f"Assembly asset digest mismatch: {filename}")
        paths[extension] = path
    records = [json.loads(line) for line in paths["catalog.jsonl"].read_text(encoding="utf-8").splitlines() if line.strip()]
    benchmark_path = ROOT / "data" / "benchmark.json"
    benchmark = json.loads(benchmark_path.read_text(encoding="utf-8"))
    manifest = {value["browser_download_url"]: {"size": value["size"], "digest": value.get("digest")}
                for name, value in assets.items() if name.endswith(".tar.gz")}
    summary = build_review(records, paths["catalog.sqlite"], benchmark, output, font_path=args.font,
                           sample_limit=args.sample_limit, archive_manifest=manifest)
    summary.update({"assembly_prefix": args.assembly_prefix, "collection_release": args.release_tag,
                    "catalog_sha256": hashlib.sha256(paths["catalog.jsonl"].read_bytes()).hexdigest(),
                    "benchmark_sha256": hashlib.sha256(benchmark_path.read_bytes()).hexdigest(),
                    "tool_commit": os.environ.get("GITHUB_SHA"), "run_id": run_id})
    _json(output / "summary.json", summary)
    bundle = base / f"review-{run_id}.tar.gz"
    with tarfile.open(bundle, "w:gz") as archive:
        for path in sorted(output.rglob("*")):
            if path.is_file():
                archive.add(path, arcname=path.relative_to(output).as_posix())
    report = base / f"review-{run_id}.summary.json"
    _json(report, {**summary, "bundle": bundle.name, "bundle_bytes": bundle.stat().st_size,
                   "bundle_sha256": hashlib.sha256(bundle.read_bytes()).hexdigest()})
    if args.publish:
        gh("release", "upload", args.release_tag, str(bundle), str(report))
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
            stream.write(f"## Visual review candidates\n\n{summary['concept_sheets']} concept sheets; "
                         f"{summary['sample_sheets']} sample sheets; {summary['unique_previews_loaded']} unique previews.\n\n"
                         f"Missing previews: {summary['candidates_without_preview']}. "
                         "Independent judgments are blank. Acceptance has not passed.\n")


if __name__ == "__main__":
    main()
