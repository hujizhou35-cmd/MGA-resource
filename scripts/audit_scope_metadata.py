"""Cloud-only scope audit of exact metadata, without artwork/archive reads."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from audit_public_scope import audit, records, read_json
from build_publish_pack import GitHubStore, json_bytes, release_location, sha, valid_sha
from restore_equivalence import read_remote_metadata, validate_restore_equivalence
from replay_catalog_metadata import download_metadata, digest, gh, require
from mga_resources.scope_policy import load_scope_policy

PREFIX = re.compile(r'assembly-[0-9]+-[0-9]+')


def bound_asset(assets, name, *, repository, tag, expected_sha=None, expected_size=None):
    row = assets.get(name)
    require(isinstance(row, dict) and row.get('name') == name, 'Exact metadata asset is missing')
    require(release_location(row.get('browser_download_url', ''), repository) == (tag, name), 'Metadata asset URL differs from requested Release/name')
    require(re.fullmatch(r'sha256:[a-f0-9]{64}', row.get('digest', '')) is not None, 'Metadata asset requires GitHub SHA-256')
    require(type(row.get('size')) is int and 0 < row['size'] < 1024 ** 3, 'Metadata size is invalid')
    if expected_sha is not None:
        require(valid_sha(expected_sha) and row['digest'] == 'sha256:' + expected_sha, 'Metadata digest differs from bound evidence')
    if expected_size is not None:
        require(row['size'] == expected_size, 'Metadata size differs from bound evidence')
    return row


def internal_location(report, current_prefix, current_tag):
    require(PREFIX.fullmatch(current_prefix) is not None, 'Invalid current assembly prefix')
    scope = report.get('public_scope') or {}
    binding = scope.get('internal_catalog') or {}
    parent = scope.get('inherited_from_assembly')
    prefix = parent.get('prefix') if isinstance(parent, dict) else current_prefix
    tag = parent.get('release_tag') if isinstance(parent, dict) else current_tag
    require(isinstance(prefix, str) and PREFIX.fullmatch(prefix) is not None and isinstance(tag, str) and bool(tag), 'Invalid bound internal assembly identity')
    name = prefix + '.internal-catalog.jsonl'
    require(binding.get('file') in {'internal-catalog.jsonl', name}, 'Internal catalog filename is not the exact allowed assembly metadata name')
    require(valid_sha(binding.get('sha256')) and type(binding.get('size_bytes')) is int and binding['size_bytes'] > 0,
            'Internal catalog requires its exact SHA-256 and size')
    require(type(binding.get('records')) is int and binding['records'] > 0, 'Internal record count is missing')
    if parent is not None:
        require(valid_sha(parent.get('report_sha256')), 'Inherited internal catalog requires the bound parent report SHA')
    return name, tag, binding, parent


def download_internal(name, asset, destination, repository, tag, binding):
    """One dedicated JSONL role; do not broaden the shared metadata downloader."""
    require(re.fullmatch(r'assembly-[0-9]+-[0-9]+\.internal-catalog\.jsonl', name) is not None,
            'Only an exact assembly internal-catalog JSONL can use this downloader')
    bound_asset({name: asset}, name, repository=repository, tag=tag,
                expected_sha=binding['sha256'], expected_size=binding['size_bytes'])
    destination.mkdir(parents=True, exist_ok=True)
    gh('release', 'download', tag, '--repo', repository, '--pattern', name, '--dir', str(destination), '--clobber')
    path = destination / name
    actual = digest(path)
    require(actual == binding['sha256'] and path.stat().st_size == binding['size_bytes'], 'Downloaded internal metadata differs from assembly/GitHub binding')
    return path, {'name': name, 'url': asset['browser_download_url'], 'release_tag': tag,
                  'sha256': actual, 'size_bytes': path.stat().st_size, 'kind': 'internal_metadata',
                  'github_digest_verified': True}


def read_equivalence(store, url, expected_sha):
    tag, name = release_location(url, store.repository)
    require(re.fullmatch(r'restore-equivalence-[0-9]+-[0-9]+\.json', name) is not None,
            'Only a separately typed equivalence JSON is allowed')
    require(valid_sha(expected_sha), 'Exact equivalence SHA-256 required')
    data = store.metadata(url, expected_sha, limit=8 * 1024 * 1024)
    value = json.loads(data)
    require(data == json_bytes(value), 'Equivalence proof must have canonical JSON bytes')
    receipt = {'url': url, 'sha256': sha(data), 'size_bytes': len(data),
               'github_digest_verified': True, 'kind': 'equivalence_metadata'}
    return value, receipt


def run_cloud_audit(store, *, assembly_prefix, assembly_tag, report_sha256,
                    equivalence_url, equivalence_sha256, output, run):
    require(PREFIX.fullmatch(assembly_prefix) is not None, 'Exact assembly prefix required')
    require(valid_sha(report_sha256), 'Exact assembly report SHA-256 required')
    incoming = output / 'input-metadata'
    proofs = {}
    assets = store.assets(assembly_tag)
    report_name = assembly_prefix + '.report.json'
    asset = bound_asset(assets, report_name, repository=store.repository, tag=assembly_tag, expected_sha=report_sha256)
    report_path, proofs['assembly_report'] = download_metadata(report_name, asset, incoming, store.repository, assembly_tag)
    assembly = read_json(report_path)
    equivalence, proofs['restore_equivalence'] = read_equivalence(store, equivalence_url, equivalence_sha256)
    documents = {}
    for role in ['historical_bundle', 'restored_manifest', 'new_manifest', 'historical_restore']:
        ref = equivalence['remote_metadata_reads'][role]
        documents[role], proofs[role] = read_remote_metadata(store, ref['url'], ref['sha256'], role)
    validate_restore_equivalence(equivalence, documents['new_manifest'], documents['restored_manifest'],
        documents['historical_bundle'], documents['historical_restore'],
        new_manifest_sha256=proofs['new_manifest']['sha256'], restored_manifest_sha256=proofs['restored_manifest']['sha256'],
        bundle_sha256=proofs['historical_bundle']['sha256'], restore_evidence_sha256=proofs['historical_restore']['sha256'])
    catalog_sha = assembly['summary']['catalog_sha256']
    require(catalog_sha == documents['new_manifest']['catalog_sha256'], 'New publication manifest does not bind the input assembly catalog')
    paths = {}
    for suffix in ['catalog.jsonl', 'catalog.sqlite']:
        name = assembly_prefix + '.' + suffix
        asset = bound_asset(assets, name, repository=store.repository, tag=assembly_tag,
                            expected_sha=catalog_sha if suffix == 'catalog.jsonl' else None)
        paths[suffix], proofs[suffix] = download_metadata(name, asset, incoming, store.repository, assembly_tag)
    name, tag, binding, parent = internal_location(assembly, assembly_prefix, assembly_tag)
    internal_assets = store.assets(tag)
    if parent:
        parent_name = parent['prefix'] + '.report.json'
        parent_asset = bound_asset(internal_assets, parent_name, repository=store.repository, tag=tag,
                                   expected_sha=parent['report_sha256'])
        parent_path, proofs['parent_report'] = download_metadata(parent_name, parent_asset, incoming, store.repository, tag)
        require(read_json(parent_path)['public_scope']['internal_catalog'] == binding,
                'Parent report and new report disagree about their internal catalog')
    asset = bound_asset(internal_assets, name, repository=store.repository, tag=tag,
                        expected_sha=binding['sha256'], expected_size=binding['size_bytes'])
    internal_path, proofs['internal_catalog'] = download_internal(name, asset, incoming, store.repository, tag, binding)
    with sqlite3.connect(paths['catalog.sqlite'].resolve().as_uri() + '?mode=ro', uri=True) as db:
        version = db.execute('PRAGMA user_version').fetchone()[0]
    require(version == assembly['summary']['search_index_version'], 'SQLite version differs from the assembly report')
    result = audit(records(paths['catalog.jsonl']), assembly, catalog_sha256=catalog_sha,
        policy=load_scope_policy(), internal=records(internal_path), internal_sha256=proofs['internal_catalog']['sha256'],
        index=paths['catalog.sqlite'], manifest=documents['new_manifest'], manifest_sha256=proofs['new_manifest']['sha256'],
        bundle=documents['historical_bundle'], bundle_sha256=proofs['historical_bundle']['sha256'],
        restore=documents['historical_restore'], restore_sha256=proofs['historical_restore']['sha256'],
        restore_equivalence=equivalence, restore_equivalence_sha256=equivalence_sha256,
        restored_manifest=documents['restored_manifest'], restored_manifest_sha256=proofs['restored_manifest']['sha256'],
        paired_review=read_json(ROOT / 'data/visual-reviews/commons-42260711756-component-scope.json'))
    result.update(operation='cloud_metadata_scope_audit', audit_run=run, input_proofs=proofs,
                  new_artwork_archive_reads=0, new_image_downloads=0,
                  new_internal_catalog_bytes_verified=True, overall_release_acceptance_claimed=False)
    output.mkdir(parents=True, exist_ok=True)
    report_out = output / f"scope-metadata-audit-{run['run_id']}-{run['run_attempt']}.scope-audit.json"
    report_out.write_bytes(json_bytes(result))
    summary = {key: result[key] for key in ['scope_end_to_end_passed', 'failed_checks', 'pending_checks', 'counts',
               'catalog_sha256', 'policy_sha256', 'restore_proof_type', 'new_internal_catalog_bytes_verified',
               'new_artwork_archive_reads', 'overall_release_acceptance_claimed']}
    summary.update(audit_run=run, report_sha256=sha(report_out.read_bytes()), report_size_bytes=report_out.stat().st_size)
    summary_out = output / f"scope-metadata-audit-{run['run_id']}-{run['run_attempt']}.summary.json"
    summary_out.write_bytes(json_bytes(summary))
    return result, [report_out, summary_out]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--assembly-prefix', required=True)
    parser.add_argument('--assembly-release-tag', default='acceptance-20261107')
    parser.add_argument('--assembly-report-sha256', required=True)
    parser.add_argument('--equivalence-url', required=True)
    parser.add_argument('--equivalence-sha256', required=True)
    parser.add_argument('--repository', default=os.environ.get('GITHUB_REPOSITORY', 'hujizhou35-cmd/MGA-resource'))
    parser.add_argument('--evidence-tag', default='acceptance-20261107')
    parser.add_argument('--output', type=Path, default=Path('build/scope-metadata-audit'))
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args()
    require(os.environ.get('GITHUB_ACTIONS') == 'true', 'Large metadata acquisition is cloud-only; do not download the catalog/index/internal on the local workstation')
    require(args.evidence_tag and not args.evidence_tag.startswith('collection-'), 'Use a separate evidence Release')
    run = {'repository': args.repository, 'run_id': os.environ.get('GITHUB_RUN_ID'),
           'run_attempt': os.environ.get('GITHUB_RUN_ATTEMPT'), 'commit_sha': os.environ.get('GITHUB_SHA'),
           'workflow_ref': os.environ.get('GITHUB_WORKFLOW_REF'),
           'url': f"https://github.com/{args.repository}/actions/runs/{os.environ.get('GITHUB_RUN_ID')}"}
    require(re.fullmatch(r'[1-9][0-9]*', str(run['run_id'])) is not None
            and re.fullmatch(r'[1-9][0-9]*', str(run['run_attempt'])) is not None
            and re.fullmatch(r'[a-f0-9]{40}', str(run['commit_sha'])) is not None
            and isinstance(run['workflow_ref'], str)
            and run['workflow_ref'].split('@', 1)[0] == args.repository + '/.github/workflows/scope-metadata-audit.yml'
            and bool(run['workflow_ref'].partition('@')[2]), 'Scope audit must run in the named metadata-only cloud workflow')
    store = GitHubStore(args.repository)
    result, outputs = run_cloud_audit(store, assembly_prefix=args.assembly_prefix,
        assembly_tag=args.assembly_release_tag, report_sha256=args.assembly_report_sha256,
        equivalence_url=args.equivalence_url, equivalence_sha256=args.equivalence_sha256,
        output=args.output, run=run)
    if args.publish:
        store.ensure_release(args.evidence_tag)
        store.preflight(args.evidence_tag, outputs)
        receipts = [store.upload_checked(args.evidence_tag, path) for path in outputs]
        (args.output / 'publish-receipts.json').write_bytes(json_bytes(receipts))
    print(json.dumps({key: result[key] for key in ['scope_end_to_end_passed', 'failed_checks', 'pending_checks', 'restore_proof_type', 'counts']}), flush=True)
    return 0 if result['scope_end_to_end_passed'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
