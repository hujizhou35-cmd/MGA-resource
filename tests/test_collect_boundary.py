"""The cloud-only boundary and archive provenance are user requirements."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import os
import hashlib
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
