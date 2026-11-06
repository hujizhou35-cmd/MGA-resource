"""Cloud-only extraction of a bounded, explicitly nominated exact-file review set."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import re
import sys
import tarfile

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from backfill_appearance import gh, sha
from build_appearance_review import fetch_selected

MAX_FILES = 20
MAX_PACK_BYTES = 24 * 1024 * 1024


def validate_plan(document):
    """Titles/questions nominate files; only exact immutable bindings select them."""
    rows = document.get("files", [])
    if document.get("schema_version") != 1 or not 1 <= len(rows) <= MAX_FILES:
        raise ValueError("An exact review plan must contain 1 to 20 files")
    selected = {}
    for index, row in enumerate(rows, 1):
        digest, preview = row.get("original_sha256"), row.get("preview", {})
        if not all(re.fullmatch(r"[a-f0-9]{64}", value or "") for value in (digest, preview.get("sha256"))):
            raise ValueError("Each file needs exact original/preview SHA-256")
        for key, prefix in (("original_archive", "files/"), ("preview", "previews/")):
            archive = row.get(key, {}) if key == "original_archive" else preview.get("archive", {})
            member, url = archive.get("member", ""), archive.get("url", "")
            if (not member.startswith(prefix) or not digest in member or "\\" in member
                    or ":" in member or any(p in {"", ".", ".."} for p in member.split("/"))
                    or not re.fullmatch(r"https://github\.com/[^/]+/[^/]+/releases/download/[^/]+/[^/]+\.tar\.gz", url)):
                raise ValueError("Unsafe or unbound exact archive member")
        if digest in selected:
            raise ValueError("Duplicate original file in bounded review plan")
        if not all(str(row.get(key, "")).strip() for key in ("source", "source_id", "asset_id", "review_question")):
            raise ValueError("File identity and concrete review question are required")
        selected[digest] = {**row, "candidate_id": digest, "tile": index,
                            "selection_is_acceptance": False}
    return selected


def render_sheet(rows, payloads, output, font_path):
    font = ImageFont.truetype(font_path, 17)
    sheet = Image.new("RGB", (1080, 45 + ((len(rows) + 1) // 2) * 590), "#e6e9ed")
    draw = ImageDraw.Draw(sheet)
    draw.text((12, 10), "Exact-file scientific review / candidates, not acceptance", font=font, fill="black")
    for i, row in enumerate(rows):
        x, y = (i % 2) * 540 + 8, 45 + (i // 2) * 590
        draw.rectangle((x, y, x + 524, y + 582), fill="white")
        payload = payloads.get(row["candidate_id"])
        if payload:
            with Image.open(io.BytesIO(payload)) as original:
                image = original.convert("RGBA")
                image.thumbnail((512, 512))
                sheet.paste(image, (x + (524-image.width)//2, y + (512-image.height)//2), image)
        else:
            draw.text((x + 10, y + 240), "Preview unavailable: no judgment", font=font, fill="red")
        lines = [f"{row['tile']:02d}. {row['source']} | {row.get('title') or row['source_id']}", row["original_sha256"][:36]]
        for j, line in enumerate(lines):
            while draw.textlength(line, font=font) > 510:
                line = line[:-2] + "…"
            draw.text((x + 8, y + 520 + j * 25), line, font=font, fill="black")
    sheet.save(output)
    return {"path": output.name, "sha256": sha(output.read_bytes())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--publish-tag", help="Separate evidence Release; source archives stay in --release-tag")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    if os.environ.get("GITHUB_ACTIONS") != "true":
        parser.error("Real archive extraction is cloud-only; local review receives only the bounded preview pack")
    plan_path = (ROOT / args.plan).resolve()
    if not plan_path.is_relative_to((ROOT / "data" / "review-plans").resolve()) or plan_path.stat().st_size > 128 * 1024:
        parser.error("Use a bounded repository data/review-plans JSON")
    raw = plan_path.read_bytes()
    document = json.loads(raw)
    candidates = validate_plan(document)
    from assemble import list_release_assets
    repository = os.environ["GITHUB_REPOSITORY"]
    assets = list_release_assets(repository, args.release_tag)
    manifest = {row["browser_download_url"]: row for row in assets.values()}
    for row in candidates.values():
        for archive in (row["original_archive"], row["preview"]["archive"]):
            if archive["url"] not in manifest or not re.fullmatch(r"sha256:[a-f0-9]{64}", manifest[archive["url"]].get("digest", "")):
                raise ValueError("Exact archive is absent from the selected release or lacks its digest")
    run = os.environ["GITHUB_RUN_ID"] + "-" + os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    base = ROOT / "build" / "exact-review" / run
    output = base / "review"
    (output / "previews").mkdir(parents=True, exist_ok=True)
    payloads, transfers, errors = fetch_selected({"candidates": candidates}, manifest)
    for key, payload in payloads.items():
        (output / "previews" / (key + ".png")).write_bytes(payload)
        candidates[key]["review_preview"] = {"path": "previews/"+key+".png", "sha256": sha(payload), "original_preview_bytes": True}
    name = "exact-review-" + run
    sheet = render_sheet(list(candidates.values()), payloads, output / (name+".review.png"), "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
    mapping = {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_release_tag": args.release_tag, "publish_tag": args.publish_tag or args.release_tag,
        "plan_path": args.plan, "plan_sha256": sha(raw), "candidates": candidates,
        "archive_verification": transfers, "preview_errors": errors, "sheet": sheet,
        "original_files_downloaded": 0, "source_websites_requested": 0, "scientific_acceptance_passed": False}
    (output / "mapping.json").write_text(json.dumps(mapping, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    if errors or len(payloads) != len(candidates):
        raise ValueError("Some exact previews are missing; do not publish incomplete visual evidence")
    archive = base / (name+".tar.gz")
    with tarfile.open(archive, "w:gz") as tar:
        for path in sorted(output.rglob("*")):
            if path.is_file():
                tar.add(path, arcname=path.relative_to(output).as_posix())
    if archive.stat().st_size > MAX_PACK_BYTES:
        raise ValueError("Bounded exact review pack exceeds 24 MiB")
    summary = {"files": len(candidates), "plan_sha256": sha(raw), "sheet": sheet,
               "source_release_tag": args.release_tag, "publish_tag": args.publish_tag or args.release_tag,
               "preview_errors": errors, "archive": {"name": archive.name, "size_bytes": archive.stat().st_size, "sha256": sha(archive.read_bytes())}}
    summary_path = base / (name+".summary.json")
    summary_path.write_text(json.dumps(summary, indent=2)+"\n", encoding="utf-8")
    if args.publish:
        gh("release", "upload", args.publish_tag or args.release_tag, str(archive), str(output / sheet["path"]), str(summary_path), "--repo", repository)
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
