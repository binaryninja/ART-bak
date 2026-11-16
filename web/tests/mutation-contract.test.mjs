import test from 'node:test';
import assert from 'node:assert/strict';
import { ApiError, isPythonProfile, mutationMustWait, ruleVersionHeaders, toolsVersionHeaders, confirmedIntentState } from '../src/lib/mutation-contract.mjs';

test('preserves precise rule version without conversion', () => {
  const value='2026-11-16T10:11:12.123456789+05:30';
  assert.deepEqual(ruleVersionHeaders(value),{'X-Rule-Updated-At':value});
  for(const bad of [undefined,null,1,'','2026-11-16']) assert.throws(()=>ruleVersionHeaders(bad));
});
test('opaque config digest only, missing older Go token stays absent',()=>{
  const value='a'.repeat(64);
  assert.deepEqual(toolsVersionHeaders(value),{'X-Intercept-Tools-Digest':value});
  assert.deepEqual(toolsVersionHeaders(undefined),{});
  for(const bad of [null,[],['Bash'],'B'.repeat(64),'a'.repeat(63)])assert.throws(()=>toolsVersionHeaders(bad));
});
test('preserves status and strict no-retry flags without unsafe payload copying',()=>{
  const error=new ApiError(409,{error:'Pending',pending:true,retry:false,unknown:'true',arbitrary:'not copied'},'fallback');
  assert.equal(error.status,409);assert.equal(error.message,'Pending');assert.equal(error.pending,true);assert.equal(error.unknown,false);assert.equal(error.retry,false);assert.equal(error.arbitrary,undefined);assert.equal(mutationMustWait(error),true);
  assert.equal(mutationMustWait(new ApiError(409,{error:'CAS changed'},'')),false);
  assert.equal(mutationMustWait(new ApiError(503,{retry:false},'')),true);
  assert.equal(mutationMustWait(new ApiError(0,{unknown:true,retry:false},'')),true);
});
test('only exact confirmed states update worker UI',()=>{
  assert.equal(confirmedIntentState({state:'paused'},'pause'),'paused');
  assert.equal(confirmedIntentState({state:'open'},'resume'),'open');
  for(const result of [{state:'running'},{state:'paused',pending:true},{state:'paused',unknown:true},{state:'paused',retry:false},null]){
    assert.throws(()=>confirmedIntentState(result,'pause'),e=>mutationMustWait(e));
  }
});
test('wire call sites retain CAS and block repeat control without an inspect endpoint', async()=>{
  const {readFile}=await import('node:fs/promises');
  const api=await readFile(new URL('../src/lib/api.ts',import.meta.url),'utf8');
  assert.match(api,/updateInterceptRule:[\s\S]*?headers: interceptVersion\(updatedAt\)/);
  assert.match(api,/deleteInterceptRule:[\s\S]*?headers: interceptVersion\(updatedAt\)/);
  assert.match(api,/toggleInterceptRule:[\s\S]*?headers: interceptVersion\(updatedAt\)/);
  assert.match(api,/headers: toolsVersionHeaders\(versionDigest, isPythonProfile/);
  const view=await readFile(new URL('../src/app/(main)/function/tasks/detail/_tabs/sessions-tab.tsx',import.meta.url),'utf8');
  assert.ok(view.indexOf('unconfirmedControls.has')<view.indexOf('const res = await api.controlIntent'));
  assert.ok(view.indexOf('confirmedIntentState(res, action)')<view.indexOf('patchIntentState(session.intent_id, "paused")'));
  assert.doesNotMatch(view,/api\.inspectIntent/);
});

test('Python compatibility is explicit opt-in and requires opaque config token',()=>{
  assert.equal(isPythonProfile('python'),true);
  for(const value of [undefined,'','go','Python',true])assert.equal(isPythonProfile(value),false);
  assert.throws(()=>toolsVersionHeaders(undefined,true));
  assert.deepEqual(toolsVersionHeaders('a'.repeat(64),true),{'X-Intercept-Tools-Digest':'a'.repeat(64)});
});
