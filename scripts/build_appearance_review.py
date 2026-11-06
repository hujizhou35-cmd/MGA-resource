"""Cloud-only, bounded real-preview review pack; selection never grants acceptance."""
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

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from mga_resources.appearance import ANALYSIS_VERSION
from backfill_appearance import DigestReader, download_checked, gh, sha

CASES = ("transparent_rgb", "small_object_white_background", "pale_color", "single_hue_line",
         "grayscale_shading", "black_white_accent", "enclosed_white_structure", "cross_source_style")
MAX_PREVIEWS = 48
MAX_PREVIEW_BYTES = 2 * 1024 * 1024
MAX_PACK_BYTES = 32 * 1024 * 1024
MAX_TRANSFER_BYTES = 4 * 1024 * 1024 * 1024


def _json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def descriptors(records):
    """Use the exact measured file; family metadata must not lend its color."""
    output = []
    for record in records:
        for file in record.get("files", []):
            appearance = file.get("appearance") or {}
            binding = appearance.get("binding") or {}
            preview = file.get("preview") or {}
            color = appearance.get("color") or {}
            if (binding.get("file_sha256") != file.get("sha256")
                    or binding.get("preview_sha256") != preview.get("sha256")
                    or appearance.get("classification_version") != ANALYSIS_VERSION
                    or color.get("review_status") != "machine_measured_pending_visual_review"):
                continue
            archive = preview.get("archive") or {}
            member = archive.get("member", "")
            if (not member.startswith("previews/") or "\\" in member or ":" in member
                    or any(p in {"", ".", ".."} for p in member.split("/"))
                    or not re.fullmatch(r"[a-f0-9]{64}", preview.get("sha256", ""))):
                continue
            output.append({"candidate_id": file["sha256"], "asset_id": record["id"],
                "family_id": record.get("family_id", record["id"]), "title": record.get("title"),
                "source": record.get("source"), "sources": record.get("sources") or [record.get("source")],
                "source_url": record.get("source_url"), "original_sha256": file["sha256"],
                "original_archive": file.get("archive"), "format": file.get("format"),
                "structure": file.get("structure"), "preview": preview, "appearance": appearance})
    return output


def _score(case, row):
    color = row["appearance"]["color"]
    raw, fg = color["raw_statistics"], color["candidate_foreground_statistics"]
    traits = color.get("palette_traits", [])
    visible = raw["visible_pixel_count"]
    mode = color["mode"]
    background = color["background"].get("detected")
    title = (row.get("title") or "").lower()
    if case == "transparent_rgb":
        return (2 if row["format"] == "png" else 1) + raw["transparent_pixel_count"] / 262144 if raw["transparent_pixel_count"] else None
    if case == "small_object_white_background":
        ratio = fg["visible_pixel_count"] / max(1, visible)
        return 1 - ratio if background and 0 < ratio < .25 and mode == "color" else None
    if case == "pale_color":
        return (raw.get("chromatic_mean_luma_0_255") or 0) if mode == "color" and "light_palette" in traits else None
    if case == "single_hue_line":
        # Sparse colored ink finds line-art candidates; line-art itself needs viewing.
        return 1 - visible / 262144 if mode == "color" and "monochrome_hue" in traits and visible < 100000 else None
    if case == "grayscale_shading":
        middle = fg["midtone_neutral_alpha_weight"] / max(1, fg["nonwhite_ink_alpha_weight"])
        return len(fg["dominant_visible_rgb"]) / 12 + 1 - abs(middle - .5) if mode == "grayscale" else None
    if case == "black_white_accent":
        chroma = fg["chromatic_alpha_weight"]
        # Prefer a visible small accent, not sub-pixel noise.
        red = fg["hue_alpha_weights"].get("0", 0) / max(1, chroma)
        return (2 if red > .5 else 0) + 1 - fg["chromatic_fraction_of_nonwhite_ink"] if mode == "color" and "accent" in traits and chroma >= 50 and fg["dark_neutral_alpha_weight"] > 100 else None
    if case == "enclosed_white_structure":
        return fg["white_neutral_alpha_weight"] if background and fg["white_neutral_alpha_weight"] >= 100 else None
    if case == "cross_source_style":
        return (4 if "syringe" in title else 2 if "tube" in title else 1) if any(t in title for t in ("syringe", "tube", "flask")) else None
    raise ValueError("Unknown appearance case")


def select_candidates(records, per_case=6):
    if not 1 <= per_case <= 6:
        raise ValueError("Choose 1 to 6 candidates per case")
    rows = descriptors(records)
    selected, cases = {}, []
    for case in CASES:
        ranked = [(score, row) for row in rows if (score := _score(case, row)) is not None]
        ranked.sort(key=lambda pair: (-pair[0], pair[1]["original_sha256"]))
        chosen, families, source_counts = [], set(), defaultdict(int)
        # First pass covers sources; second permits another independent family.
        for cap in (1, 2, per_case):
            for score, row in ranked:
                if len(chosen) >= per_case:
                    break
                if row["family_id"] in families or source_counts[row["source"]] >= cap:
                    continue
                key = row["candidate_id"]
                selected[key] = row
                chosen.append({"candidate_id": key, "selection_score": score})
                families.add(row["family_id"])
                source_counts[row["source"]] += 1
        cases.append({"id": case, "candidates": chosen, "eligible_candidates": len(ranked),
                      "passed": None, "status": "pending_actual_visual_review"})
    if len(selected) > MAX_PREVIEWS:
        raise ValueError("Selected preview limit exceeded")
    return {"schema_version": 1, "analysis_version": ANALYSIS_VERSION, "cases": cases,
            "candidates": selected, "selection_is_visual_acceptance": False}


def pixel_diagnostics(payload):
    with Image.open(io.BytesIO(payload)) as opened:
        if opened.format != "PNG" or opened.width * opened.height > 1024 * 1024:
            raise ValueError("Expected a bounded PNG preview")
        image = opened.convert("RGBA")
    hist = image.getcolors(image.width * image.height)
    transparent = [(count, rgba) for count, rgba in hist if rgba[3] == 0]
    return {"width": image.width, "height": image.height,
            "fully_transparent_pixels": sum(c for c, _ in transparent),
            "transparent_nonzero_rgb_pixels": sum(c for c, rgba in transparent if any(rgba[:3])),
            "transparent_chromatic_rgb_pixels": sum(c for c, rgba in transparent if max(rgba[:3]) - min(rgba[:3]) >= 8),
            "transparent_rgb_examples": [list(rgba[:3]) for _, rgba in sorted(transparent, reverse=True)[:6]],
            "image_unchanged": True}


def fetch_selected(plan, manifest, *, opener=None):
    """Stream each cloud archive once; store only hash-verified selected previews."""
    groups = defaultdict(list)
    for row in plan["candidates"].values():
        groups[row["preview"]["archive"]["url"]].append(row)
    if sum(manifest.get(url, {}).get("size", MAX_TRANSFER_BYTES + 1) for url in groups) > MAX_TRANSFER_BYTES:
        raise ValueError("Selected archive transfer exceeds review bound")
    opener = opener or (lambda url: urlopen(Request(url, headers={"User-Agent": "MGA-appearance-review/1"}), timeout=60))
    payloads, reports, errors = {}, [], {}
    for url, rows in sorted(groups.items()):
        remote = manifest.get(url, {})
        expected = {}
        for row in rows:
            preview = row["preview"]
            member = preview["archive"]["member"]
            if member in expected and expected[member]["sha256"] != preview["sha256"]:
                raise ValueError("Conflicting preview member hashes")
            expected[member] = preview
        buffered, reader, seen = {}, None, set()
        report = {"url": url, "selected_members": len(expected), "verified": False}
        try:
            if not re.fullmatch(r"sha256:[a-f0-9]{64}", remote.get("digest", "")):
                raise ValueError("GitHub archive SHA-256 is required")
            with closing(opener(url)) as source:
                reader = DigestReader(source, float("inf"), time.monotonic)
                with tarfile.open(fileobj=reader, mode="r|gz") as archive:
                    for member in archive:
                        if member.name not in expected:
                            continue
                        if member.name in seen:
                            raise ValueError("Duplicate selected archive member")
                        seen.add(member.name)
                        preview = expected[member.name]
                        if not member.isfile() or not 0 < member.size <= MAX_PREVIEW_BYTES:
                            raise ValueError("Preview member is not a bounded regular file")
                        with archive.extractfile(member) as stream:
                            payload = stream.read(MAX_PREVIEW_BYTES + 1)
                        if sha(payload) != preview["sha256"] or (preview.get("size_bytes") is not None and len(payload) != preview["size_bytes"]):
                            raise ValueError("Preview SHA-256 or size mismatch")
                        pixel_diagnostics(payload)
                        buffered[member.name] = payload
                while reader.read(1024 * 1024):
                    pass
            if reader.bytes_read != remote.get("size") or "sha256:" + reader.hasher.hexdigest() != remote["digest"]:
                raise ValueError("Complete archive SHA-256 or size mismatch")
            report.update(verified=True, sha256=reader.hasher.hexdigest(), size_bytes=reader.bytes_read)
            for row in rows:
                payload = buffered.get(row["preview"]["archive"]["member"])
                if payload is None:
                    errors[row["candidate_id"]] = "Preview absent from archive"
                else:
                    payloads[row["candidate_id"]] = payload
        except (OSError, ValueError, EOFError, tarfile.TarError) as exc:
            report["error"] = str(exc)
            for row in rows:
                errors[row["candidate_id"]] = str(exc)
        report["compressed_bytes_read"] = reader.bytes_read if reader else 0
        reports.append(report)
    if sum(map(len, payloads.values())) > MAX_PACK_BYTES:
        raise ValueError("Selected preview payload exceeds bounded pack limit")
    return payloads, reports, errors


def render_case(case, candidates, payloads, path, font_path):
    font, small = ImageFont.truetype(font_path, 21), ImageFont.truetype(font_path, 15)
    width, cell_h = 1080, 630
    sheet = Image.new("RGB", (width, 65 + max(1, math.ceil(len(case["candidates"]) / 2)) * cell_h), "#e6eaf0")
    draw = ImageDraw.Draw(sheet)
    draw.text((12, 8), case["id"] + " — candidates, not acceptance", font=font, fill="black")
    for index, choice in enumerate(case["candidates"]):
        row = candidates[choice["candidate_id"]]
        x, y = (index % 2) * 540 + 10, 65 + (index // 2) * cell_h
        for yy in range(512):
            for xx in range(0, 512, 16):
                draw.line((x + xx, y + yy, x + min(xx + 15, 511), y + yy), fill="#dde4eb" if (xx // 16 + yy // 16) % 2 else "white")
        payload = payloads.get(row["candidate_id"])
        if payload:
            with Image.open(io.BytesIO(payload)) as source:
                im = source.convert("RGBA")
                im.thumbnail((512, 512))
                sheet.paste(im, (x + (512 - im.width) // 2, y + (512 - im.height) // 2), im)
        c = row["appearance"]["color"]
        lines = [f"{index + 1}. {row['source']} | {row.get('title')}",
                 row["original_sha256"][:32], c["mode"] + " | " + ", ".join(c["palette_traits"]),
                 "style: " + ", ".join(row["appearance"]["style"]["representation"]["values"])]
        for n, line in enumerate(lines):
            while draw.textlength(line, font=small) > 515:
                line = line[:-2] + "…"
            draw.text((x, y + 517 + n * 23), line, font=small, fill="black")
    sheet.save(path, optimize=True)
    return {"path": path.name, "sha256": sha(path.read_bytes()), "width": sheet.width, "height": sheet.height}


def build_pack(plan, manifest, output, *, font_path, opener=None):
    output.mkdir(parents=True, exist_ok=True)
    preview_dir = output / "previews"
    preview_dir.mkdir(exist_ok=True)
    payloads, reports, errors = fetch_selected(plan, manifest, opener=opener)
    for key, payload in payloads.items():
        name = key + ".png"
        (preview_dir / name).write_bytes(payload)
        plan["candidates"][key]["review_preview"] = {"path": "previews/" + name,
            "sha256": sha(payload), "size_bytes": len(payload), "original_preview_bytes": True}
        plan["candidates"][key]["pixel_diagnostics"] = pixel_diagnostics(payload)
    for case in plan["cases"]:
        case["sheet"] = render_case(case, plan["candidates"], payloads, output / (case["id"] + ".png"), font_path)
    plan.update(archive_verification=reports, preview_errors=errors,
        original_files_downloaded=0, source_websites_requested=0,
        preview_files=len(payloads), maximum_selected_previews=MAX_PREVIEWS,
        generated_at=datetime.now(timezone.utc).isoformat())
    _json(output / "mapping.json", plan)
    _json(output / "judgments.template.json", {"schema_version": 1, "bindings": plan.get("bindings", {}),
        "reviewer": None, "appearance_cases": [{"id": case, "passed": None, "expected": None,
            "observed": None, "notes": None, "evidence": None, "real_files": []} for case in CASES]})
    total = sum(p.stat().st_size for p in output.rglob("*") if p.is_file())
    if total > MAX_PACK_BYTES:
        raise ValueError("Review package exceeds 32 MiB bound")
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--assembly-prefix", required=True)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    if os.environ.get("GITHUB_ACTIONS") != "true":
        parser.error("Real preview archives are read only in cloud CI; local work uses bounded review packs")
    if not re.fullmatch(r"assembly-[0-9]+-[0-9]+", args.assembly_prefix):
        parser.error("Use an exact assembly-RUN-ATTEMPT prefix")
    from assemble import list_release_assets
    repository = os.environ["GITHUB_REPOSITORY"]
    run_id = os.environ["GITHUB_RUN_ID"] + "-" + os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    base = ROOT / "build" / "appearance-review" / run_id
    incoming, output = base / "metadata", base / "review"
    incoming.mkdir(parents=True, exist_ok=True)
    assets = list_release_assets(repository, args.release_tag)
    catalog_name, report_name = args.assembly_prefix + ".catalog.jsonl", args.assembly_prefix + ".report.json"
    catalog = download_checked(catalog_name, assets[catalog_name], incoming, repository, args.release_tag)
    report = download_checked(report_name, assets[report_name], incoming, repository, args.release_tag)
    catalog_sha = sha(catalog.read_bytes())
    if json.loads(report.read_text(encoding="utf-8"))["summary"]["catalog_sha256"] != catalog_sha:
        raise ValueError("Assembly report and catalog disagree")
    records = [json.loads(line) for line in catalog.read_text(encoding="utf-8").splitlines() if line.strip()]
    plan = select_candidates(records)
    plan["bindings"] = {"assembly_prefix": args.assembly_prefix, "catalog_sha256": catalog_sha,
        "assembly_report_sha256": sha(report.read_bytes()), "analysis_version": ANALYSIS_VERSION}
    manifest = {row["browser_download_url"]: row for name, row in assets.items() if name.endswith(".tar.gz")}
    font_path = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
    build_pack(plan, manifest, output, font_path=font_path)
    name = "appearance-review-" + run_id
    archive = base / (name + ".tar.gz")
    with tarfile.open(archive, "w:gz") as tar:
        for path in sorted(output.rglob("*")):
            if path.is_file():
                tar.add(path, arcname=str(path.relative_to(output)).replace("\\", "/"))
    summary = base / (name + ".summary.json")
    _json(summary, {"schema_version": 1, "bindings": plan["bindings"], "preview_files": plan["preview_files"],
        "preview_errors": plan["preview_errors"], "visual_acceptance_passed": False,
        "archive": {"name": archive.name, "size_bytes": archive.stat().st_size, "sha256": sha(archive.read_bytes())}})
    if args.publish:
        gh("release", "upload", args.release_tag, str(archive), str(summary), "--repo", repository)
    print(json.dumps({"archive": archive.name, "preview_files": plan["preview_files"], "errors": len(plan["preview_errors"])}))


if __name__ == "__main__":
    main()
