"""Cloud backfill tests use tiny in-memory archives and metadata only."""
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

from PIL import Image, ImageDraw
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('backfill_appearance', ROOT / 'scripts/backfill_appearance.py')
backfill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backfill)


def image_bytes(color='blue', size=24, accent=False):
    image = Image.new('RGBA', (size, size), 'white')
    ImageDraw.Draw(image).rectangle((size // 4, size // 4, size * 3 // 4, size * 3 // 4), fill=color)
    if accent:
        image.putpixel((size // 2, size // 2), (240, 0, 0, 255))
    out = io.BytesIO()
    image.save(out, 'PNG')
    return out.getvalue()


def pack(members):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w:gz') as archive:
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return out.getvalue()


def fixture(name='a', *, svg=True, size=24, accent=False):
    url = f'https://github.com/example/library/releases/download/test/batch-{name}.tar.gz'
    original = b'<svg xmlns="http://www.w3.org/2000/svg"><path fill="currentColor" d="M0 0h10v10z"/></svg>' if svg else b'tiny raster fixture original'
    preview = image_bytes('black' if accent else 'blue', size=size, accent=accent)
    original_hash, preview_hash = backfill.sha(original), backfill.sha(preview)
    member = f'files/{original_hash}.svg' if svg else f'files/{original_hash}.png'
    preview_member = f'previews/{preview_hash}.png'
    record = {'id': 'asset-' + name, 'source': 'fixture', 'source_id': name, 'title': 'Fixture mouse ' + name,
              'license': 'CC0-1.0', 'author': 'Fixture', 'files': [{
                  'sha256': original_hash, 'size_bytes': len(original), 'format': 'svg' if svg else 'png',
                  'structure': 'pure_vector' if svg else 'raster',
                  'validation': {'status': 'machine_checked_and_rendered'},
                  'archive': {'url': url, 'member': member},
                  'preview': {'sha256': preview_hash, 'size_bytes': len(preview), 'format': 'png',
                              'archive': {'url': url, 'member': preview_member}}}]}
    # Preview-first order verifies we do not depend on collector member order.
    members = [(preview_member, preview)]
    if svg:
        members.append((member, original))
    data = pack(members)
    manifest = {url: {'size': len(data), 'digest': 'sha256:' + backfill.sha(data), 'browser_download_url': url}}
    return record, data, manifest, members


def test_archive_is_read_once_and_original_preview_resolution_is_used(tmp_path):
    record, data, manifest, _ = fixture(svg=False, size=512, accent=True)
    before = copy.deepcopy(record)
    calls, checkpoints = [], []
    def opener(url):
        calls.append(url)
        return io.BytesIO(data)
    enriched, rows, summary = backfill.run_backfill([record], manifest, opener=opener,
                                                  checkpoint=lambda rows, report: checkpoints.append((rows, report)))
    assert record == before and calls == list(manifest)
    result = enriched[0]['files'][0]
    assert result['appearance']['binding']['dimensions'] == {'width': 512, 'height': 512}
    assert result['appearance']['color']['mode'] == 'color'
    assert 'accent' in result['appearance']['color']['palette_traits']
    assert result['validation'] == before['files'][0]['validation']
    assert summary['original_files_persisted'] == 0 and summary['source_websites_requested'] == 0
    assert summary['appearance_measurement_complete'] is True and summary['visual_acceptance_passed'] is False
    assert rows[0]['archive_verification']['verified'] is True and len(checkpoints) == 1
    assert not list(tmp_path.rglob('*.svg')) and not list(tmp_path.rglob('*.png'))


def test_svg_small_original_is_hash_bound_and_never_added_to_disk():
    record, data, manifest, _ = fixture()
    output, rows, _ = backfill.run_backfill([record], manifest, opener=lambda _: io.BytesIO(data))
    appearance = output[0]['files'][0]['appearance']
    assert appearance['svg_coloring']['current_color_references'] == 1
    assert appearance['svg_coloring']['uniform_recolor_verified'] is False
    assert rows[0]['svg_supplied'] is True
    jobs, _, _ = backfill.plan_jobs([record], manifest)
    assert backfill.reusable_cache(rows[0], next(iter(jobs.values())), manifest)


def test_big_svg_only_measures_preview_and_explicitly_leaves_structure_unknown():
    record, _, manifest, members = fixture()
    record['files'][0]['size_bytes'] = backfill.MAX_SVG_BYTES + 1
    data = pack([members[0]])
    url = next(iter(manifest))
    manifest[url].update(size=len(data), digest='sha256:' + backfill.sha(data))
    output, rows, summary = backfill.run_backfill([record], manifest, opener=lambda _: io.BytesIO(data))
    appearance = output[0]['files'][0]['appearance']
    assert appearance['color']['mode'] == 'color'
    assert appearance['svg_coloring']['available'] is False and rows[0]['svg_supplied'] is False
    assert any('2 MiB' in note for note in appearance['svg_coloring']['uncertainty'])
    assert summary['pending_file_entries'] == 0


def test_bad_archive_digest_never_accepts_or_computes_partial_measurements(monkeypatch):
    record, data, manifest, _ = fixture()
    next(iter(manifest.values()))['digest'] = 'sha256:' + '0' * 64
    def analyzer(*args, **kwargs):
        # run_backfill intentionally creates a pending/null result afterwards.
        assert args[0] is None
        return original_analyzer(*args, **kwargs)
    original_analyzer = backfill.analyze_appearance
    monkeypatch.setattr(backfill, 'analyze_appearance', analyzer)
    output, rows, summary = backfill.run_backfill([record], manifest, opener=lambda _: io.BytesIO(data))
    assert rows == [] and summary['measured_file_entries'] == 0
    assert output[0]['files'][0]['appearance_status']['status'] == 'pending'
    assert summary['archive_reports'][0]['verified'] is False


def test_member_hash_mismatch_is_pending_even_when_archive_digest_is_valid():
    record, _, manifest, members = fixture()
    data = pack([(members[0][0], b'wrong bytes'), members[1]])
    url = next(iter(manifest))
    manifest[url].update(size=len(data), digest='sha256:' + backfill.sha(data))
    # Match the size to reach the content-hash check, which must still fail.
    record['files'][0]['preview']['size_bytes'] = len(b'wrong bytes')
    output, rows, summary = backfill.run_backfill([record], manifest, opener=lambda _: io.BytesIO(data))
    assert not rows and output[0]['files'][0]['appearance']['color']['mode'] == 'unknown'
    assert 'SHA-256' in output[0]['files'][0]['appearance_status']['reason']
    assert summary['archive_reports'][0]['verified'] is True


def test_cached_results_skip_archive_and_reject_different_version_svg_or_evidence():
    record, data, manifest, _ = fixture()
    _, rows, _ = backfill.run_backfill([record], manifest, opener=lambda _: io.BytesIO(data))
    def no_request(_):
        pytest.fail('Valid immutable cache must avoid the archive request')
    _, repeated, summary = backfill.run_backfill([record], manifest, cached_rows=rows, opener=no_request)
    assert repeated == rows and summary['cache_reused_jobs'] == 1 and summary['archives_read'] == 0
    jobs, _, _ = backfill.plan_jobs([record], manifest)
    job = next(iter(jobs.values()))
    for field, value in [('analysis_version', 'old'), ('svg_supplied', False), ('style_evidence_revision', 'different'),
                         ('file_sha256', 'f' * 64), ('preview_sha256', 'e' * 64)]:
        bad = copy.deepcopy(rows[0]); bad[field] = value
        assert not backfill.reusable_cache(bad, job, manifest)
    bad = copy.deepcopy(rows[0]); bad['archive_verification']['verified'] = False
    assert not backfill.reusable_cache(bad, job, manifest)


def test_archive_checkpoints_allow_safe_continuation_without_starting_over():
    first, data1, manifest1, _ = fixture('a', svg=False)
    second, data2, manifest2, _ = fixture('b', svg=True)
    manifest = {**manifest1, **manifest2}
    payloads = {next(iter(manifest1)): data1, next(iter(manifest2)): data2}
    now, saved = [0], []
    def checkpoint(rows, report):
        saved.extend(rows)
        now[0] = 101
    _, partial, report = backfill.run_backfill([first, second], manifest,
        opener=lambda url: io.BytesIO(payloads[url]), clock=lambda: now[0], deadline=100,
        minimum_start_seconds=0, checkpoint=checkpoint)
    assert len(saved) == len(partial) == 1 and report['pending_file_entries'] == 1
    calls = []
    def resume(url):
        calls.append(url)
        return io.BytesIO(payloads[url])
    _, complete, report = backfill.run_backfill([first, second], manifest, cached_rows=saved, opener=resume)
    assert calls == list(manifest2) and len(complete) == 2
    assert report['cache_reused_jobs'] == 1 and report['pending_file_entries'] == 0


def test_missing_preview_unsafe_members_and_deadline_remain_pending():
    record, data, manifest, _ = fixture()
    unsafe = copy.deepcopy(record)
    unsafe['files'][0]['preview']['archive']['member'] = 'previews/../../wrong.png'
    missing = copy.deepcopy(record); missing['files'][0]['preview'] = None
    output, rows, report = backfill.run_backfill([unsafe, missing], manifest,
        opener=lambda _: pytest.fail('Unsafe or missing preview must not be fetched'))
    assert rows == [] and report['pending_file_entries'] == 2
    assert all(item['files'][0]['appearance_status']['status'] == 'pending' for item in output)
    jobs, _, _ = backfill.plan_jobs([record], manifest)
    rows, report = backfill.process_archive(next(iter(manifest)), list(jobs.values()), next(iter(manifest.values())),
        opener=lambda _: io.BytesIO(data), deadline=0, clock=lambda: 0)
    assert not rows and report['verified'] is False


def test_source_style_evidence_uses_exact_variant_not_parent_style():
    record, _, _, _ = fixture()
    record['style'] = 'outline'
    file = record['files'][0]
    path = 'public/icons/svg/filled-24px/body/heart.svg'
    url = 'https://github.com/resolvetosavelives/healthicons/blob/' + 'a' * 40 + '/' + path
    file['source_records'] = [{'source': 'healthicons', 'source_id': path, 'source_url': url}]
    evidence = backfill.source_style_evidence(record, file)
    assert len(evidence) == 1 and evidence[0]['labels']['representation'] == ['flat_fill']
    assert evidence[0]['labels']['series']['id'] == 'healthicons:filled-24px'
    assert 'detail' not in evidence[0]['labels'] and 'realism' not in evidence[0]['labels']
    record.update(source='arcadia', source_id='SVGs/mouse-silhouette.svg', metadata_status='matched_official_gallery',
                  metadata_source='https://example.org/official-gallery', metadata_revision='fixture-revision')
    file.pop('source_records'); file['archive_member'] = record['source_id']
    evidence = backfill.source_style_evidence(record, file)
    assert evidence[0]['labels']['representation'] == ['silhouette']
    file['archive_member'] = 'Raster PNGs/mouse-raster.png'
    assert backfill.source_style_evidence(record, file) == []


def test_merged_arcadia_family_uses_file_tags_with_gallery_binding():
    record, _, _, _ = fixture()
    record.update(source='arcadia', source_id='Raster PNGs/mouse-raster.png', style='raster',
                  metadata_status='matched_official_gallery', metadata_source='https://example.org/official-gallery',
                  metadata_revision='fixture-revision')
    file = record['files'][0]
    file['archive_member'] = 'SVGs/mouse-silhouette.svg'
    file['source_records'] = [{'source': 'arcadia', 'source_id': file['archive_member'], 'tags': ['mouse', 'silhouette']}]
    record['files'].append({'format': 'png', 'sha256': '0' * 64})
    evidence = backfill.source_style_evidence(record, file)
    assert evidence[0]['labels']['series']['id'] == 'arcadia:silhouette'
    file['source_records'][0]['tags'] = ['mouse']
    assert backfill.source_style_evidence(record, file) == []


def test_old_catalog_arcadia_exact_official_directory_does_not_require_lost_tags():
    record, _, _, _ = fixture()
    record.update(source='arcadia', style='raster', metadata_status='matched_official_gallery',
                  metadata_source='https://arcadia-science.github.io/organism-illustration-library/')
    file = record['files'][0]
    file['archive_member'] = 'Silhouette SVGs/Mus-musculus-silhouette.svg'
    file['source_records'] = [{'source': 'arcadia', 'source_id': file['archive_member'],
                               'source_url': 'https://zenodo.org/records/18395998'}]
    evidence = backfill.source_style_evidence(record, file)
    assert evidence[0]['labels']['representation'] == ['silhouette']
    assert evidence[0]['file_sha256'] == file['sha256']
    file['source_records'][0]['source_id'] = 'Silhouette SVGs/different-silhouette.svg'
    assert backfill.source_style_evidence(record, file) == []
    file['source_records'][0]['source_id'] = file['archive_member'] = 'Raster PNGs/Mus-musculus-raster.png'
    assert backfill.source_style_evidence(record, file) == []


def test_empty_cache_is_nonzero_valid_jsonl(tmp_path):
    path = tmp_path / 'empty.appearance.jsonl'
    backfill.write_jsonl(path, [])
    assert path.read_bytes() == b'\n' and backfill.read_jsonl(path) == []


def test_metadata_download_requires_github_digest(tmp_path, monkeypatch):
    raw = b'{"metadata":"fixture"}'
    def fake_gh(*args):
        (tmp_path / 'cache.json').write_bytes(raw)
    monkeypatch.setattr(backfill, 'gh', fake_gh)
    asset = {'size': len(raw), 'digest': 'sha256:' + backfill.sha(raw)}
    assert backfill.download_checked('cache.json', asset, tmp_path, 'example/library', 'test').read_bytes() == raw
    asset['digest'] = 'sha256:' + '0' * 64
    with pytest.raises(ValueError, match='metadata digest'):
        backfill.download_checked('cache.json', asset, tmp_path, 'example/library', 'test')


def test_new_assembly_preserves_source_evidence_and_closes_acceptance(tmp_path):
    record, data, manifest, _ = fixture()
    records, rows, summary = backfill.run_backfill([record], manifest, opener=lambda _: io.BytesIO(data))
    parent = {'source_snapshots': [{'source': 'fixture', 'machine_validation_complete': True}],
              'historical_native_evidence': {'matched_unique_originals': 5}, 'collection_release': 'test',
              'metadata_enrichments': [{'source': 'fixture', 'artwork_refetched': False}],
              'acceptance_passed': True, 'retrieval_pass_rate': 1, 'visually_judged_queries': 120}
    before = copy.deepcopy(parent)
    output = tmp_path / 'catalog'
    report = backfill.publish_catalog(records, output, parent, parent_assembly={'prefix': 'assembly-1-1'},
                                     appearance_summary=summary, run_id='2-1')
    assert parent == before and report['source_snapshots'] == parent['source_snapshots']
    assert report['historical_native_evidence'] == parent['historical_native_evidence']
    assert report['acceptance_passed'] is False and report['retrieval_pass_rate'] is None
    assert report['visually_judged_queries'] == 0 and report['parent_assembly']['prefix'] == 'assembly-1-1'
    catalog = backfill.read_jsonl(output / 'catalog.jsonl')
    assert catalog[0]['files'][0]['appearance']['color']['mode'] == 'color'
    assert (output / 'catalog.sqlite').is_file()


def test_cli_blocks_local_bulk_backfill_before_network():
    env = dict(os.environ); env.pop('GITHUB_ACTIONS', None)
    process = subprocess.run([sys.executable, str(ROOT / 'scripts/backfill_appearance.py'),
                              '--release-tag', 'test', '--assembly-prefix', 'assembly-1-1'],
                             capture_output=True, text=True, env=env)
    assert process.returncode == 2 and 'cloud-only' in process.stderr
