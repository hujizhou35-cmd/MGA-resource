"""Rebind actual observations to an existing replay; never run a search or view.

Only unchanged requests, purposes, physical files, provenance and source science
may inherit a verdict. Derived alias-term changes are recorded independently.
Unknown rows stay pending, not 'uncertain' observations that never happened.
"""
from __future__ import annotations
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts')]
from mga_resources.component_acceptance import _file_pair, require
from accept_component_release import verify_observation_files


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_bytes())


def reference(path):
    path = Path(path).resolve()
    return {'path': path.relative_to(ROOT).as_posix(), 'sha256': digest(path)}


def reusable_result(previous, current):
    """Same file/source science; alias lists alone are not new medical facts."""
    for key in ('asset_id', 'family_id', 'variant_id', 'title', 'selected_source',
                'selected_file', 'preview', 'download'):
        if previous.get(key) != current.get(key):
            return False, 'changed:' + key
    old, new = copy.deepcopy(previous['selected_science']), copy.deepcopy(current['selected_science'])
    old_terms = old.get('primary_object', {}).pop('terms', [])
    new_terms = new.get('primary_object', {}).pop('terms', [])
    if old != new:
        return False, 'changed:science_beyond_derived_alias_terms'
    return True, {'derived_alias_terms_changed': old_terms != new_terms,
                  'previous_terms': old_terms, 'current_terms': new_terms}


def bridge(old_raw, new_raw, documents, manifest):
    for key in ('component_benchmark_sha256', 'legacy_benchmark_sha256', 'catalog_sha256'):
        require(old_raw['bindings'][key] == new_raw['bindings'][key], 'Changed frozen input: ' + key)
    require(manifest['catalog_sha256'] == new_raw['bindings']['catalog_sha256'], 'Manifest catalog mismatch')
    files = {f['sha256']: f for f in manifest['files']}
    before = {q['query_id']: q for q in old_raw['queries']}
    after = {q['query_id']: q for q in new_raw['queries']}
    prior_queries, prior_tasks = {}, {}
    for document, ref in documents:
        require(document['bindings']['raw_results_sha256'] == ref['old_raw_sha256'], 'Prior review binds another raw replay')
        for q in document['queries']:
            require(q['query_id'] not in prior_queries, 'Duplicate prior reviewed query')
            prior_queries[q['query_id']] = (q, ref['document'])
        for t in document.get('tasks', []):
            unit = t['task_id'], t['language']
            require(unit not in prior_tasks, 'Duplicate prior task-use review')
            prior_tasks[unit] = (t, ref['document'])
    queries, pending, audit = [], [], []
    for qid, (review, ref) in prior_queries.items():
        old, current = before[qid], after[qid]
        for key in ('request', 'task_id', 'component_id', 'language', 'review_requirements'):
            require(old[key] == current[key], 'Changed request/use: ' + qid + ':' + key)
        prior_rows = {r['variant_id']: r for r in old['exact_results']}
        observations = {r['variant_id']: r for r in review['result_judgments']}
        judged, deferred = [], []
        for row in current['exact_results']:
            _file_pair(row, files)
            vid = row['variant_id']
            observation, previous = observations.get(vid), prior_rows.get(vid)
            safe, detail = reusable_result(previous, row) if previous else (False, 'not_in_same_prior_query')
            if not safe or not observation or observation.get('verdict') not in {'relevant', 'irrelevant', 'uncertain'}:
                deferred.append({'query_id': qid, 'variant_id': vid, 'rank': row['rank'],
                                 'original_sha256': row['selected_file']['sha256'], 'reason': detail})
                continue
            require(observation['original_sha256'] == row['selected_file']['sha256']
                    and observation['preview_sha256'] == row['preview']['sha256'], 'Prior observation file differs')
            retained = copy.deepcopy(observation)
            retained.update(rank=row['rank'], new_visual_inspection_performed=False,
                reuse_binding={'prior_review': ref, 'prior_rank': previous['rank'],
                              'request_and_use_unchanged': True, 'source_science_unchanged': True,
                              'derived_index_difference': detail})
            judged.append(retained)
            audit.append({'query_id': qid, 'variant_id': vid, 'old_rank': previous['rank'],
                          'new_rank': row['rank'], 'verdict': retained['verdict'], **detail})
        relevant = {r['variant_id'] for r in judged if r['verdict'] == 'relevant'}
        selected = [vid for vid in review.get('selected_variant_ids', []) if vid in relevant]
        source_checks = [copy.deepcopy(c) for c in review.get('source_checks', []) if c['variant_id'] in selected]
        require({c['variant_id'] for c in source_checks} == set(selected), 'Selected source check missing')
        rows = {r['variant_id']: r for r in current['exact_results']}
        for check in source_checks:
            row = rows[check['variant_id']]
            source = check['source_record']
            require(source in files[row['selected_file']['sha256']]['provenance'], 'Current manifest provenance differs')
            require(all(source.get(k) == v for k, v in row['selected_source'].items()), 'Returned source differs')
            require(all(check.get(k) is True for k in ('source_evidence_checked', 'license_checked', 'species_matches')),
                    'Prior source review incomplete')
            require(check.get('required_species') == current['review_requirements'].get('required_species'), 'Species purpose differs')
        queries.append({'query_id': qid, 'task_id': current['task_id'], 'component_id': current['component_id'],
            'language': current['language'], 'request': current['request'], 'review_requirements': current['review_requirements'],
            'result_judgments': judged, 'pending_results': deferred,
            'all_results_reviewed': len(judged) == len(current['exact_results']),
            'selected_variant_ids': selected, 'source_checks': source_checks,
            'gap_reason': None if selected else 'No previously selected, actually reviewed file survives this exact query; further review is required.'})
        pending.extend(deferred)
    query_map = {q['query_id']: q for q in queries}
    tasks = []
    for unit, (previous, ref) in prior_tasks.items():
        children = [q for q in queries if (q['task_id'], q['language']) == unit]
        selected = {q['component_id']: q['selected_variant_ids'] for q in children}
        same = selected == previous['selected_roles']
        task = copy.deepcopy(previous)
        task.update(selected_roles=selected, review_reused_from=ref, new_composition_inspection_performed=False,
                    query_result_reviews_complete=all(q['all_results_reviewed'] for q in children))
        if not same:
            task.update(composition_use_pass=None,
                        composition_notes='Selected role files changed; the prior composition conclusion is retained only in its source document.',
                        composition_evidence=[])
        tasks.append(task)
    return {'queries': queries, 'tasks': tasks, 'pending_results': pending, 'audit': audit,
            'not_yet_reviewed_query_ids': sorted(set(after) - set(query_map))}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--old-raw', type=Path, required=True)
    p.add_argument('--new-raw', type=Path, required=True)
    p.add_argument('--review', type=Path, action='append', required=True)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    require(not args.output.exists(), 'Refusing to overwrite an existing bridge')
    old_sha = digest(args.old_raw)
    docs = [(read(path), {'document': reference(path), 'old_raw_sha256': old_sha}) for path in args.review]
    checked = [verify_observation_files(document) for document, _ in docs]
    raw = read(args.new_raw)
    result = bridge(read(args.old_raw), raw, docs, read(args.manifest))
    payload = {'schema_version': 1, 'status': 'partial_current_replay_observations_not_formal_acceptance',
        'reviewer': 'root exact observation reuse audit', 'reviewed_at': datetime.now(timezone.utc).isoformat(),
        'independent_from_metadata_matcher': True,
        'bindings': {**raw['bindings'], 'raw_results_sha256': digest(args.new_raw),
                     'publish_manifest_sha256': digest(args.manifest)},
        'source_documents': [reference(path) for path in (args.old_raw, args.new_raw, args.manifest, *args.review)],
        **result, 'verified_prior_evidence_files': checked,
        'new_visual_inspections': 0, 'new_queries_executed': 0, 'acceptance_passed': False,
        'limitations': ['Reused visual/source observations do not accept all new results or new material-policy checks.',
                       'Only aliases changed within inherited science; all source science and physical-file bytes are identical.']}
    verify_observation_files(payload)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+'\n', encoding='utf8', newline='\n')
    print(json.dumps({'path':str(args.output), 'sha256':digest(args.output),
        'queries_with_observations':len(result['queries']),
        'complete_queries':sum(q['all_results_reviewed'] for q in result['queries']),
        'reused_positions':len(result['audit']), 'pending_positions':len(result['pending_results']),
        'compositions_reused':sum(t['composition_use_pass'] is True for t in result['tasks'])}))


if __name__ == '__main__':
    main()
