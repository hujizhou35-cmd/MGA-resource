import {$,node,webLink,loadCatalog,L,language} from './common.js';
try{
 const data=await loadCatalog(), table=node('table'), head=node('tr');
 for(const label of [L('Source','来源'),L('Contents','内容'),L('Originals','原件数'),L('Recorded licenses','记录许可')]){const th=node('th',label);th.scope='col';head.append(th);}
 const thead=node('thead');thead.append(head);table.append(thead);const body=node('tbody');table.append(body);
 for(const s of data.sources){
  const tr=node('tr'), source=node('td');source.append(webLink(s.name,s.url),' ',webLink(L('Browse ↗','浏览 ↗'),'/?source='+encodeURIComponent(s.id)));
  tr.append(source,node('td',s[language]),node('td',s.originals),node('td',s.licenses.join(', ')));body.append(tr);
 }
 $('#source-status').textContent=L(`${data.originals.toLocaleString()} distinct originals · ${data.sources.length} source channels`,`${data.originals.toLocaleString()} 个唯一原件 · ${data.sources.length} 个收录来源`);
 $('#source-table').replaceChildren(table);
}catch{$('#source-status').textContent=L('The source directory could not be loaded.','无法加载来源目录。');}
