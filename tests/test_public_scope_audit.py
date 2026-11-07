"""Audit tests prove failures/pending states, not artwork content judgments."""
import copy
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("audit_public_scope", ROOT/"scripts/audit_public_scope.py")
audit_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit_module)


def fixture(tmp_path):
    review = {"source":"test", "source_id":"blocked", "original_sha256":"b"*64,
        "preview_sha256":"c"*64, "scope_status":"out_of_scope", "disposition":"exclude_from_v1",
        "reason":"Synthetic excluded nameplate", "content_scope_reason":"text_only_molecular_nameplate",
        "reviewed_by":"test fixture", "reviewed_at":"2026-11-07",
        "evidence":[{"uri":"https://example.invalid/review.png", "sha256":"d"*64}]}
    path=tmp_path/"review.json";path.write_text(json.dumps({"items":[review]}))
    policy=audit_module.load_scope_policy([path])
    good={"sha256":"a"*64, "preview":{"sha256":"e"*64}, "text_content":{"status":"present"}}
    good["public_scope"]=audit_module.evaluate_public_scope({},good,policy)
    public=[{"id":"keep", "files":[good], "variants":[{"id":"keep", "files":[{"sha256":"a"*64}]}]}]
    internal=copy.deepcopy(public)+[{"id":"blocked", "source":"test", "source_id":"blocked", "files":[{"sha256":"b"*64,"preview":{"sha256":"c"*64}}]}]
    for row in internal:row["catalog_visibility"]="internal_diagnostic"
    assembly={"summary":{"catalog_sha256":"f"*64}, "public_scope":{"policy_sha256":policy["sha256"],"internal_catalog":{"sha256":"1"*64,"records":2}}}
    return dict(catalog=public,assembly=assembly,catalog_sha256="f"*64,policy=policy,internal=internal,internal_sha256="1"*64)


def test_missing_pack_and_index_do_not_become_end_to_end_pass(tmp_path):
    result=audit_module.audit(**fixture(tmp_path))
    assert result["failed_checks"]==[]
    assert not result["scope_end_to_end_passed"]
    assert "publish_bundle.independent_restore" in result["pending_checks"]
    assert result["counts"]["nonpublic_by_reason"]=={"text_only_molecular_nameplate":1}
    assert result["counts"]["public_text_attribute_counts"]=={"present":1}


def test_stale_forbidden_alias_and_variant_reference_are_detected(tmp_path):
    args=fixture(tmp_path)
    file={"sha256":"b"*64,"preview":{"sha256":"c"*64},"source_records":[{"source":"alias","source_id":"different"}]}
    args["catalog"][0]["files"].append(file)
    args["catalog"][0]["variants"].append({"id":"dangling","files":[{"sha256":"2"*64}]})
    result=audit_module.audit(**args)
    assert {"catalog.current_policy","catalog.no_forbidden_files","catalog.no_dangling_variant_files","internal.binding_and_exact_public_set"} <= set(result["failed_checks"])


def test_actual_allowed_file_cannot_disappear_silently(tmp_path):
    args=fixture(tmp_path);args["catalog"]=[]
    result=audit_module.audit(**args)
    assert "internal.binding_and_exact_public_set" in result["failed_checks"]


def test_manifest_cannot_swap_preview_or_add_excluded_original(tmp_path):
    args=fixture(tmp_path)
    args["manifest"]={"catalog_sha256":"f"*64,"files":[
        {"sha256":"a"*64,"preview":{"sha256":"3"*64}},
        {"sha256":"b"*64,"preview":{"sha256":"c"*64}}]}
    result=audit_module.audit(**args)
    assert "download_manifest.allowed_exact_variants" in result["failed_checks"]
    assert not result["scope_end_to_end_passed"]


def test_pending_review_is_counted_separately_from_confirmed_exclusion(tmp_path):
    args=fixture(tmp_path)
    rule=args["policy"]["by_sha"]["b"*64][0]
    rule["status"]="pending_review";rule["content_scope_reason"]="whole_mechanism_boundary"
    result=audit_module.audit(**args)
    assert result["counts"]["nonpublic_by_status"]=={"pending_review":1}
    assert result["failed_checks"]==[]


def test_paired_labels_remain_while_nameplate_is_absent(tmp_path):
    args=fixture(tmp_path)
    args["paired_review"]={"items":[{"tile":i,"title":str(i),"original_sha256":"a"*64 if i<5 else "b"*64,"scope_status":"in_scope" if i<5 else "out_of_scope", "text_content":{"status":"present"}} for i in range(1,7)]}
    result=audit_module.audit(**args)
    assert all(item["supplied_stages_match"] for item in result["paired_commons"])
    assert "paired_commons.observed_text_preserved" not in result["failed_checks"]
    assert not result["scope_end_to_end_passed"]
    args["catalog"][0]["files"][0]["text_content"]={"status":"unknown"}
    result=audit_module.audit(**args)
    assert "paired_commons.observed_text_preserved" in result["failed_checks"]


def test_scope_audit_requires_independent_remote_reads_not_build_or_legacy_flags(tmp_path):
    from test_publish_pack import restored_fixture, fixture as pack_fixture, pack
    bundle, receipt, _, _, evidence = restored_fixture(tmp_path)
    args = fixture(tmp_path)
    args.update(bundle=bundle, bundle_sha256=receipt["bundle_sha256"], restore=evidence,
                manifest=pack_fixture()[0], manifest_sha256=bundle["publish_manifest"]["sha256"])
    def status():
        result = audit_module.audit(**args)
        return next(row["status"] for row in result["checks"] if row["check"] == "publish_bundle.independent_restore")
    assert status() == "passed"
    for change in ({"schema_version": 1}, {"operation": "build_verification"}):
        args["restore"] = {**evidence, **change, "passed": True}
        assert status() == "pending"
    args["restore"] = copy.deepcopy(evidence)
    args["restore"]["remote_reads"]["packs"][0]["sha256"] = "0" * 64
    assert status() == "failed"
    args["restore"] = copy.deepcopy(evidence)
    args["restore"]["restore_run"] = evidence["build_run"]
    assert status() == "failed"
    args["restore"] = evidence
    args["bundle"] = copy.deepcopy(bundle)
    args["bundle"].pop("build_run")
    args["bundle_sha256"] = pack.sha(pack.json_bytes(args["bundle"]))
    assert status() == "failed"


def test_scope_audit_explicit_equivalence_keeps_old_full_read_separate(tmp_path):
    from test_restore_equivalence import example, pack
    evidence, docs, _ = example()
    args = fixture(tmp_path)
    args.update(manifest=docs['new_manifest'], manifest_sha256=pack.sha(pack.json_bytes(docs['new_manifest'])),
        bundle=docs['historical_bundle'], bundle_sha256=pack.sha(pack.json_bytes(docs['historical_bundle'])),
        restore=docs['historical_restore'], restore_sha256=pack.sha(pack.json_bytes(docs['historical_restore'])),
        restored_manifest=docs['restored_manifest'], restored_manifest_sha256=pack.sha(pack.json_bytes(docs['restored_manifest'])),
        restore_equivalence=evidence, restore_equivalence_sha256=pack.sha(pack.json_bytes(evidence)))
    result = audit_module.audit(**args)
    checks = {x['check']: x['status'] for x in result['checks']}
    assert checks['publish_bundle.exact_manifest_members'] == 'passed'
    assert checks['publish_bundle.restore_equivalence'] == 'passed'
    assert result['restore_proof_type'] == 'equivalence_non_fresh_artwork_read'
    assert not result['full_artwork_restore_proof_directly_binds_current_manifest']
    assert not result['scope_end_to_end_passed']  # unrelated synthetic catalog is deliberately different
    args['restore_equivalence'] = {**evidence, 'new_artwork_archive_reads': 1}
    args['restore_equivalence_sha256'] = pack.sha(pack.json_bytes(args['restore_equivalence']))
    result = audit_module.audit(**args)
    assert 'publish_bundle.restore_equivalence' in result['failed_checks']


def test_scope_equivalence_does_not_accept_missing_historical_manifest(tmp_path):
    args = fixture(tmp_path)
    args.update(restore_equivalence={}, restore_equivalence_sha256='f' * 64)
    result = audit_module.audit(**args)
    assert 'publish_bundle.restore_equivalence' in result['failed_checks']
