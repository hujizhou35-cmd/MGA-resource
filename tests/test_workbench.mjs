import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {prepareWindowSearch,finishWindowSearch} from '../mga_resources/search-core.mjs';
import {optimizeSQL} from '../site/lib/sql-adapter.mjs';
import {newProject,importProject,requestContext} from '../site/public/project.js';
import {validateRequest,onRequest} from '../site/functions/v1/[[path]].js';

test('physical SQL preserves accepted variant selection, counts and cursors',async()=>{
  const db=new DatabaseSync(':memory:');
  db.exec("CREATE TABLE file_variants (variant_id TEXT PRIMARY KEY,asset_id TEXT,family_id TEXT,file_sha256 TEXT,eligible INTEGER,values_json TEXT,title_terms TEXT,context_terms TEXT,all_terms TEXT,rotation_key INTEGER,features TEXT); CREATE VIRTUAL TABLE file_variants_fts USING fts5(variant_id UNINDEXED,title,aliases,context,tokenize='porter unicode61');");
  const insert=db.prepare('INSERT INTO file_variants VALUES (?,?,?,?,?,?,?,?,?,?,?)');
  const fts=db.prepare('INSERT INTO file_variants_fts VALUES (?,?,?,?)');
  const hash='a'.repeat(64);
  for(let i=0;i<60;i++)for(let v=0;v<2;v++){
    const id='id'+i+'-'+v, family='family'+i, eligible=Number(i!==10);
    const values={asset_id:[family],format:[v?'png':'svg'],category:i>=58?[]:['animals'],color_mode:[v?'color':'black_white'],species:['mouse'],object:['mouse'],role:['whole_body']};
    const t='["mouse"]';insert.run(id,family,family,hash,eligible,JSON.stringify(values),v?'[]':t,v?t:'[]',t,10001*i,'{"signature":["mouse"]}');
    fts.run(id,'mouse','','');
  }
  const run=(sql,params)=>{
    const bindings=[];
    const query=sql.replace(/\?(\d+)/g,(_,i)=>{bindings.push(params[Number(i)-1]);return '?';});
    return db.prepare(query).all(...bindings);
  };
  const execute=(sql,state)=>finishWindowSearch(run(sql.window,sql.params).map(r=>({...r,features:JSON.parse(r.features)})),run(sql.counts,sql.params)[0],state);
  const base={query:'mouse',catalog_version:'fixture',filters:{format:'svg'},candidate_pool:50,limit:11,seed:'a'};
  for(const extra of [{},{mode:'design',project:{style:{color_mode:'black_white'},selected:[{family_id:'family7',file_sha256:hash}]},history:{adopted:[{family_id:'family0',status:'adopted'}]}},{mode:'exact'},{query:'',filters:{category:'animals'}},{query:'not_found'}]){
    let request={...base,...extra};
    for(let n=0;n<7;n++){
      const {sql,state}=await prepareWindowSearch(request);
      const old=execute(sql,state), next=execute(optimizeSQL(sql),state);
      assert.deepEqual(next,old);
      if(!old.next_cursor)break;
      request={...request,cursor:old.next_cursor};
    }
  }
  const {sql,state}=await prepareWindowSearch({query:'mouse',catalog_version:'fixture',filters:{category:'mga_unclassified'},mode:'exact'});
  const page=execute(optimizeSQL(sql,true),state);
  assert.equal(page.diagnostics.qualified_families,2);
  assert.equal(page.diagnostics.qualified_variants,4);
  db.close();
});

test('project export preserves seeds and only explicit uses enter cross-project history',()=>{
  const first=newProject('One'), second=newProject('Two');
  const x={family_id:'family',asset_id:'asset',file_sha256:'a'.repeat(64),download:{sha256:'a'.repeat(64)}};
  first.saved.push(x);first.selected.push(x);
  assert.notEqual(first.seed,second.seed);
  assert.equal(requestContext(second,[first,second],'explore').history.adopted.length,0);
  first.adopted.push(x);first.round=2;
  const imported=importProject(JSON.parse(JSON.stringify(first)));
  assert.equal(requestContext(imported,[first,second],'design').seed,first.seed+'-2');
  assert.deepEqual(requestContext(second,[imported,second],'explore').history.adopted,[{family_id:'family',status:'adopted'}]);
  assert.equal(requestContext(imported,[first,second],'explore').history.adopted.length,0);
  assert.throws(()=>importProject({...first,selected:[{...x,asset_id:'../../example'}]}));
});

test('browse and filter-only inputs preserve bounds and return genuine service errors',async()=>{
  assert.equal(validateRequest({},true).query,'');
  assert.equal(validateRequest({query:'mouse',unclassified:true}).filters.category,'mga_unclassified');
  assert.throws(()=>validateRequest({query:'mouse'},true));
  assert.throws(()=>validateRequest({unclassified:true,filters:{category:'cells'}},true));
  assert.throws(()=>validateRequest({query:'mouse',response_profile:'bad'}));
  const oldCache=globalThis.caches;
  globalThis.caches={default:{match:async()=>null}};
  try{
    const r=await onRequest({request:new Request('https://test.invalid/v1/browse'),env:{DB:{prepare(){throw Error('test unavailable');}}}});
    assert.equal(r.status,503);assert.equal((await r.json()).error,'search_temporarily_unavailable');
  }finally{globalThis.caches=oldCache;}
});
