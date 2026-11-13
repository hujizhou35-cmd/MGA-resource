"""Apply bounded, exact-file visual observations in cloud; never read artwork."""
from __future__ import annotations
import argparse
import copy
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from mga_resources.scientific_reviews import apply_scientific_reviews
from assemble import gh, list_release_assets
from build_publish_pack import json_bytes
from reindex_catalog import rebuild_and_verify, inherited_report
from replay_catalog_metadata import digest, download_metadata, read, require, write

ALLOWED_FILE_CHANGES = {'scientific', 'scientific_review_evidence', 'scientific_conflicts'}


def verify_bounded_changes(before, after, reviewed_pairs):
    """Check every source, identity, archive and disposition, including untouched files."""
    require(len(before) == len(after), 'Catalog membership changed')
    changed, pairs = [], set()
    for old, new in zip(before, after):
        require({k:v for k,v in old.items() if k != 'files'} ==
                {k:v for k,v in new.items() if k != 'files'}, 'Record identity/source metadata changed')
        require(len(old['files']) == len(new['files']), 'File membership changed')
        for a,b in zip(old['files'], new['files']):
            if a == b:
                continue
            pair = (a.get('sha256'), (a.get('preview') or {}).get('sha256'))
            require(pair in reviewed_pairs, 'An unreviewed file was modified')
            require({k:v for k,v in a.items() if k not in ALLOWED_FILE_CHANGES} ==
                    {k:v for k,v in b.items() if k not in ALLOWED_FILE_CHANGES},
                    'Original/preview/provenance/appearance/scope data changed')
            keys = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
            require(set(keys) <= ALLOWED_FILE_CHANGES, 'Unexpected metadata edit')
            changed.append({'asset_id': old['id'], 'original_sha256': pair[0],
                            'preview_sha256': pair[1], 'changed_file_fields': keys})
            pairs.add(pair)
    require(pairs == reviewed_pairs, 'Some reviewed exact files were not updated')
    return {'changed_file_entries': len(changed), 'changed_unique_pairs': len(pairs),
            'changes': changed, 'all_other_catalog_fields_equal': True,
            'original_bytes_and_locations_unchanged': True}


def update_catalog(source, target, review_paths, assets):
    require(not target.exists(), 'Refusing to overwrite updated catalog')
    before = [json.loads(line) for line in source.read_text(encoding='utf8').splitlines() if line.strip()]
    pairs = {(i['original_sha256'], i['preview_sha256']) for p in review_paths
             for i in read(p)['items'] if 'scientific_assertions' in i}
    require(0 < len(pairs) <= 30, 'This operation permits at most thirty reviewed pairs')
    after, audit = apply_scientific_reviews(before, review_paths, assets)
    require(not any(audit[k] for k in ('invalid_assertion_items', 'unmatched_assertion_items',
                                      'conflicting_unique_files')), 'Review assertions invalid, unmatched or conflicting')
    proof = verify_bounded_changes(before, after, pairs)
    with target.open('w', encoding='utf8', newline='\n') as stream:
        for record in after:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n')
    return {'scientific_reviews': audit, 'bounded_diff': proof}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--assembly-prefix', required=True)
    p.add_argument('--catalog-sha256', required=True)
    p.add_argument('--review', required=True)
    p.add_argument('--review-sha256', required=True)
    p.add_argument('--manifest-asset', required=True)
    p.add_argument('--manifest-sha256', required=True)
    p.add_argument('--release-tag', default='acceptance-20261107')
    p.add_argument('--publish-tag', default='acceptance-20261107')
    p.add_argument('--publish', action='store_true')
    args = p.parse_args()
    if os.environ.get('GITHUB_ACTIONS') != 'true':
        p.error('Actual catalog updates run in cloud only; local tests use synthetic metadata')
    require(re.fullmatch(r'assembly-[0-9]+-[0-9]+', args.assembly_prefix), 'Exact assembly required')
    require(re.fullmatch(r'data/visual-reviews/[a-zA-Z0-9_.-]+\.json', args.review), 'Exact committed review required')
    require(re.fullmatch(r'[a-zA-Z0-9_.-]+\.publish_manifest\.json', args.manifest_asset), 'Exact manifest required')
    review = ROOT / args.review
    require(digest(review) == args.review_sha256, 'Committed review hash differs')
    repository = os.environ['GITHUB_REPOSITORY']
    run = os.environ['GITHUB_RUN_ID'] + '-' + os.environ.get('GITHUB_RUN_ATTEMPT', '1')
    base = ROOT / 'build/reviewed-metadata' / run
    incoming, output = base / 'input', base / 'output'
    require(not output.exists(), 'Refusing to overwrite previous output')
    output.mkdir(parents=True)
    assets = list_release_assets(repository, args.release_tag)
    paths, inputs = {}, []
    for suffix in ('catalog.jsonl', 'report.json'):
        name = args.assembly_prefix + '.' + suffix
        paths[suffix], receipt = download_metadata(name, assets[name], incoming, repository, args.release_tag)
        inputs.append(receipt)
    parent = read(paths['report.json'])
    require(digest(paths['catalog.jsonl']) == args.catalog_sha256 == parent['summary']['catalog_sha256'],
            'Input catalog identity differs')
    manifest_asset = assets[args.manifest_asset]
    require(manifest_asset.get('digest') == 'sha256:' + args.manifest_sha256, 'Manifest API digest differs')
    gh('release', 'download', args.release_tag, '--repo', repository, '--pattern', args.manifest_asset,
       '--dir', str(incoming))
    old_manifest_path = incoming / args.manifest_asset
    require(digest(old_manifest_path) == args.manifest_sha256, 'Downloaded manifest differs')
    old_manifest = read(old_manifest_path)
    require(old_manifest['catalog_sha256'] == args.catalog_sha256, 'Manifest catalog differs')
    # Review archive anchors can belong to the collection release; read API metadata only.
    evidence_tags = {url.split('/releases/download/')[1].split('/')[0]
        for item in read(review)['items'] for url in
        (item['original_archive']['url'], item['preview_archive']['url'])}
    all_assets = dict(assets)
    for tag in sorted(evidence_tags - {args.release_tag}):
        all_assets.update({tag + '/' + k: v for k,v in list_release_assets(repository, tag).items()})
    prefix = 'assembly-' + run
    catalog = output / (prefix + '.catalog.jsonl')
    change = update_catalog(paths['catalog.jsonl'], catalog, [review], all_assets)
    rebuilt = rebuild_and_verify(catalog, output / (prefix + '.catalog.sqlite'), digest(catalog))
    manifest = copy.deepcopy(old_manifest)
    manifest['catalog_sha256'] = rebuilt['catalog_sha256']
    manifest_path = output / ('science-update-' + os.environ['GITHUB_RUN_ID'] + '.publish_manifest.json')
    manifest_path.write_bytes(json_bytes(manifest))
    require({**manifest, 'catalog_sha256': old_manifest['catalog_sha256']} == old_manifest,
            'Download manifest changed beyond catalog hash')
    report = inherited_report(parent, run=run, source_prefix=args.assembly_prefix,
        source_tag=args.release_tag, publish_tag=args.publish_tag, inputs=inputs, rebuilt=rebuilt,
        manifest={'name': manifest_path.name, 'sha256': digest(manifest_path), 'changed': True})
    report.update(operation='reviewed_scientific_metadata_update', reviewed_metadata_update={
        **change, 'review': {'path': args.review, 'sha256': args.review_sha256},
        'previous_catalog_sha256': args.catalog_sha256, 'previous_manifest_sha256': args.manifest_sha256,
        'manifest_change_paths': ['/catalog_sha256'], 'new_artwork_acquired': 0,
        'benchmark_searches_executed': 0, 'artwork_archive_reads': 0,
        'run': {'id': run, 'commit_sha': os.environ['GITHUB_SHA']}})
    report['parent_assembly']['catalog_sha256'] = args.catalog_sha256
    report['index_rebuild'].update(catalog_alias_is_byte_identical=False, new_manifest_generated=True,
        derived_features='Derived from the new exact-file observed metadata; no benchmark IDs or answers supplied.')
    report_path = output / (prefix + '.report.json')
    write(report_path, report)
    require(digest(paths['catalog.jsonl']) == args.catalog_sha256, 'Input catalog was changed')
    receipts = []
    if args.publish:
        existing = list_release_assets(repository, args.publish_tag)
        outputs = sorted(output.iterdir())
        require(not any(f.name in existing for f in outputs), 'Refusing to overwrite release assets')
        gh('release', 'upload', args.publish_tag, '--repo', repository, *map(str, outputs))
        published = list_release_assets(repository, args.publish_tag)
        for path in outputs:
            remote = published[path.name]
            require(remote.get('digest') == 'sha256:' + digest(path)
                    and remote.get('size') == path.stat().st_size, 'Published asset differs')
            receipts.append({'name': path.name, 'url': remote['browser_download_url'],
                'sha256': digest(path), 'size_bytes': path.stat().st_size, 'github_asset_id': remote['id']})
    checkpoint = {'assembly_prefix': prefix, 'verification': rebuilt, 'update': change,
        'manifest_sha256': digest(manifest_path), 'report_sha256': digest(report_path),
        'published_assets': receipts, 'acceptance_passed': False}
    write(base / 'checkpoint.json', checkpoint)
    print(json.dumps(checkpoint, ensure_ascii=False))


if __name__ == '__main__':
    main()
