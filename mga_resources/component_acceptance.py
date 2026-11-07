"""Independent component-task gate; historical retrieval scores stay historical.

This validates recorded observations and exact evidence bindings. It does not
look at images, infer relevance, or declare that a composed procedure is valid.
"""
from collections import Counter, defaultdict
import hashlib
import json


LEGACY_BENCHMARK_SHA = 'ce0282afeb4afbcee8fdef1c6f937844ef569cabf16ba7909e22bf86b1096e92'
THRESHOLDS = {
    'top_k': 10,
    'minimum_successful_task_language_units': 108,
    'minimum_successful_task_language_units_per_language': 54,
    'minimum_bilingually_covered_tasks_per_category': 4,
}
LEGACY_COVERAGE_CODES = {'queries.threshold', 'queries.language', 'queries.category'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def valid_sha(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def canonical_sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def unique(rows, key, description):
    require(isinstance(rows, list), description + ' must be a list')
    result = {row.get(key): row for row in rows if isinstance(row, dict)}
    require(len(result) == len(rows) and all(nonempty(k) for k in result), description + ' identities are missing or duplicated')
    return result


def evidence_references(value):
    return (isinstance(value, list) and bool(value)
            and all(isinstance(ref, dict) and valid_sha(ref.get('sha256'))
                    and nonempty(ref.get('path') or ref.get('url')) for ref in value))


def validate_benchmark(benchmark, *, require_frozen=True):
    """Validate fixed task/role coverage before any retrieval is performed."""
    require(benchmark.get('schema_version') == 1, 'Unsupported component benchmark schema')
    require(nonempty(benchmark.get('benchmark_version')), 'Benchmark version is missing')
    if require_frozen:
        require(benchmark.get('frozen') is True and nonempty(benchmark.get('frozen_at')), 'Freeze the component requirements before retrieval')
    require(all(benchmark.get('thresholds', {}).get(k) == v for k, v in THRESHOLDS.items()), 'Component thresholds must retain the declared 108/54/4 coverage levels')
    categories = unique(benchmark.get('categories'), 'id', 'Categories')
    tasks = unique(benchmark.get('tasks'), 'task_id', 'Tasks')
    queries = unique(benchmark.get('queries'), 'query_id', 'Child queries')
    require(len(categories) == 12 and len(tasks) == 60, 'Require twelve categories and sixty tasks')
    require(len({task.get('legacy_concept_id') for task in tasks.values()}) == 60, 'Every historical task needs a distinct explained mapping')
    require(Counter(task.get('category') for task in tasks.values()) == Counter({key: 5 for key in categories}), 'Each category requires five tasks')
    expected = set()
    roles = {}
    for task_id, task in tasks.items():
        require(all(nonempty(task.get(key)) for key in ('legacy_concept_id', 'change_type', 'change_reason', 'task_zh', 'task_en')), 'Missing task purpose or historical mapping: ' + task_id)
        require(isinstance(task.get('composition_requirements'), list) and task['composition_requirements'], 'Task composition requirements missing: ' + task_id)
        require(isinstance(task.get('limitations'), list) and task['limitations'], 'Task limitations missing: ' + task_id)
        role_rows = unique(task.get('required_roles'), 'component_id', 'Required roles for ' + task_id)
        require(bool(role_rows), 'An empty task cannot pass')
        for component_id, role in role_rows.items():
            require(nonempty(role.get('visual_requirements')) and isinstance(role.get('expected_concepts'), list)
                    and role['expected_concepts'] and isinstance(role.get('exclude_matches'), list), 'Role requirements are incomplete')
            roles[(task_id, component_id)] = role
            expected.update((task_id, component_id, language) for language in ('zh', 'en'))
    observed = set()
    for query in queries.values():
        key = query.get('task_id'), query.get('component_id'), query.get('language')
        require(key in expected and key not in observed, 'Missing/duplicate/unexpected task-role-language query')
        observed.add(key)
        role = roles[key[:2]]
        require(nonempty(query.get('query')) and isinstance(query.get('filters'), dict), 'Explicit query and filters required')
        require(query.get('limit') == 10 and query.get('mode') == 'explore'
                and query.get('seed') == 'diagnostic-fixed-project', 'Use the frozen uniform top-10 request and seed')
        required = {field: role.get(field) for field in ('expected_concepts', 'required_species', 'visual_requirements', 'exclude_matches')}
        require(query.get('review_requirements') == required, 'Child review requirements must match their declared role')
    require(observed == expected, 'Every required role must be queried in both languages')
    return tasks, queries


def _file_pair(row, manifest_files):
    original = row.get('selected_file') or {}
    preview = row.get('preview') or {}
    item = manifest_files.get(original.get('sha256'))
    require(item is not None, 'Returned file is outside the allowed download manifest')
    require(row.get('asset_id') in item.get('asset_ids', []) and row.get('family_id') in item.get('family_ids', []), 'Returned file/family identity differs from the publication manifest')
    for key in ('sha256', 'size_bytes', 'format', 'structure', 'archive'):
        require(original.get(key) == item.get(key), 'Original file binding differs: ' + key)
    require(preview == item.get('preview') and original.get('preview') == preview,
            'Returned preview is not the exact allowed preview')
    require(row.get('download') == item.get('archive'), 'Returned download member differs')
    require(item.get('public_scope', {}).get('public_allowed') is True, 'Excluded/pending file cannot enter component acceptance')
    return item


def evaluate_components(benchmark, raw, judgments, baseline, scope, manifest, *, hashes, freeze, decision):
    """Return independent current gate and unchanged historical score context."""
    missing = []

    def need(condition, code, detail):
        if not condition:
            missing.append({'code': code, 'detail': detail})
        return bool(condition)

    tasks, expected_queries = validate_benchmark(benchmark)
    require(decision.get('decided_by') == 'user'
            and decision.get('first_release_retrieval_gate') == 'separate_component_benchmark_under_current_collection_scope'
            and decision.get('acceptance_granted_by_this_decision') is False, 'Explicit user choice for the independent component gate is required')
    require(hashes.get('legacy_benchmark_sha256') == LEGACY_BENCHMARK_SHA, 'Historical 120-query benchmark changed')
    require(freeze.get('component_benchmark_sha256') == hashes.get('component_benchmark_sha256'), 'Component benchmark differs from its pre-run freeze')
    for key in ('legacy_report_sha256', 'scope_report_sha256', 'decision_sha256'):
        require(valid_sha(hashes.get(key)) and freeze.get(key) == hashes[key], 'Frozen input evidence differs: ' + key)
    catalog_sha = manifest.get('catalog_sha256')
    require(valid_sha(catalog_sha) and catalog_sha == hashes.get('catalog_sha256'), 'Manifest catalog version differs')
    require(baseline.get('required_bindings', {}).get('catalog_sha256') == scope.get('catalog_sha256') == catalog_sha, 'Historical material checks and current scope must bind this catalog')
    require(baseline.get('publish_manifest_sha256') == hashes.get('publish_manifest_sha256'), 'Material checks bind another download manifest')
    require(baseline.get('required_bindings', {}).get('benchmark_sha256') == LEGACY_BENCHMARK_SHA, 'Historical result must retain the original benchmark')
    core_sha = baseline.get('supplemental', {}).get('required_bindings', {}).get('search_core_sha256')
    require(core_sha == hashes.get('search_core_sha256') and valid_sha(core_sha), 'Revalidate material/search supplemental evidence after a core change')
    need(isinstance(baseline.get('missing'), list) and all(row.get('code') in LEGACY_COVERAGE_CODES for row in baseline['missing']), 'materials.non_retrieval', 'Existing material acceptance has unresolved checks beyond historical query coverage')
    need(baseline.get('restore_passed') is True, 'materials.restore', 'Valid current recovery evidence is required')
    need(baseline.get('visual', {}).get('curated_families', 0) >= 60, 'materials.curated', 'Sixty actual curated families are required')
    need(baseline.get('retrieval', {}).get('negative_scenarios_passed') == 6, 'materials.negatives', 'All six independently reviewed negative scenarios are required')
    need(isinstance(baseline.get('supplemental'), dict) and baseline['supplemental'].get('missing') == [], 'materials.supplement', 'Existing color/style/diversity checks must remain valid')
    need(scope.get('scope_end_to_end_passed') is True and scope.get('failed_checks') == [] and scope.get('pending_checks') == [], 'materials.scope', 'Current catalog/index/download scope audit must pass')
    for key in ('component_benchmark_sha256', 'legacy_benchmark_sha256', 'catalog_sha256', 'search_core_sha256'):
        require(raw.get('bindings', {}).get(key) == hashes.get(key), 'Raw cloud replay binding differs: ' + key)
    for key in ('component_benchmark_sha256', 'raw_results_sha256', 'catalog_sha256', 'publish_manifest_sha256', 'search_core_sha256'):
        require(judgments.get('bindings', {}).get(key) == hashes.get(key), 'Review evidence binding differs: ' + key)
    require(judgments.get('independent_from_metadata_matcher') is True and nonempty(judgments.get('reviewer'))
            and nonempty(judgments.get('reviewed_at')), 'Independent actual review identity and date are required')
    raw_queries = unique(raw.get('queries'), 'query_id', 'Actual replay queries')
    reviewed_queries = unique(judgments.get('queries'), 'query_id', 'Reviewed queries')
    require(set(raw_queries) == set(reviewed_queries) == set(expected_queries), 'All child queries require complete actual results and review, including empty ones')
    manifest_files = {f['sha256']: f for f in manifest['files']}
    require(len(manifest_files) == len(manifest['files']), 'Publication manifest has duplicate originals')
    component_passes = {}
    selected_for_query = {}
    total_positions = 0
    for query_id, expected in expected_queries.items():
        actual, reviewed = raw_queries[query_id], reviewed_queries[query_id]
        require(all(actual.get(key) == expected.get(key) for key in ('task_id', 'component_id', 'language', 'review_requirements')), 'Task/role/review requirements changed in replay: ' + query_id)
        request = {key: expected[key] for key in ('query', 'filters', 'limit', 'mode', 'seed')}
        require(actual.get('request') == request, 'Actual request differs from frozen query: ' + query_id)
        rows = actual.get('exact_results')
        require(isinstance(rows, list) and len(rows) <= 10, 'Actual results must be a complete top-10 page')
        variants = unique(rows, 'variant_id', 'Returned variants')
        require([r.get('rank') for r in rows] == list(range(1, len(rows) + 1))
                and len({r.get('family_id') for r in rows}) == len(rows), 'Rank sequence or family grouping differs')
        observations = reviewed.get('result_judgments')
        require(isinstance(observations, list) and [r.get('variant_id') for r in observations] == list(variants), 'Every actual result must have an ordered per-result judgment')
        verdicts = {}
        for row, observation in zip(rows, observations):
            _file_pair(row, manifest_files)
            require(observation.get('rank') == row['rank']
                    and observation.get('original_sha256') == row['selected_file']['sha256']
                    and observation.get('preview_sha256') == row['preview']['sha256'], 'Observation does not bind the displayed file/preview')
            require(observation.get('verdict') in {'relevant', 'irrelevant', 'uncertain'}
                    and nonempty(observation.get('evidence_notes')) and evidence_references(observation.get('visual_evidence')), 'An actual per-result visual observation is required')
            verdicts[row['variant_id']] = observation['verdict']
        total_positions += len(rows)
        selected = reviewed.get('selected_variant_ids')
        require(isinstance(selected, list) and len(selected) == len(set(selected))
                and all(key in variants and verdicts[key] == 'relevant' for key in selected), 'Selected components must be actually returned and visually relevant')
        checks = unique(reviewed.get('source_checks', []), 'variant_id', 'Selected source checks')
        require(set(checks) == set(selected), 'Every selected component requires an exact source/license check')
        for variant_id in selected:
            row, check = variants[variant_id], checks[variant_id]
            item = manifest_files[row['selected_file']['sha256']]
            source = check.get('source_record')
            require(source in item.get('provenance', []) and isinstance(source, dict), 'Selected source record is not in the allowed file provenance')
            returned_source = row.get('selected_source') or {}
            source_fields = ['source', 'source_id', 'attribution'] + [key for key in ('license', 'author', 'source_url', 'license_url', 'upstream_revision') if key in returned_source]
            require(all(source.get(key) == returned_source.get(key) for key in source_fields), 'Reviewed provenance differs from the actual selected source')
            require(nonempty(source.get('license')) and nonempty(source.get('source_url')),
                    'Selected component must retain license and source URL')
            require(all(check.get(key) is True for key in ('source_evidence_checked', 'license_checked', 'species_matches'))
                    and check.get('required_species') == expected['review_requirements'].get('required_species')
                    and nonempty(check.get('species_basis')) and evidence_references(check.get('source_evidence')),
                    'Selected source, license or applicable species confirmation is incomplete')
        if not selected:
            require(nonempty(reviewed.get('gap_reason')), 'An unsuccessful child query must explain its actual gap')
        component_passes[query_id] = bool(selected)
        selected_for_query[query_id] = selected
    task_reviews = judgments.get('tasks')
    require(isinstance(task_reviews, list), 'Task-language use reviews are missing')
    reviewed_units = {(r.get('task_id'), r.get('language')): r for r in task_reviews}
    expected_units = {(key, language) for key in tasks for language in ('zh', 'en')}
    require(len(reviewed_units) == len(task_reviews) and set(reviewed_units) == expected_units,
            'All 120 task-language units require composition-use review')
    query_groups = defaultdict(list)
    for qid, query in expected_queries.items():
        query_groups[(query['task_id'], query['language'])].append(qid)
    successes = set()
    failures = []
    for unit, qids in query_groups.items():
        task, review = tasks[unit[0]], reviewed_units[unit]
        selected_roles = {expected_queries[qid]['component_id']: selected_for_query[qid] for qid in qids}
        require(review.get('selected_roles') == selected_roles, 'Task use review must bind the exact selected role candidates')
        require(nonempty(review.get('composition_notes')) and isinstance(review.get('limitations'), list)
                and review['limitations'], 'State composition use and its scientific limits')
        require(review.get('composition_use_pass') is None or type(review.get('composition_use_pass')) is bool, 'Use review must be true, false or pending')
        complete_roles = all(component_passes[qid] for qid in qids)
        if review.get('composition_use_pass') is True:
            require(complete_roles and evidence_references(review.get('composition_evidence')),
                    'A task cannot pass with a missing role or missing actual composition-use evidence')
        if complete_roles and review.get('composition_use_pass') is True:
            successes.add(unit)
        else:
            failures.append({'task_id': unit[0], 'language': unit[1], 'category': task['category'],
                             'unmet_component_queries': [qid for qid in qids if not component_passes[qid]],
                             'composition_use_pass': review.get('composition_use_pass'),
                             'notes': review['composition_notes']})
    languages = {lang: sum(language == lang for _, language in successes) for lang in ('zh', 'en')}
    covered = Counter(task['category'] for task_id, task in tasks.items()
                      if (task_id, 'zh') in successes and (task_id, 'en') in successes)
    need(len(successes) >= 108, 'components.threshold', f'{len(successes)}/120 successful task-language units; require 108')
    for language, count in languages.items():
        need(count >= 54, 'components.language', f'{language}: {count}/60; require 54')
    for category in (c['id'] for c in benchmark['categories']):
        need(covered[category] >= 4, 'components.category', f'{category}: {covered[category]}/5 bilingual tasks; require 4')
    return {
        'schema_version': 1, 'gate_kind': 'component_retrieval_with_existing_material_acceptance',
        'benchmark_version': benchmark['benchmark_version'], 'bindings': hashes,
        'historical_retrieval': baseline['retrieval'], 'historical_acceptance_passed': baseline.get('acceptance_passed'),
        'historical_missing': baseline.get('missing'), 'historical_result_modified': False,
        'component_retrieval': {'tasks': 60, 'task_language_units': 120,
            'child_queries': len(expected_queries), 'reviewed_positions': total_positions,
            'successful_task_language_units': len(successes), 'successful_by_language': languages,
            'bilingually_covered_tasks_per_category': {c['id']: covered[c['id']] for c in benchmark['categories']},
            'failed_task_language_units': failures},
        'missing': missing, 'acceptance_passed': not missing,
        'website_gate': 'open_for_implementation' if not missing else 'closed_pending_component_acceptance',
        'limitations': ['Component availability and reviewed use do not establish a completed experimental procedure.',
                       'Task-specific measurements, routes, mechanisms, labels and data plots remain the drawing task responsibility.',
                       'This evaluator verifies evidence consistency; it does not perform image or native-editor review.'],
    }
