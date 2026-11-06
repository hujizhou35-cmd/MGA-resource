"""Build a <=32 MiB visual-review packet from independently restored cloud data.

No network access, new source collection, original edits or automatic visual
acceptance. The CLI is Actions-only; unit tests call the core with tiny synthetic
restores. Only PNG preview roles are selected for output; any byte identity with
an original role is disclosed. Historical curated opinions remain historical.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tarfile
import tempfile

from PIL import Image, __version__ as PILLOW_VERSION

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import build_publish_pack as recovery
from scripts import check_preview_composition as renderer

VERSION = 'restored-curated-review-1.0.0'
MAX_BYTES = 32 * 1024 * 1024
MAX_PREVIEW_BYTES = 8 * 1024 * 1024
MAX_PREVIEW_PIXELS = 4096 * 4096


def write_json(path: Path, value: dict) -> None:
    path.write_bytes(recovery.json_bytes(value))


def read_bound_inputs(restore_dir: Path, bundle_sha256: str):
    index_bytes = (restore_dir / 'bundle.json').read_bytes()
    if recovery.sha(index_bytes) != bundle_sha256:
        raise ValueError('Bundle SHA-256 differs from explicit caller binding')
    index = json.loads(index_bytes)
    if index_bytes != recovery.json_bytes(index):
        raise ValueError('Restored bundle must retain exact canonical index bytes')
    manifest_bytes = (restore_dir / 'publish_manifest.json').read_bytes()
    manifest_sha = recovery.sha(manifest_bytes)
    manifest = recovery.parse_manifest(manifest_bytes, index['publish_manifest']['sha256'])
    evidence_bytes = (restore_dir / 'restore_evidence.json').read_bytes()
    evidence = json.loads(evidence_bytes)
    recovery.validate_remote_restore(evidence, index, manifest,
                                     manifest_sha256=manifest_sha, bundle_sha256=bundle_sha256)
    return index, manifest, evidence, {
        'bundle_sha256': bundle_sha256, 'publish_manifest_sha256': manifest_sha,
        'restore_evidence_sha256': recovery.sha(evidence_bytes),
        'restore_evidence_size_bytes': len(evidence_bytes),
    }


def verified_object(restore_dir: Path, obj: dict, verified: dict) -> Path:
    path = restore_dir / obj['path']
    objects_root = (restore_dir / 'objects').resolve()
    # Index validation enforces content-addressed paths; do not follow a staged
    # symlink out of objects even when its target happens to contain equal bytes.
    if ((restore_dir / 'objects').is_symlink() or path.is_symlink() or not path.is_file()
            or not path.resolve().is_relative_to(objects_root)):
        raise ValueError('Restored artwork object is missing or escapes objects directory')
    if obj['sha256'] not in verified:
        proof = recovery.file_proof(path)
        if proof != {key: obj[key] for key in ('sha256', 'size_bytes')}:
            raise ValueError('Restored artwork bytes changed after restore verification')
        verified[obj['sha256']] = proof
    return path


def source_key(value: dict):
    return tuple(value.get(key) for key in ('source', 'source_id', 'source_url', 'license', 'attribution'))


def validate_selection(curated: dict, manifest: dict, index: dict, restore_dir: Path, expected_count: int):
    entries = curated.get('curated')
    if not 1 <= expected_count <= 48 or not isinstance(entries, list) or len(entries) != expected_count:
        raise ValueError('Curated document must contain the complete expected selection')
    if curated.get('catalog_binding', {}).get('sha256') != manifest['catalog_sha256']:
        raise ValueError('Curated selection belongs to a different frozen catalog')
    files = {item['sha256']: item for item in manifest['files']}
    objects = {obj['sha256']: obj for part in index['packs'] for obj in part['members']}
    seen, verified, selected = set(), {}, []
    for ordinal, entry in enumerate(entries, 1):
        original_sha, preview_sha = entry.get('original_sha256'), entry.get('preview_sha256')
        if original_sha in seen or original_sha not in files:
            raise ValueError('Curated original is duplicated or missing from restored publication manifest')
        seen.add(original_sha)
        item = files[original_sha]
        preview = item.get('preview') or {}
        if (preview.get('sha256') != preview_sha or item.get('archive') != entry.get('original_archive')
                or preview.get('archive') != entry.get('preview_archive')
                or item.get('format') != entry.get('format') or item.get('structure') != entry.get('structure')):
            raise ValueError('Curated exact original/preview pair or source archive binding mismatch')
        sources, provenance = entry.get('source_records'), item.get('provenance')
        if not isinstance(sources, list) or not sources or not isinstance(provenance, list):
            raise ValueError('Exact selected-source license/credit provenance is missing')
        if any(not any(source_key(source) == source_key(candidate) for candidate in provenance) for source in sources):
            raise ValueError('Curated source identity or license/credit differs from publication manifest')
        original_obj, preview_obj = objects[original_sha], objects[preview_sha]
        if 'original' not in original_obj['roles'] or 'preview' not in preview_obj['roles'] or preview_obj['format'] != 'png':
            raise ValueError('Bundle roles do not bind the exact original and PNG preview')
        original_path = verified_object(restore_dir, original_obj, verified)
        preview_path = verified_object(restore_dir, preview_obj, verified)
        if preview_obj['size_bytes'] > MAX_PREVIEW_BYTES:
            raise ValueError('Selected preview exceeds the bounded QA input size')
        with Image.open(preview_path) as image:
            if image.format != 'PNG' or image.width * image.height > MAX_PREVIEW_PIXELS:
                raise ValueError('Selected preview is not a bounded actual PNG')
            actual_size = image.size
            image.verify()
        if (preview.get('width') is not None and preview['width'] != actual_size[0]
                or preview.get('height') is not None and preview['height'] != actual_size[1]):
            raise ValueError('Actual PNG dimensions differ from publication preview metadata')
        selected.append({'ordinal': ordinal, 'entry': entry, 'manifest_file': item,
                         'original_object': original_obj, 'preview_object': preview_obj,
                         'original_path': original_path, 'preview_path': preview_path,
                         'preview_size': actual_size})
    return selected, verified


def build_review(restore_dir: Path, curated_path: Path, *, bundle_sha256: str, output: Path,
                 prefix: str, expected_count: int = 48, max_pack_bytes: int = MAX_BYTES) -> dict:
    """Core accepts tiny synthetic restores in tests; real artwork is cloud-only."""
    if not recovery.NAME.fullmatch(prefix) or len(prefix) > 120:
        raise ValueError('Invalid small review artifact prefix')
    if not 0 < max_pack_bytes <= MAX_BYTES:
        raise ValueError('Review packet limit must not exceed 32 MiB')
    if output.exists() and any(output.iterdir()):
        raise ValueError('Review output must be empty; existing evidence is never overwritten')
    index, manifest, evidence, bindings = read_bound_inputs(restore_dir, bundle_sha256)
    curated_bytes = curated_path.read_bytes()
    curated = json.loads(curated_bytes)
    selected, verified = validate_selection(curated, manifest, index, restore_dir, expected_count)
    bindings.update(curated_sha256=recovery.sha(curated_bytes), catalog_sha256=manifest['catalog_sha256'],
                    curated_original_manifest_binding=curated.get('manifest_binding'),
                    curated_original_catalog_binding=curated.get('catalog_binding'))
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.restored-curated-review-', dir=output.parent) as temp:
        work = Path(temp)
        payload = work / 'payload'
        payload.mkdir()
        (payload / 'curated-original.json').write_bytes(curated_bytes)
        (payload / 'previews').mkdir()
        inventory = {}
        mapped = []
        for item in selected:
            entry, obj = item['entry'], item['preview_object']
            destination = payload / 'previews' / f"{obj['sha256']}.png"
            if not destination.exists():
                shutil.copyfile(item['preview_path'], destination)
            inventory[entry['original_sha256']] = [{'path': str(item['preview_path'])}]
            mapped.append({
                **{key: entry.get(key) for key in ('candidate_id', 'family_id', 'asset_id', 'title', 'categories',
                    'original_sha256', 'preview_sha256', 'original_archive', 'preview_archive', 'format', 'structure',
                    'source_records', 'all_provenance', 'text_content')},
                'ordinal': item['ordinal'], 'preview_member': destination.relative_to(payload).as_posix(),
                'preview_size_bytes': obj['size_bytes'], 'preview_dimensions': list(item['preview_size']),
                'clean_original_object_path': item['original_object']['path'],
                'clean_preview_object_path': obj['path'],
                'preview_bytes_also_have_original_role': 'original' in obj['roles'],
                'original_and_preview_restored_bytes_rehashed': True,
                'publication_source_provenance': item['manifest_file']['provenance'],
                'display_conditions': [], 'alpha_statistics': None,
                'display_visual_review_status': 'pending_actual_inspection', 'display_visual_pass': None,
                'scientific_acceptance_granted': False, 'native_editability_tested': False,
            })
        sheets = []
        for batch_number, start in enumerate(range(0, len(selected), 12), 1):
            batch = selected[start:start + 12]
            batch_name = f'batch-{batch_number:02d}'
            subset = {'curated': [item['entry'] for item in batch]}
            subset_path = work / f'{batch_name}.curated.json'
            inventory_path = work / f'{batch_name}.inventory.json'
            write_json(subset_path, subset)
            write_json(inventory_path, {'exact_local_previews': inventory})
            result = renderer.build(subset_path, inventory_path, work / batch_name, max_items=12)
            if len(result['items']) != len(batch):
                raise ValueError('Renderer did not include every exact selected preview')
            dest_dir = payload / 'batches' / batch_name
            dest_dir.mkdir(parents=True)
            for sheet in result['sheets']:
                shutil.copyfile(work / batch_name / sheet['path'], dest_dir / sheet['path'])
                sheets.append({**sheet, 'path': f"batches/{batch_name}/{sheet['path']}",
                               'batch': batch_number, 'visual_review_status': 'pending_actual_inspection'})
            for offset, rendered in enumerate(result['items']):
                target = mapped[start + offset]
                target['alpha_statistics'] = rendered['alpha_statistics']
                target['display_conditions'].extend({**condition, 'sheet': f"batches/{batch_name}/{condition['sheet']}"}
                                                    for condition in rendered['display_conditions'])
            # Existing renderer intentionally caps adjacency at nine, so cover
            # 10–12 in another adjacency board without packaging duplicate detail sheets.
            if len(batch) > 9:
                tail_path = work / f'{batch_name}.tail.json'
                write_json(tail_path, {'curated': [item['entry'] for item in batch[9:]]})
                tail = renderer.build(tail_path, inventory_path, work / (batch_name + '-tail'), max_items=3)
                name = 'adjacency-10-12.png'
                shutil.copyfile(work / (batch_name + '-tail') / 'adjacency.png', dest_dir / name)
                tail_sheet = next(s for s in tail['sheets'] if s['path'] == 'adjacency.png')
                sheets.append({**tail_sheet, 'path': f'batches/{batch_name}/{name}', 'batch': batch_number,
                               'visual_review_status': 'pending_actual_inspection'})
                for offset, rendered in enumerate(tail['items']):
                    mapped[start + 9 + offset]['display_conditions'].extend(
                        {**condition, 'sheet': f'batches/{batch_name}/{name}'}
                        for condition in rendered['display_conditions'] if condition['sheet'] == 'adjacency.png')
        for item in mapped:
            conditions = item['display_conditions']
            if len(conditions) != 7 or not any(c['background'] == 'dark' and 'adjacency' in c['sheet'] for c in conditions):
                raise ValueError('Every selected file requires all five individual and both adjacency views')
        mapping = {
            'schema_version': 1, 'tool_version': VERSION, 'renderer_version': renderer.VERSION,
            'pillow_version': PILLOW_VERSION, 'bindings': bindings,
            'build_run': index['build_run'], 'restore_run': evidence['restore_run'],
            'independent_restore_chain_validated': True,
            'restore_evidence': {'sha256': bindings['restore_evidence_sha256'],
                'operation': evidence['operation'], 'bundle_url': evidence['bundle_url'],
                'remote_pack_count': evidence['pack_count'], 'all_member_sha256_verified': True},
            'curated_input_member': 'curated-original.json', 'items': mapped, 'sheets': sheets,
            'backgrounds': renderer.BACKGROUNDS,
            'resize': 'Uniform LANCZOS fit, no upscaling or crop; 256/96/48px individual and 208px adjacency extents',
            'visual_review_status': 'pending_actual_inspection', 'visual_acceptance_passed': False,
            'limits': ['Historical usable opinions in curated-original.json are not new display QA judgments.',
                'Technical contact sheets do not establish scientific workflow, style coherence or native editability.',
                'Only PNG preview roles are packaged. Byte identity with an original role is explicitly reported; no separate original-role member is selected and no bytes are modified.',
                'Original and preview hashes bind each exact pair; no same-family visual propagation.'],
        }
        write_json(payload / 'mapping.json', mapping)
        summary = {
            'schema_version': 1, 'tool_version': VERSION, 'prefix': prefix, 'bindings': bindings,
            'curated_count': len(selected), 'batch_count': (len(selected) + 11) // 12, 'max_batch_size': 12,
            'comparison_sheets': sum('comparison-' in s['path'] for s in sheets),
            'adjacency_sheets': sum('adjacency' in s['path'] for s in sheets),
            'sheet_count': len(sheets), 'individual_display_coverage': len(mapped), 'adjacency_display_coverage': len(mapped),
            'unique_preview_members': len({i['preview_sha256'] for i in mapped}),
            'restored_objects_rehashed': len(verified),
            'separate_original_role_members_in_output': 0,
            'artwork_original_members_in_output': len({i['preview_sha256'] for i in mapped if i['preview_bytes_also_have_original_role']}),
            'preview_role_members_also_original_bytes': len({i['preview_sha256'] for i in mapped if i['preview_bytes_also_have_original_role']}),
            'new_source_requests': 0, 'new_artwork_downloads': 0,
            'mapping_sha256': recovery.file_proof(payload / 'mapping.json')['sha256'],
            'build_run': index['build_run'], 'restore_run': evidence['restore_run'],
            'visual_review_status': 'pending_actual_inspection', 'visual_acceptance_passed': False,
            'scientific_acceptance_passed': False, 'native_editability_tested': False,
        }
        write_json(payload / 'summary.json', summary)
        payload_paths = sorted(p for p in payload.rglob('*') if p.is_file())
        payload_bytes = sum(p.stat().st_size for p in payload_paths)
        if payload_bytes > max_pack_bytes:
            raise ValueError('Bounded review payload exceeds 32 MiB; no partial acceptance packet emitted')
        archive_path = work / f'{prefix}.tar.gz'
        with archive_path.open('wb') as raw:
            with gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as compressed:
                with tarfile.open(fileobj=compressed, mode='w|', format=tarfile.USTAR_FORMAT) as tar:
                    for path in payload_paths:
                        info = tarfile.TarInfo(path.relative_to(payload).as_posix())
                        info.size, info.mode, info.mtime = path.stat().st_size, 0o644, 0
                        with path.open('rb') as stream:
                            tar.addfile(info, stream)
        proof = recovery.file_proof(archive_path)
        if proof['size_bytes'] > max_pack_bytes:
            raise ValueError('Compressed review packet exceeds 32 MiB')
        expected = {p.relative_to(payload).as_posix(): recovery.file_proof(p) for p in payload_paths}
        with tarfile.open(archive_path, 'r:gz') as tar:
            observed = {}
            for member in tar:
                if not member.isfile() or member.name not in expected or member.name in observed:
                    raise ValueError('Generated bounded packet has unexpected members')
                content = tar.extractfile(member).read()
                observed[member.name] = {'sha256': recovery.sha(content), 'size_bytes': len(content)}
            if observed != expected:
                raise ValueError('Generated bounded packet verification failed')
        output.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(archive_path, output / archive_path.name)
        shutil.copyfile(payload / 'mapping.json', output / f'{prefix}.mapping.json')
        external_summary = {**summary, 'review_archive': {'name': archive_path.name, **proof},
                            'uncompressed_payload_bytes': payload_bytes, 'member_count': len(payload_paths),
                            'archive_exact_members_verified': True, 'max_review_bytes': max_pack_bytes}
        write_json(output / f'{prefix}.summary.json', external_summary)
    return external_summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--restore-dir', type=Path, required=True)
    parser.add_argument('--curated', type=Path, required=True)
    parser.add_argument('--bundle-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--prefix', required=True)
    args = parser.parse_args(argv)
    if os.environ.get('GITHUB_ACTIONS') != 'true':
        parser.error('Real restored artwork review is cloud-only; use tiny synthetic tests locally.')
    _, _, evidence, _ = read_bound_inputs(args.restore_dir, args.bundle_sha256)
    current = recovery.actions_run(os.environ.get('GITHUB_REPOSITORY', ''))
    if current != evidence['restore_run']:
        parser.error('Review must run on the same verified restore runner and commit.')
    summary = build_review(args.restore_dir, args.curated, bundle_sha256=args.bundle_sha256,
                           output=args.output, prefix=args.prefix)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
