"""Reuse an actual historical restore only for identical download manifests.

The cloud CLI reads four JSON assets and verifies GitHub asset digests. It never
reads an artwork archive. Offline consumers verify this evidence chain; they do
not claim another full restore or independently authenticate hand-written JSON.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from build_publish_pack import (GitHubStore, json_bytes, parse_manifest, plan_manifest,
    release_location, release_url, sha, valid_sha, validate_remote_restore)

OPERATION = 'manifest_metadata_equivalence'
PROOF_TYPE = 'equivalence_non_fresh_artwork_read'
SUFFIXES = {'new_manifest': '.publish_manifest.json', 'restored_manifest': '.publish_manifest.json',
            'historical_bundle': '.bundle.json', 'historical_restore': '.restore_evidence.json'}


def metadata_location(url, repository, role):
    if not isinstance(url, str):
        raise ValueError('Metadata location must be an explicit Release URL')
    tag, name = release_location(url, repository)
    if role not in SUFFIXES or not name.endswith(SUFFIXES[role]):
        raise ValueError('Only the explicitly typed JSON metadata asset can be read')
    return tag, name


def validate_metadata_run(run, repository, old_restore):
    if (not isinstance(run, dict) or run.get('repository') != repository
            or not re.fullmatch(r'[1-9][0-9]*', str(run.get('run_id', '')))
            or not re.fullmatch(r'[1-9][0-9]*', str(run.get('run_attempt', '')))
            or not re.fullmatch(r'[0-9a-f]{40}', str(run.get('commit_sha', '')))
            or not isinstance(run.get('workflow_ref'), str)
            or run['workflow_ref'].split('@', 1)[0] != repository + '/.github/workflows/restore-equivalence.yml'
            or not run['workflow_ref'].partition('@')[2]
            or run.get('url') != f"https://github.com/{repository}/actions/runs/{run.get('run_id')}"):
        raise ValueError('A separate metadata-equivalence workflow run is required')
    previous = {str((old_restore.get(key) or {}).get('run_id')) for key in ('build_run', 'restore_run')}
    if str(run['run_id']) in previous:
        raise ValueError('Metadata verification must use a new run, not the historical build or restore run')


def compare_manifests(new_manifest, restored_manifest):
    """Reject every difference except two different, valid catalog hashes."""
    for value in (new_manifest, restored_manifest):
        if not isinstance(value, dict):
            raise ValueError('Both the new and actually restored historical manifests are required')
        parse_manifest(json_bytes(value), sha(json_bytes(value)))
    new_hash, old_hash = new_manifest['catalog_sha256'], restored_manifest['catalog_sha256']
    if new_hash == old_hash:
        raise ValueError('Equivalence requires different valid old and new catalog hashes')
    old_rebound = {**restored_manifest, 'catalog_sha256': new_hash}
    if json_bytes(new_manifest) != json_bytes(old_rebound):
        raise ValueError('Manifest differs outside the top-level catalog_sha256, including full file records')
    new_objects, new_archives = plan_manifest(new_manifest)
    old_objects, old_archives = plan_manifest(restored_manifest)
    if new_objects != old_objects or new_archives != old_archives:
        raise ValueError('Exact object or archive-member plans differ')
    return {'changed_paths': ['/catalog_sha256'], 'full_manifest_except_catalog_hash_equal': True,
            'complete_file_records_equal': True, 'file_records_sha256': sha(json_bytes(new_manifest['files'])),
            'artwork_object_plan_sha256': sha(json_bytes(new_objects)),
            'source_archive_member_plan_sha256': sha(json_bytes(new_archives)),
            'original_count': len(new_manifest['files']), 'artwork_object_count': len(new_objects),
            'source_archive_count': len(new_archives)}


def validate_restore_equivalence(evidence, new_manifest, restored_manifest, bundle, old_restore, *,
                                 new_manifest_sha256, restored_manifest_sha256,
                                 bundle_sha256, restore_evidence_sha256):
    """Explicit alternative to a fresh artwork read; never weaken full restore."""
    comparison = compare_manifests(new_manifest, restored_manifest)
    documents = {'new_manifest': new_manifest, 'restored_manifest': restored_manifest,
                 'historical_bundle': bundle, 'historical_restore': old_restore}
    hashes = {'new_manifest': new_manifest_sha256, 'restored_manifest': restored_manifest_sha256,
              'historical_bundle': bundle_sha256, 'historical_restore': restore_evidence_sha256}
    if any(not valid_sha(hashes[k]) or hashes[k] != sha(json_bytes(v)) for k, v in documents.items()):
        raise ValueError('Equivalence input bytes do not match their exact supplied hashes')
    validate_remote_restore(old_restore, bundle, restored_manifest,
                            manifest_sha256=restored_manifest_sha256, bundle_sha256=bundle_sha256)
    repository = bundle['repository']
    if (not isinstance(evidence, dict) or evidence.get('schema_version') != 1
            or evidence.get('operation') != OPERATION or evidence.get('proof_type') != PROOF_TYPE
            or evidence.get('passed') is not True
            or type(evidence.get('new_artwork_archive_reads')) is not int or evidence['new_artwork_archive_reads'] != 0
            or evidence.get('fresh_artwork_restore_performed') is not False
            or evidence.get('historical_independent_restore_validated') is not True
            or json_bytes(evidence.get('comparison')) != json_bytes(comparison)
            or not isinstance(evidence.get('checked_at'), str) or not evidence['checked_at'].strip()):
        raise ValueError('Not a complete, separately typed metadata-equivalence proof')
    validate_metadata_run(evidence.get('metadata_run'), repository, old_restore)
    reads = evidence.get('remote_metadata_reads')
    if not isinstance(reads, dict) or set(reads) != set(documents):
        raise ValueError('Exactly four remote JSON metadata reads are required')
    urls = set()
    for role, value in documents.items():
        row = reads[role]
        if not isinstance(row, dict):
            raise ValueError('Malformed metadata read evidence')
        metadata_location(row.get('url', ''), repository, role)
        expected_hash, expected_size = hashes[role], len(json_bytes(value))
        if (row.get('sha256') != expected_hash or row.get('size_bytes') != expected_size
                or row.get('github_digest') != 'sha256:' + expected_hash
                or row.get('github_asset_size') != expected_size
                or type(row.get('github_asset_id')) is not int or row['github_asset_id'] <= 0
                or row.get('complete_read_verified') is not True
                or row.get('github_digest_verified') is not True):
            raise ValueError('Remote metadata bytes/API digest evidence differs from supplied documents')
        urls.add(row['url'])
    if len(urls) != 4:
        raise ValueError('Old and new metadata must have distinct immutable asset locations')
    if (reads['restored_manifest']['url'] != bundle['publish_manifest']['url']
            or reads['historical_bundle']['url'] != old_restore['bundle_url']):
        raise ValueError('Historical metadata locations differ from actual restore evidence')
    if (evidence.get('old_catalog_sha256') != restored_manifest['catalog_sha256']
            or evidence.get('new_catalog_sha256') != new_manifest['catalog_sha256']):
        raise ValueError('Old/new catalog binding differs from the manifests')
    pack_checks = evidence.get('remote_pack_asset_checks')
    if (evidence.get('current_pack_asset_metadata_verified') is not True
            or not isinstance(pack_checks, list) or len(pack_checks) != len(bundle['packs'])
            or any(not isinstance(row, dict) for row in pack_checks)):
        raise ValueError('Current API metadata checks must cover every historical pack')
    checked_packs = {row.get('url'): row for row in pack_checks}
    if len(checked_packs) != len(pack_checks) or set(checked_packs) != {p['url'] for p in bundle['packs']}:
        raise ValueError('Current pack asset check set differs from the bundle')
    for part in bundle['packs']:
        row = checked_packs[part['url']]
        expected = {'url': part['url'], 'sha256': part['sha256'], 'size_bytes': part['size_bytes'],
                    'github_digest': 'sha256:' + part['sha256'], 'github_asset_size': part['size_bytes'],
                    'github_digest_verified': True, 'archive_body_read': False}
        if (any(row.get(k) != v for k, v in expected.items())
                or type(row.get('github_asset_id')) is not int or row['github_asset_id'] <= 0
                or row.get('archive_body_read') is not False or row.get('github_digest_verified') is not True):
            raise ValueError('Current pack asset digest/size differs from the historically restored bundle')
    if len({row['github_asset_id'] for row in pack_checks}) != len(pack_checks):
        raise ValueError('Duplicate GitHub pack asset identity')
    return True


def read_remote_metadata(store, url, expected_sha, role):
    tag, name = metadata_location(url, store.repository, role)
    if not valid_sha(expected_sha):
        raise ValueError('An exact SHA-256 is required before a metadata read')
    data = store.metadata(url, expected_sha)
    value = json.loads(data)
    if data != json_bytes(value):
        raise ValueError('Evidence metadata must use canonical JSON bytes')
    asset = store.assets(tag)[name]
    read = {'url': url, 'sha256': sha(data), 'size_bytes': len(data),
            'github_asset_id': asset['id'], 'github_digest': asset['digest'],
            'github_asset_size': asset['size'], 'complete_read_verified': True,
            'github_digest_verified': True}
    if read['github_digest'] != 'sha256:' + expected_sha or read['github_asset_size'] != len(data):
        raise ValueError('GitHub asset evidence changed during the metadata read')
    return value, read


def current_pack_assets(store, bundle):
    """Query Release asset metadata only, never open a pack body."""
    checks = []
    for part in bundle['packs']:
        tag, name = release_location(part['url'], store.repository)
        row = store.assets(tag).get(name)
        if (not isinstance(row, dict) or row.get('browser_download_url') != part['url']
                or row.get('digest') != 'sha256:' + part['sha256'] or row.get('size') != part['size_bytes']
                or type(row.get('id')) is not int or row['id'] <= 0):
            raise ValueError('Historical pack is missing or its current API digest/size changed')
        checks.append({'url': part['url'], 'sha256': part['sha256'], 'size_bytes': part['size_bytes'],
                       'github_asset_id': row['id'], 'github_digest': row['digest'],
                       'github_asset_size': row['size'], 'github_digest_verified': True,
                       'archive_body_read': False})
    return checks


def verify_remote_equivalence(store, *, new_manifest_url, new_manifest_sha256,
                              bundle_url, bundle_sha256, restore_url, restore_sha256, run):
    # All explicit locations are checked before the first network read.
    for url, role in [(new_manifest_url, 'new_manifest'), (bundle_url, 'historical_bundle'),
                      (restore_url, 'historical_restore')]:
        metadata_location(url, store.repository, role)
    for value in (new_manifest_sha256, bundle_sha256, restore_sha256):
        if not valid_sha(value):
            raise ValueError('All explicit metadata hashes must be valid SHA-256 values')
    bundle, bundle_read = read_remote_metadata(store, bundle_url, bundle_sha256, 'historical_bundle')
    reference = bundle['publish_manifest']
    old_manifest, old_read = read_remote_metadata(store, reference['url'], reference['sha256'], 'restored_manifest')
    new_manifest, new_read = read_remote_metadata(store, new_manifest_url, new_manifest_sha256, 'new_manifest')
    old_restore, restore_read = read_remote_metadata(store, restore_url, restore_sha256, 'historical_restore')
    evidence = {'schema_version': 1, 'operation': OPERATION, 'proof_type': PROOF_TYPE, 'passed': True,
        'checked_at': datetime.now(timezone.utc).isoformat(), 'metadata_run': run,
        'old_catalog_sha256': old_manifest['catalog_sha256'], 'new_catalog_sha256': new_manifest['catalog_sha256'],
        'new_artwork_archive_reads': 0, 'fresh_artwork_restore_performed': False,
        'historical_independent_restore_validated': True,
        'current_pack_asset_metadata_verified': True,
        'remote_pack_asset_checks': current_pack_assets(store, bundle),
        'comparison': compare_manifests(new_manifest, old_manifest),
        'remote_metadata_reads': {'new_manifest': new_read, 'restored_manifest': old_read,
                                  'historical_bundle': bundle_read, 'historical_restore': restore_read},
        'limits': ['Historical full artwork recovery is reused without modifying its evidence.',
                   'This run read four JSON metadata assets, no source or publication artwork archives.',
                   'Current pack existence/digests/sizes were checked using API metadata only, without fresh archive-body reads.',
                   'Scientific relevance, native editing and overall release acceptance are separate.']}
    validate_restore_equivalence(evidence, new_manifest, old_manifest, bundle, old_restore,
        new_manifest_sha256=new_manifest_sha256, restored_manifest_sha256=reference['sha256'],
        bundle_sha256=bundle_sha256, restore_evidence_sha256=restore_sha256)
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--new-manifest-url', required=True)
    parser.add_argument('--new-manifest-sha256', required=True)
    parser.add_argument('--bundle-url', required=True)
    parser.add_argument('--bundle-sha256', required=True)
    parser.add_argument('--restore-url', required=True)
    parser.add_argument('--restore-sha256', required=True)
    parser.add_argument('--repository', default=os.environ.get('GITHUB_REPOSITORY', 'hujizhou35-cmd/MGA-resource'))
    parser.add_argument('--evidence-tag', default='acceptance-20261107')
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--output', type=Path, default=Path('build/restore-equivalence'))
    args = parser.parse_args()
    if not args.evidence_tag or args.evidence_tag.startswith('collection-'):
        raise ValueError('Equivalence proof must use a separate evidence release')
    run = {'repository': args.repository, 'run_id': os.environ.get('GITHUB_RUN_ID'),
           'run_attempt': os.environ.get('GITHUB_RUN_ATTEMPT'), 'commit_sha': os.environ.get('GITHUB_SHA'),
           'workflow_ref': os.environ.get('GITHUB_WORKFLOW_REF'),
           'url': f"https://github.com/{args.repository}/actions/runs/{os.environ.get('GITHUB_RUN_ID')}"}
    validate_metadata_run(run, args.repository, {})
    store = GitHubStore(args.repository)
    evidence = verify_remote_equivalence(store, new_manifest_url=args.new_manifest_url,
        new_manifest_sha256=args.new_manifest_sha256, bundle_url=args.bundle_url,
        bundle_sha256=args.bundle_sha256, restore_url=args.restore_url, restore_sha256=args.restore_sha256, run=run)
    args.output.mkdir(parents=True, exist_ok=True)
    path = args.output / f"restore-equivalence-{run['run_id']}-{run['run_attempt']}.json"
    path.write_bytes(json_bytes(evidence))
    summary = {'operation': OPERATION, 'proof_type': PROOF_TYPE,
               'evidence_sha256': sha(path.read_bytes()), 'metadata_run': run,
               'new_artwork_archive_reads': 0, 'comparison': evidence['comparison']}
    (args.output / 'summary.json').write_bytes(json_bytes(summary))
    if args.publish:
        store.ensure_release(args.evidence_tag)
        store.preflight(args.evidence_tag, [path])
        receipt = store.upload_checked(args.evidence_tag, path)
        (args.output / 'publish-receipt.json').write_bytes(json_bytes(receipt))
    print(json.dumps(summary), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
