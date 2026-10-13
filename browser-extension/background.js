'use strict';
const TARGET = 'http://127.0.0.1:41789/api/browser';
let publishing = false, queued = false;
function detectBrowser() {
  const ua = navigator.userAgent || '';
  return /Edg\//.test(ua) ? 'msedge.exe' : /OPR\//.test(ua) ? 'opera.exe' : /Firefox\//.test(ua) ? 'firefox.exe' : 'chrome.exe';
}
async function status(message, failed = false) {
  await chrome.storage.local.set({last_status: message});
  try {
    await chrome.action.setBadgeText({text: failed ? '!' : ''});
    await chrome.action.setBadgeBackgroundColor({color: '#b64040'});
    await chrome.action.setTitle({title: 'DayTrace · ' + message});
  } catch (_) {}
}
function makeEvent(win, tab, enabled, browser) {
  const focused = !!enabled && !!win?.focused;
  const privateWindow = !!win?.incognito || !!tab?.incognito;
  let domain = '';
  if (focused && !privateWindow && tab?.url) {
    try {
      const parsed = new URL(tab.url);
      if (['http:', 'https:'].includes(parsed.protocol) && !['localhost', '127.0.0.1', '[::1]'].includes(parsed.hostname)) domain = parsed.hostname.toLowerCase();
    } catch (_) {}
  }
  return {domain, browser, focused, private: privateWindow};
}
async function publishOnce() {
  const {enabled=false, token='', browser=detectBrowser()} = await chrome.storage.local.get(['enabled','token','browser']);
  if (!token) { await status('未连接：请填写扩展连接密钥', enabled); return; }
  try {
    const win = await chrome.windows.getLastFocused();
    const tabs = win?.focused ? await chrome.tabs.query({active:true, windowId:win.id}) : [];
    const event = makeEvent(win, tabs[0], enabled, browser);
    const response = await fetch(TARGET, {method:'POST', headers:{'Content-Type':'application/json','X-DayTrace-Key':token}, body:JSON.stringify(event), signal:AbortSignal.timeout(3000)});
    if (!response.ok) {
      await status(response.status===403 ? '未授权：检查 DayTrace 采集开关与连接密钥' : '本机服务返回错误，请检查状态', enabled);
      return;
    }
    await status(!enabled ? '已关闭域名记录' : event.private ? '隐身窗口不记录' : !event.focused ? '浏览器未聚焦，不记录网站' : event.domain ? '已连接，仅记录域名' : '当前页面不记录网站');
  } catch (_) { await status('未连接：请启动 DayTrace 后台服务', enabled); }
}
async function publish() {
  if (publishing) { queued=true; return; }
  publishing=true;
  try { do { queued=false; await publishOnce(); } while (queued); }
  finally { publishing=false; }
}
async function initialize(){ await chrome.alarms.create('daytrace-heartbeat',{periodInMinutes:.5}); await publish(); }
chrome.runtime.onInstalled.addListener(initialize);
chrome.runtime.onStartup.addListener(initialize);
chrome.alarms.onAlarm.addListener(a=>{if(a.name==='daytrace-heartbeat')publish();});
chrome.tabs.onActivated.addListener(publish);
chrome.tabs.onUpdated.addListener((_,change,tab)=>{if(tab.active&&(change.url||change.status==='complete'))publish();});
chrome.windows.onFocusChanged.addListener(publish);
chrome.storage.onChanged.addListener(changes=>{if(changes.enabled||changes.token||changes.browser)publish();});
