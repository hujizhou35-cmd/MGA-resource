/** Portable search policy. No Node, SQLite, network, clock or UI dependencies.
 * The Node CLI and future Worker/MCP adapters call these same functions.
 */
import vocabulary from './search-vocabulary.mjs';

export const RANKING_VERSION = 'shared-search-3.4.3';
export const INDEX_VERSION = 9;
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
    drosophila_melanogaster: ['黑腹果蝇','drosophila melanogaster'],
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
const STRUCTURES = new Set(['raster','pure_vector','mixed','embedded_raster']);
const CONTENT_TYPES = new Set(['illustration','photograph','micrograph','photograph_or_real_micrograph']);
const FILTER_KEYS = new Set(['source','license','distribution_status','category','format','structure','content_type','species','state','view','role','object','depiction_kind','color_mode','palette_traits','representation','detail','realism','series','asset_id','family_id','file_sha256','transparent']);
// The API's historical media_type filter means visual file structure. The
// file.media_type metadata is a MIME string and must never supply this value.
const ALIASES = {color:'color_mode',colorMode:'color_mode',palette:'palette_traits',media_type:'structure'};
const extraConcepts = [
  ['脾脏','脾','spleen','splenic'], ['胃','stomach','gastric'], ['甲状腺','thyroid'],
  ['脂质双层','细胞膜','lipid bilayer','cell membrane'], ['脂质','lipid','lipids'],
  ['内皮细胞层','血管内皮细胞','endothelial cell layer','endothelial cell'],
  ['细菌杆菌','杆状细菌','bacillus','rod shaped bacteria','rod-shaped bacterium'],
  ['基因表达热图','gene expression heatmap','heatmap','heat map'],
  ['膀胱','bladder'], ['免疫细胞','immune cell'], ['正常肺','normal lung'],
  ['头部','头','head'], ['颅骨','skull'], ['尾巴','tail'], ['爪','paw'],
  ['前肢','forelimb'], ['后肢','hindlimb'], ['视网膜','retina'], ['脊髓','spinal cord'],
  ['胚胎','embryo'], ['胎儿','fetus','foetus'], ['骨髓','bone marrow'],
  ['电脑鼠标','鼠标','computer mouse','mouse cursor','pointing device'],
  ['人体','人类','human body','human','homo sapiens'],
  ['采血','抽血','taking blood sample','taking a blood sample','drawing blood','blood draw','blood collection','blood sampling','phlebotomy','venipuncture'],
  ['试管','test tube','sample tube','collection tube'],
  ['细胞分选仪','cell sorter','magnetic cell sorter'],
  ['细胞周期','cell cycle'], ['细胞分选','cell sorting'],
  ['肝吸虫','liver fluke','fasciola'], ['肝小叶','liver lobule','hepatic lobule'],
  ['黑腹果蝇','drosophila melanogaster'],
  // Tissue from an organ is a more specific object than the whole organ.
  // Do not drop the noun class globally or let a broad tissue tag prove it.
  ['肺组织','lung tissue','pulmonary tissue'],
];
// Avoid interpreting the unspecific Chinese experimental rodent as a mouse.
const concepts = [...vocabulary.concepts, ...extraConcepts].map(group => unique(group.map(norm).filter(x => x !== '实验鼠')));
// A named molecular object followed by its noun class is the same requested
// object, not a demand that the source redundantly repeat the word molecule.
for(const group of concepts){
  if(group.includes('lung'))group.push('lungs');
  for(const name of ['atp','dna','rna','protein','antibody','lipid'])if(group.includes(name))group.push(`${name} molecule`,`${name}分子`,`${name} 分子`);
  if(group.includes('bone'))group.push('bone tissue','骨组织','骨 组织');
  if(group.includes('cartilage'))group.push('cartilage tissue','软骨组织','软骨 组织');
}
const qualifiers = Object.entries({...SCIENCE,...APPEARANCE}).flatMap(([key, groups]) => Object.entries(groups).flatMap(([value, terms]) => terms.map(term => ({key,value,term:norm(term)})))).sort((a,b) => b.term.length-a.term.length || cmp(a.term,b.term));
const animalNames=new Set(Object.values(SCIENCE.species).flat().map(norm));
const nonBiologicalMouse=value=>['computer mouse','mouse cursor','pointing device','电脑鼠标','鼠标'].some(term=>matches(value,term));
const actionNames=['injection','blood collection','blood sampling','centrifugation','pipetting','cell culture','transfection','sequencing','dissection','randomization','randomisation','recruitment','taking blood sample','drawing blood','blood draw','phlebotomy','venipuncture','cell cycle','cell sorting'];
const equipmentNames=['computer mouse','mouse cursor','syringe','pipette','microscope','centrifuge','tube','flask','beaker','incubator','well plate','microplate','petri dish','culture dish','needle','scissors','cutters','sequencer','sorter','cell sorter','machine','instrument','apparatus','voltammetry','electrochemical','electrolytic','battery','solar cell'];
const actionGroups=concepts.filter(group=>group.some(term=>actionNames.includes(term)));
const equipmentGroups=concepts.filter(group=>group.some(term=>equipmentNames.includes(term)));
const cellNames=new Set(['cell','cells','macrophage','neuron','lymphocyte','neutrophil','hepatocyte','erythrocyte','fibroblast','stem cell','t cell','b cell','endothelial cell']);
const subcellularNames=new Set(['cell membrane','cell nucleus','nucleus','mitochondria','mitochondrion','endoplasmic reticulum','golgi','lysosome','ribosome','cell membrane receptor','membrane receptor','receptor']);
const organNames=new Set(['heart','brain','liver','lung','kidney','intestine','pancreas','stomach','spleen','thyroid','bladder']);
function primaryConceptGroups(groups,label='') {
  let selected=groups.filter(group=>group.some(term=>concepts.some(known=>known.includes(term))));
  const matching=names=>selected.filter(group=>group.some(term=>names.has(term)));
  const equipment=selected.filter(group=>equipmentGroups.some(known=>group.some(term=>known.includes(term))));
  const actions=selected.filter(group=>actionGroups.some(known=>group.some(term=>known.includes(term))));
  if(equipment.length&&!/\b(?:taking|drawing|collecting|injecting|pipetting|centrifuging|dissecting)\b/.test(label))return equipment;
  if(actions.length)return actions;
  const development=matching(new Set(['embryo','fetus','foetus']));
  if(development.length)return development;
  if(matching(subcellularNames).length)return matching(subcellularNames);
  if(matching(cellNames).length)return matching(cellNames);
  const notAnimals=selected.filter(group=>!group.some(term=>animalNames.has(term)));
  if(notAnimals.length)selected=notAnimals;
  // Organs qualify a more specific structure: heart cell is a cell from the
  // heart, not evidence of a whole heart. Parallel named organs stay parallel.
  const specific=selected.filter(group=>!group.some(term=>organNames.has(term)));
  if(specific.length&&selected.some(group=>group.some(term=>organNames.has(term))))selected=specific;
  return selected;
}
function explicitDepictionDescription(value) {
  // Only an explicit depiction statement may replace a generic title. Broad
  // keywords, tags, citations and background discussion do not name the object.
  const description=norm(value);
  const found=description.match(/(?:^|[.!?]\s+)(?:this\s+)?(?:(?:image|illustration|drawing|diagram|icon)\s+(?:of|depicts|shows)|(?:an?\s+)?(?:illustration|drawing|diagram|icon)\s+of|depicts|shows)\s+([^.!?]{1,180})/);
  return found?.[1]||'';
}
function depictionKind(title,objects,unresolvedAnimal){
  const equipment=equipmentNames.some(term=>matches(title,term));
  const action=actionGroups.some(group=>group.some(term=>matches(title,term)));
  const activeVerb=/\b(?:taking|drawing|collecting|injecting|pipetting|centrifuging|dissecting)\b/.test(title);
  if(equipment&&/\b(?:user|operator)\b/.test(title)&&!activeVerb)return 'unknown';
  if(equipment&&!activeVerb)return 'equipment';
  if(action)return 'action';
  if(['embryo','fetus','foetus','胚胎','胎儿'].some(term=>matches(title,term)))return 'developmental_stage';
  if(['cell membrane','cell wall','cell nucleus','axon','dendrite','细胞膜','细胞壁','细胞核','轴突','树突'].some(term=>matches(title,term)))return 'subcellular';
  if([...cellNames,'细胞','神经元'].some(term=>matches(title,term)))return 'cell';
  if(objects.some(term=>animalNames.has(term))&&!unresolvedAnimal)return 'animal';
  return objects.length?'named_object':'unknown';
}
function animalTitleRemainder(title){
  let rest=title;
  const modifiers=[...animalNames,...qualifiers.filter(q=>q.key!=='state').map(q=>q.term),...vocabulary.contextTerms,
    'adult','juvenile','newborn','neonatal','male','female','standing','sitting','running','walking','eating','drinking','sleeping','rearing','grooming','climbing','lying','dorsal','ventral','white','black','gray','grey','brown','albino','house','cartoon','simple','outline','stroke','tricolor','raster','svg','png','vector','design','drawing','silhouette','view','body','full body','whole body',
    '成年','幼年','雄性','雌性','站立','坐姿','奔跑','行走','进食','饮水','睡眠','轮廓','白色','黑色','灰色'];
  for(const term of modifiers.sort((a,b)=>b.length-a.length))rest=removeTerm(rest,norm(term));
  return rest.replace(/\b(?:c57bl[\w/-]*|balb[\w/-]*|p\d+|\d+)\b/gi,' ').replace(/[^\p{L}\p{N}]+/gu,' ').trim();
}
const phraseVocabulary=unique([...concepts.flat(),...qualifiers.map(q=>q.term),...Object.values(vocabulary.legacyPresentationTerms).flat()]).filter(term=>term.includes(' ')||term.includes('-')||/[\u3400-\u9fff]/.test(term));
const termKey=value=>norm(value).replace(/-/g,' ').replace(/\s+/g,' ');
function indexTerms(value) {
  const normalized=norm(value);
  return unique([...(normalized.match(/[\p{L}\p{N}]+/gu)||[]).map(termKey),...phraseVocabulary.filter(term=>matches(normalized,term)).map(termKey)]).sort(cmp);
}
const canonical = (key, value) => {
  const v = norm(value);
  if(key==='structure'||key==='content_type')return v.replace(/\s+/g,'_');
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
    const allowed=key==='structure'?STRUCTURES:key==='content_type'?CONTENT_TYPES:null;
    if(allowed&&values.some(value=>!allowed.has(value)))throw new Error(`Unsupported ${key} filter value; expected ${[...allowed].join(', ')}`);
    if (values.length) {
      const combined=result[key]?result[key].filter(value=>values.includes(value)):values;
      if(!combined.length)throw new Error(`Conflicting aliases for filter: ${key}`);
      result[key]=combined;
    }
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
  const mediaPhrases=[];
  // These phrases request a file representation, not medical object words.
  // Do not strip bare "vector": viral/expression vectors are real subjects.
  // Pure vector is a machine structure finding, never an Illustrator claim.
  for(const phrase of ['可编辑的矢量图','可编辑矢量图','可编辑的矢量','可编辑矢量','editable vector graphics','editable vector image','editable vector','editable svg','可编辑svg','可编辑 svg']) {
    if(!matches(remaining,phrase))continue;
    if(requirements.structure&&!requirements.structure.includes('pure_vector'))throw new Error('Query requests editable vector structure but conflicts with explicit structure filter');
    requirements.structure=['pure_vector'];
    remaining=removeTerm(remaining,phrase);
    mediaPhrases.push(phrase);
  }
  const wholeRole=contextualWholeRole(remaining);
  if(wholeRole&&!explicit.has('role')){requirements.role=[wholeRole];for(const term of ['完整','整个','whole','complete'])remaining=removeTerm(remaining,term);}
  const preferVisual = /偏好|优先|prefer(?:red)?|if possible/.test(remaining);
  const parsed = [];
  // Qualifiers are recognized before noun matching to preserve explicit medical
  // conditions even inside compounds such as 正常肺 / normal lung.
  for (const entry of qualifiers) {
    if (!matches(remaining,entry.term)) continue;
    if(entry.key==='species'&&entry.value==='mouse'&&nonBiologicalMouse(remaining))continue;
    // A requested named species must not become an OR with its broad genus.
    if(entry.key==='species'&&entry.value==='drosophila'&&SCIENCE.species.drosophila_melanogaster.some(term=>matches(remaining,term)))continue;
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
  const context = [...vocabulary.contextTerms,'process','过程','prefer','preferred','if possible','偏好','优先','需要','必须','要求'];
  for (const term of context.sort((a,b)=>b.length-a.length)) remaining=removeTerm(remaining,norm(term));
  for (const word of remaining.match(/[\p{L}\p{N}]+/gu)||[]) terms.push([word]);
  const groups = [...new Map(terms.map(group=>[stableStringify([...group].sort()),group])).values()];
  const allRequirements={};
  if(!explicit.has('view')&&(requirements.view||[]).length>1&&!/\bor\b|或者|或/.test(norm(query)))allRequirements.view=requirements.view;
  // A lone requested animal is the object, not merely the species of a brain,
  // cell or surgical instrument. More specific compound queries retain their
  // requested object terms (e.g. mouse brain) and the hard mouse species.
  if(!explicit.has('object') && groups.length && groups.every(group=>group.some(term=>animalNames.has(term)))) {
    requirements.object=unique(groups.flatMap(group=>group.filter(term=>animalNames.has(term))));
  }
  const objectGroups=!explicit.has('object')&&!requirements.object?primaryConceptGroups(groups,norm(query)):[];
  if(objectGroups.length)requirements.object=unique(objectGroups.flat());
  if(!explicit.has('depiction_kind')) {
    if(groups.some(group=>actionGroups.some(action=>group.some(term=>action.includes(term)))))requirements.depiction_kind=['action'];
    else if(groups.length&&groups.every(group=>equipmentGroups.some(equipment=>group.some(term=>equipment.includes(term)))))requirements.depiction_kind=['equipment'];
    else if(groups.some(group=>group.some(term=>['cell','cells','细胞'].includes(term)))&&groups.every(group=>group.some(term=>['cell','cells','细胞'].includes(term)||animalNames.has(term))))requirements.depiction_kind=['cell'];
  }
  const fts = groups.map(group=>'('+group.map(term=>'"'+term.replaceAll('"','""')+'"').join(' OR ')+')').join(' AND ');
  const empty = !groups.length && !Object.keys(requirements).length && !Object.keys(preferences).length && Boolean(String(query).trim());
  return {query:String(query),terms:groups,fts,requirements,all_requirements:allRequirements,object_groups:objectGroups,preferences,soft_terms:softTerms,empty,parsed_qualifiers:parsed,ranking_version:RANKING_VERSION,
    ...(mediaPhrases.length?{media_interpretation:{phrases:mediaPhrases,structure:'pure_vector',basis:'explicit_query_media_phrase',native_editability_verified:false,limitation:'Pure vector describes machine-inspected file structure; native application editing/import remains separately unverified.'}}:{})};
}

function manualEvidence(file,key) {
  if((file.scientific_conflicts||[]).includes(key))return [];
  return (file.scientific_review_evidence||[]).flatMap((e,index)=>{
    const archive=e.applies_to_archive||e.original_archive;
    const previewArchive=e.applies_to_preview_archive||e.preview_archive;
    const accepted=e.accepted_assertions||{};
    const bound=archive?.url===file.archive?.url&&archive?.member===file.archive?.member&&Boolean(archive?.url&&archive?.member)&&previewArchive?.url===file.preview?.archive?.url&&previewArchive?.member===file.preview?.archive?.member&&Boolean(previewArchive?.url&&previewArchive?.member);
    const actual=arr(file.scientific?.[key]).map(value=>norm(canonical(key,value)));
    const supported=arr(accepted[key]).map(value=>norm(canonical(key,value)));
    const matchesClaim=actual.length&&actual.every(value=>supported.includes(value));
    return e.kind==='manual_visual_review'&&['applied','partial_conflict'].includes(e.status)&&e.original_sha256===file.sha256&&e.preview_sha256===file.preview?.sha256&&bound&&Object.hasOwn(accepted,key)&&matchesClaim&&String(e.reviewer||'').trim()&&String(e.evidence_uri||'').trim()&&/^[a-f0-9]{64}$/.test(e.evidence_sha256||'')?[index]:[];
  });
}
function scientificValue(file,key) {
  if((file.scientific_conflicts||[]).includes(key))return 'unknown';
  const value=file.scientific?.[key];
  const manualClaim=(file.scientific_review_evidence||[]).some(e=>Object.hasOwn(e.accepted_assertions||{},key));
  return manualClaim&&!manualEvidence(file,key).length?'unknown':value;
}
function valuesFromMetadata(key, context, file) {
  const explicit = scientificValue(file,key) ?? file[key] ?? context.scientific?.[key] ?? context[key];
  if (explicit != null) {
    const structured=typeof explicit==='object' && !Array.isArray(explicit) ? explicit.values ?? explicit.value : explicit;
    const values=unique(arr(structured).map(value=>canonical(key,value)).filter(value=>!unknown.has(norm(value))));
    if(key==='species'&&values.includes('drosophila_melanogaster'))values.push('drosophila');
    return unique(values);
  }
  const sourceText = norm([context.title,context.title_zh,context.tags,context.upstream_category]);
  if(key==='species'&&nonBiologicalMouse(norm([context.title,context.title_zh])))return [];
  if(key==='role'&&contextualWholeRole(sourceText))return [contextualWholeRole(sourceText)];
  return Object.entries(SCIENCE[key]||{}).filter(([,terms])=>terms.some(term=>matches(sourceText,norm(term)))).map(([value])=>value);
}
function compactAppearance(file) {
  const appearance=file.appearance;
  if (!appearance || appearance.binding?.file_sha256 !== file.sha256 || (file.preview?.sha256 && appearance.binding?.preview_sha256 !== file.preview.sha256)) return {classification_version:null,color:{mode:'unknown'},style:{}};
  const style=appearance.style||{};
  const unresolvedStatus=new Set(['needs_review','not_measured','not_reviewed','conflict','conflict_pending']);
  const safeAxis = (axis,many=false) => {
    const value=style[axis]||{};
    if (!value.basis_evidence_indices?.length || unresolvedStatus.has(value.review_status) || value.confidence==='unknown') return many ? {values:[]} : {value:'unknown'};
    return value;
  };
  const color=appearance.color||{};
  // Known machine measurements may have medium confidence (e.g. a white
  // background hypothesis). Only explicitly unresolved values are withheld;
  // a tentative palette must never rescue an unknown/unmeasured color mode.
  const knownColor=['black_white','grayscale','color'].includes(color.mode)&&!unresolvedStatus.has(color.review_status)&&color.confidence!=='unknown';
  const series=style.series||{};
  return {classification_version:appearance.classification_version,binding:appearance.binding,
    color:{mode:knownColor?color.mode:'unknown',palette_traits:knownColor?(color.palette_traits||[]):[],confidence:color.confidence,review_status:color.review_status,uncertainty:color.uncertainty},
    style:{representation:safeAxis('representation',true),detail:safeAxis('detail'),realism:safeAxis('realism'),series:series.basis_evidence_indices?.length&&!unresolvedStatus.has(series.review_status)&&series.confidence!=='unknown' ? series : {memberships:[]}}};
}

function compactContentType(file) {
  const binding={file_sha256:file.sha256,preview_sha256:file.preview?.sha256};
  const unknownContent=(reason)=>({value:'unknown',review_status:'not_established',binding,basis:reason,evidence:[]});
  const validDigest=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
  const validProof=e=>e&&typeof e.uri==='string'&&Boolean(e.uri.trim())&&validDigest(e.sha256);
  const scope=file.public_scope||{};
  // A component-suitability decision is a different review dimension. Even
  // an aggregate media_type=included flag cannot borrow its other proof.
  const scopeIllustration=scope.scope_dimension==='media_type'&&scope.status==='included'
    &&scope.reviewed_dimensions?.media_type==='included'
    &&scope.evidence_original_sha256===file.sha256&&validDigest(file.sha256)
    &&scope.evidence_preview_sha256===file.preview?.sha256&&validDigest(file.preview?.sha256)
    &&Boolean(String(scope.reviewed_by||'').trim()&&String(scope.reviewed_at||'').trim())
    &&Array.isArray(scope.evidence)&&scope.evidence.some(validProof);
  const claim=file.content_type;
  if(claim!=null) {
    if(typeof claim!=='object'||Array.isArray(claim))return unknownContent('Content label lacks a file-bound typed evidence record.');
    const value=canonical('content_type',claim.value??'unknown');
    const exact=claim.binding?.file_sha256===file.sha256&&validDigest(file.sha256)
      &&claim.binding?.preview_sha256===file.preview?.sha256&&validDigest(file.preview?.sha256);
    const proof=arr(claim.evidence).filter(e=>validProof(e)&&(
      e.kind==='manual_visual_review'&&claim.review_status==='visual_reviewed'&&Boolean(String(e.reviewer||claim.reviewer||'').trim())
      ||e.kind==='official_source_file'&&claim.review_status==='official_file_label'));
    if(!exact||!CONTENT_TYPES.has(value)||!proof.length)return unknownContent('Content type is unknown, unresolved, or its evidence does not bind this file and preview.');
    if(scopeIllustration&&value!=='illustration')return unknownContent('Explicit content type conflicts with this file\'s visual media review.');
    return {value,review_status:claim.review_status,binding,basis:'explicit_file_content_type',evidence:proof};
  }
  if(scopeIllustration)return {value:'illustration',review_status:'visual_reviewed',binding,
    basis:'file_bound_scope_media_type_review',decision_id:scope.decision_id,
    reviewer:scope.reviewed_by,reviewed_at:scope.reviewed_at,evidence:scope.evidence.filter(validProof)};
  return unknownContent('No explicit file-bound content-type evidence. Format, MIME and public eligibility do not establish image content.');
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
      const factBasis=Object.fromEntries(Object.keys(facts).map(key=>[key,(file.scientific_conflicts||[]).includes(key)?{kind:'conflict_pending'}:!facts[key].length?{kind:'unknown'}:manualEvidence(file,key).length?{kind:'manual_visual_review',evidence_indices:manualEvidence(file,key)}:{kind:(file.scientific?.[key]??file[key]??fields.scientific?.[key]??fields[key])!=null?'explicit_source_field':'direct_source_label',evidence:fields.title||null}]));
      const appearance=compactAppearance(file);
      const contentType=compactContentType(file);
      const title=norm([fields.title,fields.title_zh]);
      const contextText=norm([fields.description,fields.tags,fields.upstream_category,fields.categories,fields.anatomy,fields.action,scientificValue(file,'object')??file.object??fields.object,...Object.values(facts)]);
      const descriptive=title+' '+contextText;
      const aliases=unique(concepts.filter(group=>group.some(term=>matches(descriptive,term))).flat());
      const license=origin.license||asset.license;
      const rights=origin.distribution_status || (origin.license===asset.license ? asset.distribution_status : 'needs_rights_review');
      const canDownload=Boolean(file.archive?.url && file.archive?.member || /^https?:\/\//.test(file.url||''));
      const rejectedReview=[file.validation?.scientific_review,file.validation?.visual_review,asset.validation?.scientific_review,asset.validation?.visual_review].some(value=>['failed','rejected','unusable'].includes(value));
      const publicScopeAllowed=![asset,origin,file].some(item=>item.catalog_visibility==='internal_diagnostic'||item.public_scope?.public_allowed===false||['excluded','pending_review'].includes(item.public_scope?.status)||item.scope_status==='excluded');
      const eligible=Boolean(publicScopeAllowed && file.sha256 && file.preview && canDownload && ['machine_checked','machine_checked_and_rendered'].includes(file.validation?.status) && rights==='license_recorded' && !rejectedReview);
      const declaredObjects=scientificValue(file,'object') ?? file.object ?? fields.scientific?.object ?? fields.object;
      const titleConcepts=concepts.filter(group=>group.some(term=>matches(title,term)));
      const descriptionLabel=titleConcepts.length?'':explicitDepictionDescription(fields.description);
      const objectLabel=descriptionLabel||title;
      const namedConcepts=descriptionLabel?concepts.filter(group=>group.some(term=>matches(descriptionLabel,term))):titleConcepts;
      const nonAnimalConcepts=namedConcepts.filter(group=>!group.some(term=>animalNames.has(term)));
      // In explicit compound labels such as mouse brain / mouse cell / mouse
      // syringe, animal words describe species/context rather than the primary
      // depicted object. Unknown titles do not borrow objects from broad tags.
      const primaryConcepts=primaryConceptGroups(namedConcepts,objectLabel);
      const declared=arr(declaredObjects).map(norm);
      const unresolvedAnimalTitle=!nonAnimalConcepts.length&&namedConcepts.some(group=>group.some(term=>animalNames.has(term)))&&Boolean(animalTitleRemainder(objectLabel));
      const objects=declaredObjects!=null ? unique(declared.filter(value=>!unknown.has(value)).flatMap(value=>[value,...concepts.filter(group=>group.includes(value)).flat()])) : unresolvedAnimalTitle?[]:unique(primaryConcepts.flat());
      const kind=scientificValue(file,'depiction_kind')??fields.scientific?.depiction_kind??fields.depiction_kind??depictionKind(objectLabel,objects,unresolvedAnimalTitle);
      // Named cell types remain discoverable by their biological parent class;
      // this is an explicit ontology relation, not a substring/equipment match.
      if(kind==='cell'){aliases.push('cell','cells','细胞');objects.push('cell','cells','细胞');}
      const values={...facts,source:arr(origin.source||asset.source),license:arr(license),distribution_status:arr(rights),category:arr(fields.taxonomy||asset.taxonomy),format:arr(file.format),
        structure:STRUCTURES.has(file.structure)?[file.structure]:[],content_type:CONTENT_TYPES.has(contentType.value)?[contentType.value]:[],
        color_mode:arr(appearance.color.mode).filter(v=>!unknown.has(v)),palette_traits:appearance.color.palette_traits||[],representation:appearance.style.representation?.values||[],detail:arr(appearance.style.detail?.value).filter(v=>!unknown.has(v)),realism:arr(appearance.style.realism?.value).filter(v=>!unknown.has(v)),series:(appearance.style.series?.memberships||[]).map(x=>x.id),
        asset_id:unique([asset.id,origin.asset_id].filter(Boolean)),family_id:arr(asset.family_id||asset.id),file_sha256:arr(file.sha256),object:objects,depiction_kind:unknown.has(kind)?[]:[kind],transparent:file.transparency?.has_transparent_pixels!=null ? [String(file.transparency.has_transparent_pixels)] : file.has_transparency!=null ? [String(file.has_transparency)] : []};
      const selectedFile=Object.fromEntries(['sha256','format','structure','url','archive','preview','validation','width','height','size_bytes','dimensions','transparency','scientific','scientific_review_evidence','scientific_conflicts','public_scope','text_content'].filter(k=>file[k]!=null).map(k=>[k,file[k]]));
      selectedFile.appearance=appearance;
      selectedFile.content_type=contentType;
      const lexical={title:indexTerms(title),context:indexTerms(contextText)};
      lexical.all=unique([...lexical.title,...lexical.context,...aliases.map(termKey)]).sort(cmp);
      const row={variant_id:[asset.id,digest,origin.asset_id||origin.source_id||'',originIndex].join(':'),asset_id:asset.id,family_id:asset.family_id||asset.id,title:fields.title||asset.title,source:origin.source||asset.source,source_id:origin.source_id||asset.source_id,author:origin.author||fields.author,attribution:origin.attribution||fields.attribution,values,eligible,facts,primary_object:{terms:objects,depiction_kind:kind,basis:manualEvidence(file,'object').length?'manual_visual_review':declaredObjects!=null&&objects.length?'explicit_source_field':objects.length?(descriptionLabel?'explicit_source_depiction_statement':'direct_source_title_compound_label'):'unknown',evidence:descriptionLabel||fields.title||null,manual_evidence_indices:manualEvidence(file,'object'),whole_body_verified:manualEvidence(file,'role').length>0&&facts.role.includes('whole_body')},fact_basis:factBasis,search:{title,aliases:aliases.join(' '),context:contextText},lexical,file:selectedFile,validation:asset.validation||{}};
      row.rotation_key=Math.floor(unitHash(row.family_id)*2147483647);
      row.features={signature:[...signature(row)].sort(cmp)};
      result.push(row);
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
  for(const [key,values] of Object.entries(plan.requirements)) {
    const groups=key==='object'&&plan.object_groups?.length?plan.object_groups:null;
    conditions.push({code:`hard_filter:${key}`,sql:groups?groups.map(group=>`EXISTS (SELECT 1 FROM json_each(v.values_json,'$.object') j WHERE j.value IN (${group.map(()=>'?').join(',')}))`).join(' AND '):plan.all_requirements?.[key]?`(SELECT COUNT(DISTINCT j.value) FROM json_each(v.values_json, '$.${key}') j WHERE j.value IN (${values.map(()=>'?').join(',')}))=${values.length}`:`EXISTS (SELECT 1 FROM json_each(v.values_json, '$.${key}') j WHERE j.value IN (${values.map(()=>'?').join(',')}))`,params:groups?groups.flat():values,
      unknown_sql:['species','state','view','role','object','depiction_kind','color_mode','palette_traits','representation','detail','realism','series','structure','content_type'].includes(key)?`json_array_length(v.values_json,'$.${key}')=0`:null});
  }
  return {from,text_clauses:textClauses,text_params:textParams,conditions,where:[...textClauses,...conditions.map(c=>c.sql)].join(' AND ')||'1',params:[...textParams,...conditions.flatMap(c=>c.params)]};
}

function termMatch(value,term) { return matches(value,term); }
function lexicalScore(variant,plan) {
  const fields=variant.lexical;
  let titleHits=0,contextHits=0;
  for(const group of plan.terms) {
    if(group.some(term=>fields.title.includes(termKey(term)))) titleHits++;
    else if(group.some(term=>fields.context.includes(termKey(term)))) contextHits++;
    else if(!group.some(term=>fields.all.includes(termKey(term)))) return null;
  }
  return plan.terms.length ? 1 + (titleHits + contextHits*0.4)/plan.terms.length : 1;
}
export function filterVariant(variant,plan) {
  if(!variant.eligible) return {matched:false,reason:'unavailable_or_rights_unresolved'};
  for(const [key,expected] of Object.entries(plan.requirements)) {
    const actual=variant.values[key]||[];
    if(!actual.length) return {matched:false,reason:`unknown:${key}`};
    if(key==='object'&&plan.object_groups?.length&&!plan.object_groups.every(group=>group.some(value=>actual.includes(value))))return {matched:false,reason:'hard_filter:object'};
    if(!(plan.all_requirements?.[key]?expected.every(v=>actual.includes(v)):expected.some(v=>actual.includes(v)))) return {matched:false,reason:`hard_filter:${key}`};
  }
  const relevance=lexicalScore(variant,plan);
  return relevance==null ? {matched:false,reason:'object_terms'} : {matched:true,relevance};
}
function preferenceScore(variant,plan) {
  let count=0;
  for(const [key,values] of Object.entries(plan.preferences)) if(values.some(value=>(variant.values[key]||[]).includes(value))) count++;
  for(const group of plan.soft_terms) if(group.some(term=>variant.lexical.all.includes(termKey(term)))) count++;
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
function rotationParameters(seed){return {step:1+Math.floor(unitHash(`${seed}:step`)*32766),offset:Math.floor(unitHash(`${seed}:offset`)*2147483647)};}
function rotation(key,seed){const {step,offset}=rotationParameters(seed);return ((key*step+offset)%2147483647)/2147483647*0.075;}
function signature(row) {
  return new Set([...Object.entries(row.facts).flatMap(([key,values])=>values.map(v=>`${key}:${v}`)),...row.values.representation.map(v=>`style:${v}`),...row.values.series.map(v=>`series:${v}`),...row.search.title.split(/\s+/).filter(v=>v.length>2)]);
}
function similarity(a,b) {
  const sa=a._signature,sb=b._signature;
  let intersect=0;for(const value of sa)if(sb.has(value))intersect++;
  return sa.size+sb.size-intersect ? intersect/(sa.size+sb.size-intersect) : 0;
}
function rankCompactWindow(rows,mode,needed=rows.length) {
  if(mode==='exact')return rows.slice(0,needed);
  const size=rows.length,matrix=new Float64Array(size*size).fill(NaN);
  const window=rows.map((row,index)=>({...row,_index:index,_signature:row._signature||new Set(row.features.signature)}));
  const chosen=[];
  const cachedSimilarity=(a,b)=>{
    const key=a._index*size+b._index;
    if(Number.isNaN(matrix[key])){const value=similarity(a,b);matrix[key]=value;matrix[b._index*size+a._index]=value;}
    return matrix[key];
  };
  while(window.length&&chosen.length<needed){
    let best=0,bestScore=-Infinity;
    for(let i=0;i<window.length;i++){
      const row=window[i];let redundancy=0;
      for(let j=Math.max(0,chosen.length-10);j<chosen.length;j++)redundancy=Math.max(redundancy,cachedSimilarity(row,chosen[j]));
      const score=row._score+row._rotation-(mode==='explore'?0.075:0.03)*redundancy+(row.pinned?1000:0);
      if(score>bestScore||score===bestScore&&cmp(row.family_id,window[best].family_id)<0){best=i;bestScore=score;}
    }
    chosen.push(window.splice(best,1)[0]);
  }
  return chosen;
}
export function rankCandidates(candidates,{mode='explore',seed='default',history={},candidate_pool=DEFAULT_CANDIDATE_POOL}={}) {
  if(!['explore','design','exact'].includes(mode))throw new Error('mode must be explore, design or exact');
  if(!Number.isInteger(candidate_pool)||candidate_pool<50||candidate_pool>100)throw new Error('candidate_pool must be 50..100');
  const adopted=history.adopted||[];
  const prepared=candidates.map(row=>{
    // Browsing and trial downloads never become adoption penalties.
    const uses=adopted.filter(use=>use.family_id===row.family_id && use.status==='adopted').length;
    const score=row.relevance+row.preference_score-(mode==='exact'?0:Math.min(uses,3)*0.035);
    return {...row,_signature:new Set(row.features.signature),_score:score,_rotation:mode==='exact'?0:rotation(row.rotation_key,seed)};
  }).sort((a,b)=>Number(b.pinned)-Number(a.pinned)||b._score-a._score||b._rotation-a._rotation||cmp(a.family_id,b.family_id));
  if(mode==='exact')return prepared;
  const ranked=[];
  // Bound each MMR working set; later windows are retained for real pagination.
  // Grouping before windows prevents format/color variants consuming the pool.
  while(prepared.length) {
    ranked.push(...rankCompactWindow(prepared.splice(0,candidate_pool),mode));
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

async function requestState(request={}) {
  const {query='',filters={},limit=10,mode='explore',seed='default',history={},project={},catalog_version,cursor=null,candidate_pool=DEFAULT_CANDIDATE_POOL}=request;
  if(!catalog_version)throw new Error('catalog_version is required for reproducible search');
  if(!Number.isInteger(limit)||limit<1||limit>100)throw new Error('limit must be between 1 and 100');
  if(!['explore','design','exact'].includes(mode))throw new Error('mode must be explore, design or exact');
  if(!Number.isInteger(candidate_pool)||candidate_pool<50||candidate_pool>100)throw new Error('candidate_pool must be 50..100');
  const plan=buildQueryPlan(query,filters,request);
  const bindings={catalog_version,ranking_version:RANKING_VERSION,query_plan:plan,mode,seed:String(seed),history_snapshot:await sha(history),project_snapshot:await sha(project),candidate_pool};
  const fingerprint=await sha(bindings);
  let offset=0;
  if(cursor){const decoded=decodeCursor(cursor);if(decoded.fingerprint!==fingerprint)throw new Error('Cursor inputs/version/history do not match');offset=decoded.offset;if(!Number.isSafeInteger(offset)||offset<0)throw new Error('Invalid cursor offset');}
  return {plan,bindings,fingerprint,offset,limit,mode,seed,history,project,candidate_pool};
}

/** Generate the complete SQL policy: exact terms and hard predicates apply to
 * every variant before ROW_NUMBER chooses a family representative and before
 * LIMIT takes a compact candidate window. Adapters only execute this plan.
 */
export async function prepareWindowSearch(request={}) {
  const state=await requestState(request),{plan,mode,seed,history,project,offset,limit,candidate_pool}=state;
  const params=[],param=value=>{params.push(value);return `?${params.length}`;};
  const member=(column,values)=>`EXISTS (SELECT 1 FROM json_each(${column}) term JOIN json_each(${param(JSON.stringify(unique(values)))}) wanted ON term.value=wanted.value)`;
  const titleHits=plan.terms.map(group=>`CASE WHEN ${member('v.title_terms',group.map(termKey))} THEN 1 ELSE 0 END`);
  const contextHits=plan.terms.map(group=>`CASE WHEN ${member('v.title_terms',group.map(termKey))} THEN 0 WHEN ${member('v.context_terms',group.map(termKey))} THEN 1 ELSE 0 END`);
  const relevance=plan.terms.length?`(1.0 + ((${titleHits.join('+')}) + 0.4*(${contextHits.join('+')}))/${plan.terms.length})`:'1.0';
  const preferred=[...Object.entries(plan.preferences).map(([key,values])=>member(`json_extract(v.values_json,'$.${key}')`,values)),...plan.soft_terms.map(group=>member('v.all_terms',group.map(termKey)))];
  const preference=preferred.length?`(0.06*MIN(4,${preferred.map(sql=>`CASE WHEN ${sql} THEN 1 ELSE 0 END`).join('+')}))`:'0.0';
  const pins=param(JSON.stringify(project.selected||[]));
  const pinned=`EXISTS (SELECT 1 FROM json_each(${pins}) pin WHERE json_extract(pin.value,'$.family_id')=v.family_id AND json_extract(pin.value,'$.file_sha256')=v.file_sha256 AND (json_extract(pin.value,'$.asset_id') IS NULL OR EXISTS (SELECT 1 FROM json_each(v.values_json,'$.asset_id') aid WHERE aid.value=json_extract(pin.value,'$.asset_id'))))`;
  const conditions=['v.eligible=1'];
  if(plan.empty)conditions.push('0');
  const from=plan.fts?'file_variants v JOIN file_variants_fts f ON f.variant_id=v.variant_id':'file_variants v';
  if(plan.fts)conditions.push(`file_variants_fts MATCH ${param(plan.fts)}`);
  for(const [key,values] of Object.entries(plan.requirements)) {
    if(key==='object'&&plan.object_groups?.length)for(const group of plan.object_groups)conditions.push(member("json_extract(v.values_json,'$.object')",group));
    else if(plan.all_requirements?.[key])for(const value of values)conditions.push(member(`json_extract(v.values_json,'$.${key}')`,[value]));
    else conditions.push(member(`json_extract(v.values_json,'$.${key}')`,values));
  }
  for(const group of plan.terms)conditions.push(member('v.all_terms',group.map(termKey)));
  const adopted=param(JSON.stringify(history.adopted||[]));
  const historyPenalty=mode==='exact'?'0.0':`0.035*MIN(3,(SELECT count(*) FROM json_each(${adopted}) used WHERE json_extract(used.value,'$.family_id')=r.family_id AND json_extract(used.value,'$.status')='adopted'))`;
  const rotate=rotationParameters(seed);
  const rotationSQL=mode==='exact'?'0.0':`(((r.rotation_key*${rotate.step}+${rotate.offset})%2147483647)/2147483647.0*0.075)`;
  const cte=`WITH qualified AS (SELECT v.variant_id,v.asset_id,v.family_id,v.file_sha256,v.features,v.rotation_key,v.values_json,${relevance} AS relevance,${preference} AS preference_score,${pinned} AS pinned FROM ${from} WHERE ${conditions.join(' AND ')}), representatives AS (SELECT *,ROW_NUMBER() OVER (PARTITION BY family_id ORDER BY pinned DESC,(relevance+preference_score) DESC,variant_id) AS representative FROM qualified), candidates AS (SELECT r.*,(r.relevance+r.preference_score-${historyPenalty}) AS selection_score,${rotationSQL} AS rotation FROM representatives r WHERE r.representative=1)`;
  const windowStart=Math.floor(offset/candidate_pool)*candidate_pool;
  const windowCount=Math.ceil((offset-windowStart+limit)/candidate_pool);
  const order='pinned DESC,selection_score DESC,rotation DESC,family_id';
  // Keep even unused exact-mode parameters referenced; numeric placeholders
  // must have the same bindings for counts and window queries in SQLite/D1.
  const parameterAnchor=` AND ${adopted} IS NOT NULL`;
  return {state:{...state,window_start:windowStart,window_count:windowCount},sql:{params,
    counts:`${cte} SELECT (SELECT count(*) FROM qualified) AS qualified_variants,count(*) AS qualified_families FROM candidates WHERE 1${parameterAnchor}`,
    pins:`${cte} SELECT pin.value,EXISTS(SELECT 1 FROM qualified q WHERE q.family_id=json_extract(pin.value,'$.family_id') AND q.file_sha256=json_extract(pin.value,'$.file_sha256') AND (json_extract(pin.value,'$.asset_id') IS NULL OR EXISTS(SELECT 1 FROM json_each(q.values_json,'$.asset_id') aid WHERE aid.value=json_extract(pin.value,'$.asset_id')))) FROM json_each(${pins}) pin WHERE 1${parameterAnchor}`,
    window:`${cte} SELECT variant_id,asset_id,family_id,file_sha256,features,relevance,preference_score,pinned,selection_score,rotation FROM candidates WHERE 1${parameterAnchor} ORDER BY ${order} LIMIT ${windowCount*candidate_pool} OFFSET ${windowStart}`},diagnostic_sql:sqlitePlan(plan)};
}

/** Small portable ranking kernel. SQL already applied every hard constraint.
 * It consumes only compact references/similarity features, never family files.
 * Windows are independent: cursor offsets reproduce them without giant history.
 */
export function finishWindowSearch(rows,counts,state) {
  const ranked=[];
  for(let start=0;start<rows.length;start+=state.candidate_pool){
    const window=rows.slice(start,start+state.candidate_pool).map(row=>({...row,_signature:new Set(row.features.signature),_score:row.selection_score,_rotation:row.rotation}));
    ranked.push(...rankCompactWindow(window,state.mode,Math.min(window.length,state.offset-state.window_start+state.limit-ranked.length)));
  }
  const selected=ranked.slice(state.offset-state.window_start,state.offset-state.window_start+state.limit).map(({_signature,_score,_rotation,_index,features,...row})=>row);
  return {selected,next_cursor:state.offset+selected.length<counts.qualified_families?encodeCursor({fingerprint:state.fingerprint,offset:state.offset+selected.length}):null,bindings:state.bindings,query_plan:state.plan,
    diagnostics:{qualified_variants:counts.qualified_variants,qualified_families:counts.qualified_families,candidate_pool:state.candidate_pool,rerank_windows:Math.ceil(counts.qualified_families/state.candidate_pool),compact_candidates_loaded:rows.length,window_start:state.window_start,scarcity:counts.qualified_families<state.limit,no_match:!selected.length,acceptance:'not_established_by_search'}};
}

export function materializeResult(row,variant) {
  return {asset_id:variant.asset_id,family_id:variant.family_id,variant_id:variant.variant_id,title:variant.title,selected_file:variant.file,selected_source:{source:variant.source,source_id:variant.source_id,attribution:variant.attribution},selected_science:{values:variant.facts,primary_object:variant.primary_object,basis:variant.fact_basis},preview:variant.file.preview,download:variant.file.archive||{url:variant.file.url},pinned:Boolean(row.pinned),search_score:row.selection_score??row._score,validation:variant.validation};
}

export async function searchCandidates(variants,request={}) {
  const {plan,bindings,fingerprint,offset,limit,mode,seed,history,project,candidate_pool}=await requestState(request);
  const counts={},uncertain=[],qualified=[];
  if(!plan.empty)for(const variant of variants){const match=filterVariant(variant,plan);if(match.matched)qualified.push({...variant,relevance:match.relevance});else{counts[match.reason]=(counts[match.reason]||0)+1;if(match.reason.startsWith('unknown:')&&uncertain.length<20)uncertain.push({variant_id:variant.variant_id,family_id:variant.family_id,reason:match.reason});}}
  const families=groupFamilies(qualified,plan,project);
  const ranked=rankCandidates(families,{mode,seed,history,candidate_pool});
  const results=ranked.slice(offset,offset+limit).map(row=>materializeResult(row,row));
  const unavailablePins=(project.selected||[]).filter(pin=>!qualified.some(row=>pinFor({selected:[pin]},row))).map(pin=>({...pin,reason:'selected file no longer satisfies this query, hard filters, availability or catalog'}));
  return {results,next_cursor:offset+limit<ranked.length?encodeCursor({fingerprint,offset:offset+limit}):null,bindings,query_plan:plan,diagnostics:{input_variants:variants.length,qualified_variants:qualified.length,qualified_families:families.length,candidate_pool,rerank_windows:Math.ceil(families.length/candidate_pool),rejected:counts,uncertain_candidates:uncertain,unavailable_project_pins:unavailablePins,scarcity:families.length<limit,no_match:!results.length,acceptance:'not_established_by_search',resource_note:'All hard constraints precede family grouping and pool truncation. Adapter can use sqlitePlan pushdown; wide-query latency must be measured.'}};
}
