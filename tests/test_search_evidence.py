import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from mga_resources.catalog import finalize, rebuild_search_index, search_page

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("validate_search_behavior",ROOT/"scripts/validate_search_behavior.py")
behavior=importlib.util.module_from_spec(spec)
spec.loader.exec_module(behavior)


def test_adoption_cannot_be_inferred_from_rank_or_empty_decision_template():
    project={"project_id":"validation-1","seed":"seed-1","new_top_family_ids":["mouse-a"]}
    assert behavior.attach_ledger(project,None,["mouse-a"],"a"*64)["adopted_family_ids"]==[]
    ledger={"catalog_sha256":"a"*64,"exercise":"validation_exercise","projects":[{"project_id":"validation-1","adopted_family_ids":["mouse-a"],"decision":{"reviewer":" ","observations":" ","reviewed_evidence":[{}]}}]}
    with pytest.raises(ValueError,match="actual review evidence"):
        behavior.attach_ledger(project,ledger,["mouse-a"],"a"*64)


def test_adoption_uses_bound_reviewed_decision_and_rejects_outside_pool():
    project={"project_id":"validation-1","seed":"seed-1","new_top_family_ids":["mouse-a"]}
    entry={"project_id":"validation-1","adopted_family_ids":["mouse-a"],"decision":{"reviewer":"Reviewer","observations":"Actual decision recorded","reviewed_evidence":[{"uri":"review.json","sha256":"b"*64}]}}
    ledger={"catalog_sha256":"a"*64,"exercise":"validation_exercise","projects":[entry]}
    assert behavior.attach_ledger(project,ledger,["mouse-a"],"a"*64)["adopted_family_ids"]==["mouse-a"]
    with pytest.raises(ValueError,match="outside the qualified pool"):
        behavior.attach_ledger(project,ledger,["another"],"a"*64)


def test_rebuilding_frozen_index_never_rewrites_catalog(tmp_path):
    finalize([],tmp_path)
    path=tmp_path/"catalog.jsonl"
    before=path.read_bytes()
    version=rebuild_search_index(path,tmp_path/"rebuilt.sqlite")
    assert path.read_bytes()==before
    assert version==hashlib.sha256(before).hexdigest()
    assert search_page(tmp_path/"rebuilt.sqlite","mouse")["bindings"]["catalog_version"]==version


def test_supplemental_report_cannot_borrow_another_catalog_analysis_version(tmp_path):
    catalog=tmp_path/"catalog.jsonl"
    catalog.write_text("\n",encoding="utf-8")
    assembly=tmp_path/"report.json"
    assembly.write_text(json.dumps({"summary":{"catalog_sha256":"f"*64},"appearance":{"analysis_version":"appearance-1.1.0"}}),encoding="utf-8")
    with pytest.raises(ValueError,match="Assembly report does not bind"):
        behavior.validate(catalog,assembly,tmp_path/"out")
