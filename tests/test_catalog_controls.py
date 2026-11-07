"""Test control evidence integrity; synthetic results are not visual acceptance."""
import copy
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("catalog_controls", ROOT / "scripts/replay_catalog_controls.py")
controls = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controls)


def pair():
    file = {"sha256": "original", "archive": {"url": "old", "member": "original.svg"},
            "preview": {"sha256": "preview", "archive": {"url": "old", "member": "preview.png"}},
            "public_scope": {"public_allowed": True}}
    item = {"original_sha256": "original", "preview_sha256": "preview",
            "original_archive": copy.deepcopy(file["archive"]), "preview_archive": copy.deepcopy(file["preview"]["archive"])}
    return item, file


@pytest.mark.parametrize("tamper", ["original", "preview", "archive", "preview_archive", "scope"])
def test_existing_choice_cannot_be_reused_for_changed_file_or_scope(tamper):
    item, file = pair()
    controls.exact_pair(item, file)
    if tamper == "original":
        file["sha256"] = "new"
    elif tamper == "preview":
        file["preview"]["sha256"] = "new"
    elif tamper == "archive":
        file["archive"]["member"] = "elsewhere.svg"
    elif tamper == "preview_archive":
        file["preview"]["archive"]["member"] = "elsewhere.png"
    else:
        file["public_scope"]["public_allowed"] = False
    with pytest.raises(ValueError):
        controls.exact_pair(item, file)


def test_original_negative_filters_and_empty_results_are_recorded_without_promotion(monkeypatch):
    requests = []
    benchmark = controls.read(controls.BENCHMARK)
    def search(db, **request):
        requests.append(copy.deepcopy(request))
        return {"bindings": {"catalog_version": "current"}, "results": []}
    monkeypatch.setattr(controls, "search_page", search)
    result = controls.negative_replay("unused", benchmark, {"catalog_sha256": "current"})
    assert len(requests) == 6
    for source, request in zip(benchmark["negative_scenarios"], requests):
        assert request == {"query": source["query"], "filters": source.get("filters", {}),
                           "limit": 10, "mode": "explore", "seed": "default"}
    assert result["visual_acceptance_passed"] is False
    assert all("pass" not in row and row["raw_result"]["results"] == [] for row in result["scenarios"])


def test_negative_runtime_cannot_claim_a_different_catalog(monkeypatch):
    monkeypatch.setattr(controls, "search_page", lambda *args, **kw: {"bindings": {"catalog_version": "stale"}, "results": []})
    with pytest.raises(ValueError, match="catalog differs"):
        controls.negative_replay("unused", controls.read(controls.BENCHMARK), {"catalog_sha256": "current"})


def appearance_case():
    item, file = pair()
    item.update(title="Unknown-color fixture", source="fixture", catalog_analysis_version="appearance-1.1.0",
                measured_color_mode="unknown", measured_palette_traits=[])
    file["appearance"] = {"classification_version": "appearance-1.1.0",
        "binding": {"file_sha256": "original", "preview_sha256": "preview"},
        "color": {"mode": "unknown", "palette_traits": []}}
    document = {"reused_observations": {"two_cross_source_style_evidence_checks": []}}
    return item, file, document


@pytest.mark.parametrize("failure", [None, "unknown_promoted", "wrong_preview"])
def test_appearance_checks_reject_known_color_for_unknown_and_cross_file_preview(monkeypatch, failure):
    item, file, document = appearance_case()
    def search(db, query, filters, seed):
        hit = "color_mode" not in filters or failure == "unknown_promoted"
        row = {"family_id": "fixture", "selected_file": file, "preview": copy.deepcopy(file["preview"]), "download": file["archive"]}
        if failure == "wrong_preview":
            row["preview"]["sha256"] = "other"
        return {"bindings": {"catalog_version": "current"}, "results": [row] if hit else [], "diagnostics": {}}
    monkeypatch.setattr(controls, "search_page", search)
    args = ("unused", document, {"original": file}, {"original": item},
            {"catalog_sha256": "current", "analysis_version": "appearance-1.1.0"})
    if failure:
        with pytest.raises(ValueError):
            controls.appearance_replay(*args)
    else:
        result = controls.appearance_replay(*args)
        calls = result["files"][0]["search_checks"]
        assert [len(x["returned"]) for x in calls] == [1, 0, 0, 0]
        assert result["new_visual_judgments"] == result["originals_downloaded"] == 0


def test_history_replay_uses_only_prior_choices_and_does_not_mutate_ledger(monkeypatch, tmp_path):
    item, file = pair()
    item["family_id"] = "family"
    ledger = {"catalog_sha256": "old", "ranking_version": "old-version", "projects": [
        {"case_id": kind, "project_id": f"{kind}-{i}", "seed": f"{kind}-{i}",
         "decision": {"selected_files": [copy.deepcopy(item)]}}
        for kind in ("rich", "scarce") for i in range(5)]}
    original = copy.deepcopy(ledger)
    def search(db, query, limit, seed, mode="explore", history=None):
        history = history or {}
        if mode == "explore" and "adopted" in history:
            kind, current = seed.split("-")
            assert [x["project_id"] for x in history["adopted"]] == [f"{kind}-{i}" for i in range(int(current))]
        score = 1 if mode == "exact" else 1 - 0.01 * len(history.get("adopted", []))
        return {"bindings": {"catalog_version": "current"}, "diagnostics": {"qualified_families": 1},
            "results": [{"family_id": "family", "selected_file": copy.deepcopy(file), "search_score": score,
                         "selected_source": {}, "selected_science": {"object": "fixture"}, "pinned": False}]}
    monkeypatch.setattr(controls, "search_page", search)
    output = tmp_path / "history"
    result = controls.history_replay("unused", ledger, output, {"catalog_sha256": "current"})
    assert ledger == original
    assert sum(x["projects"] for x in result["scenarios"]) == 10
    for kind in ("rich", "scarce"):
        raw = controls.read(output / (kind + ".json"))
        assert all(x["new_adoption_recorded_from_replay"] is False for x in raw["projects"])
        assert raw["exact_mode_control"]["identical"] is True
