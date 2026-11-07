"""Cloud-only index rebuild from exact frozen catalog bytes; no artwork access."""
from __future__ import annotations
import argparse
import copy
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from mga_resources.catalog import rebuild_search_index, search_policy_metadata
from assemble import gh, list_release_assets
from replay_catalog_metadata import digest, download_metadata, read, require, write


def rebuild_and_verify(catalog, index, expected_catalog_sha):
    """Rebuild derived values, preserving all catalog/source/scientific records."""
    catalog, index = Path(catalog), Path(index)
    require(not index.exists(), 'Refusing to replace an existing index')
    require(digest(catalog) == expected_catalog_sha, 'Frozen catalog SHA differs before rebuild')
    policy = search_policy_metadata()
    actual = rebuild_search_index(catalog, index)
    require(actual == expected_catalog_sha == digest(catalog), 'Catalog bytes changed during index rebuild')
    require(search_policy_metadata() == policy, 'Runtime policy changed during rebuild')
    with closing(sqlite3.connect(index.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        require(db.execute('PRAGMA quick_check').fetchone()[0] == 'ok', 'Rebuilt SQLite failed integrity check')
        metadata = dict(db.execute('SELECT key,value FROM index_metadata'))
        require(metadata.get('catalog_version') == expected_catalog_sha
                and all(metadata.get(key) == str(value) for key, value in policy.items()), 'Rebuilt policy/index binding differs')
        require(db.execute('PRAGMA user_version').fetchone()[0] == policy['search_index_version'], 'Rebuilt schema version differs')
        count, files = 0, set()
        with catalog.open(encoding='utf8') as stream:
            for line in stream:
                if not line.strip(): continue
                record = json.loads(line)
                indexed = db.execute('SELECT metadata FROM assets WHERE id=?', (record['id'],)).fetchone()
                require(indexed and json.loads(indexed[0]) == record, 'Indexed source/scientific metadata changed')
                count += 1
                files.update(file['sha256'] for file in record['files'])
        require(count == db.execute('SELECT count(*) FROM assets').fetchone()[0], 'Index has extra or missing source records')
        stats = {'logical_families': count, 'unique_original_files': len(files),
            'derived_variants': db.execute('SELECT count(*) FROM file_variants').fetchone()[0],
            'eligible_derived_variants': db.execute('SELECT count(*) FROM file_variants WHERE eligible=1').fetchone()[0]}
    return {'policy': policy, 'catalog_sha256': expected_catalog_sha, 'sqlite_sha256': digest(index),
        'sqlite_size_bytes': index.stat().st_size, 'source_records_checked': count,
        'source_metadata_semantically_unchanged': True, 'catalog_bytes_unchanged': True, 'stats': stats}


def inherited_report(parent, *, run, source_prefix, source_tag, publish_tag, inputs, rebuilt, manifest):
    report = copy.deepcopy(parent)
    report.update(operation='search_index_only', run_id=run, assembled_at=datetime.now(timezone.utc).isoformat(),
        historical_parent_assembly=parent.get('parent_assembly'),
        parent_assembly={'prefix': source_prefix, 'release_tag': source_tag,
            'catalog_sha256': rebuilt['catalog_sha256'], 'report_sha256': next(r['sha256'] for r in inputs if r['name'].endswith('.report.json'))},
        index_rebuild={'inputs': inputs, 'verification': rebuilt, 'manifest_reference': manifest,
            'old_sqlite_reused': False, 'source_sites_requested': 0, 'artwork_archive_reads': 0,
            'preview_archive_reads': 0, 'new_visual_inspections': 0, 'new_native_tests': 0,
            'new_manifest_generated': False, 'catalog_alias_is_byte_identical': True,
            'derived_features': 'Recomputed values/facts/primary_object/lexical/features from unchanged source evidence; no new scientific source assertion.'},
        previous_benchmark_queries=parent.get('benchmark_queries'), benchmark_queries=0,
        visually_judged_queries=0, retrieval_pass_rate=None, acceptance_passed=False,
        website_gate='closed_pending_retrieval_replay_and_checkpoint_review')
    report['summary'] = {**parent['summary'], **rebuilt['policy'],
        'catalog_sha256': rebuilt['catalog_sha256'], 'sqlite_sha256': rebuilt['sqlite_sha256']}
    report['release_context'] = {**parent.get('release_context', {}), 'publish_tag': publish_tag}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-tag', default='acceptance-20261107')
    parser.add_argument('--assembly-prefix', required=True)
    parser.add_argument('--catalog-sha256', required=True)
    parser.add_argument('--manifest-asset', default='science-update-42320673266.publish_manifest.json')
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--publish-tag', default='acceptance-20261107')
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args()
    if os.environ.get('GITHUB_ACTIONS') != 'true':
        parser.error('Real reindexing is cloud-only; local execution is limited to tiny synthetic tests')
    require(re.fullmatch(r'assembly-[0-9]+-[0-9]+', args.assembly_prefix), 'Exact source assembly prefix required')
    require(all(re.fullmatch(r'[a-f0-9]{64}', value) for value in (args.catalog_sha256, args.manifest_sha256)), 'Exact catalog/manifest hashes required')
    require(re.fullmatch(r'[a-zA-Z0-9_.-]+\.publish_manifest\.json', args.manifest_asset), 'Exact manifest metadata asset required')
    repository = os.environ['GITHUB_REPOSITORY']
    run = os.environ['GITHUB_RUN_ID'] + '-' + os.environ.get('GITHUB_RUN_ATTEMPT', '1')
    base = ROOT / 'build/reindex' / run
    incoming, output = base / 'input', base / 'output'
    require(not output.exists(), 'Refusing to overwrite a previous reindex output')
    output.mkdir(parents=True)
    assets = list_release_assets(repository, args.release_tag)
    manifest_asset = assets[args.manifest_asset]
    require(manifest_asset.get('digest') == 'sha256:' + args.manifest_sha256 and manifest_asset.get('size', 0) > 0,
            'Existing manifest API identity/digest differs')
    manifest = {'url': manifest_asset['browser_download_url'], 'sha256': args.manifest_sha256,
        'size_bytes': manifest_asset['size'], 'github_asset_id': manifest_asset['id'],
        'github_digest_verified': True, 'body_read_in_this_run': False, 'changed': False}
    paths, receipts = {}, []
    for suffix in ('catalog.jsonl', 'report.json'):
        name = args.assembly_prefix + '.' + suffix
        paths[suffix], receipt = download_metadata(name, assets[name], incoming, repository, args.release_tag)
        receipts.append(receipt)
    parent = read(paths['report.json'])
    require(parent['run_id'] == args.assembly_prefix.removeprefix('assembly-')
            and parent['summary']['catalog_sha256'] == digest(paths['catalog.jsonl']) == args.catalog_sha256,
            'Source report/catalog/prefix binding differs')
    prefix = 'assembly-' + run
    catalog = output / (prefix + '.catalog.jsonl')
    shutil.copyfile(paths['catalog.jsonl'], catalog)  # Cloud-only identical alias for existing replay interfaces.
    rebuilt = rebuild_and_verify(catalog, output / (prefix + '.catalog.sqlite'), args.catalog_sha256)
    require(digest(paths['catalog.jsonl']) == args.catalog_sha256, 'Original input catalog was modified')
    report = inherited_report(parent, run=run, source_prefix=args.assembly_prefix, source_tag=args.release_tag,
        publish_tag=args.publish_tag, inputs=receipts, rebuilt=rebuilt, manifest=manifest)
    report['index_rebuild']['metadata_run'] = {'repository': repository, 'run_id': os.environ['GITHUB_RUN_ID'],
        'run_attempt': os.environ.get('GITHUB_RUN_ATTEMPT', '1'), 'commit_sha': os.environ['GITHUB_SHA'],
        'workflow_ref': os.environ.get('GITHUB_WORKFLOW_REF'), 'url': f"https://github.com/{repository}/actions/runs/{os.environ['GITHUB_RUN_ID']}"}
    report_path = output / (prefix + '.report.json')
    write(report_path, report)
    outputs = sorted(output.iterdir())
    receipts_out = []
    if args.publish:
        existing = list_release_assets(repository, args.publish_tag)
        require(not any(path.name in existing for path in outputs), 'Refusing to overwrite any existing output asset')
        gh('release', 'upload', args.publish_tag, '--repo', repository, *map(str, outputs))
        published = list_release_assets(repository, args.publish_tag)
        for path in outputs:
            asset = published[path.name]
            require(asset.get('digest') == 'sha256:' + digest(path) and asset.get('size') == path.stat().st_size,
                    'Published reindex artifact digest/size differs')
            receipts_out.append({'name': path.name, 'url': asset['browser_download_url'], 'sha256': digest(path),
                'size_bytes': path.stat().st_size, 'github_asset_id': asset['id'], 'github_digest_verified': True})
    checkpoint = {'operation': 'search_index_only', 'assembly_prefix': prefix, 'verification': rebuilt,
        'report_sha256': digest(report_path), 'published_assets': receipts_out, 'acceptance_passed': False}
    write(base / 'reindex-checkpoint.json', checkpoint)
    print(json.dumps(checkpoint, ensure_ascii=False))


if __name__ == '__main__':
    main()
