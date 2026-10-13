'use strict';
const token=document.getElementById('token'), enabled=document.getElementById('enabled'), browser=document.getElementById('browser'), status=document.getElementById('status');
const ua=navigator.userAgent||'';
const detected=/Edg\//.test(ua)?'msedge.exe':/OPR\//.test(ua)?'opera.exe':'chrome.exe';
chrome.storage.local.get(['token','enabled','browser','last_status']).then(data=>{token.value=data.token||'';enabled.checked=!!data.enabled;browser.value=data.browser||detected;status.textContent=data.last_status||'请先连接本机 DayTrace。';});
document.getElementById('save').onclick=async()=>{await chrome.storage.local.set({token:token.value.trim(),enabled:enabled.checked,browser:browser.value});status.textContent='已保存，正在检查连接。';};
chrome.storage.onChanged.addListener(changes=>{if(changes.last_status)status.textContent=changes.last_status.newValue||'';});
