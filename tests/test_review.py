"""Cloud review regression tests use tiny synthetic PNG/tar bytes, no network."""
import hashlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

from PIL import Image
import pytest

from mga_resources.catalog import finalize


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_review", ROOT / "scripts/build_review.py")
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)
URL = "https://github.com/example/library/releases/download/test/batch.tar.gz"


def png(color):
    output = io.BytesIO()
    Image.new("RGBA", (12, 8), color).save(output, format="PNG")
    return output.getvalue()


def archive_bytes(members):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return output.getvalue()


def raw_record(number, title="Laboratory mouse side view", *, source="test", style=None, species="mouse"):
    data = png((number * 20, 100, 150, 255))
    return {"source": source, "source_id": str(number), "title": title, "license": "CC0-1.0",
            "author": "Fixture", "species": species, "style": style,
            "files": [{"sha256": hashlib.sha256(str(number).encode()).hexdigest(), "format": "svg",
                       "structure": "pure_vector", "validation": {"status": "machine_checked_and_rendered"},
                       "archive": {"url": URL, "member": f"files/{number}.svg"},
                       "preview": {"sha256": hashlib.sha256(data).hexdigest(),
                                   "archive": {"url": URL, "member": f"previews/{number}.png"}}}]}, data


def fixture_catalog(tmp_path):
    pairs = [raw_record(1), raw_record(2, "Laboratory rat", source="another", style="outline", species="rat")]
    folder = tmp_path / "catalog"
    finalize([record for record, _ in pairs], folder)
    records = [json.loads(line) for line in (folder / "catalog.jsonl").read_text(encoding="utf-8").splitlines()]
    packed = archive_bytes([("files/unselected.svg", b"Do not persist this original"),
                            *[(f"previews/{index + 1}.png", data) for index, (_, data) in enumerate(pairs)]])
    return records, folder / "catalog.sqlite", packed


def benchmark():
    concepts = [{"id": "animals-mouse", "label_en": "laboratory mouse", "label_zh": "实验小鼠",
                 "required_species": "mouse", "visual_requirements": "完整身体与尾巴"},
                {"id": "organs-spleen", "label_en": "spleen", "label_zh": "脾脏"}]
    return {"benchmark_version": "fixture", "concepts": concepts, "queries": [
        {"id": "mouse-zh", "concept_id": "animals-mouse", "language": "zh", "query": "实验小鼠侧视插画"},
        {"id": "mouse-en", "concept_id": "animals-mouse", "language": "en", "query": "laboratory mouse side view illustration"},
        {"id": "spleen-en", "concept_id": "organs-spleen", "language": "en", "query": "spleen"}]}


def test_query_union_keeps_both_ranks_and_real_empty_results(tmp_path):
    records, database, _ = fixture_catalog(tmp_path)
    plan = review.plan_review(records, database, benchmark(), sample_limit=0)
    assert len(plan["concepts"][0]["candidates"]) == 1
    assert plan["concepts"][0]["candidates"][0]["rankings"] == [
        {"query_id": "mouse-zh", "language": "zh", "rank": 1},
        {"query_id": "mouse-en", "language": "en", "rank": 1}]
    assert plan["concepts"][1]["candidates"] == []
    assert all(row["visual_judgment"] is None for row in plan["queries"])


def test_frozen_review_replays_exact_variants_and_rejects_drift(tmp_path):
    from scripts.build_frozen_review import frozen_search
    records, database, _ = fixture_catalog(tmp_path)
    plan = review.plan_review(records, database, benchmark(), sample_limit=0)
    replay = frozen_search(records, benchmark(), plan)
    reproduced = review.plan_review(records, None, benchmark(), sample_limit=0, search_fn=replay)
    assert reproduced["queries"] == plan["queries"]
    assert reproduced["candidates"] == plan["candidates"]
    for mutation in ("query", "rank", "preview", "original"):
        bad = copy.deepcopy(plan)
        first = next(iter(bad["candidates"].values()))
        if mutation == "query":
            bad["queries"][0]["query"] = "different query"
        elif mutation == "rank":
            bad["queries"][0]["candidates"][0]["rank"] = 2
        elif mutation == "preview":
            first["preview"]["sha256"] = "f" * 64
        else:
            first["original_sha256"] = "f" * 64
        with pytest.raises(ValueError, match="Frozen"):
            frozen_search(records, benchmark(), bad)


def test_review_uses_the_search_selected_variant_not_the_first_family_file(tmp_path):
    records, database, _ = fixture_catalog(tmp_path)
    item = records[0]
    second_file = copy.deepcopy(item["files"][0])
    second_file["sha256"] = "c" * 64
    second_file["preview"]["sha256"] = "d" * 64
    second_file["preview"]["archive"]["member"] = "previews/color.png"
    item["files"].append(second_file)
    selected = {**item, "selected_file": second_file}
    plan = review.plan_review(records, database, benchmark(), sample_limit=0,
                             search_fn=lambda *args, **kwargs: [selected])
    assert all(value["original_sha256"] == "c" * 64 for value in plan["candidates"].values())
    selected["selected_file"] = {"sha256": "e" * 64}
    with pytest.raises(ValueError, match="Search-selected file"):
        review.plan_review(records, database, benchmark(), sample_limit=0,
                           search_fn=lambda *args, **kwargs: [selected])


def test_stream_reads_each_archive_once_and_checks_hash(tmp_path):
    records, database, packed = fixture_catalog(tmp_path)
    plan = review.plan_review(records, database, benchmark(), sample_limit=20)
    calls = []
    def opener(url):
        calls.append(url)
        return io.BytesIO(packed)
    manifest = {URL: {"size": len(packed), "digest": "sha256:" + hashlib.sha256(packed).hexdigest()}}
    images, errors, transfers = review.fetch_previews(plan["candidates"], opener=opener, archive_manifest=manifest)
    assert calls == [URL]
    assert len(images) == 2 and not errors
    assert transfers[0]["compressed_bytes_read"] == len(packed)
    assert all(image.width <= 310 and image.height <= 235 for image in images.values())
    assert not list(tmp_path.rglob("*.svg"))


def test_bad_archive_hash_and_bad_preview_do_not_turn_green(tmp_path):
    records, database, packed = fixture_catalog(tmp_path)
    plan = review.plan_review(records, database, benchmark(), sample_limit=0)
    images, errors, transfers = review.fetch_previews(plan["candidates"], opener=lambda _: io.BytesIO(packed),
        archive_manifest={URL: {"size": len(packed), "digest": "sha256:" + "0" * 64}})
    assert not images and len(errors) == 1
    assert transfers[0]["status"] == "failed"
    wrong = archive_bytes([("previews/1.png", png("red"))])
    images, errors, _ = review.fetch_previews(plan["candidates"], opener=lambda _: io.BytesIO(wrong))
    assert not images and "SHA-256 mismatch" in next(iter(errors.values()))


def test_missing_and_unsafe_preview_members_are_explicit(tmp_path):
    records, database, _ = fixture_catalog(tmp_path)
    plan = review.plan_review(records, database, benchmark(), sample_limit=0)
    missing = archive_bytes([("files/original.svg", b"tiny fixture")])
    images, errors, _ = review.fetch_previews(plan["candidates"], opener=lambda _: io.BytesIO(missing))
    assert not images and "missing" in next(iter(errors.values()))
    for member in ("../escape.png", "/previews/test.png", "previews/../../escape.png", "files/original.svg"):
        record = json.loads(json.dumps(records[0]))
        record["files"][0]["preview"]["archive"]["member"] = member
        item = review.descriptors(record)[0]
        assert item["preview_key"] is None and item["preview_error"]


def test_sampling_is_reproducible_and_preserves_variants(tmp_path):
    records, _, _ = fixture_catalog(tmp_path)
    extra = dict(records[0]["files"][0], format="png", structure="raster", style="color")
    records[0]["files"].append(extra)
    selected, strata = review.stratified_sample(records, limit=3)
    again, same_strata = review.stratified_sample(list(reversed(records)), limit=3)
    assert selected == again and strata == same_strata
    assert len(selected) == 3
    assert {row["type"] for row in strata} == {"pure_vector", "raster"}
    assert all(row["selected_candidate_ids"] for row in strata)
    limited, incomplete = review.stratified_sample(records, limit=1)
    assert len(limited) == 1 and any(not row["selected_candidate_ids"] for row in incomplete)


def test_complete_pack_stays_blank_and_has_bounded_sheets(tmp_path):
    records, database, packed = fixture_catalog(tmp_path)
    # Linux test runners may lack the CJK font installed by review.yml. The pack
    # integration test can use DejaVu; cloud main requires the CJK default font.
    try:
        font = review._font_path()
    except ValueError:
        font = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    output = tmp_path / "review"
    result = review.build_review(records, database, benchmark(), output, font_path=font,
                                 opener=lambda _: io.BytesIO(packed), sample_limit=20)
    assert result["concept_sheets"] == 2 and result["sample_sheets"] == 1
    assert result["original_files_persisted"] == 0
    assert result["visually_judged_queries"] == 0 and result["acceptance_passed"] is False
    assert result["queries_without_candidates"] == ["spleen-en"]
    judgments = json.loads((output / "judgments.blank.json").read_text(encoding="utf-8"))
    assert judgments["reviewer"] is None and judgments["acceptance_passed"] is None
    assert all(row["pass"] is None and row["original_download_checked"] is None for row in judgments["queries"])
    mapping = json.loads((output / "mapping.json").read_text(encoding="utf-8"))
    assert mapping["queries"][0]["query"] == "实验小鼠侧视插画"
    assert mapping["concepts"][0]["sheet_metadata"]["tiles"][0]["rankings"][1]["language"] == "en"
    for path in output.rglob("*.png"):
        with Image.open(path) as image:
            assert image.width == 1650 and image.height <= 1590
    assert {path.suffix for path in output.rglob("*") if path.is_file()} == {".json", ".png"}
    with pytest.raises(ValueError, match="empty"):
        review.build_review(records, database, benchmark(), output, font_path=font)


def test_archive_allowlist_prevents_unlisted_requests(tmp_path):
    records, database, _ = fixture_catalog(tmp_path)
    plan = review.plan_review(records, database, benchmark(), sample_limit=0)
    with pytest.raises(ValueError, match="absent"):
        review.fetch_previews(plan["candidates"], archive_manifest={},
                              opener=lambda _: pytest.fail("Unlisted archive must not be fetched"))


def test_cli_refuses_local_bulk_review():
    environment = dict(os.environ)
    environment.pop("GITHUB_ACTIONS", None)
    result = subprocess.run([sys.executable, str(ROOT / "scripts/build_review.py"),
                             "--release-tag", "test", "--assembly-prefix", "assembly-1-1"],
                            capture_output=True, text=True, env=environment)
    assert result.returncode != 0
    assert "only on GitHub Actions" in result.stderr
