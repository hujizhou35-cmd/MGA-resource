"""Evaluate the separately authorized component gate, preserving old scores."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mga_resources.component_acceptance import evaluate_components, require, valid_sha


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block := stream.read(1024 * 1024):
            value.update(block)
    return value.hexdigest()


def read(path):
    return json.loads(Path(path).read_bytes())


def referenced_observations(judgments):
    for query in judgments.get('queries', []):
        for result in query.get('result_judgments', []):
            yield from result.get('visual_evidence', [])
        for source in query.get('source_checks', []):
            yield from source.get('source_evidence', [])
    for task in judgments.get('tasks', []):
        yield from task.get('composition_evidence', [])


def verify_observation_files(judgments):
    """Verify the reviewed metadata/preview bytes, not a source URL's existence."""
    checked = {}
    for reference in referenced_observations(judgments):
        relative = reference.get('path')
        require(isinstance(relative, str) and relative and valid_sha(reference.get('sha256')),
                'Actual review references need an available exact local evidence copy; a URL alone is not a verified viewing')
        path = (ROOT / relative).resolve()
        require(path.is_relative_to(ROOT.resolve()) and path.is_file(), 'Review evidence path is missing or outside this checkout')
        if path not in checked:
            checked[path] = digest(path)
        require(checked[path] == reference['sha256'], 'Review evidence bytes changed: ' + relative)
    return [{'path': path.relative_to(ROOT).as_posix(), 'sha256': value} for path, value in sorted(checked.items())]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--benchmark', type=Path, default=ROOT / 'data/component-benchmark.json')
    parser.add_argument('--freeze', type=Path, default=ROOT / 'data/component-benchmark-freeze.json')
    parser.add_argument('--decision', type=Path, default=ROOT / 'data/acceptance-decisions/component-boundary-20261107.json')
    parser.add_argument('--legacy-benchmark', type=Path, default=ROOT / 'data/benchmark.json')
    for name in ('raw-results', 'judgments', 'legacy-report', 'scope-report', 'manifest', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    manifest, raw, judgments = read(args.manifest), read(args.raw_results), read(args.judgments)
    hashes = {key: digest(path) for key, path in (
        ('component_benchmark_sha256', args.benchmark), ('freeze_sha256', args.freeze),
        ('legacy_benchmark_sha256', args.legacy_benchmark), ('decision_sha256', args.decision),
        ('raw_results_sha256', args.raw_results), ('judgments_sha256', args.judgments),
        ('legacy_report_sha256', args.legacy_report), ('scope_report_sha256', args.scope_report),
        ('publish_manifest_sha256', args.manifest), ('search_core_sha256', ROOT / 'mga_resources/search-core.mjs'))}
    hashes['catalog_sha256'] = manifest['catalog_sha256']
    verified = verify_observation_files(judgments)
    report = evaluate_components(read(args.benchmark), raw, judgments, read(args.legacy_report), read(args.scope_report), manifest,
        hashes=hashes, freeze=read(args.freeze), decision=read(args.decision))
    report['verified_observation_documents'] = verified
    report['raw_replay_run'] = raw.get('metadata_run') or raw.get('run')
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'component-checkreport.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf8', newline='\n')
    retrieval = report['component_retrieval']
    lines = ['# Component retrieval acceptance', '', 'Status: ' + report['website_gate'], '',
             f"Component task-language units: {retrieval['successful_task_language_units']}/120.",
             f"Original benchmark: {report['historical_retrieval']['successful']}/120; preserved as historical evidence.", '',
             'Missing checks:' if report['missing'] else 'Component and existing material gates passed. Website implementation may start.']
    lines.extend(f"- [{row['code']}] {row['detail']}" for row in report['missing'])
    (args.output / 'component-checkreport.md').write_text('\n'.join(lines) + '\n', encoding='utf8', newline='\n')
    print(json.dumps({'website_gate': report['website_gate'], 'component_successful': retrieval['successful_task_language_units'],
                      'historical_successful': report['historical_retrieval']['successful'], 'missing': report['missing']}, ensure_ascii=False))
    return 0 if report['acceptance_passed'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
