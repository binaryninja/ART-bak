import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {stripTypeScriptTypes} from 'node:module';
import vm from 'node:vm';
import {ApiError,mutationMustWait,toolsVersionHeaders,isPythonProfile} from '../src/lib/mutation-contract.mjs';
async function httpWith(response){
 const source=await readFile(new URL('../src/lib/api.ts',import.meta.url),'utf8');
 const start=source.indexOf('export async function http<');
 const end=source.indexOf('\n// sseUrl',start);
 const code=stripTypeScriptTypes(source.slice(start,end).replace('export async','async'));
 const context=vm.createContext({ApiError,MOCK:false,getToken:()=>null,fetch:async()=>response});
 vm.runInContext(code,context);return context.http;
}
test('mutation unreadable success cannot unlock retry',async()=>{
 const http=await httpWith({status:200,ok:true,json:async()=>{throw new SyntaxError('truncated')}});
 await assert.rejects(http('/control',{method:'POST'}),error=>mutationMustWait(error));
});
test('mutation unreadable error cannot unlock retry',async()=>{
 const http=await httpWith({status:503,ok:false,json:async()=>{throw new SyntaxError('truncated')}});
 await assert.rejects(http('/control',{method:'POST'}),error=>mutationMustWait(error));
});
test('control accepted but unconfirmed status is unknown',async()=>{
 const http=await httpWith({status:202,ok:true,json:async()=>({state:'paused'})});
 await assert.rejects(http('/control',{method:'POST'},200),error=>mutationMustWait(error));
});
test('explicit profile and raw token preserved; absence never invented',()=>{
 for(const p of [undefined,null,'Python','go',''])assert.equal(isPythonProfile(p),false);
 assert.equal(isPythonProfile('python'),true);
 assert.throws(()=>toolsVersionHeaders(undefined,true));
 const token='0123456789abcdef'.repeat(4);
 assert.equal(toolsVersionHeaders(token,true)['X-Intercept-Tools-Digest'],token);
});
