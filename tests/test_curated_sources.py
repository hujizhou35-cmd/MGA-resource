"""Curated manifest discovery is metadata-only; downloads use synthetic bytes."""
import hashlib
import json

import pytest

from mga_resources import curated_sources
from mga_resources.http import HttpClient, SourceAccessError
from mga_resources.sources import discover


def fixture(tmp_path, monkeypatch, *, mutate=None):
    monkeypatch.setattr(curated_sources, "ROOT", tmp_path)
    folder = tmp_path / "data"; folder.mkdir()
    value = {"source": "curated-test", "source_id": "1", "title": "Synthetic tissue", "author": "Test author",
             "license": "CC-BY-4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/",
             "attribution": "Test author, synthetic tissue, CC BY 4.0", "source_url": "https://example.org/page/1",
             "rights_evidence": ["https://example.org/page/1"], "upstream_revision": "sha1:" + "a" * 40,
             "files": [{"url": "https://example.org/original.svg", "format": "svg", "upstream_size": 6,
                        "upstream_hash": "sha1:" + "a" * 40}]}
    if mutate:
        mutate(value)
    manifest = {"schema_version": 1, "version": "v1", "items": [value]}
    data = json.dumps(manifest).encode(); (folder / "curated.json").write_bytes(data)
    source = {"source_id": "curated-test", "adapter": "curated_manifest", "manifest_path": "data/curated.json",
              "manifest_sha256": hashlib.sha256(data).hexdigest(), "manifest_version": "v1",
              "expected_item_count": 1, "download_hosts": ["example.org"], "extensions": ["svg"]}
    return source


class NoNetwork:
    def __getattr__(self, name):
        raise AssertionError("Curated discovery must not download any image or crawl pages")


def test_curated_discovery_keeps_item_credit_and_download_pins(tmp_path, monkeypatch):
    source = fixture(tmp_path, monkeypatch)
    record = discover(source, NoNetwork())[0]
    assert record["author"] == "Test author"
    assert record["license"] == "CC-BY-4.0"
    assert record["files"][0]["upstream_hash"] == "sha1:" + "a" * 40
    assert record["curated_manifest"]["sha256"] == source["manifest_sha256"]


def test_explicit_item_rights_hold_is_never_overwritten(tmp_path, monkeypatch):
    source = fixture(tmp_path, monkeypatch, mutate=lambda item: item.update(distribution_allowed=False, rights_status="pending"))
    record = discover(source, NoNetwork())[0]
    assert record["distribution_allowed"] is False
    assert record["rights_status"] == "pending"


@pytest.mark.parametrize("mutation", [lambda r: r.pop("author"), lambda r: r.pop("rights_evidence"),
                                     lambda r: r.update(license="per-item"),
                                     lambda r: r["files"][0].pop("upstream_hash"),
                                     lambda r: r["files"][0].update(upstream_size=0),
                                     lambda r: r["files"][0].update(url="https://another.example/a.svg")])
def test_curated_items_cannot_inherit_missing_rights_or_unpinned_files(tmp_path, monkeypatch, mutation):
    with pytest.raises(ValueError):
        discover(fixture(tmp_path, monkeypatch, mutate=mutation), NoNetwork())


def test_manifest_changed_or_partial_inventory_fails_before_any_network(tmp_path, monkeypatch):
    source = fixture(tmp_path, monkeypatch)
    with pytest.raises(SourceAccessError, match="SHA-256"):
        discover({**source, "manifest_sha256": "0" * 64}, NoNetwork())
    with pytest.raises(ValueError, match="count"):
        discover({**source, "expected_item_count": 2}, NoNetwork())
    with pytest.raises(ValueError, match="inside project data"):
        discover({**source, "manifest_path": "../../outside.json"}, NoNetwork())


@pytest.mark.parametrize("algorithm", ["sha1", "sha256"])
def test_raw_content_hashes_are_distinct_from_git_blob_hashes(monkeypatch, algorithm):
    client, data = HttpClient(), b"<svg/>"
    monkeypatch.setattr(client, "get_bytes", lambda _: data)
    descriptor = {"url": "https://example.org/a.svg", "upstream_size": len(data),
                  "upstream_hash": algorithm + ":" + hashlib.new(algorithm, data).hexdigest()}
    assert client.get_file(descriptor) == data
    with pytest.raises(SourceAccessError, match="content hash"):
        client.get_file({**descriptor, "upstream_hash": algorithm + ":" + "0" * len(hashlib.new(algorithm).hexdigest())})
    with pytest.raises(SourceAccessError, match="size mismatch"):
        client.get_file({**descriptor, "upstream_size": len(data) + 1})


def test_unknown_content_hash_cannot_silently_skip_verification(monkeypatch):
    client = HttpClient()
    monkeypatch.setattr(client, "get_bytes", lambda _: b"test")
    with pytest.raises(SourceAccessError, match="algorithm"):
        client.get_file({"url": "https://example.org/a.svg", "upstream_hash": "unknown:abc"})


def test_checked_in_sources_are_exact_small_inventories_and_no_network():
    registry = json.loads((curated_sources.ROOT / "data/sources.json").read_text(encoding="utf-8"))
    sources = [row for row in registry["sources"] if row.get("adapter") == "curated_manifest"]
    assert {row["source_id"] for row in sources} == {"commons", "openclipart"}
    records = [item for source in sources for item in discover(source, NoNetwork())]
    assert len(records) == 7
    assert all(item["thumbnail_review"]["status"] == "viewed" for item in records)
    assert all(item["thumbnail_review"]["original_download_verified"] is False for item in records if item["source"] == "commons")
    assert not any("forest" in item["title"].lower() for item in records)
