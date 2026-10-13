const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function extension({tab, focused = true, status = 200, browser = 'chrome.exe', enabled = true}) {
  const calls = [], settings = {enabled, token: 'synthetic-pairing-key', browser};
  const event = () => ({addListener(){}});
  const chrome = {
    storage: {local: {async get(){return settings;}, async set(data){Object.assign(settings,data);}}, onChanged: event()},
    windows: {async getLastFocused(){return {id:1, focused, incognito:!!tab.incognito};}, onFocusChanged:event()},
    tabs: {async query(){return [tab];}, onActivated:event(), onUpdated:event()},
    alarms: {async create(){}, onAlarm:event()},
    runtime: {onInstalled:event(), onStartup:event()},
    action: {async setBadgeText(){}, async setTitle(){}, async setBadgeBackgroundColor(){}},
  };
  const context = vm.createContext({chrome, URL, navigator:{userAgent:'Chromium'}, crypto:require('node:crypto').webcrypto,
    AbortSignal, console, setTimeout, clearTimeout,
    async fetch(url, options){calls.push({url, ...options, data:JSON.parse(options.body)});return {ok:status===200,status, async json(){return {error:'invalid token'};}};}});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../browser-extension/background.js'),'utf8'),context);
  return {calls,settings, async publish(){await vm.runInContext('publish()',context);}};
}

test('new tab explicitly clears old domain', async()=>{
  const app=extension({tab:{url:'chrome://newtab/'}});await app.publish();
  assert.equal(app.calls.length,1);assert.equal(app.calls[0].data.domain,'');
});
test('local page clears domain instead of keeping old site', async()=>{
  const app=extension({tab:{url:'http://127.0.0.1:41789/'}});await app.publish();
  assert.equal(app.calls.length,1);assert.equal(app.calls[0].data.domain,'');
});
test('incognito never sends hostname or path', async()=>{
  const app=extension({tab:{url:'https://private.example/secrets',incognito:true}});await app.publish();
  assert.equal(app.calls[0].data.domain,'');assert.equal(app.calls[0].data.private,true);
  assert.ok(!JSON.stringify(app.calls).includes('private.example'));
});
test('domain payload carries browser identity but no URL path',async()=>{
  const app=extension({tab:{url:'https://example.com/a?secret=123'},browser:'msedge.exe'});await app.publish();
  assert.equal(app.calls[0].data.browser,'msedge.exe');assert.equal(app.calls[0].data.domain,'example.com');
  assert.ok(!JSON.stringify(app.calls).includes('secret=123'));
});
test('authorization failure becomes visible status',async()=>{
  const app=extension({tab:{url:'https://example.com/'},status:403});await app.publish();
  assert.ok(app.settings.last_status?.includes('密钥') || app.settings.last_status?.includes('授权'));
});
test('disabling extension clears last domain',async()=>{
  const app=extension({tab:{url:'https://example.com/'},enabled:false});await app.publish();
  assert.equal(app.calls[0]?.data.domain,'');assert.equal(app.calls[0]?.data.focused,false);
});
