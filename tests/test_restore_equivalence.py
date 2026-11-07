"""Synthetic metadata only; these are not real cloud recovery observations."""
import copy
import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import restore_equivalence as eq
import build_publish_pack as pack

spec = importlib.util.spec_from_file_location('acceptance_test_fixtures', ROOT / 'tests/test_accept_release.py')
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)

REPO = 'fixture/library'


def run():
    return {'repository': REPO, 'run_id': '3', 'run_attempt': '1', 'commit_sha': 'b' * 40,
            'workflow_ref': REPO + '/.github/workflows/restore-equivalence.yml@refs/heads/main',
            'url': f'https://github.com/{REPO}/actions/runs/3'}


class MetadataStore:
    repository = REPO

    def __init__(self, values, packs):
        self.values = values
        self.calls = []
        self.packs = {p['url']: {'id': 1000 + n, 'digest': 'sha256:' + p['sha256'],
                               'size': p['size_bytes'], 'browser_download_url': p['url']}
                      for n, p in enumerate(packs)}

    def metadata(self, url, expected_sha):
        assert url.endswith('.json') and '.tar' not in url
        self.calls.append(url)
        value = self.values[url]
        if pack.sha(value) != expected_sha:
            raise ValueError('Synthetic API digest mismatch')
        return value

    def assets(self, tag):
        rows = {pack.release_location(url, REPO)[1]: {'id': n + 1, 'digest': 'sha256:' + pack.sha(data), 'size': len(data)}
                for n, (url, data) in enumerate(self.values.items()) if pack.release_location(url, REPO)[0] == tag}
        rows.update({pack.release_location(url, REPO)[1]: row for url, row in self.packs.items()
                     if pack.release_location(url, REPO)[0] == tag})
        return rows


def example(old_manifest=None):
    if old_manifest is None:
        location = pack.release_url(REPO, 'collection-synthetic', 'source.tar.gz')
        old_manifest = {'schema_version': 1, 'acceptance_report_required': True,
            'collection_release': 'test', 'catalog_sha256': 'a' * 64, 'files': [{
                'sha256': '1' * 64, 'size_bytes': 7, 'format': 'svg', 'structure': 'pure_vector',
                'archive': {'url': location, 'member': 'files/example.svg'},
                'preview': {'sha256': '2' * 64, 'size_bytes': 9, 'format': 'png',
                            'archive': {'url': location, 'member': 'previews/example.png'}},
                'provenance': [{'license': 'CC-BY-4.0', 'attribution': 'Synthetic author'}],
                'text_content': {'status': 'unknown'}, 'unknown_future_field': {'numeric': 1}}]}
    args = {'hashes': {}}
    fixtures.bind_synthetic_restore(args, old_manifest)
    bundle, restore = args['bundle_index'], args['restore_evidence']
    new_manifest = {**copy.deepcopy(old_manifest), 'catalog_sha256': 'd' * 64}
    new_url = pack.release_url(REPO, 'acceptance-test', 'new.publish_manifest.json')
    restore_url = pack.release_url(REPO, 'acceptance-test', 'old.restore_evidence.json')
    urls = {'new_manifest': new_url, 'restored_manifest': bundle['publish_manifest']['url'],
            'historical_bundle': restore['bundle_url'], 'historical_restore': restore_url}
    documents = {'new_manifest': new_manifest, 'restored_manifest': old_manifest,
                 'historical_bundle': bundle, 'historical_restore': restore}
    store = MetadataStore({urls[k]: pack.json_bytes(v) for k, v in documents.items()}, bundle['packs'])
    evidence = eq.verify_remote_equivalence(store, new_manifest_url=new_url,
        new_manifest_sha256=pack.sha(pack.json_bytes(new_manifest)), bundle_url=restore['bundle_url'],
        bundle_sha256=pack.sha(pack.json_bytes(bundle)), restore_url=restore_url,
        restore_sha256=pack.sha(pack.json_bytes(restore)), run=run())
    return evidence, documents, store


def validate(evidence, docs):
    return eq.validate_restore_equivalence(evidence, docs['new_manifest'], docs['restored_manifest'],
        docs['historical_bundle'], docs['historical_restore'],
        new_manifest_sha256=pack.sha(pack.json_bytes(docs['new_manifest'])),
        restored_manifest_sha256=pack.sha(pack.json_bytes(docs['restored_manifest'])),
        bundle_sha256=pack.sha(pack.json_bytes(docs['historical_bundle'])),
        restore_evidence_sha256=pack.sha(pack.json_bytes(docs['historical_restore'])))


def test_remote_metadata_only_path_has_four_reads_and_no_artwork_reads():
    evidence, docs, store = example()
    assert validate(evidence, docs)
    assert len(store.calls) == len(set(store.calls)) == 4
    assert evidence['new_artwork_archive_reads'] == 0
    assert evidence['fresh_artwork_restore_performed'] is False
    assert evidence['proof_type'] == 'equivalence_non_fresh_artwork_read'
    assert evidence['current_pack_asset_metadata_verified'] is True
    assert len(evidence['remote_pack_asset_checks']) == len(docs['historical_bundle']['packs'])
    with pytest.raises(ValueError, match='binding mismatch'):
        pack.validate_remote_restore(docs['historical_restore'], docs['historical_bundle'], docs['new_manifest'],
            manifest_sha256=pack.sha(pack.json_bytes(docs['new_manifest'])),
            bundle_sha256=pack.sha(pack.json_bytes(docs['historical_bundle'])))


@pytest.mark.parametrize('field,value', [
    ('size_bytes', 8), ('format', 'png'), ('structure', 'mixed'),
    ('sha256', '9' * 64), ('provenance', [{'license': 'CC0-1.0'}]),
    ('text_content', {'status': 'absent'}), ('family_ids', ['different']),
    ('public_scope', {'public_allowed': True}),
    ('unknown_future_field', {'numeric': True}),
    ('archive', {'url': 'https://example.invalid/file', 'member': 'x.svg'}),
    ('preview', {'sha256': '8' * 64}),
])
def test_every_changed_file_field_including_unknown_fields_is_rejected(field, value):
    evidence, docs, _ = example()
    docs['new_manifest']['files'][0][field] = value
    with pytest.raises(ValueError, match='outside the top-level'):
        validate(evidence, docs)


@pytest.mark.parametrize('mutation', ['same_catalog', 'invalid_old_catalog', 'invalid_new_catalog', 'new_top_field', 'missing_file'])
def test_catalog_and_whole_manifest_contract_is_strict(mutation):
    evidence, docs, _ = example()
    if mutation == 'same_catalog': docs['new_manifest']['catalog_sha256'] = docs['restored_manifest']['catalog_sha256']
    elif mutation == 'invalid_old_catalog': docs['restored_manifest']['catalog_sha256'] = None
    elif mutation == 'invalid_new_catalog': docs['new_manifest']['catalog_sha256'] = 'invalid'
    elif mutation == 'new_top_field': docs['new_manifest']['other'] = 'different'
    else: docs['new_manifest']['files'] = []
    with pytest.raises(ValueError): validate(evidence, docs)


@pytest.mark.parametrize('mutation', ['build_only', 'same_restore_run', 'missing_pack_read', 'false_full_read'])
def test_equivalence_never_replaces_a_real_historical_full_restore(mutation):
    evidence, docs, _ = example()
    restore = docs['historical_restore']
    if mutation == 'build_only': restore['operation'] = 'build_verification'
    elif mutation == 'same_restore_run': restore['restore_run'] = restore['build_run']
    elif mutation == 'missing_pack_read': restore['remote_reads']['packs'] = []
    else: restore['remote_reads']['packs'][0]['complete_stream_verified'] = False
    with pytest.raises(ValueError): validate(evidence, docs)


@pytest.mark.parametrize('mutation', ['run', 'workflow', 'digest', 'size', 'asset_id', 'no_read', 'extra_read',
                                     'new_fresh_claim', 'archive_read', 'wrong_comparison', 'old_location', 'catalog_binding'])
def test_metadata_observation_or_proof_tampering_is_rejected(mutation):
    evidence, docs, _ = example()
    reads = evidence['remote_metadata_reads']
    if mutation == 'run':
        evidence['metadata_run'].update(run_id='2', url=f'https://github.com/{REPO}/actions/runs/2')
    elif mutation == 'workflow': evidence['metadata_run']['workflow_ref'] = REPO + '/.github/workflows/publish-pack.yml@refs/heads/main'
    elif mutation == 'digest': reads['new_manifest']['github_digest'] = 'sha256:' + 'f' * 64
    elif mutation == 'size': reads['new_manifest']['github_asset_size'] += 1
    elif mutation == 'asset_id': reads['new_manifest']['github_asset_id'] = None
    elif mutation == 'no_read': reads['new_manifest']['complete_read_verified'] = False
    elif mutation == 'extra_read': reads['artwork'] = {}
    elif mutation == 'new_fresh_claim': evidence['fresh_artwork_restore_performed'] = True
    elif mutation == 'archive_read': evidence['new_artwork_archive_reads'] = 1
    elif mutation == 'wrong_comparison': evidence['comparison']['complete_file_records_equal'] = False
    elif mutation == 'old_location': reads['restored_manifest']['url'] = pack.release_url(REPO, 'wrong', 'wrong.publish_manifest.json')
    else: evidence['new_catalog_sha256'] = 'f' * 64
    with pytest.raises(ValueError): validate(evidence, docs)


def test_archive_input_is_rejected_before_any_remote_read():
    _, docs, store = example()
    store.calls.clear()
    with pytest.raises(ValueError, match='typed JSON'):
        eq.verify_remote_equivalence(store, new_manifest_url=pack.release_url(REPO, 'test', 'source.tar.gz'),
            new_manifest_sha256='a' * 64, bundle_url=docs['historical_restore']['bundle_url'],
            bundle_sha256='b' * 64, restore_url=pack.release_url(REPO, 'test', 'old.restore_evidence.json'),
            restore_sha256='c' * 64, run=run())
    assert store.calls == []


@pytest.mark.parametrize('mutation', ['missing', 'digest', 'size'])
def test_cloud_pack_inventory_missing_or_changed_is_rejected_without_body_reads(mutation):
    _, docs, store = example()
    key = next(iter(store.packs))
    if mutation == 'missing': del store.packs[key]
    elif mutation == 'digest': store.packs[key]['digest'] = 'sha256:' + 'f' * 64
    else: store.packs[key]['size'] += 1
    before = list(store.calls)
    with pytest.raises(ValueError, match='missing or its current API'):
        eq.current_pack_assets(store, docs['historical_bundle'])
    assert store.calls == before


@pytest.mark.parametrize('mutation', ['missing', 'digest', 'size', 'body_read'])
def test_consumer_rejects_incomplete_or_tampered_current_pack_checks(mutation):
    evidence, docs, _ = example()
    if mutation == 'missing': evidence['remote_pack_asset_checks'] = []
    elif mutation == 'digest': evidence['remote_pack_asset_checks'][0]['github_digest'] = 'sha256:' + 'f' * 64
    elif mutation == 'size': evidence['remote_pack_asset_checks'][0]['github_asset_size'] += 1
    else: evidence['remote_pack_asset_checks'][0]['archive_body_read'] = True
    with pytest.raises(ValueError): validate(evidence, docs)


def test_acceptance_requires_explicit_equivalence_and_reports_its_limited_type():
    args = fixtures.fixture()
    old_manifest, report = fixtures.accept.evaluate_release(**args)
    assert report['acceptance_passed']
    evidence, docs, _ = example(old_manifest)
    args['hashes']['catalog_sha256'] = docs['new_manifest']['catalog_sha256']
    args['assembly']['summary']['catalog_sha256'] = docs['new_manifest']['catalog_sha256']
    args['bundle_index'] = docs['historical_bundle']
    args['restore_evidence'] = docs['historical_restore']
    args['hashes']['bundle_index_sha256'] = pack.sha(pack.json_bytes(docs['historical_bundle']))
    _, report = fixtures.accept.evaluate_release(**args)
    assert not report['restore_passed'] and report['restore_proof_type'] == 'pending'
    args.update(restore_equivalence=evidence, restored_manifest=old_manifest)
    args['hashes'].update(restored_manifest_sha256=pack.sha(pack.json_bytes(old_manifest)),
        restore_evidence_sha256=pack.sha(pack.json_bytes(docs['historical_restore'])),
        restore_equivalence_sha256=pack.sha(pack.json_bytes(evidence)))
    _, report = fixtures.accept.evaluate_release(**args)
    assert report['restore_passed']
    assert report['restore_proof_type'] == 'equivalence_non_fresh_artwork_read'
    assert report['full_artwork_restore_proof_directly_binds_current_manifest'] is False
    # Changing recovery evidence does not silently rebind the other scientific gates.
    assert not report['acceptance_passed']
    assert not any(k in report['required_bindings'] for k in ['restore_equivalence_sha256', 'restored_manifest_sha256', 'restore_evidence_sha256'])
    args['hashes']['restore_equivalence_sha256'] = 'f' * 64
    _, report = fixtures.accept.evaluate_release(**args)
    assert not report['restore_passed']
