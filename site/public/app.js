import {$,$$,node,action,webLink,fileLink,mediaURL,localGet,localSet,L,language,copy,save,loadCatalog} from './common.js';
import {newProject,importProject,keyOf,fileIdentity,requestContext} from './project.js';

const storeKey='mga-workbench-v1';
const names={source:['Source','来源'],color_mode:['Color','颜色'],format:['Format','格式'],structure:['File structure','文件结构'],representation:['Representation','表现方式'],detail:['Detail','细节'],realism:['Realism','写实程度'],series:['Style series','画风系列'],license:['License','许可'],species:['Species','物种'],state:['State','状态'],view:['View','视角'],role:['Role','用途'],depiction_kind:['Depiction','描绘对象类型'],content_type:['Content type','内容类型'],transparent:['Transparency','透明背景'],palette_traits:['Palette','配色特点']};
const labels={color:['Color','彩色'],black_white:['Black and white','黑白'],grayscale:['Grayscale','灰阶'],pure_vector:['Vector structure','纯矢量结构'],mixed_svg:['Mixed SVG','混合 SVG'],raster:['Raster','位图'],line_art:['Line art','线稿'],silhouette:['Silhouette','剪影'],flat_fill:['Flat fill','平面填色'],shaded:['Shaded','明暗塑形'],hand_drawn:['Hand drawn','手绘'],'3d_render':['3D render','三维渲染'],minimal:['Minimal','极简'],moderate:['Moderate','适中'],detailed:['Detailed','精细'],schematic:['Schematic','示意'],semi_realistic:['Semi-realistic','半写实'],realistic:['Realistic','写实'],true:['Yes','有'],false:['No','无']};
const styleKeys=['color_mode','representation','detail','realism','series'];
const modes={explore:['Explore','探索候选'],design:['Respect project style','使用项目画风'],exact:['Exact ranking','精确排序']};
const params=new URL(location.href).searchParams;
let info,category=params.get('category')||'',snapshot=null,page=null,aborter=null,serial=0,comparison=new Map(),background=false,pendingRefresh=false;
const status=$('#status');
let projects=[],activeId='';
try{const data=JSON.parse(localGet(storeKey)||'null');projects=(data?.projects||[]).map(importProject);activeId=data?.activeId;}catch{status.textContent=L('Local project records could not be read.','无法读取本机项目记录。');}
if(!projects.length)projects=[newProject(L('My project','我的项目'))];
if(!projects.some(p=>p.id===activeId))activeId=projects[0].id;
const active=()=>projects.find(p=>p.id===activeId);
function persist(){if(!localSet(storeKey,JSON.stringify({projects,activeId})))$('#notice').textContent=L('Storage unavailable. Export your project to keep it.','无法写入本机存储，请导出项目以保存。');}
persist();
function label(key,value){const s=key==='source'&&info?.sources.find(x=>x.id===value);return s?s.name:labels[value]?L(...labels[value]):(key==='format'?value.toUpperCase():value);}
function selectFor(key,value='',id=key){
  const s=node('select');s.id=id;
  const any=node('option',L('Any','不限'));any.value='';s.append(any);
  for(const {value:v} of info.facets[key]||[]){const opt=node('option',label(key,v));opt.value=v;s.append(opt);}
  s.value=value;if(s.selectedIndex<0)s.value='';
  return s;
}
function field(key,value='',id=key){const wrap=node('label',L(...names[key]));wrap.htmlFor=id;wrap.append(selectFor(key,value,id));return wrap;}
function selectedFilters(){
  const f={};for(const k of Object.keys(names))if($('#'+k)?.value)f[k]=$('#'+k).value;
  if(category&&category!=='unclassified')f.category=category;
  return f;
}
function currentRequest(){
  const query=$('#query').value.trim(), context=requestContext(active(),projects,$('#mode').value);
  const payload={...context,filters:selectedFilters(),limit:12,response_profile:'compact'};
  if(category==='unclassified')payload.unclassified=true;
  if(query)payload.query=query;
  return {url:'/v1/'+(query?'search':'browse'),payload};
}
function publicURL(){
  const url=new URL('/',location.origin);url.searchParams.set('lang',language);
  const q=$('#query').value.trim();if(q)url.searchParams.set('q',q);
  for(const [k,v] of Object.entries(selectedFilters()))url.searchParams.set(k,v);
  if(category==='unclassified')url.searchParams.set('category',category);
  url.searchParams.set('mode',$('#mode').value);return url;
}
function replaceState(){history.replaceState(null,'',publicURL());}
function errorHint(error){
  if(error.status===503)return L('Search is temporarily unavailable. Try again later; saved download links still work.','检索暂时不可用。稍后重试；已保存的下载链接仍可访问。');
  if(error.status===400)return L('Check filters, project style, or request size.','请检查筛选、项目画风或请求大小。')+' '+error.message;
  return L('Could not load results. Check the connection.','无法加载结果，请检查网络连接。');
}
function statusText(text,retry){status.replaceChildren(document.createTextNode(text));if(retry)status.append(' ',action(L('Retry','重试'),retry));}
async function search(append=false){
  if(!info||append&&!page?.next_cursor)return;
  const attempt=++serial;aborter?.abort();aborter=new AbortController();
  if(!append){snapshot=currentRequest();page=null;replaceState();pendingRefresh=false;}
  const body=JSON.stringify(append?{...snapshot.payload,cursor:page.next_cursor}:snapshot.payload);
  if(new TextEncoder().encode(body).length>16384){$('#more').hidden=true;statusText(L('Project history is too large for one request. Use a smaller project collection or export your records.','项目记录超过单次请求大小。请缩小参与比较的本机项目范围，或导出保存记录。'));return;}
  $('#more').disabled=true;$('#results').setAttribute('aria-busy','true');
  statusText(L('Searching…','正在查找…'));
  try{
    const response=await fetch(snapshot.url,{method:'POST',headers:{'Content-Type':'application/json'},body,signal:aborter.signal});
    const data=await response.json();
    if(!response.ok)throw Object.assign(new Error(data.message||data.error),{status:response.status});
    if(attempt!==serial)return;
    if(!append)$('#results').replaceChildren();
    page=data;
    for(const r of data.results)$('#results').append(card(fileIdentity(r),r.pinned));
    const showing=$('#results').children.length, count=data.diagnostics.qualified_families;
    statusText(count?L(`${showing} shown · ${count} matching families`,`已显示 ${showing} 件 · ${count} 个匹配家族`):
      L('No matching files. Try a different term or remove a filter.','没有匹配文件。试试换个关键词或取消部分筛选。'));
    $('#more').hidden=!data.next_cursor;
  }catch(e){if(attempt===serial&&e.name!=='AbortError'){if(!append){$('#more').hidden=true;$('#results').replaceChildren();}statusText(errorHint(e),()=>search(append));}}
  finally{if(attempt===serial){$('#more').disabled=false;$('#results').removeAttribute('aria-busy');}}
}
function attribution(x){const p=x.selected_source?.provenance||[];return p.length?p.map(r=>[r.attribution,r.license,r.source_url].filter(Boolean).join(' · ')).join('\n'):(x.selected_source?.attribution||L('Attribution unavailable','署名未确认'));}
function picture(x,klass='preview'){const img=node('img');img.alt=x.title||'';img.loading='lazy';const src=mediaURL(x.preview?.url);if(src)img.src=src;const box=node('div',null,klass+(background?' checker':''));box.append(img);return box;}
function badges(x){return [x.download?.format?.toUpperCase(),label('structure',x.selected_file?.structure||L('Unconfirmed structure','结构未确认')),x.text_content?.status==='present'?L('Labels present','含文字标签'):null].filter(Boolean).join(' · ');}
function card(x,pinned){
  const el=node('article',null,'card');const open=action('',()=>openDetail(x),'preview-button');open.setAttribute('aria-label',L('View ','查看 ')+x.title);open.append(picture(x));el.append(open);
  const copyBody=node('div',null,'card-body');copyBody.append(node('h2',x.title),node('p',badges(x),'subtle'),node('p',label('source',x.selected_source?.source||''),'subtle'));
  const row=node('div',null,'row');row.append(fileLink(L('Original ↗','原件 ↗'),x.download?.url),action(L('Compare','比较'),()=>toggleCompare(x)),action(pinned?L('Selected','已选用'):L('Save','收藏'),()=>projectAction('saved',x)));copyBody.append(row);el.append(copyBody);return el;
}
function renderCategories(){
  const list=$('#categories');list.replaceChildren();
  for(const x of [{id:'',en:'All components',zh:'全部素材',families:info.families},...info.categories,{id:'unclassified',en:'Unclassified',zh:'尚未归类',families:info.unclassified_families}]){
    const b=action('',()=>{category=x.id;renderCategories();void search();},'category');b.setAttribute('aria-pressed',String(category===x.id));b.append(node('span',x[language]),node('span',x.families));list.append(b);
  }
}
function openSafeDialog(sel){const dialog=$(sel);if(!dialog.open)dialog.showModal();}
function projectAction(kind,x){
  const p=active(),array=p[kind],key=keyOf(x);
  if(array.some(a=>keyOf(a)===key)){if(kind==='saved')$('#notice').textContent=L('Already saved','已在收藏中');return;}
  if(array.length>=200){$('#notice').textContent=L('This list holds up to 200 files.','这个列表最多保存 200 件。');return;}
  if(kind==='selected')p.selected=p.selected.filter(a=>a.family_id!==x.family_id);
  p[kind].push(x);persist();if(kind==='selected')pendingRefresh=true;
  $('#notice').textContent={saved:L('Saved to this project','已收藏到本项目'),selected:L('Selected for this project','已选为本项目素材'),adopted:L('Recorded as adopted','已记录实际采用')}[kind];
}
function renderDetail(x){
  const box=$('#detail-body');box.replaceChildren();const title=node('h2',x.title);title.id='detail-title';box.append(title,picture(x,'large-image'),node('p',badges(x)));
  const actions=node('div',null,'row');
  actions.append(fileLink(L('Open original ↗','打开原件 ↗'),x.download?.url),action(L('Copy link','复制原件链接'),()=>{const u=mediaURL(x.download?.url);if(u)void copy(u);}),action(L('Save to project','收藏到项目'),()=>projectAction('saved',x)),action(L('Select for project','设为项目选用'),()=>projectAction('selected',x)),action(L('Compare','比较'),()=>toggleCompare(x)),action(L('Toggle background','切换透明背景'),()=>{background=!background;renderDetail(x);}));
  box.append(actions);
  const variants=action(L('Other versions of this work','查看同家族版本'),async()=>{
    variants.disabled=true;
    try{
      const res=await fetch('/catalog/'+encodeURIComponent(x.asset_id)+'.json');if(!res.ok)throw Error();const family=await res.json();
      const list=node('div',null,'row');
      for(const f of family.files){
        const other={...x,file_sha256:f.sha256,download:{url:f.download_url,sha256:f.sha256,size_bytes:f.size_bytes,format:f.format},preview:{url:f.preview_url,sha256:f.preview?.sha256},selected_file:f,text_content:f.text_content,selected_source:{provenance:f.provenance,source:f.provenance?.[0]?.source}};
        const b=action([f.format.toUpperCase(),f.dimensions?.width&&f.dimensions?.height?f.dimensions.width+'×'+f.dimensions.height:null,f.sha256.slice(0,8)].filter(Boolean).join(' · '),()=>renderDetail(other));
        b.setAttribute('aria-pressed',String(f.sha256===x.file_sha256));list.append(b);
      }
      variants.replaceWith(list);
    }catch{variants.disabled=false;$('#notice').textContent=L('Could not load versions','无法加载家族版本');}
  });
  box.append(variants,node('h3',L('Source and attribution','来源与署名')),node('pre',attribution(x)),action(L('Copy attribution','复制署名'),()=>void copy(attribution(x))));
  const textStatus={present:L('Labels recorded','已确认含文字'),absent:L('No labels recorded','已确认无文字'),unknown:L('Unconfirmed','未确认')};
  box.append(node('p',L('Text: ','文字：')+(textStatus[x.text_content?.status]||textStatus.unknown)),node('pre',x.file_sha256),webLink(L('Family metadata','完整家族元数据'),'/catalog/'+encodeURIComponent(x.asset_id)+'.json'));
}
function openDetail(x){renderDetail(x);openSafeDialog('#detail');}
function toggleCompare(x){
  const key=keyOf(x);
  if(comparison.has(key))comparison.delete(key);
  else if(comparison.size<4)comparison.set(key,x);
  else $('#notice').textContent=L('Compare up to four files.','最多并排比较四件。');
  renderTray();
}
function renderTray(){
  const tray=$('#compare-tray');tray.hidden=!comparison.size;tray.replaceChildren(node('span',L('Compare '+comparison.size+'/4','比较 '+comparison.size+'/4')),action(L('Open comparison','打开对比'),openCompare),action(L('Clear','清空'),()=>{comparison.clear();renderTray();}));
}
function openCompare(){
  const box=$('#compare-body');const h=node('h2',L('Compare selected files','比较候选文件'));h.id='compare-title';const grid=node('div',null,'compare-grid');
  for(const x of comparison.values()){
    const col=node('article');col.append(picture(x),node('h3',x.title),node('p',badges(x)),fileLink(L('Open original ↗','打开原件 ↗'),x.download?.url),action(L('Remove','移除'),()=>{toggleCompare(x);openCompare();}));grid.append(col);
  }
  box.replaceChildren(h,action(L('Toggle background','切换透明背景'),()=>{background=!background;openCompare();}),grid);openSafeDialog('#compare');
}
function projectLabel(){$('#project-open').textContent=L('Project: ','项目：')+(active().name||L('Untitled','未命名'));}
function renderProjects(){
  const p=active(),box=$('#project-body'),h=node('h2',L('Projects on this device','本机项目'));h.id='projects-title';box.replaceChildren(h);
  const select=node('select');select.setAttribute('aria-label',L('Switch project','切换项目'));
  for(const item of projects){const op=node('option',item.name||L('Untitled','未命名'));op.value=item.id;select.append(op);}select.value=p.id;
  select.onchange=()=>{activeId=select.value;persist();projectLabel();pendingRefresh=true;renderProjects();};
  const name=node('input');name.value=p.name;name.maxLength=80;name.setAttribute('aria-label',L('Project name','项目名称'));
  name.onchange=()=>{p.name=name.value.trim();persist();projectLabel();renderProjects();};
  const row=node('div',null,'row');row.append(select,name,action(L('New project','新建项目'),()=>{const fresh=newProject(L('Project ','项目 ')+(projects.length+1));projects.push(fresh);activeId=fresh.id;persist();projectLabel();pendingRefresh=true;renderProjects();}));box.append(row);
  box.append(node('p',L('Your seed stays with the project. Alternatives change the round, not files you already selected.','种子跟随本机项目保存。换一组只改变候选轮次，已经选用的文件保持不变。'),'subtle'));
  const seed=node('details');seed.append(node('summary',L('Seed and round','种子与探索轮次')),node('code',p.seed+'-'+p.round));box.append(seed);
  const style=node('div',null,'filters');
  for(const k of styleKeys){const el=field(k,p.style[k],'project-'+k);$('select',el).onchange=e=>{if(e.target.value)p.style[k]=e.target.value;else delete p.style[k];persist();pendingRefresh=true;};style.append(el);}
  box.append(node('h3',L('Project style (used in design mode)','项目画风（在项目画风模式中使用）')),style);
  const files=[...new Map([...p.selected,...p.adopted].map(x=>[keyOf(x),x])).values()];
  const exportRow=node('div',null,'row');
  exportRow.append(action(L('Export project','导出项目'),()=>save('mga-project.json',JSON.stringify(p,null,2))),
    action(L('Download list','导出下载清单'),()=>save('mga-downloads.json',JSON.stringify(files.map(x=>({title:x.title,...x.download})),null,2))),
    action(L('Attribution list','导出署名清单'),()=>save('mga-attribution.txt',files.map(attribution).join('\n\n'),'text/plain')));
  const imp=node('input');imp.type='file';imp.accept='.json,application/json';imp.hidden=true;
  imp.onchange=async()=>{try{const file=imp.files[0];if(!file||file.size>8*1024*1024)throw Error();const imported=importProject(JSON.parse(await file.text()));
    if(projects.some(x=>x.id===imported.id)&&!window.confirm(L('Replace the local copy of this project?','替换本机已有的同一项目？')))return;
    projects=projects.filter(x=>x.id!==imported.id);projects.push(imported);activeId=imported.id;persist();projectLabel();pendingRefresh=true;renderProjects();
  }catch{$('#notice').textContent=L('Invalid project import','无法导入：项目格式无效');}finally{imp.value='';}};
  exportRow.append(action(L('Import project','导入项目'),()=>imp.click()),imp);box.append(exportRow);
  for(const [kind,title] of [['saved',L('Saved candidates','收藏候选')],['selected',L('Selected files','已选用')],['adopted',L('Confirmed adoption','确认实际采用')]]){
    box.append(node('h3',title+' · '+p[kind].length));
    for(const x of p[kind]){
      const item=node('div',null,'project-item row');item.append(node('span',x.title),fileLink(L('Original ↗','原件 ↗'),x.download?.url));
      if(kind==='saved')item.append(action(L('Select','选用'),()=>{projectAction('selected',x);renderProjects();}));
      if(kind==='selected')item.append(action(L('Confirm adoption','确认实际采用'),()=>{projectAction('adopted',x);renderProjects();}));
      item.append(action(L('Remove','移除'),()=>{p[kind]=p[kind].filter(a=>keyOf(a)!==keyOf(x));persist();pendingRefresh||=kind==='selected';renderProjects();}));box.append(item);
    }
  }
}
$('#search-form').onsubmit=e=>{e.preventDefault();void search();};
$('#more').onclick=()=>void search(true);
$('#rotate').onclick=()=>{active().round++;persist();void search();};
$('#mode').onchange=()=>{$('#rotate').disabled=$('#mode').value==='exact';void search();};
$('#share').onclick=()=>void copy(publicURL().href);
$('#agent-copy').onclick=()=>{const r=currentRequest();const body=JSON.stringify(r.payload).replace(/'/g,"'\"'\"'");void copy("curl -sS '"+location.origin+r.url+"' -H 'Content-Type: application/json' --data-binary '"+body+"'");};
$('#project-open').onclick=()=>{if(info){renderProjects();openSafeDialog('#projects');}};
$('#projects').addEventListener('close',()=>{if(pendingRefresh)void search();});
$('#detail').addEventListener('close',()=>{if(pendingRefresh)void search();});
async function start(){
  projectLabel();
  for(const [key,name] of Object.entries(modes)){const opt=node('option',L(...name));opt.value=key;$('#mode').append(opt);}
  $('#mode').value=Object.hasOwn(modes,params.get('mode'))?params.get('mode'):'explore';
  $('#rotate').disabled=$('#mode').value==='exact';
  $('#query').value=(params.get('q')||'').slice(0,512);$('#query').placeholder=L('e.g. side view of a mouse','例如：侧视小鼠');
  if(matchMedia('(max-width: 760px)').matches)$('#category-panel').open=false;
  try{info=await loadCatalog();}catch{statusText(L('Could not load the catalog navigation.','无法加载目录导航。'),()=>location.reload());return;}
  if(!info.categories.some(x=>x.id===category)&&category!=='unclassified')category='';
  for(const k of Object.keys(names)){const basic=['source','color_mode','format','structure'].includes(k);const f=field(k,params.get(k)||'');$('select',f).onchange=()=>void search();$(basic?'#basic':'#extra').append(f);if(!basic&&$('select',f).value)$('#advanced').open=true;}
  renderCategories();
  if(params.has('q')||params.has('category')||Object.keys(selectedFilters()).length)void search();
  else statusText(L('Choose a category or enter a search term to begin.','选择一个类别，或输入关键词开始查找。'));
}
void start();
