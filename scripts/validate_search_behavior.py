"""Real-catalog behavior observations and explicitly separate adoption ledgers.

This tool reads metadata only. It never equates top rank, browsing, or a trial
download with adoption and never supplies visual judgments for artwork.
"""
from __future__ import annotations

import argparse
import copy
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from mga_resources.catalog import rebuild_search_index, search_page, search_legacy, _search_core


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    try:
        uri=path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        uri=path.name
    return {"uri":uri,"sha256":sha(path)}


def compact(page):
    return {"results":[{"family_id":r["family_id"],"asset_id":r["asset_id"],"variant_id":r["variant_id"],
        "title":r["title"],"source":r["selected_source"],"sha256":r["selected_file"]["sha256"],
        "preview":r["preview"],"download":r["download"],"appearance":r["selected_file"].get("appearance"),
        "science":r["selected_science"],"pinned":r["pinned"],"score":r["search_score"]} for r in page["results"]],
        "next_cursor":page["next_cursor"],"bindings":page["bindings"],"diagnostics":page["diagnostics"],"timings":page.get("adapter_metrics")}


def ids(page):
    return [r["family_id"] for r in page["results"]]


def signatures(page):
    return [(r["family_id"],r["selected_file"]["sha256"]) for r in page["results"]]


def attach_ledger(project, ledger, pool, catalog_sha256):
    """Return actual declared decisions, never infer them from rank or download."""
    result={**project,"trial_downloaded_family_ids":[],"adopted_family_ids":[]}
    if not ledger:
        return result
    if ledger.get("catalog_sha256")!=catalog_sha256 or ledger.get("exercise")!="validation_exercise":
        raise ValueError("Adoption ledger must bind this catalog and identify validation_exercise")
    entry=next((p for p in ledger.get("projects",[]) if p.get("project_id")==project["project_id"]),None)
    if not entry:
        return result
    decision=entry.get("decision") or {}
    has_text=lambda value:isinstance(value,str) and bool(value.strip())
    evidence=decision.get("reviewed_evidence")
    if not has_text(decision.get("reviewer")) or not has_text(decision.get("observations")) or not isinstance(evidence,list) or not evidence or not all(isinstance(row,dict) and has_text(row.get("uri")) and re.fullmatch(r"[0-9a-f]{64}",str(row.get("sha256",""))) for row in evidence):
        raise ValueError("Final adoption requires an identified decision with actual review evidence")
    for key in ("trial_downloaded_family_ids","adopted_family_ids"):
        values=entry.get(key,[])
        if not isinstance(values,list) or len(values)!=len(set(values)) or not set(values)<=set(pool):
            raise ValueError("Ledger family is outside the qualified pool or duplicated")
        result[key]=values
    result["decision"]=decision
    return result


def validate(catalog,assembly_report,output,ledger_path=None,rich_query="mouse",scarce_query="Naegleria gruberi",index_path=None):
    output.mkdir(parents=True,exist_ok=True)
    catalog_sha=sha(catalog)
    dbpath=index_path or output/"catalog.sqlite"
    if index_path:
        with closing(sqlite3.connect(Path(index_path).resolve().as_uri()+"?mode=ro",uri=True)) as index_db:
            if index_db.execute("SELECT value FROM index_metadata WHERE key='catalog_version'").fetchone()[0]!=catalog_sha:
                raise ValueError("Reused search index does not bind this exact catalog")
    else:
        rebuild_search_index(catalog,dbpath)
    assembly=json.loads(assembly_report.read_text(encoding="utf-8"))
    ledger=json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path else None
    db=sqlite3.connect(dbpath)
    eligible=[{"family":family,"hash":digest,"values":json.loads(values)} for family,digest,values in db.execute("SELECT family_id,file_sha256,values_json FROM file_variants WHERE eligible=1 ORDER BY family_id,variant_id")]
    families={}
    for row in eligible:
        families.setdefault(row["family"],[]).append(row)
    cases=[]
    def case(case_id,passed,inputs,observed,notes):
        evidence=write_json(output/"raw"/(case_id+".json"),{"input":inputs,"observed":observed,"notes":notes})
        cases.append({"id":case_id,"passed":bool(passed),"input":inputs,"observed":observed,"evidence":evidence,"notes":notes})
    multi=next(((family,rows) for family,rows in families.items() if len({m for r in rows for m in r["values"]["color_mode"]})>1),None)
    if multi:
        family,rows=multi
        modes=sorted({m for r in rows for m in r["values"]["color_mode"]})
        observations=[];passed=True
        for mode in modes:
            expected={r["hash"] for r in rows if mode in r["values"]["color_mode"]}
            page=search_page(dbpath,"",filters={"family_id":family,"color_mode":mode})
            passed &= len(page["results"])==1 and page["results"][0]["selected_file"]["sha256"] in expected
            passed &= all(r["preview"]==r["selected_file"].get("preview") and r["download"]==(r["selected_file"].get("archive") or {"url":r["selected_file"].get("url")}) for r in page["results"])
            observations.append({"mode":mode,"expected_hashes":sorted(expected),"page":compact(page)})
        case("variant_before_family",passed,{"family_id":family,"color_modes":modes},observations,"Real measured variants; confirms selected file/preview/download correspondence. Pixel color correctness is reviewed separately.")
    else:
        case("variant_before_family",False,{}, {"missing":"No real family with two known color modes"},"Pending real multi-color-family evidence.")
    colored=next((f for f,rows in families.items() if any("color" in r["values"]["color_mode"] for r in rows)),None)
    noncolored=next((f for f,rows in families.items() if all("color" not in r["values"]["color_mode"] for r in rows)),None)
    if colored and noncolored:
        scope=[colored,noncolored]
        hard=search_page(dbpath,"",filters={"family_id":scope,"color_mode":"color"})
        soft=search_page(dbpath,"",filters={"family_id":scope,"preferences":{"color_mode":"color"}})
        case("hard_vs_preference",ids(hard)==[colored] and set(ids(soft))==set(scope),{"families":scope,"required_or_preferred_color":"color"},{"hard":compact(hard),"soft":compact(soft)},"Uses two actual families; soft preference retains the nonmatching family while hard color removes it.")
    else:
        case("hard_vs_preference",False,{}, {"missing":"Known color/noncolor contrast absent"},"Pending a real comparison pair.")
    unknown=next((f for f,rows in families.items() if all(not r["values"]["state"] for r in rows)),None)
    if unknown:
        page=search_page(dbpath,"",filters={"family_id":unknown,"state":"normal"})
        case("unknown_is_not_match",not page["results"] and bool(page["diagnostics"]["uncertain_candidates"]),{"family_id":unknown,"state":"normal"},compact(page),"Actual source lacks a known scientific state; no normal-state assertion is manufactured.")
    else:
        case("unknown_is_not_match",False,{}, {"missing":"No unknown-state example"},"Pending real unknown example.")
    base=search_page(dbpath,rich_query,seed="behavior-a")
    repeated=search_page(dbpath,rich_query,seed="behavior-a")
    case("stable_replay",signatures(base)==signatures(repeated) and base["bindings"]==repeated["bindings"] and base["next_cursor"]==repeated["next_cursor"],{"query":rich_query,"seed":"behavior-a"},{"first":compact(base),"replay":compact(repeated)},"Replay compares selected hashes, order, versions and cursor; runtime measurements are excluded.")
    all_ids=[];pages=[];cursor=None
    while True:
        page=search_page(dbpath,rich_query,limit=100,seed="behavior-pages",cursor=cursor)
        all_ids.extend(ids(page));pages.append(compact(page));cursor=page["next_cursor"]
        if not cursor:break
    case("pagination_no_duplicate",len(all_ids)==len(set(all_ids))==pages[0]["diagnostics"]["qualified_families"],{"query":rich_query,"limit":100,"seed":"behavior-pages"},{"pages":pages,"returned_families":len(all_ids)},"Traversed the complete actual qualified pool, including fixed-window boundaries.")
    if base["results"]:
        chosen=base["results"][-1]
        pin={"asset_id":chosen["asset_id"],"family_id":chosen["family_id"],"file_sha256":chosen["selected_file"]["sha256"]}
        pinned=[search_page(dbpath,rich_query,mode="design",seed=s,project={"selected":[pin]}) for s in ("pin-a","pin-b")]
        case("pins_stable",all(p["results"] and p["results"][0]["selected_file"]["sha256"]==pin["file_sha256"] and p["results"][0]["pinned"] for p in pinned),{"query":rich_query,"controlled_pin":pin},{"projects":[compact(p) for p in pinned]},"Controlled replay test using a real file. This test pin is not recorded as final research adoption.")
        kwargs={"filters":{"family_id":pin["family_id"]},"seed":"history-check"}
        empty=search_page(dbpath,rich_query,**kwargs)
        history_row={"family_id":pin["family_id"],"status":"adopted"}
        browse=search_page(dbpath,rich_query,history={"browsed":[history_row],"trial_downloaded":[history_row]},**kwargs)
        adopt=search_page(dbpath,rich_query,history={"adopted":[history_row]},**kwargs)
        score=lambda p:p["results"][0]["search_score"]
        case("adopted_history_only",score(empty)==score(browse) and abs(score(empty)-score(adopt)-0.035)<1e-8,{"query":rich_query,"controlled_history_row":history_row},{"empty":compact(empty),"browse_trial":compact(browse),"adopted":compact(adopt)},"Controlled history-input experiment; browsing/trial does not affect score. This artificial history is separate from final decision ledgers.")
    for absent in ("pins_stable","adopted_history_only"):
        if not any(c["id"]==absent for c in cases):case(absent,False,{}, {"missing":"No candidate"},"Pending a usable actual pool.")
    modes=[search_page(dbpath,rich_query,filters={"species":"mouse"},mode=m,seed="mode-check") for m in ("explore","design","exact")]
    case("modes_scientific_constraints",all(p["results"] and all("mouse" in r["selected_science"]["values"]["species"] for r in p["results"]) for p in modes),{"query":rich_query,"species":"mouse","modes":["explore","design","exact"]},{"modes":[compact(p) for p in modes]},"Modes retain the same explicit species condition based on source facts; independent artwork relevance remains a separate review.")
    plan=_search_core({"action":"plan","query":rich_query})["sql"]
    variants=[json.loads(row[0]) for row in db.execute(f"SELECT v.metadata FROM {plan['from']} WHERE {plan['where']}",plan["params"])]
    request={"query":rich_query,"catalog_version":catalog_sha,"seed":"badge-invariance"}
    direct=_search_core({"action":"search","variants":variants,"request":request})
    changed=copy.deepcopy(variants)
    for row in changed:row["validation"]={"illustrator_editability":"verified","visual_review":"curated"};row["downloads"]=10000000
    counterfactual=_search_core({"action":"search","variants":changed,"request":request})
    select=lambda p:[(r["variant_id"],r["selected_file"]["sha256"],r["search_score"]) for r in p["results"]]
    case("no_curated_boost",select(direct)==select(counterfactual),{"query":rich_query,"mutation":"in-memory badges/popularity only; original catalog unchanged"},{"original":select(direct),"counterfactual":select(counterfactual)},"Metamorphic test on actual candidates; no whitelist or editability/popularity bonus is introduced.")
    scenarios=[];ledger_template={"schema_version":1,"catalog_sha256":catalog_sha,"exercise":"validation_exercise","projects":[]}
    for kind,query in (("rich",rich_query),("scarce",scarce_query)):
        pool=[];cursor=None
        while True:
            page=search_page(dbpath,query,limit=100,seed="pool",cursor=cursor)
            pool.extend(ids(page));cursor=page["next_cursor"]
            if not cursor:break
        projects=[];observations=[]
        for number in range(5):
            seed=f"validation-{kind}-{number+1}"
            page=search_page(dbpath,query,seed=seed)
            project={"project_id":seed,"seed":seed,"old_top_family_ids":[r["id"] for r in search_legacy(dbpath,query)],"new_top_family_ids":ids(page)}
            project=attach_ledger(project,ledger,pool,catalog_sha)
            if project.get("decision"):
                project["ledger_evidence"]=write_json(output/"ledgers"/(seed+".json"),{"catalog_sha256":catalog_sha,"exercise":"validation_exercise",**project})
            projects.append(project);observations.append({"project":project,"page":compact(page)})
            ledger_template["projects"].append({"project_id":seed,"case_id":kind,"trial_downloaded_family_ids":[],"adopted_family_ids":[],"decision":{"reviewer":None,"observations":None,"reviewed_evidence":[]}})
        raw=write_json(output/"raw"/(kind+"-projects.json"),{"query":query,"eligible_family_ids":pool,"projects":observations})
        note="Final validation-exercise decisions are separately identified and evidence-bound." if all(p["adopted_family_ids"] for p in projects) else "Adoption is pending until an identified reviewer supplies actual decisions."
        scenarios.append({"case_id":kind+"-"+query.replace(" ","-"),"pool_kind":kind,"eligible_family_ids":pool,"query":{"query":query},"review_notes":"Real catalog/source-qualified candidate pool; source labels are not independent visual acceptance. Validation exercise projects only. "+note+" No user research project adoption is claimed.","projects":projects,"evidence":raw})
    db.close()
    report={"schema_version":1,"bindings":{"catalog_sha256":catalog_sha,"benchmark_sha256":sha(ROOT/"data/benchmark.json"),"search_core_sha256":sha(ROOT/"mga_resources/search-core.mjs"),"ranking_version":base["bindings"]["ranking_version"],"analysis_version":assembly.get("appearance",{}).get("analysis_version")},"generated_at":datetime.now(timezone.utc).isoformat(),"reviewer":"Codex: automated real-catalog behavior observations; final adoption reviewer is recorded separately","functional_cases":cases,"diversity_scenarios":scenarios,"appearance_cases":[],"status":"pending_appearance_review_and_final_adoption_ledgers"}
    write_json(output/"supplemental-search-evidence.json",report)
    write_json(output/"adoption-ledger.to-review.json",ledger_template)
    print(json.dumps({"functional_cases":{c["id"]:c["passed"] for c in cases},"pools":{s["pool_kind"]:len(s["eligible_family_ids"]) for s in scenarios},"catalog_sha256":catalog_sha},ensure_ascii=False),flush=True)
    return report


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog",type=Path,required=True)
    parser.add_argument("--assembly-report",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--ledger",type=Path)
    parser.add_argument("--rich-query",default="mouse")
    parser.add_argument("--scarce-query",default="Naegleria gruberi")
    parser.add_argument("--index",type=Path,help="Reuse an existing index bound to this exact frozen catalog")
    args=parser.parse_args()
    validate(args.catalog,args.assembly_report,args.output,args.ledger,args.rich_query,args.scarce_query,args.index)
