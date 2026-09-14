// Run: node tests/test_library_ui_race.cjs
// Exercises the shipped load/rate functions; fake DOM and deferred transport only.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '../savvy/library/index.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1].split("$('readyTab').onclick=")[0];
function context(source) {
  const elements = new Map();
  const c = vm.createContext({document:{getElementById(id){if(!elements.has(id))elements.set(id,{value:'',replaceChildren(){}});return elements.get(id)}}, Option:function(t,v){this.value=v;}, console});
  vm.runInContext(source, c);
  vm.runInContext(`applyFilters=()=>{};counts=()=>{};renderGrid=()=>{};updateActions=()=>{};
    state.items=[{id:'a',choice:'unreviewed',event:'Test'},{id:'b',choice:'unreviewed',event:'Test'}];state.id='a';
    const replies=[];request=(url,options)=>new Promise((resolve,reject)=>replies.push({url,options,resolve,reject}));`,c);
  return c;
}
async function completedSaveBeatsOldLoad(source) {
  const c=context(source);
  const load=vm.runInContext('load()',c);
  const save=vm.runInContext("rate('keep')",c);
  vm.runInContext('replies[1].resolve({})',c); await save;
  assert.equal(vm.runInContext("state.pending.size",c),0);
  vm.runInContext("replies[0].resolve({items:[{id:'a',choice:'unreviewed',event:'Test'},{id:'b',choice:'maybe',event:'Test'}]})",c);await load;
  assert.equal(vm.runInContext("state.items[0].choice",c),'keep','older library response must not erase completed Keep');
  assert.equal(vm.runInContext("state.items[1].choice",c),'maybe','unrelated library changes must still arrive');
  const fresh=vm.runInContext('load()',c);
  vm.runInContext("replies[2].resolve({items:[{id:'a',choice:'pass',event:'Test'}]})",c);await fresh;
  assert.equal(vm.runInContext('state.items[0].choice',c),'pass','later refresh can adopt a fresh server choice');
}
async function pendingAndFailedSave() {
  const c=context(script);
  const load=vm.runInContext('load()',c);
  const save=vm.runInContext("rate('keep')",c);
  vm.runInContext("replies[0].resolve({items:[{id:'a',choice:'pass',event:'Test'}]})",c);await load;
  assert.equal(vm.runInContext('state.items[0].choice',c),'unreviewed','pending save retains existing choice');
  vm.runInContext("replies[1].reject(new Error('Disconnected'))",c);await save;
  assert.equal(vm.runInContext('state.items[0].choice',c),'unreviewed','failed save must not claim Keep');
  assert.equal(vm.runInContext('state.writeRevision',c),0,'only successful writes advance revision');
}
(async()=>{
  await completedSaveBeatsOldLoad(script);
  await pendingAndFailedSave();
  const oldBehavior=script.replace("||(state.choiceRevisions.get(String(i.id))||0)>startedAtRevision",'');
  assert.notEqual(oldBehavior,script,'mutation must remove the revision guard');
  await assert.rejects(()=>completedSaveBeatsOldLoad(oldBehavior), /older library response/);
  console.log('PASS: saved Keep survives stale reload; pending/failure/fresh reload behavior holds; removing guard is caught.');
})().catch(e=>{console.error(e);process.exitCode=1});
