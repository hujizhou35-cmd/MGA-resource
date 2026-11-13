"""Verify current cloud controls and reuse unchanged visual/adoption evidence."""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'scripts')]
from mga_resources.catalog import search_policy_metadata
from mga_resources.supplemental_acceptance import evaluate_supplement
from replay_catalog_metadata import require, digest, read, write

APPEARANCE=ROOT/'data/appearance-reviews/appearance-42289434868-1-v1.1.0-bound-review.json'
LEDGER=ROOT/'data/adoption-reviews/final-validation-ledger-42289434868.json'
OLD_NEGATIVE=ROOT/'build/negative-final-42289434868/raw-negative-results-post-phrase-3.4.3.json'
OLD_NEGATIVE_REVIEW=ROOT/'data/diagnostics/final-negative-observation-42289434868-post-phrase-3.4.3.json'
OLD_SOURCE=ROOT/'data/diagnostics/final-negative-source-binding-42289434868-3.4.3.json'


def ref(path):
    return {'path':Path(path).resolve().relative_to(ROOT).as_posix(),'sha256':digest(path)}


def verify_local(reference):
    p=ROOT/(reference.get('path') or reference.get('uri'))
    require(p.is_file() and digest(p)==reference['sha256'],'Historical evidence changed: '+str(p))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--controls',type=Path,required=True)
    p.add_argument('--receipts',type=Path,required=True)
    p.add_argument('--manifest',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    require(not args.output.exists(),'Refusing to replace a prior control review')
    base=args.controls.resolve(); cloud=read(base/'summary.json');manifest=read(args.manifest)
    bindings=cloud['bindings'];files={f['sha256']:f for f in manifest['files']}
    require(bindings['catalog_sha256']==manifest['catalog_sha256'],'Control catalog differs')
    require(all(bindings[k]==v for k,v in search_policy_metadata().items()),'Control search policy differs')
    receipts=read(args.receipts); members={m['member']:m for r in receipts for m in r.get('members',[])}
    require(all(r['github_digest_and_size_verified'] for r in receipts),'Cloud download receipt missing')
    for path in base.rglob('*.json'):
        suffix='/evidence/'+path.relative_to(base).as_posix()
        found=[m for k,m in members.items() if k.endswith(suffix)]
        require(len(found)==1 and digest(path)==found[0]['sha256'],'Control bytes not bound to downloaded cloud package')
    appearance=read(base/'appearance-filter-replay.json');history=read(base/'actual-history-replay/summary.json')
    behavior=read(base/'behavior/supplemental-search-evidence.json')
    old_appearance=read(APPEARANCE);ledger=read(LEDGER)
    require(cloud['original_adoption_ledger_sha256']==history['bindings']['ledger_sha256']==digest(LEDGER),'Original adoption decisions changed')
    require(appearance['original_review']=={'uri':ref(APPEARANCE)['path'],'sha256':digest(APPEARANCE)},
            'Original appearance observations changed')
    for doc in (appearance,history,behavior):
        require(all(doc['bindings'][k]==bindings[k] for k in
            ('catalog_sha256','benchmark_sha256','search_core_sha256','ranking_version','analysis_version')),'Control input policy mismatch')
    appearance_files={x['original_sha256']:x for case in old_appearance['appearance_cases'] for x in case['real_files']}
    require(len(appearance_files)==len(appearance['files'])==18,'Appearance coverage differs')
    calls=0
    for current in appearance['files']:
        previous=appearance_files[current['original_sha256']];item=files[current['original_sha256']]
        require(current['preview_sha256']==previous['preview_sha256']==item['preview']['sha256']
            and current['original_archive']==previous['catalog_original_archive']==item['archive']
            and current['preview_archive']==previous['catalog_preview_archive']==item['preview']['archive'],
            'Appearance observation borrowed from a different pair')
        for c in current['search_checks']:
            require(bool(c['returned'])==c['expected_hit'],'Current appearance filter failed')
            require(all(x['original_sha256']==current['original_sha256'] and x['preview_sha256']==current['preview_sha256']
                and x['download']==current['original_archive'] for x in c['returned']),'Appearance filter returned another file')
            require(c['bindings']['catalog_version']==bindings['catalog_sha256']
                and c['bindings']['ranking_version']==bindings['ranking_version'],'Appearance execution version differs')
            calls+=1
    require(calls==cloud['appearance_filter_calls']==75,'Appearance calls incomplete')
    original_projects={x['project_id']:x for x in ledger['projects']}
    result=copy.deepcopy(behavior)
    result['appearance_cases']=copy.deepcopy(old_appearance['appearance_cases'])
    result['reviewer']='root: current executed controls with exact prior appearance/adoption observation reuse'
    result['generated_at']=datetime.now(timezone.utc).isoformat()
    result['status']='current_controls_bound_to_unchanged_actual_observations'
    for c in result['functional_cases']:
        path=base/c['evidence']['uri'].split('/evidence/',1)[1]
        require(path.is_file() and digest(path)==c['evidence']['sha256'],'Functional raw evidence differs')
        c['original_cloud_reference']=copy.deepcopy(c['evidence'])
        c['evidence']={'uri':ref(path)['path'],'sha256':digest(path)}
    history_proofs=[]
    for scenario in result['diversity_scenarios']:
        raw_path=base/scenario['evidence']['uri'].split('/evidence/',1)[1]
        require(digest(raw_path)==scenario['evidence']['sha256'],'Diversity raw evidence differs')
        scenario['original_cloud_reference']=copy.deepcopy(scenario['evidence'])
        scenario['evidence']={'uri':ref(raw_path)['path'],'sha256':digest(raw_path)}
        kind=scenario['pool_kind'];h=read(base/'actual-history-replay'/f'{kind}.json')
        actual={x['project_id']:x for x in h['projects']}
        require(h['exact_mode_control']['identical'] is True,'History affected exact mode')
        for project in scenario['projects']:
            prev=original_projects[project['project_id']];current=actual[project['project_id']]
            require(current['fixed_actual_decision']==prev['decision']['selected_files'][0]
                and current['seed']==project['seed']==prev['seed'],'Original exercise choice/seed differs')
            require(current['new_adoption_recorded_from_replay'] is False
                and current['browse_only_changes_rank_or_score'] is False
                and current['qualified_pool_or_science_changed'] is False,'History assertions failed')
            chosen=current['fixed_actual_decision'];item=files[chosen['original_sha256']]
            require(chosen['preview_sha256']==item['preview']['sha256'] and chosen['original_archive']==item['archive']
                and chosen['preview_archive']==item['preview']['archive'],'Adoption observation is a different file')
            documents=[]
            for evidence in prev['decision']['reviewed_evidence']:
                if not evidence.get('uri','').startswith('https://'):
                    verify_local(evidence);documents.append((ROOT/(evidence.get('path') or evidence['uri'])).read_text(encoding='utf8'))
            require(documents,'Actual prior adoption review document missing')
            for evidence in prev['decision']['reviewed_evidence']:
                if evidence.get('uri','').startswith('https://'):
                    require(any(evidence['uri'] in d and evidence['sha256'] in d for d in documents),
                        'Historical remote sheet must be anchored in the verified actual review document')
            for key in ('trial_downloaded_family_ids','adopted_family_ids'):
                require(set(prev[key])<=set(scenario['eligible_family_ids']),'Old choice outside current qualified pool')
                project[key]=copy.deepcopy(prev[key])
            project['decision']=copy.deepcopy(prev['decision'])
            project['ledger_evidence']={'uri':ref(LEDGER)['path'],'sha256':digest(LEDGER)}
            project['decision_origin_catalog_sha256']=ledger['catalog_sha256']
            project['new_adoption_recorded']=False
        scenario['review_notes']+=' Original exercise decisions reused after exact pair checks; this is not new user adoption.'
        history_proofs.append(ref(base/'actual-history-replay'/f'{kind}.json'))
    result['current_execution_evidence']={'appearance':ref(base/'appearance-filter-replay.json'),'history':history_proofs,
        'original_appearance':ref(APPEARANCE),'original_ledger':ref(LEDGER),'cloud_receipts':ref(args.receipts)}
    result['new_visual_inspections']=0;result['new_adoption_decisions']=0
    check=evaluate_supplement(result,catalog_sha256=manifest['catalog_sha256'],benchmark_sha256=bindings['benchmark_sha256'],manifest_files=files)
    # Actual negative queries and all 40 exact source/file slots must still match.
    fresh=read(base/'raw-negative-results.json');old=read(OLD_NEGATIVE);observed=read(OLD_NEGATIVE_REVIEW);source=read(OLD_SOURCE)
    require(observed['source_binding_audit']['sha256']==digest(OLD_SOURCE) and not source['errors'],'Historical source evidence differs')
    old_by={x['scenario']['id']:x for x in old['scenarios']};observed_by={x['scenario_id']:x for x in observed['negative_scenarios']}
    negatives=[];positions=0
    for scenario in fresh['scenarios']:
        sid=scenario['scenario']['id'];previous=old_by[sid];review=observed_by[sid]
        require(scenario['scenario']==previous['scenario'] and scenario['effective_request']==previous['effective_request']==review['request'],
                'Negative request/use changed')
        rows=scenario['raw_result']['results'];prior=previous['raw_result']['results']
        require(not scenario.get('execution_error') and len(rows)==len(prior),'Negative query failed or count changed')
        require(review['observed_candidate_ids']==[r['variant_id'] for r in rows] and review['pass'] is True,'Negative actual view list changed')
        for a,b in zip(rows,prior):
            require(all(a[k]==b[k] for k in ('variant_id','asset_id','family_id','selected_file','selected_source','preview','download')),
                    'Negative current file/source differs; a new review is required')
            f=a['selected_file'];item=files[f['sha256']]
            require(f['archive']==item['archive'] and a['preview']==item['preview']
                and f['source_records']==item['provenance'],'Negative current manifest source differs')
        for c in review['candidates']:verify_local(c['visual_evidence']['review_document'])
        positions+=len(rows)
        negatives.append({**copy.deepcopy(review),'prior_review':ref(OLD_NEGATIVE_REVIEW),
            'current_raw_evidence':ref(base/'raw-negative-results.json'),'exact_current_order_file_source_match':True,
            'new_visual_inspections':0})
    require(len(negatives)==6 and positions==40,'Negative coverage incomplete')
    write(args.output/'supplemental-evidence.json',result)
    write(args.output/'supplement-check.json',check)
    write(args.output/'negative-observations.json',{'bindings':bindings,'negative_scenarios':negatives,
        'historical_source_audit':ref(OLD_SOURCE),'positions':positions,'new_visual_inspections':0})
    summary={'bindings':bindings,'supplement_passed':not check['missing'],'supplement_missing':check['missing'],
        'negative_scenarios_passed':len(negatives),'negative_positions_reused':positions,
        'appearance_filter_calls':calls,'appearance_files_observations_reused':18,'functional_cases':cloud['functional_cases'],
        'original_adoption_decisions_reused':10,'new_visual_inspections':0,'new_adoptions':0,
        'formal_component_acceptance_established':False,'website_release_allowed':False}
    write(args.output/'summary.json',summary);print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__':main()
