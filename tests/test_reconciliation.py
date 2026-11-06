import copy
import hashlib

import pytest

from mga_resources.reconciliation import reconcile_github_snapshot


SOURCE = {"source_id": "example", "adapter": "github_tree", "repository": "source/art"}
COMMIT = "c" * 40
OLD_DIGEST, CURRENT_DIGEST = "a" * 64, "b" * 64


def inventory(sid="heart"):
    return {"source": "example", "source_id": sid, "title": "Current " + sid,
            "author": "Current artist", "license": "CC0-1.0", "upstream_revision": COMMIT,
            "files": [{"url": f"https://raw.githubusercontent.com/source/art/{COMMIT}/{sid}.svg",
                       "format": "svg", "upstream_hash": "git-blob-sha1:" + hashlib.sha1(sid.encode()).hexdigest(),
                       "upstream_size": 100}]}


def successful_state(item=None, index=0):
    record = copy.deepcopy(item or inventory())
    proof = {"source": "example", "discovery_sha256": OLD_DIGEST, "source_index": index,
             "run_id": "old-run", "repository": "test/library", "release_tag": "test",
             "archive": "old.tar.gz", "archive_bytes": 1000, "archive_sha256": "d" * 64,
             "records_file": "old.records.jsonl", "records_sha256": "e" * 64,
             "failures_file": "old.failures.json", "failures_sha256": "f" * 64,
             "receipt_file": "old.receipt.json", "receipt_sha256": "7" * 64}
    archive_url = "https://github.com/test/library/releases/download/test/old.tar.gz"
    for number, f in enumerate(record["files"]):
        f.update(sha256="1" * 64, size_bytes=100, storage_path=f"files/{number}.svg",
                 archive={"url": archive_url, "member": f"files/{number}.svg"},
                 validation={"status": "machine_checked_and_rendered", "scientific_review": "not_reviewed"},
                 preview={"sha256": "2" * 64, "size_bytes": 50, "path": f"previews/{number}.png",
                          "archive": {"url": archive_url, "member": f"previews/{number}.png"}})
    record.update(title="Old title", author="Old artist", source_index=index, collection_run="old-run",
                  id="stale-id", family_id="stale-family", rights_status="stale-nonpending-value")
    return {"source_id": record["source_id"], "status": "acquired", "record": record,
            "failures": [], "evidence": proof}


def reconcile(items, prior, current=None):
    return reconcile_github_snapshot(SOURCE, items, CURRENT_DIGEST, current or {},
                                     [{"discovery_sha256": OLD_DIGEST, "states": prior}])


def test_reuses_exact_file_with_current_metadata_and_original_proof():
    old = successful_state(index=8)
    before = copy.deepcopy(old)
    states, audit = reconcile([inventory()], {8: old})
    record = states[0]["record"]
    assert record["title"] == "Current heart" and record["author"] == "Current artist"
    assert "id" not in record and "family_id" not in record and "rights_status" not in record
    assert record["files"] == before["record"]["files"]
    assert record["collection_run"] == "old-run"
    assert record["acquisition"]["discovery_sha256"] == CURRENT_DIGEST
    assert record["acquisition"]["artifact_evidence"]["source_index"] == 8
    assert record["acquisition"]["artifact_evidence"]["records_sha256"] == "e" * 64
    assert record["files"][0]["validation"]["scientific_review"] == "not_reviewed"
    assert audit["reused_records"] == audit["reused_files"] == 1
    assert old == before


@pytest.mark.parametrize("change", ["commit", "path", "blob", "size", "added_file", "removed_file", "missing_blob", "mutable_url"])
def test_changed_file_identity_is_never_reused(change):
    old = successful_state()
    current = inventory()
    if change == "commit": current["upstream_revision"] = "9" * 40
    elif change == "path": current["files"][0]["url"] += ".new"
    elif change == "blob": current["files"][0]["upstream_hash"] = "git-blob-sha1:" + "9" * 40
    elif change == "size": current["files"][0]["upstream_size"] += 1
    elif change == "added_file": current["files"].append(inventory("extra")["files"][0])
    elif change == "removed_file": current["files"] = []
    elif change == "missing_blob": current["files"][0].pop("upstream_hash")
    elif change == "mutable_url": current["files"][0]["url"] = current["files"][0]["url"].replace(COMMIT, "main")
    states, audit = reconcile([current], {0: old})
    assert states == {} and audit["reused_records"] == 0 and audit["unresolved_records"] == 1


def test_deleted_artwork_does_not_survive_and_reordering_uses_source_identity():
    old = {0: successful_state(inventory("deleted"), 0), 1: successful_state(inventory("kept"), 1)}
    states, audit = reconcile([inventory("kept")], old)
    assert list(states) == [0]
    assert states[0]["record"]["source_id"] == "kept"
    assert states[0]["record"]["acquisition"]["artifact_evidence"]["source_index"] == 1
    assert audit["current_inventory_count"] == 1


@pytest.mark.parametrize("new_state", [
    {"status": "failed", "record": None, "failures": [{"error": "current failure"}]},
    {"status": "unknown", "record": None, "failures": [], "issue": "Current checksum mismatch"},
])
def test_current_failed_or_corrupt_attempt_cannot_be_masked(new_state):
    states, audit = reconcile([inventory()], {0: successful_state()}, {0: copy.deepcopy(new_state)})
    assert states[0] == new_state
    assert audit["reused_records"] == 0


def test_current_rights_and_scope_override_old_downloads_without_claiming_fetches():
    held, excluded, unheld = inventory("held"), inventory("excluded"), inventory("formerly-held")
    held.update(distribution_allowed=False, rights_note="Current license restricts distribution")
    excluded.update(scope_status="excluded", exclusion_reason="Outside illustration scope")
    old = {0: successful_state(inventory("held"), 0), 1: successful_state(inventory("excluded"), 1),
           2: {"source_id": "formerly-held", "status": "held", "record": None, "failures": []}}
    states, audit = reconcile([held, excluded, unheld], old)
    assert states[0]["status"] == "held" and states[0]["record"] is None
    assert states[1]["status"] == "excluded" and states[1]["exclusion"]["reason"]
    assert states[0]["reconciliation"]["fetch_attempted"] is False
    assert 2 not in states  # Permission becoming known does not manufacture art.
    assert audit["metadata_dispositions"] == 2 and audit["reused_records"] == 0


@pytest.mark.parametrize("damage", ["proof", "archive", "preview", "machine_status", "size"])
def test_unverified_or_incomplete_artifacts_do_not_become_reuse_candidates(damage):
    old = successful_state()
    if damage == "proof": old.pop("evidence")
    elif damage == "archive": old["record"]["files"][0]["archive"]["url"] = "https://example.org/forged.tar.gz"
    elif damage == "preview": old["record"]["files"][0]["preview"].pop("sha256")
    elif damage == "machine_status": old["record"]["files"][0]["validation"]["status"] = "preview_failed"
    elif damage == "size": old["record"]["files"][0]["size_bytes"] = 99
    states, audit = reconcile([inventory()], {0: old})
    assert states == {} and audit["reused_records"] == 0


def test_duplicate_inventory_and_non_github_sources_are_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        reconcile([inventory(), inventory()], {})
    with pytest.raises(ValueError, match="GitHub"):
        reconcile_github_snapshot({**SOURCE, "adapter": "nih_bioart"}, [inventory()], CURRENT_DIGEST, {}, [])


def test_newer_bad_history_prevents_falling_back_to_older_success():
    states, audit = reconcile_github_snapshot(SOURCE, [inventory()], CURRENT_DIGEST, {}, [
        {"discovery_sha256": "3" * 64, "states": {}, "metadata_issues": ["checksum mismatch"]},
        {"discovery_sha256": OLD_DIGEST, "states": {0: successful_state()}},
    ])
    assert states == {} and audit["reused_records"] == 0
