const $=s=>document.querySelector(s);let page=null,active=null,turn=0;let variant=0;
const text=(tag,value,className)=>{const el=document.createElement(tag);el.textContent=value;if(className)el.className=className;return el;};
function safeURL(value){try{const url=new URL(value);return url.protocol==='https:'?url.href:null;}catch{return null;}}
function link(label,url){const el=text('a',label);const href=safeURL(url);if(href){el.href=href;el.target='_blank';el.rel='noopener noreferrer';}return el;}
function showDetail(result){const box=$('#detail-content');box.replaceChildren(text('h2',result.title));const img=new Image();img.src=result.preview.url;img.alt=result.title;box.append(img);
box.append(text('p',result.selected_file.structure+' · '+result.download.format.toUpperCase()+' · '+(result.download.size_bytes/1024).toFixed(1)+' KB'));
box.append(link('下载原件 ↗',result.download.url),document.createTextNode('　'),link('完整来源与元数据 ↗',result.metadata_url));
box.append(text('h3','来源与许可'),text('pre',(result.selected_source.provenance||[]).map(p=>[p.attribution,p.license,p.source_url].filter(Boolean).join('\n')).join('\n\n')));
box.append(text('h3','文件 SHA-256'),text('pre',result.download.sha256));
box.append(text('p','文字情况：'+(result.text_content?.status||'unknown')+'。下载后在工作副本上按任务处理标签，保留科学含义与署名。'));
$('#detail').showModal();}
function card(result){const el=text('article','', 'card');const preview=text('button','', 'preview-button');preview.type='button';preview.setAttribute('aria-label','查看 '+result.title);const img=new Image();img.src=result.preview.url;img.alt=result.title;img.loading='lazy';preview.append(img);preview.addEventListener('click',()=>showDetail(result));el.append(preview);
const body=text('div','', 'card-body');body.append(text('h2',result.title),text('p',result.selected_source.source+' · '+(result.selected_source.license||[]).join(', '),'meta'));
const tags=text('div','', 'tags');for(const value of [result.download.format.toUpperCase(),result.selected_file.structure,result.text_content?.status==='present'?'含文字标签':null].filter(Boolean))tags.append(text('span',value,'tag'));body.append(tags);
const links=text('div','', 'card-links');links.append(link('下载原件 ↗',result.download.url),link('元数据 ↗',result.metadata_url));body.append(links);el.append(body);return el;}
async function search(append=false){const current=++turn;const request=append?{...active,cursor:page.next_cursor}:{query:$('#query').value,filters:{},mode:$('#mode').value,seed:$('#seed').value,limit:12};
if(!append){if($('#format').value)request.filters.format=$('#format').value;if($('#color').value)request.filters.color_mode=$('#color').value;active=request;$('#results').replaceChildren();}
$('#status').textContent='正在检索…';$('#more').disabled=true;
try{const response=await fetch('/v1/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(request)});const data=await response.json();if(current!==turn)return;if(!response.ok)throw new Error(data.message||data.error||'检索暂时不可用');page=data;
for(const item of data.results)$('#results').append(card(item));$('#status').textContent=data.results.length?`已显示 ${$('#results').children.length} 件 · 共 ${data.diagnostics.qualified_families} 个符合条件的素材家族`:'没有找到满足条件的素材。可检查对象名称与筛选条件。';$('#json-output').textContent=JSON.stringify(data,null,2);$('#more').hidden=!data.next_cursor;
}catch(error){if(current===turn){$('#status').textContent='检索未完成：'+error.message;$('#more').hidden=true;}}
finally{$('#more').disabled=false;}}
$('#search-form').addEventListener('submit',event=>{event.preventDefault();search();});$('#more').addEventListener('click',()=>search(true));$('#alternate').addEventListener('click',()=>{$('#seed').value='mga-browser-'+(++variant);search();});
$('#json-toggle').addEventListener('click',()=>{const hidden=!$('#json-output').hidden;$('#json-output').hidden=hidden;$('#json-toggle').setAttribute('aria-expanded',String(!hidden));});$('.close').addEventListener('click',()=>$('#detail').close());
search();
