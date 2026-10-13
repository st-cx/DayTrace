const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function page() {
  const nodes = new Map();
  const html = fs.readFileSync(path.join(__dirname, '../web/index.html'), 'utf8');
  for (const [, id] of html.matchAll(/id="([^"]+)"/g)) {
    const classes = new Set();
    nodes.set(id, {id, textContent:'', value:'', checked:false, disabled:false,
      classList:{toggle(name, value){if(value)classes.add(name);else classes.delete(name);},
        contains(name){return classes.has(name);},add(name){classes.add(name);},remove(name){classes.delete(name);}},
      querySelector(){return null;}, querySelectorAll(){return [];}, focus(){}, reset(){}});
  }
  const document = {getElementById:id=>nodes.get(id), querySelectorAll:()=>[], addEventListener(){}};
  const context = vm.createContext({document, Date, console, setTimeout(){}, setInterval(){}, window:{scrollTo(){}}, URL, navigator:{}});
  const source = fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8').replace(/^init\(\)\.catch.*$/m, '');
  vm.runInContext(source, context);
  const calls=[];
  let state={running:false,entry:null,elapsed:0,focus_remaining:null,last_reason:'app_stopped',
    recovery:{recovery_id:'synthetic-recovery-key',entry:{id:1,start:100,end:110,note:'整理资料 <img>',category:'工作',project:'课程',tags:['备考'],billable:true,hourly_rate:360,focus_minutes:25},stopped_at:110,reason:'app_stopped'}};
  context.fakeRequest=async (url, method='GET', data)=>{
    calls.push({url,method,data});
    if(url==='/api/timer/resume')state={running:true,entry:{...state.recovery.entry,id:2,start:100000,end:100000},elapsed:0,focus_remaining:1500,recovery:null,last_reason:''};
    if(url==='/api/timer/dismiss-recovery')state={running:false,entry:null,elapsed:0,focus_remaining:null,recovery:null,last_reason:'recovery_dismissed'};
    return state;
  };
  vm.runInContext('request=fakeRequest;refreshCurrent=async()=>{};toast=()=>{};',context);
  return {nodes, calls, context, async load(){await vm.runInContext('loadTimer()',context);}, wire(){vm.runInContext('wire()',context);}};
}

test('pending recovery shows task, preserves stopped display and offers explicit decisions', async()=>{
  const ui=page();
  assert.ok(ui.nodes.has('timer-recovery'), '页面需要可见的任务恢复选择');
  await ui.load();
  assert.equal(ui.nodes.get('timer-recovery').classList.contains('hidden'),false);
  assert.equal(ui.nodes.get('timer-start').classList.contains('hidden'),true);
  assert.equal(ui.nodes.get('timer-stop').classList.contains('hidden'),true);
  assert.equal(ui.nodes.get('timer-clock').textContent,'00:00:00');
  assert.match(ui.nodes.get('timer-recovery-description').textContent,/整理资料 <img>/);
  assert.match(ui.nodes.get('timer-recovery-description').textContent,/25/);
  ui.wire();
  await ui.nodes.get('timer-resume').onclick();
  const decision=ui.calls.find(call=>call.url==='/api/timer/resume');
  assert.equal(decision.data.recovery_id,'synthetic-recovery-key');
  assert.equal(ui.nodes.get('timer-recovery').classList.contains('hidden'),true);
  assert.equal(ui.nodes.get('timer-stop').classList.contains('hidden'),false);
});

test('discarding continuation sends the candidate id and restores normal start controls', async()=>{
  const ui=page();
  assert.ok(ui.nodes.has('timer-dismiss'), '页面需要保留工时的放弃继续按钮');
  await ui.load();ui.wire();
  await ui.nodes.get('timer-dismiss').onclick();
  const decision=ui.calls.find(call=>call.url==='/api/timer/dismiss-recovery');
  assert.equal(decision.data.recovery_id,'synthetic-recovery-key');
  assert.equal(ui.nodes.get('timer-start').classList.contains('hidden'),false);
  assert.equal(ui.nodes.get('timer-note').disabled,false);
});
