// Node 22+; no dependencies. Run without arguments to display usage.
import {readFile,writeFile,mkdir,rename} from 'node:fs/promises';
import {createReadStream,createWriteStream} from 'node:fs';
import {dirname,join} from 'node:path';
import {createHash,randomUUID} from 'node:crypto';
import {Transform,Readable} from 'node:stream';
import {pipeline} from 'node:stream/promises';
import {rm} from 'node:fs/promises';

const base=new URL(process.env.MGA_ENDPOINT||'https://mga-resource-mainly-for-agent.pages.dev');
if(base.protocol!=='https:'||!/(^|\.)mga-resource-mainly-for-agent\.pages\.dev$/.test(base.hostname))throw Error('Invalid MGA endpoint');
const projectFormat='mga-project-v1';
const read=async p=>JSON.parse(await readFile(p,'utf8'));
const put=(p,v,flag='wx')=>writeFile(p,JSON.stringify(v,null,2)+'\n',{flag});
const validId=x=>typeof x==='string'&&/^[a-zA-Z0-9_-]{1,160}$/.test(x);
async function load(p){
  const x=await read(p);
  if(x.schema!==projectFormat||!validId(x.id)||!validId(x.seed)||!Number.isSafeInteger(x.round)||x.round<0||
      !['selected','saved','adopted'].every(key=>Array.isArray(x[key])&&x[key].length<=200))throw Error('Invalid MGA project '+p);
  return x;
}
function checkFile(item){
  if(!item||!validId(item.asset_id)||!validId(item.family_id))throw Error('Invalid asset identity');
  const {url,sha256,size_bytes,format}=item.download;
  const u=new URL(url),file=sha256+'.'+format;
  if(u.protocol!=='https:'||!/^([a-f0-9]{64})$/.test(sha256)||!['svg','png','jpg','jpeg'].includes(format)||
    !Number.isSafeInteger(size_bytes)||size_bytes<1||size_bytes>64*1024*1024||
    !((/^mga-resource-files-[012]\.pages\.dev$/.test(u.hostname)&&u.pathname==='/objects/'+file)||
      (u.hostname==='github.com'&&u.pathname==='/hujizhou35-cmd/MGA-resource/releases/download/website-files-v1/'+file)))throw Error('Invalid published download link');
  return file;
}
const [cmd,path,...args]=process.argv.slice(2);
try{
  if(cmd==='init'&&path){await put(path,{schema:projectFormat,id:randomUUID(),seed:randomUUID(),round:0,name:args[0]||'New project',style:{},selected:[],saved:[],adopted:[]});}
  else if(cmd==='search'&&path&&args.length>=2){
    const p=await load(path),[query,output,...previous]=args;
    const others=await Promise.all(previous.map(load));
    const adopted=[...new Map(others.filter(x=>x.id!==p.id).map(x=>[x.id,x])).values()].flatMap(x=>[...new Set(x.adopted.map(y=>y.family_id))].map(family_id=>({family_id,status:'adopted'})));
    const payload={query,mode:Object.keys(p.style||{}).length?'design':'explore',seed:p.seed+'-'+p.round,project:{style:p.style||{},selected:p.selected.map(({asset_id,family_id,file_sha256})=>({asset_id,family_id,file_sha256}))},history:{adopted},limit:10,response_profile:'compact'};
    const body=JSON.stringify(payload);
    if(Buffer.byteLength(body)>16384)throw Error('Project request exceeds 16 KiB');
    const res=await fetch(new URL('/v1/search',base),{method:'POST',headers:{'Content-Type':'application/json'},body,signal:AbortSignal.timeout(30000)});
    if(!res.ok)throw Error(`Search HTTP ${res.status}; Retry-After: ${res.headers.get('Retry-After')||'n/a'}`);
    const page=await res.json();await put(output,{project_id:p.id,seed:payload.seed,page});
    for(const [i,c] of page.results.entries())console.log(i+1,c.title,'\n   preview:',c.preview.url);
    console.log('Inspect previews before adopting a file.');
  }else if(cmd==='adopt'&&path&&args.length===3){
    const p=await load(path),[source,index,out]=args;
    const candidates=await read(source);
    if(candidates.project_id!==p.id||candidates.seed!==p.seed+'-'+p.round)throw Error('Candidate project or round differs');
    const c=candidates.page.results[Number(index)-1],filename=checkFile(c);
    const file_sha256=c.download.sha256;
    const entry={family_id:c.family_id,asset_id:c.asset_id,file_sha256,title:c.title,download:c.download,preview:c.preview,metadata_url:c.metadata_url,selected_source:c.selected_source,text_content:c.text_content,selected_file:c.selected_file};
    const same=x=>x.family_id===entry.family_id&&x.file_sha256===file_sha256;
    const selected=p.selected.filter(x=>x.family_id!==entry.family_id);
    if(selected.length>=200||p.adopted.length>=200&&!p.adopted.some(same))throw Error('Project has reached the 200-file limit');
    await mkdir(out,{recursive:true});const target=join(out,filename);
    const tmp=target+'.'+randomUUID()+'.part';
    try{
      const res=await fetch(c.download.url,{signal:AbortSignal.timeout(60000)});
      if(!res.ok||!res.body)throw Error('Download HTTP '+res.status);
      const digest=createHash('sha256');let bytes=0;
      const guard=new Transform({transform(chunk,_encoding,callback){bytes+=chunk.length;if(bytes>c.download.size_bytes)return callback(Error('Size exceeds the catalog record'));digest.update(chunk);callback(null,chunk);}});
      await pipeline(Readable.fromWeb(res.body),guard,createWriteStream(tmp,{flags:'wx'}));
      if(bytes!==c.download.size_bytes||digest.digest('hex')!==file_sha256)throw Error('Size or SHA-256 mismatch');
      await pipeline(createReadStream(tmp),createWriteStream(target,{flags:'wx'}));
      await put(target+'.credit.json',{title:c.title,file_sha256,source:c.selected_source,modifications:[]});
      p.selected=[...selected,entry];if(!p.adopted.some(same))p.adopted.push(entry);
      const next=join(dirname(path),'.mga-'+randomUUID()+'.json');await put(next,p);await rename(next,path);
      console.log('Verified and recorded adoption:',target);
    }finally{await rm(tmp,{force:true});}
  }else{
    console.log('node agent-example.mjs init mga-project.json "Study"');
    console.log('node agent-example.mjs search mga-project.json "mouse" candidates.json [earlier-project.json ...]');
    console.log('node agent-example.mjs adopt mga-project.json candidates.json 2 selected');
    if(cmd)process.exitCode=1;
  }
}catch(e){console.error(e.message);process.exitCode=1;}
