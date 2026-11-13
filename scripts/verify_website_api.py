"""Compare the live adapter against existing accepted results, with a free read budget."""
import argparse,hashlib,json,os,sys,tarfile,time,urllib.error,urllib.request
from pathlib import Path
from prepare_website import cloud_asset,REPO,TAG,ORIGIN
from replay_catalog_metadata import read,write,require
from assemble import gh

def request(path,payload=None):
    req=urllib.request.Request(ORIGIN+path,data=None if payload is None else json.dumps(payload,ensure_ascii=False).encode(),headers={'Content-Type':'application/json','User-Agent':'MGA-resource-adapter-verifier/1.0'})
    try:
        with urllib.request.urlopen(req,timeout=90) as res:return res.status,json.load(res)
    except urllib.error.HTTPError as error:
        try:body=json.load(error)
        except Exception:body={'error':'non_json_service_failure'}
        return error.code,body

def identity(row):
    file=row['selected_file'];source=row['selected_source']
    return {'asset_id':row['asset_id'],'family_id':row['family_id'],'variant_id':row['variant_id'],
        'file':{k:file.get(k) for k in ('sha256','size_bytes','format','structure','archive','public_scope','text_content','scientific','scientific_review_evidence')},
        'preview_sha256':row['preview']['sha256'],
        'source':{k:source.get(k) for k in ('source','source_id','attribution')},'science':row['selected_science']}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--resume-run');parser.add_argument('--resume-sha');args=parser.parse_args()
    require(os.environ.get('GITHUB_ACTIONS')=='true','Full deployed adapter checks stay in cloud')
    out=Path('build/website-api');out.mkdir(parents=True)
    bundle=cloud_asset('component-benchmark-43481252923-1.tar.gz',TAG,'60b0b4cfd0117f832d3c8ec58ea4f03c44d5e5543f7b2d2e0262da736595e017',out/'input')
    with tarfile.open(bundle) as tar:
        entries=[m for m in tar.getmembers() if m.name.endswith('/raw-component-results.json')]
        require(len(entries)==1,'Raw result not unique');raw_bytes=tar.extractfile(entries[0]).read();raw=json.loads(raw_bytes)
    formal=read(Path('data/acceptance-reports/component-43481252923.json'))
    require(hashlib.sha256(raw_bytes).hexdigest()==formal['bindings']['raw_results_sha256'],'Existing accepted replay differs')
    rows=[];reads=0;stop=None;previous=None;reuse=0;budget=3_000_000
    # Review the original zero-result/failed-role families first, then cover all unchanged inputs once.
    priorities=('blood','microcentrifuge','doctor','oral','dna-binding','pipette','computer','questionnaire')
    queries=sorted(raw['queries'],key=lambda q:(not any(s in q['query_id'] for s in priorities),q['query_id']))
    if args.resume_run:
        require(args.resume_run.isdigit() and args.resume_sha and len(args.resume_sha)==64,'Pinned previous run and digest required')
        previous=read(cloud_asset('website-api-'+args.resume_run+'.json',TAG,args.resume_sha,out/'input'))
        require(previous['accepted_replay']==43481252923 and previous['endpoint']==ORIGIN and previous['raw_results_sha256']==formal['bindings']['raw_results_sha256'],'Resume binding differs')
        require(previous['stop_reason']=='free_read_budget_guard' and not previous['failures'] and previous['checked']==previous['passed'],'Only exactly successful completed queries can be reused')
        require(previous['pending']==[q['query_id'] for q in queries[previous['checked']:]],'Pending query identity/order differs')
        reuse=previous['passed'];queries=queries[previous['checked']:];budget=1_000_000
    code,health=request('/v1/health');require(code==200 and health['status']=='ready','Live catalog is not ready')
    with (out/'live-responses.jsonl').open('w',encoding='utf8') as stream:
        for query in queries:
            if reads>=budget:stop='free_read_budget_guard';break
            started=time.monotonic();code,actual=request('/v1/search',query['request'])
            row={'query_id':query['query_id'],'request':query['request'],'status':code,'seconds':time.monotonic()-started,'response':actual}
            if code!=200:
                row['passed']=False;row['reason']='HTTP service failure';stop='service_failure'
            else:
                same=([identity(r) for r in actual['results']]==[identity(r) for r in query['exact_results']])
                row['passed']=same and actual['bindings']==query['search_bindings'] and actual['next_cursor']==query['next_cursor']
                row['same_result_identity_order']=same
                reads+=actual.get('service',{}).get('d1_rows_read',0)
            rows.append(row);stream.write(json.dumps(row,ensure_ascii=False)+'\n');stream.flush()
            print(json.dumps({'query':query['query_id'],'passed':row['passed'],'d1_rows_read_upper_bound':reads}),flush=True)
            if stop:break
    checks=previous['invalid_request_checks'] if previous else []
    if not previous:
        for payload in ({'query':'mouse','limit':100},{'query':'mouse','cursor':'invalid'},{'query':'mouse','filters':{'unknown_field':'yes'}}):
            code,body=request('/v1/search',payload);checks.append({'request':payload,'status':code,'passed':code==400})
    report={'schema_version':1,'run_id':os.environ['GITHUB_RUN_ID'],'endpoint':ORIGIN,'accepted_replay':43481252923,'raw_results_sha256':hashlib.sha256(raw_bytes).hexdigest(),
        'checked':reuse+len(rows),'passed':reuse+sum(r['passed'] for r in rows),'new_queries_checked':len(rows),'previous_queries_reused':reuse,
        'previous_report':{'run_id':args.resume_run,'sha256':args.resume_sha} if previous else None,
        'failures':[{'query_id':r['query_id'],'status':r['status'],'reason':r.get('reason','Result/binding difference')} for r in rows if not r['passed']],
        'pending':[q['query_id'] for q in queries[len(rows):]],'stop_reason':stop,'d1_rows_read_upper_bound':reads,'d1_writes':0,'invalid_request_checks':checks,
        'total_d1_rows_read_upper_bound':reads+(previous['d1_rows_read_upper_bound'] if previous else 0),
        'responses_sha256':hashlib.sha256((out/'live-responses.jsonl').read_bytes()).hexdigest(),
        'passed_all':reuse+len(rows)==142 and all(r['passed'] for r in rows+checks),'new_visual_reviews':0,'interpretation':'Execution-adapter parity only. Existing file/use reviews reused; this is not a new component score.'}
    filename=out/f'website-api-{os.environ["GITHUB_RUN_ID"]}.json';write(filename,report)
    gh('release','upload',TAG,str(filename),'--repo',REPO)
    require(report['passed_all'],'Deployed adapter verification incomplete or failed; retain exact response evidence')

if __name__=='__main__':main()
