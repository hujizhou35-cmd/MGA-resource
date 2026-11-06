import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile

from PIL import Image, ImageDraw
import pytest

from mga_resources.appearance import analyze_appearance

spec = importlib.util.spec_from_file_location("appearance_review", Path(__file__).parents[1] / "scripts/build_appearance_review.py")
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)


def png(color=(0, 0, 0, 0)):
    image = Image.new("RGBA", (64, 64), color)
    ImageDraw.Draw(image).rectangle((25, 25, 35, 35), fill=(0, 50, 220, 255))
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def record(key="a", source="one", payload=None):
    payload = payload or png()
    original = hashlib.sha256(key.encode()).hexdigest()
    preview_sha = hashlib.sha256(payload).hexdigest()
    return {"id": key, "source": source, "title": "Syringe", "files": [{"sha256": original, "format": "png",
        "preview": {"sha256": preview_sha, "size_bytes": len(payload),
                    "archive": {"url": "https://example.test/archive.tar.gz", "member": "previews/" + key + ".png"}},
        "appearance": analyze_appearance(payload, file_sha256=original, preview_sha256=preview_sha)}]}


def archive(rows, payloads):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as tar:
        for row, payload in zip(rows, payloads):
            member = tarfile.TarInfo(row["files"][0]["preview"]["archive"]["member"])
            member.size = len(payload)
            tar.addfile(member, io.BytesIO(payload))
        original = tarfile.TarInfo("files/not-requested.svg")
        original.size = 8
        tar.addfile(original, io.BytesIO(b"original"))
    data = stream.getvalue()
    url = "https://example.test/archive.tar.gz"
    return data, {url: {"size": len(data), "digest": "sha256:" + review.sha(data)}}


def test_selection_is_deterministic_bounded_and_unjudged():
    records = [record(str(i), "source" + str(i % 3)) for i in range(12)]
    first = review.select_candidates(records)
    second = review.select_candidates(list(reversed(records)))
    assert first == second
    assert len(first["candidates"]) <= 48
    assert all(case["passed"] is None for case in first["cases"])
    assert first["selection_is_visual_acceptance"] is False
    cross = next(c for c in first["cases"] if c["id"] == "cross_source_style")
    assert {first["candidates"][c["candidate_id"]]["source"] for c in cross["candidates"]} == {"source0", "source1", "source2"}
    with pytest.raises(ValueError):
        review.select_candidates(records, per_case=7)


def test_binding_and_path_mismatch_are_not_candidates():
    bad_hash, bad_preview, unsafe = record("a"), record("b"), record("c")
    bad_hash["files"][0]["sha256"] = "0" * 64
    bad_preview["files"][0]["preview"]["sha256"] = "1" * 64
    unsafe["files"][0]["preview"]["archive"]["member"] = "previews/../secret.png"
    assert review.descriptors([bad_hash, bad_preview, unsafe]) == []


def test_one_archive_read_only_selected_previews():
    payloads = [png(), png((255, 255, 255, 255))]
    rows = [record("a", payload=payloads[0]), record("b", payload=payloads[1])]
    data, manifest = archive(rows, payloads)
    plan = review.select_candidates(rows)
    calls = []
    def opener(url):
        calls.append(url)
        return io.BytesIO(data)
    selected, reports, errors = review.fetch_selected(plan, manifest, opener=opener)
    assert len(calls) == 1
    assert set(selected.values()) == set(payloads)
    assert reports[0]["verified"] is True
    assert errors == {}


def test_complete_archive_digest_failure_discards_all_previews():
    row, payload = record(), png()
    data, manifest = archive([row], [payload])
    next(iter(manifest.values()))["digest"] = "sha256:" + "0" * 64
    selected, reports, errors = review.fetch_selected(review.select_candidates([row]), manifest, opener=lambda _: io.BytesIO(data))
    assert selected == {}
    assert reports[0]["verified"] is False
    assert len(errors) == 1


def test_member_digest_failure_discards_archive():
    row = record()
    data, manifest = archive([row], [png((255, 255, 255, 255))])
    selected, reports, errors = review.fetch_selected(review.select_candidates([row]), manifest, opener=lambda _: io.BytesIO(data))
    assert selected == {} and errors
    assert reports[0]["verified"] is False


def test_transparent_rgb_diagnostics_leave_preview_untouched():
    payload = png((255, 0, 255, 0))
    before = review.sha(payload)
    result = review.pixel_diagnostics(payload)
    assert result["transparent_nonzero_rgb_pixels"] == 4096 - 121
    assert result["transparent_chromatic_rgb_pixels"] == 4096 - 121
    assert result["image_unchanged"]
    assert review.sha(payload) == before


def test_pack_preserves_raw_preview_bytes_and_blank_judgments(tmp_path):
    font = next((p for p in ("C:/Windows/Fonts/msyh.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf") if Path(p).exists()), None)
    if font is None:
        pytest.skip("No test font")
    payload, row = png(), record()
    data, manifest = archive([row], [payload])
    plan = review.select_candidates([row], per_case=1)
    result = review.build_pack(plan, manifest, tmp_path, font_path=font, opener=lambda _: io.BytesIO(data))
    assert result["original_files_downloaded"] == 0
    assert len(list((tmp_path / "previews").glob("*.png"))) == 1
    assert next((tmp_path / "previews").glob("*.png")).read_bytes() == payload
    judgments = json.loads((tmp_path / "judgments.template.json").read_text(encoding="utf-8"))
    assert len(judgments["appearance_cases"]) == 8
    assert all(c["passed"] is None and not c["real_files"] for c in judgments["appearance_cases"])
