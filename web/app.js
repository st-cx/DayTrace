'use strict';
const $ = id => document.getElementById(id);
const categories = ['学习','编程','工作','浏览器','娱乐','游戏','休息','生活','离开电脑','其他'];
const pages = {history:['系统历史线索','查看 Windows 已有痕迹，不把线索伪装成应用工时。'],today:['每日时间轴','回顾电脑活动，也记录屏幕之外的生活。'],search:['查找活动','用关键词、项目和标签找回过去的投入。'],report:['时间报表','从每日变化中看清你的节奏。'],projects:['我的项目','为每件重要的事，留一份时间账。'],rules:['分类规则','让后续记录按照你的习惯归类。'],settings:['隐私与数据','自己选择记录什么，也掌握备份与恢复。']};
let config = null, token = '', currentPage = 'today', projects = [], searchPage = 1, editRecord = null;
let timerState = null, timerSyncedAt = 0, refreshBusy = false, lastFocusNotice = '', lastFocusedElement;
let recoveryBusy = false, appExited = false;
const recordCache = new Map(), knownTags = new Set();
const e = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const dayISO = d => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
const today = () => dayISO(new Date());
const asDate = value => new Date(typeof value === 'number' ? value * 1000 : value);
const localDT = value => { const d=asDate(value); return `${dayISO(d)}T${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}:${String(d.getSeconds()).padStart(2,'0')}`; };
const stamp = value => asDate(value).getTime()/1000;
const clockTime = value => asDate(value).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false});
const duration = seconds => {const s=Math.max(0,Math.floor(Number(seconds)||0)),h=Math.floor(s/3600),m=Math.floor(s%3600/60),r=s%60;return s===0?'0分钟':`${h?h+'小时':''}${m?m+'分':''}${r?r+'秒':''}`;};
const clock = seconds => {const n=Math.max(0,Math.floor(seconds||0));return [Math.floor(n/3600),Math.floor(n%3600/60),n%60].map(v=>String(v).padStart(2,'0')).join(':');};
const validColor = value => /^#[0-9a-f]{6}$/i.test(value||'') ? value : '#8798a9';
const color = name => validColor(config?.colors?.[name]);
const tags = value => [...new Set(value.split(/[,，;；\n]/).map(x=>x.trim()).filter(Boolean))];
const lines = value => value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean);
const params = object => new URLSearchParams(object).toString();
function toast(message, error=false){$('toast').textContent=message;$('toast').className='toast'+(error?' error':'');clearTimeout(window.toastTimer);window.toastTimer=setTimeout(()=>$('toast').classList.add('hidden'), error?6500:4000);}
async function safe(fn){if(appExited)return;try{await fn();}catch(err){toast(err.message||'操作失败，请稍后重试',true);}}
async function refreshToken(){const response=await fetch('/api/config',{cache:'no-store'});const data=await response.json();if(!response.ok)throw Error(data.error||'无法连接本地服务');config=data;token=data.token||'';return data;}
async function responseFor(path, method='GET', data, retried=false){
  const headers={'X-DayTrace-Key':token};const options={method,headers,cache:'no-store'};
  if(data!==undefined){headers['Content-Type']='application/json';options.body=JSON.stringify(data);}
  const response=await fetch(path, options);
  if(response.status===403&&!retried){await refreshToken();return responseFor(path,method,data,true);}
  if(!response.ok){const body=await response.json().catch(()=>({}));throw Error(body.error||`操作失败（${response.status}）`);}
  return response;
}
async function request(path,method='GET',data){return (await responseFor(path,method,data)).json();}
function populateSelect(id, all=false){$(id).innerHTML=(all?'<option value="">全部分类</option>':'')+categories.map(c=>`<option value="${e(c)}">${e(c)}</option>`).join('');}
async function loadConfig(applySettings=true){
  const data=await refreshToken(), s=data.settings||{};
  if(applySettings){$('set-paused').checked=!!s.paused;$('set-titles').checked=!!s.record_titles;$('set-browser').checked=!!s.browser_enabled;$('set-idle').value=s.idle_seconds??180;$('set-goal').value=s.daily_goal_minutes??0;for(const [id,key] of [['set-apps','excluded_apps'],['set-titles-exclude','excluded_titles'],['set-domains','excluded_domains']])$(id).value=(s[key]||[]).join('\n');}
  $('version').textContent=`DayTrace ${data.version||''} · 本地保存`;
  const available=!!data.tracking_supported;
  const health=data.health||{}, states={recording:'正在记录电脑活动',idle:'离开电脑，正在记录',excluded:'当前应用已排除',paused:'自动采集已暂停',starting:'正在启动采集',unavailable:'未读取到前台活动',error:'自动采集出现错误'};
  $('running-dot').classList.toggle('paused',s.paused||!available||['error','unavailable','starting'].includes(health.state)||health.collector_alive===false);
  $('running-label').textContent=s.paused?'自动采集已暂停':!available?'手动记录模式':health.collector_alive===false?'采集服务未运行':states[health.state]||'自动采集已开启';
  $('running-label').title=health.error||'';
  $('pause').textContent=s.paused?'恢复自动记录':'暂停自动记录';
  $('platform-note').textContent=!available?'当前平台无法自动采集窗口；可以使用任务计时和手动记录。':health.state==='error'?'采集出现错误：'+(health.error||'请重启后台服务后再检查状态。'):health.collector_alive===false?'自动采集服务未运行，请重启 DayTrace。':health.state==='unavailable'?'暂未读取到可用前台活动，自动采集将在获得有效窗口后继续。':'Windows 活动采集可用。关闭此页面后，后台服务仍会继续记录。';
  if(applySettings&&$('set-autostart'))$('set-autostart').checked=!!s.autostart;
}
function remember(records){for(const r of records||[]){recordCache.set(String(r.id),r);for(const t of r.tags||[])knownTags.add(t);} $('tag-options').innerHTML=[...knownTags].sort().map(t=>`<option value="${e(t)}"></option>`).join('');}
function bars(id, totals={}, palette=color){const entries=Object.entries(totals||{}).map(([k,v])=>[k,typeof v==='object'?(v.seconds??v.total??0):v]).filter(([,v])=>Number(v)>0).sort((a,b)=>b[1]-a[1]);const sum=entries.reduce((s,[,v])=>s+Number(v),0)||1;$(id).innerHTML=entries.length?entries.map(([k,v])=>`<div><div class="category-head"><b><span class="swatch" style="background:${palette(k)}"></span> ${e(k||'未归项目')}</b><em>${duration(v)} · ${(v/sum*100).toFixed(1)}%</em></div><div class="track"><div class="fill" style="background:${palette(k)};width:${v/sum*100}%"></div></div></div>`).join(''):'<div class="empty">这段时间还没有记录。</div>';}
function activities(id, records, includeDate=false){
  remember(records);
  const sourceNames={manual:'手动补记',auto:'自动采集',browser:'浏览器',timer:'任务计时'};
  $(id).innerHTML=records.length?records.map(r=>`<div class="activity"><i class="bullet" style="background:${color(r.category)}"></i><div class="details"><strong>${e(r.note||r.title||r.domain||r.app_purpose?.display_name||r.app||r.category||'活动')}</strong><small>${e(r.source==='manual'&&/\.exe$/i.test(r.app||'')?'手动确认':(sourceNames[r.source]||r.source||'记录'))} · ${e(r.category)}${r.project?' · '+e(r.project):''}${(r.tags||[]).length?' · '+r.tags.map(t=>'#'+e(t)).join(' '):''}${r.billable?' · 计费':''}</small>${r.source==='auto'&&r.app_purpose?`<small>应用用途 · ${e(r.app_purpose.hint)}${r.app_purpose.remembered_category?' · 已记住：'+e(r.app_purpose.remembered_category):''}</small>`:''}</div><div class="time">${includeDate?e(dayISO(asDate(r.start)))+'<br>':''}${clockTime(r.visible_start??r.start)}–${clockTime(r.visible_end??r.end)}<br>${duration(stamp(r.visible_end??r.end)-stamp(r.visible_start??r.start))}</div><button class="btn tiny" data-edit="${e(r.id)}" aria-label="编辑活动">编辑</button><button class="xbutton" data-remove="${e(r.id)}" aria-label="删除活动">×</button></div>`).join(''):'<div class="empty">暂无活动。可以开始任务计时，或补记屏幕之外的生活。</div>';
  $(id).querySelectorAll('[data-edit]').forEach(b=>b.onclick=()=>openModal(recordCache.get(b.dataset.edit)));
  $(id).querySelectorAll('[data-remove]').forEach(b=>b.onclick=()=>safe(async()=>{if(!confirm('删除这条活动记录？删除后无法直接撤销，建议先备份。'))return;await request('/api/record/'+encodeURIComponent(b.dataset.remove),'DELETE');recordCache.delete(b.dataset.remove);toast('活动已删除');await refreshCurrent();}));
}
async function loadDay(){
  const date=$('date').value||today(), x=await request('/api/day?'+params({date}));
  remember(x.records);$('total-time').textContent=duration(x.total);const productive=x.productive_seconds??['学习','编程','工作'].reduce((s,k)=>s+(x.totals?.[k]||0),0);$('study-time').textContent=duration(productive);$('segment-count').textContent=(x.records||[]).length;
  const goal=x.goal_minutes??config.settings.daily_goal_minutes??0;$('goal-progress').textContent=goal>0?`目标 ${duration(goal*60)} · 已完成 ${Math.round(productive/(goal*60)*100)}%`:'每日目标未设置';
  const start=stamp(date+'T00:00:00'), next=new Date(date+'T00:00:00');next.setDate(next.getDate()+1);const range=next.getTime()/1000-start;
  $('timeline').innerHTML='';
  for(const r of x.timeline||[]){const begin=Math.max(start,stamp(r.start)), end=Math.min(start+range,stamp(r.end));if(end<=begin)continue;const b=document.createElement('button');b.className='tblock';b.style.left=((begin-start)/range*100)+'%';b.style.width=((end-begin)/range*100)+'%';b.style.background=color(r.category);b.title=`${clockTime(r.start)}–${clockTime(r.end)} ${r.category} ${r.note||r.app||''}`;b.setAttribute('aria-label',b.title);if(recordCache.has(String(r.id)))b.onclick=()=>openModal(recordCache.get(String(r.id)));else b.disabled=true;$('timeline').appendChild(b);}
  $('legend').innerHTML=Object.keys(x.totals||{}).map(k=>`<span><i class="swatch" style="background:${color(k)}"></i>${e(k)}</span>`).join('');bars('categories',x.totals);activities('activity-list',(x.records||[]).slice().sort((a,b)=>stamp(b.start)-stamp(a.start)));
}
function checkRange(from,to){if(!from||!to)throw Error('请选择开始和结束日期');if(from>to)throw Error('开始日期不能晚于结束日期');return {from,to};}
async function loadSearch(){const range=checkRange($('search-from').value,$('search-to').value);const x=await request('/api/records?'+params({...range,q:$('search-q').value.trim(),category:$('search-category').value,project:$('search-project').value.trim(),tag:$('search-tag').value.trim(),source:$('search-source').value,page:searchPage,page_size:30}));searchPage=x.page||searchPage;const count=x.total_count||0,pagesCount=Math.max(1,Math.ceil(count/(x.page_size||30)));$('search-summary').textContent=`共 ${count} 条原始记录 · 有效时长 ${duration(x.total_seconds)}（已去重）`;$('search-page').textContent=`第 ${searchPage} / ${pagesCount} 页`;$('search-prev').disabled=searchPage<=1;$('search-next').disabled=searchPage>=pagesCount;activities('search-results',x.records||[],true);}
function presetReport(value){if(value==='custom')return;const end=new Date(),start=new Date(end);start.setDate(start.getDate()-(value==='month'?29:6));$('report-from').value=dayISO(start);$('report-to').value=dayISO(end);}
async function loadReport(){const range=checkRange($('report-from').value,$('report-to').value),x=await request('/api/report?'+params(range));$('period-total').textContent=duration(x.total);$('period-average').textContent=`日均 ${duration((x.total||0)/(x.days?.length||1))}`;$('period-study').textContent=duration(x.productive_seconds);$('period-billable').textContent=duration(x.billable_seconds);$('period-amount').textContent=`估算金额 ${Number(x.estimated_amount||0).toFixed(2)}（按设置费率）`;const max=Math.max(1,...(x.days||[]).map(d=>d.total||0));$('period-bars').innerHTML=(x.days||[]).length?(x.days||[]).map(d=>`<div class="report-row"><button class="date-link" data-day="${e(d.date)}">${e(d.date.slice(5))}</button><div class="track">${Object.entries(d.totals||{}).map(([k,v])=>`<div class="fill" title="${e(k)} ${duration(v)}" style="background:${color(k)};width:${v/max*100}%"></div>`).join('')}</div><b>${duration(d.total)}</b></div>`).join(''):'<div class="empty">暂无记录。</div>';$('period-bars').querySelectorAll('[data-day]').forEach(b=>b.onclick=()=>{$('date').value=b.dataset.day;changePage('today');});bars('period-categories',x.totals);bars('period-projects',x.projects,k=>validColor(projects.find(p=>p.name===k)?.color));bars('period-apps',x.apps,()=> '#5589ab');bars('period-domains',x.domains,()=> '#8a7bb2');}
async function loadProjects(){const x=await request('/api/projects');projects=x.projects||[];$('project-options').innerHTML=projects.filter(p=>!p.archived).map(p=>`<option value="${e(p.name)}"></option>`).join('');$('project-list').innerHTML=projects.length?projects.map(p=>`<div class="project-card${p.archived?' archived':''}"><span class="swatch" style="background:${validColor(p.color)}"></span><div><strong>${e(p.name)}</strong><small>${p.archived?'已归档 · ':''}费率 ${Number(p.hourly_rate||0).toFixed(2)}/小时${p.budget_minutes?' · 预算 '+duration(p.budget_minutes*60):''}</small></div><button class="btn tiny" data-project-edit="${e(p.id)}">${p.archived?'恢复 / 编辑':'编辑'}</button>${p.archived?'':`<button class="btn tiny" data-project-archive="${e(p.id)}">归档</button>`}</div>`).join(''):'<div class="empty">尚未建立项目，已有活动也可以随时归入新项目。</div>';$('project-list').querySelectorAll('[data-project-edit]').forEach(b=>b.onclick=()=>{const p=projects.find(p=>String(p.id)===b.dataset.projectEdit);$('project-id').value=p.id;$('project-name').value=p.name;$('project-color').value=validColor(p.color);$('project-rate').value=p.hourly_rate||0;$('project-budget').value=p.budget_minutes||0;$('project-save').textContent=p.archived?'恢复并保存':'保存修改';$('project-cancel').classList.remove('hidden');$('project-name').focus();});$('project-list').querySelectorAll('[data-project-archive]').forEach(b=>b.onclick=()=>safe(async()=>{if(!confirm('归档这个项目？已有活动记录会保留。'))return;await request('/api/project/'+encodeURIComponent(b.dataset.projectArchive),'DELETE');await loadProjects();toast('项目已归档');}));}
function resetProject(){$('project-form').reset();$('project-id').value='';$('project-save').textContent='新增项目';$('project-cancel').classList.add('hidden');}
async function loadRules(){await loadConfig(false);const targets={any:'全部字段',app:'应用名',title:'窗口标题',domain:'域名'};$('rule-list').innerHTML=(config.rules||[]).length?config.rules.map(r=>`<div class="rule"><i class="swatch" style="background:${color(r.category)}"></i><div class="rule-body"><b>${e(r.pattern)}</b><small>${e(targets[r.target]||'全部字段')} → ${e(r.category)}${r.project?' · '+e(r.project):''}${(r.tags||[]).length?' · '+r.tags.map(t=>'#'+e(t)).join(' '):''}</small></div><button class="btn tiny" data-rule-delete="${e(r.id)}">移除</button></div>`).join(''):'<div class="empty">暂无自定义规则，正在使用基础分类。</div>';$('rule-list').querySelectorAll('[data-rule-delete]').forEach(b=>b.onclick=()=>safe(async()=>{await request('/api/rule/'+encodeURIComponent(b.dataset.ruleDelete),'DELETE');await loadRules();toast('规则已移除');}));}
async function applyRules(dry_run){const range=checkRange($('rule-from').value,$('rule-to').value);if(!dry_run){const preview=await request('/api/rules/apply','POST',{...range,dry_run:true});if(!preview.changed){$('rule-impact').textContent='当前范围没有需要修改的活动。';return;}if(!confirm(`按当前规则修改 ${preview.changed} 条历史活动？建议先保存 JSON 备份。`))return;}const result=await request('/api/rules/apply','POST',{...range,dry_run});$('rule-impact').textContent=dry_run?`预计修改 ${result.changed||0} 条活动；尚未修改任何记录。`:`已修改 ${result.changed||0} 条活动。`;if(!dry_run)toast('历史规则已应用');}
async function loadTimer(){
  const before=timerState;timerState=await request('/api/timer');timerSyncedAt=Date.now();
  const running=!!timerState.running,pending=!running&&!!timerState.recovery,entry=timerState.entry||timerState.recovery?.entry||{};
  for(const id of ['timer-note','timer-project','timer-category','timer-focus','timer-tags','timer-billable','timer-rate'])$(id).disabled=running||pending;
  $('timer-start').classList.toggle('hidden',running||pending);$('timer-stop').classList.toggle('hidden',!running);
  if(running||pending){$('timer-note').value=entry.note||'';$('timer-project').value=entry.project||'';$('timer-category').value=entry.category||'工作';$('timer-tags').value=(entry.tags||[]).join(', ');$('timer-billable').checked=!!entry.billable;$('timer-rate').value=entry.hourly_rate||0;$('timer-focus').value=entry.focus_minutes===25?'25':'0';}
  const reason=timerState.last_reason||'';
  if(before?.running&&!running&&/focus|pomodoro|complete/.test(reason)){const key=JSON.stringify([entry.id,reason,before.entry?.start]);if(key!==lastFocusNotice){lastFocusNotice=key;toast('本次专注已完成，活动已保存。休息一下吧。');await refreshCurrent();}}
  renderTimer();
}
function renderTimer(){if(appExited)return;
  if(!timerState)return;
  const delta=timerState.running?(Date.now()-timerSyncedAt)/1000:0,elapsed=(timerState.elapsed||0)+delta,remaining=timerState.focus_remaining;
  const hasFocus=timerState.running&&remaining!==null&&remaining!==undefined,shown=hasFocus?Math.max(0,remaining-delta):timerState.running?elapsed:0;
  $('timer-clock').textContent=clock(shown);$('timer-clock').classList.toggle('active',!!timerState.running);
  const pending=!timerState.running&&timerState.recovery;
  $('timer-recovery').classList.toggle('hidden',!pending);
  $('timer-resume').disabled=$('timer-dismiss').disabled=recoveryBusy;
  if(pending){
    const entry=pending.entry||{},saved=asDate(pending.stopped_at).toLocaleString('zh-CN',{hour12:false}),name=entry.note||entry.project||entry.category||'上次任务';
    $('timer-recovery-description').textContent=`“${name}”已保存至 ${saved}，已记录 ${duration((entry.end||0)-(entry.start||0))}。停止期间没有计时。继续会从现在开始一段新记录${entry.focus_minutes?`，专注重新计满 ${entry.focus_minutes} 分钟`:''}；放弃继续会保留已有工时。`;
  }
  const reason=timerState.last_reason,stoppedText=pending?'上次任务已停止，等待你的选择。':reason==='focus_completed'?'上次专注已完成并保存。休息一下，再开始下一件事。':reason==='limit_reached'?'上次任务达到时长上限，已停止并保存。':'选择任务后开始，关闭页面也会继续计时。';
  $('timer-description').textContent=timerState.running?`${hasFocus?'专注倒计时':'任务正在计时'} · ${timerState.entry?.note||timerState.entry?.project||timerState.entry?.category||'当前任务'}${hasFocus?' · 完成后自动保存':''}`:stoppedText;
}
async function decideRecovery(resume){
  if(recoveryBusy||timerState?.running||!timerState?.recovery)return;
  const recovery_id=timerState.recovery.recovery_id;
  recoveryBusy=true;renderTimer();
  try{
    await request(resume?'/api/timer/resume':'/api/timer/dismiss-recovery','POST',{recovery_id});
    await loadTimer();await refreshCurrent();
    toast(resume?'已从现在继续任务，停止期间没有计入工时。':'已放弃继续，已有工时仍保留。');
  }catch(error){try{await loadTimer();}catch{}throw error;}
  finally{recoveryBusy=false;renderTimer();}
}
function openModal(record=null){lastFocusedElement=document.activeElement;editRecord=record;$('manual-form').reset();$('modal-title').textContent=record?'编辑活动':'补记一段活动';const end=new Date(),start=new Date(end.getTime()-30*60000);$('manual-start').value=localDT(record?.start??start.toISOString());$('manual-end').value=localDT(record?.end??end.toISOString());$('manual-category').value=record?.category||'生活';$('manual-note').value=record?.note||'';$('manual-project').value=record?.project||'';$('manual-tags').value=(record?.tags||[]).join(', ');$('manual-billable').checked=!!record?.billable;$('manual-rate').value=record?.hourly_rate||0;const canRemember=record?.source==='auto'&&/\.exe$/i.test(record?.app||'');$('manual-remember').checked=false;$('manual-remember-box').classList.toggle('hidden',!canRemember);$('manual-purpose').classList.toggle('hidden',!record?.app_purpose||!record?.app);if(record?.app_purpose){const purpose=record.app_purpose;$('manual-purpose').textContent=`${purpose.display_name||record.app} · ${purpose.hint}。${purpose.remembered_category?'已记住分类：'+purpose.remembered_category+'。':'这是按应用用途的基本判断，不代表这次活动的实际目的。'}`;$('manual-remember-help').textContent=purpose.category==='浏览器'?'勾选后会影响此浏览器的所有网页；仅改这一段时请保持不勾选。已有自定义规则仍优先。':'只记住准确的应用名，不影响其他程序；已有自定义规则仍优先。';}$('modal').classList.remove('hidden');$('manual-start').focus();}
function closeModal(){$('modal').classList.add('hidden');editRecord=null;lastFocusedElement?.focus();}
async function refreshCurrent(){if(currentPage==='today')await loadDay();else if(currentPage==='search')await loadSearch();else if(currentPage==='report')await loadReport();else if(currentPage==='projects')await loadProjects();else if(currentPage==='rules')await loadRules();else if(currentPage==='history')await loadHistory();}
function changePage(page){if(!pages[page])return;window.scrollTo({top:0,behavior:"auto"});currentPage=page;document.querySelectorAll('.page').forEach(x=>x.classList.toggle('active',x.id===page));document.querySelectorAll('#nav button').forEach(b=>{b.classList.toggle('active',b.dataset.page===page);b.setAttribute('aria-current',b.dataset.page===page?'page':'false');});$('page-title').textContent=pages[page][0];$('page-desc').textContent=pages[page][1];safe(async()=>{if(page==='settings')await loadConfig();else await refreshCurrent();});}
async function download(path,fallback){const response=await responseFor(path),blob=await response.blob();const header=response.headers.get('Content-Disposition')||'';let filename=fallback;const utf=header.match(/filename\*=UTF-8''([^;]+)/i),normal=header.match(/filename="?([^";]+)"?/i);if(utf){try{filename=decodeURIComponent(utf[1]);}catch{}}else if(normal)filename=normal[1];const a=document.createElement('a'),url=URL.createObjectURL(blob);a.href=url;a.download=filename.replace(/[\/\\]/g,'_');document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);toast('导出文件已准备，请查看浏览器下载。');}
function projectRate(prefix){const p=projects.find(p=>p.name===$(prefix+'-project').value.trim());if(p)$(prefix+'-rate').value=p.hourly_rate||0;}
function formHandler(id,fn){let busy=false;$(id).onsubmit=event=>{event.preventDefault();if(busy)return;busy=true;const button=$(id).querySelector('button[type=submit]');if(button)button.disabled=true;safe(fn).finally(()=>{busy=false;if(button)button.disabled=false;});};}
function shiftDate(offset){const d=new Date($('date').value+'T12:00:00');d.setDate(d.getDate()+offset);$('date').value=dayISO(d);safe(loadDay);}
function wire(){
  $('history-refresh').onclick=()=>safe(loadHistory);
  document.querySelectorAll('#nav button').forEach(b=>b.onclick=()=>changePage(b.dataset.page));
  $('pause').onclick=()=>safe(async()=>{await request('/api/settings','POST',{paused:!config.settings.paused});await loadConfig(false);toast(config.settings.paused?'自动记录已暂停':'自动记录已恢复');});
  $('add').onclick=()=>openModal();$('modal-close').onclick=$('manual-cancel').onclick=closeModal;
  $('modal').onclick=event=>{if(event.target===$('modal'))closeModal();};
  document.addEventListener('keydown',event=>{if($('modal').classList.contains('hidden'))return;if(event.key==='Escape')closeModal();if(event.key==='Tab'){const focusable=[...$('modal').querySelectorAll('button,input,select,textarea')].filter(el=>!el.disabled&&el.type!=='hidden');const first=focusable[0],last=focusable[focusable.length-1];if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}}});
  formHandler('manual-form',async()=>{const start=$('manual-start').value,end=$('manual-end').value;if(stamp(end)<=stamp(start))throw Error('结束时间必须晚于开始时间');const data={start,end,category:$('manual-category').value,note:$('manual-note').value.trim(),project:$('manual-project').value.trim(),tags:tags($('manual-tags').value),billable:$('manual-billable').checked,hourly_rate:Number($('manual-rate').value)};if(editRecord?.source==='auto')data.remember_app=$('manual-remember').checked;await request(editRecord?'/api/record/'+encodeURIComponent(editRecord.id):'/api/manual',editRecord?'PUT':'POST',data);closeModal();toast(data.remember_app?'活动已纠正，已记住应用分类':'活动已保存');await refreshCurrent();});
  $('prev-date').onclick=()=>shiftDate(-1);$('next-date').onclick=()=>shiftDate(1);$('go-today').onclick=()=>{$('date').value=today();safe(loadDay);};$('date').onchange=$('refresh').onclick=()=>safe(loadDay);
  formHandler('search-form',async()=>{searchPage=1;await loadSearch();});$('search-prev').onclick=()=>{searchPage--;safe(loadSearch);};$('search-next').onclick=()=>{searchPage++;safe(loadSearch);};
  formHandler('report-form',loadReport);$('report-preset').onchange=()=>{presetReport($('report-preset').value);safe(loadReport);};$('report-from').onchange=$('report-to').onchange=()=>{$('report-preset').value='custom';};
  formHandler('project-form',async()=>{const data={name:$('project-name').value.trim(),color:$('project-color').value,hourly_rate:Number($('project-rate').value),budget_minutes:Number($('project-budget').value),archived:false};if($('project-id').value)data.id=Number($('project-id').value);await request('/api/projects','POST',data);resetProject();await loadProjects();toast('项目已保存');});$('project-cancel').onclick=resetProject;
  for(const prefix of ['timer','manual'])$(prefix+'-project').onchange=()=>projectRate(prefix);
  formHandler('rule-form',async()=>{await request('/api/rule','POST',{pattern:$('rule-pattern').value.trim(),target:$('rule-target').value,category:$('rule-category').value,project:$('rule-project').value.trim(),tags:tags($('rule-tags').value)});$('rule-pattern').value='';await loadRules();toast('规则已添加');});$('rule-preview').onclick=()=>safe(()=>applyRules(true));$('rule-apply').onclick=()=>safe(()=>applyRules(false));
  formHandler('timer-form',async()=>{await request('/api/timer/start','POST',{note:$('timer-note').value.trim(),project:$('timer-project').value.trim(),category:$('timer-category').value,tags:tags($('timer-tags').value),focus_minutes:Number($('timer-focus').value),billable:$('timer-billable').checked,hourly_rate:Number($('timer-rate').value)});await loadTimer();toast('任务计时已开始');});$('timer-stop').onclick=()=>safe(async()=>{await request('/api/timer/stop','POST',{});await loadTimer();await refreshCurrent();toast('任务已结束并保存');});
  $('timer-resume').onclick=()=>safe(()=>decideRecovery(true));$('timer-dismiss').onclick=()=>safe(()=>decideRecovery(false));
  $('save-settings').onclick=()=>safe(async()=>{const idle=Number($('set-idle').value),goal=Number($('set-goal').value);if(!Number.isInteger(idle)||idle<20||idle>1800)throw Error('离开电脑判定需为 20–1800 秒的整数');if(!Number.isInteger(goal)||goal<0||goal>1440)throw Error('每日目标需为 0–1440 分钟的整数');await request('/api/settings','POST',{paused:$('set-paused').checked,record_titles:$('set-titles').checked,browser_enabled:$('set-browser').checked,idle_seconds:idle,daily_goal_minutes:goal,autostart:$('set-autostart').checked,excluded_apps:lines($('set-apps').value),excluded_titles:lines($('set-titles-exclude').value),excluded_domains:lines($('set-domains').value)});await loadConfig();toast('设置已保存');});
  $('export').onclick=()=>safe(async()=>{const range=checkRange($('export-from').value,$('export-to').value);await download('/api/export?'+params({...range,mode:$('export-mode').value}),`DayTrace-${range.from}-${range.to}.csv`);});$('backup-download').onclick=()=>safe(()=>download('/api/backup',`DayTrace-backup-${today()}.json`));$('backup-create').onclick=()=>safe(async()=>{const x=await request('/api/backup/create','POST',{});$('backup-result').textContent='本机备份已保存：'+(x.path||'');toast('本机备份已创建');});
  $('import-choose').onclick=()=>{$('import-file').value='';$('import-file').click();};$('import-file').onchange=()=>safe(async()=>{const file=$('import-file').files[0];if(!file)return;if(file.size>7*1024*1024)throw Error('文件超过 7 MB，请拆分后导入');const csv=/\.csv$/i.test(file.name),json=/\.json$/i.test(file.name);if(!csv&&!json)throw Error('请选择 JSON 或 CSV 文件');const text=await file.text();let payload;if(csv){const csv_options={date_order:$('import-date-order').value};const offset=$('import-utc-offset').value;if(offset!==''){const minutes=Number(offset);if(!Number.isInteger(minutes)||minutes< -720||minutes>840)throw Error('UTC 时差需为 -720 至 840 的整数分钟');csv_options.utc_offset_minutes=minutes;}payload={csv:text,csv_options};}else{try{payload=JSON.parse(text.replace(/^\uFEFF/,''));}catch{throw Error('JSON 文件格式无效');}}if(!confirm(`导入“${file.name}”？${json?'恢复记录、项目与规则，保留当前隐私设置。':'活动将添加到当前记录中。外部 CSV 请先核对日期顺序和时区；金额与费率不会导入。'}建议先创建本机备份。`))return;const x=await request('/api/import','POST',payload);$('backup-result').textContent=`导入完成：新增 ${x.imported||0} 条，跳过 ${x.skipped||0} 条。${x.settings_restored===false?'当前隐私设置已保留。':''}`;await loadConfig();await loadProjects();toast('数据已导入');});
  $('exit-app').onclick=()=>safe(async()=>{await request('/api/shutdown','POST',{});appExited=true;$('platform-note').textContent='后台服务已停止，记录已保存。';$('running-label').textContent='软件已退出';$('running-dot').classList.add('paused');$('page-title').textContent='DayTrace 已退出';$('page-desc').textContent='记录已保存，可以关闭此页。再次打开 DayTrace 即可继续。';document.querySelectorAll('button,input,select,textarea').forEach(x=>x.disabled=true);toast('已停止后台记录，数据已保存');});
  $('copy-token').onclick=()=>safe(async()=>{await loadConfig(false);if(!config.browser_token)throw Error('扩展连接密钥暂不可用，请刷新页面后重试');await navigator.clipboard.writeText(config.browser_token);toast('连接密钥已复制，请勿分享给其他人');});
}
async function init(){
  for(const id of ['manual-category','timer-category','rule-category'])populateSelect(id);populateSelect('search-category',true);$('timer-category').value='工作';$('rule-category').value='学习';$('date').value=today();const start=new Date();start.setDate(start.getDate()-29);for(const prefix of ['search','rule','export']){$(prefix+'-from').value=dayISO(start);$(prefix+'-to').value=today();}presetReport('week');$('manual-start').step=$('manual-end').step='1';wire();await loadConfig();await Promise.all([loadProjects(),loadDay(),loadTimer()]);
  if(pages[location.hash.slice(1)])changePage(location.hash.slice(1));
  setInterval(renderTimer,1000);setInterval(async()=>{if(appExited||refreshBusy||document.hidden)return;refreshBusy=true;try{await loadConfig(false);await loadTimer();if(currentPage==='today'&&$('date').value===today())await loadDay();}catch(err){toast('自动刷新失败：'+err.message,true);$('running-label').textContent='连接中断';$('running-dot').classList.add('paused');}finally{refreshBusy=false;}},15000);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)safe(async()=>{await loadTimer();await refreshCurrent();});});
}
init().catch(err=>{toast('连接失败：'+err.message+'。请确认 DayTrace 后台服务正在运行，再刷新页面。',true);$('running-label').textContent='连接失败';});



async function loadHistory(){
  $('history-refresh').disabled=true;
  $('history-errors').textContent='正在读取本机已有线索…';
  try{
    const data=await request('/api/system-history?refresh=1');
    $('history-notice').textContent=data.notice;
    $('history-count').textContent=(data.sessions||[]).length;
    $('history-power').textContent=duration(data.closed_power_seconds);
    $('history-app-count').textContent=(data.apps||[]).length;
    $('history-errors').textContent=(data.errors||[]).join(' · ') || '只读扫描完成；线索独立展示，不计入工作或学习时间。';
    const at=s=>asDate(s).toLocaleString('zh-CN',{hour12:false});
    $('history-sessions').innerHTML=(data.sessions||[]).map(s=>`<div class="history-row"><b>${e(at(s.start))}</b><small>${s.end?'→ '+e(at(s.end))+' · 区间 '+duration(s.duration):'未观察到关机记录；不补算时长'}</small></div>`).join('')||'<div class="empty">没有可读取的开关机日志。</div>';
    $('history-apps').innerHTML=(data.apps||[]).map(a=>`<div class="history-row"><b>${e(a.app)}</b><small>最近执行线索 · ${e(at(a.time))}</small></div>`).join('')||'<div class="empty">系统未留存可读取的应用执行线索。</div>';
    $('history-recent').innerHTML=(data.recent||[]).map(r=>`<div class="history-row"><b>${e(r.name)}</b><small>近期快捷方式更新时间 · ${e(at(r.time))}</small></div>`).join('')||'<div class="empty">没有近期文件线索。</div>';
  }finally{$('history-refresh').disabled=false;}
}
