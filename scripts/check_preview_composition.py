"""Bounded display QA of already-local exact previews; never fetches artwork.

The contact sheets are technical test canvases, not publication figures or an
automatic visual/style acceptance. Input previews and original artwork are not
modified. Only uniformly resized display copies are alpha-composited.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

VERSION = 'preview-composition-qa-1.0.0'
BACKGROUNDS = {'white': '#ffffff', 'dark': '#182333', 'grid_light': '#eef1f5', 'grid_dark': '#b7c2d0'}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def font(size: int):
    for name in ['C:/Windows/Fonts/arial.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']:
        if Path(name).is_file():
            return ImageFont.truetype(name, size)
    return ImageFont.load_default(size=size)


def read_preview(path: Path, expected_sha256: str) -> tuple[Image.Image, dict]:
    if digest(path) != expected_sha256:
        raise ValueError('Preview SHA-256 mismatch; refusing unrelated or modified display bytes')
    with Image.open(path) as source:
        source.load()
        rgba = source.convert('RGBA')
        source_mode = source.mode
    alpha = rgba.getchannel('A')
    counts = alpha.histogram()
    return rgba, {
        'width': rgba.width, 'height': rgba.height, 'decoded_mode': source_mode,
        'rgba_conversion_for_display_only': source_mode != 'RGBA',
        'pixels': rgba.width * rgba.height,
        'fully_transparent_pixels': counts[0],
        'partial_alpha_pixels': sum(counts[1:255]),
        'fully_opaque_pixels': counts[255],
        'nonzero_alpha_bbox': list(alpha.getbbox()) if alpha.getbbox() else None,
        'source_preview_bytes_unchanged': True,
    }


def background(size: tuple[int, int], kind: str) -> Image.Image:
    if kind != 'grid':
        return Image.new('RGBA', size, BACKGROUNDS[kind])
    result = Image.new('RGBA', size, BACKGROUNDS['grid_light'])
    draw = ImageDraw.Draw(result)
    for y in range(0, size[1], 16):
        for x in range(0, size[0], 16):
            if (x // 16 + y // 16) % 2:
                draw.rectangle((x, y, x + 15, y + 15), fill=BACKGROUNDS['grid_dark'])
    # A known line through the canvas helps distinguish transparency from white paint.
    draw.line((0, size[1] // 2, size[0], size[1] // 2), fill='#c36b30', width=2)
    return result


def overlay(rgba: Image.Image, size: tuple[int, int], kind: str, max_extent: int) -> tuple[Image.Image, dict]:
    result = background(size, kind)
    scale = min(max_extent / rgba.width, max_extent / rgba.height, 1.0)
    scaled_size = (max(1, round(rgba.width * scale)), max(1, round(rgba.height * scale)))
    shown = rgba.resize(scaled_size, Image.Resampling.LANCZOS) if scaled_size != rgba.size else rgba
    xy = ((size[0] - shown.width) // 2, (size[1] - shown.height) // 2)
    result.alpha_composite(shown, xy)
    return result.convert('RGB'), {'extent': max_extent, 'rendered_size': list(scaled_size), 'offset': list(xy), 'uniform_scale': scale, 'cropped': False, 'background': kind}


def build(curated_path: Path, inventory_path: Path, output: Path, max_items: int = 12) -> dict:
    if not 1 <= max_items <= 12:
        raise ValueError('This local QA tool is bounded to 1–12 existing previews')
    curated = json.loads(curated_path.read_text(encoding='utf-8'))
    inventory = json.loads(inventory_path.read_text(encoding='utf-8'))['exact_local_previews']
    entries = [c for c in curated['curated'] if c['original_sha256'] in inventory][:max_items]
    output.mkdir(parents=True, exist_ok=True)
    images = []
    items = []
    for index, entry in enumerate(entries, 1):
        path = Path(inventory[entry['original_sha256']][0]['path'])
        rgba, stats = read_preview(path, entry['preview_sha256'])
        images.append(rgba)
        items.append({
            'tile': index, 'candidate_id': entry['candidate_id'], 'title': entry['title'],
            'categories': entry['categories'], 'original_sha256': entry['original_sha256'],
            'original_archive': entry['original_archive'], 'preview_sha256': entry['preview_sha256'],
            'preview_archive': entry['preview_archive'], 'input_preview': path.as_posix(),
            'alpha_statistics': stats, 'display_conditions': [], 'visual_judgment': 'pending_actual_inspection',
        })
    sheets = []
    # Three independent backgrounds at 256px; small views preserve actual 96/48px extents.
    for page, start in enumerate(range(0, len(items), 3), 1):
        canvas = Image.new('RGB', (1300, 1080), '#e6eaf0')
        draw = ImageDraw.Draw(canvas)
        draw.text((18, 12), 'Technical preview QA | exact bytes | no crop, cleanup, recolor or native-edit claim', fill='#172334', font=font(20))
        for x, title in [(20, 'White / 256px'), (300, 'Dark / 256px'), (580, 'Grid + line / 256px'), (860, 'White / 96px + 48px')]:
            draw.text((x, 48), title, fill='#172334', font=font(18))
        for row, offset in enumerate(range(start, min(start + 3, len(items)))):
            item, rgba = items[offset], images[offset]
            y = 92 + row * 326
            draw.text((20, y), f"{item['tile']:02d}. {item['title'][:68]} | {item['original_sha256'][:16]}", fill='#172334', font=font(18))
            for x, kind in [(20, 'white'), (300, 'dark'), (580, 'grid')]:
                tile, condition = overlay(rgba, (256, 256), kind, 256)
                canvas.paste(tile, (x, y + 28))
                item['display_conditions'].append(dict(condition, sheet=f'comparison-{page:02d}.png', bounds_xywh=[x, y + 28, 256, 256]))
            for x, extent in [(860, 96), (1016, 48)]:
                tile, condition = overlay(rgba, (132, 256), 'white', extent)
                canvas.paste(tile, (x, y + 28))
                item['display_conditions'].append(dict(condition, sheet=f'comparison-{page:02d}.png', bounds_xywh=[x, y + 28, 132, 256]))
            draw.text((860, y + 292), 'Visible spacing retained; no content crop', fill='#172334', font=font(16))
        name = f'comparison-{page:02d}.png'
        canvas.save(output / name)
        sheets.append({'path': name, 'sha256': digest(output / name), 'width': 1300, 'height': 1080, 'purpose': 'Background and reduction comparison'})
    # A repeatable adjacency test has no arrows or implied scientific causal sequence.
    canvas = Image.new('RGB', (1440, 930), '#e6eaf0')
    draw = ImageDraw.Draw(canvas)
    draw.text((20, 12), 'Technical adjacency test | same box sizes | no scientific sequence or style-coherence pass', fill='#172334', font=font(20))
    for base_x, kind in [(20, 'white'), (740, 'dark')]:
        canvas.paste(background((680, 860), kind).convert('RGB'), (base_x, 55))
        draw.text((base_x + 10, 65), kind + ' | display extent 208px; 12px cell separation', fill=('#172334' if kind == 'white' else '#ffffff'), font=font(17))
        for pos, rgba in enumerate(images):
            col, row = pos % 3, pos // 3
            x, y = base_x + 10 + col * 220, 102 + row * 270
            if row >= 3:  # Additional 10–12 entries still get individual sheets; board stays bounded.
                break
            tile, condition = overlay(rgba, (208, 230), kind, 208)
            canvas.paste(tile, (x, y))
            label = f"{pos+1:02d}. {items[pos]['title'][:24]}"
            draw.text((x, y + 234), label, fill=('#172334' if kind == 'white' else '#ffffff'), font=font(14))
            items[pos]['display_conditions'].append(dict(condition, sheet='adjacency.png', bounds_xywh=[x, y, 208, 230]))
    canvas.save(output / 'adjacency.png')
    sheets.append({'path': 'adjacency.png', 'sha256': digest(output / 'adjacency.png'), 'width': 1440, 'height': 930, 'purpose': 'Independent object adjacency; no semantic arrows or sequence'})
    report = {
        'schema_version': 1, 'tool_version': VERSION, 'curated_sha256': digest(curated_path),
        'inventory_sha256': digest(inventory_path), 'curated_items': len(curated['curated']),
        'exact_local_previews_reviewed_by_machine': len(items), 'adjacency_items': min(9, len(items)), 'items': items, 'sheets': sheets,
        'input_fetches': 0, 'source_originals_modified': False, 'preview_bytes_modified': False,
        'backgrounds': BACKGROUNDS, 'resize': 'Pillow LANCZOS, uniform fit, maximum extent, never upscale or crop',
        'automatic_visual_acceptance': False, 'native_editability_tested': False,
        'limits': ['Machine rendering is not actual visual acceptance.', 'Only exact available local previews are included; contact-sheet crops cannot establish original transparency.', 'The adjacency board includes at most the first nine previews; additional items receive individual comparison sheets only.', 'No actual manuscript composition, style coherence, scientific layout or label cleanup is certified.'],
    }
    (output / 'render-manifest.json').write_bytes((json.dumps(report, ensure_ascii=False, indent=2) + '\n').encode())
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--curated', type=Path, required=True)
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--max-items', type=int, default=12)
    args = parser.parse_args()
    report = build(args.curated, args.inventory, args.output, args.max_items)
    print(json.dumps({'items': len(report['items']), 'sheets': len(report['sheets']), 'output': str(args.output)}))


if __name__ == '__main__':
    main()
