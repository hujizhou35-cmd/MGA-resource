import hashlib
from pathlib import Path

import pytest
from PIL import Image

from scripts.check_preview_composition import overlay, read_preview


def test_rejects_unbound_preview_before_composition(tmp_path: Path):
    path = tmp_path / 'preview.png'
    Image.new('RGBA', (2, 2), (255, 255, 255, 255)).save(path)
    with pytest.raises(ValueError, match='SHA-256 mismatch'):
        read_preview(path, '0' * 64)


def test_transparent_hidden_rgb_and_white_structure_survive_exact_input(tmp_path: Path):
    path = tmp_path / 'preview.png'
    image = Image.new('RGBA', (3, 1))
    image.putdata([(255, 0, 255, 0), (255, 255, 255, 255), (0, 0, 255, 128)])
    image.save(path)
    before = path.read_bytes()
    rgba, stats = read_preview(path, hashlib.sha256(before).hexdigest())
    assert stats['fully_transparent_pixels'] == 1
    assert stats['partial_alpha_pixels'] == 1
    assert stats['fully_opaque_pixels'] == 1
    composited, condition = overlay(rgba, (3, 1), 'dark', 3)
    assert composited.getpixel((0, 0)) == (24, 35, 51)
    assert composited.getpixel((1, 0)) == (255, 255, 255)
    assert condition['cropped'] is False
    assert path.read_bytes() == before
