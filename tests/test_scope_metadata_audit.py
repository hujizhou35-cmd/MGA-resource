"""Synthetic metadata acquisition and binding checks; no network or images."""
import copy
import json
from pathlib import Path
import sqlite3
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import audit_scope_metadata as cloud
import replay_catalog_metadata as downloader
from test_restore_equivalence import example, MetadataStore, pack, eq, run, REPO


class Store(MetadataStore):
    def metadata(self, url, expected_sha, **kwargs):
        return super().metadata(url, expected_sha)

    def assets(self, tag):
        rows = super().assets(tag)
        for name, row in rows.items():
            row.update(name=name, browser_download_url=pack.release_url(REPO, tag, name))
        return rows


def cloud_fixture(tmp_path, monkeypatch):
    _, docs, memory = example()
    policy = cloud.load_scope_policy([])
    file = copy.deepcopy(docs['new_manifest']['files'][0])
    from mga_resources.scope_policy import evaluate_public_scope
    file['public_scope'] = evaluate_public_scope({}, file, policy)
    public = {'id': 'synthetic-family', 'files': [file]}
    public_bytes = pack.json_bytes(public)
    catalog_sha = pack.sha(public_bytes)
    docs['new_manifest']['catalog_sha256'] = catalog_sha
    new_url = next(url for url in memory.values if url.endswith('new.publish_manifest.json'))
    memory.values[new_url] = pack.json_bytes(docs['new_manifest'])
    store = Store(memory.values, docs['historical_bundle']['packs'])
    restore_url = next(url for url in store.values if url.endswith('old.restore_evidence.json'))
    evidence = eq.verify_remote_equivalence(store, new_manifest_url=new_url,
        new_manifest_sha256=pack.sha(pack.json_bytes(docs['new_manifest'])),
        bundle_url=docs['historical_restore']['bundle_url'], bundle_sha256=pack.sha(pack.json_bytes(docs['historical_bundle'])),
        restore_url=restore_url, restore_sha256=pack.sha(pack.json_bytes(docs['historical_restore'])), run=run())
    tag, prefix, parent_prefix = 'acceptance-test', 'assembly-11-1', 'assembly-10-1'
    internal = {**copy.deepcopy(public), 'catalog_visibility': 'internal_diagnostic'}
    internal_bytes = pack.json_bytes(internal)
    binding = {'file': 'internal-catalog.jsonl', 'sha256': pack.sha(internal_bytes),
               'size_bytes': len(internal_bytes), 'records': 1}
    parent_report = {'public_scope': {'internal_catalog': binding}}
    report = {'summary': {'catalog_sha256': catalog_sha, 'search_index_version': 9},
              'public_scope': {'policy_sha256': policy['sha256'], 'internal_catalog': binding,
                'inherited_from_assembly': {'prefix': parent_prefix, 'release_tag': tag,
                                           'report_sha256': pack.sha(pack.json_bytes(parent_report))}}}
    db_path = tmp_path / 'fixture.sqlite'
    with sqlite3.connect(db_path) as db:
        db.executescript('CREATE TABLE index_metadata(key TEXT,value TEXT); CREATE TABLE file_variants(file_sha256 TEXT); CREATE TABLE assets(metadata TEXT); PRAGMA user_version=9;')
        db.execute('INSERT INTO index_metadata VALUES (?,?)', ('catalog_version', catalog_sha))
        db.execute('INSERT INTO file_variants VALUES (?)', (file['sha256'],))
        db.execute('INSERT INTO assets VALUES (?)', (json.dumps(public),))
    data = {prefix + '.report.json': pack.json_bytes(report), prefix + '.catalog.jsonl': public_bytes,
            prefix + '.catalog.sqlite': db_path.read_bytes(), parent_prefix + '.report.json': pack.json_bytes(parent_report),
            parent_prefix + '.internal-catalog.jsonl': internal_bytes, 'restore-equivalence-3-1.json': pack.json_bytes(evidence)}
    store.values.update({pack.release_url(REPO, tag, name): value for name, value in data.items()})
    download_calls = []

    def gh(*args):
        assert args[:2] == ('release', 'download')
        selected_tag = args[2]
        name = args[args.index('--pattern') + 1]
        assert name.endswith(('.json', '.jsonl', '.sqlite')) and '.tar' not in name
        destination = Path(args[args.index('--dir') + 1])
        (destination / name).write_bytes(store.values[pack.release_url(REPO, selected_tag, name)])
        download_calls.append(name)
        return ''

    monkeypatch.setattr(cloud, 'gh', gh)
    monkeypatch.setattr(downloader, 'gh', gh)
    monkeypatch.setattr(cloud, 'load_scope_policy', lambda: policy)
    original_read = cloud.read_json
    monkeypatch.setattr(cloud, 'read_json', lambda path: {} if Path(path).name == 'commons-42260711756-component-scope.json' else original_read(path))
    args = {'assembly_prefix': prefix, 'assembly_tag': tag, 'report_sha256': pack.sha(data[prefix + '.report.json']),
            'equivalence_url': pack.release_url(REPO, tag, 'restore-equivalence-3-1.json'),
            'equivalence_sha256': pack.sha(data['restore-equivalence-3-1.json']),
            'output': tmp_path / 'out', 'run': {'run_id': '4', 'run_attempt': '1'}}
    return store, args, report, download_calls


def test_cloud_scope_uses_bound_new_internal_and_exact_index_without_artwork(tmp_path, monkeypatch):
    store, args, report, downloads = cloud_fixture(tmp_path, monkeypatch)
    result, outputs = cloud.run_cloud_audit(store, **args)
    assert result['scope_end_to_end_passed'], result['checks']
    assert result['new_internal_catalog_bytes_verified'] is True
    assert result['restore_proof_type'] == 'equivalence_non_fresh_artwork_read'
    assert result['new_artwork_archive_reads'] == 0 and result['new_image_downloads'] == 0
    assert len(downloads) == 5
    assert 'assembly-10-1.internal-catalog.jsonl' in downloads
    assert result['input_proofs']['internal_catalog']['sha256'] == report['public_scope']['internal_catalog']['sha256']
    assert all(p.exists() for p in outputs)


@pytest.mark.parametrize('name', ['source.tar.gz', '../assembly-1-1.internal-catalog.jsonl', 'assembly-1-1.catalog.jsonl'])
def test_internal_downloader_never_broadens_to_other_roles_or_archives(tmp_path, monkeypatch, name):
    monkeypatch.setattr(cloud, 'gh', lambda *args: pytest.fail('No download permitted'))
    with pytest.raises(ValueError, match='exact assembly internal'):
        cloud.download_internal(name, {}, tmp_path, REPO, 'test', {})


def test_old_internal_digest_cannot_be_substituted(tmp_path, monkeypatch):
    store, args, report, _ = cloud_fixture(tmp_path, monkeypatch)
    name, tag, binding, _ = cloud.internal_location(report, args['assembly_prefix'], args['assembly_tag'])
    asset = store.assets(tag)[name]
    asset['digest'] = 'sha256:' + 'f' * 64
    monkeypatch.setattr(cloud, 'gh', lambda *args: pytest.fail('Reject before download'))
    with pytest.raises(ValueError, match='differs from bound'):
        cloud.download_internal(name, asset, tmp_path, REPO, tag, binding)


@pytest.mark.parametrize('field,value', [('file', '../source.tar.gz'), ('sha256', None), ('records', None)])
def test_internal_binding_requires_exact_filename_hash_and_count(tmp_path, monkeypatch, field, value):
    _, args, report, _ = cloud_fixture(tmp_path, monkeypatch)
    report['public_scope']['internal_catalog'][field] = value
    with pytest.raises(ValueError): cloud.internal_location(report, args['assembly_prefix'], args['assembly_tag'])


def test_parent_and_child_internal_binding_must_agree(tmp_path, monkeypatch):
    store, args, report, _ = cloud_fixture(tmp_path, monkeypatch)
    parent = report['public_scope']['inherited_from_assembly']
    url = pack.release_url(REPO, parent['release_tag'], parent['prefix'] + '.report.json')
    changed = json.loads(store.values[url]); changed['public_scope']['internal_catalog']['sha256'] = 'f' * 64
    store.values[url] = pack.json_bytes(changed)
    parent['report_sha256'] = pack.sha(store.values[url])
    report_url = pack.release_url(REPO, args['assembly_tag'], args['assembly_prefix'] + '.report.json')
    store.values[report_url] = pack.json_bytes(report)
    args['report_sha256'] = pack.sha(store.values[report_url])
    with pytest.raises(ValueError, match='Parent report and new report disagree'):
        cloud.run_cloud_audit(store, **args)
