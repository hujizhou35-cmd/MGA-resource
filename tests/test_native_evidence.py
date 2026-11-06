"""Evidence joins are tested with public metadata only, never native artwork."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from mga_resources.catalog import deduplicate
from mga_resources.native_evidence import attach_native_evidence, load_native_evidence, PROVIDER


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def metadata(tmp_path):
    # Reuse the five actual historical metadata records, not copies of artwork.
    evidence = json.loads((ROOT / 'data/legacy-illustrator-verification.json').read_text(encoding='utf-8'))
    evidence_path = tmp_path / 'evidence.json'
    evidence_raw = (json.dumps(evidence, ensure_ascii=False, indent=2) + '\n').replace('\n', '\r\n').encode('utf-8')
    evidence_path.write_bytes(evidence_raw)
    manifest = {'schema_version': 1, 'assets': copy.deepcopy(evidence['assets']),
                'verification_metadata_sha256': hashlib.sha256(evidence_raw).hexdigest(),
                'evidence_boundary': copy.deepcopy(evidence['evidence_boundary'])}
    manifest['evidence_boundary'].pop('no_artwork_copied_or_uploaded', None)
    for asset in manifest['assets']:
        for role, name in [('source', 'source.svg'), ('native_ai', 'native.ai')]:
            asset['files'][role].update(archive_member=f"components/{asset['name']}/{name}",
                                        integrity_rechecked_for_this_package=True, cloud_upload_status='prepared_not_uploaded')
        asset['license_notice_file'] = {'archive_member': f"components/{asset['name']}/LICENSE.md",
                                        'sha256': hashlib.sha256(asset['name'].encode()).hexdigest(), 'size_bytes': 100}
    manifest_path = tmp_path / 'manifest.json'
    manifest_raw = json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf-8')
    manifest_path.write_bytes(manifest_raw)
    publication = {'schema_version': 1,
                   'archive': {'url': 'https://github.com/example/library/releases/download/test/legacy.tar.gz',
                               'size_bytes': 1000, 'sha256': 'a' * 64},
                   'manifest': {'url': 'https://github.com/example/library/releases/download/test/legacy.manifest.json',
                                'size_bytes': len(manifest_raw), 'sha256': hashlib.sha256(manifest_raw).hexdigest()},
                   'verification': {'original_sha256_rechecked': True, 'archive_members_verified': True,
                                    'github_remote_digests_match': True, 'new_native_tests_performed': False}}
    publication_path = tmp_path / 'publication.json'
    publication_path.write_text(json.dumps(publication), encoding='utf-8')
    return evidence, (evidence_path, publication_path, manifest_path)


def catalog_record(asset, *, source='example', digest=None):
    original = asset['files']['source']
    return {'id': 'record-' + asset['name'], 'source': source, 'source_id': asset['name'],
            'family_id': 'same-family', 'title': asset['name'], 'license': 'CC0-1.0', 'formats': ['svg'],
            'validation': {'status': 'machine_checked', 'illustrator_editability': 'not_tested',
                           'scientific_review': 'not_reviewed', 'visual_review': 'not_reviewed'},
            'files': [{'format': 'svg', 'sha256': digest or original['sha256'], 'size_bytes': original['size_bytes'],
                       'url': 'https://example.org/source.svg', 'archive': {'member': 'files/original.svg'},
                       'validation': {'status': 'machine_checked_and_rendered', 'illustrator_editability': 'not_tested'}}]}


def test_exact_hash_only_preserves_original_media_and_limits(metadata):
    evidence, paths = metadata
    record = catalog_record(evidence['assets'][0])
    unmatched_variant = copy.deepcopy(record['files'][0])
    unmatched_variant['sha256'] = '0' * 64
    record['files'].append(unmatched_variant)
    before = copy.deepcopy(record)
    annotated, report = attach_native_evidence([record], *paths)
    assert record == before
    result = annotated[0]
    assert result['validation'] == before['validation'] and result['formats'] == ['svg']
    assert len(result['files']) == 2 and result['files'][1] == before['files'][1]
    native = result['files'][0].pop('historical_native_validation')
    assert result['files'][0] == before['files'][0]
    assert native['illustrator_version'] == '27.6.0' and native['mcp_server_version'] == '1.10.3'
    assert native['importer_variant'] == 'isolated patched copy' and native['other_environments_verified'] is False
    assert native['family_variants_covered'] is False and native['current_native_test_performed'] is False
    assert native['evidence_boundary']['stock_importer_not_covered'] is True
    variant = result['native_variants'][0]
    assert variant['sha256'] == evidence['assets'][0]['files']['native_ai']['sha256']
    assert variant['archive']['member'] == 'components/cell_culture_dish/native.ai'
    assert variant['validation']['status'] == 'not_processed_by_collection_media_pipeline'
    assert 'machine_checked' not in variant['validation'].values()
    assert variant['license_statement'] == evidence['assets'][0]['license_statement']
    assert report['matched_unique_originals'] == 1 and report['whole_catalog_native_verified'] is False


def test_family_title_and_source_identity_cannot_substitute_for_exact_bytes(metadata):
    evidence, paths = metadata
    record = catalog_record(evidence['assets'][0], source='bioicons', digest='b' * 64)
    record['source_id'] = evidence['assets'][0]['catalog_match']['source_id']
    annotated, report = attach_native_evidence([record], *paths)
    assert annotated == [record] and report['matched_unique_originals'] == 0
    assert len(report['unmatched_legacy_source_hashes']) == 5


def test_all_five_match_across_sources_but_not_other_formats_or_wrong_size(metadata):
    evidence, paths = metadata
    records = [catalog_record(asset, source='mirror') for asset in evidence['assets']]
    annotated, report = attach_native_evidence(records, *paths)
    assert report['matched_unique_originals'] == 5 and all(len(x['native_variants']) == 1 for x in annotated)
    records[0]['files'][0]['format'] = 'png'
    records[1]['files'][0]['size_bytes'] += 1
    annotated, report = attach_native_evidence(records, *paths)
    assert report['matched_unique_originals'] == 3
    assert 'historical_native_validation' not in annotated[0]['files'][0]
    assert 'historical_native_validation' not in annotated[1]['files'][0]
    assert len(report['inconsistent_catalog_files']) == 1


def test_windows_git_newline_changes_use_explicit_semantic_binding(metadata):
    evidence, paths = metadata
    first, original_binding = load_native_evidence(*paths)
    assert original_binding['raw_digest_matches'] is True
    paths[0].write_bytes(json.dumps(evidence, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
    second, binding = load_native_evidence(*paths)
    assert first.keys() == second.keys()
    assert binding['raw_digest_matches'] is False
    assert binding['semantic_sha256'] == original_binding['semantic_sha256']
    assert binding['semantic_evidence_matches_published_manifest'] is True
    assert 'json-sort-keys-compact-utf8' in binding['semantic_digest_algorithm']


def test_semantic_evidence_tampering_is_rejected(metadata):
    evidence, paths = metadata
    evidence['assets'][0]['historical_validation']['other_environments_verified'] = True
    paths[0].write_text(json.dumps(evidence), encoding='utf-8')
    with pytest.raises(ValueError, match='semantically'):
        load_native_evidence(*paths)


def test_manifest_bytes_must_match_publication(metadata):
    _, paths = metadata
    paths[2].write_bytes(paths[2].read_bytes() + b' ')
    with pytest.raises(ValueError, match='published byte digest'):
        load_native_evidence(*paths)


def test_repeated_attach_is_idempotent_and_stale_propagation_is_removed(metadata):
    evidence, paths = metadata
    records = [catalog_record(evidence['assets'][0])]
    attached, report = attach_native_evidence(records, *paths)
    repeated, same_report = attach_native_evidence(attached, *paths)
    assert repeated == attached and same_report == report
    attached[0]['files'][0]['sha256'] = 'f' * 64
    attached[0]['native_variants'].append({'id': 'unrelated-evidence', 'evidence_provider': 'another-reviewed-source'})
    cleaned, report = attach_native_evidence(attached, *paths)
    assert 'historical_native_validation' not in cleaned[0]['files'][0]
    assert cleaned[0]['native_variants'] == [{'id': 'unrelated-evidence', 'evidence_provider': 'another-reviewed-source'}]
    assert report['matched_unique_originals'] == 0


def test_file_level_evidence_and_download_survive_family_merge(metadata):
    evidence, paths = metadata
    matched = catalog_record(evidence['assets'][0])
    unmatched = catalog_record(evidence['assets'][0], digest='0' * 64)
    unmatched['source_id'] = 'another-variant'
    annotated, _ = attach_native_evidence([matched, unmatched], *paths)
    merged, _ = deduplicate(annotated)
    assert len(merged) == 1 and len(merged[0]['files']) == 2
    files = {x['sha256']: x for x in merged[0]['files']}
    assert 'historical_native_validation' not in files['0' * 64]
    history = files[matched['files'][0]['sha256']]['historical_native_validation']
    assert history['evidence_provider'] == PROVIDER
    assert history['native_ai_download']['archive']['member'] == 'components/cell_culture_dish/native.ai'
    assert merged[0]['validation']['illustrator_editability'] == 'not_tested'
