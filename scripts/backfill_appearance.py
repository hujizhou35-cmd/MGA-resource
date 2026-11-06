"""Cloud-only incremental appearance measurement from immutable Release packs."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import closing
import copy
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import subprocess
import sys
import tarfile
import time
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mga_resources.appearance import ANALYSIS_VERSION, analyze_appearance, cache_key, evidence_revision
from mga_resources.catalog import finalize

MAX_SVG_BYTES = 2 * 1024 * 1024
MAX_PREVIEW_BYTES = 8 * 1024 * 1024
MAX_BUFFER_BYTES = 1024 * 1024 * 1024
MAX_ARCHIVE_BYTES = 8 * 1024 * 1024 * 1024
CACHE_PATTERN = re.compile(r"appearance-[0-9]+-[0-9]+(?:-b[0-9]+)?\.appearance\.jsonl")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def valid_sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def write_jsonl(path, rows):
    # GitHub upload does not accept a zero-byte artifact; a blank JSONL is valid.
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        found = False
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            found = True
        if not found:
            stream.write("\n")


def gh(*args):
    result = subprocess.run(["gh", *args], text=True, encoding="utf-8", capture_output=True, timeout=180)
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    return result.stdout


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def prior_cache_candidates(assets, verified_aggregates, skipped):
    """Try each run's final cache first; retain checkpoints as failure fallback.

    A final aggregate contains all accepted rows from that run's checkpoints.
    The caller marks an aggregate only after its whole asset digest and JSONL
    parse pass. A missing or rejected aggregate never suppresses checkpoints.
    """
    groups = defaultdict(list)
    for name, asset in assets.items():
        if CACHE_PATTERN.fullmatch(name):
            run = name.removesuffix(".appearance.jsonl").split("-b", 1)[0]
            groups[run].append((name, asset))
    ordered = sorted(groups, key=lambda run: max(
        (asset.get("updated_at", ""), name) for name, asset in groups[run]), reverse=True)
    for run in ordered:
        aggregate = run + ".appearance.jsonl"
        entries = sorted(groups[run], key=lambda pair: (
            pair[0] == aggregate, pair[1].get("updated_at", ""), pair[0]), reverse=True)
        for name, asset in entries:
            if name != aggregate and run in verified_aggregates:
                skipped.append(name)
                continue
            yield name, asset, run, name == aggregate


def release_cache_candidates(releases, verified_aggregates, skipped):
    """Retain the owning Release when selecting caches with colliding names."""
    ordered = sorted(releases, key=lambda tag: max(
        (asset.get("updated_at", "") for name, asset in releases[tag].items()
         if CACHE_PATTERN.fullmatch(name)), default=""), reverse=True)
    for tag in ordered:
        accepted, local_skipped = set(), []
        for name, asset, run, aggregate in prior_cache_candidates(releases[tag], accepted, local_skipped):
            yield tag, name, asset, (tag, run), aggregate
            if (tag, run) in verified_aggregates:
                accepted.add(run)
        skipped.extend({"release_tag": tag, "asset": name} for name in local_skipped)


def load_release_context(repository, metadata_tag, archive_tags=(), publish_tag=None):
    """Resolve inputs without moving immutable original or preview URLs."""
    from assemble import list_release_assets
    from review_releases import load_review_inputs
    releases = {}
    def listing(repo, tag):
        if tag not in releases:
            releases[tag] = list_release_assets(repo, tag)
        return releases[tag]
    assets, manifest, tags = load_review_inputs(repository, metadata_tag, archive_tags, list_assets=listing)
    output_tag = publish_tag or metadata_tag
    listing(repository, output_tag)  # Continuation caches may be in the output Release.
    bindings = {"metadata_release_tag": metadata_tag, "archive_release_tags": tags,
                "cache_release_tags": list(releases), "publish_tag": output_tag}
    return assets, manifest, releases, bindings


def source_style_evidence(record, file):
    """Use exact file-level source labels; never label an entire library style."""
    digest = file.get("sha256")
    origins = file.get("source_records") or []
    variants = [variant for variant in record.get("variants", [])
                if any(value.get("sha256") == digest for value in variant.get("files", []))]
    contexts = [*origins, *variants]
    if not contexts:
        contexts = [record]
    output = []
    for context in contexts:
        source = context.get("source", record.get("source"))
        source_id = context.get("source_id", "")
        if source == "healthicons":
            match = re.fullmatch(r"public/icons/svg/(filled(?:-24px)?|outline(?:-24px)?)/.+\.svg", source_id)
            if not match:
                continue
            style = match.group(1)
            url = context.get("source_url") or file.get("url")
            # Exact upstream file paths supply the filled/outline labels.
            if not url or "resolvetosavelives/healthicons/" not in url or source_id not in unquote(url):
                continue
            output.append({"kind": "official_source_file", "scope": "file", "file_sha256": digest,
                "source_url": url, "statement": f"Official Health Icons file directory: {style}; {source_id}",
                "labels": {"representation": ["line_art" if style.startswith("outline") else "flat_fill"],
                           "series": {"id": "healthicons:" + style, "label": "Health Icons " + style,
                                      "author": "Resolve to Save Lives / Health Icons contributors"}}})
        elif source == "arcadia":
            # A matching official gallery assertion is necessary. Raster is a
            # format label and is deliberately not translated into a style.
            member = file.get("archive_member") or source_id
            if source_id != member:
                continue
            filename = PurePosixPath(member).name.lower()
            if "silhouette" in filename:
                style, labels = "silhouette", {"representation": ["silhouette"]}
            elif "tricolor" in filename:
                style, labels = "tricolor-stroke", {}
            else:
                continue
            candidates = [context, record] if len(record.get("files", [])) == 1 else [context]
            annotated = next((item for item in candidates if item.get("metadata_status") == "matched_official_gallery"), None)
            # Finalized families retain the exact file's gallery-derived tags
            # in source_records/variants, while the shared gallery URL/revision
            # stays on the family. Require both, and the exact archive member;
            # the family's selected top-level style never supplies this label.
            if (annotated is None and record.get("metadata_status") == "matched_official_gallery"
                    and context.get("source_id") == member and style in (context.get("tags") or [])):
                annotated = record
            # Older finalized catalogs retained exact upstream file locators
            # but dropped per-file tags. The published archive's explicit
            # style directory plus matching basename supplies file evidence;
            # the family's selected top-level style still supplies nothing.
            published_directory = (
                re.fullmatch(r"Silhouette SVGs/[^/]+-silhouette\.svg", member)
                if style == "silhouette" else
                re.fullmatch(r"Tricolor \+ stroke (SVGs|PNGs)/[^/]+-tricolorstroke\.(svg|png)", member))
            if (annotated is None and published_directory
                    and context.get("source_id") == member
                    and context.get("source") == "arcadia"
                    and str(context.get("source_url", "")).startswith("https://zenodo.org/records/")
                    and record.get("metadata_status") == "matched_official_gallery"
                    and record.get("metadata_source") == "https://arcadia-science.github.io/organism-illustration-library/"):
                annotated = record
            if annotated is None:
                continue
            labels["series"] = {"id": "arcadia:" + style, "label": "Arcadia organism library " + style,
                                "author": "Arcadia Science"}
            url = annotated.get("metadata_source")
            if not isinstance(url, str) or not url.startswith("https://"):
                continue
            output.append({"kind": "official_source_file", "scope": "file", "file_sha256": digest,
                "source_url": url, "statement": f"Matched official gallery file {member}; series {style}; metadata revision {annotated.get('metadata_revision', 'unknown')}",
                "labels": labels})
    unique = {json.dumps(item, ensure_ascii=False, sort_keys=True): item for item in output}
    return [unique[key] for key in sorted(unique)]


def load_overrides(path):
    if not path.exists():
        return []
    value = json.loads(path.read_text(encoding="utf-8"))
    rows = value.get("evidence", []) if isinstance(value, dict) else value
    if not isinstance(rows, list):
        raise ValueError("Appearance overrides must contain an evidence list")
    return rows


def _location(value, prefix, archive_manifest):
    location = value.get("archive") or {}
    url, member = location.get("url"), location.get("member")
    if url not in archive_manifest:
        raise ValueError("Archive is absent from the selected Release")
    if (not isinstance(member, str) or not member.startswith(prefix + "/") or "\\" in member or ":" in member
            or any(part in {"", ".", ".."} for part in member.split("/"))):
        raise ValueError("Unsafe or unexpected archive member path")
    if not valid_sha(value.get("sha256")):
        raise ValueError("Archive member lacks SHA-256")
    return url, member


def plan_jobs(records, archive_manifest, overrides=()):
    jobs, targets, pending = {}, {}, {}
    for record_index, record in enumerate(records):
        for file_index, file in enumerate(record.get("files", [])):
            target = (record_index, file_index)
            if not valid_sha(file.get("sha256")):
                pending[target] = "Original file SHA-256 missing"
                continue
            preview = file.get("preview")
            if not isinstance(preview, dict):
                pending[target] = "Archived preview missing; no new rendering or source download attempted"
                continue
            try:
                url, preview_member = _location(preview, "previews", archive_manifest)
                if not preview_member.endswith(".png"):
                    raise ValueError("Appearance expects the archived PNG preview")
            except ValueError as exc:
                pending[target] = str(exc)
                continue
            supplied, original_member, svg_reason = False, None, None
            if file.get("format") == "svg":
                if type(file.get("size_bytes")) is int and 0 < file["size_bytes"] <= MAX_SVG_BYTES:
                    try:
                        original_url, original_member = _location(file, "files", archive_manifest)
                        supplied = original_url == url
                        if not supplied:
                            svg_reason = "Optional SVG is in a separate archive; cross-archive artwork buffering is not used"
                    except ValueError as exc:
                        svg_reason = "Optional SVG locator unavailable: " + str(exc)
                else:
                    svg_reason = "Original SVG exceeds 2 MiB or lacks a trustworthy size; structure remains unknown"
            evidence = source_style_evidence(record, file)
            evidence += [copy.deepcopy(item) for item in overrides if item.get("file_sha256") == file["sha256"]]
            revision = evidence_revision(evidence)
            key = cache_key(file["sha256"], preview["sha256"], style_evidence_revision=revision, svg_supplied=supplied)
            targets[target] = key
            if key not in jobs:
                jobs[key] = {"cache_key": key, "file_sha256": file["sha256"], "preview_sha256": preview["sha256"],
                    "archive_url": url, "preview_member": preview_member, "preview_size": preview.get("size_bytes"),
                    "original_member": original_member if supplied else None, "original_size": file.get("size_bytes"),
                    "svg_supplied": supplied, "svg_skip_reason": svg_reason, "style_evidence_revision": revision,
                    "evidence": evidence}
    return jobs, targets, pending


def reusable_cache(row, job, archive_manifest):
    if not isinstance(row, dict):
        return False
    if row.get("schema_version") != 1 or row.get("analysis_version") != ANALYSIS_VERSION:
        return False
    for key in ("cache_key", "file_sha256", "preview_sha256", "svg_supplied", "style_evidence_revision"):
        if row.get(key) != job.get(key):
            return False
    proof = row.get("archive_verification") or {}
    if not isinstance(proof, dict):
        return False
    remote = archive_manifest.get(proof.get("url")) or {}
    if (proof.get("verified") is not True or remote.get("digest") != "sha256:" + str(proof.get("sha256"))
            or remote.get("size") != proof.get("size_bytes")):
        return False
    appearance = row.get("appearance") or {}
    if not isinstance(appearance, dict):
        return False
    binding = appearance.get("binding") or {}
    color = appearance.get("color") or {}
    if not isinstance(binding, dict) or not isinstance(color, dict):
        return False
    return (appearance.get("cache_key") == job["cache_key"]
            and appearance.get("classification_version") == ANALYSIS_VERSION
            and appearance.get("style_evidence_revision") == job["style_evidence_revision"]
            and binding.get("file_sha256") == job["file_sha256"]
            and binding.get("preview_sha256") == job["preview_sha256"]
            and color.get("review_status") == "machine_measured_pending_visual_review"
            and isinstance(color.get("raw_statistics"), dict))


class DigestReader:
    def __init__(self, source, deadline, clock):
        self.source, self.deadline, self.clock = source, deadline, clock
        self.hasher, self.bytes_read = hashlib.sha256(), 0

    def read(self, size=-1):
        if self.clock() >= self.deadline:
            raise TimeoutError("Safe appearance checkpoint deadline reached")
        data = self.source.read(size)
        self.bytes_read += len(data)
        if self.bytes_read > MAX_ARCHIVE_BYTES:
            raise ValueError("Archive exceeds streaming byte limit")
        self.hasher.update(data)
        return data


def process_archive(url, jobs, remote, *, opener=None, deadline=float("inf"), clock=time.monotonic):
    """Read an immutable tar once; accept metadata only after full digest passes."""
    expected = {}
    for job in jobs:
        items = [(job["preview_member"], job["preview_sha256"], job["preview_size"], MAX_PREVIEW_BYTES)]
        if job["svg_supplied"]:
            items.append((job["original_member"], job["file_sha256"], job["original_size"], MAX_SVG_BYTES))
        for member, digest, size, limit in items:
            value = (digest, size, limit)
            if member in expected and expected[member] != value:
                raise ValueError("Conflicting member metadata within one archive")
            expected[member] = value
    opener = opener or (lambda value: urlopen(Request(value, headers={"User-Agent": "MGA-resource-appearance/1"}), timeout=60))
    rows, failures, data, reader = [], {}, {}, None
    report = {"archive_url": url, "status": "pending", "requested_jobs": len(jobs), "verified": False,
              "original_files_persisted": 0, "compressed_bytes_read": 0}
    try:
        if remote.get("size", 0) > MAX_ARCHIVE_BYTES or not str(remote.get("digest", "")).startswith("sha256:"):
            raise ValueError("Release archive digest or bounded size unavailable")
        with closing(opener(url)) as stream:
            reader = DigestReader(stream, deadline, clock)
            buffered, seen = 0, set()
            with tarfile.open(fileobj=reader, mode="r|gz") as archive:
                for member in archive:
                    if member.name not in expected:
                        continue
                    if member.name in seen:
                        raise ValueError("Duplicate requested archive member")
                    seen.add(member.name)
                    digest, size, limit = expected[member.name]
                    if not member.isfile() or not 0 < member.size <= limit or (size is not None and size != member.size):
                        failures[member.name] = "Member is not a regular file with the recorded bounded size"
                        continue
                    with archive.extractfile(member) as source:
                        payload = source.read(limit + 1)
                    if sha(payload) != digest:
                        failures[member.name] = "Member SHA-256 mismatch"
                        continue
                    buffered += len(payload)
                    if buffered > MAX_BUFFER_BYTES:
                        raise ValueError("Selected members exceed single-archive memory limit")
                    data[member.name] = payload
            while reader.read(1024 * 1024):
                pass
        report["compressed_bytes_read"] = reader.bytes_read
        if reader.bytes_read != remote["size"] or "sha256:" + reader.hasher.hexdigest() != remote["digest"]:
            raise ValueError("Complete archive SHA-256 or size mismatch; no appearance results accepted")
        report["verified"] = True
        proof = {"url": url, "sha256": reader.hasher.hexdigest(), "size_bytes": reader.bytes_read, "verified": True}
        for job in jobs:
            if clock() >= deadline:
                report["status"] = "partial_deadline"
                break
            key, preview_member = job["cache_key"], job["preview_member"]
            needed = [preview_member] + ([job["original_member"]] if job["svg_supplied"] else [])
            missing = next((member for member in needed if member not in data), None)
            if missing:
                failures[key] = failures.get(missing, "Requested member absent from archive")
                continue
            try:
                with Image.open(io.BytesIO(data[preview_member])) as image:
                    if image.format != "PNG":
                        raise ValueError("Archived preview is not a PNG")
                appearance = analyze_appearance(data[preview_member], file_sha256=job["file_sha256"],
                    preview_sha256=job["preview_sha256"], svg_bytes=data.get(job["original_member"]), evidence=job["evidence"])
                if appearance["color"]["review_status"] != "machine_measured_pending_visual_review":
                    raise ValueError("Archived preview could not be measured")
                if appearance["cache_key"] != key:
                    raise ValueError("Appearance analyzer cache key disagrees with planned inputs")
                if job["svg_skip_reason"]:
                    appearance["svg_coloring"]["uncertainty"].append(job["svg_skip_reason"])
                rows.append({"schema_version": 1, "analysis_version": ANALYSIS_VERSION,
                    **{field: job[field] for field in ("cache_key", "file_sha256", "preview_sha256", "svg_supplied", "style_evidence_revision")},
                    "archive_verification": proof, "appearance": appearance})
            except (OSError, ValueError) as exc:
                failures[key] = str(exc)
        if report["status"] != "partial_deadline":
            report["status"] = "complete" if len(rows) == len(jobs) else "partial_errors"
    except (OSError, ValueError, tarfile.TarError, EOFError, TimeoutError) as exc:
        rows = []
        report.update(status="pending_archive_error", error=str(exc))
        if reader:
            report["compressed_bytes_read"] = reader.bytes_read
        for job in jobs:
            failures[job["cache_key"]] = str(exc)
    report.update(completed_jobs=len(rows), pending_jobs=len(jobs) - len(rows), failures=failures)
    return rows, report


def run_backfill(records, archive_manifest, *, overrides=(), cached_rows=(), opener=None,
                 deadline=float("inf"), clock=time.monotonic, checkpoint=None, minimum_start_seconds=120):
    """Pure orchestration for synthetic tests and cloud callers; no artwork disk I/O."""
    jobs, targets, pending = plan_jobs(records, archive_manifest, overrides)
    cached, reports = {}, []
    for row in cached_rows:
        job = jobs.get(row.get("cache_key")) if isinstance(row, dict) else None
        if job and reusable_cache(row, job, archive_manifest):
            cached[job["cache_key"]] = row
    reused = len(cached)
    groups = defaultdict(list)
    for key, job in jobs.items():
        if key not in cached:
            groups[job["archive_url"]].append(job)
    for url, group in sorted(groups.items()):
        if clock() + minimum_start_seconds >= deadline:
            break
        rows, report = process_archive(url, group, archive_manifest[url], opener=opener, deadline=deadline, clock=clock)
        # process_archive returns accepted rows only after full archive digest.
        cached.update({row["cache_key"]: row for row in rows})
        reports.append(report)
        if checkpoint:
            checkpoint(rows, report)
    output = copy.deepcopy(records)
    completed = 0
    for record_index, record in enumerate(output):
        for file_index, file in enumerate(record.get("files", [])):
            target = (record_index, file_index)
            key = targets.get(target)
            if key in cached:
                file["appearance"] = copy.deepcopy(cached[key]["appearance"])
                file["appearance_status"] = {"status": "measured", "analysis_version": ANALYSIS_VERSION,
                                              "cache_key": key, "archive_integrity_verified": True}
                completed += 1
            else:
                reason = pending.get(target) or next((r["failures"][key] for r in reports if key in r["failures"]),
                                                     "Pending safe continuation; no accepted cache result")
                if valid_sha(file.get("sha256")):
                    file["appearance"] = analyze_appearance(None, file_sha256=file["sha256"])
                    file["appearance"]["color"]["uncertainty"].append(reason)
                else:
                    file.pop("appearance", None)
                file["appearance_status"] = {"status": "pending", "reason": reason, "analysis_version": ANALYSIS_VERSION}
    total = sum(len(record.get("files", [])) for record in output)
    summary = {"analysis_version": ANALYSIS_VERSION, "total_file_entries": total, "measured_file_entries": completed,
               "pending_file_entries": total - completed, "unique_planned_jobs": len(jobs), "cache_reused_jobs": reused,
               "newly_measured_jobs": len(cached) - reused, "archive_reports": reports,
               "archives_read": len(reports), "compressed_bytes_read": sum(row["compressed_bytes_read"] for row in reports),
               "original_files_persisted": 0, "source_websites_requested": 0,
               "color_modes": dict(Counter(file["appearance"]["color"]["mode"] for record in output
                                           for file in record.get("files", []) if file.get("appearance"))),
               "appearance_measurement_complete": completed == total, "visual_acceptance_passed": False,
               "style_rule_scope": "Health Icons exact filled/outline file paths; Arcadia matched gallery silhouette/tricolor series; manual file-bound overrides"}
    return output, [cached[key] for key in sorted(cached)], summary


def download_checked(filename, asset, incoming, repository, release_tag):
    if Path(filename).name != filename or "\\" in filename:
        raise ValueError("Metadata must use an exact Release asset filename")
    gh("release", "download", release_tag, "--repo", repository, "--pattern", filename, "--dir", str(incoming), "--clobber")
    path = incoming / filename
    raw = path.read_bytes()
    if len(raw) != asset.get("size") or "sha256:" + sha(raw) != asset.get("digest"):
        raise ValueError(f"Release metadata digest or size mismatch: {filename}")
    return path


def publish_catalog(records, output, parent_report, *, parent_assembly, appearance_summary, run_id,
                    release_context=None):
    summary = finalize(records, output)
    report = copy.deepcopy(parent_report)
    report.update(assembled_at=datetime.now(timezone.utc).isoformat(), run_id=run_id,
                  parent_assembly=parent_assembly, summary=summary, appearance=appearance_summary,
                  benchmark_queries=parent_report.get("benchmark_queries", 120), visually_judged_queries=0,
                  retrieval_pass_rate=None, acceptance_passed=False, collection_complete=False,
                  visual_acceptance="pending_after_appearance_rebuild", illustrator_verified=False,
                  website_gate="closed_pending_collection_and_acceptance")
    if release_context is not None:
        report["release_context"] = copy.deepcopy(release_context)
    if isinstance(report.get("public_scope"), dict):
        scope = report["public_scope"]
        scope["parent_public_catalog_sha256"] = scope.get("public_catalog_sha256")
        scope["public_catalog_sha256"] = summary["catalog_sha256"]
        scope["inherited_from_assembly"] = copy.deepcopy(parent_assembly)
        # Appearance adds file metadata only. The parent's internal diagnostic
        # catalog and scope decisions remain bound evidence, not new artifacts.
        write_json(output / "scope-report.json", scope)
    write_json(output / "report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    # These imports do not list or download assets; the cloud-only guard below
    # still runs before any network or filesystem acquisition.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from review_releases import add_release_options
    parser.add_argument("--release-tag", required=True, help="Release containing the input assembly metadata")
    add_release_options(parser)
    parser.add_argument("--assembly-prefix", required=True)
    parser.add_argument("--budget-minutes", type=int, default=50)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    if os.environ.get("GITHUB_ACTIONS") != "true":
        parser.error("Appearance backfill is cloud-only; local execution is limited to synthetic tests")
    if not re.fullmatch(r"assembly-[0-9]+-[0-9]+", args.assembly_prefix) or not 1 <= args.budget_minutes <= 52:
        parser.error("Use exact assembly-RUN-ATTEMPT and a 1–52 minute measurement budget")
    deadline = time.monotonic() + args.budget_minutes * 60
    sys.path.insert(0, str(ROOT / "scripts"))
    repository = os.environ["GITHUB_REPOSITORY"]
    run_id = os.environ["GITHUB_RUN_ID"] + "-" + os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    base = ROOT / "build" / "appearance" / run_id
    incoming, output = base / "metadata", base / "catalog"
    incoming.mkdir(parents=True, exist_ok=True)
    assets, manifest, releases, bindings = load_release_context(
        repository, args.release_tag, args.archive_release_tag, args.publish_tag)
    publish_tag = bindings["publish_tag"]
    paths = {}
    for suffix in ("catalog.jsonl", "catalog.sqlite", "report.json"):
        name = f"{args.assembly_prefix}.{suffix}"
        if name not in assets:
            raise ValueError(f"Exact assembly metadata missing: {name}")
        paths[suffix] = download_checked(name, assets[name], incoming, repository, args.release_tag)
    parent_report = json.loads(paths["report.json"].read_text(encoding="utf-8"))
    catalog_digest = sha(paths["catalog.jsonl"].read_bytes())
    if parent_report.get("summary", {}).get("catalog_sha256") != catalog_digest:
        raise ValueError("Parent report and catalog SHA-256 disagree")
    with closing(sqlite3.connect(paths["catalog.sqlite"].resolve().as_uri() + "?mode=ro", uri=True)) as db:
        has_versions = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='index_metadata'").fetchone()
        if has_versions:
            row = db.execute("SELECT value FROM index_metadata WHERE key='catalog_version'").fetchone()
            if not row or row[0] != catalog_digest:
                raise ValueError("Parent SQLite index and catalog disagree")
    records = read_jsonl(paths["catalog.jsonl"])
    overrides = load_overrides(ROOT / "data/appearance_overrides.json")
    jobs, _, _ = plan_jobs(records, manifest, overrides)
    cached, cache_assets_read, cache_asset_sources, cache_errors = {}, [], [], []
    verified_aggregates, skipped_checkpoints = set(), []
    # A newest aggregate cache often covers everything. Older checkpoint caches
    # are downloaded only while required keys remain unmatched.
    prior = release_cache_candidates(releases, verified_aggregates, skipped_checkpoints)
    for tag, name, asset, cache_run, is_aggregate in prior:
        if len(cached) == len(jobs) or time.monotonic() + 120 >= deadline:
            break
        try:
            cache_dir = incoming / ("cache-" + sha(tag.encode())[:16])
            cache_dir.mkdir(exist_ok=True)
            path = download_checked(name, asset, cache_dir, repository, tag)
            cache_input_rows = read_jsonl(path)
            for row in cache_input_rows:
                job = jobs.get(row.get("cache_key")) if isinstance(row, dict) else None
                if job and job["cache_key"] not in cached and reusable_cache(row, job, manifest):
                    cached[job["cache_key"]] = row
            cache_assets_read.append(name)
            cache_asset_sources.append({"release_tag": tag, "asset": name,
                                        "url": asset["browser_download_url"], "digest": asset["digest"]})
            if is_aggregate:
                verified_aggregates.add(cache_run)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            cache_errors.append({"release_tag": tag, "asset": name, "error": str(exc)})
    checkpoint_index = 0
    def checkpoint(rows, report):
        nonlocal checkpoint_index
        prefix = f"appearance-{run_id}-b{checkpoint_index:04d}"
        checkpoint_index += 1
        cache_path = base / f"{prefix}.appearance.jsonl"
        receipt = base / f"{prefix}.checkpoint.json"
        write_jsonl(cache_path, rows)
        write_json(receipt, {"schema_version": 1, "run_id": run_id, "parent_assembly": args.assembly_prefix,
                            "release_context": bindings,
                            "analysis_version": ANALYSIS_VERSION, "archive": report,
                            "cache_file": cache_path.name, "cache_sha256": sha(cache_path.read_bytes()),
                            "completed_jobs": len(rows), "acceptance_passed": False})
        if args.publish:
            gh("release", "upload", publish_tag, "--repo", repository, str(cache_path), str(receipt))
        print(json.dumps({"checkpoint": prefix, "completed": len(rows), "pending": report["pending_jobs"],
                          "status": report["status"]}), flush=True)
    enriched, cache_rows, summary = run_backfill(records, manifest, overrides=overrides,
        cached_rows=cached.values(), deadline=deadline, checkpoint=checkpoint)
    summary.update(cache_assets_read=cache_assets_read, cache_asset_sources=cache_asset_sources,
                   rejected_cache_assets=cache_errors,
                   cache_checkpoints_covered_by_verified_aggregate=skipped_checkpoints,
                   safe_budget_minutes=args.budget_minutes, original_catalog_sha256=catalog_digest)
    parent = {"prefix": args.assembly_prefix, "catalog_sha256": catalog_digest,
              "report_sha256": sha(paths["report.json"].read_bytes()), "release_tag": args.release_tag}
    report = publish_catalog(enriched, output, parent_report, parent_assembly=parent,
                             appearance_summary=summary, run_id=run_id, release_context=bindings)
    aggregate = base / f"appearance-{run_id}.appearance.jsonl"
    write_jsonl(aggregate, cache_rows)
    uploads = [aggregate]
    for path in output.iterdir():
        target = base / f"assembly-{run_id}.{path.name}"
        path.replace(target)
        uploads.append(target)
    if args.publish:
        gh("release", "upload", publish_tag, "--repo", repository, *map(str, uploads))
    print(json.dumps({"assembly_prefix": f"assembly-{run_id}", "appearance": summary,
                      "acceptance_passed": report["acceptance_passed"]}, ensure_ascii=False), flush=True)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
            stream.write(f"## Appearance backfill\n\nOutput: assembly-{run_id}.\n\n"
                         f"Metadata input: {args.release_tag}. Output Release: {publish_tag}.\n\n"
                         f"Measured file entries: {summary['measured_file_entries']}; pending: {summary['pending_file_entries']}. "
                         f"Reused jobs: {summary['cache_reused_jobs']}; newly measured: {summary['newly_measured_jobs']}.\n\n"
                         "Archive checkpoints and immutable caches support continuation. Visual acceptance remains pending.\n")


if __name__ == "__main__":
    main()
