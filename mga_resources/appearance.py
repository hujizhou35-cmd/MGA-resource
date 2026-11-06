"""File-bound appearance metadata from existing previews and explicit evidence.

No artwork is modified, recolored, downloaded or rendered here. Pixel measures
describe this preview; style assertions require per-file official evidence or
a hash-bound visual review. Confidence labels are heuristic, not probabilities.
"""
from __future__ import annotations

from collections import Counter, defaultdict, deque
import colorsys
import copy
import hashlib
import io
import json
import re
from typing import Any

from defusedxml import ElementTree as SafeET
from PIL import Image, ImageChops, ImageFilter, UnidentifiedImageError

VERSION = "appearance-1.1.0"
ANALYSIS_VERSION = VERSION
MAX_PREVIEW_PIXELS = 2048 * 2048
COLOR_RULES = {"strong_channel_spread": 8, "weak_channel_spread": 3,
               "minimum_chromatic_alpha_weight": 0.25, "white_candidate_min_channel": 248,
               "white_candidate_max_spread": 2, "white_candidate_min_alpha": 250,
               "minimum_white_border_fraction": 0.9, "black_endpoint_max": 48,
               "white_endpoint_min": 240, "black_white_endpoint_fraction": 0.8,
               "clear_chroma_channel_spread": 20, "pale_chroma_min_channel_max": 200,
               "minimum_midgray_interior_pixels": 4, "minimum_midgray_interior_fraction": 0.01}
REPRESENTATIONS = {"line_art", "silhouette", "flat_fill", "shaded", "hand_drawn", "3d_render"}
DETAILS = {"minimal", "moderate", "detailed"}
REALISMS = {"schematic", "semi_realistic", "realistic"}


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _valid_sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def evidence_revision(evidence):
    """Stable public-label evidence revision; no evidence has revision None."""
    if not evidence:
        return None
    return _sha(json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())


def cache_key(file_sha256, preview_sha256, version=ANALYSIS_VERSION, style_evidence_revision=None, *, svg_supplied=False):
    """Stable backfill cache key; use an evidence revision when labels change.

    This helper and analyze_appearance use the same encoded-preview cache key.
    Optional SVG structure and evidence revisions are explicit key components.
    """
    if not _valid_sha(file_sha256) or not _valid_sha(preview_sha256):
        raise ValueError("Cache keys require complete source and preview SHA-256 values")
    return _sha(json.dumps({"file_sha256": file_sha256, "preview_sha256": preview_sha256,
                           "version": version, "style_evidence_revision": style_evidence_revision,
                           "svg_supplied": bool(svg_supplied)},
                          sort_keys=True, separators=(",", ":")).encode())


def _round(value):
    return round(value, 6)


def _stats(histogram):
    """Alpha-weighted statistics, always excluding invisible RGB values."""
    weight = chromatic = clear_chromatic = display_chromatic = weak = dark = middle = white = 0.0
    visible = opaque = transparent = 0
    hue_weights = defaultdict(float)
    display_hue_weights = defaultdict(float)
    palette = defaultdict(float)
    luma_sum = spread_sum = chromatic_luma_sum = 0.0
    for (r, g, b, a), count in histogram.items():
        if a == 0:
            transparent += count
            continue
        visible += count
        opaque += count if a == 255 else 0
        mass = count * a / 255
        weight += mass
        spread = max(r, g, b) - min(r, g, b)
        luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
        luma_sum += mass * luma
        spread_sum += mass * spread
        palette[(r, g, b)] += mass
        if spread >= COLOR_RULES["strong_channel_spread"]:
            chromatic += mass
            clear = spread >= COLOR_RULES["clear_chroma_channel_spread"] or max(r, g, b) >= COLOR_RULES["pale_chroma_min_channel_max"]
            if clear:
                clear_chromatic += mass
            chromatic_luma_sum += mass * luma
            hue = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)[0]
            # Offset the circular bins so red and near-red share the same bin.
            hue_bin = int((hue * 360 + 15) % 360 // 30)
            hue_weights[hue_bin] += mass
            # Unpremultiplication can create saturated RGB in barely visible
            # antialias pixels. Keep it in raw stats, but require visible channel
            # separation before treating it as palette evidence.
            if clear and spread * a / 255 >= COLOR_RULES["strong_channel_spread"]:
                display_chromatic += mass
                display_hue_weights[hue_bin] += mass
        elif spread >= COLOR_RULES["weak_channel_spread"]:
            weak += mass
        elif luma <= COLOR_RULES["black_endpoint_max"]:
            dark += mass
        elif luma >= COLOR_RULES["white_endpoint_min"]:
            white += mass
        else:
            middle += mass
    nonwhite_ink = dark + middle + weak + chromatic
    return {
        "visible_pixel_count": visible, "transparent_pixel_count": transparent, "opaque_pixel_count": opaque,
        "alpha_weight": _round(weight), "chromatic_alpha_weight": _round(chromatic),
        "clear_chromatic_alpha_weight": _round(clear_chromatic),
        "display_chromatic_alpha_weight": _round(display_chromatic),
        "weak_chroma_alpha_weight": _round(weak), "dark_neutral_alpha_weight": _round(dark),
        "midtone_neutral_alpha_weight": _round(middle), "white_neutral_alpha_weight": _round(white),
        "nonwhite_ink_alpha_weight": _round(nonwhite_ink),
        "chromatic_fraction": _round(chromatic / weight) if weight else None,
        "chromatic_fraction_of_nonwhite_ink": _round(chromatic / nonwhite_ink) if nonwhite_ink else None,
        "mean_luma_0_255": _round(luma_sum / weight) if weight else None,
        "chromatic_mean_luma_0_255": _round(chromatic_luma_sum / chromatic) if chromatic else None,
        "mean_channel_spread_0_255": _round(spread_sum / weight) if weight else None,
        "hue_alpha_weights": {str(key): _round(value) for key, value in sorted(hue_weights.items())},
        "display_hue_alpha_weights": {str(key): _round(value) for key, value in sorted(display_hue_weights.items())},
        "dominant_visible_rgb": [{"rgb": list(rgb), "alpha_weight": _round(mass),
                                   "fraction": _round(mass / weight)}
                                  for rgb, mass in sorted(palette.items(), key=lambda pair: (-pair[1], pair[0]))[:12]],
    }


def _white_background_candidate(image, histogram):
    """A separate border-connected measurement hypothesis, never an image edit.

    White enclosed by colored/dark structures is retained. White structures
    connected to the border cannot be reliably separated and remain an explicit
    uncertainty; raw all-visible statistics are always retained alongside it.
    """
    width, height = image.size
    pixels = image.load()
    border = set(range(width)) | set(range((height - 1) * width, height * width))
    border |= {y * width for y in range(height)} | {y * width + width - 1 for y in range(height)}

    def is_white(index):
        r, g, b, a = pixels[index % width, index // width]
        return (a >= COLOR_RULES["white_candidate_min_alpha"] and min(r, g, b) >= 248
                and max(r, g, b) - min(r, g, b) <= 2)

    seeds = [index for index in border if is_white(index)]
    coverage = len(seeds) / len(border)
    result = {"method": "border_connected_near_white_candidate", "detected": False,
              "white_border_fraction": _round(coverage), "candidate_pixel_count": 0,
              "pixels_modified": 0, "confidence": "unknown", "uncertainty": []}
    if coverage < COLOR_RULES["minimum_white_border_fraction"]:
        return histogram, result
    # Build the exact per-pixel condition in Pillow C. The scanline flood below
    # visits horizontal runs, avoiding four Python neighbor checks per pixel.
    red, green, blue, alpha = image.split()
    low = ImageChops.darker(red, ImageChops.darker(green, blue))
    high = ImageChops.lighter(red, ImageChops.lighter(green, blue))
    mask = ImageChops.multiply(
        ImageChops.multiply(low.point(lambda value: 255 if value >= 248 else 0),
                            ImageChops.subtract(high, low).point(lambda value: 255 if value <= 2 else 0)),
        alpha.point(lambda value: 255 if value >= 250 else 0))
    remaining_mask = bytearray(mask.tobytes())
    queue = deque(seeds)
    while queue:
        index = queue.popleft()
        if remaining_mask[index] != 255:
            continue
        row_start = index // width * width
        left = max(row_start, remaining_mask.rfind(b"\0", row_start, index) + 1)
        right = remaining_mask.find(b"\0", index, row_start + width)
        if right == -1:
            right = row_start + width
        remaining_mask[left:right] = b"\x80" * (right - left)
        for offset in (-width, width):
            if not 0 <= row_start + offset < width * height:
                continue
            scan, end = left + offset, right + offset
            while scan < end:
                candidate = remaining_mask.find(b"\xff", scan, end)
                if candidate == -1:
                    break
                queue.append(candidate)
                gap = remaining_mask.find(b"\0", candidate, end)
                if gap == -1:
                    break
                scan = gap + 1
    selection = Image.frombytes("L", image.size, bytes(remaining_mask)).point(lambda value: 255 if value == 128 else 0)
    removed_image = Image.new("RGBA", image.size)
    removed_image.paste(image, mask=selection)
    removed = Counter({rgba: count for count, rgba in removed_image.getcolors(maxcolors=width * height) if rgba[3]})
    remaining = histogram - removed
    result.update(detected=True, candidate_pixel_count=sum(removed.values()), confidence="medium",
                  uncertainty=["Border-connected white can include real white structures; only measurement statistics exclude this candidate."])
    return remaining, result


def _mode(stats):
    if not stats["visible_pixel_count"] or stats["alpha_weight"] <= 0:
        return "unknown"
    # Absolute colored mass protects a tiny, real accent from white-background
    # dilution. Sparse/noisy color is reported separately, never silently erased.
    if stats["chromatic_alpha_weight"] >= COLOR_RULES["minimum_chromatic_alpha_weight"]:
        return "color"
    if stats["weak_chroma_alpha_weight"] >= COLOR_RULES["minimum_chromatic_alpha_weight"]:
        return "unknown"
    neutral = stats["dark_neutral_alpha_weight"] + stats["midtone_neutral_alpha_weight"]
    if neutral and stats["dark_neutral_alpha_weight"] / neutral < COLOR_RULES["black_white_endpoint_fraction"]:
        return "grayscale"
    return "black_white"


def _midgray_interior(image):
    """Gray 3x3 interiors support shading; isolated edge grays remain ambiguous."""
    r, g, b, a = image.split()
    low = ImageChops.darker(r, ImageChops.darker(g, b))
    high = ImageChops.lighter(r, ImageChops.lighter(g, b))
    mid = low.point(lambda v: 255 if 48 < v < 240 else 0)
    neutral = ImageChops.subtract(high, low).point(lambda v: 255 if v < 3 else 0)
    visible = a.point(lambda v: 255 if v >= 250 else 0)
    mask = ImageChops.multiply(ImageChops.multiply(mid, neutral), visible)
    return mask.filter(ImageFilter.MinFilter(3)).histogram()[255]


def _measure_color(image, svg_hints=None):
    histogram = Counter({rgba: count for count, rgba in image.getcolors(maxcolors=image.width * image.height)})
    raw = _stats(histogram)
    foreground_hist, background = _white_background_candidate(image, histogram)
    foreground = _stats(foreground_hist) if background["detected"] else raw
    measured = foreground if background["detected"] and foreground["visible_pixel_count"] else raw
    basis = "background_candidate_excluded_statistics" if measured is foreground else "all_visible_statistics"
    mode = _mode(measured)
    uncertainty = list(background["uncertainty"])
    confidence = "high"
    midgray_interior = _midgray_interior(image) if mode == "grayscale" else None
    structural_bw = (svg_hints or {}).get("black_white_paint_evidence", {}).get("supported") is True
    extra_basis = []
    if mode == "grayscale":
        if structural_bw:
            mode = "black_white"
            extra_basis.append("exact_svg_opaque_black_white_paints_without_complex_paint_effects")
            uncertainty.append("Preview mid-gray pixels are consistent with antialiasing of this SVG's explicit opaque black/white paints.")
        elif (midgray_interior < COLOR_RULES["minimum_midgray_interior_pixels"]
                or midgray_interior / max(1, measured["midtone_neutral_alpha_weight"]) < COLOR_RULES["minimum_midgray_interior_fraction"]):
            mode, confidence = "unknown", "low"
            uncertainty.append("Gray occurs mainly on narrow edges; antialiasing and intentional gray strokes cannot be distinguished from this preview alone.")
    if mode == "color" and measured["clear_chromatic_alpha_weight"] < COLOR_RULES["minimum_chromatic_alpha_weight"]:
        mode, confidence = "unknown", "low"
        uncertainty.append("Only weak near-neutral dark/midtone tint is present; intentional color versus rendering noise remains uncertain.")
    if (mode == "color" and measured["display_chromatic_alpha_weight"] < COLOR_RULES["minimum_chromatic_alpha_weight"]
            and raw["alpha_weight"] / max(1, raw["visible_pixel_count"]) >= .1):
        mode, confidence = "unknown", "low"
        uncertainty.append("Chromatic RGB exists only in barely visible alpha edges; an intentional colored accent is not established.")
    if not raw["visible_pixel_count"]:
        uncertainty.append("No visible pixels; fully transparent RGB is ignored.")
        confidence = "unknown"
    elif background["detected"] and not foreground["visible_pixel_count"]:
        mode, confidence = "unknown", "low"
        uncertainty.append("No distinguishable non-background region; an opaque white image may be a white object or empty background.")
    if measured["weak_chroma_alpha_weight"]:
        uncertainty.append("Very weak tint or compression/color-management noise exists near the grayscale boundary.")
        confidence = "low" if mode == "unknown" else "medium"
    if raw["alpha_weight"] and raw["alpha_weight"] / max(1, raw["visible_pixel_count"]) < 0.1:
        confidence = "low"
        uncertainty.append("Most visible pixels have very low alpha; appearance depends strongly on the display background.")
    if mode == "color" and measured["chromatic_alpha_weight"] < 4:
        if confidence == "high":
            confidence = "medium"
        uncertainty.append("Color is supported by very few alpha-weighted pixels; confirm a small accent versus raster noise visually.")
    if measured is foreground and _mode(raw) != mode:
        confidence = "medium" if confidence == "high" else confidence
        uncertainty.append("Color classification depends on the white-background hypothesis; raw and candidate-foreground results are both retained.")
    traits = []
    hues = measured["display_hue_alpha_weights"] or measured["hue_alpha_weights"]
    chromatic = sum(hues.values())
    if mode == "color":
        dominant_hue_fraction = max(hues.values(), default=0) / chromatic
        # Even a tiny true secondary hue prevents claiming the whole palette is
        # monochrome. Dominant hue and single hue are deliberately independent.
        significant_hues = sum(value >= COLOR_RULES["minimum_chromatic_alpha_weight"] for value in hues.values())
        if significant_hues == 1:
            traits.append("monochrome_hue")
        elif significant_hues <= 3:
            traits.append("limited_hues")
        else:
            traits.append("multicolor")
        if significant_hues > 1 and dominant_hue_fraction >= .85:
            traits.append("dominant_hue")
        ink_fraction = measured["chromatic_fraction_of_nonwhite_ink"]
        if ink_fraction is not None and ink_fraction <= 0.25:
            traits.append("accent")
        if (measured["chromatic_mean_luma_0_255"] or 0) >= 205:
            traits.append("light_palette")
    elif mode == "grayscale":
        traits.append("neutral_shading")
    elif mode == "black_white":
        traits.append("neutral_endpoints")
    return {"mode": mode, "palette_traits": traits, "confidence": confidence,
            "confidence_is_calibrated_probability": False,
            "review_status": "machine_measured_pending_visual_review", "classification_version": VERSION,
            "basis": ["actual_rgba_preview", "alpha_weighted_visible_pixels", basis, *extra_basis],
            "uncertainty": uncertainty, "background": background,
            "midgray_interior_pixel_count": midgray_interior,
            "raw_statistics": raw, "candidate_foreground_statistics": foreground,
            "thresholds": dict(COLOR_RULES)}


def _opaque_bw_paint_evidence(root):
    """Narrow paint evidence, not a CSS renderer or editability promise."""
    forbidden_tags = {"style", "image", "foreignobject", "lineargradient", "radialgradient",
                      "pattern", "filter", "mask", "use", "animate", "animatetransform", "animatecolor", "set"}
    allowed_paints = {"black", "white", "none", "#000", "#fff", "#000000", "#ffffff",
                      "rgb(0,0,0)", "rgb(255,255,255)", "rgb(0%,0%,0%)", "rgb(100%,100%,100%)"}
    paints = set()
    reason = None
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1].lower()
        if tag in forbidden_tags:
            reason = "Complex, inherited or raster paint construct: " + tag
            break
        attributes = {k.rsplit("}", 1)[-1].lower(): v for k, v in node.attrib.items()}
        if "class" in attributes or any(k.startswith("on") for k in attributes):
            reason = "Class or event-dependent presentation is not resolved"
            break
        properties = dict(attributes)
        for declaration in attributes.get("style", "").split(";"):
            if not declaration.strip():
                continue
            if ":" not in declaration or any(t in declaration for t in ("/*", "\\", "!", "var(")):
                reason = "Complex inline CSS is not resolved"
                break
            key, value = declaration.split(":", 1)
            properties[key.strip().lower()] = value.strip()
        if reason:
            break
        for key, value in properties.items():
            value = re.sub(r"\s+", "", value).lower()
            if key in {"fill", "stroke", "color", "stop-color", "flood-color"}:
                paints.add(value)
                if value not in allowed_paints:
                    reason = "A paint is not explicit opaque black/white/none"
            elif key in {"opacity", "fill-opacity", "stroke-opacity"} and value not in {"1", "1.0", "100%"}:
                reason = "Non-opaque or unresolved opacity"
            elif key in {"filter", "mask", "mix-blend-mode", "isolation", "color-profile"} and value not in {"none", "normal", "auto"}:
                reason = "Paint effect is not resolved"
        if reason:
            break
    return {"supported": reason is None, "basis": "restricted_svg_explicit_opaque_paints_and_default_black_fill",
            "literal_paints": sorted(paints), "reason": reason,
            "uniform_recolor_verified": False}


def _svg_hints(svg_bytes):
    empty = {"available": False, "basis": [], "uncertainty": [],
             "recolor_suitability": "unknown", "uniform_recolor_verified": False, "review_status": "not_tested"}
    if svg_bytes is None:
        return empty
    result = dict(empty, available=True, basis=["svg_structure_auxiliary_only"])
    try:
        root = SafeET.fromstring(svg_bytes, forbid_entities=True, forbid_external=True)
    except Exception as exc:
        return {**result, "available": False, "uncertainty": [f"Optional SVG structure parse unavailable: {type(exc).__name__}"]}
    counts, paints = Counter(), set()
    current_color = 0
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1].lower()
        counts[tag] += 1
        for attribute, value in node.attrib.items():
            local = attribute.rsplit("}", 1)[-1].lower()
            if local in {"fill", "stroke", "color", "stop-color", "flood-color"}:
                paints.add(value)
            if local in {"fill", "stroke", "color", "style"}:
                current_color += value.lower().count("currentcolor")
        if tag == "style":
            current_color += (node.text or "").lower().count("currentcolor")
    result.update(current_color_references=current_color, literal_paint_values=sorted(paints),
                  black_white_paint_evidence=_opaque_bw_paint_evidence(root),
                  gradient_count=counts["lineargradient"] + counts["radialgradient"], image_count=counts["image"],
                  stylesheet_count=counts["style"], filter_count=counts["filter"], mask_count=counts["mask"],
                  text_element_count=counts["text"],
                  capability_hint="current_color_present" if current_color else ("fixed_paints_observed" if paints else "unknown"),
                  uncertainty=["No full CSS cascade, paint-server or native-edit test was performed; monochrome pixels do not establish uniform recolorability."])
    if b"<?xml-stylesheet" in svg_bytes.lower():
        result["black_white_paint_evidence"].update(supported=False, reason="XML stylesheet processing instruction is not resolved")
    if counts["image"]:
        result["uncertainty"].append("SVG contains image elements; their raster colors are not editable vector paints.")
    return result


def _style_metadata(evidence, file_sha256, preview_sha256):
    accepted, rejected = [], []
    for index, supplied in enumerate(evidence or []):
        item = copy.deepcopy(supplied)
        reason = None
        if not isinstance(item.get("labels"), dict):
            reason = "Style evidence must contain explicit structured labels"
        elif item.get("file_sha256") != file_sha256:
            reason = "Evidence does not bind this exact original file"
        elif item.get("kind") == "official_source_file":
            if item.get("scope") != "file" or not str(item.get("source_url", "")).startswith(("https://", "http://")) or not item.get("statement"):
                reason = "Official style evidence must identify the specific file, source URL and label statement"
        elif item.get("kind") == "manual_visual_review":
            if not (preview_sha256 and item.get("preview_sha256") == preview_sha256 and item.get("reviewer")
                    and item.get("reviewed_at") and item.get("reference") and item.get("statement")):
                reason = "Visual style evidence must identify this exact preview and its reviewer/reference"
        else:
            reason = "Only official per-file evidence or an identified visual review confirms style"
        if reason:
            rejected.append({"index": index, "reason": reason})
        else:
            # Keep explicit public evidence, not unrelated upstream metadata.
            accepted.append({key: item.get(key) for key in (
                "kind", "scope", "file_sha256", "preview_sha256", "source_url", "statement", "reviewer", "reviewed_at", "reference", "labels")
                if item.get(key) is not None})

    def axis(name, valid, multi=False):
        claims = [(index, item["labels"][name]) for index, item in enumerate(accepted)
                  if isinstance(item.get("labels"), dict) and name in item["labels"]]
        values, references = set(), []
        for index, claim in claims:
            labels = claim if isinstance(claim, list) else [claim]
            known = {value for value in labels if isinstance(value, str) and value in valid}
            if known:
                values.update(known)
                references.append(index)
        conflict = len(values) > 1 and not multi
        result = {"confidence": "high" if values and not conflict else "unknown", "basis_evidence_indices": references,
                  "review_status": ("visual_reviewed" if any(accepted[i]["kind"] == "manual_visual_review" for i in references)
                                    else "official_file_label") if values and not conflict else "needs_review",
                  "uncertainty": ["Conflicting explicit labels; no automatic winner selected."] if conflict else [],
                  "classification_version": VERSION}
        if multi:
            result["values"] = sorted(values) or ["unknown"]
        else:
            result["value"] = next(iter(values)) if len(values) == 1 else "unknown"
        return result

    series, series_refs = [], []
    for index, item in enumerate(accepted):
        labels = item.get("labels") or {}
        claim = labels.get("series")
        if isinstance(claim, dict) and claim.get("id") and claim.get("label"):
            member = {key: claim[key] for key in ("id", "label", "author") if claim.get(key)}
            if member not in series:
                series.append(member)
            series_refs.append(index)
    return {"representation": axis("representation", REPRESENTATIONS, multi=True),
            "detail": axis("detail", DETAILS), "realism": axis("realism", REALISMS),
            "series": {"memberships": series, "confidence": "high" if series else "unknown",
                       "review_status": "evidence_recorded" if series else "needs_review",
                       "basis_evidence_indices": series_refs, "classification_version": VERSION},
            "accepted_evidence": accepted, "rejected_evidence": rejected}


def analyze_appearance(preview: bytes | Image.Image | None, *, file_sha256: str,
                       preview_sha256: str | None = None, svg_bytes: bytes | None = None,
                       evidence: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Return appearance metadata for one original file and its exact preview.

    Bytes bind the encoded preview SHA-256. PIL inputs are useful for synthetic
    tests and instead bind RGBA pixels plus dimensions; they cannot claim an
    encoded-preview hash or satisfy a hash-bound manual preview review.
    Style evidence labels are representation(list), detail, realism, series(dict).
    """
    if not _valid_sha(file_sha256):
        raise ValueError("An original file SHA-256 is required")
    encoded_digest, pixel_digest, image, error = None, None, None, None
    if isinstance(preview, bytes):
        encoded_digest = _sha(preview)
        if preview_sha256 is not None and preview_sha256 != encoded_digest:
            raise ValueError("Encoded preview SHA-256 mismatch")
        try:
            with Image.open(io.BytesIO(preview)) as source:
                if source.width * source.height > MAX_PREVIEW_PIXELS:
                    raise ValueError("Preview exceeds appearance pixel limit")
                source.load()
                image = source.convert("RGBA")
        except (OSError, ValueError, UnidentifiedImageError) as exc:
            error = f"Preview could not be measured: {type(exc).__name__}"
    elif isinstance(preview, Image.Image):
        if preview_sha256 is not None:
            raise ValueError("Provide encoded preview bytes to verify its SHA-256")
        if not preview.width or not preview.height or preview.width * preview.height > MAX_PREVIEW_PIXELS:
            raise ValueError("Preview exceeds appearance pixel limit")
        image = preview.convert("RGBA")
    elif preview is not None:
        raise TypeError("Preview must be encoded bytes, a PIL image, or None")
    if svg_bytes is not None and _sha(svg_bytes) != file_sha256:
        raise ValueError("SVG hints must come from the exact original file SHA-256")
    svg_hints = _svg_hints(svg_bytes)
    if image:
        pixel_digest = _sha(f"rgba-v1:{image.width}:{image.height}:".encode() + image.tobytes())
        color = _measure_color(image, svg_hints)
    else:
        color = {"mode": "unknown", "palette_traits": [], "confidence": "unknown",
                 "review_status": "not_measured", "classification_version": VERSION,
                 "basis": [], "uncertainty": [error or "No preview supplied"],
                 "raw_statistics": None, "candidate_foreground_statistics": None, "background": None}
    style = _style_metadata(evidence, file_sha256, encoded_digest)
    binding = {"file_sha256": file_sha256, "preview_sha256": encoded_digest,
               "rgba_pixels_sha256": pixel_digest, "rgba_hash_algorithm": "sha256(rgba-v1:width:height:+rgba_bytes)",
               "dimensions": {"width": image.width, "height": image.height} if image else None}
    result = {"schema_version": 1, "classification_version": VERSION, "binding": binding,
              "color": color, "style": style, "svg_coloring": svg_hints,
              "original_modified": False, "preview_modified": False,
              "limitations": ["Preview rendering, color management and resolution can affect measured colors.",
                              "Color, presentation, detail, realism, medical accuracy and native editability are separate dimensions."]}
    # Evidence changes invalidate style results even when original pixels do not.
    revision = evidence_revision(evidence)
    result["style_evidence_revision"] = revision
    result["cache_key"] = cache_key(file_sha256, encoded_digest or pixel_digest or _sha(b"missing-preview"),
        style_evidence_revision=revision, svg_supplied=svg_bytes is not None)
    return result
