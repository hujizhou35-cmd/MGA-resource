import copy
import json

from mga_resources.catalog import _search_core, deduplicate, finalize, normalize_record, search, search_legacy


def test_empty_catalog_remains_uploadable_and_searchable(tmp_path):
    summary = finalize([], tmp_path)
    assert (tmp_path / "catalog.jsonl").read_bytes() == b"\n"
    assert summary["logical_assets"] == 0
    assert search(tmp_path / "catalog.sqlite", "mouse") == []


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


def test_full_queries_enforce_views_and_preserve_legacy_comparison(tmp_path):
    lateral = record("Mouse lateral view", source_id="mouse-side", digest="a" * 64)
    other_mouse = record("Mouse head", source_id="mouse-head", digest="b" * 64)
    rat = record("Rat lateral view", source_id="rat", digest="c" * 64)
    human_liver = record("Liver", source_id="human-liver", digest="d" * 64)
    human_liver["upstream_category"] = "Human_physiology"
    finalize([lateral, other_mouse, rat, human_liver], tmp_path)
    for query in ("实验小鼠侧视插画", "laboratory mouse side view illustration"):
        results = search(tmp_path / "catalog.sqlite", query)
        assert results[0]["title"] == "Mouse lateral view"
        assert {item["title"] for item in results} == {"Mouse lateral view"}
        assert {item["title"] for item in search_legacy(tmp_path / "catalog.sqlite", query)} == {"Mouse lateral view", "Mouse head"}
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


def reviewed_reactome(title="SLC25A15", source_id="R-ICO-014806", label="Transporter"):
    # Actual source labels from the frozen 42283380295 catalog; the file and
    # review binding below are synthetic mechanics fixtures, not visual proof.
    item = record(title, source="reactome", source_id=source_id)
    item["tags"] = [label]
    item["files"][0]["public_scope"] = {
        "status": "included", "public_allowed": True,
        "evidence_original_sha256": item["files"][0]["sha256"],
        "decision_id": "fixture-exact-file-review", "reviewed_by": "Fixture reviewer",
        "evidence": [{"uri": "https://example.invalid/review", "sha256": "c" * 64}],
    }
    return item


def test_narrow_official_topics_preserve_source_labels_and_scope_bindings():
    items = [reviewed_reactome(), reviewed_reactome("Diglyceride", "R-ICO-012621", "Compound")]
    bird = record("bird silhouette", source="scidraw", source_id="c2bc4521-30c7-4ee9-a0bf-8ee75eca7b4e")
    bird.update(upstream_category="bird", tags=["bird"])
    items.append(bird)
    for item, topic in zip(items, ["molecules", "molecules", "animals"]):
        original = copy.deepcopy(item)
        normalized = normalize_record(item)
        assert item == original
        assert normalized["taxonomy"] == [topic]
        assert normalized["tags"] == original["tags"]
        assert normalized.get("upstream_category") == original.get("upstream_category")
        facet = normalized["source_subject_facets"][0]
        assert facet["taxonomy"] == topic and facet["scientific_assertion"] is False
        assert facet["source_id"] == item["source_id"]
        assert facet["source_url"] == item["source_url"]
        assert facet["upstream_revision"] == item["upstream_revision"]
        if item["source"] == "reactome":
            assert facet["scope_review_bindings"][0]["file_sha256"] == item["files"][0]["sha256"]
        assert any(value.startswith("source_subject:") for value in normalized["taxonomy_evidence"][topic])


def test_topic_mapping_does_not_trust_other_sources_or_unreviewed_reactome():
    for mutation in ("other_source", "unreviewed", "wrong_hash", "blocked"):
        item = reviewed_reactome()
        if mutation == "other_source":
            item["source"] = "unrelated-source"
        elif mutation == "unreviewed":
            item["files"][0].pop("public_scope")
        elif mutation == "wrong_hash":
            item["files"][0]["public_scope"]["evidence_original_sha256"] = "b" * 64
        else:
            item["files"][0]["public_scope"].update(status="pending_review", public_allowed=False)
        assert normalize_record(item)["taxonomy"] == []
    generic_tag = record("Silhouette", source="scidraw")
    generic_tag["tags"] = ["bird"]  # Only the official category_name field qualifies.
    assert normalize_record(generic_tag)["taxonomy"] == []


def test_actual_fried_egg_and_conflicting_togo_tags_do_not_become_animals():
    egg = record("Fried Egg", source="nih-bioart", source_id="625")
    egg.update(upstream_category=["Bioart Category", "Image Style", "Image Type", "Animals"],
               tags=["Animals", "Birds", "Food", "Shaded", "Vector", "chicken", "egg", "fried"])
    beetle = record("Harmonia axyridis (polymorphic form)", source="togo", source_id="2111")
    beetle.update(tags=["Organisms", "Mammals", "Vertebrates", "Comparative anatomy", "Insecta", "Coleoptera", "Harmonia axyridis"],
                  scientific_name="Harmonia axyridis", taxonomy_id="115357")
    adrenal = record("Adrenal gland", source="scidraw")
    adrenal.update(upstream_category="human", tags=["human"])
    for item in (egg, beetle, adrenal):
        normalized = normalize_record(item)
        assert "animals" not in normalized["taxonomy"]
        assert "people" not in normalized["taxonomy"]
        assert "source_subject_facets" not in normalized
        for key in ("species", "object", "view", "state", "role", "scientific"):
            assert normalized.get(key) == item.get(key)


def test_topic_mapping_keeps_scientific_features_identical(monkeypatch):
    from mga_resources import catalog
    bird = record("bird silhouette", source="scidraw", digest="b" * 64)
    bird.update(upstream_category="bird", tags=["bird"])
    inputs = [reviewed_reactome(), bird]
    mapped, _ = deduplicate(inputs)
    with monkeypatch.context() as isolated:
        isolated.setattr(catalog, "_narrow_source_topics", lambda item: [])
        baseline, _ = deduplicate(inputs)
    before = _search_core({"action": "prepare", "records": baseline})["variants"]
    after = _search_core({"action": "prepare", "records": mapped})["variants"]
    for old, new in zip(before, after):
        assert old["variant_id"] == new["variant_id"]
        assert old["values"]["category"] == [] and new["values"]["category"]
        for key in ("facts", "fact_basis", "primary_object", "search", "lexical", "file"):
            assert old[key] == new[key]
        for key in ("object", "species", "view", "state", "role", "depiction_kind"):
            assert old["values"][key] == new["values"][key]


def test_source_topics_do_not_spread_across_duplicate_origins():
    bird = record("Silhouette", source="scidraw", source_id="bird")
    bird.update(upstream_category="bird", tags=["bird"])
    mirror = record("Silhouette", source="mirror", source_id="generic-copy")
    merged, _ = deduplicate([bird, mirror])
    assert len(merged) == 1
    for origin in merged[0]["files"][0]["source_records"]:
        assert origin["taxonomy"] == (["animals"] if origin["source"] == "scidraw" else [])
        assert bool(origin.get("source_subject_facets")) == (origin["source"] == "scidraw")
    rows = _search_core({"action": "prepare", "records": merged})["variants"]
    assert {row["source"]: row["values"]["category"] for row in rows} == {"scidraw": ["animals"], "mirror": []}
    again, _ = deduplicate(merged)
    assert again[0]["variants"] == merged[0]["variants"]
    assert again[0]["files"][0]["source_records"] == merged[0]["files"][0]["source_records"]
