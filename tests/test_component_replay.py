"""Tiny synthetic metadata tests, never component relevance or image acceptance."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tarfile

import pytest

from mga_resources.catalog import finalize, search_page

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("component_replay", ROOT / "scripts/replay_component_benchmark.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def h(value):
    return hashlib.sha256(value.encode()).hexdigest()


def benchmark():
    roles = [{"component_id": name, "role_label_zh": name, "role_label_en": name,
        "expected_concepts": ["mouse"], "required_species": "mouse", "visual_requirements": "Synthetic whole mouse requirement",
        "exclude_matches": ["computer mouse"]} for name in ("vector-role", "raster-role")]
    queries = [{"query_id": role["component_id"] + "-" + language, "task_id": "task", "component_id": role["component_id"],
        "language": language, "query": "mouse", "filters": {"media_type": "pure_vector" if role["component_id"] == "vector-role" else "raster"},
        "limit": 10, "mode": "explore", "seed": "diagnostic-fixed-project",
        "review_requirements": {key: copy.deepcopy(role[key]) for key in module.REVIEW_FIELDS}}
        for role in roles for language in ("zh", "en")]
    return {"schema_version": 1, "benchmark_version": "component-synthetic-only-v1", "status": "frozen",
        "frozen": True, "frozen_at": "2026-11-07T00:00:00Z",
        "tasks": [{"task_id": "task", "category": "animals", "legacy_concept_id": "old-synthetic-concept",
            "required_roles": roles, "composition_requirements": "Review both uses independently", "limitations": ["synthetic test only"]}],
        "queries": queries}


@pytest.fixture(scope="module")
def tiny(tmp_path_factory):
    folder = tmp_path_factory.mktemp("component-synthetic")
    records = []
    for name, fmt, structure in (("vector", "svg", "pure_vector"), ("raster", "png", "raster")):
        original = h(name)
        url = "https://github.com/example/library/releases/download/synthetic/synthetic-1.tar.gz"
        file = {"sha256": original, "size_bytes": 123, "format": fmt, "structure": structure,
            "url": "https://example.invalid/" + name + "." + fmt,
            "archive": {"url": url, "member": f"files/{original[:2]}/{original}.{fmt}"},
            "preview": {"sha256": h(name + "preview"), "archive": {"url": url, "member": f"previews/{original}.png"}},
            "validation": {"status": "machine_checked_and_rendered"},
            "text_content": {"status": "unknown"}}
        records.append({"source": "synthetic", "source_id": name, "title": "Mouse " + name,
            "species": "mouse", "author": "Synthetic artist", "license": "CC0-1.0", "files": [file]})
    finalize(records, folder)
    page = search_page(folder / "catalog.sqlite", "mouse")
    bindings = {"catalog_sha256": module.digest(folder / "catalog.jsonl"), "ranking_version": page["bindings"]["ranking_version"],
                "component_benchmark_sha256": h("synthetic"), "legacy_benchmark_sha256": h("legacy unchanged")}
    calls = []
    def record_call(database, **request):
        calls.append(copy.deepcopy(request))
        return search_page(database, **request)
    raw = module.execute_queries(folder / "catalog.sqlite", benchmark(), bindings, search_fn=record_call)
    return folder, bindings, raw, calls


def test_explicit_filters_and_request_only_reach_core_with_duplicate_text(tiny):
    _, _, raw, calls = tiny
    assert calls == [{key: q[key] for key in module.REQUEST_FIELDS} for q in benchmark()["queries"]]
    assert all(set(call) == set(module.REQUEST_FIELDS) for call in calls)
    assert len({q["query_id"] for q in raw["queries"]}) == 4
    assert [q["exact_results"][0]["selected_file"]["structure"] for q in raw["queries"]] == ["pure_vector", "pure_vector", "raster", "raster"]
    assert all(q["role_id"] == q["component_id"] and q["component_use_pass"] is None for q in raw["queries"])
    assert raw["composition_use_pass"] is None and raw["acceptance_passed"] is False


def test_catalog_binding_retains_exact_sources_hashes_archives_and_occurrences(tiny):
    folder, _, original, _ = tiny
    raw = copy.deepcopy(original)
    mapped = module.bind_exact_candidates(folder / "catalog.jsonl", raw)
    assert len(mapped["candidates"]) == 2
    for candidate in mapped["candidates"].values():
        assert candidate["candidate_id"] == candidate["asset_id"] + ":file-0"
        assert len(candidate["occurrences"]) == 2
        assert candidate["file_source_records"][0]["license"] == "CC0-1.0"
        assert candidate["file_source_records"][0]["author"] == "Synthetic artist"
        assert candidate["original_sha256"] in candidate["original_archive"]["member"]
        assert candidate["preview"]["sha256"]
        assert candidate["existing_observation_reuse"] == "not_assessed"
        assert candidate["component_use_pass"] is None
    assert all(q["exact_results"][0]["variant_id"] for q in raw["queries"])
    assert all(q["exact_results"][0]["candidate_id"] in mapped["candidates"] for q in raw["queries"])


@pytest.mark.parametrize("field", ["preview", "source", "license", "archive", "rank", "format", "family"])
def test_exact_binding_refuses_cross_file_source_preview_rank_or_format(tiny, field):
    folder, _, original, _ = tiny
    raw = copy.deepcopy(original)
    row = raw["queries"][0]["exact_results"][0]
    if field == "preview": row["preview"]["sha256"] = h("wrong")
    elif field == "source": row["selected_source"]["source_id"] = "wrong alias"
    elif field == "license": row["selected_file"]["source_records"][0]["license"] = "forged"
    elif field == "archive": row["selected_file"]["archive"]["member"] = "wrong.svg"
    elif field == "rank": row["rank"] = 2
    elif field == "format": row["selected_file"]["format"] = "ai"
    else: row["family_id"] = "wrong-family"
    with pytest.raises(ValueError):
        module.bind_exact_candidates(folder / "catalog.jsonl", raw)


@pytest.mark.parametrize("change", ["draft", "duplicate-query", "duplicate-role", "missing-language", "unknown-role", "review", "limit", "seed"])
def test_frozen_role_graph_rejects_ambiguous_or_mutated_requests(change):
    value = benchmark()
    if change == "draft": value["frozen"] = False
    elif change == "duplicate-query": value["queries"][1]["query_id"] = value["queries"][0]["query_id"]
    elif change == "duplicate-role": value["tasks"][0]["required_roles"].append(copy.deepcopy(value["tasks"][0]["required_roles"][0]))
    elif change == "missing-language": value["queries"].pop()
    elif change == "unknown-role": value["queries"][0]["component_id"] = "not-declared"
    elif change == "review": value["queries"][0]["review_requirements"]["required_species"] = "rat"
    elif change == "limit": value["queries"][0]["limit"] = 9
    else: value["queries"][0]["seed"] = "unfrozen-other-seed"
    with pytest.raises(ValueError):
        module.validate_benchmark(value)


def test_empty_results_are_retained_without_converting_them_to_visual_failure(tiny):
    folder, bindings, _, _ = tiny
    value = benchmark()
    for query in value["queries"]: query["query"] = "unobtainium"
    raw = module.execute_queries(folder / "catalog.sqlite", value, bindings)
    mapped = module.bind_exact_candidates(folder / "catalog.jsonl", raw)
    assert len(raw["queries"]) == 4 and not mapped["candidates"]
    assert all(q["exact_results"] == [] and q["component_use_pass"] is None for q in raw["queries"])


def test_expected_use_metadata_does_not_change_request_or_results(tiny):
    folder, bindings, previous, _ = tiny
    value = benchmark()
    for role in value["tasks"][0]["required_roles"]: role["expected_concepts"] = ["does-not-enter-search"]
    for query in value["queries"]: query["review_requirements"]["expected_concepts"] = ["does-not-enter-search"]
    raw = module.execute_queries(folder / "catalog.sqlite", value, bindings)
    assert [q["request_sha256"] for q in raw["queries"]] == [q["request_sha256"] for q in previous["queries"]]
    assert [[r["variant_id"] for r in q["exact_results"]] for q in raw["queries"]] == [[r["variant_id"] for r in q["exact_results"]] for q in previous["queries"]]


def test_explicit_input_hash_and_separate_benchmark_required_before_network(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "ROOT", tmp_path)
    path = tmp_path / "data/component-benchmark.json"
    path.parent.mkdir()
    path.write_text(json.dumps(benchmark()), encoding="utf8")
    assert module.frozen_input(path, module.digest(path))["frozen"] is True
    with pytest.raises(ValueError, match="frozen SHA"):
        module.frozen_input(path, h("wrong"))
    old = path.with_name("benchmark.json")
    old.write_bytes(path.read_bytes())
    with pytest.raises(ValueError, match="separate"):
        module.frozen_input(old, module.digest(old))


def test_cloud_entry_rejects_draft_before_downloading_any_catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "ROOT", tmp_path)
    value = benchmark(); value["frozen"] = False
    path = tmp_path / "data/component-benchmark.json"
    path.parent.mkdir(); path.write_text(json.dumps(value), encoding="utf8")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(module, "list_release_assets", lambda *args: pytest.fail("Draft must not read cloud metadata"))
    monkeypatch.setattr(module.sys, "argv", ["component", "--benchmark-sha256", module.digest(path), "--assembly-prefix", "assembly-1-1"])
    with pytest.raises(ValueError, match="explicitly frozen"):
        module.main()


def test_cloud_entry_packages_only_new_metadata_and_checks_publication(tiny, tmp_path, monkeypatch):
    """Mock cloud IO around the actual tiny index; never touch a real benchmark."""
    folder, bindings, _, _ = tiny
    monkeypatch.setattr(module, "ROOT", tmp_path)
    (tmp_path / "data").mkdir()
    benchmark_path = tmp_path / "data/component-benchmark.json"
    benchmark_path.write_text(json.dumps(benchmark()), encoding="utf8")
    (tmp_path / "data/benchmark.json").write_bytes(b'{"legacy_fixture":true}\n')
    (tmp_path / "mga_resources").mkdir()
    shutil.copyfile(ROOT / "mga_resources/search-core.mjs", tmp_path / "mga_resources/search-core.mjs")
    report = tmp_path / "synthetic.report.json"
    report.write_text(json.dumps({"run_id": "1-1", "summary": {"catalog_sha256": bindings["catalog_sha256"]}}))
    inputs = {"catalog.jsonl": folder / "catalog.jsonl", "catalog.sqlite": folder / "catalog.sqlite", "report.json": report}
    for key, value in {"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": "example/library",
                       "GITHUB_RUN_ID": "900", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_SHA": "a" * 40}.items():
        monkeypatch.setenv(key, value)
    downloaded, uploaded = [], {}
    names = {"assembly-1-1." + suffix: {"name": "assembly-1-1." + suffix} for suffix in inputs}
    def listing(repository, tag):
        assert repository == "example/library"
        return names if tag == "metadata" else uploaded
    def download(name, asset, destination, repository, tag):
        assert tag == "metadata" and name in names
        suffix = name.removeprefix("assembly-1-1.")
        downloaded.append(suffix)
        return inputs[suffix], {"name": name, "sha256": module.digest(inputs[suffix]), "kind": "metadata"}
    def upload(*args):
        assert args[:3] == ("release", "upload", "qa") and "--clobber" not in args
        for i, filename in enumerate(args[3:5], 1):
            path = Path(filename)
            uploaded[path.name] = {"id": i, "digest": "sha256:" + module.digest(path),
                "size": path.stat().st_size, "browser_download_url": "https://example.invalid/" + path.name}
    monkeypatch.setattr(module, "list_release_assets", listing)
    monkeypatch.setattr(module, "download_metadata", download)
    monkeypatch.setattr(module, "verify_producer", lambda *args: {"run_id": "1", "synthetic": True})
    monkeypatch.setattr(module, "gh", upload)
    monkeypatch.setattr(module.sys, "argv", ["component", "--benchmark-sha256", module.digest(benchmark_path),
        "--assembly-prefix", "assembly-1-1", "--release-tag", "metadata", "--publish-tag", "qa", "--publish"])
    module.main()
    base = tmp_path / "build/component-benchmark/900-1"
    summary = json.loads((base / "component-benchmark-900-1.summary.json").read_bytes())
    raw = json.loads((base / "evidence/raw-component-results.json").read_bytes())
    assert downloaded == ["catalog.jsonl", "catalog.sqlite", "report.json"]
    assert summary["child_queries"] == 4 and summary["task_language_units"] == 2
    assert summary["component_relevance_score"] is None and summary["new_visual_inspections"] == 0
    assert len(raw["queries"]) == 4 and len(uploaded) == 2
    assert raw["metadata_run"]["run_id"] == "900"
    assert json.loads((base / "evidence/candidates.json").read_bytes())["raw_results_sha256"] == module.digest(base / "evidence/raw-component-results.json")
    assert json.loads((base / "publication-receipt.json").read_bytes())["assets"][0]["github_digest_verified"] is True
    with tarfile.open(base / "component-benchmark-900-1.tar.gz") as archive:
        assert len(archive.getmembers()) == 4
        assert all(member.isfile() and member.name.endswith('.json') for member in archive.getmembers())
        assert all('catalog.sqlite' not in member.name for member in archive.getmembers())
