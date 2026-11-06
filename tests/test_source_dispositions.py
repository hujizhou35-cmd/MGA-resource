"""Synthetic provenance/gap tests. These are not visual or release acceptance."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("prepare_source_dispositions", ROOT / "scripts/prepare_source_dispositions.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def proof(label="evidence"):
    return {"uri": "https://example.invalid/" + label, "sha256": m.digest(label.encode())}


def row(files=None, **kw):
    return {"source": "example", "source_id": "one", "title": "Example", "files": files or [], **kw}


def descriptor(fmt="svg", member=None):
    return {"url": "https://example.invalid/pack.zip" if member else "https://example.invalid/file." + fmt,
            "format": fmt, **({"archive_member": member} if member else {})}


def file(fmt="svg", member=None):
    return {**descriptor(fmt, member), "sha256": m.digest((fmt + str(member)).encode()),
            "validation": {"status": "machine_checked_and_rendered"}, "archive": proof("archive")}


def attempt(errors, status="failed"):
    return {"failures": errors, "status": status, "evidence": proof("failures"),
            "receipt_evidence": proof("receipt"), "archive_evidence": proof("archive"),
            "receipt": {"run_id": "1-1"}, "original_discovery_sha256": "a" * 64,
            "evidence_reuse": "same_inventory"}


def failure(fmt="png", member=None, **kw):
    return {"url": descriptor(fmt, member)["url"], "format": fmt,
            "upstream_archive_member": member, "error": "HTTP 404 for original", "error_type": "SourceAccessError", **kw}


def prepare(item, available=(), errors=(), **kw):
    return m.prepare_source(item["source"], {}, [item], {(item["source"], "one"): list(available)},
                            {0: attempt(list(errors), **kw)}, proof("inventory"))


def test_missing_png_retains_svg_and_never_invents_unavailable_sha():
    svg = file()
    groups, pending = prepare(row([descriptor(), descriptor("png")]), [svg], [failure()])
    entry = groups["unavailable_variants", "not_acquired"][0]
    assert entry["available_file_sha256"] == svg["sha256"]
    assert "sha256" not in entry
    assert entry["retained_file_sha256"] == [svg["sha256"]]
    assert not pending and not groups.get(("items", "quarantine"))


def test_legacy_shared_zip_url_needs_exact_missing_member_difference():
    a, b = descriptor(member="a.svg"), descriptor(member="b.svg")
    legacy = {"url": a["url"], "error_type": "MediaValidationError", "error": "Invalid XML"}
    groups, pending = prepare(row([a, b]), [file(member="a.svg")], [legacy])
    assert groups["unavailable_variants", "quarantine"][0]["upstream_archive_member"] == "b.svg"
    assert not pending
    groups, pending = prepare(row([a, b]), [], [legacy])
    assert not groups and len(pending) == 2  # Never infer two failed members from one URL.


def test_new_failure_member_does_not_match_other_zip_member():
    groups, pending = prepare(row([descriptor(member="a.svg"), descriptor(member="b.svg")]),
                              [file(member="a.svg")], [failure("svg", "a.svg")])
    assert not groups and len(pending) == 1


def test_acquired_file_ignores_old_failure_and_later_success_requires_refresh():
    groups, pending = prepare(row([descriptor()]), [file()], [failure("svg")])
    assert not groups and not pending
    groups, pending = prepare(row([descriptor()]), [], [failure("svg")], status="acquired")
    assert not groups and "refresh assembly" in pending[0]["reason"]


def test_partial_unknown_does_not_quarantine_entire_item():
    groups, pending = prepare(row([descriptor(), descriptor("png")]), [], [failure("svg")])
    assert not groups and len(pending) == 2


@pytest.mark.parametrize("message", ["HTTP 521 for original", "HTTP 429", "TLS handshake timeout"])
def test_temporary_errors_remain_pending(message):
    groups, pending = prepare(row([descriptor()]), [], [failure("svg", error=message)])
    assert not groups and pending


def test_official_preset_scope_is_specific_not_title_guessing():
    groups, pending = prepare(row([], source="nih-bioart", upstream_image_types=["Swatch"]))
    assert groups["items", "exclude_from_v1"][0]["fetch_attempted"] is False
    assert not pending
    groups, pending = prepare(row([], title="Brush with no official type"))
    assert not groups and pending


def test_pinned_git_evidence_bridge_requires_identical_blob_path_commit():
    old = row([{**descriptor(), "url": "https://raw.githubusercontent.com/u/r/" + "b" * 40 + "/a.svg",
                "upstream_hash": "git-blob-sha1:" + "c" * 40}])
    new = copy.deepcopy(old)
    new["license"] = "CC-BY-4.0"
    assert m.pinned_equivalent(old, new)
    new["files"][0]["upstream_hash"] = "git-blob-sha1:" + "d" * 40
    assert not m.pinned_equivalent(old, new)
    new = copy.deepcopy(old);new["files"][0]["url"] = new["files"][0]["url"].replace("b" * 40, "main")
    assert not m.pinned_equivalent(old, new)


def test_receipt_identity_mismatch_fails_instead_of_joining_by_title():
    receipt = {"source": "example", "run_id": "1-1", "discovery_sha256": "a" * 64,
               "range_start": 0, "range_end": 1, "discovered": 1,
               "dispositions": [{"index": 0, "source_id": "different", "status": "failed"}]}
    with pytest.raises(ValueError, match="identity mismatch"):
        m.latest_attempts("example", [row()], "a" * 64, [{"receipt": receipt, "failures": []}], {})


def test_merged_source_uses_origin_url():
    stored = {**file(), "source_records": [{"source": "second", "source_id": "two", "url": "https://second.invalid/file.svg"}]}
    values = m.acquired_files([{**row(), "files": [stored]}])
    assert values["second", "two"][0]["url"] == "https://second.invalid/file.svg"


def test_scope_pending_review_is_not_silently_excluded_or_approved():
    reviewed = {"source": "example", "items": [{"source_id": "one", "original_sha256": file()["sha256"],
                "visual_verdict": "scope_pending", "observations": "Photography appearance, provenance unconfirmed."}]}
    available = {("example", "one"): [file()]}
    groups, pending = m.review_files([(reviewed, proof("review"))], available, {"example": {}})
    assert not groups and pending[0]["state"] == "scope_pending"
    item = reviewed["items"][0]
    item.update(disposition="exclude_from_v1", reason="Scope remains pending; defer exact file from v1.",
                reviewed_by="Fixture agent", reviewed_at="2026-11-07", scope_status="scope_pending")
    groups, pending = m.review_files([(reviewed, proof("review"))], available, {"example": {}})
    assert groups["example", "files", "exclude_from_v1"][0]["sha256"] == file()["sha256"]


def test_metadata_wrong_bytes_or_artwork_access_fail(tmp_path):
    data = b'[]\n';name = 'example-1-1.failures.json'
    path = tmp_path / name;path.write_bytes(b'bad!')
    url = 'https://github.com/u/r/releases/download/test/' + name
    assets = [{"name": name, "browser_download_url": url, "size": len(data), "digest": "sha256:" + m.digest(data)}]
    metadata = m.Metadata(assets, [tmp_path], repository="u/r", tag="test", cache=tmp_path / "cache")
    with pytest.raises(FileNotFoundError):
        metadata.get(name)
    with pytest.raises(ValueError, match="Only"):
        metadata.get('pack.tar.gz')
    path.write_bytes(data)
    value, proof_ = metadata.get(name)
    assert value == [] and proof_["sha256"] == m.digest(data)


def test_failed_preview_is_file_quarantine_not_source_redownload():
    broken, good = file(), file("png")
    broken["validation"]["status"] = "preview_failed"
    groups = m.failed_previews({("example", "one"): [broken, good]}, {"example": {}}, proof("catalog"))
    selected = groups["example", "files", "quarantine"]
    assert len(selected) == 1 and selected[0]["sha256"] == broken["sha256"]
    assert selected[0]["source_download_requested"] is False


def test_decisions_and_selection_hashes_bind_exact_files():
    groups = {("example", "items", "quarantine"): [{"source_id": "one", "source_index": 3, "reason": "Bad original", "evidence": proof()}]}
    decisions, selections = m.make_documents(groups, {"example": {"discovery_sha256": "a" * 64}},
        {"release_id": "test", "input_catalog_sha256": "b" * 64, "benchmark_sha256": "c" * 64},
        reviewed_at="2026-11-07", reviewed_by={"kind": "agent", "id": "Fixture"})
    ref = decisions["decisions"][0]["target"]["selection_manifest"]
    assert ref["sha256"] == m.digest(m.json_bytes(selections[Path(ref["uri"]).name]))
    assert decisions["final_acceptance_claimed"] is False


def test_acceptance_contract_keeps_successful_counterpart():
    # Reuse the production acceptance fixture, but leave its review gates closed.
    spec = importlib.util.spec_from_file_location("acceptance_fixture", ROOT / "tests/test_accept_release.py")
    fixture = importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
    args = fixture.fixture()
    good = args["catalog"][0]["files"][0]
    good["url"] = "https://example.invalid/success.png"
    selected = {("example", "unavailable_variants", "not_acquired"): [{"source_id": "0", "source_index": 0,
        "url": "https://example.invalid/missing.svg", "format": "svg", "reason": "HTTP 404",
        "available_file_sha256": good["sha256"], "evidence": proof()}]}
    decisions, selections = m.make_documents(selected, {x["source"]: x for x in args["assembly"]["source_snapshots"]},
        {"release_id": "test", "input_catalog_sha256": args["hashes"]["catalog_sha256"],
         "benchmark_sha256": args["hashes"]["benchmark_sha256"]},
        reviewed_at="2026-11-07", reviewed_by={"kind": "agent", "id": "Fixture"})
    args.update(decisions=decisions, selection_manifests={m.digest(m.json_bytes(v)): v for v in selections.values()}, judgments={})
    manifest, report = m.evaluate_release(**args)
    assert good["sha256"] in {f["sha256"] for f in manifest["files"]}
    assert not any(x["code"] == "decisions.incomplete" for x in report["missing"])
    assert not report["acceptance_passed"]
