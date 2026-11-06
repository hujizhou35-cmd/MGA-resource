import copy
import hashlib
import json

import pytest

from mga_resources.catalog import finalize
from mga_resources.scientific_reviews import apply_scientific_reviews, finalized_scientific_review_counts

URL = 'https://github.com/example/library/releases/download/test/batch.tar.gz'
SHEET = 'https://github.com/example/library/releases/download/test/batch.review.png'


def setup(tmp_path):
    file = {'sha256': 'a' * 64, 'format': 'svg', 'size_bytes': 100,
            'structure': 'pure_vector', 'validation': {'status': 'machine_checked_and_rendered'},
            'archive': {'url': URL, 'member': 'files/aa.svg'},
            'preview': {'sha256': 'b' * 64, 'archive': {'url': URL, 'member': 'previews/aa.png'}}}
    record = {'source': 'test', 'source_id': 'mouse-gray', 'title': 'Mouse gray',
              'license': 'CC0-1.0', 'author': 'Artist', 'files': [file]}
    item = {'original_sha256': 'a' * 64, 'preview_sha256': 'b' * 64,
            'original_archive': file['archive'], 'preview_archive': file['preview']['archive'],
            'tile': 2, 'observations': 'Actually viewed complete side-on mouse; strain is not inferred.',
            'visual_verdict': 'usable_as_schematic_component',
            'scientific_assertions': {'object': ['mouse'], 'species': ['mouse'], 'view': ['lateral'],
                                      'role': ['whole_body'], 'depiction_kind': 'animal'}}
    review = {'schema_version': 1, 'reviewer': 'Test reviewer', 'reviewed_at': '2026-11-07',
              'review_method': 'Direct inspection of the actual hash-bound contact sheet',
              'review_sheet_url': SHEET, 'review_sheet_sha256': 'c' * 64, 'items': [item]}
    path = tmp_path / 'review.json'
    path.write_text(json.dumps(review), encoding='utf-8')
    assets = {'batch.tar.gz': {'browser_download_url': URL, 'digest': 'sha256:' + 'd' * 64},
              'batch.review.png': {'browser_download_url': SHEET, 'digest': 'sha256:' + 'c' * 64}}
    return record, review, path, assets


def write(path, review):
    path.write_text(json.dumps(review), encoding='utf-8')


def test_exact_bound_review_applies_only_to_file_and_preserves_input(tmp_path):
    record, _, path, assets = setup(tmp_path)
    before = copy.deepcopy(record)
    result, report = apply_scientific_reviews([record], [path], assets)
    assert record == before and 'scientific' not in result[0]
    file = result[0]['files'][0]
    assert file['scientific']['species'] == ['mouse'] and file['scientific']['view'] == ['lateral']
    evidence = file['scientific_review_evidence'][0]
    assert evidence['kind'] == 'manual_visual_review'
    assert evidence['evidence_sha256'] == 'c' * 64 and evidence['status'] == 'applied'
    assert evidence['applies_to_archive'] == file['archive']
    assert report['applied_unique_files'] == report['applied_file_entries'] == 1
    assert report['review_documents'][0]['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    again, _ = apply_scientific_reviews(result, [path], assets)
    assert again == result


@pytest.mark.parametrize('field,value', [
    ('original_sha256', 'e' * 64), ('preview_sha256', 'e' * 64),
    ('original_archive', {'url': URL, 'member': 'files/different.svg'}),
    ('preview_archive', {'url': URL, 'member': 'previews/different.png'}),
])
def test_all_original_preview_and_archive_bindings_must_match(tmp_path, field, value):
    record, review, path, assets = setup(tmp_path)
    review['items'][0][field] = value
    write(path, review)
    result, report = apply_scientific_reviews([record], [path], assets)
    assert 'scientific' not in result[0]['files'][0]
    assert report['unmatched_assertion_items'] == 1


@pytest.mark.parametrize('change', ['sheet', 'reviewer', 'date', 'method', 'verdict', 'archive_digest'])
def test_incomplete_or_unverified_review_never_applies(tmp_path, change):
    record, review, path, assets = setup(tmp_path)
    if change == 'sheet':
        review['review_sheet_sha256'] = 'f' * 64
    elif change == 'reviewer':
        review['reviewer'] = ''
    elif change == 'date':
        review['reviewed_at'] = 'unknown'
    elif change == 'method':
        review['review_method'] = ''
    elif change == 'verdict':
        review['items'][0]['visual_verdict'] = 'pending'
    else:
        assets['batch.tar.gz']['digest'] = None
    write(path, review)
    result, report = apply_scientific_reviews([record], [path], assets)
    assert result == [record] and report['invalid_assertion_items'] == 1


def test_conflicting_source_values_are_preserved_and_blocked(tmp_path):
    record, _, path, assets = setup(tmp_path)
    record['files'][0]['scientific'] = {'species': ['rat'], 'view': ['dorsal']}
    result, report = apply_scientific_reviews([record], [path], assets)
    file = result[0]['files'][0]
    assert file['scientific']['species'] == ['rat'] and file['scientific']['view'] == ['dorsal']
    assert file['scientific_conflicts'] == ['species', 'view']
    evidence = file['scientific_review_evidence'][0]
    assert evidence['status'] == 'partial_conflict' and 'species' not in evidence['accepted_assertions']
    assert evidence['conflicts']['species']['review_values'] == ['mouse']
    assert report['conflicting_unique_files'] == 1 and report['conflicting_fields'] == 2


def test_known_scientific_synonyms_do_not_create_false_conflicts(tmp_path):
    record, _, path, assets = setup(tmp_path)
    record['species'] = ['Mus musculus']
    record['view'] = ['side view']
    _, report = apply_scientific_reviews([record], [path], assets)
    assert report['conflicting_fields'] == 0


def test_same_bytes_alias_requires_an_observed_archive_anchor(tmp_path):
    record, _, path, assets = setup(tmp_path)
    alias = copy.deepcopy(record)
    alias['source_id'] = 'other-alias'
    alias['files'][0]['archive']['member'] = 'files/mirror.svg'
    alias['files'][0]['preview']['archive']['member'] = 'previews/mirror.png'
    result, report = apply_scientific_reviews([alias], [path], assets)
    assert result == [alias] and report['unmatched_assertion_items'] == 1
    result, report = apply_scientific_reviews([record, alias], [path], assets)
    assert report['applied_unique_files'] == 1 and report['applied_file_entries'] == 2
    assert result[1]['files'][0]['scientific_review_evidence'][0]['binding_scope'] == 'identical_original_and_preview_sha_with_observed_archive_anchor'


def test_other_family_variants_or_changed_preview_do_not_inherit_review(tmp_path):
    record, _, path, assets = setup(tmp_path)
    other = copy.deepcopy(record)
    other['family_hint'] = record['family_hint'] = 'family'
    other['files'][0]['sha256'] = 'e' * 64
    changed_preview = copy.deepcopy(record)
    changed_preview['files'][0]['preview']['sha256'] = 'f' * 64
    result, report = apply_scientific_reviews([record, other, changed_preview], [path], assets)
    assert report['applied_file_entries'] == 1
    assert 'scientific' not in result[1]['files'][0] and 'scientific' not in result[2]['files'][0]


def test_conflicting_review_claims_never_choose_first(tmp_path):
    record, review, path, assets = setup(tmp_path)
    conflicting = copy.deepcopy(review['items'][0])
    conflicting['scientific_assertions']['species'] = ['rat']
    review['items'].append(conflicting)
    write(path, review)
    result, report = apply_scientific_reviews([record], [path], assets)
    assert 'species' in result[0]['files'][0]['scientific_conflicts']
    assert 'species' not in result[0]['files'][0]['scientific']
    assert report['conflicting_unique_files'] == 1


def test_prior_conflict_is_not_silently_resolved_by_reapplying_same_review(tmp_path):
    record, _, path, assets = setup(tmp_path)
    record['files'][0]['scientific_conflicts'] = ['species']
    result, _ = apply_scientific_reviews([record], [path], assets)
    evidence = result[0]['files'][0]['scientific_review_evidence'][0]
    assert 'species' not in evidence['accepted_assertions']
    assert evidence['conflicts']['species']['status'] == 'pending_independent_review'


def test_item_in_second_contact_sheet_has_independent_verified_binding(tmp_path):
    record, review, path, assets = setup(tmp_path)
    second = SHEET.replace('batch.review', 'second.review')
    review['items'][0].update(review_sheet_url=second, review_sheet_sha256='e' * 64)
    assets['second.review.png'] = {'browser_download_url': second, 'digest': 'sha256:' + 'e' * 64}
    write(path, review)
    result, _ = apply_scientific_reviews([record], [path], assets)
    evidence = result[0]['files'][0]['scientific_review_evidence'][0]
    assert evidence['evidence_uri'] == second and evidence['evidence_sha256'] == 'e' * 64
    assets['second.review.png']['digest'] = 'sha256:' + 'f' * 64
    result, report = apply_scientific_reviews([record], [path], assets)
    assert result == [record] and report['invalid_assertion_items'] == 1


def test_finalize_preserves_exact_file_evidence_with_aliases(tmp_path):
    record, _, path, assets = setup(tmp_path)
    alias = copy.deepcopy(record)
    alias['source_id'] = 'alias'
    result, _ = apply_scientific_reviews([record, alias], [path], assets)
    output = tmp_path / 'final'
    finalize(result, output)
    finalized = [json.loads(x) for x in (output / 'catalog.jsonl').read_text(encoding='utf-8').splitlines() if x]
    assert len(finalized) == 1
    file = finalized[0]['files'][0]
    assert file['scientific']['view'] == ['lateral']
    assert file['scientific_review_evidence'][0]['kind'] == 'manual_visual_review'
    assert finalized_scientific_review_counts(finalized)['files_with_accepted_assertions'] == 1
