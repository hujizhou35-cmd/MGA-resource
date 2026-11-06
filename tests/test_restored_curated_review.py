"""Only tiny in-memory synthetic artwork; no source fetch or real-library reads."""
import copy
import gzip
import io
import json
from pathlib import Path
import tarfile

import pytest
from PIL import Image

from scripts import build_publish_pack as pack
from scripts import build_restored_curated_review as review

REPO = 'test/library'
SOURCE = pack.release_url(REPO, 'collection-1', 'source.tar.gz')


def identity(run):
    return {'repository': REPO, 'run_id': str(run), 'run_attempt': '1', 'commit_sha': str(run) * 40,
            'workflow_ref': REPO + '/.github/workflows/publish-pack.yml@refs/heads/main',
            'url': f'https://github.com/{REPO}/actions/runs/{run}'}


class Store:
    repository = REPO
    def __init__(self, values): self.values = values
    def proof(self, url): return {'sha256': pack.sha(self.values[url]), 'size_bytes': len(self.values[url])}
    def metadata(self, url, expected):
        assert pack.sha(self.values[url]) == expected
        return self.values[url]
    def open(self, url): return io.BytesIO(self.values[url])


def fixture(tmp_path, count=2, *, non_png=False, shared_role=False):
    files, entries, rows = [], [], []
    for n in range(count):
        original = f'<svg><!-- tiny synthetic original {n} --></svg>'.encode()
        im = Image.new('RGBA', (3, 2), (n, 80, 100, 0))
        im.putpixel((1, 0), (255, 255, 255, 255))
        im.putpixel((1, 1), (n, 20, 255, 128))
        stream = io.BytesIO(); im.save(stream, format='PNG')
        preview = b'not a PNG' if non_png else stream.getvalue()
        if shared_role:
            original = preview
        def ref(data, role, ext):
            return {'sha256': pack.sha(data), 'size_bytes': len(data), 'format': ext,
                    'archive': {'url': SOURCE, 'member': f'{role}/{n}.{ext}'}}
        original_format = 'png' if shared_role else 'svg'
        structure = 'raster' if shared_role else 'pure_vector'
        f = {**ref(original, 'files', original_format), 'structure': structure, 'preview': ref(preview, 'previews', 'png')}
        source = {'source': 'synthetic', 'source_id': str(n), 'source_url': f'https://example.org/{n}',
                  'license': 'CC0-1.0', 'attribution': f'Synthetic artist {n}'}
        f['provenance'] = [source]; files.append(f)
        entries.append({'candidate_id': f'synthetic-{n}:file-0', 'family_id': f'synthetic-{n}', 'title': f'Tiny {n}',
                        'categories': ['cells'], 'original_sha256': f['sha256'], 'preview_sha256': f['preview']['sha256'],
                        'original_archive': f['archive'], 'preview_archive': f['preview']['archive'],
                        'format': original_format, 'structure': structure, 'source_records': [source]})
        rows.extend([(f['archive']['member'], original), (f['preview']['archive']['member'], preview)])
    manifest = {'schema_version': 1, 'acceptance_report_required': True, 'collection_release': 'collection-1',
                'catalog_sha256': 'c' * 64, 'files': files}
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode='w') as tar:
        for name, data in rows:
            info = tarfile.TarInfo(name); info.size = len(data); tar.addfile(info, io.BytesIO(data))
    archive = gzip.compress(raw.getvalue(), mtime=0)
    md = pack.json_bytes(manifest)
    index, index_path = pack.build_bundle(md, pack.sha(md), repository=REPO, version='v1',
        archive_proofs={SOURCE: {'sha256': pack.sha(archive), 'size_bytes': len(archive)}},
        opener=lambda _: io.BytesIO(archive), output=tmp_path/'build', build_run=identity(1))
    bundle_url = pack.release_url(REPO, index['release_tag'], index_path.name)
    values = {bundle_url: index_path.read_bytes(), index['publish_manifest']['url']: md}
    values.update({p['url']: (index_path.parent/p['name']).read_bytes() for p in index['packs']})
    restored = tmp_path/'restored'
    evidence = pack.restore_bundle(bundle_url, pack.sha(index_path.read_bytes()), store=Store(values),
        output=restored, checked_by='synthetic test', restore_run=identity(2))
    curated = {'catalog_binding': {'sha256': 'c'*64}, 'manifest_binding': {'sha256': pack.sha(md)}, 'curated': entries}
    path = tmp_path/'curated.json'; path.write_bytes(pack.json_bytes(curated))
    return restored, path, evidence['bundle_sha256']


def run(tmp_path, restored, curated, bundle_sha, **kwargs):
    return review.build_review(restored, curated, bundle_sha256=bundle_sha, output=tmp_path/'qa',
                               prefix='tiny-review', expected_count=kwargs.pop('expected_count', 2), **kwargs)


def test_complete_bound_packet_contains_previews_not_originals_and_no_visual_pass(tmp_path):
    restored, curated, digest = fixture(tmp_path)
    result = run(tmp_path, restored, curated, digest)
    assert result['curated_count'] == result['individual_display_coverage'] == result['adjacency_display_coverage'] == 2
    assert result['build_run']['commit_sha'] != result['restore_run']['commit_sha']
    assert result['new_artwork_downloads'] == result['artwork_original_members_in_output'] == 0
    assert result['visual_acceptance_passed'] is False
    with tarfile.open(tmp_path/'qa/tiny-review.tar.gz') as tar:
        names = tar.getnames()
        assert not any(n.startswith('objects/') or n.endswith('.svg') for n in names)
        assert len([n for n in names if n.startswith('previews/')]) == 2
        assert tar.extractfile('curated-original.json').read() == curated.read_bytes()
        mapping = json.load(tar.extractfile('mapping.json'))
        assert all(i['display_visual_pass'] is None and len(i['display_conditions']) == 7 for i in mapping['items'])
        assert all(i['source_records'] == i['publication_source_provenance'] for i in mapping['items'])


def test_twelve_item_batch_covers_last_three_in_second_adjacency(tmp_path):
    restored, curated, digest = fixture(tmp_path, count=12)
    result = run(tmp_path, restored, curated, digest, expected_count=12)
    assert result['batch_count'] == 1 and result['comparison_sheets'] == 4 and result['adjacency_sheets'] == 2
    mapping = json.loads((tmp_path/'qa/tiny-review.mapping.json').read_bytes())
    assert all(any('adjacency-10-12' in c['sheet'] for c in i['display_conditions']) for i in mapping['items'][9:])


def test_preview_bytes_identical_to_original_are_disclosed_not_claimed_byte_disjoint(tmp_path):
    restored, curated, digest = fixture(tmp_path, shared_role=True)
    result = run(tmp_path, restored, curated, digest)
    assert result['preview_role_members_also_original_bytes'] == result['artwork_original_members_in_output'] == 2
    assert result['separate_original_role_members_in_output'] == 0
    assert result['restored_objects_rehashed'] == 2


@pytest.mark.parametrize('fault', ['preview_pair', 'source_archive', 'source_credit', 'catalog', 'count', 'preview_bytes', 'original_bytes', 'restore_evidence', 'bundle_binding'])
def test_wrong_pair_or_restore_proof_fails_before_packet_emission(tmp_path, fault):
    restored, curated, digest = fixture(tmp_path)
    d = json.loads(curated.read_bytes())
    if fault == 'preview_pair': d['curated'][0]['preview_sha256'] = d['curated'][1]['preview_sha256']
    elif fault == 'source_archive': d['curated'][0]['preview_archive']['member'] = 'other.png'
    elif fault == 'source_credit': d['curated'][0]['source_records'][0]['license'] = 'CC-BY-4.0'
    elif fault == 'catalog': d['catalog_binding']['sha256'] = 'b'*64
    elif fault == 'count': d['curated'].pop()
    elif fault in ('preview_bytes', 'original_bytes'):
        role = 'preview_sha256' if fault == 'preview_bytes' else 'original_sha256'
        ext = 'png' if fault == 'preview_bytes' else 'svg'
        (restored/'objects'/f"{d['curated'][0][role]}.{ext}").write_bytes(b'corrupted')
    elif fault == 'restore_evidence':
        p = restored/'restore_evidence.json';e=json.loads(p.read_bytes());e['remote_reads']['packs'][0]['complete_stream_verified']=False;p.write_bytes(pack.json_bytes(e))
    elif fault == 'bundle_binding': digest = '0'*64
    curated.write_bytes(pack.json_bytes(d))
    with pytest.raises(ValueError): run(tmp_path, restored, curated, digest)
    assert not list((tmp_path/'qa').glob('*.tar.gz'))


def test_actual_png_decoding_is_required_even_when_restore_hashes_are_valid(tmp_path):
    restored, curated, digest = fixture(tmp_path, non_png=True)
    with pytest.raises((ValueError, OSError)): run(tmp_path, restored, curated, digest)
    assert not (tmp_path/'qa').exists()


def test_size_cap_fails_without_emitting_partial_review(tmp_path):
    restored, curated, digest = fixture(tmp_path)
    with pytest.raises(ValueError, match='exceeds 32 MiB'):
        run(tmp_path, restored, curated, digest, max_pack_bytes=1)
    assert not (tmp_path/'qa').exists()


def test_cli_rejects_real_local_execution(tmp_path, monkeypatch):
    monkeypatch.delenv('GITHUB_ACTIONS', raising=False)
    with pytest.raises(SystemExit):
        review.main(['--restore-dir', str(tmp_path), '--curated', 'absent.json', '--bundle-sha256', 'a'*64,
                     '--output', str(tmp_path/'out'), '--prefix', 'test'])
