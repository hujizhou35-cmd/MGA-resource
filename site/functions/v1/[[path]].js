import {prepareWindowSearch, finishWindowSearch, materializeResult, RANKING_VERSION, INDEX_VERSION} from '../../../mga_resources/search-core.mjs';

const CATALOG='f3f869f4ed24e4582ff26c46fabb64a1e68593d2a731eff66e728ec6b280f377';
const CORE='679abe5bf0b62d35f2b849d0e49a48020a6a54a2969ea9ab01dabb6c29c6e0c5';
const CORS={'Access-Control-Allow-Origin':'*','Access-Control-Allow-Methods':'GET, POST, OPTIONS','Access-Control-Allow-Headers':'Content-Type','X-Content-Type-Options':'nosniff'};
const json=(value,status=200,headers={})=>Response.json(value,{status,headers:{...CORS,'Cache-Control':'no-store',...headers}});

export function validateRequest(input) {
  if(!input||Array.isArray(input)||typeof input!=='object')throw new Error('A JSON object is required');
  const allowed=new Set(['query','limit','filters','mode','seed','history','project','cursor','preferences','candidate_pool']);
  for(const key of Object.keys(input))if(!allowed.has(key))throw new Error('Unknown request field: '+key);
  if(typeof input.query!=='string'||!input.query.trim()||input.query.length>512)throw new Error('query must contain 1–512 characters');
  const limit=input.limit??10;
  if(!Number.isInteger(limit)||limit<1||limit>20)throw new Error('limit must be an integer from 1 to 20');
  for(const key of ['filters','history','project','preferences'])if(input[key]!=null&&(typeof input[key]!=='object'||Array.isArray(input[key])))throw new Error(key+' must be an object');
  if(input.seed!=null&&(typeof input.seed!=='string'||input.seed.length>256))throw new Error('seed must be a string of at most 256 characters');
  return {...input,limit,catalog_version:CATALOG};
}

async function parseRequest(request,url) {
  if(request.method==='POST'){
    if(!request.headers.get('content-type')?.includes('application/json'))throw new Error('POST requires application/json');
    const reader=request.body?.getReader();let size=0;const chunks=[];
    if(!reader)throw new Error('JSON body required');
    try{for(;;){const {value,done}=await reader.read();if(done)break;size+=value.length;if(size>16384){await reader.cancel();throw new Error('Request body exceeds 16 KiB');}chunks.push(value);}}
    finally{reader.releaseLock();}
    const bytes=new Uint8Array(size);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.length;}
    return validateRequest(JSON.parse(new TextDecoder().decode(bytes)));
  }
  const input={};
  for(const [key,value] of url.searchParams){
    const name=key==='q'?'query':key;
    if(Object.hasOwn(input,name))throw new Error('Duplicate request field: '+name);
    input[name]=['filters','history','project','preferences'].includes(name)?JSON.parse(value):['limit','candidate_pool'].includes(name)?Number(value):value;
  }
  return validateRequest(input);
}

/** @type {import('@cloudflare/workers-types').PagesFunction<Env>} */
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
      return json({status:ready?'ready':'version_mismatch',catalog_sha256:CATALOG,search_version:RANKING_VERSION,index_version:INDEX_VERSION},ready?200:503);
    }catch{return json({error:'catalog_unavailable',retryable:true},503,{'Retry-After':'60'});}
  }
  const asset=/^\/v1\/assets\/([a-zA-Z0-9_-]{1,160})$/.exec(url.pathname);
  if(asset&&request.method==='GET')return Response.redirect(new URL('/catalog/'+asset[1]+'.json',url),307);
  if(url.pathname!=='/v1/search')return json({error:'not_found',documentation:'/agent'},404);
  if(request.method==='HEAD')return json({error:'method_not_allowed'},405);
  let input,prepared;
  try{input=await parseRequest(request,url);prepared=await prepareWindowSearch(input);}
  catch(error){return json({error:'invalid_request',message:error.message},400);}
  const cacheKey=new Request(new URL('/_search_cache/'+CATALOG+'/'+prepared.state.fingerprint+'/'+prepared.state.offset+'/'+prepared.state.limit,url),{method:'GET'});
  const cached=await caches.default.match(cacheKey);if(cached)return cached;
  try{
    const {sql,state}=prepared;
    const queries=[env.DB.prepare(sql.counts).bind(...sql.params),env.DB.prepare(sql.window).bind(...sql.params)];
    if(input.project?.selected?.length)queries.push(env.DB.prepare(sql.pins).bind(...sql.params));
    const executed=await env.DB.batch(queries);
    const rows=executed[1].results.map(row=>({...row,features:JSON.parse(row.features)}));
    const page=finishWindowSearch(rows,executed[0].results[0],state);
    const selected=page.selected;delete page.selected;
    let details=[];
    if(selected.length)details=await env.DB.batch(selected.map(row=>env.DB.prepare('SELECT metadata FROM file_variants WHERE variant_id=?').bind(row.variant_id)));
    page.results=selected.map((row,i)=>{
      if(!details[i].results[0])throw new Error('Missing selected variant');
      const variant=JSON.parse(details[i].results[0].metadata);const result=materializeResult(row,variant);const delivery=variant._delivery;
      if(delivery.file_sha256!==result.selected_file.sha256||delivery.preview_sha256!==result.preview.sha256)throw new Error('Delivery identity mismatch');
      return {...result,
        selected_source:{...result.selected_source,author:variant.author,license:variant.values.license,provenance:delivery.provenance},
        preview:{...result.preview,url:delivery.preview_url},
        download:{url:delivery.original_url,sha256:delivery.file_sha256,size_bytes:delivery.size_bytes,format:result.selected_file.format},
        metadata_url:delivery.metadata_url,text_content:delivery.text_content,
        use_guidance:'Preserve the original. Clean task-irrelevant labels only in a working copy; retain scientific meaning and attribution.'};
    });
    page.diagnostics.unavailable_project_pins=executed[2]?.results.filter(row=>!Object.values(row)[1]).map(row=>({...JSON.parse(Object.values(row)[0]),reason:'Selected file does not satisfy current query or constraints'}))??[];
    const all=[...executed,...details];
    page.service={name:'MGA-resource',purpose:'mainly-for-agent',catalog_sha256:CATALOG,core_sha256:CORE,
      d1_rows_read:all.reduce((n,r)=>n+(r.meta?.rows_read||0),0),d1_rows_written:all.reduce((n,r)=>n+(r.meta?.rows_written||0),0)};
    const response=json(page,200,{'Cache-Control':'public, max-age=300','X-MGA-Catalog':CATALOG,'X-MGA-Search':RANKING_VERSION});
    context.waitUntil(caches.default.put(cacheKey,response.clone()));
    return response;
  }catch(error){
    console.error(JSON.stringify({event:'search_failed',message:String(error.message).slice(0,180),version:RANKING_VERSION}));
    return json({error:'search_temporarily_unavailable',retryable:true,message:'The free service is temporarily unavailable. Retry later; no results were substituted.'},503,{'Retry-After':'60'});
  }
}
