import test from 'node:test';
import assert from 'node:assert/strict';
import {validateRequest} from '../site/functions/v1/[[path]].js';
import {prepareWindowSearch} from '../mga_resources/search-core.mjs';

test('API rejects mismatched client catalog and unsafe/unbounded request fields',()=>{
  for(const input of [null,[],{query:''},{query:'a'.repeat(513)},{query:'mouse',limit:100},{query:'mouse',filters:[]},{query:'mouse',catalog_version:'old'}])assert.throws(()=>validateRequest(input));
});
test('API forwards full scientific constraints and stable project identity to the accepted core',async()=>{
  const input={query:'mouse',filters:{format:'svg',color_mode:'black_white'},mode:'exact',seed:'verified-project',project:{selected:[]},history:{adopted:[]},limit:5};
  const request=validateRequest(input);assert.deepEqual(request.filters,input.filters);assert.equal(request.seed,input.seed);
  const plan=await prepareWindowSearch(request);assert.equal(plan.state.limit,5);assert.equal(plan.state.mode,'exact');assert.deepEqual(plan.state.plan.requirements.color_mode,['black_white']);
  await assert.rejects(prepareWindowSearch({...request,cursor:'not-a-real-cursor'}));
});
