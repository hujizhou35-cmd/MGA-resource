"""Collect artwork on a GitHub-hosted runner and publish durable cloud packages.

This command deliberately refuses artwork acquisition on a local machine.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import random
import shutil
import subprocess
import sys
import tarfile
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def dump_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def dump_jsonl(path: Path, records) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as out:
        wrote_record = False
        for record in records:
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            wrote_record = True
        if not wrote_record:
            # GitHub Releases reject zero-byte uploads. A blank JSONL line is
            # an empty dataset, not a fabricated record.
            out.write("\n")


def slug(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip(".-")
    if not value:
        raise ValueError("Empty identifier")
    return value[:120]


def select_source_rows(discovered, source, *, offset=0, limit=0, plan=None, discovery_sha256=None):
    """Bind an incremental selection to the complete ordered source inventory."""
    if offset < 0 or limit < 0:
        raise ValueError("Selection offset and limit must be non-negative")
    if plan is None:
        indices = list(range(len(discovered)))
    else:
        if plan.get("expected_discovery_sha256") and plan["expected_discovery_sha256"] != discovery_sha256:
            raise ValueError("Selection plan full metadata snapshot changed; retain the previous cloud snapshot")
        if plan.get("source") != source or plan.get("inventory_count") != len(discovered):
            raise ValueError("Selection plan source or inventory size changed")
        ids = [row.get("source_id") for row in discovered]
        if any(not isinstance(sid, str) or not sid for sid in ids) or len(set(ids)) != len(ids):
            raise ValueError("Source inventory IDs must be unique non-empty strings")
        digest = hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()
        if digest != plan.get("ordered_source_ids_sha256"):
            raise ValueError("Selection plan inventory order/identity changed; rebuild the plan")
        pinned = plan.get("pinned_commit")
        if pinned and any(row.get("upstream_revision") != pinned for row in discovered):
            raise ValueError("Selection plan upstream revision changed")
        ranges = plan.get("recommended_now", {}).get("ranges", plan.get("ranges"))
        if not isinstance(ranges, list) or not ranges:
            raise ValueError("Selection plan has no explicit ranges")
        indices = []
        previous_end = 0
        for item in ranges:
            start, count = item.get("offset"), item.get("limit")
            if (type(start) is not int or type(count) is not int or count <= 0
                    or start < previous_end or start + count > len(discovered)):
                raise ValueError("Selection ranges must be sorted, disjoint and inside inventory")
            indices.extend(range(start, start + count))
            previous_end = start + count
    indices = [index for index in indices if index >= offset]
    if limit:
        indices = indices[:limit]
    return [(index, discovered[index]) for index in indices]


def run_gh(arguments: list[str], *, check=True):
    return subprocess.run(["gh", *arguments], text=True, capture_output=True, check=check)


def ensure_release(tag: str) -> None:
    if run_gh(["release", "view", tag], check=False).returncode == 0:
        return
    notes = (
        "Cloud collection checkpoint. Collection and acceptance are in progress.\n\n"
        "Original artwork keeps its recorded source license. File structure and rendering checks "
        "are not Illustrator acceptance or scientific validation. Each batch includes metadata, "
        "attribution, SHA-256 and a failure report. No website has been deployed at this stage."
    )
    result = run_gh(["release", "create", tag, "--title", f"Cloud collection: {tag}",
                     "--notes", notes, "--target", os.environ["GITHUB_SHA"], "--prerelease"], check=False)
    if result.returncode and run_gh(["release", "view", tag], check=False).returncode:
        raise RuntimeError(f"Could not create cloud checkpoint: {result.stderr}")


def upload(tag: str, files: list[Path], *, retry_transient: bool = True) -> None:
    if not files:
        return
    repo = os.environ.get("GITHUB_REPOSITORY")
    existing = {}
    if repo:
        release = run_gh(["api", f"repos/{repo}/releases/tags/{tag}"], check=False)
        if release.returncode:
            raise RuntimeError(f"Cannot verify existing release assets: {release.stderr}")
        release_id = json.loads(release.stdout)["id"]
        asset_pages = run_gh(["api", "--paginate", "--slurp",
                             f"repos/{repo}/releases/{release_id}/assets?per_page=100"], check=False)
        if asset_pages.returncode:
            raise RuntimeError(f"Cannot list existing release assets: {asset_pages.stderr}")
        existing = {item["name"]: item for page in json.loads(asset_pages.stdout) for item in page}
    pending = []
    for path in files:
        remote = existing.get(path.name)
        if remote is None:
            pending.append(path)
            continue
        with path.open("rb") as stream:
            expected = "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()
        if remote.get("digest") != expected or remote.get("size") != path.stat().st_size:
            raise RuntimeError(f"Existing immutable cloud asset differs: {path.name}")
    if pending:
        result = run_gh(["release", "upload", tag, *map(str, pending)], check=False)
        if result.returncode:
            if retry_transient and re.search(r"timeout|timed out|connection reset|unexpected EOF|HTTP 50[0234]", result.stderr, re.I):
                time.sleep(3)
                return upload(tag, files, retry_transient=False)
            raise RuntimeError(f"Cloud checkpoint upload failed: {result.stderr}")


def public_paths(value, base: Path):
    """Remove temporary runner paths; keep archive-relative paths only."""
    if isinstance(value, Path):
        value = str(value)
    if isinstance(value, dict):
        return {key: public_paths(item, base) for key, item in value.items()
                if key not in {"local_path", "absolute_path"}}
    if isinstance(value, list):
        return [public_paths(item, base) for item in value]
    if isinstance(value, str):
        prefix = str(base.resolve())
        if value.startswith(prefix + os.sep):
            return Path(value).relative_to(base).as_posix()
    return value


def write_batch(base: Path, prefix: str, records: list, failures: list,
                assets: set[Path], publication: dict, publish: bool) -> dict:
    index = base / f"{prefix}.records.jsonl"
    failure_file = base / f"{prefix}.failures.json"
    archive = base / f"{prefix}.tar.gz"
    archive_url = f"https://github.com/{publication.get('repository')}/releases/download/{publication['release_tag']}/{archive.name}"
    for record in records:
        record["collection_run"] = publication.get("run_id")
        for item in record.get("files", []):
            item["archive"] = {"url": archive_url, "member": item.get("storage_path")}
            preview = item.get("preview")
            if isinstance(preview, dict) and preview.get("path"):
                preview["archive"] = {"url": archive_url, "member": preview["path"]}
    cleaned = [public_paths(record, base) for record in records]
    dump_jsonl(index, cleaned)
    dump_json(failure_file, failures)
    with index.open("rb") as stream:
        records_sha = hashlib.file_digest(stream, "sha256").hexdigest()
    with failure_file.open("rb") as stream:
        failures_sha = hashlib.file_digest(stream, "sha256").hexdigest()
    review_image, review_records = make_review_sheet(base, prefix, cleaned)
    with tarfile.open(archive, "w:gz", compresslevel=3) as tar:
        tar.add(index, arcname="records.jsonl")
        tar.add(failure_file, arcname="failures.json")
        for path in sorted(assets):
            if path.is_file():
                tar.add(path, arcname=path.relative_to(base).as_posix())
    with archive.open("rb") as stream:
        sha = hashlib.file_digest(stream, "sha256").hexdigest()
    receipt = {
        **publication, "records": len(records), "failures": len(failures),
        "finished_at": datetime.now(timezone.utc).isoformat(), "status": "checkpoint",
        "files": len(assets), "archive": archive.name,
        "archive_bytes": archive.stat().st_size, "sha256": sha,
        "records_file": index.name, "failures_file": failure_file.name,
        "records_sha256": records_sha, "failures_sha256": failures_sha,
        "review_sheet": review_image.name if review_image else None, "review_tiles": review_records,
        "license_notice": "Per-record artwork licenses and credits apply; not project MIT.",
    }
    receipt_file = base / f"{prefix}.receipt.json"
    dump_json(receipt_file, receipt)
    if publish:
        # Publish the receipt last: its presence is proof the batch files exist.
        upload(publication["release_tag"], [archive, index, failure_file,
                                          *([review_image] if review_image else [])])
        upload(publication["release_tag"], [receipt_file])
        for path in assets:
            if path.is_file():
                path.unlink()
        archive.unlink()
    return receipt


def make_review_sheet(base: Path, prefix: str, records: list):
    """A bounded cloud-generated contact sheet, not an automatic visual verdict."""
    from PIL import Image, ImageDraw, ImageOps
    candidates = []
    for record in records:
        for item in record.get("files", []):
            preview = item.get("preview") or {}
            preview_path = preview.get("path") if isinstance(preview, dict) else preview
            if preview_path:
                path = base / preview_path
                if path.is_file():
                    candidates.append((record, path))
                    break
    if not candidates:
        return None, []
    sample = candidates if len(candidates) <= 30 else random.Random(20261106).sample(candidates, 30)
    cols, tile_width, tile_height = 5, 300, 260
    rows = (len(sample) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tile_width, rows * tile_height), "#e4e8ed")
    draw = ImageDraw.Draw(sheet)
    entries = []
    for number, (record, path) in enumerate(sample, 1):
        x, y = (number - 1) % cols * tile_width, (number - 1) // cols * tile_height
        draw.rectangle((x + 4, y + 4, x + tile_width - 4, y + tile_height - 4), fill="white")
        with Image.open(path) as preview:
            thumb = ImageOps.contain(preview.convert("RGBA"), (tile_width - 20, tile_height - 62))
            sheet.paste(thumb, (x + (tile_width - thumb.width) // 2, y + 8), thumb)
        title = str(record.get("title", record.get("id", ""))).encode("ascii", "replace").decode()[:43]
        draw.text((x + 10, y + tile_height - 48), f"{number:02d}. {title}", fill="#172231")
        draw.text((x + 10, y + tile_height - 28), str(record.get("id", ""))[:43], fill="#344b60")
        entries.append({"tile": number, "id": record.get("id"), "title": record.get("title")})
    destination = base / f"{prefix}.review.png"
    sheet.save(destination, optimize=True)
    return destination, entries


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--selection-plan", default="", help="Verified incremental plan under data/collection-plans")
    parser.add_argument("--release-tag", default="collection-20261106")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    if os.environ.get("GITHUB_ACTIONS") != "true":
        parser.error("Bulk collection is cloud-only. Run the GitHub Actions 'Collect source' workflow.")
    if args.limit < 0 or args.offset < 0:
        parser.error("limit and offset must be non-negative")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,100}", args.release_tag):
        parser.error("Invalid release tag")
    selection_plan = None
    selection_path = None
    if args.selection_plan:
        selection_path = (ROOT / args.selection_plan).resolve()
        if not selection_path.is_relative_to((ROOT / "data/collection-plans").resolve()):
            parser.error("Selection plan must be under data/collection-plans")
        selection_plan = json.loads(selection_path.read_text(encoding="utf-8"))

    from mga_resources.http import HttpClient
    from mga_resources.sources import discover
    from mga_resources.media import process_file
    from mga_resources.catalog import normalize_record, finalize

    registry = json.loads((ROOT / "data/sources.json").read_text(encoding="utf-8"))
    sources = registry["sources"] if isinstance(registry, dict) else registry
    source = next((item for item in sources if item["source_id"] == args.source), None)
    if source is None or not source.get("enabled", False):
        parser.error("Source is missing or not enabled in the source registry")
    run_id = slug(os.environ.get("GITHUB_RUN_ID", "unknown") + "-" + os.environ.get("GITHUB_RUN_ATTEMPT", "1"))
    base = ROOT / "build" / slug(args.source) / run_id
    base.mkdir(parents=True, exist_ok=True)
    publication = {
        "source": args.source, "run_id": run_id, "release_tag": args.release_tag,
        "offset": args.offset, "limit": args.limit,
        "repository": os.environ.get("GITHUB_REPOSITORY"),
        "tooling_commit": os.environ.get("GITHUB_SHA"),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "run_url": f"https://github.com/{os.environ.get('GITHUB_REPOSITORY')}/actions/runs/{os.environ.get('GITHUB_RUN_ID')}",
    }
    if selection_path:
        publication["selection_plan"] = {
            "path": selection_path.relative_to(ROOT).as_posix(),
            "sha256": hashlib.sha256(selection_path.read_bytes()).hexdigest(),
            "ordered_source_ids_sha256": selection_plan.get("ordered_source_ids_sha256"),
        }
    if args.publish:
        ensure_release(args.release_tag)
    client = HttpClient()
    all_records, all_failures, held_records, excluded_records, receipts = [], [], [], [], []
    batch_records, batch_failures, batch_assets, batch_dispositions = [], [], set(), []
    batch_input_bytes, batch_number, processed, discovered_count = 0, 0, 0, 0
    acquisition_started = time.monotonic()
    flush_failed = False
    fatal = None
    source_complete = False
    selected_count = 0

    def flush():
        nonlocal batch_records, batch_failures, batch_assets, batch_input_bytes, batch_number
        nonlocal batch_dispositions, flush_failed
        if not batch_dispositions:
            return
        prefix = f"{slug(args.source)}-{run_id}-b{batch_number:04d}"
        batch_start = batch_dispositions[0]["index"]
        batch_end = batch_dispositions[-1]["index"] + 1
        if [entry["index"] for entry in batch_dispositions] != list(range(batch_start, batch_end)):
            raise ValueError("A batch must cover a contiguous source range")
        checkpoint = {**publication, "range_start": batch_start, "range_end": batch_end,
                      "discovered": discovered_count, "dispositions": batch_dispositions,
                      "held_records": [x for x in held_records if batch_start <= x["source_index"] < batch_end],
                      "excluded_records": [x for x in excluded_records if batch_start <= x["source_index"] < batch_end],
                      "held_count": sum(x["status"] == "held" for x in batch_dispositions),
                      "excluded_count": sum(x["status"] == "excluded" for x in batch_dispositions)}
        try:
            receipts.append(write_batch(base, prefix, batch_records, batch_failures,
                                        batch_assets, checkpoint, args.publish))
        except Exception:
            flush_failed = True
            raise
        batch_records, batch_failures, batch_assets, batch_dispositions = [], [], set(), []
        batch_input_bytes = 0
        batch_number += 1

    try:
        discovered = list(discover(source, client))
        discovered_count = len(discovered)
        if not discovered:
            raise RuntimeError("Source discovery returned no artwork; cannot verify a complete inventory")
        discovery_file = base / f"{slug(args.source)}-{run_id}.discovery.jsonl"
        dump_jsonl(discovery_file, discovered)
        with discovery_file.open("rb") as stream:
            discovered_sha = hashlib.file_digest(stream, "sha256").hexdigest()
        # Reject source drift before publishing any snapshot identity. A failed
        # new sparse run must not supersede the last verified complete inventory.
        selected = select_source_rows(discovered, args.source, offset=args.offset, limit=args.limit,
                                      plan=selection_plan, discovery_sha256=discovered_sha)
        publication["discovery_sha256"] = discovered_sha
        if args.publish:
            upload(args.release_tag, [discovery_file])
        selected_count = len(selected)
        print(json.dumps({"source": args.source, "discovered": discovered_count,
                          "selected": selected_count, "offset": args.offset}), flush=True)
        for item_index, item in selected:
            if batch_dispositions and item_index != batch_dispositions[-1]["index"] + 1:
                flush()
            # A checkpoint can only be taken between complete logical records.
            if time.monotonic() - acquisition_started > 60 * 60:
                flush()
                raise RuntimeError(f"Checkpointed before source offset {item_index}; continue with the same selection plan and this offset")
            if shutil.disk_usage(base).free < 2 * 1024**3:
                flush()
                if shutil.disk_usage(base).free < 2 * 1024**3:
                    raise RuntimeError("Cloud runner has less than 2 GiB free disk; checkpointed before stopping")
            processed += 1
            disposition = {"index": item_index, "source_id": item.get("source_id"), "status": "attempting"}
            batch_dispositions.append(disposition)
            if item.get("scope_status") == "excluded" and item.get("exclusion_reason"):
                excluded_records.append({"source_id": item.get("source_id"), "source_index": item_index,
                                         "title": item.get("title"), "source_url": item.get("source_url"),
                                         "reason": item["exclusion_reason"]})
                disposition.update(status="excluded", reason=item["exclusion_reason"])
                if len(batch_dispositions) >= 500:
                    flush()
                continue
            allowed_licenses = {"CC0-1.0", "CC-BY-3.0", "CC-BY-4.0", "CC-BY-SA-3.0",
                                "CC-BY-SA-4.0", "MIT", "BSD-2-Clause", "BSD-3-Clause",
                                "Public Domain", "public domain", "PD"}
            if item.get("distribution_allowed") is False or item.get("license") not in allowed_licenses:
                held_records.append({"source_id": item.get("source_id"), "source_index": item_index, "title": item.get("title"),
                                     "source_url": item.get("source_url"), "license": item.get("license"),
                                     "reason": item.get("rights_note") or "Item redistribution license needs review"})
                disposition.update(status="held", reason=held_records[-1]["reason"])
                if len(batch_dispositions) >= 500:
                    flush()
                continue
            files = []
            item_assets = set()
            item_failures = 0
            for descriptor, content, download_error in client.iter_files(item.get("files", []), workers=4):
                url = descriptor.get("url")
                try:
                    if download_error is not None:
                        raise download_error
                    digest = hashlib.sha256(content).hexdigest()
                    suffix = re.sub(r"[^a-z0-9]", "", str(descriptor.get("format", "bin")).lower()) or "bin"
                    relative = Path("files") / digest[:2] / f"{digest}.{suffix}"
                    path = base / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(content)
                    batch_input_bytes += len(content)
                    del content
                    inspected = process_file(path, preview_dir=base / "previews")
                    file_record = {**descriptor, **inspected, "storage_path": relative.as_posix()}
                    file_record = public_paths(file_record, base)
                    item_assets.add(path)
                    preview = inspected.get("preview")
                    if isinstance(preview, dict):
                        for key in ("path", "file", "local_path"):
                            candidate = preview.get(key)
                            if candidate:
                                candidate_path = Path(candidate)
                                if not candidate_path.is_absolute():
                                    candidate_path = base / candidate_path
                                    if not candidate_path.is_file():
                                        candidate_path = base / "previews" / Path(candidate).name
                                if candidate_path.is_file() and candidate_path.is_relative_to(base):
                                    item_assets.add(candidate_path)
                                    file_record["preview"]["path"] = candidate_path.relative_to(base).as_posix()
                    elif isinstance(preview, str):
                        candidate_path = Path(preview)
                        if candidate_path.is_file() and candidate_path.is_relative_to(base):
                            item_assets.add(candidate_path)
                    files.append(file_record)
                except Exception as exc:
                    item_failures += 1
                    failure = {"source": args.source, "source_id": item.get("source_id"), "source_index": item_index,
                               "url": url, "error_type": type(exc).__name__, "error": str(exc)[:1200]}
                    batch_failures.append(failure)
                    all_failures.append(failure)
                    print(json.dumps({"failure": failure}, ensure_ascii=False), flush=True)
                    # Access denials stop acquisition for the source, with a durable checkpoint.
                    if getattr(exc, "status", None) in {401, 403, 451} or getattr(exc, "status_code", None) in {401, 403, 451}:
                        disposition.update(status="failed", reason=str(exc)[:1200])
                        raise
            if files:
                record = normalize_record(item, processed_files=files)
                record["source_index"] = item_index
                record = public_paths(record, base)
                all_records.append(record)
                batch_records.append(record)
                batch_assets.update(item_assets)
            elif not item_failures:
                failure = {"source": args.source, "source_id": item.get("source_id"), "source_index": item_index,
                           "error_type": "MissingArtwork", "error": "No original file was acquired for this item"}
                batch_failures.append(failure)
                all_failures.append(failure)
                item_failures += 1
            machine_ok = bool(files) and all(f.get("validation", {}).get("status") in
                                           {"machine_checked", "machine_checked_and_rendered"} for f in files)
            disposition.update(status="acquired" if files and not item_failures else "failed",
                               files=len(files), file_failures=item_failures, machine_checked=machine_ok)
            if processed % 25 == 0:
                print(json.dumps({"processed": processed, "selected": selected_count,
                                  "records": len(all_records), "failures": len(all_failures)}), flush=True)
            if len(batch_dispositions) >= 500 or batch_input_bytes >= 512 * 1024**2:
                flush()
        source_complete = processed == discovered_count and len(selected) == discovered_count
    except Exception as exc:
        fatal = {"error_type": type(exc).__name__, "error": str(exc)[:2400]}
        traceback.print_exc()
    finally:
        for disposition in batch_dispositions:
            if disposition["status"] == "attempting":
                disposition.update(status="failed", reason=(fatal or {}).get("error", "Item processing interrupted"))
                failure = {"source": args.source, "source_id": disposition["source_id"],
                           "source_index": disposition["index"], "error_type": "InterruptedItem",
                           "error": disposition["reason"]}
                batch_failures.append(failure)
                all_failures.append(failure)
        if not flush_failed:
            flush()
        catalog_dir = base / "catalog"
        catalog_summary = finalize(all_records, catalog_dir)
        renamed = []
        for path in catalog_dir.iterdir():
            if path.is_file():
                destination = base / f"{slug(args.source)}-{run_id}.{path.name}"
                path.replace(destination)
                renamed.append(destination)
        report = {
            **publication, "finished_at": datetime.now(timezone.utc).isoformat(), "status": "final",
            "discovered": discovered_count, "selected": selected_count, "processed": processed,
            "downloaded_records": len(all_records), "failures": len(all_failures),
            "held_records": held_records, "held_count": len(held_records),
            "excluded_records": excluded_records, "excluded_count": len(excluded_records),
            "source_inventory_processed": source_complete,
            "acquisition_complete": source_complete and not all_failures and not held_records and fatal is None,
            "machine_acceptance_passed": source_complete and not all_failures and not held_records and fatal is None
              and all(d.get("machine_checked") or d.get("status") == "excluded"
                      for receipt in receipts for d in receipt.get("dispositions", [])),
            "acceptance_passed": False, "collection_complete": False,
            "illustrator_verified": False, "visual_acceptance": "pending",
            "fatal": fatal, "batches": receipts, "catalog_summary": catalog_summary,
        }
        report_file = base / f"{slug(args.source)}-{run_id}.report.json"
        dump_json(report_file, report)
        if args.publish:
            upload(args.release_tag, [report_file, *renamed])
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            with open(summary_path, "a", encoding="utf-8") as output:
                output.write(f"## {args.source}: cloud collection\n\n")
                output.write(f"Discovered: {discovered_count}; processed: {processed}; downloaded records: {len(all_records)}; failures: {len(all_failures)}.\n\n")
                output.write(f"Full source processed: {source_complete}. Visual acceptance pending; no Illustrator claim.\n\n")
                output.write(f"Cloud packages: https://github.com/{publication['repository']}/releases/tag/{args.release_tag}\n")
        print(json.dumps(report, ensure_ascii=False), flush=True)
    return 1 if fatal else 0


if __name__ == "__main__":
    raise SystemExit(main())
