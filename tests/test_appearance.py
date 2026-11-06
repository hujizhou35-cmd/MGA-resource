"""Small constructed boundary fixtures; no real artwork or network is used."""
import hashlib
import io

from PIL import Image, ImageDraw
import pytest

from mga_resources.appearance import analyze_appearance, cache_key, evidence_revision, VERSION, ANALYSIS_VERSION

FILE_SHA = 'a' * 64


def encoded(image):
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()


def boundary_cases():
    images = {}
    image = Image.new('RGBA', (96, 96), (255, 0, 255, 0))
    ImageDraw.Draw(image).ellipse((24, 24, 72, 72), fill=(0, 0, 0, 255))
    images['transparent_rgb'] = image
    image = Image.new('RGBA', (96, 96), 'white')
    ImageDraw.Draw(image).rectangle((47, 47, 48, 48), fill=(0, 50, 240, 255))
    images['white_small_blue'] = image
    image = Image.new('RGBA', (96, 96), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((12, 12, 84, 84), fill=(250, 230, 240, 255))
    images['pale_pink'] = image
    image = Image.new('RGBA', (96, 96), 'white')
    ImageDraw.Draw(image).ellipse((20, 20, 76, 76), outline=(0, 70, 230, 255), width=2)
    images['single_blue_line'] = image
    image = Image.new('RGBA', (96, 96), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    for x in range(12, 84):
        level = round((x - 12) / 71 * 255)
        draw.line((x, 12, x, 83), fill=(level, level, level, 255))
    images['grayscale_gradient'] = image
    image = Image.new('RGBA', (96, 96), 'white')
    ImageDraw.Draw(image).rectangle((16, 16, 79, 79), fill='black')
    image.putpixel((48, 48), (230, 0, 0, 255))
    images['black_white_one_red_pixel'] = image
    image = Image.new('RGBA', (96, 96), 'white')
    ImageDraw.Draw(image).rectangle((20, 20, 75, 75), outline='black', width=3)
    images['enclosed_white_structure'] = image
    image = Image.new('RGBA', (96, 96), 'white')
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 75, 75), fill=(160, 160, 160, 255))
    draw.rectangle((21, 21, 74, 74), fill='black')
    images['gray_antialias_black_icon'] = image
    image = Image.new('RGBA', (96, 96), (50, 200, 90, 0))
    images['invisible'] = image
    image = Image.new('RGBA', (96, 96), 'white')
    images['ambiguous_white_only'] = image
    return images


@pytest.mark.parametrize('name,mode,trait', [
    ('transparent_rgb', 'black_white', 'neutral_endpoints'),
    ('white_small_blue', 'color', 'monochrome_hue'),
    ('pale_pink', 'color', 'light_palette'),
    ('single_blue_line', 'color', 'monochrome_hue'),
    ('grayscale_gradient', 'grayscale', 'neutral_shading'),
    ('black_white_one_red_pixel', 'color', 'accent'),
    ('enclosed_white_structure', 'black_white', 'neutral_endpoints'),
    ('gray_antialias_black_icon', 'black_white', 'neutral_endpoints'),
    ('invisible', 'unknown', None),
    ('ambiguous_white_only', 'unknown', None),
])
def test_boundary_color_cases(name, mode, trait):
    result = analyze_appearance(boundary_cases()[name], file_sha256=FILE_SHA)
    assert result['color']['mode'] == mode
    if trait:
        assert trait in result['color']['palette_traits']
    assert result['style']['representation']['values'] == ['unknown']
    assert result['style']['detail']['value'] == result['style']['realism']['value'] == 'unknown'


def test_transparent_rgb_is_ignored_but_low_alpha_is_measured_with_uncertainty():
    image = boundary_cases()['transparent_rgb']
    result = analyze_appearance(image, file_sha256=FILE_SHA)
    stats = result['color']['raw_statistics']
    assert stats['transparent_pixel_count'] > 0 and stats['chromatic_alpha_weight'] == 0
    assert len(stats['dominant_visible_rgb']) == 1 and stats['dominant_visible_rgb'][0]['rgb'] == [0, 0, 0]
    faint = Image.new('RGBA', (16, 16), (255, 0, 0, 1))
    result = analyze_appearance(faint, file_sha256=FILE_SHA)
    assert result['color']['mode'] == 'color' and result['color']['confidence'] == 'low'
    assert any('low alpha' in note for note in result['color']['uncertainty'])


def test_white_structure_and_original_pixels_are_preserved():
    image = boundary_cases()['enclosed_white_structure']
    original = image.tobytes()
    result = analyze_appearance(image, file_sha256=FILE_SHA)
    assert image.tobytes() == original and result['preview_modified'] is False
    color = result['color']
    assert color['background']['detected'] and color['background']['pixels_modified'] == 0
    assert color['raw_statistics']['white_neutral_alpha_weight'] > color['candidate_foreground_statistics']['white_neutral_alpha_weight'] > 0
    assert any('real white structures' in note for note in color['uncertainty'])


def test_blue_outline_is_not_black_white_accent_or_pale_due_to_its_white_interior():
    result = analyze_appearance(boundary_cases()['single_blue_line'], file_sha256=FILE_SHA)
    assert result['color']['mode'] == 'color'
    assert 'accent' not in result['color']['palette_traits']
    assert 'light_palette' not in result['color']['palette_traits']
    assert result['svg_coloring']['recolor_suitability'] == 'unknown'


def test_weak_tint_boundary_remains_unknown():
    image = Image.new('RGBA', (16, 16), (200, 204, 200, 255))
    result = analyze_appearance(image, file_sha256=FILE_SHA)
    assert result['color']['mode'] == 'unknown'
    assert result['color']['confidence'] == 'low' and result['color']['uncertainty']


def test_solid_gray_small_object_is_not_diluted_by_white_background():
    image = Image.new('RGBA', (96, 96), 'white')
    ImageDraw.Draw(image).rectangle((45, 45, 50, 50), fill=(150, 150, 150, 255))
    result = analyze_appearance(image, file_sha256=FILE_SHA)
    assert result['color']['mode'] == 'grayscale'
    assert result['color']['raw_statistics']['white_neutral_alpha_weight'] > 9000


def test_black_white_with_tiny_red_marker_reports_sparse_color():
    result = analyze_appearance(boundary_cases()['black_white_one_red_pixel'], file_sha256=FILE_SHA)
    assert result['color']['mode'] == 'color' and 'accent' in result['color']['palette_traits']
    assert result['color']['raw_statistics']['chromatic_fraction'] < 0.001
    assert any('very few' in note for note in result['color']['uncertainty'])


def official(labels, **changes):
    return {'kind': 'official_source_file', 'scope': 'file', 'file_sha256': FILE_SHA,
            'source_url': 'https://example.org/artist/series/file.svg',
            'statement': 'Official per-file style and series labels.', 'labels': labels, **changes}


def test_style_axes_and_series_require_specific_file_evidence():
    labels = {'representation': ['line_art', 'hand_drawn'], 'detail': 'detailed', 'realism': 'schematic',
              'series': {'id': 'artist:anatomy-series', 'label': 'Anatomy series', 'author': 'Example Artist'}}
    image = boundary_cases()['single_blue_line']
    result = analyze_appearance(image, file_sha256=FILE_SHA, evidence=[official(labels)])
    style = result['style']
    assert style['representation']['values'] == ['hand_drawn', 'line_art']
    assert style['detail']['value'] == 'detailed' and style['realism']['value'] == 'schematic'
    assert style['series']['memberships'][0]['author'] == 'Example Artist'
    assert style['detail']['review_status'] == 'official_file_label'
    assert result['color']['mode'] == 'color'
    for bad in [official(labels, scope='source'), official(labels, file_sha256='b' * 64),
                official(labels, kind='path_count_heuristic'), official(labels, source_url='')]:
        rejected = analyze_appearance(image, file_sha256=FILE_SHA, evidence=[bad])
        assert rejected['style']['detail']['value'] == 'unknown'
        assert rejected['style']['series']['memberships'] == []
        assert len(rejected['style']['rejected_evidence']) == 1


def test_visual_style_evidence_must_bind_exact_preview():
    raw = encoded(boundary_cases()['pale_pink'])
    digest = hashlib.sha256(raw).hexdigest()
    review = {'kind': 'manual_visual_review', 'file_sha256': FILE_SHA, 'preview_sha256': digest,
              'reviewer': 'Independent reviewer', 'reviewed_at': '2026-11-06', 'reference': 'review-record-1',
              'statement': 'Viewed the actual preview.', 'labels': {'detail': 'minimal', 'realism': 'schematic'}}
    result = analyze_appearance(raw, file_sha256=FILE_SHA, evidence=[review])
    assert result['style']['detail']['value'] == 'minimal'
    assert result['style']['detail']['review_status'] == 'visual_reviewed'
    review['preview_sha256'] = '0' * 64
    result = analyze_appearance(raw, file_sha256=FILE_SHA, evidence=[review])
    assert result['style']['detail']['value'] == 'unknown'


def test_conflicting_detail_evidence_remains_unknown_without_losing_basis():
    result = analyze_appearance(boundary_cases()['transparent_rgb'], file_sha256=FILE_SHA,
        evidence=[official({'detail': 'minimal'}), official({'detail': 'detailed'})])
    detail = result['style']['detail']
    assert detail['value'] == 'unknown' and len(detail['basis_evidence_indices']) == 2
    assert detail['uncertainty'] and detail['review_status'] == 'needs_review'


def test_svg_hints_do_not_certify_recolor_or_style():
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><style>.x{fill:currentColor}</style><defs><linearGradient id="g"/></defs><path class="x" d="M0 0h10v10z"/><image href="data:image/png;base64,AA=="/></svg>'
    result = analyze_appearance(boundary_cases()['single_blue_line'], file_sha256=hashlib.sha256(svg).hexdigest(), svg_bytes=svg)
    hints = result['svg_coloring']
    assert hints['current_color_references'] == 1 and hints['gradient_count'] == 1 and hints['image_count'] == 1
    assert hints['recolor_suitability'] == 'unknown' and hints['uniform_recolor_verified'] is False
    assert result['style']['representation']['values'] == ['unknown']
    assert result['style']['detail']['value'] == 'unknown'
    assert svg.endswith(b'</svg>')
    with pytest.raises(ValueError, match='exact original'):
        analyze_appearance(None, file_sha256=FILE_SHA, svg_bytes=svg)


def test_hash_binding_cache_and_missing_preview():
    raw = encoded(boundary_cases()['transparent_rgb'])
    digest = hashlib.sha256(raw).hexdigest()
    result = analyze_appearance(raw, file_sha256=FILE_SHA, preview_sha256=digest)
    assert result == analyze_appearance(raw, file_sha256=FILE_SHA, preview_sha256=digest)
    assert result['classification_version'] == VERSION and result['binding']['preview_sha256'] == digest
    assert ANALYSIS_VERSION == VERSION
    assert result['cache_key'] == cache_key(FILE_SHA, digest)
    changed = analyze_appearance(raw, file_sha256=FILE_SHA, evidence=[official({'detail': 'minimal'})])
    assert changed['cache_key'] != result['cache_key']
    assert changed['cache_key'] == cache_key(FILE_SHA, digest, style_evidence_revision=evidence_revision([official({'detail': 'minimal'})]))
    assert cache_key(FILE_SHA, digest, svg_supplied=True) != result['cache_key']
    with pytest.raises(ValueError, match='mismatch'):
        analyze_appearance(raw, file_sha256=FILE_SHA, preview_sha256='0' * 64)
    with pytest.raises(ValueError, match='encoded preview'):
        analyze_appearance(boundary_cases()['transparent_rgb'], file_sha256=FILE_SHA, preview_sha256=digest)
    unknown = analyze_appearance(None, file_sha256=FILE_SHA)
    assert unknown['color']['mode'] == 'unknown' and unknown['color']['review_status'] == 'not_measured'
    assert analyze_appearance(b'not an image', file_sha256=FILE_SHA)['color']['mode'] == 'unknown'


def test_multicolor_is_separate_from_minimal_or_realistic():
    image = Image.new('RGBA', (60, 20))
    draw = ImageDraw.Draw(image)
    colors = ['red', 'yellow', 'lime', 'cyan', 'blue', 'magenta']
    for index, color in enumerate(colors):
        draw.rectangle((index * 10, 0, index * 10 + 9, 19), fill=color)
    result = analyze_appearance(image, file_sha256=FILE_SHA)
    assert result['color']['mode'] == 'color' and 'multicolor' in result['color']['palette_traits']
    assert result['style']['detail']['value'] == result['style']['realism']['value'] == 'unknown'


def test_scanline_background_matches_independent_four_neighbor_reference():
    # Several enclosed white regions, diagonal contacts and border-connected
    # corridors exercise the optimized scanline fill against a simple oracle.
    image = Image.new('RGBA', (24, 24), 'white')
    draw = ImageDraw.Draw(image)
    draw.rectangle((3, 3, 10, 10), outline='black')
    draw.rectangle((13, 13, 20, 20), outline='blue')
    draw.line((15, 0, 15, 8), fill='black')
    image.putpixel((13, 13), (255, 255, 255, 255))
    pixels = image.load()
    pending = [(x, y) for y in range(24) for x in range(24) if x in (0, 23) or y in (0, 23)]
    visited = set()
    while pending:
        x, y = pending.pop()
        if not 0 <= x < 24 or not 0 <= y < 24 or (x, y) in visited or pixels[x, y] != (255, 255, 255, 255):
            continue
        visited.add((x, y))
        pending.extend([(x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)])
    result = analyze_appearance(image, file_sha256=FILE_SHA)
    assert result['color']['background']['candidate_pixel_count'] == len(visited)
