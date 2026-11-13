"""Bounded live interface checks; no catalog-wide fetch or search replay."""
import hashlib,json,os,urllib.error,urllib.parse,urllib.request
from pathlib import Path
from prepare_website import ORIGIN,REPO,TAG
from replay_catalog_metadata import read,write,require
from assemble import gh

def main():
    require(os.environ.get('GITHUB_ACTIONS')=='true','Live contract checks run in cloud')
    out=Path('build/website-contract');out.mkdir(parents=True)
    checks=[];reads=0
    def get(path,status=200,method='GET'):
        url=path if path.startswith('https:') else ORIGIN+path
        req=urllib.request.Request(url,method=method,headers={'User-Agent':'MGA-resource-contract-verifier/1.0'})
        try:response=urllib.request.urlopen(req,timeout=60)
        except urllib.error.HTTPError as error:response=error
        with response:
            body=response.read(2*1024*1024+1)
            row={'url':url,'method':method,'expected_status':status,'status':response.status,'bytes':len(body),'sha256':hashlib.sha256(body).hexdigest(),'cors':response.headers.get('access-control-allow-origin')}
            row['passed']=response.status==status and len(body)<=2*1024*1024
            checks.append(row)
            require(row['passed'],'Unexpected bounded HTTP response: '+url)
            return body,row
    error=None
    try:
        for path in ('/','/agent','/llms.txt','/openapi.json','/version.json','/acceptance.json'):
            body,row=get(path)
            require(row['cors']=='*','Missing public CORS')
            if path=='/version.json':require(json.loads(body)['originals']==19473,'Published count differs')
            if path=='/acceptance.json':require(hashlib.sha256(body).hexdigest()=='59ab4653858ebdd71c1e9b4593d413c5359a72f49e69dd92f311654341247c5b','Component report differs')
        for control in read(Path('data/acceptance-inputs/website-scope-controls.json')):
            body,row=get('/catalog/'+control['id']+'.json',200 if control['public'] else 404)
            if control['public']:
                files=json.loads(body)['files'];selected=[f for f in files if f['sha256']==control['sha256']]
                require(len(selected)==1,'Retained control file absent')
                require(selected[0].get('text_content',{}).get('status')==control['text_status'],'Text status changed')
            else:
                shard=int(control['sha256'][:8],16)%3
                get(f'https://mga-resource-files-{shard}.pages.dev/objects/'+control['sha256']+'.svg',404)
        get('/v1/search',204,'OPTIONS')
        get('/v1/does-not-exist',404)
        body,_=get('/v1/search?q=mouse&limit=5')
        first=json.loads(body);reads+=first['service']['d1_rows_read']
        require(len(first['results'])==5 and first['next_cursor'],'GET search or cursor unavailable')
        for row in first['results']:
            body,_=get(row['metadata_url']);meta=json.loads(body)
            matches=[f for f in meta['files'] if f['sha256']==row['download']['sha256']]
            require(len(matches)==1 and matches[0]['download_url']==row['download']['url'] and matches[0]['preview_url']==row['preview']['url'],'Agent metadata/file link mismatch')
        require(reads<40000,'Bounded contract read budget exceeded')
        params=urllib.parse.urlencode({'q':'mouse','limit':5,'cursor':first['next_cursor']})
        body,_=get('/v1/search?'+params);second=json.loads(body);reads+=second['service']['d1_rows_read']
        require(len(second['results'])==5 and first['bindings']==second['bindings'],'Page binding differs')
        require(not ({r['family_id'] for r in first['results']}&{r['family_id'] for r in second['results']}),'Pagination repeats families')
        write(out/'pages.json',{'first':first,'second':second})
    except Exception as exc:error=str(exc)
    report={'schema_version':1,'run_id':os.environ['GITHUB_RUN_ID'],'endpoint':ORIGIN,'passed':error is None,'failure':error,'checks':checks,'d1_rows_read_upper_bound':reads,'d1_rows_written':0,'scope_controls':6,'new_benchmark_runs':0}
    path=out/f'website-contract-{os.environ["GITHUB_RUN_ID"]}.json';write(path,report)
    gh('release','upload',TAG,str(path),'--repo',REPO)
    require(report['passed'],'Website contract failed; keep actual evidence')

if __name__=='__main__':main()
