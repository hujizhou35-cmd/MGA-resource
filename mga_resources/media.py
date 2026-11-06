"""Inspect and preview artwork without changing its original bytes.

This module does not download anything. Bulk processing belongs in cloud CI.
Machine decoding and rendering are deliberately not called scientific or
Illustrator acceptance.
"""

from __future__ import annotations

import base64
import hashlib
import io
import math
import re
import warnings
from pathlib import Path
from typing import Any

from defusedxml import ElementTree as SafeET
from PIL import Image, ImageDraw, ImageOps


class MediaValidationError(ValueError):
    """An unreadable or unsafe source must not be published automatically."""


MAX_SOURCE_BYTES = 128 * 1024 * 1024
MAX_PIXELS = 100_000_000
SVG_NS = "http://www.w3.org/2000/svg"
NONLOADING_NAMESPACES = (
    "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "http://creativecommons.org/ns#",
    "http://purl.org/dc/",
    "http://www.inkscape.org/namespaces/inkscape",
    "http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd",
)
VECTOR_TAGS = {"path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "text", "use"}
ACTIVE_TAGS = {"script", "foreignobject", "animate", "animatetransform", "animatemotion", "set"}
MIME_TYPES = {
    "svg": "image/svg+xml", "png": "image/png", "jpeg": "image/jpeg",
    "webp": "image/webp", "gif": "image/gif", "tiff": "image/tiff",
    "pdf": "application/pdf", "eps": "application/postscript",
    "ai": "application/postscript",
}


def _tag(value: str) -> str:
    return value.rsplit("}", 1)[-1].lower()


def _length(value: str | None) -> float | None:
    if not value:
        return None
    match = re.fullmatch(r"\s*([\d.+eE-]+)\s*(px|pt|pc|mm|cm|in)?\s*", value)
    if not match:
        return None
    try:
        number = float(match.group(1))
    except ValueError:
        return None
    number *= {None: 1, "px": 1, "pt": 96 / 72, "pc": 16, "mm": 96 / 25.4, "cm": 96 / 2.54, "in": 96}[match.group(2)]
    return number if math.isfinite(number) and number > 0 else None


def _decode_raster(data: bytes) -> tuple[Image.Image, dict[str, Any]]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as candidate:
                width, height = candidate.size
                if width <= 0 or height <= 0 or width * height > MAX_PIXELS:
                    raise MediaValidationError("Invalid or oversized raster dimensions")
                candidate.verify()
            with Image.open(io.BytesIO(data)) as candidate:
                fmt = (candidate.format or "").lower()
                frames = getattr(candidate, "n_frames", 1)
                image = ImageOps.exif_transpose(candidate).convert("RGBA")
                image.load()
                # Pixel alpha is inspected; an RGBA container alone does not
                # prove that its background is transparent.
                low, high = image.getchannel("A").getextrema()
                return image, {
                    "format": fmt,
                    "dimensions": {"width": image.width, "height": image.height, "unit": "px"},
                    "transparency": {
                        "has_transparent_pixels": low < 255,
                        "has_fully_transparent_pixels": low == 0,
                        "fully_transparent": high == 0,
                    },
                    "frames": frames,
                    "print_size_mm_at_300dpi": {
                        "width": round(image.width / 300 * 25.4, 2),
                        "height": round(image.height / 300 * 25.4, 2),
                    },
                }
    except MediaValidationError:
        raise
    except Exception as exc:
        raise MediaValidationError(f"Raster could not be fully decoded: {type(exc).__name__}: {exc}") from exc


def _inspect_svg(data: bytes) -> dict[str, Any]:
    # A normal SVG 1.1 PUBLIC doctype is allowed, but its DTD is not loaded.
    # Entity declarations and external entities are forbidden by defusedxml.
    if re.search(br"<!\s*ENTITY\b", data, re.I):
        raise MediaValidationError("SVG entity declaration is not allowed")
    if re.search(br"<\?xml-stylesheet\b", data, re.I):
        raise MediaValidationError("SVG external stylesheet instruction is not allowed")
    try:
        root = SafeET.fromstring(data, forbid_dtd=False, forbid_entities=True, forbid_external=True)
    except Exception as exc:
        raise MediaValidationError(f"SVG XML could not be safely parsed: {exc}") from exc
    if _tag(root.tag) != "svg":
        raise MediaValidationError("XML root is not SVG")
    counts: dict[str, int] = {}
    embedded_images = []
    findings = []
    css_urls = re.compile(r"url\(\s*['\"]?\s*([^)'\"\s]+)", re.I)

    def inspect_css(value: str) -> None:
        value = re.sub(r"/\*.*?\*/", "", value, flags=re.S)
        value = re.sub(
            r"\\([0-9a-fA-F]{1,6})\s?|\\([^\r\n])",
            lambda match: chr(int(match.group(1), 16)) if match.group(1) and int(match.group(1), 16) <= 0x10FFFF else (match.group(2) or "\ufffd"),
            value,
        )
        if re.search(r"@import\b|expression\s*\(|javascript\s*:", value, re.I):
            raise MediaValidationError("SVG contains active or external CSS")
        for match in css_urls.finditer(value):
            if not match.group(1).startswith("#"):
                raise MediaValidationError("SVG CSS loads an external resource")

    for element in root.iter():
        tag = _tag(element.tag)
        counts[tag] = counts.get(tag, 0) + 1
        if tag in ACTIVE_TAGS:
            raise MediaValidationError(f"SVG contains unsupported active element: {tag}")
        if tag == "style" and element.text:
            inspect_css(element.text)
        for attr, raw in element.attrib.items():
            key = _tag(attr)
            value = raw.strip()
            if key.startswith("on") and key != "only_selected":
                raise MediaValidationError("SVG contains an event handler")
            inspect_css(value)
            if key not in {"href", "src", "resource", "base"} or value.startswith("#"):
                continue
            # References inside source/author/license metadata do not fetch art.
            if any(element.tag.startswith("{" + ns) for ns in NONLOADING_NAMESPACES):
                continue
            if tag == "a" and re.match(r"^(https?://|mailto:)", value, re.I):
                continue
            if tag == "image" and re.match(r"^data:image/(png|jpeg|jpg|webp);base64,", value, re.I):
                try:
                    encoded = value.split(",", 1)[1]
                    payload = base64.b64decode(re.sub(r"\s+", "", encoded), validate=True)
                    image, details = _decode_raster(payload)
                    image.close()
                    embedded_images.append(details)
                except Exception as exc:
                    raise MediaValidationError(f"SVG embedded image is invalid: {exc}") from exc
                continue
            raise MediaValidationError(f"SVG loads an external or unsupported resource via {tag}/{key}")
    width, height = _length(root.get("width")), _length(root.get("height"))
    viewbox = None
    if root.get("viewBox"):
        try:
            values = [float(x) for x in re.split(r"[\s,]+", root.get("viewBox", "").strip())]
            if len(values) == 4 and all(math.isfinite(x) for x in values) and values[2] > 0 and values[3] > 0:
                viewbox = values
        except ValueError:
            pass
    if width is None and viewbox:
        width = viewbox[2] if height is None else height * viewbox[2] / viewbox[3]
    if height is None and viewbox:
        height = viewbox[3] if width is None else width * viewbox[3] / viewbox[2]
    if width is None or height is None:
        findings.append("SVG has no resolvable intrinsic size; preview uses a square viewport")
    shape_count = sum(counts.get(tag, 0) for tag in VECTOR_TAGS)
    # Transparent bounding/background rectangles do not make a PNG wrapper an
    # editable vector illustration. Treat these separately from real geometry.
    substantive = sum(counts.get(tag, 0) for tag in VECTOR_TAGS - {"rect"})
    if embedded_images and substantive == 0 and counts.get("rect", 0) <= 1:
        structure = "embedded_raster"
    elif embedded_images:
        structure = "mixed"
    else:
        structure = "pure_vector"
    if shape_count == 0 and not embedded_images:
        findings.append("SVG has no visible vector geometry or embedded raster")
    if counts.get("text", 0):
        findings.append("Contains live text; appearance can depend on installed fonts")
    if counts.get("filter", 0):
        findings.append("Contains SVG filters; Illustrator compatibility is untested")
    return {
        "format": "svg", "media_type": MIME_TYPES["svg"], "structure": structure,
        "dimensions": {"width": width, "height": height, "unit": "css_px", "viewbox": viewbox},
        "transparency": {"has_transparent_pixels": None, "has_fully_transparent_pixels": None, "fully_transparent": None},
        "element_counts": counts, "vector_element_count": shape_count,
        "embedded_images": embedded_images, "findings": findings,
    }


def inspect_media(data: bytes, filename: str = "") -> dict[str, Any]:
    """Return factual structure/decoding data, rejecting active or broken media."""
    if not data or len(data) > MAX_SOURCE_BYTES:
        raise MediaValidationError("Source is empty or exceeds the processing byte limit")
    common = {"sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}
    prefix = data[:8192].lstrip(b"\xef\xbb\xbf\x00\t\n\r ")
    if re.search(br"<(?:[\w.-]+:)?svg(?:\s|>)", prefix, re.I):
        result = _inspect_svg(data)
        result.update(common)
        return result
    if prefix.startswith(b"%PDF") or prefix.startswith(b"%!PS"):
        fmt = "ai" if filename.lower().endswith(".ai") else ("pdf" if prefix.startswith(b"%PDF") else "eps")
        return {
            **common, "format": fmt, "media_type": MIME_TYPES[fmt], "structure": "unknown",
            "dimensions": None, "transparency": None,
            "findings": ["Original retained; this pipeline does not inspect PDF/AI/EPS editability or render it"],
            "unsupported": True,
        }
    image, details = _decode_raster(data)
    image.close()
    if details["format"] not in MIME_TYPES:
        raise MediaValidationError(f"Unsupported decoded image format: {details['format']}")
    return {
        **common, **details, "media_type": MIME_TYPES[details["format"]],
        "structure": "raster", "findings": ["Preview uses the first frame"] if details["frames"] > 1 else [],
    }


def process_file(path: str | Path, preview_dir: str | Path | None = None, preview_size: int = 512) -> dict[str, Any]:
    """Inspect one local/cloud-CI file, optionally create a bounded PNG preview.

    The returned preview path is only the filename, relative to preview_dir.
    Callers decide its archive/public URL. Original files are never rewritten.
    """
    path = Path(path)
    if path.stat().st_size > MAX_SOURCE_BYTES:
        raise MediaValidationError("Source exceeds the processing byte limit")
    data = path.read_bytes()
    result = inspect_media(data, path.name)
    findings = result.pop("findings", [])
    result["validation"] = {
        "status": "unsupported" if result.pop("unsupported", False) else "machine_checked",
        "findings": findings, "scientific_review": "not_reviewed",
        "visual_review": "not_reviewed", "illustrator_editability": "not_tested",
    }
    if preview_dir is None or result["validation"]["status"] == "unsupported":
        result["preview"] = None
        return result
    if not 32 <= preview_size <= 2048:
        raise ValueError("preview_size must be between 32 and 2048 pixels")
    preview_dir = Path(preview_dir)
    preview_dir.mkdir(parents=True, exist_ok=True)
    try:
        if result["format"] == "svg":
            import cairosvg
            dimensions = result["dimensions"]
            width = dimensions["width"] or preview_size
            height = dimensions["height"] or preview_size
            scale = min(preview_size / width, preview_size / height)
            png = cairosvg.svg2png(
                bytestring=data, output_width=max(1, round(width * scale)),
                output_height=max(1, round(height * scale)),
                parent_width=width, parent_height=height,
            )
            image, preview_info = _decode_raster(png)
            result["transparency"] = {**preview_info["transparency"], "measured_from": "rendered_preview"}
        else:
            image, _ = _decode_raster(data)
        image.thumbnail((preview_size, preview_size), Image.Resampling.LANCZOS)
        preview_path = preview_dir / f"{result['sha256']}.png"
        image.save(preview_path, format="PNG", optimize=True)
        preview_bytes = preview_path.read_bytes()
        result["preview"] = {
            "path": preview_path.name, "format": "png", "width": image.width, "height": image.height,
            "sha256": hashlib.sha256(preview_bytes).hexdigest(), "size_bytes": len(preview_bytes),
        }
        image.close()
        result["validation"]["status"] = "machine_checked_and_rendered"
    except Exception as exc:
        result["preview"] = None
        result["validation"]["status"] = "preview_failed"
        result["validation"]["findings"].append(f"Preview renderer failed: {type(exc).__name__}: {exc}")
    return result


def create_contact_sheet(items: list[dict[str, Any]], output_path: str | Path, columns: int = 5, cell_size: int = 180) -> dict[str, Any]:
    """Create a bounded review sheet from local previews; not a visual approval."""
    if not items or len(items) > 100:
        raise ValueError("A contact sheet must have 1–100 items")
    if not 1 <= columns <= 10 or not 64 <= cell_size <= 512:
        raise ValueError("Invalid contact sheet dimensions")
    label_height = 44
    rows = math.ceil(len(items) / columns)
    sheet = Image.new("RGB", (columns * cell_size, rows * (cell_size + label_height)), "white")
    draw = ImageDraw.Draw(sheet)
    index = []
    for number, item in enumerate(items):
        x = (number % columns) * cell_size
        y = (number // columns) * (cell_size + label_height)
        with Image.open(item["preview_path"]) as source:
            preview = source.convert("RGBA")
            preview.thumbnail((cell_size - 12, cell_size - 12), Image.Resampling.LANCZOS)
            px, py = x + (cell_size - preview.width) // 2, y + (cell_size - preview.height) // 2
            sheet.paste(preview, (px, py), preview)
        # The sheet index preserves the full Unicode title. The rendered short
        # caption uses ASCII when the runner's default font lacks CJK glyphs.
        title = str(item.get("title") or item.get("id") or "")
        caption = title.encode("ascii", "replace").decode("ascii")
        draw.text((x + 5, y + cell_size), f"{number + 1}. {caption[:26]}", fill="black")
        if len(caption) > 26:
            draw.text((x + 5, y + cell_size + 14), caption[26:54], fill="black")
        index.append({"number": number + 1, "id": item.get("id"), "title": title})
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path, format="PNG", optimize=True)
    return {"path": output_path.name, "items": index, "visual_review": "not_reviewed"}
