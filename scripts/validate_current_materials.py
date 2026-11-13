"""Bind new material checks separately from the immutable historical score.

This consumes existing cloud results and actual review records. No retrieval,
artwork download, new visual observation, or historical score mutation occurs.
"""
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'scripts')]
from mga_resources.catalog import search_policy_metadata
from mga_resources.component_acceptance import require, valid_sha, LEGACY_BENCHMARK_SHA
from mga_resources.supplemental_acceptance import evaluate_supplement
from restore_equivalence import validate_restore_equivalence, compare_manifests
from bind_current_controls import OLD_NEGATIVE, OLD_NEGATIVE_REVIEW, OLD_SOURCE, verify_local


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_current_materials(config_path, *, manifest, manifest_sha256, baseline, freeze, raw_bindings):
    config=json.loads(Path(config_path).read_bytes())
    names={'historical_manifest','restored_manifest','bundle','restore','equivalence','scope',
           'supplemental','negatives','controls_summary','negative_raw'}
    require(set(config.get('documents',{}))==names,'Current material configuration has missing or unexpected documents')
    docs={};refs={}
    for name,reference in config['documents'].items():
        p=(ROOT/reference['path']).resolve()
        require(p.is_relative_to(ROOT.resolve()) and p.is_file(),'Material evidence unavailable: '+name)
        require(valid_sha(reference.get('sha256')) and digest(p)==reference['sha256'],'Material evidence bytes changed: '+name)
        docs[name]=json.loads(p.read_bytes());refs[name]=reference
    historical=docs['historical_manifest'];scope=docs['scope'];equivalence=docs['equivalence']
    require(refs['historical_manifest']['sha256']==freeze['publish_manifest_sha256']==baseline['publish_manifest_sha256'],
            'Historical download manifest differs from the frozen report')
    require(historical['catalog_sha256']==baseline['required_bindings']['catalog_sha256']==freeze['catalog_sha256'],
            'Historical catalog identity changed')
    # This is the basis for reusing curated observations, rights and unchanged
    # original/preview bytes. Comparing only counts or file hashes is insufficient.
    identity=compare_manifests(manifest,historical)
    validate_restore_equivalence(equivalence,manifest,docs['restored_manifest'],docs['bundle'],docs['restore'],
        new_manifest_sha256=manifest_sha256,restored_manifest_sha256=refs['restored_manifest']['sha256'],
        bundle_sha256=refs['bundle']['sha256'],restore_evidence_sha256=refs['restore']['sha256'])
    require(scope['catalog_sha256']==manifest['catalog_sha256'] and scope['restore_equivalence_sha256']==refs['equivalence']['sha256'],
            'Scope report binds another catalog/equivalence proof')
    scope_inputs=scope['input_proofs']
    for role,key in [('assembly_report','assembly_report_sha256'),('catalog.sqlite','sqlite_sha256'),('catalog.jsonl','catalog_sha256')]:
        require(scope_inputs[role]['sha256']==raw_bindings[key],'Scope and actual component replay inputs differ: '+role)
    require(scope_inputs['new_manifest']['sha256']==manifest_sha256,'Scope download manifest differs')
    controls=docs['controls_summary'];policy=search_policy_metadata()
    bindings=controls['bindings']
    require(all(bindings.get(k)==v for k,v in policy.items()),'Current controls do not bind the checked-out search policy')
    for key in ('catalog_sha256','sqlite_sha256','assembly_report_sha256',*policy):
        require(bindings.get(key)==raw_bindings.get(key),'Controls and component replay differ: '+key)
    require(bindings['benchmark_sha256']==LEGACY_BENCHMARK_SHA,'Control benchmark identity changed')
    for name in ('supplemental','negatives'):
        require(all(docs[name]['bindings'].get(k)==v for k,v in bindings.items()),'Current control evidence binding differs: '+name)
    files={f['sha256']:f for f in manifest['files']}
    supplement=evaluate_supplement(docs['supplemental'],catalog_sha256=manifest['catalog_sha256'],
        benchmark_sha256=LEGACY_BENCHMARK_SHA,manifest_files=files)
    # Reconfirm the normalized review against the actual cloud rows and the
    # unchanged historical visual/source review. This is not a new image review.
    old_raw=json.loads(OLD_NEGATIVE.read_bytes());old_review=json.loads(OLD_NEGATIVE_REVIEW.read_bytes());old_source=json.loads(OLD_SOURCE.read_bytes())
    require(old_review['source_binding_audit']['sha256']==digest(OLD_SOURCE) and old_source['errors']==[],
        'Historical negative source evidence changed')
    old_by={s['scenario']['id']:s for s in old_raw['scenarios']}
    reviews_by={s['scenario_id']:s for s in old_review['negative_scenarios']}
    actual={s['scenario']['id']:s for s in docs['negative_raw']['scenarios']}
    current={s['scenario_id']:s for s in docs['negatives']['negative_scenarios']}
    require(len(current)==6 and set(current)==set(actual)==set(old_by)==set(reviews_by),'All six exact negative scenarios are required')
    positions=0
    for sid,review in current.items():
        fresh,previous,prior=actual[sid],old_by[sid],reviews_by[sid]
        require(fresh['scenario']==previous['scenario'] and fresh['effective_request']==previous['effective_request']==review['request']==prior['request'],
            'Negative scenario or request changed')
        require(not fresh.get('execution_error') and review['pass'] is True and prior['pass'] is True,'Negative scenario failed')
        require(review['prior_review']['sha256']==digest(OLD_NEGATIVE_REVIEW)
            and review['current_raw_evidence']['sha256']==refs['negative_raw']['sha256'],'Negative actual evidence linkage differs')
        rows=fresh['raw_result']['results'];before=previous['raw_result']['results']
        require(len(rows)==len(before) and review['observed_candidate_ids']==prior['observed_candidate_ids']==[r['variant_id'] for r in rows],
            'Negative viewed file order differs')
        require(review['candidates']==prior['candidates'] and review['evidence_notes']==prior['evidence_notes'],
            'Negative visual observations were rewritten')
        for a,b in zip(rows,before):
            require(all(a[k]==b[k] for k in ('variant_id','asset_id','family_id','selected_file','selected_source','preview','download')),
                'Negative exact file/source differs; new review required')
            item=files[a['selected_file']['sha256']]
            require(item['archive']==a['download'] and item['preview']==a['preview'] and item['provenance']==a['selected_file']['source_records'],
                'Negative file/source differs from current publication')
        for candidate in prior['candidates']:verify_local(candidate['visual_evidence']['review_document'])
        positions+=len(rows)
    require(positions==40,'Negative result coverage incomplete')
    missing=[]
    if supplement['missing']:missing.append({'code':'materials.current_supplement','detail':supplement['missing']})
    if not (scope.get('scope_end_to_end_passed') is True and scope['failed_checks']==[] and scope['pending_checks']==[]):
        missing.append({'code':'materials.current_scope','detail':'Current catalog/index scope audit did not pass.'})
    return {'schema_version':1,'validation_method':'existing_current_cloud_evidence_and_exact_historical_file_identity',
        'bindings':{**raw_bindings,'publish_manifest_sha256':manifest_sha256},
        'historical_manifest_sha256':refs['historical_manifest']['sha256'],
        'full_historical_file_records_equal':identity['complete_file_records_equal'],
        'restore_equivalence_validated':True,'current_scope_passed':scope.get('scope_end_to_end_passed') is True,
        'supplemental':supplement,'negative_scenarios_passed':6,'negative_positions_reused':positions,
        'verified_input_documents':refs,'config_sha256':digest(config_path),'missing':missing,
        'new_queries_executed':0,'new_visual_inspections':0,'historical_report_modified':False,
        'limits':['This verifies stored evidence; it is not a fresh whole-artwork restore or new adoption/visual review.']}
