"""Actual HTTP reads of every accepted object, entirely on a cloud runner."""
import concurrent.futures,hashlib,json,os,sys,time,urllib.request
from pathlib import Path
from prepare_website import object_url
from replay_catalog_metadata import read,write,require
from assemble import gh

def verify(obj):
    url=object_url(obj);digest=hashlib.sha256();size=0
    try:
        req=urllib.request.Request(url,headers={'Accept-Encoding':'identity','User-Agent':'MGA-resource-delivery-verifier/1.0'})
        with urllib.request.urlopen(req,timeout=90) as response:
            status=response.status;ctype=response.headers.get('content-type');cors=response.headers.get('access-control-allow-origin')
            while block:=response.read(1024*1024):
                size+=len(block)
                if size>obj['size_bytes']:raise ValueError('Response exceeds expected size')
                digest.update(block)
        passed=status==200 and size==obj['size_bytes'] and digest.hexdigest()==obj['sha256']
        return {'sha256':obj['sha256'],'url':url,'passed':passed,'received_sha256':digest.hexdigest(),'received_size':size,'status':status,'content_type':ctype,'cors':cors}
    except Exception as error:
        return {'sha256':obj['sha256'],'url':url,'passed':False,'error':str(error),'received_size':size}

def main():
    require(os.environ.get('GITHUB_ACTIONS')=='true','All-object delivery checks are cloud-only')
    out=Path('build/website-delivery');out.mkdir(parents=True)
    objects=read(Path('build/website/delivery-objects.json'))
    receipt=read(Path('build/website/prepare-receipt.json'))
    require(len(objects)==receipt['objects']==38748,'Object set differs from preparation')
    results=[];started=time.monotonic()
    with (out/'object-http-results.jsonl').open('w',encoding='utf8') as stream,concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        futures=[pool.submit(verify,obj) for obj in objects.values()]
        for future in concurrent.futures.as_completed(futures):
            row=future.result();results.append(row);stream.write(json.dumps(row,ensure_ascii=False)+'\n')
            if len(results)%2000==0:print(json.dumps({'checked':len(results),'failed':sum(not r['passed'] for r in results)}),flush=True)
    failed=[r for r in results if not r['passed']]
    report={'schema_version':1,'run_id':os.environ['GITHUB_RUN_ID'],'catalog_sha256':receipt['accepted_bindings']['catalog_sha256'],'objects_expected':len(objects),'http_reads':len(results),'passed':len(results)-len(failed),'failed':failed,'elapsed_seconds':time.monotonic()-started,'all_bytes_read_in_cloud':True,'local_library_download':False,'results_sha256':hashlib.sha256((out/'object-http-results.jsonl').read_bytes()).hexdigest()}
    write(out/f'website-http-delivery-{os.environ["GITHUB_RUN_ID"]}.json',report)
    gh('release','upload','acceptance-20261107',str(out/f'website-http-delivery-{os.environ["GITHUB_RUN_ID"]}.json'),'--repo','hujizhou35-cmd/MGA-resource')
    require(not failed,'Published object delivery failed; inspect exact failures, do not silently drop files')

if __name__=='__main__':main()
