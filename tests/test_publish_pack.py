"""Only synthetic bytes: no network requests and no real library downloads."""
from collections import Counter
import copy
import gzip
import importlib.util
import io
import json
from pathlib import Path
import tarfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_publish_pack", ROOT / "scripts/build_publish_pack.py")
pack = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pack)
REPO = "test/library"
SOURCE = pack.release_url(REPO, "collection-1", "source.tar.gz")


def tar_bytes(rows):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for name, data in rows:
            member = tarfile.TarInfo(name)
            if isinstance(data, tarfile.TarInfo):
                archive.addfile(data)
            else:
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
    return gzip.compress(output.getvalue(), mtime=0)


def reference(data, member, *, fmt="png", url=SOURCE):
    return {"sha256": pack.sha(data), "size_bytes": len(data), "format": fmt,
            "archive": {"url": url, "member": member}}


def fixture():
    first, second, preview = b"synthetic SVG one", b"synthetic SVG two", b"synthetic shared preview"
    manifest = {"schema_version": 1, "acceptance_report_required": True, "collection_release": "collection-1",
                "catalog_sha256": "c" * 64, "files": [
                    {**reference(first, "files/first.svg", fmt="svg"), "preview": reference(preview, "previews/p.png"),
                     "provenance": [{"license": "CC-BY-4.0", "author": "Test artist", "attribution": "Synthetic credit"}]},
                    {**reference(second, "files/second.svg", fmt="svg"), "preview": reference(preview, "previews/p.png")}]}
    data = tar_bytes([("files/first.svg", first), ("files/second.svg", second), ("previews/p.png", preview),
                      ("quarantine/bad.svg", b"must never be republished"), ("records.jsonl", b"unlisted collection metadata")])
    return manifest, {SOURCE: data}


def build(tmp_path, *, manifest=None, streams=None, **kwargs):
    default_manifest, default_streams = fixture()
    manifest = default_manifest if manifest is None else manifest
    streams = default_streams if streams is None else streams
    calls = Counter()
    def opener(url):
        calls[url] += 1
        return io.BytesIO(streams[url])
    data = pack.json_bytes(manifest)
    index, index_path = pack.build_bundle(data, pack.sha(data), repository=REPO, version="v1",
         archive_proofs={url: {"sha256": pack.sha(value), "size_bytes": len(value)} for url, value in streams.items()},
         opener=opener, output=tmp_path / "build", **kwargs)
    return index, index_path, calls


class MemoryStore:
    repository = REPO
    def __init__(self, values=None):
        self.values, self.events, self.calls = values or {}, [], Counter()
    def proof(self, url):
        return {"sha256": pack.sha(self.values[url]), "size_bytes": len(self.values[url])}
    def metadata(self, url, expected_sha):
        assert pack.sha(self.values[url]) == expected_sha
        return self.values[url]
    def open(self, url):
        self.calls[url] += 1
        return io.BytesIO(self.values[url])
    def ensure_release(self, tag):
        self.events.append(("release", tag))
    def preflight(self, tag, paths):
        for path in paths:
            url = pack.release_url(REPO, tag, path.name)
            if url in self.values and self.values[url] != path.read_bytes():
                raise ValueError("Publication version already binds different bytes")
    def upload_checked(self, tag, path):
        self.events.append(("upload", path.name))
        url = pack.release_url(REPO, tag, path.name)
        data = path.read_bytes()
        if url in self.values and self.values[url] != data:
            raise ValueError("Immutable asset conflict")
        self.values[url] = data
        return {"name": path.name, "url": url, **self.proof(url), "github_digest_verified": True}


def test_build_streams_each_source_once_and_excludes_every_unlisted_member(tmp_path):
    manifest, _ = fixture()
    index, index_path, calls = build(tmp_path)
    assert calls == {SOURCE: 1}
    assert index["artwork_object_count"] == 3
    assert index["source_archives"][0]["complete_stream_verified"] is True
    assert index["source_archives"][0]["selected_member_count"] == 3
    objects, _ = pack.plan_manifest(manifest)
    actual = {}
    for part in index["packs"]:
        with tarfile.open(index_path.parent / part["name"], "r:gz") as archive:
            for member in archive:
                actual[member.name] = pack.sha(archive.extractfile(member).read())
    assert actual == {obj["path"]: obj["sha256"] for obj in objects.values()}
    stored = json.loads((index_path.parent / index["publish_manifest"]["name"]).read_bytes())
    assert stored["files"][0]["provenance"] == manifest["files"][0]["provenance"]


def test_shared_bytes_at_multiple_source_locations_are_all_checked_once(tmp_path):
    manifest, streams = fixture()
    second_url = pack.release_url(REPO, "other", "preview.tar.gz")
    manifest["files"][1]["preview"]["archive"] = {"url": second_url, "member": "p.png"}
    streams[second_url] = tar_bytes([("p.png", b"synthetic shared preview")])
    index, _, calls = build(tmp_path, manifest=manifest, streams=streams)
    assert calls == {SOURCE: 1, second_url: 1}
    assert index["artwork_object_count"] == 3


def test_acceptance_preview_contract_allows_png_member_without_format(tmp_path):
    manifest, streams = fixture()
    for item in manifest["files"]:
        del item["preview"]["format"]
    index, _, _ = build(tmp_path, manifest=manifest, streams=streams)
    assert index["artwork_object_count"] == 3
    assert [obj["format"] for part in index["packs"] for obj in part["members"] if "preview" in obj["roles"]] == ["png"]


def test_large_objects_are_retained_and_static_count_is_bounded(tmp_path):
    index, _, _ = build(tmp_path, file_limit=20, count_limit=1)
    assert sum(part["member_count"] for part in index["packs"]) == 3
    assert [part["kind"] for part in index["packs"]].count("download") == 1
    assert all(part["member_count"] <= 1 for part in index["packs"])
    assert any(obj["size_bytes"] >= 20 for part in index["packs"] if part["kind"] == "download" for obj in part["members"])


def test_size_exactly_at_static_threshold_is_download_only(tmp_path):
    manifest, _ = fixture()
    size = manifest["files"][0]["size_bytes"]
    index, _, _ = build(tmp_path, file_limit=size)
    assert all(part["kind"] == "download" for part in index["packs"])


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "wrong_hash", "wrong_size", "symlink"])
def test_source_member_faults_stop_build(tmp_path, mutation):
    manifest, _ = fixture()
    rows = [("files/first.svg", b"synthetic SVG one"), ("files/second.svg", b"synthetic SVG two"),
            ("previews/p.png", b"synthetic shared preview")]
    if mutation == "missing":
        rows.pop()
    elif mutation == "duplicate":
        rows.append(rows[0])
    elif mutation == "wrong_hash":
        rows[0] = (rows[0][0], b"X" * len(rows[0][1]))
    elif mutation == "wrong_size":
        rows[0] = (rows[0][0], b"X")
    else:
        link = tarfile.TarInfo(rows[0][0])
        link.type, link.linkname = tarfile.SYMTYPE, "../../outside"
        rows[0] = (rows[0][0], link)
    with pytest.raises(ValueError):
        build(tmp_path, manifest=manifest, streams={SOURCE: tar_bytes(rows)})
    assert not list((tmp_path / "build").glob("*.bundle.json"))


@pytest.mark.parametrize("field,bad", [("sha256", "0" * 64), ("size_bytes", 1), ("size_bytes", 99999999)])
def test_full_source_archive_digest_and_size_must_match(tmp_path, field, bad):
    manifest, streams = fixture()
    _, archives = pack.plan_manifest(manifest)
    value = streams[SOURCE]
    proof = {"sha256": pack.sha(value), "size_bytes": len(value), field: bad}
    with pytest.raises(ValueError):
        pack.extract_selected(lambda _: io.BytesIO(value), SOURCE, proof, archives[SOURCE], tmp_path)


def test_truncated_gzip_is_rejected_even_with_matching_compressed_digest(tmp_path):
    manifest, streams = fixture()
    _, archives = pack.plan_manifest(manifest)
    data = streams[SOURCE][:-5]
    with pytest.raises((EOFError, ValueError, tarfile.ReadError)):
        pack.extract_selected(lambda _: io.BytesIO(data), SOURCE, {"sha256": pack.sha(data), "size_bytes": len(data)}, archives[SOURCE], tmp_path)


def test_exact_clean_pack_rejects_extra_members_including_after_tar_end(tmp_path):
    body = b"selected"
    wanted = {"objects/a.png": reference(body, "ignored")}
    for data in (tar_bytes([("objects/a.png", body), ("quarantine/extra", b"x")]),
                 gzip.compress(gzip.decompress(tar_bytes([("objects/a.png", body)]))
                               + gzip.decompress(tar_bytes([("quarantine/extra", b"x")])))):
        with tempfile_directory(tmp_path) as destination:
            with pytest.raises(ValueError, match="Unlisted"):
                pack.extract_selected(lambda _: io.BytesIO(data), SOURCE, {"sha256": pack.sha(data), "size_bytes": len(data)}, wanted, destination, exact=True)


def tempfile_directory(parent):
    # ContextManager with Path for isolated repeated synthetic extraction.
    from contextlib import contextmanager
    import tempfile
    @contextmanager
    def folder():
        with tempfile.TemporaryDirectory(dir=parent) as value:
            yield Path(value)
    return folder()


def test_manifest_requires_exact_canonical_bytes_and_hash(tmp_path):
    manifest, _ = fixture()
    data = pack.json_bytes(manifest)
    with pytest.raises(ValueError, match="SHA-256"):
        pack.parse_manifest(data, "0" * 64)
    noncanonical = json.dumps(manifest, indent=2).encode()
    with pytest.raises(ValueError, match="canonical"):
        pack.parse_manifest(noncanonical, pack.sha(noncanonical))
    manifest["files"] = []
    with pytest.raises(ValueError, match="nonempty"):
        pack.parse_manifest(pack.json_bytes(manifest), pack.sha(pack.json_bytes(manifest)))


@pytest.mark.parametrize("member", ["../bad", "/absolute", "C:/bad", "a//b", "a\\b", "a/./b", "a\x00b"])
def test_manifest_member_paths_cannot_escape_staging(member):
    manifest, _ = fixture()
    manifest["files"][0]["archive"]["member"] = member
    with pytest.raises(ValueError):
        pack.plan_manifest(manifest)


@pytest.mark.parametrize("url", ["file:///tmp/a", "https://evil.example/a", "https://github.com/test/other/releases/download/v/a.tar.gz",
                                  SOURCE + "?token=abc", SOURCE.replace("github.com", "github.com:443")])
def test_only_current_repository_release_assets_are_allowed(url):
    with pytest.raises(ValueError):
        pack.release_location(url, REPO)


def test_publication_is_receipt_last_and_evidence_matches_acceptance_contract(tmp_path):
    manifest, _ = fixture()
    index, index_path, _ = build(tmp_path)
    store = MemoryStore()
    result = pack.publish_bundle(index, index_path, index_path.parent, store, run_id="123-1", checked_by={"kind": "agent", "id": "synthetic-test"})
    assert store.events[-1][1].endswith(".receipt.json")
    receipt = json.loads(store.values[result["url"]])
    evidence = json.loads(store.values[receipt["restore_evidence"]["url"]])
    assert evidence["passed"] is True and evidence["published_subset_only"] is True
    assert evidence["publish_manifest_sha256"] == pack.sha(pack.json_bytes(manifest))
    assert evidence["bundle_sha256"] == pack.sha(index_path.read_bytes())
    assert set(evidence["artwork_member_sha256"]) == set(pack.plan_manifest(manifest)[0])
    assert all(item["github_digest_verified"] for item in receipt["published_assets"])


def test_failed_upload_never_publishes_completion_receipt(tmp_path):
    index, index_path, _ = build(tmp_path)
    class BrokenStore(MemoryStore):
        def upload_checked(self, tag, path):
            if path.name.endswith(".restore_evidence.json"):
                raise RuntimeError("HTTP 403")
            return super().upload_checked(tag, path)
    store = BrokenStore()
    with pytest.raises(RuntimeError, match="403"):
        pack.publish_bundle(index, index_path, index_path.parent, store, run_id="1-1", checked_by="test")
    assert not any(name.endswith(".receipt.json") for _, name in store.events)


def test_conflicting_existing_version_is_rejected_before_uploading_new_parts(tmp_path):
    index, index_path, _ = build(tmp_path)
    url = pack.release_url(REPO, index["release_tag"], index_path.name)
    store = MemoryStore({url: b"previous version index"})
    with pytest.raises(ValueError, match="different bytes"):
        pack.publish_bundle(index, index_path, index_path.parent, store, run_id="1-1", checked_by="test")
    assert store.events == [("release", index["release_tag"])]


def test_mutated_pack_cannot_be_published_after_successful_local_restore(tmp_path):
    index, index_path, _ = build(tmp_path)
    (index_path.parent / index["packs"][0]["name"]).write_bytes(b"changed")
    store = MemoryStore()
    with pytest.raises(ValueError, match="changed"):
        pack.publish_bundle(index, index_path, index_path.parent, store, run_id="1-1", checked_by="test")
    assert store.events == []


def test_independent_restore_from_index_needs_no_website_or_collection_archive(tmp_path):
    index, index_path, _ = build(tmp_path)
    store = MemoryStore()
    result = pack.publish_bundle(index, index_path, index_path.parent, store, run_id="1-1", checked_by="test")
    receipt = json.loads(store.values[result["url"]])
    output = tmp_path / "restored"
    evidence = pack.restore_bundle(receipt["bundle_url"], receipt["bundle_sha256"], store=store, output=output, checked_by="test-restore")
    assert evidence["passed"]
    assert len(list((output / "objects").iterdir())) == 3
    assert store.calls == {part["url"]: 1 for part in index["packs"]}
    for part in index["packs"]:
        for obj in part["members"]:
            assert pack.file_proof(output / obj["path"])["sha256"] == obj["sha256"]


@pytest.mark.parametrize("mutation", ["missing", "extra", "duplicate", "large_static", "manifest", "path", "role"])
def test_restore_index_rejects_different_selection_or_limits(tmp_path, mutation):
    manifest, _ = fixture()
    index, _, _ = build(tmp_path)
    part = index["packs"][0]
    if mutation == "missing":
        part["members"].pop()
    elif mutation == "extra":
        obj = copy.deepcopy(part["members"][0]); obj["sha256"] = "a" * 64
        part["members"].append(obj)
    elif mutation == "duplicate":
        part["members"].append(part["members"][0])
    elif mutation == "large_static":
        index["static_limits"]["file_size_strictly_less_than"] = 1
    elif mutation == "manifest":
        index["publish_manifest"]["sha256"] = "0" * 64
    elif mutation == "path":
        part["members"][0]["path"] = "../escape"
    else:
        part["members"][0]["roles"] = ["quarantine"]
    part["member_count"] = len(part["members"])
    with pytest.raises(ValueError):
        pack.validate_index(index, manifest, REPO)


def test_cli_refuses_local_bulk_processing_before_any_network(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(pack, "GitHubStore", lambda _: pytest.fail("must not start networking"))
    with pytest.raises(SystemExit, match="2"):
        pack.main(["build", "--manifest-url", SOURCE, "--manifest-sha256", "a" * 64, "--version", "v1", "--output", str(tmp_path)])


def test_pack_bytes_are_reproducible_for_identical_manifest_and_sources(tmp_path):
    first, first_path, _ = build(tmp_path / "first")
    second, second_path, _ = build(tmp_path / "second")
    assert first == second
    assert first_path.read_bytes() == second_path.read_bytes()
    assert (first_path.parent / first["packs"][0]["name"]).read_bytes() == (second_path.parent / second["packs"][0]["name"]).read_bytes()


def test_github_metadata_requires_remote_digest_and_rechecks_actual_bytes(monkeypatch):
    value = b'{"synthetic":true}\n'
    store = pack.GitHubStore(REPO)
    metadata = {"source.tar.gz": {"browser_download_url": SOURCE, "digest": "sha256:" + pack.sha(value), "size": len(value)}}
    monkeypatch.setattr(store, "assets", lambda *args, **kwargs: metadata)
    monkeypatch.setattr(store, "open", lambda _: io.BytesIO(value))
    assert store.metadata(SOURCE, pack.sha(value)) == value
    monkeypatch.setattr(store, "open", lambda _: io.BytesIO(b"X" * len(value)))
    with pytest.raises(ValueError, match="SHA-256"):
        store.metadata(SOURCE, pack.sha(value))
    metadata["source.tar.gz"]["digest"] = None
    with pytest.raises(ValueError, match="lacks"):
        store.proof(SOURCE)


def test_github_asset_listing_uses_all_paginated_assets_and_caches_them(monkeypatch):
    store, calls = pack.GitHubStore(REPO), []
    def gh(*args):
        calls.append(args)
        if "--paginate" in args:
            return json.dumps([[{"name": "first"}], [{"name": "second"}]])
        return json.dumps({"id": 17})
    monkeypatch.setattr(store, "gh", gh)
    assert set(store.assets("tag")) == {"first", "second"}
    assert set(store.assets("tag")) == {"first", "second"}
    assert len(calls) == 2
    assert "--slurp" in calls[-1] and "per_page=100" in calls[-1][-1]


def test_github_existing_asset_cannot_be_clobbered(monkeypatch, tmp_path):
    store = pack.GitHubStore(REPO)
    path = tmp_path / "sample.tar.gz"; path.write_bytes(b"new")
    url = pack.release_url(REPO, "tag", path.name)
    row = {"name": path.name, "browser_download_url": url, "digest": "sha256:" + pack.sha(b"old"), "size": 3}
    monkeypatch.setattr(store, "assets", lambda *args, **kwargs: {path.name: row})
    monkeypatch.setattr(store, "gh", lambda *args: pytest.fail("must not overwrite"))
    with pytest.raises(ValueError, match="overwritten"):
        store.upload_checked("tag", path)


def test_access_denial_does_not_retry_or_create_alternative_release(monkeypatch):
    store, calls = pack.GitHubStore(REPO), []
    def denied(*args, **kwargs):
        calls.append(args)
        raise RuntimeError("HTTP 403")
    monkeypatch.setattr(store, "assets", denied)
    monkeypatch.setattr(store, "gh", lambda *args: pytest.fail("must not bypass denial"))
    with pytest.raises(RuntimeError, match="403"):
        store.ensure_release("tag")
    assert calls == [("tag",)]
