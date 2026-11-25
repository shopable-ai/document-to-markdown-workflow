'use strict';
const $ = id => document.getElementById(id);
const show = v => v === null || v === undefined || v === '' ? 'Unknown' : String(v);
const node = (tag, text, cls) => { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (cls) e.className = cls; return e; };
const size = n => Number.isFinite(n) ? (n / 1024 ** 3).toFixed(2) + ' GiB' : 'Unknown';
const duration = n => Number.isFinite(n) ? Math.floor(n / 60) + ' 分 ' + n % 60 + ' 秒' : 'Unknown';
let latest = null, stream = null, lastSeen = 0, preferredBatch = '';
try { preferredBatch=sessionStorage.getItem('d2md.batch')||''; } catch {}
function cards(target, values) { target.replaceChildren(...values.map(([label,value]) => { const e=node('div',undefined,'card'); e.append(node('div',label,'small'),node('div',show(value),'number')); return e; })); }
function render() {
  if (!latest) return;
  const selected = preferredBatch || $('batch').value;
  const batches = latest.batches;
  $('batch').replaceChildren(...batches.map(b => { const opt=node('option',b.run_id + ' · ' + show(b.status)); opt.value=b.run_id; return opt; }));
  if (batches.some(b=>b.run_id===selected)) $('batch').value=selected;
  else { const active=batches.find(b=>b.active===true); if(active) $('batch').value=active.run_id; }
  const batch=batches.find(b=>b.run_id===$('batch').value)||{};
  const summary=batch.summary||{};
  $('batch-meta').textContent='路线：'+show(batch.route)+' · 状态：'+show(batch.status)+' · 批次证据：'+show(batch.batch_updated_at);
  cards($('metrics'),[['登记来源',summary.planned_total],['当前处理',batch.active===true?summary.running:null],['当前路线严格通过',batch.strict_accepted_count],['失败 / 待复核',Number.isInteger(summary.failed)&&Number.isInteger(summary.needs_review)?summary.failed+' / '+summary.needs_review:null]]);
  $('reason').textContent=[batch.reason,batch.halt_reason,batch.error].filter(Boolean).join(' · ')||'无已登记异常';
  $('ledger').textContent='跨路线严格验收总账：'+show(latest.ledger.status)+'（'+latest.ledger.reason+'）';
  const search=$('search').value.toLowerCase(), filter=$('filter').value;
  const rows=(batch.rows||[]).filter(r=>r.source.toLowerCase().includes(search)&&(!filter||r.status===filter));
  const openDetails=new Set([...$('tasks').querySelectorAll('details[open]')].map(d=>d.dataset.source));
  $('tasks').replaceChildren();
  for (const r of rows) {
    const task=node('article',undefined,'task');
    const grid=node('div',undefined,'task-grid');
    const name=node('div'); name.append(node('strong',r.source.split('/').pop()),node('div',r.source,'small'));
    const progress=node('div',undefined,'taskprogress'); progress.append(node('div','最近阶段：'+show(r.phase)),node('div','页数：'+show(r.done)+' / '+show(r.total),'small'));
    if(Number.isInteger(r.done)&&Number.isInteger(r.total)&&r.total>0&&r.done<=r.total){ const bar=node('progress');bar.max=r.total;bar.value=r.done;bar.setAttribute('aria-label','处理页数');progress.append(bar); }
    const status=node('div'); status.append(node('strong',show(r.status),r.status==='failed'?'red':r.status==='needs-review'?'amber':r.strict_in_batch===true?'green':''),node('div','路线严格验收：'+(r.strict_in_batch===true?'通过':r.strict_in_batch===false?'未通过':'Unknown'),'small'));
    const timing=node('div'); timing.append(node('span',duration(r.elapsed_seconds)),node('div',show(r.route),'small'));
    grid.append(name,progress,status,timing); task.append(grid);
    const detail=node('details');detail.dataset.source=r.source;detail.open=openDetails.has(r.source);detail.append(node('summary','证据与异常'),node('p','开始：'+show(r.started_at)+'；结束：'+show(r.finished_at)),node('p','最近检查点页：'+show(r.checkpoint_page)+'；时间：'+show(r.checkpoint_at)+(Number.isFinite(r.checkpoint_age_seconds)&&r.checkpoint_age_seconds>=300?'（超过 5 分钟无新检查点，不代表失败）':'')),node('p','异常：'+show(r.error)));task.append(detail); $('tasks').append(task);
  }
  if(!rows.length) $('tasks').append(node('p','无可显示来源，或筛选无匹配。'));
  const policy=batch.resource_policy||{};
  cards($('resources'),[['可用磁盘',size(latest.disk?.free)],['磁盘底线',size(batch.disk_floor_bytes)],['本地 PDF 槽容量',policy.local_pdf_capacity],['视觉模型槽容量',policy.visual_model_capacity],['当前槽占用',latest.slot_occupancy],['缓存候选',null]]);
  $('observed').textContent='服务采样：'+latest.observed_at+' · #'+latest.sequence;
}
function disconnected(){ $('connection').textContent=latest?'连接中断 · 最后快照（非实时）':'连接中断 · Unknown'; $('connection').className='amber'; }
function connect(){
  if(stream) stream.close();
  lastSeen=Date.now(); $('connection').textContent=latest?'正在重连 · 最后快照（非实时）':'正在连接 · Unknown';
  const current=new EventSource('/api/events'); stream=current;
  stream.onmessage=event=>{
    if(current!==stream)return;
    try { const snapshot=JSON.parse(event.data); if(snapshot.schema!=='d2md-workbench-r3'||!Array.isArray(snapshot.batches)) throw Error('schema');
      if(latest&&snapshot.epoch===latest.epoch&&snapshot.sequence<latest.sequence)return;
      latest=snapshot; lastSeen=Date.now(); $('connection').textContent='已连接 · 只读'; $('connection').className='green'; render();
    } catch { disconnected(); }
  };
  stream.onerror=()=>{if(current===stream)disconnected();};
}
$('batch').addEventListener('change',()=>{preferredBatch=$('batch').value;try{sessionStorage.setItem('d2md.batch',preferredBatch);}catch{}render();}); $('filter').addEventListener('change',render); $('search').addEventListener('input',render); $('reconnect').addEventListener('click',connect);
for(const button of document.querySelectorAll('[data-tab]')) button.addEventListener('click',()=>{ for(const b of document.querySelectorAll('[data-tab]')) { const current=b===button; b.setAttribute('aria-selected',String(current)); $(b.dataset.tab).hidden=!current; } });
setInterval(()=>{ if(lastSeen&&Date.now()-lastSeen>12000){ disconnected(); connect(); } },2000);
window.addEventListener('pagehide',()=>{ if(stream)stream.close(); }); window.addEventListener('pageshow',e=>{ if(e.persisted)connect(); });
connect();
