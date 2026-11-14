export const PROJECT_FORMAT='mga-project-v1';
const clean=(s,n)=>typeof s==='string'?s.slice(0,n):'';
const id=s=>typeof s==='string'&&/^[a-zA-Z0-9_-]{1,160}$/.test(s);
const sha=s=>typeof s==='string'&&/^[a-f0-9]{64}$/.test(s);
export const keyOf=x=>x.family_id+':'+x.file_sha256;
export function newProject(name='') {
  return {schema:PROJECT_FORMAT,id:crypto.randomUUID(),name,seed:crypto.randomUUID(),round:0,style:{},saved:[],selected:[],adopted:[]};
}
export function fileIdentity(r) {
  return {family_id:r.family_id,asset_id:r.asset_id,file_sha256:r.download.sha256,title:r.title,
    download:r.download,preview:r.preview,metadata_url:r.metadata_url,selected_source:r.selected_source,text_content:r.text_content,selected_file:r.selected_file};
}
export function importProject(value) {
  if(!value||value.schema!==PROJECT_FORMAT||!id(value.id)||!id(value.seed)||!Number.isSafeInteger(value.round)||value.round<0||value.round>1e9)throw Error('Invalid project format');
  const p={schema:PROJECT_FORMAT,id:value.id,seed:value.seed,round:value.round,name:clean(value.name,80),style:{},saved:[],selected:[],adopted:[]};
  if(value.style!=null&&(typeof value.style!=='object'||Array.isArray(value.style)))throw Error('Invalid style');
  for(const [k,v] of Object.entries(value.style||{})){
    if(!['color_mode','representation','detail','realism','series'].includes(k)||typeof v!=='string'||!v||v.length>256)throw Error('Invalid style value');
    p.style[k]=v;
  }
  for(const k of ['saved','selected','adopted']){
    if(!Array.isArray(value[k])||value[k].length>200)throw Error('Invalid project records (max 200 per list)');
    const seen=new Set();
    for(const x of value[k]){
      if(!x||!id(x.family_id)||!sha(x.file_sha256)||!id(x.asset_id)||x.download?.sha256!==x.file_sha256)throw Error('Invalid file identity');
      const key=keyOf(x);
      if(!seen.has(key)){
        seen.add(key);
        p[k].push({family_id:x.family_id,asset_id:x.asset_id,file_sha256:x.file_sha256,title:clean(x.title,400),download:x.download,preview:x.preview,metadata_url:x.metadata_url,selected_source:x.selected_source,text_content:x.text_content,selected_file:x.selected_file});
      }
    }
    if(k==='selected'&&new Set(p.selected.map(x=>x.family_id)).size!==p.selected.length)throw Error('Only one selected variant per family');
  }
  return p;
}
export function requestContext(project,projects,mode) {
  const adopted=projects.filter(p=>p.id!==project.id).flatMap(p=>[...new Set(p.adopted.map(x=>x.family_id))].map(family_id=>({family_id,status:'adopted'})));
  return {mode,seed:project.seed+'-'+project.round,project:{style:project.style,selected:project.selected.map(({asset_id,family_id,file_sha256})=>({asset_id,family_id,file_sha256}))},history:{adopted}};
}
