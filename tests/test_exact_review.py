import copy
import importlib.util
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
