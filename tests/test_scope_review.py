import copy
import importlib.util
import io
import json
from pathlib import Path
import tarfile

from PIL import Image
import pytest

spec = importlib.util.spec_from_file_location("scope_review", Path(__file__).parents[1] / "scripts/build_scope_review.py")
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)

URL = "https://example.test/archive.tar.gz"


def png(color="red", compression=6):
    stream = io.BytesIO()
    Image.new("RGBA", (32, 32), color).save(stream, "PNG", compress_level=compression)
    return stream.getvalue()


def file(key, payload, format="png", structure="raster"):
    return {"sha256": review.sha(key.encode()), "format": format, "structure": structure,
            "archive": {"url": URL, "member": "files/" + key + "." + format},
            "preview": {"sha256": review.sha(payload), "size_bytes": len(payload),
                        "archive": {"url": URL, "member": "previews/" + key + ".png"}}}


def record(key, files):
    return {"source": "togo", "source_id": key, "id": "asset-" + key, "title": "Test object " + key, "files": files}


def archive(files, payloads):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as tar:
        for row, payload in zip(files, payloads):
            info = tarfile.TarInfo(row["preview"]["archive"]["member"])
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
        info = tarfile.TarInfo("files/unselected-original.svg")
        info.size = 13
        tar.addfile(info, io.BytesIO(b"not extracted"))
    data = stream.getvalue()
    return data, {URL: {"size": len(data), "digest": "sha256:" + review.sha(data)}}


def test_scope_selection_covers_failed_svg_and_packaged_raster_with_siblings():
    payload = png()
    rows = [record("raster", [file("raster", payload)]),
            record("mixed", [file("mixed", payload, "svg", "mixed"), file("sibling", payload)]),
            record("embedded", [file("embedded", payload, "svg", "embedded_raster")]),
            record("pure", [file("pure", payload, "svg", "pure_vector"), file("raster-sibling", payload)])]
    before = copy.deepcopy(rows)
    selected = review.scope_candidates(rows)
    assert len(selected) == 4
    assert {row["source_id"] for row in selected.values()} == {"raster", "mixed", "embedded"}
    assert all(row["scope_verdict"] is None for row in selected.values())
    assert rows == before


@pytest.mark.parametrize("member", ["previews/../x.png", "previews\\x.png", "/previews/x.png", "previews/C:x.png"])
def test_unsafe_member_rejected(member):
    row = record("one", [file("one", png())])
    row["files"][0]["preview"]["archive"]["member"] = member
    with pytest.raises(ValueError, match="Unsafe"):
        review.scope_candidates([row])


def test_streams_archive_once_and_groups_only_identical_full_rgba_in_same_record():
    payloads = [png(compression=0), png(compression=9), png(), png("blue")]
    files = [file(str(i), payload) for i, payload in enumerate(payloads)]
    rows = [record("one", files[:2]), record("two", files[2:])]
    data, manifest = archive(files, payloads)
    calls = []
    def opener(url):
        calls.append(url)
        return io.BytesIO(data)
    candidates = review.scope_candidates(rows)
    images, pixels, reports, errors = review.fetch_previews(candidates, manifest, opener=opener)
    assert calls == [URL]
    assert len(images) == 4
    assert errors == {}
    assert reports[0]["verified"] is True
    assert len({row["preview"]["sha256"] for row in candidates.values()}) == 4
    assert len(set(pixels.values())) == 2
    tiles = review.group_exact_content(candidates, pixels)
    assert sorted(len(tile["file_ids"]) for tile in tiles) == [1, 1, 2]
    assert all(tile["scope_verdict"] is None for tile in tiles)


@pytest.mark.parametrize("bad", ["archive_digest", "archive_size", "member_digest", "member_size"])
def test_unverified_archive_discards_all_selected_images(bad):
    payloads = [png(), png("blue")]
    files = [file(str(i), payload) for i, payload in enumerate(payloads)]
    data, manifest = archive(files, payloads)
    candidates = review.scope_candidates([record("one", files)])
    if bad == "archive_digest":
        manifest[URL]["digest"] = "sha256:" + "0" * 64
    elif bad == "archive_size":
        manifest[URL]["size"] += 1
    elif bad == "member_digest":
        next(iter(candidates.values()))["preview"]["sha256"] = "0" * 64
    else:
        next(iter(candidates.values()))["preview"]["size_bytes"] += 1
    images, pixels, reports, errors = review.fetch_previews(candidates, manifest, opener=lambda _: io.BytesIO(data))
    assert images == pixels == {}
    assert len(errors) == 2
    assert reports[0]["verified"] is False


def test_missing_previews_never_share_an_unseen_tile():
    candidates = review.scope_candidates([record("one", [file("a", png()), file("b", png())])])
    assert len(review.group_exact_content(candidates, {})) == 2


def test_pack_is_bounded_unjudged_and_contains_no_originals(tmp_path):
    font = next((str(path) for path in [Path("C:/Windows/Fonts/msyh.ttc"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")] if path.exists()), None)
    assert font, "A test rendering font is required"
    payload = png()
    files = [file("one", payload)]
    data, manifest = archive(files, [payload])
    mapping = review.build_review([record("one", files)], manifest, tmp_path, font_path=font,
                                  opener=lambda _: io.BytesIO(data))
    assert set(path.name for path in tmp_path.iterdir()) == {"scope-01.png", "mapping.json", "judgments.template.json"}
    assert mapping["original_files_downloaded"] == mapping["source_websites_requested"] == 0
    assert mapping["scope_acceptance_passed"] is False
    judgments = json.loads((tmp_path / "judgments.template.json").read_text())
    assert judgments["reviewer"] is None
    assert judgments["items"][0]["scope_verdict"] is None
    assert sum(path.stat().st_size for path in tmp_path.iterdir()) < review.MAX_PACK_BYTES
