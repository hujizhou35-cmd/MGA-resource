"""Summarize partial review honestly; this cannot grant formal acceptance."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'scripts')]
from mga_resources.component_acceptance import validate_benchmark, require, _file_pair
from accept_component_release import verify_observation_files, digest, read


def summarize(benchmark,raw,review,manifest):
    tasks,expected=validate_benchmark(benchmark)
    queries={q['query_id']:q for q in review['queries']};replayed={q['query_id']:q for q in raw['queries']}
    require(set(replayed)==set(expected),'Raw replay is incomplete')
    require(set(queries)<=set(expected),'Review contains foreign queries')
    files={f['sha256']:f for f in manifest['files']}
    statuses={};positions=0
    for qid,q in queries.items():
        actual=replayed[qid];observations={r['variant_id']:r for r in q['result_judgments']}
        require(len(observations)==len(q['result_judgments']),'Duplicate observed position')
        rows={r['variant_id']:r for r in actual['exact_results']}
        require(set(observations)<=set(rows),'Observation does not belong to actual query')
        for vid,o in observations.items():
            row=rows[vid];_file_pair(row,files)
            require(o['rank']==row['rank'] and o['original_sha256']==row['selected_file']['sha256']
                and o['preview_sha256']==row['preview']['sha256'],'Observation binding differs')
        require(q['all_results_reviewed']==(set(observations)==set(rows)),'Review completeness claim differs')
        require(all(vid in observations and observations[vid]['verdict']=='relevant' for vid in q['selected_variant_ids']),
                'Selected component was not reviewed relevant')
        statuses[qid]='complete_with_selected' if q['all_results_reviewed'] and q['selected_variant_ids'] else \
            'complete_without_selected' if q['all_results_reviewed'] else 'partly_reviewed'
        positions+=len(observations)
    task_reviews={(t['task_id'],t['language']):t for t in review['tasks']}
    units=[]
    for tid,task in tasks.items():
        for language in ('zh','en'):
            children=[qid for qid,q in expected.items() if q['task_id']==tid and q['language']==language]
            t=task_reviews.get((tid,language),{})
            exhausted=[qid for qid in children if statuses.get(qid)=='complete_without_selected']
            complete=all(statuses.get(qid)=='complete_with_selected' for qid in children)
            selected={expected[qid]['component_id']:queries.get(qid,{}).get('selected_variant_ids',[]) for qid in children}
            if t.get('composition_use_pass') is True:
                require(t['selected_roles']==selected,'Composition selected files differ')
            state='failed_required_role' if exhausted else 'fully_reviewed_supported' if complete and t.get('composition_use_pass') is True else 'pending'
            units.append({'task_id':tid,'category':task['category'],'language':language,'state':state,'failed_query_ids':exhausted})
    category=[]
    for cid in benchmark['categories']:
        group=[t for t in tasks if tasks[t]['category']==cid['id']]
        impossible={u['task_id'] for u in units if u['category']==cid['id'] and u['state']=='failed_required_role'}
        category.append({'category':cid['id'],'bilingual_task_upper_bound':len(group)-len(impossible),
            'minimum_required':4,'known_threshold_failure':len(group)-len(impossible)<4})
    return {'status':'partial_review_with_explicit_remaining_gaps_not_formal_score',
        'retrieval_queries_executed':len(replayed),'nonempty_queries':sum(bool(q['exact_results']) for q in replayed.values()),
        'query_review_status':{**Counter(statuses.values()),'not_reviewed':len(expected)-len(queries)},
        'result_positions_total':sum(len(q['exact_results']) for q in replayed.values()),'result_positions_reviewed':positions,
        'result_positions_pending':sum(len(q['exact_results']) for q in replayed.values())-positions,
        'task_language_unit_status':dict(Counter(u['state'] for u in units)),
        'recorded_composition_use_true':sum(t.get('composition_use_pass') is True for t in task_reviews.values()),
        'units':units,'categories':category,'formal_component_score':None,'acceptance_passed':False,
        'website_gate':'closed','known_threshold_failure':any(c['known_threshold_failure'] for c in category)}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('raw','review','manifest','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();raw,review,manifest=map(read,(a.raw,a.review,a.manifest))
    require(review['bindings']['raw_results_sha256']==digest(a.raw),'Review raw binding differs')
    require(raw['bindings']['catalog_sha256']==manifest['catalog_sha256']==review['bindings']['catalog_sha256'],'Catalog mismatch')
    checked=verify_observation_files(review)
    result=summarize(read(ROOT/'data/component-benchmark.json'),raw,review,manifest)
    result['bindings']={**raw['bindings'],'raw_results_sha256':digest(a.raw),'judgments_sha256':digest(a.review),
        'publish_manifest_sha256':digest(a.manifest)}
    result['verified_evidence_documents']=len(checked)
    a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({k:v for k,v in result.items() if k not in ['units','categories','bindings']},ensure_ascii=False))


if __name__=='__main__':main()
