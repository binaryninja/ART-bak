/** Independent pure presentation tests; no React/app module imports. */
import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { assetSummaryDisplay, retestErrorDisplay } from './legacy-display-text.ts';

test('only the two exact schema provenance literals map', () => {
  const sql=readFileSync(new URL('../../../db/schema.sql',import.meta.url),'utf8');
  const expected=[['\\4efb\\52a1\\6267\\884c\\671f\\95f4\\81ea\\52a8\\5173\\8054','Automatically associated during task execution'],['\\7531\\5386\\53f2\\4efb\\52a1\\8d44\\4ea7\\5173\\8054\\8fc1\\79fb','Migrated from a historical task-asset association']];
  for(const [escaped,english] of expected){
    assert.ok(sql.includes("U&'"+escaped+"'"));
    const text=escaped.replace(/\\([0-9a-f]{4})/g,(_,code)=>String.fromCharCode(parseInt(code,16)));
    assert.equal(assetSummaryDisplay(text),english);
    for(const custom of [' '+text,text+'\n',text+' (custom)',text+'\u200b'])assert.equal(assetSummaryDisplay(custom),custom);
    assert.equal(retestErrorDisplay(text),text);
  }
});

test('three consumers render mapped strings without replacing raw records',()=>{
  const base=new URL('../',import.meta.url);
  const assets=readFileSync(new URL('app/(main)/function/tasks/detail/_tabs/assets-tab.tsx',base),'utf8');
  const sessions=readFileSync(new URL('app/(main)/function/tasks/detail/_tabs/sessions-tab.tsx',base),'utf8');
  const retest=readFileSync(new URL('components/finding-retest-panel.tsx',base),'utf8');
  assert.ok(assets.includes('firstText([assetSummaryDisplay(asset.task_source_summary ?? "")]'));
  assert.ok(sessions.includes('{assetSummaryDisplay(asset.source_summary)}'));
  assert.ok(retest.includes('{retestErrorDisplay(item.error)}'));
  for(const source of [assets,sessions,retest])assert.doesNotMatch(source,/(?:asset\.(?:task_source_summary|source_summary)|item\.error)\s*=(?!=)/);
});
