"""Restore the accepted cloud packs into free static shards; never run locally."""
import json, os, shutil, subprocess, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from build_publish_pack import GitHubStore, extract_selected, file_proof
from prepare_website import REPO, FILE_TAG, cloud_asset, object_url
from replay_catalog_metadata import read, write, require

ROOT=Path(__file__).resolve().parents[1]
def main():
    require(os.environ.get('GITHUB_ACTIONS')=='true','Artwork deployment must stay in cloud')
    require(read(ROOT/'data/diagnostics/cloudflare-free-plan-20261114.json')['observed_current_plan']=='Free','Free plan verification absent')
    out=ROOT/'build/website-files';out.mkdir(parents=True)
    bundle=read(cloud_asset('qa-20261107-42289434868-3.4.3.bundle.json','publish-qa-20261107-42289434868-3.4.3','54b055c631c5fd814a7012f176151eb86d65c66849f8b2c71ea3a53ae44dc3a0',out/'input'))
    expected=read(ROOT/'build/website/delivery-objects.json')
    actual={m['sha256']:m for p in bundle['packs'] for m in p['members']}
    require(set(expected)==set(actual) and all(expected[k]['size_bytes']==actual[k]['size_bytes'] and expected[k]['format']==actual[k]['format'] for k in expected),'Accepted object set differs')
    store=GitHubStore(REPO)
    # Existing repository only; the new release contains independently downloadable large originals.
    try:store.assets(FILE_TAG)
    except RuntimeError as error:
        if 'HTTP 404' not in str(error):raise
        store.gh('release','create',FILE_TAG,'--repo',REPO,'--target',os.environ['GITHUB_SHA'],'--title','MGA-resource original files v1','--notes','Accepted medical drawing originals larger than the free Pages per-file limit. Each artwork retains its own license and attribution; see the public catalog. Bytes are unchanged.')
    shards=[out/f'shard-{i}' for i in range(3)]
    for shard in shards:
        (shard/'objects').mkdir(parents=True)
        (shard/'_headers').write_text('/*\n  Access-Control-Allow-Origin: *\n  Cache-Control: public, max-age=31536000, immutable\n  X-Content-Type-Options: nosniff\n  Content-Security-Policy: sandbox; default-src \'none\'; style-src \'unsafe-inline\'; img-src data:\n',encoding='utf8')
        (shard/'404.html').write_text('<!doctype html><title>Not found</title><p>Object not in published catalog.</p>',encoding='utf8')
    reads=[];large=[];counts=[0,0,0]
    staging=out/'staging';staging.mkdir()
    for part in bundle['packs']:
        proof={k:part[k] for k in ('sha256','size_bytes')}
        require(store.proof(part['url'])==proof,'Remote pack differs')
        reads.append(extract_selected(store.open,part['url'],proof,{m['path']:m for m in part['members']},staging,exact=True))
        for obj in part['members']:
            source=staging/obj['sha256'];require(file_proof(source)=={k:obj[k] for k in ('sha256','size_bytes')},'Staged artwork differs')
            if obj['size_bytes']>=25*1024*1024:
                target=out/(obj['sha256']+'.'+obj['format']);shutil.move(source,target)
                large.append(store.upload_checked(FILE_TAG,target))
            else:
                shard=int(obj['sha256'][:8],16)%3;target=shards[shard]/obj['path'];shutil.move(source,target);counts[shard]+=1
    require(sum(counts)+len(large)==len(expected) and max(counts)<19000,'Static file count mismatch')
    deployments=[]
    write(out/'files-preupload.json',{'run_id':os.environ['GITHUB_RUN_ID'],'objects':len(expected),'counts':counts,'cloud_pack_reads':reads,'large_direct_files':large})
    for i,folder in enumerate(shards):
        # Every destination file was hashed after full cloud-pack verification.
        # A static-only working directory prevents the main site's Functions/config from being attached to artwork hosts.
        result=subprocess.run(['node',str(ROOT/'site/node_modules/wrangler/bin/wrangler.js'),'pages','deploy',str(folder),'--project-name',f'mga-resource-files-{i}','--branch','main','--commit-dirty=true'],cwd=out,check=False,capture_output=True,text=True)
        log=out/f'shard-{i}-deployment.txt';log.write_text(result.stdout+'\n'+result.stderr,encoding='utf8');print(result.stdout);print(result.stderr)
        result.check_returncode()
        deployments.append({'project':f'mga-resource-files-{i}','objects':counts[i],'log':log.name})
    receipt={'schema_version':1,'run_id':os.environ['GITHUB_RUN_ID'],'objects':len(expected),'all_original_bytes_preserved':True,'all_object_hashes_verified_before_upload':True,'cloud_pack_reads':reads,'large_direct_files':large,'static_deployments':deployments,'http_delivery_verification':'pending separate real remote reads'}
    write(out/'files-deployment.json',receipt)
    copy=out/f'website-files-{os.environ["GITHUB_RUN_ID"]}.json';write(copy,receipt)
    store.gh('release','upload','acceptance-20261107',str(copy),'--repo',REPO)

if __name__=='__main__':main()
