import copy
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("build_exact_review", Path(__file__).resolve().parents[1]/"scripts/build_exact_review.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def row():
    sha = "a" * 64
    url = "https://github.com/owner/repo/releases/download/release/source-b0000.tar.gz"
    return {"source": "fixture", "source_id": "one", "asset_id": "fixture-one", "review_question": "Actual visible object?",
            "original_sha256": sha, "original_archive": {"url": url, "member": f"files/aa/{sha}.svg"},
            "preview": {"sha256": "b"*64, "archive": {"url": url, "member": f"previews/{sha}.png"}}}


def test_exact_review_keeps_file_binding_without_inventing_acceptance():
    item = row()
    result = module.validate_plan({"schema_version": 1, "files": [item]})
    assert result[item["original_sha256"]]["preview"] == item["preview"]
    assert result[item["original_sha256"]]["selection_is_acceptance"] is False
    assert "scientific_assertions" not in result[item["original_sha256"]]


def test_exact_review_refuses_wrong_member_duplicates_missing_question_and_unbounded_plan():
    bad = row(); bad["preview"]["archive"]["member"] = "previews/../../other.png"
    with pytest.raises(ValueError, match="archive member"):
        module.validate_plan({"schema_version": 1, "files": [bad]})
    with pytest.raises(ValueError, match="Duplicate"):
        module.validate_plan({"schema_version": 1, "files": [row(), row()]})
    bad = row(); bad["review_question"] = ""
    with pytest.raises(ValueError, match="review question"):
        module.validate_plan({"schema_version": 1, "files": [bad]})
    with pytest.raises(ValueError, match="1 to 20"):
        module.validate_plan({"schema_version": 1, "files": [copy.deepcopy(row()) for _ in range(21)]})


@pytest.mark.parametrize("publish_tag,expected", [(None, "release"), ("acceptance-20261107", "acceptance-20261107")])
def test_exact_review_publishes_evidence_separately_without_switching_source_release(tmp_path, monkeypatch, publish_tag, expected):
    """Exercise the main routing with synthetic local bytes; no cloud artwork is fetched."""
    import assemble
    item = row()
    plan = tmp_path/"data/review-plans/test.json"
    plan.parent.mkdir(parents=True)
    plan.write_text(json.dumps({"schema_version": 1, "files": [item]}), encoding="utf-8")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    for key, value in {"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": "owner/repo", "GITHUB_RUN_ID": "synthetic", "GITHUB_RUN_ATTEMPT": "1"}.items():
        monkeypatch.setenv(key, value)
    source_calls, publish_calls, extracted = [], [], []
    url = item["original_archive"]["url"]
    def assets(repository, tag):
        source_calls.append((repository, tag))
        return {"source-b0000.tar.gz": {"browser_download_url": url, "digest": "sha256:"+"c"*64}}
    def fetch(mapping, manifest):
        extracted.append((mapping, manifest))
        return {item["original_sha256"]: b"synthetic-preview"}, [], []
    def render(rows, payloads, path, font):
        path.write_bytes(b"synthetic-sheet")
        return {"path": path.name, "sha256": module.sha(path.read_bytes())}
    monkeypatch.setattr(assemble, "list_release_assets", assets)
    monkeypatch.setattr(module, "fetch_selected", fetch)
    monkeypatch.setattr(module, "render_sheet", render)
    monkeypatch.setattr(module, "gh", lambda *args: publish_calls.append(args))
    argv = ["build_exact_review.py", "--plan", "data/review-plans/test.json", "--release-tag", "release", "--publish"]
    if publish_tag:
        argv += ["--publish-tag", publish_tag]
    monkeypatch.setattr(module.sys, "argv", argv)
    module.main()
    assert source_calls == [("owner/repo", "release")]
    assert extracted[0][0]["candidates"][item["original_sha256"]]["original_archive"] == item["original_archive"]
    assert set(extracted[0][1]) == {url}
    assert len(publish_calls) == 1 and publish_calls[0][:3] == ("release", "upload", expected)
    summary = json.loads((tmp_path/"build/exact-review/synthetic-1/exact-review-synthetic-1.summary.json").read_text())
    assert summary["source_release_tag"] == "release" and summary["publish_tag"] == expected
    assert summary["files"] == 1 and not summary["preview_errors"]
