"""Bounded live controls and one replay of previously accepted query identities."""
import argparse
import hashlib
import json
import math
import os
import statistics
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from prepare_website import cloud_asset, TAG, ORIGIN
from verify_website_api import identity

OUT=Path("build/website")
WORKBENCH="workbench-20261114-1"
BASELINE=4_753_118

def request(origin,path,value=None):
    r=urllib.request.Request(origin+path,data=None if value is None else json.dumps(value,ensure_ascii=False).encode(),
                             headers={"Content-Type":"application/json","User-Agent":"MGA-workbench-validation/1"})
    try:
        with urllib.request.urlopen(r,timeout=35) as response:
            raw=response.read()
            return response.status, json.loads(raw) if "json" in response.headers.get("Content-Type","") else raw, response.headers
    except urllib.error.HTTPError as e:
        return e.code, {"error":str(e)}, e.headers

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--production",action="store_true")
    args=ap.parse_args()
    assert os.environ.get("GITHUB_ACTIONS")=="true"
    origin=ORIGIN if args.production else (OUT/"upgrade-url.txt").read_text().strip()
    assert origin.startswith("https://") and origin.endswith(".mga-resource-mainly-for-agent.pages.dev") or origin==ORIGIN
    info=json.loads((OUT/"public/catalog-info.json").read_text(encoding="utf-8"))
    successes=[];failures=[];reads=0;frozen_read=0;times=[];evidence=[]
    def check(label, ok):
        (successes if ok else failures).append(label)
    def req(path,data=None):
        nonlocal reads
        t=time.monotonic()
        code,body,h=request(origin,path,data)
        seconds=time.monotonic()-t
        spent=body.get("service",{}).get("d1_rows_read",0) if isinstance(body,dict) else 0
        reads+=spent
        evidence.append({"path":path,"request":data,"status":code,"seconds":seconds,"db_rows":spent,
                         "server_timing":h.get("Server-Timing"),"qualified_families":body.get("diagnostics",{}).get("qualified_families") if isinstance(body,dict) else None,
                         "results":[[x["family_id"],x["download"]["sha256"]] for x in body.get("results",[])] if isinstance(body,dict) else None})
        if reads>2_500_000:
            raise RuntimeError("Free replay budget reached")
        return code,body,seconds
    stop=None; parity=0; baseline_compatible=0
    try:
        c,health,_=req("/v1/health")
        check("health",c==200 and health.get("execution_version")==WORKBENCH)
        if failures:raise RuntimeError("Preview DB binding/health failed")
        c,live,_=req("/catalog-info.json")
        check("catalog_info",c==200 and live==info and len(info["categories"])==12 and len(info["sources"])==13)
        for path in ("/","/about","/sources","/agent","/llms.txt","/openapi.json","/agent-example.mjs"):
            c,_,_=req(path);check(path,c==200)
        if not args.production:
            bundle=cloud_asset("component-benchmark-43481252923-1.tar.gz",TAG,"60b0b4cfd0117f832d3c8ec58ea4f03c44d5e5543f7b2d2e0262da736595e017",OUT/"input")
            with tarfile.open(bundle) as tar:
                entries=[m for m in tar.getmembers() if m.name.endswith("/raw-component-results.json")]
                assert len(entries)==1
                raw=tar.extractfile(entries[0]).read()
            assert hashlib.sha256(raw).hexdigest()=="e4364869d611f2411740c7317241c1078201f3d10919276ddc90c0a995054988"
            snapshot_reads=reads
            for q in json.loads(raw)["queries"]:
                code,actual,seconds=req("/v1/search",q["request"])
                ok=code==200 and [identity(item) for item in actual["results"]]==[identity(item) for item in q["exact_results"]] and actual["bindings"]==q["search_bindings"] and actual["next_cursor"]==q["next_cursor"]
                parity+=int(ok)
                if code==200 and actual["service"]["cache"]=="miss":
                    times.append(seconds)
                    baseline_compatible+=1
                if not ok:
                    failures.append(q["query_id"])
                    if code!=200:raise RuntimeError("Parity query failed with service status "+str(code))
            frozen_read=reads-snapshot_reads
            for category in [*info["categories"],{"id":"unclassified","families":info["unclassified_families"]}]:
                data={"mode":"exact","limit":2,"response_profile":"compact"}
                data.update({"unclassified":True} if category["id"]=="unclassified" else {"filters":{"category":category["id"]}})
                code,a,_=req("/v1/browse",data)
                check("browse:"+category["id"],code==200 and a["diagnostics"]["qualified_families"]==category["families"] and len(a["results"])==min(2,category["families"]))
            a={"query":"mouse","seed":"workbench-parity-1","limit":6,"response_profile":"compact"}
            code,first,_=req("/v1/search",a)
            if code!=200:raise RuntimeError("Mouse control failed")
            code,again,_=req("/v1/search",a)
            check("stable_seed",code==200 and first["results"]==again["results"])
            check("cache_diagnostics",code==200 and (again["service"]["cache"]!="hit" or again["service"]["d1_rows_read"]==0))
            code,alternative,_=req("/v1/search",{**a,"seed":"workbench-parity-2"})
            check("new_seed",code==200 and [x["family_id"] for x in alternative["results"]]!=[x["family_id"] for x in first["results"]])
            code,page2,_=req("/v1/search",{**a,"cursor":first["next_cursor"]})
            check("no_duplicate_pages",code==200 and not {x["family_id"] for x in first["results"]}&{x["family_id"] for x in page2["results"]})
            pin=first["results"][-1]
            pinned={"family_id":pin["family_id"],"asset_id":pin["asset_id"],"file_sha256":pin["download"]["sha256"]}
            code,with_pin,_=req("/v1/search",{**a,"seed":"workbench-parity-2","project":{"selected":[pinned]},"history":{"adopted":[{"family_id":first["results"][0]["family_id"],"status":"adopted"}]}})
            check("pin_and_history",code==200 and any(x["pinned"] and x["download"]["sha256"]==pinned["file_sha256"] for x in with_pin["results"]))
            code,b,_=req("/v1/search",{"query":"小鼠","filters":{"color_mode":"black_white","format":"svg"},"response_profile":"compact","limit":3})
            check("bilingual_file_filters",code==200 and bool(b["results"]) and all(x["download"]["format"]=="svg" and x["selected_file"]["appearance"]["color"]["mode"]=="black_white" for x in b["results"]))
            code,b,_=req("/v1/search",{"query":"mga_no_object_zxyxxyxyz","limit":2})
            check("zero_is_not_unavailable",code==200 and b["results"]==[])
            code,_,_=req("/v1/search",{**a,"cursor":"stale"})
            check("invalid_cursor",code==400)
        else:
            code,page,_=req("/v1/search",{"query":"mouse","limit":3,"response_profile":"compact"})
            check("search",code==200 and len(page["results"])==3)
            code,page,_=req("/v1/browse",{"filters":{"category":"organs"},"limit":2})
            check("browse",code==200 and len(page["results"])==2)
    except Exception as e:
        stop=str(e)
        failures.append("execution_error")
    report={"commit":os.environ["GITHUB_SHA"],"run_id":os.environ["GITHUB_RUN_ID"],"endpoint":origin,
            "stage":"production" if args.production else "preview","parity_passed":parity,
            "control_passes":successes,"control_failures":failures,"stop":stop,"observed_d1_rows":reads,
            "frozen_requests_d1_rows":frozen_read,"previous_upper_bound_rows":BASELINE,
            "row_reduction":1-frozen_read/BASELINE if parity==142 else None,
            "page_cache_miss_count":baseline_compatible,
            "miss_p50_s":statistics.median(times) if times else None,
            "miss_p95_s":sorted(times)[math.ceil(len(times)*.95)-1] if times else None,
            "performance_goals_met":parity==142 and frozen_read<=BASELINE*.2 and bool(times) and sorted(times)[math.ceil(len(times)*.95)-1]<=1}
    (OUT/"upgrade-verification.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    (OUT/"upgrade-http.jsonl").write_text("".join(json.dumps(x,ensure_ascii=False)+"\n" for x in evidence),encoding="utf-8")
    print(json.dumps(report))
    if failures or not args.production and parity!=142:raise SystemExit("Upgrade parity or controls failed")

if __name__=="__main__":
    main()
