import base64
import hashlib
import io

import pytest
from PIL import Image

from mga_resources.media import MediaValidationError, inspect_media, process_file, _embedded_only_fetcher


def png_bytes(alpha=255):
    output = io.BytesIO()
    Image.new("RGBA", (8, 4), (200, 20, 40, alpha)).save(output, format="PNG")
    return output.getvalue()


def svg(contents, attrs='width="100%" height="100%" viewBox="0 0 100 50"'):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" {attrs}>{contents}</svg>').encode()


def test_standard_doctype_is_safe_and_percent_viewbox_resolves():
    data = b'<?xml version="1.0"?><!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">' + svg('<path d="M0 0 L100 50"/>')
    result = inspect_media(data)
    assert result["structure"] == "pure_vector"
    assert result["dimensions"]["width"] == 100
    assert result["dimensions"]["height"] == 50


def test_bitmap_wrapped_in_svg_is_not_classified_as_vector():
    embedded = base64.b64encode(png_bytes()).decode()
    content = f'<image href="data:image/png;base64,{embedded}" width="100" height="50"/>'
    result = inspect_media(svg(content))
    assert result["structure"] == "embedded_raster"
    assert result["embedded_images"][0]["dimensions"]["width"] == 8
    mixed = inspect_media(svg(content + '<path d="M0 0 L20 20"/>'))
    assert mixed["structure"] == "mixed"


@pytest.mark.parametrize("contents", [
    '<script>alert(1)</script>',
    '<image href="https://example.invalid/private.png"/>',
    '<use href="file:///etc/passwd"/>',
    '<rect width="10" onload="alert(1)"/>',
    '<style>@import "https://example.invalid/font.css";</style>',
    '<style>rect {fill:u\\72l(https://example.invalid/image.svg)}</style>',
    '<foreignObject><p>HTML</p></foreignObject>',
])
def test_active_or_external_svg_is_rejected(contents):
    with pytest.raises(MediaValidationError):
        inspect_media(svg(contents))


def test_entities_are_rejected_without_loading_external_data():
    data = b'<!DOCTYPE svg [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>' + svg('<text>&xxe;</text>')
    with pytest.raises(MediaValidationError):
        inspect_media(data)


def test_internal_gradient_and_license_links_are_allowed():
    data = svg('<metadata><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:cc="http://creativecommons.org/ns#"><cc:license rdf:resource="https://creativecommons.org/licenses/by/4.0/"/></rdf:RDF></metadata><defs><linearGradient id="g"><stop stop-color="red"/></linearGradient></defs><rect width="100" height="50" fill="url(#g)"/>')
    assert inspect_media(data)["structure"] == "pure_vector"


def test_legacy_cc_and_drawio_resource_are_nonloading_metadata():
    data = svg('<metadata><cc:license xmlns:cc="http://web.resource.org/cc/" xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" rdf:resource="http://creativecommons.org/licenses/publicdomain/"/></metadata><rect width="10" height="10"/>',
               'viewBox="0 0 20 20" resource="https://app.diagrams.net/#diagram"')
    assert inspect_media(data)["structure"] == "pure_vector"


def illustrator_svg(native_ref='<i:aipgfRef href="#adobe_illustrator_pgf"/>'):
    return ('''<!DOCTYPE svg [<!ENTITY ns_ai "http://ns.adobe.com/AdobeIllustrator/10.0/">]>
    <svg xmlns="http://www.w3.org/2000/svg" xmlns:i="&ns_ai;" viewBox="0 0 20 20">
    <switch><foreignObject requiredExtensions="&ns_ai;" width="1" height="1">'''
    + native_ref + '''</foreignObject><g><rect width="15" height="15" fill="red"/></g></switch></svg>''').encode()


@pytest.mark.parametrize("native_ref", ['<i:aipgfRef href="#adobe_illustrator_pgf"/>', '<i:pgfRef href="#native"/>', ''])
def test_illustrator_namespace_constants_and_native_data_fallback(tmp_path, monkeypatch, native_ref):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: pytest.fail("Renderer attempted network/file IO"))
    original = illustrator_svg(native_ref)
    path = tmp_path / "illustrator.svg"
    path.write_bytes(original)
    result = process_file(path, tmp_path / "previews", 64)
    assert result["validation"]["status"] == "machine_checked_and_rendered"
    assert result["illustrator_namespace_entity_count"] == 1
    assert result["foreign_object_fallbacks"] == [{"label": None, "kind": "illustrator_svg_fallback"}]
    assert result["structure"] == "pure_vector"
    assert path.read_bytes() == original
    assert result["sha256"] == hashlib.sha256(original).hexdigest()


@pytest.mark.parametrize("declaration", [
    '<!ENTITY ns_ai SYSTEM "file:///etc/passwd">',
    '<!ENTITY ns_ai "&recursive;">',
    '<!ENTITY % ns_ai "http://ns.adobe.com/AdobeIllustrator/10.0/">',
    '<!ENTITY ns_ai "https://example.invalid/namespace">',
    '<!ENTITY ns_ai "http://ns.adobe.com/AdobeIllustrator/10.0/"><!ENTITY ns_ai "http://ns.adobe.com/AdobeIllustrator/10.0/">',
])
def test_only_known_nonrecursive_illustrator_entities_are_allowed(declaration):
    original = illustrator_svg().replace(b'<!ENTITY ns_ai "http://ns.adobe.com/AdobeIllustrator/10.0/">', declaration.encode())
    with pytest.raises(MediaValidationError):
        inspect_media(original)


def test_illustrator_entity_expansion_is_bounded(monkeypatch):
    monkeypatch.setattr("mga_resources.media.MAX_SOURCE_BYTES", 550)
    with pytest.raises(MediaValidationError, match="expansion exceeds"):
        inspect_media(illustrator_svg().replace(b'<g>', b'<g><desc>' + b'&ns_ai;' * 12 + b'</desc>'))


@pytest.mark.parametrize("native_ref", ['<i:aipgfRef href="https://example.invalid/native"/>', '<i:aipgfRef href="#native"><text>visible</text></i:aipgfRef>', '<text>Important label</text>'])
def test_nonstandard_native_foreign_content_is_quarantined(native_ref):
    with pytest.raises(MediaValidationError):
        inspect_media(illustrator_svg(native_ref))


@pytest.mark.parametrize("href", ["", "/name/Animal_cell", "../locations/SL-0001", "https://www.uniprot.org/locations/SL-0001"])
def test_static_empty_and_relative_anchor_links_are_allowed(href):
    data = svg(f'<a href="{href}"><rect width="10" height="10"/></a>')
    assert inspect_media(data)["structure"] == "pure_vector"


@pytest.mark.parametrize("href", ["javascript:alert(1)", "data:text/html;base64,PGgxPkhlbGxvPC9oMT4=", "file:///etc/passwd"])
def test_active_anchor_schemes_stay_rejected(href):
    with pytest.raises(MediaValidationError):
        inspect_media(svg(f'<a href="{href}"><rect width="10" height="10"/></a>'))


def test_embedded_icc_profile_is_metadata_but_external_profile_is_rejected():
    profile = bytearray(128)
    profile[:4] = len(profile).to_bytes(4, "big")
    profile[36:40] = b"acsp"
    encoded = base64.b64encode(profile).decode()
    result = inspect_media(svg(f'<defs><color-profile name="sRGB" href="data:application/vnd.iccprofile;base64,{encoded}"/></defs><rect width="10" height="10"/>'))
    assert result["embedded_color_profile_count"] == 1
    assert result["structure"] == "pure_vector"
    for uri in ("https://example.invalid/profile.icc", "file:///private/profile.icc", "data:application/vnd.iccprofile;base64,YWJj"):
        with pytest.raises(MediaValidationError):
            inspect_media(svg(f'<color-profile href="{uri}"/>'))


def foreign_switch(label, fallback, child=None):
    child = child or f'<div xmlns="http://www.w3.org/1999/xhtml"><b>{label}</b></div>'
    return f'<switch><foreignObject width="50" height="20" requiredFeatures="http://www.w3.org/TR/SVG11/feature#Extensibility">{child}</foreignObject>{fallback}</switch>'


def test_matching_foreign_text_fallback_renders_without_changing_original(tmp_path, monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: pytest.fail("Renderer attempted network/file IO"))
    original = svg(foreign_switch("Intelligence", '<text x="2" y="20">Intelligence</text>'))
    path = tmp_path / "drawio.svg"
    path.write_bytes(original)
    result = process_file(path, tmp_path / "previews", 128)
    assert result["validation"]["status"] == "machine_checked_and_rendered"
    assert result["foreign_object_fallbacks"] == [{"label": "Intelligence", "kind": "matching_svg_text"}]
    assert path.read_bytes() == original
    assert result["transparency"]["fully_transparent"] is False


def test_exporter_embedded_image_fallback_is_retained_and_marked_mixed(tmp_path):
    encoded = base64.b64encode(png_bytes()).decode()
    fallback = f'<image x="0" y="0" width="100" height="50" href="data:image/png;base64,{encoded}"/>'
    original = svg(foreign_switch("metabolites", fallback) + '<path d="M0 0L10 10"/>')
    path = tmp_path / "raster-label.svg"
    path.write_bytes(original)
    result = process_file(path, tmp_path / "previews", 64)
    assert result["validation"]["status"] == "machine_checked_and_rendered"
    assert result["structure"] == "mixed"
    assert result["foreign_object_fallbacks"][0]["kind"] == "exporter_embedded_raster"
    assert path.read_bytes() == original


def test_light_dark_uses_authored_light_colors_and_keeps_nested_rgb(tmp_path):
    original = svg('<rect width="100" height="50" style="fill:light-dark(rgb(240, 20, 30), var(--dark, #000));"/>')
    path = tmp_path / "themes.svg"
    path.write_bytes(original)
    result = process_file(path, tmp_path / "previews", 64)
    assert result["validation"]["status"] == "machine_checked_and_rendered"
    assert result["preview_color_scheme"] == "light"
    with Image.open(tmp_path / "previews" / result["preview"]["path"]) as preview:
        assert preview.convert("RGB").getpixel((20, 10)) == (240, 20, 30)
    assert path.read_bytes() == original


@pytest.mark.parametrize("contents", [
    foreign_switch("full label", '<text x="0" y="10">full...</text>'),
    foreign_switch("label", '<image href="https://example.invalid/label.png"/>'),
    foreign_switch("label", '<text>label</text>', '<iframe xmlns="http://www.w3.org/1999/xhtml" src="https://example.invalid/"/>'),
    foreign_switch("label", '<text>label</text>', '<div xmlns="http://www.w3.org/1999/xhtml" onclick="alert(1)">label</div>'),
])
def test_foreign_object_without_faithful_static_fallback_is_quarantined(contents):
    with pytest.raises(MediaValidationError):
        inspect_media(svg(contents))


@pytest.mark.parametrize("url", ["https://example.invalid/x.png", "file:///etc/passwd", "data:image/svg+xml;base64,PHN2Zy8+", "data:text/css;base64,e30="])
def test_preview_fetcher_refuses_non_raster_resource(url):
    with pytest.raises(MediaValidationError):
        _embedded_only_fetcher(url, "image/*")


def test_raster_preview_preserves_original_and_inspects_actual_alpha(tmp_path):
    original = png_bytes(alpha=128)
    path = tmp_path / "mislabelled.svg"
    path.write_bytes(original)
    result = process_file(path, tmp_path / "previews", preview_size=64)
    assert path.read_bytes() == original
    assert result["sha256"] == hashlib.sha256(original).hexdigest()
    assert result["format"] == "png"
    assert result["structure"] == "raster"
    assert result["transparency"]["has_transparent_pixels"] is True
    assert result["transparency"]["has_fully_transparent_pixels"] is False
    assert result["preview"]["width"] == 8  # No misleading upscaling.
    assert (tmp_path / "previews" / result["preview"]["path"]).exists()
    assert result["validation"]["illustrator_editability"] == "not_tested"


def test_opaque_rgba_does_not_claim_transparent_background():
    result = inspect_media(png_bytes())
    assert result["transparency"]["has_transparent_pixels"] is False


def test_undecodable_image_is_rejected():
    with pytest.raises(MediaValidationError):
        inspect_media(b"not an image", "sample.png")


def test_pdf_is_kept_but_not_claimed_inspected(tmp_path):
    path = tmp_path / "native.ai"
    path.write_bytes(b"%PDF-1.4\nminimal fixture, not a real accepted PDF")
    result = process_file(path, tmp_path / "previews")
    assert result["format"] == "ai"
    assert result["validation"]["status"] == "unsupported"
    assert result["preview"] is None
