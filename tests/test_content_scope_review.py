import copy
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("content_scope_review", Path(__file__).parents[1] / "scripts/build_content_scope_review.py")
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)


def record(key, title, source="example", files=1):
    return {"id": source + "-" + key, "source": source, "source_id": key, "title": title, "files": [
        {"sha256": review.sha((key + str(i)).encode()), "format": "svg", "structure": "pure_vector",
         "archive": {"url": "https://example.test/archive.tar.gz", "member": "files/" + key + str(i) + ".svg"},
         "preview": {"sha256": review.sha(("preview" + key + str(i)).encode()),
                     "archive": {"url": "https://example.test/archive.tar.gz", "member": "previews/" + key + str(i) + ".png"}}}
        for i in range(files)]}


def test_table_object_and_results_are_nominated_but_never_automatically_excluded():
    rows = [record("desk", "Laboratory table"), record("figure", "Clinical trial results"), record("lung", "Lung")]
    before = copy.deepcopy(rows)
    selected, report = review.select_candidates(rows, controls_per_source=0)
    assert len(selected) == 2
    assert all(row["scope_verdict"] is None and "disposition" not in row for row in selected.values())
    assert all(row["contains_text"] == row["potential_cleanup"] == "unknown" for row in selected.values())
    assert report["selection_is_a_scope_verdict"] is False
    assert rows == before


def test_alias_title_and_all_file_variants_are_preserved():
    row = record("diagram", "Unknown", files=2)
    row["files"][1]["source_records"] = [{"title": "Study design diagram", "source_id": "alias", "local_path": "private"}]
    selected, _ = review.select_candidates([row], controls_per_source=0)
    assert len(selected) == 2
    assert {f["structure"] for f in selected.values()} == {"pure_vector"}
    assert {f["format"] for f in selected.values()} == {"svg"}
    assert any(f["source_records"] == [{"title": "Study design diagram", "source_id": "alias"}] for f in selected.values())


def test_deterministic_range_has_explicit_remaining_and_no_duplicate_candidates():
    rows = [record(str(i), "Figure " + str(i)) for i in range(6)]
    first, a = review.select_candidates(rows, limit=3, controls_per_source=0)
    second, b = review.select_candidates(reversed(rows), offset=3, limit=3, controls_per_source=0)
    assert len(first) == len(second) == 3 and not set(first) & set(second)
    assert a["ordered_candidate_ids_sha256"] == b["ordered_candidate_ids_sha256"]
    assert a["remaining_after_range"] == 3 and b["remaining_after_range"] == 0


def test_unmatched_controls_are_distinguished_from_keyword_evidence():
    selected, report = review.select_candidates([record("a", "Lung"), record("b", "Kidney")], controls_per_source=1)
    assert len(selected) == 1 and report["metadata_nomination_records"] == 0
    assert next(iter(selected.values()))["nomination"]["kind"] == "deterministic_unmatched_control"


def test_reactome_category_representatives_include_plain_molecular_names():
    rows = [record(str(i), "MOL" + str(i), source="reactome") for i in range(6)]
    for i, row in enumerate(rows):
        row["tags"] = ["Receptor" if i < 3 else "Drug"]
    selected, report = review.select_candidates(rows, controls_per_source=0)
    assert len(selected) == 4
    assert report["reactome_category_representatives"] == 4
    assert all(row["scope_verdict"] is None for row in selected.values())


def test_full_reactome_mode_covers_every_variant_not_only_keyword_matches():
    rows = [record("a", "AKT", "reactome", files=2), record("b", "Insulin", "reactome"), record("c", "A chart", "other")]
    selected, report = review.select_candidates(rows, source_filter="reactome", full_source=True)
    assert len(selected) == 3 and report["full_source_review"] is True
    assert {v["source"] for v in selected.values()} == {"reactome"}
    with pytest.raises(ValueError, match="explicit source"):
        review.select_candidates(rows, full_source=True)


def test_missing_preview_stays_explicitly_pending():
    row = record("a", "Table", files=2)
    row["files"][1]["preview"] = None
    selected, report = review.select_candidates([row], controls_per_source=0)
    assert len(selected) == 1
    assert report["unreviewable_files"] == [{"asset_id": "example-a", "original_sha256": row["files"][1]["sha256"], "status": "pending_no_verified_preview"}]


@pytest.mark.parametrize("kwargs", [{"limit": 1001}, {"offset": -1}, {"controls_per_source": 6}])
def test_explicit_limits(kwargs):
    with pytest.raises(ValueError):
        review.select_candidates([], **kwargs)
