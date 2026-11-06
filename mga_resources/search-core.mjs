/** Portable search policy. No Node, SQLite, network, clock or UI dependencies.
 * The Node CLI and future Worker/MCP adapters call these same functions.
 */
import vocabulary from './search-vocabulary.mjs';

export const RANKING_VERSION = 'shared-search-2.0.0';
export const INDEX_VERSION = 2;
export const DEFAULT_CANDIDATE_POOL = 75;
const unknown = new Set(['', 'unknown', 'unspecified', 'not_reviewed', '待确认']);
const arr = value => value == null ? [] : Array.isArray(value) ? value : [value];
const unique = values => [...new Set(values)];
const text = value => value == null ? '' : Array.isArray(value) ? value.map(text).join(' ') : typeof value === 'object' ? Object.values(value).map(text).join(' ') : String(value);
const norm = value => text(value).normalize('NFKC').toLowerCase().replace(/[_–—]/g, ' ').replace(/\s+/g, ' ').trim();
const escape = value => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const cmp = (a, b) => a < b ? -1 : a > b ? 1 : 0;
const termPatterns=new Map();
const matches = (haystack, needle) => {
  if(/[\u3400-\u9fff]/.test(needle))return haystack.includes(needle);
  let pattern=termPatterns.get(needle);
  if(!pattern){pattern=new RegExp(`(?<![a-z0-9])${escape(needle)}(?![a-z0-9])`,'iu');if(termPatterns.size>=1024)termPatterns.delete(termPatterns.keys().next().value);termPatterns.set(needle,pattern);}
  return pattern.test(haystack);
};
const removeTerm = (value, term) => /[\u3400-\u9fff]/.test(term) ? value.split(term).join(' ') : value.replace(new RegExp(`(?<![a-z0-9])${escape(term)}(?![a-z0-9])`, 'giu'), ' ');

// These are narrow label vocabularies, not a mapping from benchmark IDs to assets.
const SCIENCE = {
  species: {
    mouse: ['小白鼠','小鼠','实验小鼠','mouse','mice','mus musculus'],
    rat: ['大鼠','实验大鼠','rat','rats','rattus','rattus norvegicus'],
    human: ['人体','人类','human','homo sapiens'], rabbit: ['兔','rabbit','oryctolagus cuniculus'],
    zebrafish: ['斑马鱼','zebrafish','danio rerio'], drosophila: ['果蝇','drosophila','fruit fly'],
  },
  state: {
    normal: ['正常','健康','normal','healthy'], pneumonia: ['肺炎','pneumonia'],
    inflamed: ['炎症','发炎','inflamed','inflammation'], cancer: ['癌','肿瘤','cancer','tumor','tumour','malignant'],
    apoptotic: ['凋亡','apoptotic','apoptosis'], activated: ['活化','activated'],
    infected: ['感染','infected','infection'], fibrotic: ['纤维化','fibrotic','fibrosis'],
  },
  view: {
    lateral: ['侧视','侧面','侧视图','side view','lateral','profile view'],
    anterior: ['正面','前视','front view','anterior','front'], posterior: ['背面','后视','back view','posterior'],
    cross_section: ['横截面','截面','横切面','cross section','cross-section','transverse section'],
    longitudinal_section: ['纵切面','longitudinal section'], top: ['俯视','top view'],
  },
  role: {
    whole_body: ['全身','整只','whole body','full body'], whole_organ: ['完整器官','整个器官','whole organ'],
    isolated_structure: ['局部结构','isolated structure'],
  },
};
const APPEARANCE = {
  color_mode: {black_white:['黑白','black and white','black-white'], grayscale:['灰阶','灰度','grayscale','greyscale'], color:['彩色','colored','coloured','color','colour']},
  representation: {line_art:['线稿','line art','outline'], silhouette:['剪影','silhouette'], flat_fill:['平面填色','flat fill','flat'], shaded:['明暗塑形','shaded'], hand_drawn:['手绘','hand drawn'], '3d_render':['3d渲染','三维渲染','3d render']},
  detail: {minimal:['极简','minimal','minimalist'], moderate:['适中细节','moderate detail'], detailed:['精细','detailed']},
  realism: {schematic:['示意风格','schematic style'], semi_realistic:['半写实','semi realistic','semi-realistic'], realistic:['写实','realistic']},
};
const FILTER_KEYS = new Set(['source','license','distribution_status','category','format','species','state','view','role','object','color_mode','palette_traits','representation','detail','realism','series','asset_id','family_id','file_sha256','transparent']);
const ALIASES = {color:'color_mode',colorMode:'color_mode',palette:'palette_traits'};
const extraConcepts = [
  ['脾脏','脾','spleen','splenic'], ['胃','stomach','gastric'], ['甲状腺','thyroid'],
  ['脂质双层','细胞膜','lipid bilayer','cell membrane'], ['脂质','lipid','lipids'],
  ['内皮细胞层','血管内皮细胞','endothelial cell layer','endothelial cell'],
  ['细菌杆菌','杆状细菌','bacillus','rod shaped bacteria','rod-shaped bacterium'],
  ['基因表达热图','gene expression heatmap','heatmap','heat map'],
  ['膀胱','bladder'], ['免疫细胞','immune cell'], ['正常肺','normal lung'],
];
// Avoid interpreting the unspecific Chinese experimental rodent as a mouse.
const concepts = [...vocabulary.concepts, ...extraConcepts].map(group => unique(group.map(norm).filter(x => x !== '实验鼠')));
// A named molecular object followed by its noun class is the same requested
// object, not a demand that the source redundantly repeat the word molecule.
for(const group of concepts)for(const name of ['atp','dna','rna','protein','antibody','lipid'])if(group.includes(name))group.push(`${name} molecule`);
const qualifiers = Object.entries({...SCIENCE,...APPEARANCE}).flatMap(([key, groups]) => Object.entries(groups).flatMap(([value, terms]) => terms.map(term => ({key,value,term:norm(term)})))).sort((a,b) => b.term.length-a.term.length || cmp(a.term,b.term));
const canonical = (key, value) => {
  const v = norm(value);
  for (const [name, aliases] of Object.entries(SCIENCE[key] || APPEARANCE[key] || {})) if (v === name || aliases.some(x => norm(x) === v)) return name;
  return String(value).trim();
};
function contextualWholeRole(value) {
  if(!/(?:完整|整个|\bwhole\b|\bcomplete\b)/.test(value))return null;
  if(/(?:全身|整只|\bbody\b)/.test(value))return 'whole_body';
  if(/(?:肝|肺|肾|脑|心脏|脾|胰腺|胃|器官|\bliver\b|\blung\b|\bkidney\b|\bbrain\b|\bheart\b|\bspleen\b|\bpancreas\b|\bstomach\b|\borgan\b)/.test(value))return 'whole_organ';
  if(/(?:\bmouse\b|\brat\b|小鼠|大鼠)/.test(value))return 'whole_body';
  return null;
}
function normalizeFilters(filters = {}) {
  const result = {};
  for (const [original, input] of Object.entries(filters)) {
    if (original === 'preferences') continue;
    const key = ALIASES[original] || original;
    if (!FILTER_KEYS.has(key)) throw new Error(`Unknown filter: ${original}`);
    const values = unique(arr(input).map(v => key === 'transparent' ? String(v) : canonical(key,v)).filter(v => !unknown.has(v)));
    if (!values.length && input != null) throw new Error(`Filter ${key} must specify a known value`);
    if (values.length) result[key] = values;
  }
  return result;
}

export function resolveFilters(filters = {}, {mode,project={}} = {}) {
  if(mode!=='design')return filters;
  const explicit=normalizeFilters(filters),style=normalizeFilters(project.style||{});
  for(const [key,values] of Object.entries(style)) if(explicit[key] && !values.some(value=>explicit[key].includes(value))) throw new Error(`Project style conflicts with explicit filter: ${key}`);
  return {...project.style,...filters};
}

export function buildQueryPlan(query = '', filters = {}, options = {}) {
  if(String(query).length>1000)throw new Error('Query must be at most 1000 characters');
  filters=resolveFilters(filters,options);
  let remaining = norm(query);
  const requirements = normalizeFilters(filters), preferences = normalizeFilters({...filters.preferences,...options.preferences});
  const explicit = new Set(Object.keys(requirements));
  const wholeRole=contextualWholeRole(remaining);
  if(wholeRole&&!explicit.has('role')){requirements.role=[wholeRole];for(const term of ['完整','整个','whole','complete'])remaining=removeTerm(remaining,term);}
  const preferVisual = /偏好|优先|prefer(?:red)?|if possible/.test(remaining);
  const parsed = [];
  // Qualifiers are recognized before noun matching to preserve explicit medical
  // conditions even inside compounds such as 正常肺 / normal lung.
  for (const entry of qualifiers) {
    if (!matches(remaining,entry.term)) continue;
    if (entry.key === 'species') {
      // Species also names the requested animal; leave it as an object term.
      if (!explicit.has(entry.key)) requirements[entry.key] = unique([...(requirements[entry.key]||[]),entry.value]);
    } else {
      const target = preferVisual && !['state','role'].includes(entry.key) ? preferences : requirements;
      if (!explicit.has(entry.key)) target[entry.key] = unique([...(target[entry.key]||[]),entry.value]);
      remaining = removeTerm(remaining,entry.term);
    }
    parsed.push(entry);
  }
  if (remaining.includes('人体') && /肝|肺|肾|心|脑|细胞|血管|胃|脾/.test(remaining)) remaining = remaining.replaceAll('人体',' human ');
  const terms = [];
  const nounMatches = concepts.flatMap(group => group.filter(term => matches(remaining,term)).map(term => ({term,group}))).sort((a,b)=>b.term.length-a.term.length || cmp(a.term,b.term));
  for (const {term,group} of nounMatches) {
    if (!matches(remaining,term)) continue;
    terms.push(unique([term,...group]));
    remaining = removeTerm(remaining,term);
  }
  // Remaining historical descriptive phrases are soft preferences only.
  const softTerms = [];
  for (const [term, aliases] of Object.entries(vocabulary.legacyPresentationTerms).sort((a,b)=>b[0].length-a[0].length)) {
    if (matches(remaining,norm(term))) {softTerms.push(aliases.map(norm)); remaining=removeTerm(remaining,norm(term));}
  }
  const context = [...vocabulary.contextTerms,'prefer','preferred','if possible','偏好','优先','需要','必须','要求'];
  for (const term of context.sort((a,b)=>b.length-a.length)) remaining=removeTerm(remaining,norm(term));
  for (const word of remaining.match(/[\p{L}\p{N}]+/gu)||[]) terms.push([word]);
  const groups = [...new Map(terms.map(group=>[stableStringify([...group].sort()),group])).values()];
  const fts = groups.map(group=>'('+group.map(term=>'"'+term.replaceAll('"','""')+'"').join(' OR ')+')').join(' AND ');
  const empty = !groups.length && !Object.keys(requirements).length && !Object.keys(preferences).length && Boolean(String(query).trim());
  return {query:String(query),terms:groups,fts,requirements,preferences,soft_terms:softTerms,empty,parsed_qualifiers:parsed,ranking_version:RANKING_VERSION};
}

function valuesFromMetadata(key, context, file) {
  const explicit = file.scientific?.[key] ?? file[key] ?? context.scientific?.[key] ?? context[key];
  if (explicit != null) {
    const structured=typeof explicit==='object' && !Array.isArray(explicit) ? explicit.values ?? explicit.value : explicit;
    return unique(arr(structured).map(value=>canonical(key,value)).filter(value=>!unknown.has(norm(value))));
  }
  const sourceText = norm([context.title,context.title_zh,context.tags,context.upstream_category]);
  if(key==='role'&&contextualWholeRole(sourceText))return [contextualWholeRole(sourceText)];
  return Object.entries(SCIENCE[key]||{}).filter(([,terms])=>terms.some(term=>matches(sourceText,norm(term)))).map(([value])=>value);
}
function compactAppearance(file) {
  const appearance=file.appearance;
  if (!appearance || appearance.binding?.file_sha256 !== file.sha256 || (file.preview?.sha256 && appearance.binding?.preview_sha256 !== file.preview.sha256)) return {classification_version:null,color:{mode:'unknown'},style:{}};
  const style=appearance.style||{};
  const safeAxis = (axis,many=false) => {
    const value=style[axis]||{};
    if (!value.basis_evidence_indices?.length || value.review_status==='needs_review' || value.confidence==='unknown') return many ? {values:[]} : {value:'unknown'};
    return value;
  };
  return {classification_version:appearance.classification_version,binding:appearance.binding,
    color:{mode:appearance.color?.mode||'unknown',palette_traits:appearance.color?.palette_traits||[],confidence:appearance.color?.confidence,review_status:appearance.color?.review_status,uncertainty:appearance.color?.uncertainty},
    style:{representation:safeAxis('representation',true),detail:safeAxis('detail'),realism:safeAxis('realism'),series:style.series?.basis_evidence_indices?.length ? style.series : {memberships:[]}}};
}

/** One row per physical file and original scientific context, never union
 * family colors/species onto every file. Rows are compact retrieval metadata.
 */
export function prepareVariants(records) {
  const result=[];
  for (const asset of records) for (const file of asset.files||[]) {
    const digest=file.sha256 || file.url;
    const origins=file.source_records?.length ? file.source_records : [{...asset,asset_id:asset.id}];
    for (const [originIndex,origin] of [...origins].sort((a,b)=>cmp(stableStringify(a),stableStringify(b))).entries()) {
      const variant=(asset.variants||[]).find(v=>v.id===origin.asset_id);
      const context={...variant,...origin};
      // Old normalized catalogs have only a title in per-file provenance.
      // Only fall back to the family metadata for single-variant families.
      const single=(asset.variants||[]).length<=1;
      const fields=single ? {...asset,...context} : context;
      const facts=Object.fromEntries(Object.keys(SCIENCE).map(key=>[key,valuesFromMetadata(key,fields,file)]));
      const appearance=compactAppearance(file);
      const title=norm([fields.title,fields.title_zh]);
      const contextText=norm([fields.description,fields.tags,fields.upstream_category,fields.categories,fields.anatomy,fields.action,fields.object,...Object.values(facts)]);
      const descriptive=title+' '+contextText;
      const aliases=unique(concepts.filter(group=>group.some(term=>matches(descriptive,term))).flat());
      const license=origin.license||asset.license;
      const rights=origin.distribution_status || (origin.license===asset.license ? asset.distribution_status : 'needs_rights_review');
      const canDownload=Boolean(file.archive?.url && file.archive?.member || /^https?:\/\//.test(file.url||''));
      const rejectedReview=[file.validation?.scientific_review,file.validation?.visual_review,asset.validation?.scientific_review,asset.validation?.visual_review].some(value=>['failed','rejected','unusable'].includes(value));
      const eligible=Boolean(file.sha256 && file.preview && canDownload && ['machine_checked','machine_checked_and_rendered'].includes(file.validation?.status) && rights==='license_recorded' && !rejectedReview);
      const declaredObjects=file.scientific?.object ?? fields.scientific?.object ?? fields.object;
      const objects=declaredObjects!=null ? arr(declaredObjects).map(norm) : unique(concepts.filter(group=>group.some(term=>matches(title,term))).flat());
      const values={...facts,source:arr(origin.source||asset.source),license:arr(license),distribution_status:arr(rights),category:arr(fields.taxonomy||asset.taxonomy),format:arr(file.format),
        color_mode:arr(appearance.color.mode).filter(v=>!unknown.has(v)),palette_traits:appearance.color.palette_traits||[],representation:appearance.style.representation?.values||[],detail:arr(appearance.style.detail?.value).filter(v=>!unknown.has(v)),realism:arr(appearance.style.realism?.value).filter(v=>!unknown.has(v)),series:(appearance.style.series?.memberships||[]).map(x=>x.id),
        asset_id:unique([asset.id,origin.asset_id].filter(Boolean)),family_id:arr(asset.family_id||asset.id),file_sha256:arr(file.sha256),object:objects,transparent:file.transparency?.has_transparent_pixels!=null ? [String(file.transparency.has_transparent_pixels)] : file.has_transparency!=null ? [String(file.has_transparency)] : []};
      const selectedFile=Object.fromEntries(['sha256','format','structure','url','archive','preview','validation','width','height','size_bytes','dimensions','transparency'].filter(k=>file[k]!=null).map(k=>[k,file[k]]));
      selectedFile.appearance=appearance;
      result.push({variant_id:[asset.id,digest,origin.asset_id||origin.source_id||'',originIndex].join(':'),asset_id:asset.id,family_id:asset.family_id||asset.id,title:fields.title||asset.title,source:origin.source||asset.source,source_id:origin.source_id||asset.source_id,author:origin.author||fields.author,attribution:origin.attribution||fields.attribution,values,eligible,facts,fact_basis:'explicit_source_fields_or_direct_source_labels; not independent scientific review',search:{title,aliases:aliases.join(' '),context:contextText},file:selectedFile,validation:asset.validation||{}});
    }
  }
  return result.sort((a,b)=>cmp(a.variant_id,b.variant_id));
}

/** SQL pushdown uses the SAME prepared value arrays and requirements as JS.
 * No LIMIT occurs here. It is safe for SQLite/D1 adapters to run count queries
 * over rejected rows without transporting their large metadata to the core.
 */
export function sqlitePlan(plan) {
  const from=plan.fts ? 'file_variants v JOIN file_variants_fts f ON f.variant_id=v.variant_id' : 'file_variants v';
  const textClauses=plan.fts ? ['file_variants_fts MATCH ?'] : [];
  const textParams=plan.fts ? [plan.fts] : [];
  const conditions=[{code:'unavailable_or_rights_unresolved',sql:'v.eligible=1',params:[]}];
  for(const [key,values] of Object.entries(plan.requirements)) conditions.push({code:`hard_filter:${key}`,sql:`EXISTS (SELECT 1 FROM json_each(v.values_json, '$.${key}') j WHERE j.value IN (${values.map(()=>'?').join(',')}))`,params:values});
  return {from,text_clauses:textClauses,text_params:textParams,conditions,where:[...textClauses,...conditions.map(c=>c.sql)].join(' AND ')||'1',params:[...textParams,...conditions.flatMap(c=>c.params)]};
}

function termMatch(value,term) { return matches(value,term); }
function lexicalScore(variant,plan) {
  const fields=variant.search,all=norm([fields.title,fields.aliases,fields.context]);
  let titleHits=0,contextHits=0;
  for(const group of plan.terms) {
    if(group.some(term=>termMatch(fields.title,term))) titleHits++;
    else if(group.some(term=>termMatch(fields.context,term))) contextHits++;
    else if(!group.some(term=>termMatch(all,term))) return null;
  }
  return plan.terms.length ? 1 + (titleHits + contextHits*0.4)/plan.terms.length : 1;
}
export function filterVariant(variant,plan) {
  if(!variant.eligible) return {matched:false,reason:'unavailable_or_rights_unresolved'};
  for(const [key,expected] of Object.entries(plan.requirements)) {
    const actual=variant.values[key]||[];
    if(!actual.length) return {matched:false,reason:`unknown:${key}`};
    if(!expected.some(v=>actual.includes(v))) return {matched:false,reason:`hard_filter:${key}`};
  }
  const relevance=lexicalScore(variant,plan);
  return relevance==null ? {matched:false,reason:'object_terms'} : {matched:true,relevance};
}
function preferenceScore(variant,plan) {
  let count=0;
  for(const [key,values] of Object.entries(plan.preferences)) if(values.some(value=>(variant.values[key]||[]).includes(value))) count++;
  const all=norm(variant.search);
  for(const group of plan.soft_terms) if(group.some(term=>matches(all,term))) count++;
  return Math.min(count,4)*0.06;
}
function pinFor(project,variant) {
  return (project?.selected||[]).find(pin=>pin.family_id===variant.family_id && pin.file_sha256===variant.file.sha256 && (!pin.asset_id || (variant.values.asset_id||[]).includes(pin.asset_id)));
}
export function groupFamilies(candidates,plan,project={}) {
  const groups=new Map();
  for(const candidate of candidates) {
    const row={...candidate,pinned:Boolean(pinFor(project,candidate)),preference_score:preferenceScore(candidate,plan)};
    const previous=groups.get(row.family_id);
    if(!previous || row.pinned && !previous.pinned || row.pinned===previous.pinned && (row.relevance+row.preference_score>previous.relevance+previous.preference_score || row.relevance+row.preference_score===previous.relevance+previous.preference_score && cmp(row.variant_id,previous.variant_id)<0)) groups.set(row.family_id,row);
  }
  return [...groups.values()];
}
// This is deterministic tie rotation, not a cryptographic/authentication hash.
function unitHash(value) {let h=2166136261;for(const char of value){h^=char.codePointAt(0);h=Math.imul(h,16777619);}return (h>>>0)/4294967296;}
function signature(row) {
  return new Set([...Object.entries(row.facts).flatMap(([key,values])=>values.map(v=>`${key}:${v}`)),...row.values.representation.map(v=>`style:${v}`),...row.values.series.map(v=>`series:${v}`),...row.search.title.split(/\s+/).filter(v=>v.length>2)]);
}
function similarity(a,b) {
  const sa=a._signature,sb=b._signature;
  let intersect=0;for(const value of sa)if(sb.has(value))intersect++;
  return sa.size+sb.size-intersect ? intersect/(sa.size+sb.size-intersect) : 0;
}
export function rankCandidates(candidates,{mode='explore',seed='default',history={},candidate_pool=DEFAULT_CANDIDATE_POOL}={}) {
  if(!['explore','design','exact'].includes(mode))throw new Error('mode must be explore, design or exact');
  if(!Number.isInteger(candidate_pool)||candidate_pool<50||candidate_pool>100)throw new Error('candidate_pool must be 50..100');
  const adopted=history.adopted||[];
  const prepared=candidates.map(row=>{
    // Browsing and trial downloads never become adoption penalties.
    const uses=adopted.filter(use=>use.family_id===row.family_id && use.status==='adopted').length;
    const score=row.relevance+row.preference_score-(mode==='exact'?0:Math.min(uses,3)*0.035);
    return {...row,_signature:signature(row),_score:score,_rotation:mode==='exact'?0:unitHash(`${seed}\0${row.family_id}`)*0.075};
  }).sort((a,b)=>Number(b.pinned)-Number(a.pinned)||b._score-a._score||b._rotation-a._rotation||cmp(a.family_id,b.family_id));
  if(mode==='exact')return prepared;
  const ranked=[];
  // Bound each MMR working set; later windows are retained for real pagination.
  // Grouping before windows prevents format/color variants consuming the pool.
  while(prepared.length) {
    const window=prepared.splice(0,candidate_pool);
    while(window.length) {
      let best=0,bestScore=-Infinity;
      for(let i=0;i<window.length;i++) {
        const row=window[i],recent=ranked.slice(-10);
        const redundancy=recent.length?Math.max(...recent.map(other=>similarity(row,other))):0;
        const score=row._score+row._rotation-(mode==='explore'?0.075:0.03)*redundancy+(row.pinned?1000:0);
        if(score>bestScore || score===bestScore && cmp(row.family_id,window[best].family_id)<0){best=i;bestScore=score;}
      }
      ranked.push(window.splice(best,1)[0]);
    }
  }
  return ranked;
}
export function stableStringify(value) {
  if(Array.isArray(value))return '['+value.map(stableStringify).join(',')+']';
  if(value&&typeof value==='object')return '{'+Object.keys(value).sort().filter(k=>value[k]!==undefined).map(k=>JSON.stringify(k)+':'+stableStringify(value[k])).join(',')+'}';
  return JSON.stringify(value);
}
async function sha(value) {const bytes=new TextEncoder().encode(stableStringify(value));return [...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(x=>x.toString(16).padStart(2,'0')).join('');}
const encodeCursor=value=>btoa(JSON.stringify(value)).replaceAll('+','-').replaceAll('/','_').replaceAll('=','');
const decodeCursor=value=>{try{return JSON.parse(atob(value.replaceAll('-','+').replaceAll('_','/')));}catch{throw new Error('Invalid cursor');}};

export async function searchCandidates(variants,request={}) {
  const {query='',filters={},limit=10,mode='explore',seed='default',history={},project={},catalog_version,cursor=null,candidate_pool=DEFAULT_CANDIDATE_POOL}=request;
  if(!catalog_version)throw new Error('catalog_version is required for reproducible search');
  if(!Number.isInteger(limit)||limit<1||limit>100)throw new Error('limit must be between 1 and 100');
  const plan=buildQueryPlan(query,filters,request);
  const bindings={catalog_version,ranking_version:RANKING_VERSION,query_plan:plan,mode,seed:String(seed),history_snapshot:await sha(history),project_snapshot:await sha(project),candidate_pool};
  const fingerprint=await sha(bindings);
  let offset=0;
  if(cursor){const decoded=decodeCursor(cursor);if(decoded.fingerprint!==fingerprint)throw new Error('Cursor inputs/version/history do not match');offset=decoded.offset;if(!Number.isSafeInteger(offset)||offset<0)throw new Error('Invalid cursor offset');}
  const counts={},uncertain=[],qualified=[];
  if(!plan.empty)for(const variant of variants){const match=filterVariant(variant,plan);if(match.matched)qualified.push({...variant,relevance:match.relevance});else{counts[match.reason]=(counts[match.reason]||0)+1;if(match.reason.startsWith('unknown:')&&uncertain.length<20)uncertain.push({variant_id:variant.variant_id,family_id:variant.family_id,reason:match.reason});}}
  const families=groupFamilies(qualified,plan,project);
  const ranked=rankCandidates(families,{mode,seed,history,candidate_pool});
  const results=ranked.slice(offset,offset+limit).map(row=>({asset_id:row.asset_id,family_id:row.family_id,variant_id:row.variant_id,title:row.title,selected_file:row.file,selected_source:{source:row.source,source_id:row.source_id,attribution:row.attribution},selected_science:{values:row.facts,basis:row.fact_basis},preview:row.file.preview,download:row.file.archive||{url:row.file.url},pinned:row.pinned,search_score:row._score,validation:row.validation}));
  const unavailablePins=(project.selected||[]).filter(pin=>!qualified.some(row=>pinFor({selected:[pin]},row))).map(pin=>({...pin,reason:'selected file no longer satisfies this query, hard filters, availability or catalog'}));
  return {results,next_cursor:offset+limit<ranked.length?encodeCursor({fingerprint,offset:offset+limit}):null,bindings,query_plan:plan,diagnostics:{input_variants:variants.length,qualified_variants:qualified.length,qualified_families:families.length,candidate_pool,rerank_windows:Math.ceil(families.length/candidate_pool),rejected:counts,uncertain_candidates:uncertain,unavailable_project_pins:unavailablePins,scarcity:families.length<limit,no_match:!results.length,acceptance:'not_established_by_search',resource_note:'All hard constraints precede family grouping and pool truncation. Adapter can use sqlitePlan pushdown; wide-query latency must be measured.'}};
}
