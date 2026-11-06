import base64
import hashlib
import io

import pytest
from PIL import Image

from mga_resources.media import MediaValidationError, inspect_media, process_file


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
