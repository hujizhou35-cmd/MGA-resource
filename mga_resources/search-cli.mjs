// Thin process adapter only; all query/filter/ranking policy lives in the core.
import {readFileSync} from 'node:fs';
import {prepareVariants,buildQueryPlan,sqlitePlan,searchCandidates,RANKING_VERSION} from './search-core.mjs';
try {
  const input=JSON.parse(readFileSync(0,'utf8'));
  let output;
  if(input.action==='prepare')output={variants:prepareVariants(input.records),ranking_version:RANKING_VERSION};
  else if(input.action==='plan'){const plan=buildQueryPlan(input.query,input.filters,input.options);output={plan,sql:sqlitePlan(plan)};}
  else if(input.action==='search'){const start=performance.now();output=await searchCandidates(input.variants,input.request);output.adapter_metrics={core_milliseconds:performance.now()-start};}
  else throw new Error('Unknown action');
  process.stdout.write(JSON.stringify(output));
} catch(error) {process.stderr.write(String(error.message)+'\n');process.exitCode=2;}
