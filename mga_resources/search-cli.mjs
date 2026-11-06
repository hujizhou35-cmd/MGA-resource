// Thin process adapter only; all query/filter/ranking policy lives in the core.
import {readFileSync} from 'node:fs';
import {prepareVariants,buildQueryPlan,sqlitePlan,searchCandidates,prepareWindowSearch,finishWindowSearch,materializeResult,RANKING_VERSION} from './search-core.mjs';
try {
  const input=JSON.parse(readFileSync(0,'utf8'));
  let output;
  if(input.action==='prepare')output={variants:prepareVariants(input.records),ranking_version:RANKING_VERSION};
  else if(input.action==='plan'){const plan=buildQueryPlan(input.query,input.filters,input.options);output={plan,sql:sqlitePlan(plan)};}
  else if(input.action==='search'){const start=performance.now();output=await searchCandidates(input.variants,input.request);output.adapter_metrics={core_milliseconds:performance.now()-start};}
  else if(input.action==='window_plan')output=await prepareWindowSearch(input.request);
  else if(input.action==='window_finish'){
    const start=performance.now();output=finishWindowSearch(input.rows,input.counts,input.state);
    output.adapter_metrics={core_milliseconds:performance.now()-start,pure_ranking_kernel:true};
  }
  else if(input.action==='hydrate')output={results:input.selected.map(row=>materializeResult(row,input.variants.find(v=>v.variant_id===row.variant_id)))};
  else if(input.action==='window_profile'){
    const samples={query_plan:[],complete_plan:[],ranking_kernel:[]};
    for(let i=0;i<20;i++){
      let start=performance.now();buildQueryPlan(input.request.query,input.request.filters,input.request);samples.query_plan.push(performance.now()-start);
      start=performance.now();await prepareWindowSearch(input.request);samples.complete_plan.push(performance.now()-start);
      start=performance.now();finishWindowSearch(input.rows,input.counts,input.state);samples.ranking_kernel.push(performance.now()-start);
    }
    output={iterations:20,milliseconds:samples,note:'Local Node wall time including async binding hashes; not a Cloudflare CPU measurement'};
  }
  else throw new Error('Unknown action');
  process.stdout.write(JSON.stringify(output));
} catch(error) {process.stderr.write(String(error.message)+'\n');process.exitCode=2;}
