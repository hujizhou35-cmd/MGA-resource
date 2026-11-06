"""The cloud-only boundary and archive provenance are user requirements."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import os
import hashlib
import pytest
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("collect", ROOT / "scripts/collect.py")
collect = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collect)


def test_local_bulk_collection_refused_before_network():
    env = {key: value for key, value in os.environ.items() if key != "GITHUB_ACTIONS"}
    completed = subprocess.run([sys.executable, str(ROOT / "scripts/collect.py"), "--source", "bioicons"],
                               capture_output=True, text=True, env=env)
    assert completed.returncode == 2
    assert "cloud-only" in completed.stderr


def test_batch_preserves_bytes_credit_and_archive_relative_paths(tmp_path):
    original = tmp_path / "files" / "sample.svg"
    original.parent.mkdir()
    original.write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg"/>')
    records = [{"id": "test", "author": "Test artist", "license": "CC-BY-4.0",
                "files": [{"local_path": str(original), "storage_path": "files/sample.svg"}]}]
    receipt = collect.write_batch(tmp_path, "test", records, [], {original}, {"release_tag": "test"}, False)
    with tarfile.open(tmp_path / receipt["archive"]) as archive:
        assert archive.extractfile("files/sample.svg").read() == original.read_bytes()
        metadata = json.loads(archive.extractfile("records.jsonl").read().decode())
        assert metadata["author"] == "Test artist"
        assert metadata["license"] == "CC-BY-4.0"
        assert "local_path" not in metadata["files"][0]
        assert all(not name.startswith("/") for name in archive.getnames())


def test_upload_recognizes_existing_asset_after_first_page(tmp_path, monkeypatch):
    asset = tmp_path / "later.tar.gz"
    asset.write_bytes(b"already published bytes")
    digest = "sha256:" + hashlib.sha256(asset.read_bytes()).hexdigest()
    calls = []

    def fake_gh(arguments, **kwargs):
        calls.append(arguments)
        if "--paginate" in arguments:
            payload = [[{"name": "earlier.tar.gz"}],
                       [{"name": asset.name, "size": asset.stat().st_size, "digest": digest}]]
        else:
            payload = {"id": 123, "assets": [{"name": "earlier.tar.gz"}]}
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setattr(collect, "run_gh", fake_gh)
    collect.upload("release", [asset])
    assert len(calls) == 2
    assert "--paginate" in calls[1]


def test_empty_held_batch_has_uploadable_empty_jsonl(tmp_path):
    receipt = collect.write_batch(tmp_path, "held", [], [], set(), {"release_tag": "test"}, False)
    payload = (tmp_path / receipt["records_file"]).read_bytes()
    assert payload == b"\n"
    assert not [json.loads(line) for line in payload.splitlines() if line.strip()]
    assert hashlib.sha256(payload).hexdigest() == receipt["records_sha256"]


def selection_fixture():
    rows = [{"source_id": str(i), "upstream_revision": "abc"} for i in range(8)]
    plan = {"source": "test", "inventory_count": 8, "pinned_commit": "abc",
            "ordered_source_ids_sha256": hashlib.sha256("\n".join(str(i) for i in range(8)).encode()).hexdigest(),
            "recommended_now": {"ranges": [{"offset": 1, "limit": 2}, {"offset": 6, "limit": 2}]}}
    return rows, plan


def test_incremental_plan_skips_successes_and_resumes_by_source_index():
    rows, plan = selection_fixture()
    assert [i for i, _ in collect.select_source_rows(rows, "test", plan=plan)] == [1, 2, 6, 7]
    assert [i for i, _ in collect.select_source_rows(rows, "test", plan=plan, offset=3)] == [6, 7]
    assert [i for i, _ in collect.select_source_rows(rows, "test", plan=plan, limit=3)] == [1, 2, 6]
    assert [i for i, _ in collect.select_source_rows(rows, "test", offset=3, limit=2)] == [3, 4]


def test_non_git_source_plan_rejects_full_metadata_drift_even_with_same_ids():
    rows, plan = selection_fixture()
    plan["expected_discovery_sha256"] = "a" * 64
    with pytest.raises(ValueError, match="full metadata snapshot changed"):
        collect.select_source_rows(rows, "test", plan=plan, discovery_sha256="b" * 64)
    assert len(collect.select_source_rows(rows, "test", plan=plan, discovery_sha256="a" * 64)) == 4
    plan.pop("ordered_source_ids_sha256")
    assert len(collect.select_source_rows(rows, "test", plan=plan, discovery_sha256="a" * 64)) == 4
    plan.pop("expected_discovery_sha256")
    with pytest.raises(ValueError, match="requires an ordered-ID digest"):
        collect.select_source_rows(rows, "test", plan=plan)


def test_frozen_discovery_uses_only_exact_verified_release_metadata(tmp_path, monkeypatch):
    name = "test-42-1.discovery.jsonl"
    rows = [{"source": "test", "source_id": "one", "files": []}]
    raw = (json.dumps(rows[0]) + "\n").encode()
    digest = hashlib.sha256(raw).hexdigest()
    plan = {"frozen_discovery_asset": name, "expected_discovery_sha256": digest}
    asset = {"name": name, "size": len(raw), "digest": "sha256:" + digest,
             "browser_download_url": "https://github.com/example/repo/releases/download/test/" + name}
    calls = []
    def fake_gh(arguments, **kwargs):
        calls.append(arguments)
        if "--paginate" in arguments:
            value = [[asset]]
        elif arguments[:2] == ["release", "download"]:
            destination = Path(arguments[arguments.index("--dir") + 1])
            (destination / name).write_bytes(raw)
            value = {}
        else:
            value = {"id": 42}
        return SimpleNamespace(stdout=json.dumps(value), returncode=0)
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/repo")
    monkeypatch.setattr(collect, "run_gh", fake_gh)
    actual, origin = collect.load_frozen_discovery(plan, "test", "test", tmp_path)
    assert actual == rows and origin["sha256"] == digest
    assert len(calls) == 3
    asset["digest"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError, match="cloud digest"):
        collect.load_frozen_discovery(plan, "test", "test", tmp_path)
    assert len(calls) == 5  # A mismatch is rejected before downloading metadata.


@pytest.mark.parametrize("mutation", ["order", "source", "revision", "count", "overlap", "bounds", "zero"])
def test_incremental_plan_rejects_drift_and_ambiguous_ranges(mutation):
    rows, plan = selection_fixture()
    if mutation == "order":
        rows.reverse()
    elif mutation == "source":
        plan["source"] = "other"
    elif mutation == "revision":
        rows[0]["upstream_revision"] = "new"
    elif mutation == "count":
        plan["inventory_count"] = 9
    elif mutation == "overlap":
        plan["recommended_now"]["ranges"][1]["offset"] = 2
    elif mutation == "bounds":
        plan["recommended_now"]["ranges"][1]["limit"] = 3
    else:
        plan["recommended_now"]["ranges"][0]["limit"] = 0
    with pytest.raises(ValueError):
        collect.select_source_rows(rows, "test", plan=plan)


def test_sparse_collection_flushes_gaps_without_claiming_missing_indices(tmp_path, monkeypatch):
    from mga_resources import sources, catalog
    rows, plan = selection_fixture()
    for row in rows:
        row.update(source="test", scope_status="excluded", exclusion_reason="Not artwork")
    (tmp_path / "data/collection-plans").mkdir(parents=True)
    (tmp_path / "data/sources.json").write_text(json.dumps([{"source_id": "test", "enabled": True}]))
    (tmp_path / "data/collection-plans/test.json").write_text(json.dumps(plan))
    monkeypatch.setattr(collect, "ROOT", tmp_path)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setattr(sys, "argv", ["collect.py", "--source", "test", "--limit", "0",
                                     "--selection-plan", "data/collection-plans/test.json"])
    monkeypatch.setattr(sources, "discover", lambda *a: rows)
    def empty_finalize(records, output):
        output.mkdir(parents=True)
        return {"logical_assets": 0}
    monkeypatch.setattr(catalog, "finalize", empty_finalize)
    assert collect.main() == 0
    report = json.loads((tmp_path / "build/test/42-1/test-42-1.report.json").read_text())
    assert report["processed"] == 4
    assert report["source_inventory_processed"] is False
    assert [(r["range_start"], r["range_end"]) for r in report["batches"]] == [(1, 3), (6, 8)]
    assert [[d["index"] for d in r["dispositions"]] for r in report["batches"]] == [[1, 2], [6, 7]]
    assert [[d["source_index"] for d in r["excluded_records"]] for r in report["batches"]] == [[1, 2], [6, 7]]
    assert report["selection_plan"]["ordered_source_ids_sha256"] == plan["ordered_source_ids_sha256"]
