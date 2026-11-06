"""Checkpoint correctness is tested with metadata only, never artwork downloads."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

from mga_resources.catalog import finalize


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("assemble", ROOT / "scripts/assemble.py")
assemble = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assemble)
REGISTRY = [{"source_id": "example", "primary": True}]


def checkpoint(folder, *, run="1", snapshot="a", day="01", start=0, end=2,
               discovered=2, statuses=None, unchecked=(), exclusion_reason="AI brush preset, outside illustration/icon scope"):
    statuses = statuses or {}
    prefix = f"example-{run}-b{start:04d}"
    archive = f"{prefix}.tar.gz"
    archive_url = f"https://github.com/test/library/releases/download/test/{archive}"
    records, failures, dispositions, held, excluded = [], [], [], [], []
    for index in range(start, end):
        status = statuses.get(index, "acquired")
        dispositions.append({"index": index, "source_id": str(index), "status": status})
        if status == "acquired":
            records.append({"source": "example", "source_id": str(index), "source_index": index,
                            "title": f"Test organ {index}", "license": "CC0-1.0", "author": "Test",
                            "files": [{"sha256": hashlib.sha256(f"{snapshot}-{index}".encode()).hexdigest(),
                                       "format": "svg", "size_bytes": 100, "structure": "pure_vector",
                                       "storage_path": f"files/{index}.svg",
                                       "archive": {"url": archive_url, "member": f"files/{index}.svg"},
                                       "preview": {"path": f"previews/{index}.png",
                                                   "archive": {"url": archive_url, "member": f"previews/{index}.png"}},
                                       "validation": {"status": "preview_failed" if index in unchecked else "machine_checked_and_rendered"}}]})
        elif status == "failed":
            failures.append({"source_index": index, "source_id": str(index), "error": "Synthetic failed download"})
        elif status == "held":
            held.append({"source_index": index, "source_id": str(index), "reason": "Unknown license"})
        elif status == "excluded":
            dispositions[-1]["reason"] = exclusion_reason
            excluded.append({"source_index": index, "source_id": str(index), "reason": exclusion_reason})
    record_file = folder / f"{prefix}.records.jsonl"
    record_file.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    failure_file = folder / f"{prefix}.failures.json"
    failure_file.write_text(json.dumps(failures), encoding="utf-8")
    receipt = {"source": "example", "run_id": run, "discovery_sha256": snapshot * 64,
               "repository": "test/library", "release_tag": "test",
               "started_at": f"2026-11-{day}T01:00:00+00:00", "status": "checkpoint",
               "discovered": discovered, "range_start": start, "range_end": end,
               "records": len(records), "failures": len(failures), "held_records": held,
               "excluded_records": excluded, "excluded_count": len(excluded),
               "records_file": record_file.name, "failures_file": failure_file.name,
               "records_sha256": hashlib.sha256(record_file.read_bytes()).hexdigest(),
               "failures_sha256": hashlib.sha256(failure_file.read_bytes()).hexdigest(),
               "archive": archive, "sha256": "e" * 64, "archive_bytes": 1000,
               "dispositions": dispositions}
    (folder / f"{prefix}.receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
    return receipt


def cloud_assets(receipt):
    return {receipt["archive"]: {"size": receipt["archive_bytes"], "digest": "sha256:" + receipt["sha256"]},
            f"example-{receipt['run_id']}.discovery.jsonl": {"digest": "sha256:" + receipt["discovery_sha256"]}}


def test_new_smaller_snapshot_replaces_larger_old_source(tmp_path):
    checkpoint(tmp_path, run="old", snapshot="a", end=3, discovered=3)
    checkpoint(tmp_path, run="new", snapshot="b", day="02", end=1, discovered=1)
    records, sources, outstanding, _ = assemble.assemble_snapshots(tmp_path, REGISTRY)
    assert len(records) == 1
    assert sources[0]["discovery_sha256"] == "b" * 64
    assert sources[0]["machine_validation_complete"]
    assert not sources[0]["collection_complete"]
    assert outstanding == []


def test_offset_runs_merge_without_a_final_report(tmp_path):
    checkpoint(tmp_path, run="1", start=0, end=2, discovered=4)
    checkpoint(tmp_path, run="2", day="02", start=2, end=4, discovered=4)
    records, sources, outstanding, _ = assemble.assemble_snapshots(tmp_path, REGISTRY)
    assert [record["source_index"] for record in records] == [0, 1, 2, 3]
    assert sources[0]["coverage_ranges"] == [[0, 4]]
    assert sources[0]["acquisition_complete"]
    assert sources[0]["machine_validation_complete"]
    assert not sources[0]["collection_complete"]
    assert sources[0]["visual_acceptance"] == "pending"
    assert not sources[0]["illustrator_verified"]
    assert outstanding == []


def test_new_incomplete_snapshot_keeps_old_complete_only_as_labeled_fallback(tmp_path):
    checkpoint(tmp_path, run="old", end=3, discovered=3)
    checkpoint(tmp_path, run="new", snapshot="b", day="02", start=1, end=2, discovered=2)
    records, sources, outstanding, _ = assemble.assemble_snapshots(tmp_path, REGISTRY)
    assert len(records) == 1
    assert records[0]["acquisition"]["discovery_sha256"] == "b" * 64
    assert sources[0]["missing_ranges"] == [[0, 1]]
    assert sources[0]["older_complete_fallback"]["discovery_sha256"] == "a" * 64
    assert not sources[0]["collection_complete"]
    assert outstanding == ["example"]


def test_later_retry_replaces_failed_and_held_outcomes(tmp_path):
    checkpoint(tmp_path, end=3, discovered=3, statuses={1: "failed", 2: "held"})
    _, before, outstanding, _ = assemble.assemble_snapshots(tmp_path, REGISTRY)
    assert before[0]["source_inventory_processed"]
    assert before[0]["held"] == before[0]["failed"] == 1
    assert not before[0]["acquisition_complete"]
    assert outstanding == ["example"]
    checkpoint(tmp_path, run="2", day="02", start=1, end=3, discovered=3)
    records, after, outstanding, _ = assemble.assemble_snapshots(tmp_path, REGISTRY)
    assert len(records) == 3
    assert after[0]["held"] == after[0]["failed"] == 0
    assert after[0]["machine_validation_complete"]
    assert outstanding == []


def test_render_failure_does_not_become_machine_or_visual_acceptance(tmp_path):
    checkpoint(tmp_path, unchecked=(1,))
    _, sources, outstanding, _ = assemble.assemble_snapshots(tmp_path, REGISTRY)
    assert sources[0]["acquisition_complete"]
    assert not sources[0]["machine_validation_complete"]
    assert sources[0]["machine_validation_pending_records"] == 1
    assert sources[0]["visual_acceptance"] == "pending"
    assert outstanding == ["example"]


def test_metadata_corruption_cannot_silently_reuse_old_success(tmp_path):
    checkpoint(tmp_path)
    broken = checkpoint(tmp_path, run="2", day="02")
    (tmp_path / broken["records_file"]).write_text("", encoding="utf-8")
    records, sources, outstanding, _ = assemble.assemble_snapshots(tmp_path, REGISTRY)
    assert records == []
    assert sources[0]["unknown"] == 2
    assert "SHA-256" in sources[0]["metadata_issues"][0]
    assert outstanding == ["example"]
    checkpoint(tmp_path, run="3", day="03")
    records, sources, outstanding, _ = assemble.assemble_snapshots(tmp_path, REGISTRY)
    assert len(records) == 2
    assert sources[0]["machine_validation_complete"]
    assert sources[0]["metadata_issues"] == []
    assert sources[0]["historical_metadata_issues"]
    assert outstanding == []


def test_missing_cloud_archive_is_not_counted_as_acquired(tmp_path):
    receipt = checkpoint(tmp_path)
    records, sources, outstanding, _ = assemble.assemble_snapshots(tmp_path, REGISTRY, {})
    assert records == [] and outstanding == ["example"]
    assert "Archive is missing" in sources[0]["metadata_issues"][0]
    records, sources, _, _ = assemble.assemble_snapshots(tmp_path, REGISTRY, cloud_assets(receipt))
    assert len(records) == 2 and sources[0]["machine_validation_complete"]


def test_same_size_archive_with_different_cloud_hash_is_rejected(tmp_path):
    receipt = checkpoint(tmp_path)
    assets = cloud_assets(receipt)
    assets[receipt["archive"]]["digest"] = "sha256:" + "0" * 64
    records, sources, outstanding, _ = assemble.assemble_snapshots(tmp_path, REGISTRY, assets)
    assert records == [] and outstanding == ["example"]
    assert "Cloud archive SHA-256" in sources[0]["metadata_issues"][0]


def test_discovery_hash_must_match_uploaded_inventory(tmp_path):
    receipt = checkpoint(tmp_path)
    assets = cloud_assets(receipt)
    assets["example-1.discovery.jsonl"]["digest"] = "sha256:" + "0" * 64
    _, sources, outstanding, _ = assemble.assemble_snapshots(tmp_path, REGISTRY, assets)
    assert outstanding == ["example"]
    assert "Cloud discovery SHA-256" in sources[0]["metadata_issues"][0]


def test_record_cannot_point_to_a_different_archive_even_with_valid_metadata_hash(tmp_path):
    receipt = checkpoint(tmp_path)
    path = tmp_path / receipt["records_file"]
    records = assemble.read_lines(path)
    records[0]["files"][0]["preview"]["archive"]["url"] = "https://example.invalid/wrong.tar.gz"
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    receipt["records_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    (tmp_path / "example-1-b0000.receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
    records, sources, outstanding, _ = assemble.assemble_snapshots(tmp_path, REGISTRY)
    assert records == [] and outstanding == ["example"]
    assert "archive link" in sources[0]["metadata_issues"][0]


def test_release_assets_include_every_paginated_page(monkeypatch):
    calls = []
    def fake_gh(*args):
        calls.append(args)
        if "--paginate" in args:
            return json.dumps([[{"name": "first.receipt.json"}], [{"name": "second.receipt.json"}]])
        return json.dumps({"id": 7, "assets": [{"name": "first.receipt.json"}]})
    monkeypatch.setattr(assemble, "gh", fake_gh)
    assert set(assemble.list_release_assets("test/library", "test")) == {"first.receipt.json", "second.receipt.json"}
    assert "--slurp" in calls[1]
    assert calls[1][-1].endswith("assets?per_page=100")


def test_explicit_scope_exclusion_is_processed_without_becoming_a_download_failure(tmp_path):
    checkpoint(tmp_path, end=3, discovered=3, statuses={1: "excluded"})
    records, sources, outstanding, _ = assemble.assemble_snapshots(tmp_path, REGISTRY)
    source = sources[0]
    assert [record["source_index"] for record in records] == [0, 2]
    assert source["discovered"] == source["processed"] == 3
    assert source["in_scope_discovered"] == source["acquired"] == 2
    assert source["excluded_count"] == 1
    assert source["failed_records"] == source["unknown"] == 0
    assert source["excluded_records"] == [{"source_index": 1, "source_id": "1",
                                            "reason": "AI brush preset, outside illustration/icon scope"}]
    assert source["source_inventory_processed"]
    assert source["acquisition_complete"] and source["machine_validation_complete"]
    assert source["status"] == "machine_complete_pending_visual"
    assert not source["collection_complete"]
    assert source["visual_acceptance"] == "pending"
    assert outstanding == []


def test_unexplained_exclusion_remains_unknown_and_does_not_shrink_scope(tmp_path):
    checkpoint(tmp_path, end=3, discovered=3, statuses={1: "excluded"}, exclusion_reason="  ")
    records, sources, outstanding, _ = assemble.assemble_snapshots(tmp_path, REGISTRY)
    source = sources[0]
    assert len(records) == 2
    assert source["excluded_count"] == 0 and source["excluded_records"] == []
    assert source["in_scope_discovered"] == 3
    assert source["unknown"] == 1
    assert not source["acquisition_complete"] and not source["machine_validation_complete"]
    assert not source["collection_complete"]
    assert outstanding == ["example"]


def test_later_valid_exclusion_replaces_failed_attempt(tmp_path):
    checkpoint(tmp_path, statuses={1: "failed"})
    checkpoint(tmp_path, run="2", day="02", start=1, end=2, statuses={1: "excluded"})
    records, sources, outstanding, _ = assemble.assemble_snapshots(tmp_path, REGISTRY)
    assert len(records) == 1
    assert sources[0]["failed_records"] == 0
    assert sources[0]["excluded_count"] == 1
    assert sources[0]["in_scope_discovered"] == 1
    assert sources[0]["machine_validation_complete"]
    assert not sources[0]["collection_complete"]
    assert outstanding == []


def test_legacy_pilot_is_explicit_pending_and_not_loaded(tmp_path):
    (tmp_path / "example-old.report.json").write_text(json.dumps(
        {"source": "example", "run_id": "old", "downloaded_records": 20, "collection_complete": True}), encoding="utf-8")
    records, sources, outstanding, legacy = assemble.assemble_snapshots(tmp_path, REGISTRY)
    assert records == []
    assert sources[0]["status"] == "legacy_pending"
    assert legacy[0]["status"] == "legacy_pending"
    assert outstanding == ["example"]


def test_aggregation_and_catalog_are_idempotent(tmp_path):
    checkpoint(tmp_path)
    checkpoint(tmp_path, run="2", day="02", start=1, end=2)
    first = assemble.assemble_snapshots(tmp_path, REGISTRY)
    second = assemble.assemble_snapshots(tmp_path, REGISTRY)
    assert first == second
    output = tmp_path / "catalog"
    finalize(first[0], output)
    before = (output / "catalog.jsonl").read_bytes()
    finalize(second[0], output)
    assert (output / "catalog.jsonl").read_bytes() == before
    assert len(assemble.read_lines(output / "catalog.jsonl")) == 2


def test_local_entrypoint_refuses_before_network():
    env = {key: value for key, value in os.environ.items() if key != "GITHUB_ACTIONS"}
    result = subprocess.run([sys.executable, str(ROOT / "scripts/assemble.py"), "--release-tag", "test"],
                            env=env, text=True, capture_output=True)
    assert result.returncode == 2
    assert "GitHub Actions" in result.stderr


GITHUB_REGISTRY = [{"source_id": "example", "primary": True, "adapter": "github_tree", "repository": "source/art"}]


def git_inventory(count=3, *, author="Original artist"):
    from test_reconciliation import inventory
    return [{**inventory(str(index)), "author": author} for index in range(count)]


def github_checkpoint(folder, inventory, *, run, day, start=0, end=None, statuses=None):
    """Real SHA-256s over small synthetic metadata, no artwork transfers."""
    receipt = checkpoint(folder, run=run, day=day, discovered=len(inventory), start=start,
                         end=len(inventory) if end is None else end, statuses=statuses)
    metadata_file = folder / f"example-{run}.discovery.jsonl"
    metadata_file.write_text("".join(json.dumps(item) + "\n" for item in inventory), encoding="utf-8")
    receipt["discovery_sha256"] = hashlib.sha256(metadata_file.read_bytes()).hexdigest()
    path = folder / receipt["records_file"]
    records = assemble.read_lines(path)
    for record in records:
        index = record["source_index"]
        processed_file = record["files"][0]
        processed_file.update(inventory[index]["files"][0])
        processed_file["sha256"] = hashlib.sha256(inventory[index]["source_id"].encode()).hexdigest()
        processed_file["preview"].update(sha256="8" * 64, size_bytes=50)
        record.update(inventory[index])
        record["files"] = [processed_file]
        record["collection_run"] = run
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    receipt["records_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    receipt_path = folder / receipt["records_file"].replace(".records.jsonl", ".receipt.json")
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    assets = cloud_assets(receipt)
    assets[metadata_file.name]["size"] = metadata_file.stat().st_size
    for metadata in (receipt_path, path, folder / receipt["failures_file"]):
        assets[metadata.name] = {"size": metadata.stat().st_size,
                                "digest": "sha256:" + hashlib.sha256(metadata.read_bytes()).hexdigest()}
    return receipt, assets


def test_metadata_only_update_reuses_verified_originals_without_new_batch_claim(tmp_path):
    old, assets = github_checkpoint(tmp_path, git_inventory(), run="old", day="01")
    current_inventory = git_inventory(author="Corrected attribution")
    current_inventory[0]["title"] = "Updated title"
    current, new_assets = github_checkpoint(tmp_path, current_inventory, run="new", day="02", start=1, end=2)
    assets.update(new_assets)
    records, sources, outstanding, _ = assemble.assemble_snapshots(tmp_path, GITHUB_REGISTRY, assets)
    summary = sources[0]
    assert len(records) == 3 and not outstanding
    assert summary["machine_validation_complete"]
    assert summary["catalog_uses"] == "latest_metadata_with_verified_original_reuse"
    assert summary["metadata_reconciliation"]["reused_records"] == 2
    assert all(r["author"] == "Corrected attribution" for r in records)
    assert records[0]["title"] == "Updated title"
    assert records[0]["files"][0]["archive"]["url"].endswith(old["archive"])
    assert records[1]["files"][0]["archive"]["url"].endswith(current["archive"])
    assert records[0]["acquisition"]["artifact_evidence"]["records_sha256"] == old["records_sha256"]
    assert records[0]["acquisition"]["discovery_sha256"] == current["discovery_sha256"]
    assert records[0]["collection_run"] == "old" and not summary["collection_complete"]


def test_reconciliation_does_not_hide_bad_current_attempt(tmp_path):
    _, assets = github_checkpoint(tmp_path, git_inventory(2), run="old", day="01")
    bad, newer = github_checkpoint(tmp_path, git_inventory(2, author="Updated"), run="new", day="02", start=1, end=2)
    assets.update(newer)
    (tmp_path / bad["records_file"]).write_text("broken", encoding="utf-8")
    records, sources, outstanding, _ = assemble.assemble_snapshots(tmp_path, GITHUB_REGISTRY, assets)
    assert len(records) == 1 and records[0]["source_id"] == "0"
    assert sources[0]["unknown"] == 1
    assert sources[0]["metadata_reconciliation"]["reused_records"] == 1
    assert sources[0]["metadata_issues"] and outstanding == ["example"]


def test_current_full_inventory_can_remove_art_and_make_rights_held(tmp_path):
    _, assets = github_checkpoint(tmp_path, git_inventory(3), run="old", day="01")
    items = git_inventory(2, author="Updated")
    items[1].update(distribution_allowed=False, rights_note="Current license unresolved")
    _, newer = github_checkpoint(tmp_path, items, run="new", day="02", start=0, end=0)
    assets.update(newer)
    records, sources, outstanding, _ = assemble.assemble_snapshots(tmp_path, GITHUB_REGISTRY, assets)
    assert [r["source_id"] for r in records] == ["0"]
    assert sources[0]["discovered"] == sources[0]["processed"] == 2
    assert sources[0]["held_count"] == 1
    decision = sources[0]["metadata_reconciliation"]["decisions"][1]
    assert decision["origin"] == "current_discovery_metadata" and not decision["fetch_attempted"]
    assert outstanding == ["example"]


def test_changed_blob_cannot_be_reused_despite_same_title_and_count(tmp_path):
    _, assets = github_checkpoint(tmp_path, git_inventory(2), run="old", day="01")
    items = git_inventory(2)
    items[1]["files"][0]["upstream_hash"] = "git-blob-sha1:" + "9" * 40
    _, newer = github_checkpoint(tmp_path, items, run="new", day="02", start=0, end=0)
    assets.update(newer)
    records, sources, outstanding, _ = assemble.assemble_snapshots(tmp_path, GITHUB_REGISTRY, assets)
    assert [r["source_id"] for r in records] == ["0"]
    assert sources[0]["missing_ranges"] == [[1, 2]] and outstanding == ["example"]


def test_missing_old_cloud_archive_blocks_reuse(tmp_path):
    old, assets = github_checkpoint(tmp_path, git_inventory(2), run="old", day="01")
    _, newer = github_checkpoint(tmp_path, git_inventory(2, author="Updated"), run="new", day="02", start=0, end=0)
    assets.update(newer)
    assets.pop(old["archive"])
    records, sources, outstanding, _ = assemble.assemble_snapshots(tmp_path, GITHUB_REGISTRY, assets)
    assert records == [] and sources[0]["missing_ranges"] == [[0, 2]]
    assert sources[0]["metadata_reconciliation"]["reused_records"] == 0
    assert outstanding == ["example"]


def test_unverified_old_receipt_cannot_authorize_reuse(tmp_path):
    old, assets = github_checkpoint(tmp_path, git_inventory(2), run="old", day="01")
    _, newer = github_checkpoint(tmp_path, git_inventory(2, author="Updated"), run="new", day="02", start=0, end=0)
    assets.update(newer)
    receipt_name = old["records_file"].replace(".records.jsonl", ".receipt.json")
    assets[receipt_name]["digest"] = "sha256:" + "0" * 64
    records, sources, outstanding, _ = assemble.assemble_snapshots(tmp_path, GITHUB_REGISTRY, assets)
    assert records == [] and outstanding == ["example"]
    assert sources[0]["metadata_reconciliation"]["rejected_prior_candidates"]["prior_evidence_or_media_incomplete"] == 2


def test_actual_complete_discovery_hash_is_required_before_reuse(tmp_path):
    _, assets = github_checkpoint(tmp_path, git_inventory(2), run="old", day="01")
    current, newer = github_checkpoint(tmp_path, git_inventory(2, author="Updated"), run="new", day="02", start=0, end=0)
    assets.update(newer)
    (tmp_path / "example-new.discovery.jsonl").write_text("{}\n", encoding="utf-8")
    records, sources, outstanding, _ = assemble.assemble_snapshots(tmp_path, GITHUB_REGISTRY, assets)
    assert records == [] and outstanding == ["example"]
    assert "SHA-256" in sources[0]["metadata_issues"][0]


def test_duplicate_complete_inventory_is_rejected_even_with_valid_hash(tmp_path):
    _, assets = github_checkpoint(tmp_path, git_inventory(2), run="old", day="01")
    items = git_inventory(2, author="Updated")
    items[1] = dict(items[0])
    _, newer = github_checkpoint(tmp_path, items, run="new", day="02", start=0, end=0)
    assets.update(newer)
    records, sources, outstanding, _ = assemble.assemble_snapshots(tmp_path, GITHUB_REGISTRY, assets)
    assert records == [] and outstanding == ["example"]
    assert "duplicate" in sources[0]["metadata_issues"][0]
