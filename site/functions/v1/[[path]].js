import {prepareWindowSearch, finishWindowSearch, materializeResult, stableStringify, RANKING_VERSION, INDEX_VERSION} from '../../../mga_resources/search-core.mjs';
import {optimizeSQL, EXECUTION} from '../../lib/sql-adapter.mjs';

const CATALOG='f3f869f4ed24e4582ff26c46fabb64a1e68593d2a731eff66e728ec6b280f377';
const CORE='679abe5bf0b62d35f2b849d0e49a48020a6a54a2969ea9ab01dabb6c29c6e0c5';
const CORS={'Access-Control-Allow-Origin':'*','Access-Control-Allow-Methods':'GET, POST, OPTIONS','Access-Control-Allow-Headers':'Content-Type','Access-Control-Expose-Headers':'Retry-After, Server-Timing, X-MGA-Cache','X-Content-Type-Options':'nosniff'};
const json=(value,status=200,headers={})=>Response.json(value,{status,headers:{...CORS,'Cache-Control':'no-store',...headers}});
const record=x=>x!=null&&typeof x==='object'&&!Array.isArray(x);
const identifier=x=>typeof x==='string'&&/^[a-zA-Z0-9_-]{1,160}$/.test(x);
const hash=x=>typeof x==='string'&&/^[a-f0-9]{64}$/.test(x);
const digest=async x=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(stableStringify(x)))),b=>b.toString(16).padStart(2,'0')).join('');

export function validateRequest(input,browse=false) {
  if(!record(input))throw new Error('A JSON object is required');
  const allowed=new Set(['query','limit','filters','mode','seed','history','project','cursor','preferences','candidate_pool','response_profile','unclassified']);
  for(const key of Object.keys(input))if(!allowed.has(key))throw new Error('Unknown request field: '+key);
  if((!browse&&(typeof input.query!=='string'||!input.query.trim()||input.query.length>512))||(browse&&input.query!=null&&input.query!==''))throw new Error(browse?'Use /v1/search for keywords':'query must contain 1–512 characters');
  const limit=input.limit??10;
  if(!Number.isInteger(limit)||limit<1||limit>20)throw new Error('limit must be an integer from 1 to 20');
  for(const key of ['filters','history','project','preferences'])if(input[key]!=null&&!record(input[key]))throw new Error(key+' must be an object');
  if(input.seed!=null&&(typeof input.seed!=='string'||input.seed.length>256))throw new Error('seed must be a string of at most 256 characters');
  if(input.response_profile!=null&&!['full','compact'].includes(input.response_profile))throw new Error('response_profile must be full or compact');
  if(input.cursor!=null&&(typeof input.cursor!=='string'||input.cursor.length>256))throw new Error('Invalid cursor');
  if(input.project?.style!=null&&!record(input.project.style))throw new Error('project.style must be an object');
  if(input.project?.selected!=null&&(!Array.isArray(input.project.selected)||input.project.selected.some(p=>!record(p)||!identifier(p.family_id)||!hash(p.file_sha256)||(p.asset_id!=null&&!identifier(p.asset_id)))))throw new Error('Invalid project.selected');
  if(input.history?.adopted!=null&&(!Array.isArray(input.history.adopted)||input.history.adopted.some(p=>!record(p)||!identifier(p.family_id))))throw new Error('Invalid history.adopted');
  if(input.unclassified!=null&&typeof input.unclassified!=='boolean')throw new Error('unclassified must be a boolean');
  if([input.filters?.category,input.project?.style?.category].flat().includes('mga_unclassified'))throw new Error('Use unclassified=true');
  if(input.unclassified&&(input.filters?.category!=null||input.project?.style?.category!=null))throw new Error('Unclassified cannot be combined with a category');
  const filters=input.unclassified?{...input.filters,category:'mga_unclassified'}:input.filters;
  return {...input,filters,...(browse?{query:''}:{}),limit,catalog_version:CATALOG};
}

async function parseRequest(request,url,browse) {
  if(request.method==='POST'){
    if(!request.headers.get('content-type')?.includes('application/json'))throw new Error('POST requires application/json');
    const reader=request.body?.getReader();let size=0;const chunks=[];
    if(!reader)throw new Error('JSON body required');
    try{for(;;){const {value,done}=await reader.read();if(done)break;size+=value.length;if(size>16384){await reader.cancel();throw new Error('Request body exceeds 16 KiB');}chunks.push(value);}}
    finally{reader.releaseLock();}
    const bytes=new Uint8Array(size);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.length;}
    return validateRequest(JSON.parse(new TextDecoder().decode(bytes)),browse);
  }
  const input=Object.create(null);
  for(const [key,value] of url.searchParams){
    const name=key==='q'?'query':key;
    if(Object.hasOwn(input,name))throw new Error('Duplicate request field: '+name);
    input[name]=['filters','history','project','preferences','unclassified'].includes(name)?JSON.parse(value):['limit','candidate_pool'].includes(name)?Number(value):value;
  }
  return validateRequest(input,browse);
}

const cachedKey=(url,path)=>new Request(new URL('/_mga_cache/'+EXECUTION+'/'+CATALOG+'/'+path,url),{method:'GET'});
function keep(context,key,response){context.waitUntil(caches.default.put(key,response).catch(()=>{}));}
function resultHeaders(hit,timing){return {'Cache-Control':'no-store','X-MGA-Catalog':CATALOG,'X-MGA-Search':RANKING_VERSION,'X-MGA-Cache':hit,'Server-Timing':timing};}
function projection(result){
  const {asset_id,family_id,variant_id,title,selected_file,selected_source,preview,download,metadata_url,text_content,pinned,selected_science}=result;
  return {asset_id,family_id,variant_id,title,selected_file:Object.fromEntries(['sha256','format','structure','appearance','width','height','transparency','content_type'].filter(k=>k in selected_file).map(k=>[k,selected_file[k]])),selected_source,preview:{url:preview.url,sha256:preview.sha256},download,metadata_url,text_content,pinned,selected_science};
}

export async function onRequest(context) {
  const {request,env}=context;const url=new URL(request.url);
  if(request.method==='OPTIONS')return new Response(null,{status:204,headers:CORS});
  if(!['GET','POST','HEAD'].includes(request.method))return json({error:'method_not_allowed'},405,{Allow:'GET, POST, HEAD, OPTIONS'});
  if(url.pathname==='/v1/health'){
    if(request.method==='POST')return json({error:'method_not_allowed'},405);
    try{
      const rows=await env.DB.prepare('SELECT key,value FROM index_metadata').all();
      const meta=Object.fromEntries(rows.results.map(r=>[r.key,r.value]));
      const ready=meta.catalog_version===CATALOG&&meta.ranking_version===RANKING_VERSION&&Number(meta.search_index_version)===INDEX_VERSION&&meta.search_core_sha256===CORE;
      return json({status:ready?'ready':'version_mismatch',catalog_sha256:CATALOG,search_version:RANKING_VERSION,index_version:INDEX_VERSION,execution_version:EXECUTION},ready?200:503);
    }catch{return json({error:'catalog_unavailable',retryable:true},503,{'Retry-After':'60'});}
  }
  const asset=/^\/v1\/assets\/([a-zA-Z0-9_-]{1,160})$/.exec(url.pathname);
  if(asset&&request.method==='GET')return new Response(null,{status:307,headers:{...CORS,Location:new URL('/catalog/'+asset[1]+'.json',url).href}});
  const browse=url.pathname==='/v1/browse';
  if(!browse&&url.pathname!=='/v1/search')return json({error:'not_found',documentation:'/agent'},404);
  if(request.method==='HEAD')return json({error:'method_not_allowed'},405);
  let input,prepared;
  try{input=await parseRequest(request,url,browse);prepared=await prepareWindowSearch(input);}
  catch(error){return json({error:'invalid_request',message:error.message},400);}

  try{
    const {state}=prepared;
    const cacheKey=cachedKey(url,['page',browse?'browse':'search',state.fingerprint,input.unclassified?'u':'all',state.offset,state.limit,input.response_profile||'full'].join('/'));
    const cached=await caches.default.match(cacheKey).catch(()=>null);
    if(cached){
      const page=await cached.json();
      page.service={...page.service,cache:'hit',count_cache:'not_needed',d1_rows_read:0,d1_rows_written:0,d1_rows_by_phase:{count:0,window:0,pins:0,details:0}};
      return json(page,200,resultHeaders('HIT','cache;desc="page hit"'));
    }
    const sql=optimizeSQL(prepared.sql,input.unclassified===true);
    const countKey=cachedKey(url,'counts/'+await digest({terms:state.plan.terms,fts:state.plan.fts,requirements:state.plan.requirements,all:state.plan.all_requirements,objects:state.plan.object_groups,empty:state.plan.empty,unclassified:input.unclassified===true}));
    const countCache=await caches.default.match(countKey).catch(()=>null);
    const queries=[];
    if(!countCache)queries.push(env.DB.prepare(sql.counts).bind(...sql.params));
    queries.push(env.DB.prepare(sql.window).bind(...sql.params));
    if(input.project?.selected?.length)queries.push(env.DB.prepare(sql.pins).bind(...sql.params));
    const began=performance.now();
    const executed=await env.DB.batch(queries);
    const queryMs=performance.now()-began;
    const countRun=countCache?null:executed[0];
    const windowRun=executed[countCache?0:1];
    const pinsRun=executed[countCache?1:2];
    const counts=countCache?await countCache.json():countRun.results[0];
    if(countRun)keep(context,countKey,json(counts,200,{'Cache-Control':'public, max-age=86400'}));
    const rows=windowRun.results.map(row=>({...row,features:JSON.parse(row.features)}));
    const page=finishWindowSearch(rows,counts,state);
    const selected=page.selected;delete page.selected;
    const detailStart=performance.now();
    const detailRun=selected.length?await env.DB.prepare('SELECT variant_id,metadata FROM file_variants WHERE variant_id IN ('+selected.map(()=>'?').join(',')+')').bind(...selected.map(r=>r.variant_id)).all():null;
    const details=new Map(detailRun?.results.map(r=>[r.variant_id,r.metadata])??[]);
    page.results=selected.map(row=>{
      if(!details.has(row.variant_id))throw new Error('Missing selected variant');
      const variant=JSON.parse(details.get(row.variant_id));const result=materializeResult(row,variant);const delivery=variant._delivery;
      if(delivery.file_sha256!==result.selected_file.sha256||delivery.preview_sha256!==result.preview.sha256)throw new Error('Delivery identity mismatch');
      const item={...result,
        selected_source:{...result.selected_source,author:variant.author,license:variant.values.license,provenance:delivery.provenance},
        preview:{...result.preview,url:delivery.preview_url},
        download:{url:delivery.original_url,sha256:delivery.file_sha256,size_bytes:delivery.size_bytes,format:result.selected_file.format},
        metadata_url:delivery.metadata_url,text_content:delivery.text_content,
        use_guidance:'Preserve the original. Clean task-irrelevant labels only in a working copy; retain scientific meaning and attribution.'};
      return input.response_profile==='compact'?projection(item):item;
    });
    page.diagnostics.unavailable_project_pins=pinsRun?.results.filter(row=>!Object.values(row)[1]).map(row=>({...JSON.parse(Object.values(row)[0]),reason:'Selected file does not satisfy current query or constraints'}))??[];
    const phases=Object.fromEntries(Object.entries({count:countRun,window:windowRun,pins:pinsRun,details:detailRun}).map(([k,v])=>[k,v?.meta?.rows_read||0]));
    page.service={name:'MGA-resource',purpose:'mainly-for-agent',catalog_sha256:CATALOG,core_sha256:CORE,execution_version:EXECUTION,
      cache:'miss',count_cache:countCache?'hit':'miss',d1_rows_by_phase:phases,d1_rows_read:Object.values(phases).reduce((a,b)=>a+b,0),
      d1_rows_written:[...executed,detailRun].reduce((n,r)=>n+(r?.meta?.rows_written||0),0)};
    keep(context,cacheKey,json(page,200,{'Cache-Control':'public, max-age=300'}));
    return json(page,200,resultHeaders('MISS',`queries;dur=${queryMs.toFixed(1)}, details;dur=${(performance.now()-detailStart).toFixed(1)}`));
  }catch(error){
    console.error(JSON.stringify({event:'search_failed',message:String(error.message).slice(0,180),version:EXECUTION}));
    return json({error:'search_temporarily_unavailable',retryable:true,message:'The free service is temporarily unavailable. Retry later.'},503,{'Retry-After':'60'});
  }
}
