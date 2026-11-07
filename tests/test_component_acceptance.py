"""Synthetic gate tests, not actual artwork or clinical procedure acceptance."""
from copy import deepcopy
import pytest
from mga_resources.component_acceptance import evaluate_components, validate_benchmark, THRESHOLDS, LEGACY_BENCHMARK_SHA


def fixture():
    h = lambda char: char * 64
    role = {'component_id': 'object', 'expected_concepts': ['synthetic object'], 'required_species': None,
            'visual_requirements': 'Inspect the depicted object.', 'exclude_matches': ['unrelated object']}
    benchmark = {'schema_version': 1, 'benchmark_version': 'synthetic', 'frozen': True, 'frozen_at': '2026-11-07T00:00:00Z',
                 'categories': [{'id': f'category-{i}'} for i in range(12)], 'thresholds': dict(THRESHOLDS), 'tasks': [], 'queries': []}
    reference = {'path': 'synthetic-observation.json', 'sha256': h('e')}
    source = {'source': 'synthetic', 'source_id': 'object', 'attribution': 'Synthetic fixture',
              'license': 'CC0-1.0', 'source_url': 'https://example.invalid/object'}
    archive = {'url': 'https://example.invalid/pack.tar.gz', 'member': 'object.svg'}
    preview = {'sha256': h('b'), 'size_bytes': 12, 'format': 'png', 'archive': {**archive, 'member': 'preview.png'}}
    file = {'sha256': h('a'), 'size_bytes': 40, 'format': 'svg', 'structure': 'pure_vector', 'archive': archive, 'preview': preview,
            'asset_ids': ['asset'], 'family_ids': ['family'], 'provenance': [source], 'public_scope': {'public_allowed': True}}
    original = {key: deepcopy(file[key]) for key in ('sha256', 'size_bytes', 'format', 'structure', 'archive', 'preview')}
    row = {'variant_id': 'asset:file-0', 'asset_id': 'asset', 'family_id': 'family', 'rank': 1, 'selected_file': original,
           'preview': preview, 'download': archive, 'selected_source': {key: source[key] for key in ('source', 'source_id', 'attribution')}}
    hashes = {'component_benchmark_sha256': h('c'), 'legacy_benchmark_sha256': LEGACY_BENCHMARK_SHA,
              'legacy_report_sha256': h('d'), 'scope_report_sha256': h('e'), 'decision_sha256': h('f'),
              'catalog_sha256': h('1'), 'publish_manifest_sha256': h('2'), 'search_core_sha256': h('3'), 'raw_results_sha256': h('4')}
    raw = {'bindings': {k: hashes[k] for k in ('component_benchmark_sha256', 'legacy_benchmark_sha256', 'catalog_sha256', 'search_core_sha256')}, 'queries': []}
    judgments = {'bindings': {k: hashes[k] for k in ('component_benchmark_sha256', 'raw_results_sha256', 'catalog_sha256', 'publish_manifest_sha256', 'search_core_sha256')},
                 'reviewer': 'Synthetic reviewer', 'reviewed_at': '2026-11-07', 'independent_from_metadata_matcher': True, 'queries': [], 'tasks': []}
    for i in range(60):
        task_id = f'task-{i}'
        benchmark['tasks'].append({'task_id': task_id, 'category': f'category-{i // 5}', 'legacy_concept_id': task_id,
            'change_type': 'synthetic', 'change_reason': 'Gate fixture only.', 'task_zh': '合成部件测试', 'task_en': 'Synthetic component test',
            'composition_requirements': ['Correct depicted object.'], 'limitations': ['No real image review.'], 'required_roles': [deepcopy(role)]})
        for language in ('zh', 'en'):
            qid = f'{task_id}-object-{language}'
            query = {'query_id': qid, 'task_id': task_id, 'component_id': 'object', 'language': language,
                     'query': 'synthetic object', 'filters': {}, 'limit': 10, 'mode': 'explore', 'seed': 'diagnostic-fixed-project',
                     'review_requirements': {key: deepcopy(role[key]) for key in ('expected_concepts', 'required_species', 'visual_requirements', 'exclude_matches')}}
            benchmark['queries'].append(query)
            actual = {key: deepcopy(query[key]) for key in ('query_id', 'task_id', 'component_id', 'language', 'review_requirements')}
            actual.update(request={key: deepcopy(query[key]) for key in ('query', 'filters', 'limit', 'mode', 'seed')}, exact_results=[deepcopy(row)])
            raw['queries'].append(actual)
            judgments['queries'].append({'query_id': qid, 'result_judgments': [{'variant_id': row['variant_id'], 'rank': 1,
                'original_sha256': h('a'), 'preview_sha256': h('b'), 'verdict': 'relevant', 'evidence_notes': 'Synthetic observation.', 'visual_evidence': [reference]}],
                'selected_variant_ids': [row['variant_id']], 'source_checks': [{'variant_id': row['variant_id'], 'source_record': deepcopy(source),
                    'source_evidence_checked': True, 'license_checked': True, 'species_matches': True, 'required_species': None,
                    'species_basis': 'No species requirement.', 'source_evidence': [reference]}], 'gap_reason': None})
            judgments['tasks'].append({'task_id': task_id, 'language': language, 'selected_roles': {'object': [row['variant_id']]},
                'composition_use_pass': True, 'composition_notes': 'Synthetic component use only.', 'limitations': ['No real artwork.'], 'composition_evidence': [reference]})
    baseline = {'required_bindings': {'catalog_sha256': h('1'), 'benchmark_sha256': LEGACY_BENCHMARK_SHA},
        'publish_manifest_sha256': h('2'), 'missing': [{'code': 'queries.threshold', 'detail': 'Historical 102/120.'}],
        'restore_passed': True, 'visual': {'curated_families': 60}, 'retrieval': {'negative_scenarios_passed': 6, 'successful': 102},
        'supplemental': {'required_bindings': {'search_core_sha256': h('3')}, 'missing': []}, 'acceptance_passed': False}
    return dict(benchmark=benchmark, raw=raw, judgments=judgments, baseline=baseline,
        scope={'catalog_sha256': h('1'), 'scope_end_to_end_passed': True, 'failed_checks': [], 'pending_checks': []},
        manifest={'catalog_sha256': h('1'), 'files': [file]}, hashes=hashes,
        freeze={key: hashes[key] for key in ('component_benchmark_sha256', 'legacy_report_sha256', 'scope_report_sha256', 'decision_sha256')},
        decision={'decided_by': 'user', 'first_release_retrieval_gate': 'separate_component_benchmark_under_current_collection_scope', 'acceptance_granted_by_this_decision': False})


def test_independent_gate_preserves_real_historical_failure():
    args = fixture()
    before = deepcopy(args['baseline'])
    result = evaluate_components(**args)
    assert result['acceptance_passed'] is True
    assert result['component_retrieval']['successful_task_language_units'] == 120
    assert result['historical_retrieval']['successful'] == 102
    assert result['historical_acceptance_passed'] is False
    assert args['baseline'] == before


@pytest.mark.parametrize('key', ['minimum_successful_task_language_units', 'minimum_successful_task_language_units_per_language', 'minimum_bilingually_covered_tasks_per_category'])
def test_threshold_cannot_be_lowered(key):
    data = fixture()['benchmark']
    data['thresholds'][key] -= 1
    with pytest.raises(ValueError, match='thresholds'):
        validate_benchmark(data)


def test_unfrozen_benchmark_rejected():
    data = fixture()['benchmark']
    data['frozen'] = False
    with pytest.raises(ValueError, match='Freeze'):
        validate_benchmark(data)


def test_child_role_cannot_be_omitted():
    data = fixture()['benchmark']
    data['tasks'][0]['required_roles'].append({**data['tasks'][0]['required_roles'][0], 'component_id': 'second'})
    with pytest.raises(ValueError, match='Every required role'):
        validate_benchmark(data)


def test_changed_scientific_requirement_rejected():
    args = fixture()
    args['raw']['queries'][0]['review_requirements']['required_species'] = 'rat'
    with pytest.raises(ValueError, match='requirements changed'):
        evaluate_components(**args)


def test_wrong_preview_and_excluded_file_rejected():
    for mutation in ('preview', 'scope'):
        args = fixture()
        if mutation == 'preview':
            args['raw']['queries'][0]['exact_results'][0]['preview'] = {'sha256': '9' * 64}
        else:
            args['manifest']['files'][0]['public_scope']['public_allowed'] = False
        with pytest.raises(ValueError):
            evaluate_components(**args)


def test_uncertain_result_is_not_a_selected_success():
    args = fixture()
    args['judgments']['queries'][0]['result_judgments'][0]['verdict'] = 'uncertain'
    with pytest.raises(ValueError, match='visually relevant'):
        evaluate_components(**args)


def test_mismatched_selected_source_rejected():
    args = fixture()
    args['judgments']['queries'][0]['source_checks'][0]['source_record']['source_id'] = 'different'
    with pytest.raises(ValueError, match='provenance'):
        evaluate_components(**args)


@pytest.mark.parametrize('field', ['license', 'author', 'source_url', 'license_url', 'upstream_revision'])
def test_wrong_returned_rights_field_rejected(field):
    args = fixture()
    args['raw']['queries'][0]['exact_results'][0]['selected_source'][field] = 'wrong returned value'
    with pytest.raises(ValueError, match='actual selected source'):
        evaluate_components(**args)


def test_labels_do_not_exclude_an_otherwise_usable_object():
    args = fixture()
    args['manifest']['files'][0]['text_content'] = {'status': 'present', 'cleanup_advice': 'task_dependent'}
    result = evaluate_components(**args)
    assert result['acceptance_passed'] is True


def test_complete_task_requires_all_actual_roles():
    args = fixture()
    review = args['judgments']['queries'][0]
    review.update(selected_variant_ids=[], source_checks=[], gap_reason='No usable component.')
    args['judgments']['tasks'][0]['selected_roles']['object'] = []
    with pytest.raises(ValueError, match='missing role'):
        evaluate_components(**args)


def test_nonretrieval_failure_cannot_be_waived():
    args = fixture()
    args['baseline']['missing'].append({'code': 'source.review', 'detail': 'Missing source.'})
    result = evaluate_components(**args)
    assert result['acceptance_passed'] is False
    assert any(row['code'] == 'materials.non_retrieval' for row in result['missing'])


def test_tasks_must_pass_both_languages_for_category_coverage():
    args = fixture()
    for task_id in ('task-0', 'task-1'):
        for row in args['judgments']['tasks']:
            if row['task_id'] == task_id and row['language'] == 'en':
                row['composition_use_pass'] = False
    result = evaluate_components(**args)
    assert result['component_retrieval']['successful_task_language_units'] == 118
    assert result['component_retrieval']['bilingually_covered_tasks_per_category']['category-0'] == 3
    assert result['acceptance_passed'] is False


def test_raw_rank_or_missing_judgment_rejected():
    args = fixture()
    args['judgments']['queries'][0]['result_judgments'] = []
    with pytest.raises(ValueError, match='Every actual result'):
        evaluate_components(**args)
