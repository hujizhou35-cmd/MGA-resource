"""Cloud-only deployment export; accepted search values and artwork are immutable."""
import hashlib, json, os, shutil, sqlite3, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from assemble import gh, list_release_assets
from replay_catalog_metadata import digest, download_metadata, read, write, require
from build_publish_pack import plan_manifest

ROOT=Path(__file__).resolve().parents[1]
REPO='hujizhou35-cmd/MGA-resource'
TAG='acceptance-20261107'
PREFIX='assembly-43480608326-1'
ORIGIN='https://mga-resource-mainly-for-agent.pages.dev'
FILE_TAG='website-files-v1'

def compact(value): return json.dumps(value,ensure_ascii=False,separators=(',',':'))
def sqlvalue(value):
    if value is None: return 'NULL'
    if isinstance(value,(int,float)): return str(value)
    return "'"+str(value).replace("'","''")+"'"

def cloud_asset(name,tag,expected,out):
    assets=list_release_assets(REPO,tag); a=assets[name]
    require(a['digest']=='sha256:'+expected,'Cloud asset pin mismatch: '+name)
    out.mkdir(parents=True,exist_ok=True); path=out/name
    if not path.exists(): gh('release','download',tag,'--repo',REPO,'--pattern',name,'--dir',str(out))
    require(digest(path)==expected and path.stat().st_size==a['size'],'Downloaded bytes mismatch')
    return path

def object_url(obj):
    name=obj['sha256']+'.'+obj['format']
    if obj['size_bytes']>=25*1024*1024:
        return f'https://github.com/{REPO}/releases/download/{FILE_TAG}/{name}'
    shard=int(obj['sha256'][:8],16)%3
    return f'https://mga-resource-files-{shard}.pages.dev/objects/{name}'

def main():
    require(os.environ.get('GITHUB_ACTIONS')=='true','Website catalog export is cloud-only')
    out=ROOT/'build/website'; incoming=out/'input'; public=out/'public'
    require(not public.exists(),'Refusing overwrite of deployment export')
    public.mkdir(parents=True)
    formal=read(ROOT/'data/acceptance-reports/component-43481252923.json')
    require(formal['website_gate']=='open_for_implementation' and not formal['missing'],'Formal component gate closed')
    binding=formal['current_material_validation']['bindings']
    require(digest(ROOT/'mga_resources/search-core.mjs')==binding['search_core_sha256'],'Core differs from formal report')
    assets=list_release_assets(REPO,TAG)
    index,_=download_metadata(PREFIX+'.catalog.sqlite',assets[PREFIX+'.catalog.sqlite'],incoming,REPO,TAG)
    require(digest(index)==binding['sqlite_sha256'],'Index differs from acceptance')
    manifest=read(cloud_asset('science-update-43479028418.publish_manifest.json',TAG,binding['publish_manifest_sha256'],incoming))
    bundle=read(cloud_asset('qa-20261107-42289434868-3.4.3.bundle.json','publish-qa-20261107-42289434868-3.4.3','54b055c631c5fd814a7012f176151eb86d65c66849f8b2c71ea3a53ae44dc3a0',incoming))
    objects,_=plan_manifest(manifest)
    packed={m['sha256']:m for p in bundle['packs'] for m in p['members']}
    require(set(objects)==set(packed),'Accepted files differ from verified cloud bundle')
    require(all(all(packed[k][f]==v[f] for f in ('size_bytes','format')) for k,v in objects.items()),'Object identities differ')
    published={f['sha256']:f for f in manifest['files']}
    db=sqlite3.connect(index);db.row_factory=sqlite3.Row
    metadata=dict(db.execute('SELECT key,value FROM index_metadata').fetchall())
    require(metadata['catalog_version']==binding['catalog_sha256'] and metadata['ranking_version']=='shared-search-3.5.3','Metadata binding differs')
    statements=["CREATE TABLE index_metadata (key TEXT PRIMARY KEY,value TEXT NOT NULL) WITHOUT ROWID;",
        "CREATE TABLE file_variants (variant_id TEXT PRIMARY KEY,asset_id TEXT NOT NULL,family_id TEXT NOT NULL,file_sha256 TEXT,eligible INTEGER NOT NULL,values_json TEXT NOT NULL,metadata TEXT NOT NULL,title_terms TEXT NOT NULL,context_terms TEXT NOT NULL,all_terms TEXT NOT NULL,rotation_key INTEGER NOT NULL,features TEXT NOT NULL) WITHOUT ROWID;",
        "CREATE VIRTUAL TABLE file_variants_fts USING fts5(variant_id UNINDEXED,title,aliases,context,tokenize='porter unicode61');"]
    for key,value in metadata.items():statements.append('INSERT INTO index_metadata VALUES ('+sqlvalue(key)+','+sqlvalue(value)+');')
    count=0;max_statement=0;max_metadata=0
    fts_rows={row['variant_id']:tuple(row) for row in db.execute('SELECT variant_id,title,aliases,context FROM file_variants_fts')}
    for row in db.execute('SELECT * FROM file_variants ORDER BY variant_id'):
        values=list(row);variant=json.loads(row['metadata']);file=published.get(row['file_sha256'])
        if not file:
            require(row['eligible']==0,'Eligible variant absent from accepted manifest')
            continue
        original=objects[file['sha256']];preview=objects[file['preview']['sha256']]
        variant['_delivery']={'original_url':object_url(original),'preview_url':object_url(preview),
            'metadata_url':ORIGIN+'/catalog/'+row['asset_id']+'.json',
            'provenance':file['provenance'],'text_content':file.get('text_content'),
            'file_sha256':file['sha256'],'preview_sha256':preview['sha256'],'size_bytes':file['size_bytes']}
        values[6]=compact(variant)
        statement='INSERT INTO file_variants VALUES ('+','.join(map(sqlvalue,values))+');'
        max_statement=max(max_statement,len(statement.encode()));max_metadata=max(max_metadata,len(values[6].encode()))
        statements.append(statement)
        fts=fts_rows.get(row['variant_id'])
        require(fts is not None,'Missing FTS source')
        statements.append('INSERT INTO file_variants_fts VALUES ('+','.join(map(sqlvalue,fts))+');')
        count+=1
    require(max_statement<100000,'D1 statement limit exceeded; export requires metadata sharding, not truncation')
    catalog_dir=public/'catalog';catalog_dir.mkdir()
    families=0
    for row in db.execute('SELECT id,metadata FROM assets ORDER BY id'):
        asset=json.loads(row['metadata']);asset['files']=[f for f in asset['files'] if f['sha256'] in published]
        if not asset['files']:continue
        for file in asset['files']:
            file['download_url']=object_url(objects[file['sha256']])
            file['preview_url']=object_url(objects[file['preview']['sha256']])
            file['provenance']=published[file['sha256']]['provenance']
        (catalog_dir/(row['id']+'.json')).write_text(compact(asset)+'\n',encoding='utf8')
        families+=1
    # One export, tested in fresh SQLite. No reranking or benchmark rerun here.
    sql_path=out/'search.sql';sql_path.write_text('\n'.join(statements)+'\n',encoding='utf8')
    clean=sqlite3.connect(out/'search.sqlite');clean.executescript(sql_path.read_text(encoding='utf8'))
    require(clean.execute('PRAGMA quick_check').fetchone()[0]=='ok','Deployment DB integrity failed')
    require(clean.execute('SELECT count(*) FROM file_variants').fetchone()[0]==count,'Variant count differs')
    clean.close()
    shards=[sum(1 for v in objects.values() if v['size_bytes']<25*1024*1024 and int(v['sha256'][:8],16)%3==i) for i in range(3)]
    require(max(shards)<19000 and families<19000,'Static shard exceeds free file budget')
    require((out/'search.sqlite').stat().st_size<450*1024*1024,'Deployment DB too large for free plan')
    receipt={'schema_version':1,'run_id':os.environ['GITHUB_RUN_ID'],'accepted_bindings':binding,
        'formal_report_sha256':digest(ROOT/'data/acceptance-reports/component-43481252923.json'),
        'variant_rows':count,'family_metadata_files':families,'objects':len(objects),'static_shard_counts':shards,
        'large_direct_files':[v for v in objects.values() if v['size_bytes']>=25*1024*1024],
        'sql_sha256':digest(sql_path),'sql_bytes':sql_path.stat().st_size,'database_bytes':(out/'search.sqlite').stat().st_size,
        'max_statement_bytes':max_statement,'max_variant_metadata_bytes':max_metadata,
        'ranking_values_unchanged':True,'artwork_reads':0,'deployed':False}
    write(out/'prepare-receipt.json',receipt)
    write(public/'version.json',{'name':'MGA-resource','purpose':'mainly-for-agent','catalog_sha256':binding['catalog_sha256'],
        'search_version':metadata['ranking_version'],'originals':len(published),'families':families,'component_acceptance':{'passed':True,'score':'120/120'},'historical_original120':'102/120'})
    write(out/'delivery-objects.json',objects)
    shutil.copyfile(ROOT/'data/acceptance-reports/component-43481252923.json',public/'acceptance.json')
    print(compact(receipt))

if __name__=='__main__':main()
