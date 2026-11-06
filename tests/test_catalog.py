import json

from mga_resources.catalog import deduplicate, finalize, normalize_record, search


def record(title="Mouse liver", source="source-a", source_id="liver", digest="a" * 64, license="CC BY 4.0"):
    return {
        "source": source, "source_id": source_id, "title": title,
        "source_url": f"https://example.invalid/{source}/{source_id}",
        "author": "An Artist", "license": license, "license_url": None,
        "attribution": "An Artist, source, CC BY 4.0", "categories": [], "tags": [],
        "upstream_revision": "example-fixed-version",
        "files": [{"url": f"https://example.invalid/{source_id}.svg", "format": "svg", "sha256": digest,
                   "size_bytes": 100, "structure": "pure_vector", "validation": {"status": "machine_checked_and_rendered"},
                   "preview": {"path": digest + ".png"}}],
    }


def test_exact_duplicate_merges_with_all_provenance_and_formats():
    first = record()
    mirror = record(source="mirror", source_id="123")
    mirror["files"].append({"url": "https://example.invalid/mouse.png", "format": "png", "sha256": "b" * 64, "size_bytes": 200, "structure": "raster"})
    result, stats = deduplicate([first, mirror])
    assert stats["input_records"] == 2
    assert stats["logical_assets"] == stats["logical_families"] == 1
    assert stats["merged_records"] == 1
    assert len(result[0]["provenance"]) == 2
    assert len(result[0]["files"]) == 2
    assert set(result[0]["formats"]) == {"png", "svg"}
    assert result[0]["validation"]["status"] == "incomplete"  # The extra PNG has not been checked.
    assert len(next(file for file in result[0]["files"] if file["sha256"] == "a" * 64)["source_records"]) == 2
    repeated, _ = deduplicate(result)
    assert len(repeated[0]["provenance"]) == 2
    assert len(next(file for file in repeated[0]["files"] if file["sha256"] == "a" * 64)["source_records"]) == 2
    assert repeated[0]["sources"] == result[0]["sources"]
    assert repeated[0]["variants"] == result[0]["variants"]
    assert repeated[0]["alternate_ids"] == result[0]["alternate_ids"]


def test_matching_titles_do_not_merge_different_drawings():
    result, _ = deduplicate([record(digest="a" * 64), record(source_id="liver2", digest="b" * 64)])
    assert len(result) == 2


def test_same_source_identity_combines_separately_processed_formats():
    first, second = record(), record(digest="b" * 64)
    second["files"][0]["format"] = "png"
    result, _ = deduplicate([first, second])
    assert len(result) == 1
    assert len(result[0]["files"]) == 2


def test_unknown_license_and_attribution_are_not_approved():
    unknown = record(license=None)
    assert normalize_record(unknown)["distribution_status"] == "needs_rights_review"
    missing_author = record()
    missing_author["author"] = None
    missing_author["attribution"] = None
    assert normalize_record(missing_author)["distribution_status"] == "needs_attribution"
    blocked = record()
    blocked["redistribution"] = "hold"
    assert normalize_record(blocked)["distribution_status"] == "needs_rights_review"
    blocked = record()
    blocked["distribution_allowed"] = False
    assert normalize_record(blocked)["distribution_status"] == "needs_rights_review"
    blocked = record()
    blocked["rights_status"] = "pending"
    assert normalize_record(blocked)["distribution_status"] == "needs_rights_review"


def test_explicit_family_merges_variants_without_losing_files_or_credits():
    first = record("Mouse outline", source_id="mouse-svg")
    second = record("Mouse outline", source_id="mouse-png", digest="b" * 64)
    first["family_hint"] = second["family_hint"] = "mus-musculus-outline"
    second["files"][0]["format"] = "png"
    second["author"] = "Another Artist"
    second["attribution"] = "Another Artist, source, CC BY 4.0"
    result, stats = deduplicate([first, second])
    assert stats["logical_families"] == 1
    assert stats["variant_records"] == 2
    assert len(result[0]["files"]) == 2
    assert len(result[0]["variants"]) == 2
    assert {entry["author"] for entry in result[0]["provenance"]} == {"An Artist", "Another Artist"}
    repeated, repeat_stats = deduplicate(result)
    assert repeat_stats["variant_records"] == 2
    assert repeated[0]["variants"] == result[0]["variants"]
    assert repeated[0]["id"] == result[0]["id"]


def test_local_paths_removed_and_no_visual_acceptance_inferred():
    source = record()
    source["local_path"] = "C:\\private\\source.svg"
    source["files"][0].update(local_path="/home/runner/work/original.svg", storage_path="files/original.svg")
    source["files"][0]["preview"]["path"] = "C:\\private\\preview.png"
    result = normalize_record(source)
    serialized = json.dumps(result)
    assert "private" not in serialized
    assert "home/runner" not in serialized
    assert result["files"][0]["storage_path"] == "files/original.svg"
    assert result["validation"]["visual_review"] == "not_reviewed"
    assert result["validation"]["illustrator_editability"] == "not_tested"


def test_bilingual_search_preserves_species_and_handles_no_match(tmp_path):
    inputs = [
        record(), record("Rat liver", source_id="rat", digest="b" * 64),
        record("Macrophage", source_id="cell", digest="c" * 64),
        record("Human kidney", source_id="kidney", digest="d" * 64),
    ]
    summary = finalize(inputs, tmp_path)
    for query in ("小鼠肝脏", "mouse liver", "mice liver"):
        matches = search(tmp_path / "catalog.sqlite", query)
        assert [match["title"] for match in matches] == ["Mouse liver"]
    assert [x["title"] for x in search(tmp_path / "catalog.sqlite", "大鼠肝脏")] == ["Rat liver"]
    assert [x["title"] for x in search(tmp_path / "catalog.sqlite", "巨噬细胞")] == ["Macrophage"]
    assert search(tmp_path / "catalog.sqlite", "unobtainium organ") == []
    assert search(tmp_path / "catalog.sqlite", '" OR *') == []
    assert summary["acceptance"]["scientific_coverage"] == "not_accepted"
    assert summary["unique_original_files"] == 4
    assert summary["taxonomy"]["organs"]["assets"] == 3


def test_filters_apply_without_unrelated_fallback(tmp_path):
    finalize([record(), record("Macrophage", source_id="cell", digest="b" * 64)], tmp_path)
    assert len(search(tmp_path / "catalog.sqlite", "", filters={"category": "cells"})) == 1
    assert search(tmp_path / "catalog.sqlite", "liver", filters={"category": "cells"}) == []
    assert search(tmp_path / "catalog.sqlite", "liver", filters={"format": "png"}) == []


def test_full_queries_keep_objects_hard_and_rank_view_preferences(tmp_path):
    lateral = record("Mouse lateral view", source_id="mouse-side", digest="a" * 64)
    other_mouse = record("Mouse head", source_id="mouse-head", digest="b" * 64)
    rat = record("Rat lateral view", source_id="rat", digest="c" * 64)
    human_liver = record("Liver", source_id="human-liver", digest="d" * 64)
    human_liver["upstream_category"] = "Human_physiology"
    finalize([lateral, other_mouse, rat, human_liver], tmp_path)
    for query in ("实验小鼠侧视插画", "laboratory mouse side view illustration"):
        results = search(tmp_path / "catalog.sqlite", query)
        assert results[0]["title"] == "Mouse lateral view"
        assert {item["title"] for item in results} == {"Mouse lateral view", "Mouse head"}
    assert [item["title"] for item in search(tmp_path / "catalog.sqlite", "人体肝脏插画")] == ["Liver"]
    assert search(tmp_path / "catalog.sqlite", "mouse unobtainium illustration") == []


def test_actual_source_categories_classify_molecules_and_clinical_symbols():
    amino = record("alanine")
    amino["upstream_category"] = "Amino-Acids"
    assert "molecules" in normalize_record(amino)["taxonomy"]
    for title in ("Blood Type AB+", "Blood Bag"):
        assert "clinical_workflow" in normalize_record(record(title))["taxonomy"]
    assert "lab_equipment" in normalize_record(record("Blood Bag"))["taxonomy"]
    assert "people" in normalize_record(record("Arm"))["taxonomy"]
    assert "tissues_vessels" in normalize_record(record("Blood Drop"))["taxonomy"]
