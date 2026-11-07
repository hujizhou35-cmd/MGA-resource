"""Small synthetic metadata only; no artwork or real catalog acquisition."""
import copy
from contextlib import closing
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest

from mga_resources import catalog

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('reindex_catalog', ROOT / 'scripts/reindex_catalog.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture()
def frozen(tmp_path):
    file = {'sha256': 'a'*64, 'size_bytes': 123, 'format': 'svg', 'structure': 'pure_vector',
        'archive': {'url': 'https://example.invalid/a.tar.gz', 'member': 'files/a.svg'},
        'preview': {'sha256': 'b'*64, 'archive': {'url': 'https://example.invalid/a.tar.gz', 'member': 'previews/a.png'}},
        'scientific': {'object': 'mouse', 'view': 'unknown'},
        'validation': {'status': 'machine_checked_and_rendered'}}
    catalog.finalize([{'source': 'synthetic', 'source_id': 'mouse', 'title': 'Mouse', 'species': 'mouse',
        'license': 'CC0-1.0', 'author': 'Synthetic author', 'files': [file]}], tmp_path)
    return tmp_path / 'catalog.jsonl'


def test_reindex_preserves_exact_source_bytes_and_scientific_metadata(frozen):
    # An alternate valid JSON whitespace encoding must remain unchanged too.
    row = json.loads(frozen.read_bytes())
    frozen.write_bytes((json.dumps(row, ensure_ascii=False, separators=(', ', ': '))+'\r\n').encode())
    original = frozen.read_bytes()
    target = frozen.with_name('new.sqlite')
    proof = module.rebuild_and_verify(frozen, target, hashlib.sha256(original).hexdigest())
    assert frozen.read_bytes() == original
    assert proof['catalog_bytes_unchanged'] and proof['source_metadata_semantically_unchanged']
    assert proof['source_records_checked'] == 1 and proof['stats']['unique_original_files'] == 1
    with closing(sqlite3.connect(target)) as db:
        actual = json.loads(db.execute('SELECT metadata FROM assets').fetchone()[0])
        metadata = dict(db.execute('SELECT key,value FROM index_metadata'))
        assert actual == row and actual['files'][0]['scientific'] == {'object': 'mouse', 'view': 'unknown'}
        assert db.execute('PRAGMA user_version').fetchone()[0] == proof['policy']['search_index_version']
    assert all(metadata[key] == str(value) for key,value in catalog.search_policy_metadata().items())
    assert catalog.search_page(target, 'mouse')['results']


def test_reindex_refuses_wrong_catalog_hash_before_new_index(frozen):
    target = frozen.with_name('wrong.sqlite')
    with pytest.raises(ValueError, match='SHA differs'):
        module.rebuild_and_verify(frozen, target, 'f'*64)
    assert not target.exists()


@pytest.mark.parametrize('field', ['search_core_sha256','search_vocabulary_sha256','search_cli_sha256','search_policy_sha256','ranking_version','search_index_version'])
def test_runtime_rejects_stale_policy_even_when_catalog_hash_is_same(frozen, field):
    db = frozen.with_name('catalog.sqlite')
    with closing(sqlite3.connect(db)) as connection, connection:
        connection.execute('UPDATE index_metadata SET value=? WHERE key=?', ('wrong',field))
    with pytest.raises(ValueError, match='policy version or byte hash'):
        catalog.search_page(db, 'mouse')


def test_policy_digest_binds_imported_vocabulary_and_cli_by_canonical_names():
    actual = catalog.search_policy_binding()
    files = {'mga_resources/'+name: hashlib.sha256((ROOT/'mga_resources'/name).read_bytes()).hexdigest()
             for name in ['search-core.mjs','search-vocabulary.mjs','search-cli.mjs']}
    assert actual['search_policy_sha256'] == hashlib.sha256(json.dumps(files,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def test_report_marks_reindex_without_promoting_old_visual_or_scientific_claims():
    parent = {'run_id': '1-1','summary': {'catalog_sha256':'a'*64},'benchmark_queries':120,
        'scientific_visual_reviews': {'applied':2},'public_scope':{'unchanged':True},
        'appearance': {'analysis_version':'appearance-1.1.0'},'acceptance_passed':False}
    original = copy.deepcopy(parent)
    rebuilt = {'catalog_sha256':'a'*64,'policy':{'ranking_version':'synthetic-new','search_index_version':10},'sqlite_sha256':'b'*64}
    report = module.inherited_report(parent,run='2-1',source_prefix='assembly-1-1',source_tag='raw',publish_tag='qa',
        inputs=[{'name':'assembly-1-1.report.json','sha256':'c'*64}],rebuilt=rebuilt,manifest={'sha256':'d'*64,'changed':False})
    assert parent == original
    assert report['operation']=='search_index_only' and report['benchmark_queries']==0
    assert report['summary']['catalog_sha256']==parent['summary']['catalog_sha256']
    assert report['scientific_visual_reviews']==parent['scientific_visual_reviews']
    assert report['index_rebuild']['new_visual_inspections']==0 and report['index_rebuild']['new_manifest_generated'] is False
    assert report['acceptance_passed'] is False
