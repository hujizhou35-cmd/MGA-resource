"""Cloud-only Togo raster/mixed-content scope contact sheets; no scope verdicts."""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import sys
import tarfile
import time
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from backfill_appearance import DigestReader, download_checked, gh, sha

PER_SHEET = 35
MAX_FILES = 1000
MAX_SHEETS = 29
MAX_PACK_BYTES = 32 * 1024 * 1024
MAX_PREVIEW_BYTES = 8 * 1024 * 1024


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def describe_review_files(record):
    """Format-independent descriptors with exact original/preview bindings."""
    selected = {}
    for file in record.get("files", []):
        preview = file.get("preview") or {}
        digest, preview_sha = file.get("sha256"), preview.get("sha256")
        if not all(isinstance(v, str) and re.fullmatch(r"[a-f0-9]{64}", v) for v in (digest, preview_sha)):
            raise ValueError("Scope candidates need exact original and preview SHA-256")
        archive = preview.get("archive") or {}
        member = archive.get("member", "")
        if (not member.startswith("previews/") or "\\" in member or ":" in member
                or any(p in {"", ".", ".."} for p in member.split("/"))):
            raise ValueError("Unsafe preview member")
        key = sha((record["source"] + "\0" + str(record["source_id"]) + "\0" + digest + "\0" + preview_sha).encode())
        selected[key] = {"candidate_id": key, "source": record["source"], "source_id": str(record["source_id"]),
            "asset_id": record["id"], "title": record.get("title"), "source_url": record.get("source_url"),
            "source_declared_scientific_name": record.get("scientific_name"), "tags": record.get("tags", []),
            "original_sha256": digest, "original_archive": file.get("archive"), "format": file.get("format"),
            "structure": file.get("structure"), "preview": preview,
            "selection_reason": "explicit_review_candidate", "scope_verdict": None}
    return selected


def scope_candidates(records):
    """Select uncertain content groups, not all PNGs and not alleged photos."""
    selected = {}
    for record in records:
        files = record.get("files", [])
        if not files:
            continue
        only_raster = all(f.get("format") != "svg" for f in files)
        packaged_raster = any(f.get("structure") in {"mixed", "embedded_raster"} for f in files)
        if not only_raster and not packaged_raster:
            continue
        # Every sibling keeps its own exact file/preview descriptor.
        for key, row in describe_review_files(record).items():
            row["selection_reason"] = "only_raster_acquired" if only_raster else "raster_embedded_or_mixed_record_and_sibling_formats"
            selected[key] = row
    if len(selected) > MAX_FILES:
        raise ValueError("Scope review exceeds the 1000-file bound; split the input collection ranges")
    return selected


def fetch_previews(candidates, manifest, *, opener=None):
    """Full preview pixels for exact dedup; retain only small thumbnails after hashing."""
    groups = defaultdict(list)
    for key, row in candidates.items():
        groups[row["preview"]["archive"]["url"]].append((key, row))
    opener = opener or (lambda url: urlopen(Request(url, headers={"User-Agent": "MGA-scope-review/1"}), timeout=60))
    images, pixel_hashes, reports, errors = {}, {}, [], {}
    for url, rows in sorted(groups.items()):
        remote = manifest.get(url, {})
        wanted = {}
        for _, row in rows:
            preview = row["preview"]
            member = preview["archive"]["member"]
            if member in wanted and wanted[member]["sha256"] != preview["sha256"]:
                raise ValueError("Conflicting member SHA")
            wanted[member] = preview
        pending, seen, reader = {}, set(), None
        report = {"url": url, "verified": False, "selected_members": len(wanted)}
        try:
            if not re.fullmatch(r"sha256:[a-f0-9]{64}", remote.get("digest", "")):
                raise ValueError("Verified Release archive digest required")
            with closing(opener(url)) as stream:
                reader = DigestReader(stream, float("inf"), time.monotonic)
                with tarfile.open(fileobj=reader, mode="r|gz") as archive:
                    for member in archive:
                        if member.name not in wanted:
                            continue
                        if member.name in seen:
                            raise ValueError("Duplicate selected member")
                        seen.add(member.name)
                        expected = wanted[member.name]
                        if not member.isfile() or not 0 < member.size <= MAX_PREVIEW_BYTES:
                            raise ValueError("Selected preview is not a bounded regular file")
                        with archive.extractfile(member) as source:
                            raw = source.read(MAX_PREVIEW_BYTES + 1)
                        if sha(raw) != expected["sha256"] or len(raw) != expected.get("size_bytes", len(raw)):
                            raise ValueError("Preview member digest or size mismatch")
                        with Image.open(io.BytesIO(raw)) as im:
                            if im.format != "PNG" or im.width * im.height > 1024 * 1024:
                                raise ValueError("Unexpected scope preview format or dimensions")
                            rgba = im.convert("RGBA")
                            pixels = sha(f"rgba-v1:{rgba.width}:{rgba.height}:".encode() + rgba.tobytes())
                            thumb = ImageOps.contain(rgba, (280, 198))
                        pending[member.name] = (thumb, pixels)
                while reader.read(1024 * 1024):
                    pass
            if reader.bytes_read != remote.get("size") or "sha256:" + reader.hasher.hexdigest() != remote["digest"]:
                raise ValueError("Complete archive digest or size mismatch")
            report.update(verified=True, sha256=reader.hasher.hexdigest(), size_bytes=reader.bytes_read)
            for key, row in rows:
                member = row["preview"]["archive"]["member"]
                if member in pending:
                    images[key], pixel_hashes[key] = pending[member]
                else:
                    errors[key] = "Selected member missing"
        except (OSError, ValueError, EOFError, tarfile.TarError) as exc:
            report["error"] = str(exc)
            for key, _ in rows:
                errors[key] = str(exc)
        report["compressed_bytes_read"] = reader.bytes_read if reader else 0
        reports.append(report)
    return images, pixel_hashes, reports, errors


def group_exact_content(candidates, pixel_hashes):
    """Only the same record AND identical full RGBA can share a reviewed tile."""
    groups = defaultdict(list)
    for key, row in candidates.items():
        identity = (row["source"], row["source_id"], pixel_hashes.get(key) or key)
        groups[identity].append(key)
    tiles = []
    for identity, keys in sorted(groups.items(), key=lambda p: (p[0][0], p[0][1], p[0][2])):
        tiles.append({"tile_id": len(tiles) + 1, "file_ids": sorted(keys),
                      "full_preview_rgba_sha256": pixel_hashes.get(keys[0]),
                      "shared_tile_basis": "same_source_record_and_exact_full_preview_rgba" if len(keys) > 1 else "single_file",
                      "scope_verdict": None})
    return tiles


def render_sheet(path, tiles, candidates, images, *, font_path, heading=None):
    width, height = 1500, 55 + max(1, math.ceil(len(tiles) / 5)) * 260
    sheet = Image.new("RGB", (width, height), "#e4e8ed")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.truetype(font_path, 14)
    draw.text((12, 12), heading or "Togo scope candidates / photography or illustration must be visually judged", font=font, fill="black")
    for index, tile in enumerate(tiles):
        key = tile["file_ids"][0]
        row = candidates[key]
        x, y = index % 5 * 300, 55 + index // 5 * 260
        draw.rectangle((x + 4, y + 4, x + 295, y + 255), fill="white")
        image = images.get(key)
        if image:
            sheet.paste(image, (x + (300 - image.width) // 2, y + 8), image)
        title = f"{tile['tile_id']:03d}. {row.get('title') or row['source_id']}"
        labels = [title, f"id {row['source_id']} | " + '/'.join(sorted({candidates[k]['format'] for k in tile['file_ids']})),
                  f"{row['original_sha256'][:14]} | files {len(tile['file_ids'])}"]
        for n, label in enumerate(labels):
            while draw.textlength(label, font=font) > 280:
                label = label[:-2] + "…"
            draw.text((x + 10, y + 207 + 16 * n), label, font=font, fill="black")
    sheet.save(path, optimize=True)
    return {"filename": path.name, "sha256": sha(path.read_bytes()), "size_bytes": path.stat().st_size,
            "width": width, "height": height, "tile_ids": [t["tile_id"] for t in tiles]}


def build_review(records, manifest, output, *, font_path, opener=None, selected_candidates=None, heading=None):
    candidates = scope_candidates(records) if selected_candidates is None else selected_candidates
    if len(candidates) > MAX_FILES:
        raise ValueError("Scope review exceeds the 1000-file bound")
    images, pixels, reports, errors = fetch_previews(candidates, manifest, opener=opener)
    tiles = group_exact_content(candidates, pixels)
    if math.ceil(len(tiles) / PER_SHEET) > MAX_SHEETS:
        raise ValueError("Scope contact-sheet count exceeds limit")
    output.mkdir(parents=True, exist_ok=True)
    sheets = []
    for offset in range(0, len(tiles), PER_SHEET):
        name = f"scope-{offset // PER_SHEET + 1:02d}.png"
        sheet = render_sheet(output / name, tiles[offset:offset + PER_SHEET], candidates, images, font_path=font_path, heading=heading)
        sheets.append(sheet)
        for tile in tiles[offset:offset + PER_SHEET]:
            tile["sheet"] = name
    mapping = {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
               "files": candidates, "tiles": tiles, "sheets": sheets, "archive_verification": reports,
               "preview_errors": errors, "file_count": len(candidates), "tile_count": len(tiles),
               "scope_acceptance_passed": False, "original_files_downloaded": 0,
               "source_websites_requested": 0, "grouping_uses_full_preview_pixels_before_thumbnail": True}
    write_json(output / "mapping.json", mapping)
    write_json(output / "judgments.template.json", {"schema_version": 1, "reviewer": None,
        "items": [{"tile_id": t["tile_id"], "file_ids": t["file_ids"], "scope_verdict": None, "observations": None} for t in tiles]})
    if sum(p.stat().st_size for p in output.iterdir() if p.is_file()) > MAX_PACK_BYTES:
        raise ValueError("Scope pack contents exceed 32 MiB")
    return mapping


def main():
    from review_releases import add_release_options, load_review_inputs, release_bindings
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-tag", required=True)
    add_release_options(parser)
    parser.add_argument("--source-prefix", required=True)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    if os.environ.get("GITHUB_ACTIONS") != "true":
        parser.error("Real scope archives are read only in cloud CI")
    if not re.fullmatch(r"togo-[0-9]+-[0-9]+", args.source_prefix):
        parser.error("Use exact togo-RUN-ATTEMPT")
    repository = os.environ["GITHUB_REPOSITORY"]
    run_id = os.environ["GITHUB_RUN_ID"] + "-" + os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    base = ROOT / "build" / "scope-review" / run_id
    incoming, output = base / "metadata", base / "review"
    incoming.mkdir(parents=True, exist_ok=True)
    assets, manifest, archive_tags = load_review_inputs(repository, args.release_tag, args.archive_release_tag)
    records, bindings = [], []
    pattern = re.compile(re.escape(args.source_prefix) + r"-b[0-9]+\.records\.jsonl")
    for name in sorted(n for n in assets if pattern.fullmatch(n)):
        path = download_checked(name, assets[name], incoming, repository, args.release_tag)
        records.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
        bindings.append({"name": name, "sha256": sha(path.read_bytes())})
    if not records:
        raise ValueError("No exact-prefix records were found")
    mapping = build_review(records, manifest, output, font_path="/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
    mapping["input_records"] = bindings
    mapping["source_prefix"] = args.source_prefix
    mapping["releases"] = release_bindings(args, archive_tags)
    write_json(output / "mapping.json", mapping)
    name = "scope-review-" + run_id
    archive = base / (name + ".tar.gz")
    with tarfile.open(archive, "w:gz") as tar:
        for path in sorted(output.iterdir()):
            tar.add(path, arcname=path.name)
    if archive.stat().st_size > MAX_PACK_BYTES:
        raise ValueError("Scope archive exceeds 32 MiB")
    summary = {"source_prefix": args.source_prefix, "releases": mapping["releases"], "file_count": mapping["file_count"],
               "tile_count": mapping["tile_count"], "sheets": mapping["sheets"],
               "preview_errors": mapping["preview_errors"], "scope_acceptance_passed": False,
               "archive": {"name": archive.name, "sha256": sha(archive.read_bytes()), "size_bytes": archive.stat().st_size}}
    summary_path = base / (name + ".summary.json")
    write_json(summary_path, summary)
    if args.publish:
        gh("release", "upload", args.publish_tag or args.release_tag, str(archive), str(summary_path), "--repo", repository)
    print(json.dumps({k: summary[k] for k in ("file_count", "tile_count", "preview_errors", "archive")}))


if __name__ == "__main__":
    main()
