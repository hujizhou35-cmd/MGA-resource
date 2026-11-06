"""Cross-Release evidence tests use small synthetic metadata, never artwork."""
import argparse
import hashlib
import importlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from review_releases import add_release_options, load_review_inputs, release_bindings


def asset(tag, name="same.tar.gz", digest="a"):
    return {"browser_download_url": f"https://github.com/example/library/releases/download/{tag}/{name}",
            "digest": "sha256:" + digest * 64, "size": 100}


def test_archive_names_can_repeat_across_releases_without_changing_metadata():
    calls = []
    metadata = {"catalog.jsonl": {"digest": "catalog"}, "same.tar.gz": asset("metadata")}
    old = {"same.tar.gz": asset("raw", digest="b"), "catalog.jsonl": {"digest": "old-catalog"}}
    def listing(repository, tag):
        calls.append((repository, tag))
        return {"metadata": metadata, "raw": old}[tag]
    selected, archives, tags = load_review_inputs("example/library", "metadata", ["raw", "raw", "metadata"], list_assets=listing)
    assert selected is metadata
    assert tags == ["metadata", "raw"]
    assert calls == [("example/library", "metadata"), ("example/library", "raw")]
    assert archives == {asset("metadata")["browser_download_url"]: metadata["same.tar.gz"],
                        asset("raw")["browser_download_url"]: old["same.tar.gz"]}


def test_conflicting_proofs_for_one_archive_url_fail():
    left, right = asset("raw"), asset("raw", digest="b")
    with pytest.raises(ValueError, match="Conflicting archive evidence"):
        load_review_inputs("example/library", "one", ["two"],
                           list_assets=lambda repo, tag: {"same.tar.gz": left if tag == "one" else right})


def test_legacy_one_release_and_repeated_archive_options():
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-tag")
    add_release_options(parser)
    args = parser.parse_args(["--release-tag", "legacy"])
    assert release_bindings(args, ["legacy"]) == {"metadata_release_tag": "legacy", "archive_release_tags": ["legacy"], "publish_tag": "legacy"}
    args = parser.parse_args(["--release-tag", "metadata", "--publish-tag", "output",
                             "--archive-release-tag", "old", "--archive-release-tag", "new"])
    assert args.archive_release_tag == ["old", "new"]
    assert release_bindings(args, ["metadata", "old", "new"])["publish_tag"] == "output"


@pytest.mark.parametrize("script", ["build_content_scope_review", "build_appearance_review"])
def test_catalog_review_reads_metadata_and_archive_separately_then_publishes_output(tmp_path, monkeypatch, script):
    module = importlib.import_module(script)
    import assemble
    monkeypatch.setattr(module, "ROOT", tmp_path)
    for key, value in {"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": "example/library",
                       "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1"}.items():
        monkeypatch.setenv(key, value)
    catalog_bytes = b'{}\n'
    report_bytes = json.dumps({"summary": {"catalog_sha256": hashlib.sha256(catalog_bytes).hexdigest()}}).encode()
    downloads, listings, uploads, renders = [], [], [], []
    def listing(repo, tag):
        listings.append(tag)
        if tag == "metadata":
            return {"assembly-1-1.catalog.jsonl": {"data": catalog_bytes}, "assembly-1-1.report.json": {"data": report_bytes}}
        assert tag == "raw"
        return {"same.tar.gz": asset("raw")}
    def download(name, proof, incoming, repo, tag):
        downloads.append((name, tag))
        path = incoming / name
        path.write_bytes(proof["data"])
        return path
    monkeypatch.setattr(assemble, "list_release_assets", listing)
    monkeypatch.setattr(module, "download_checked", download)
    monkeypatch.setattr(module, "gh", lambda *args: uploads.append(args))
    if script == "build_content_scope_review":
        monkeypatch.setattr(module, "select_candidates", lambda *a, **kw: ({"selected": {}}, {"total_candidate_files": 1, "selected_files": 1}))
        def render(records, manifest, output, **kwargs):
            renders.append(set(manifest))
            output.mkdir(parents=True)
            return {"tiles": [], "tile_count": 0, "sheets": [], "preview_errors": []}
        monkeypatch.setattr(module, "build_review", render)
        folder, prefix = "content-scope-review", "content-scope-review"
    else:
        monkeypatch.setattr(module, "select_candidates", lambda *a: {"preview_files": 1, "preview_errors": []})
        def render(plan, manifest, output, **kwargs):
            renders.append(set(manifest))
            output.mkdir(parents=True)
            (output / "synthetic.json").write_text("{}")
        monkeypatch.setattr(module, "build_pack", render)
        folder, prefix = "appearance-review", "appearance-review"
    monkeypatch.setattr(module.sys, "argv", [script, "--release-tag", "metadata", "--assembly-prefix", "assembly-1-1",
                                              "--archive-release-tag", "raw", "--publish-tag", "output", "--publish"])
    module.main()
    assert listings == ["metadata", "raw"]
    assert downloads == [("assembly-1-1.catalog.jsonl", "metadata"), ("assembly-1-1.report.json", "metadata")]
    assert renders == [{asset("raw")["browser_download_url"]}]
    assert len(uploads) == 1 and uploads[0][:3] == ("release", "upload", "output")
    summary = json.loads((tmp_path / f"build/{folder}/123-1/{prefix}-123-1.summary.json").read_text())
    assert summary["bindings"]["metadata_release_tag"] == "metadata"
    assert summary["bindings"]["archive_release_tags"] == ["metadata", "raw"]
    assert summary["bindings"]["publish_tag"] == "output"
