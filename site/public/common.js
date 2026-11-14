export const $=(s,el=document)=>el.querySelector(s);
export const $$=(s,el=document)=>Array.from(el.querySelectorAll(s));
export function node(tag,value,cls){const el=document.createElement(tag);if(value!=null)el.textContent=value;if(cls)el.className=cls;return el;}
export function mediaURL(value){
  try{
    const u=new URL(value,location.origin);
    return u.protocol==='https:'&&(/^mga-resource-files-[012]\.pages\.dev$/.test(u.hostname)||
      (u.hostname==='github.com'&&u.pathname.startsWith('/hujizhou35-cmd/MGA-resource/releases/download/website-files-v1/')))?u.href:null;
  }catch{return null;}
}
export function webLink(label,value){const el=node('a',label);try{const url=new URL(value,location.origin);if(url.protocol==='https:'||url.origin===location.origin){el.href=url;el.rel='noopener noreferrer';}}catch{}return el;}
export function fileLink(label,value){return webLink(label,mediaURL(value)||'#');}
export function action(label,fn,cls='quiet'){const b=node('button',label,cls);b.type='button';b.addEventListener('click',fn);return b;}
export function localGet(key){try{return localStorage.getItem(key);}catch{return null;}}
export function localSet(key,value){try{localStorage.setItem(key,value);return true;}catch{return false;}}
const lang=new URL(location.href).searchParams.get('lang');
export let language=['en','zh'].includes(lang)?lang:(localGet('mga-language')||(/^zh/i.test(navigator.language)?'zh':'en'));
export const L=(en,zh)=>language==='zh'?zh:en;
document.documentElement.lang=language==='zh'?'zh-CN':'en';
$$('[data-en][data-zh]').forEach(el=>{el.textContent=el.dataset[language];});
$$('[data-locale]').forEach(el=>el.hidden=el.dataset.locale!==language);
$('#language')?.addEventListener('click',()=>{const next=L('zh','en');localSet('mga-language',next);const u=new URL(location.href);u.searchParams.set('lang',next);location.assign(u);});
export async function copy(value){
  try{await navigator.clipboard.writeText(value);const live=$('#notice');live.textContent=L('Copied to clipboard','已复制');}
  catch{window.prompt(L('Copy this text','复制以下文本'),value);}
}
export function save(name,value,type='application/json'){
  const uri=URL.createObjectURL(new Blob([value],{type}));
  const link=node('a');link.href=uri;link.download=name;link.click();
  setTimeout(()=>URL.revokeObjectURL(uri),1000);
}
export async function loadCatalog(){const r=await fetch('/catalog-info.json');if(!r.ok)throw Error('Catalog navigation unavailable');return r.json();}
