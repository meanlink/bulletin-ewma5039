import {jobs} from '../api/entities.js';
import {workspace} from '../stores/workspace.js';
import {session} from '../stores/session.js';
import {heading} from '../components/common/shell.js';
import {chart} from '../components/common/chart.js';
import {table} from '../components/common/table.js';
import {status} from '../components/common/status.js';
import {esc,fmt,date,notify} from '../utils/dom.js';

const CHANNEL_COLUMNS=[
 {key:'channel',label:'播出链路'},{key:'state',label:'状态'},
 {key:'baseline',label:'固定基准'},{key:'monitored_days',label:'监测日数'},
 {key:'anomaly_days',label:'越限日数'},{key:'event_count',label:'连续事件数'},
 {key:'note',label:'说明'}
];

function channelRows(r){
 return (r.channels||[]).map(c=>c.status==='completed'
  ?{channel:c.channel,state:'已完成',baseline:(c.baseline?.start||'—')+' 至 '+(c.baseline?.end||'—'),monitored_days:c.monitored_days,anomaly_days:c.anomaly_days,event_count:c.event_count,note:'独立基准 μ='+fmt(c.baseline?.mean)+'，σ='+fmt(c.baseline?.sigma)}
  :{channel:c.channel||'（未标注链路）',state:'失败',baseline:'—',monitored_days:0,anomaly_days:0,event_count:0,note:c.error||'分析失败'});
}

function failurePanel(r){
 const failures=r.failures||[];
 if(!failures.length)return '';
 return '<section class="panel"><div class="section-title"><h2>失败链路与原因</h2><span>'+failures.length+' 条失败 · 其余链路照常出结果</span></div><ul class="failure-list">'+failures.map(f=>'<li><strong>'+esc(f.channel||'（未标注链路）')+'</strong><span>'+esc(f.reason)+'</span></li>').join('')+'</ul></section>';
}

function parameterTrace(j){
 return '<pre>'+esc(JSON.stringify(j.params,null,2))+'</pre>';
}

export async function results(id){
 workspace.jobs=await jobs.list();const j=id?await jobs.get(id):(workspace.jobs.find(j=>j.id===workspace.selectedJob)||workspace.jobs[0]);
 if(j&&!workspace.jobs.some(x=>x.id===j.id))workspace.jobs.push(j);
 if(!j)return heading('04 / REVIEW','结果复核','尚无分析结果。')+'<div class="empty">先到分析工作台，选择批次并提交一次计算。<a href="#/workbench">前往工作台 ↗</a></div>';
 workspace.selectedJob=j.id;const r=j.result||{};
 const eventTotal=r.event_total??(r.events||[]).length;
 const anomalyTotal=r.anomaly_total??(r.anomaly_table?.rows||[]).length;
 const hasDetail=!!r.anomaly_table;
 const lead='<section class="result-lead"><p class="eyebrow">COMPUTED FINDING</p><h2>'+esc(r.summary)+'</h2><div class="metrics">'+(r.metrics||[]).map(m=>'<div><span>'+esc(m.label)+'</span><strong>'+esc(fmt(m.value))+'<small>'+esc(m.unit||'')+'</small></strong></div>').join('')+'</div></section>';
 const events='<section class="panel"><div class="section-title"><h2>连续异常事件</h2><span>'+eventTotal+' 个事件 · 本表 '+((r.table?.rows||[]).length)+' 行，与分发文件逐行对账</span></div>'+table(r.table?.columns||[],r.table?.rows||[],'本次未检测到连续异常事件；各链路基准与参数仍应留存。')+'</section>';
 const anomalies='<section class="panel"><div class="section-title"><h2>越限日完整明细</h2><span>'+anomalyTotal+' 个越限日 · 本表 '+((r.anomaly_table?.rows||[]).length)+' 行，不截断</span></div>'+table(r.anomaly_table?.columns||[],r.anomaly_table?.rows||[],'本次未检测到越限日。')+'</section>';
 const legacy='<section class="panel"><div class="section-title"><h2>越限日明细（历史结果）</h2><span>'+((r.table?.rows||[]).length)+' 行 · 旧版结果可能只保留最近条目</span></div><p class="hint">该结果由旧版单链路口径生成，不含连续事件划分；如需完整事件与全量明细，请按当前口径重跑。</p>'+table(r.table?.columns||[],r.table?.rows||[])+'</section>';
 const reconciliation='<section class="panel reconciliation"><div class="section-title"><h2>对账口径</h2><span>页面与分发文件共用同一计算结果</span></div><div class="reconcile-grid"><div><span>连续异常事件</span><strong>'+eventTotal+'</strong><small>分发文件“记录类型=事件”行数</small></div><div><span>越限日</span><strong>'+anomalyTotal+'</strong><small>分发文件“记录类型=越限日”行数</small></div><div><span>失败链路</span><strong>'+((r.failures||[]).length)+'</strong><small>分发文件“记录类型=失败链路”行数</small></div></div><p class="hint">重跑同一批次与参数会得到相同事件划分、起止日与峰值偏离；分发文件包含全部事件、越限日与失败原因，不截断。</p></section>';
 return heading('04 / REVIEW','结果复核与业务解释',j.name,'<button class="button" id="refresh-results">刷新结果 ↻</button>')+'<div class="result-selector"><label>分析记录<select id="select-job">'+workspace.jobs.map(x=>'<option value="'+x.id+'" '+(x.id===j.id?'selected':'')+'>'+esc(x.name)+' · '+esc(x.status)+'</option>').join('')+'</select></label>'+status(j.status)+'<span>'+date(j.created_at)+'</span><span class="approval">'+(j.approved?'已获业务批准':'待负责人复核')+'</span></div>'+(j.status==='completed'?lead+'<section class="panel">'+chart(r.chart)+'</section>'+reconciliation+(hasDetail?events+anomalies:legacy)+'<section class="panel"><div class="section-title"><h2>各链路独立状态</h2><span>'+((r.channels||[]).length)+' 条链路 · 各自最初30日为基准</span></div>'+table(CHANNEL_COLUMNS,channelRows(r))+'</section>'+failurePanel(r)+'<div class="two-columns"><section class="panel"><div class="section-title"><h2>业务解释</h2><span>由计算结果生成</span></div><ol class="questions">'+(r.findings||[]).map(v=>'<li>'+esc(v)+'</li>').join('')+'</ol>'+(r.warnings||[]).map(v=>'<p class="warning">'+esc(v)+'</p>').join('')+'</section><section class="method-sheet compact"><p class="eyebrow">REPRODUCIBILITY</p><h3>计算口径与参数</h3><p>'+esc(r.method)+'</p>'+parameterTrace(j)+(session.user.role==='admin'&&!j.approved?'<button class="primary" id="approve-job">批准此业务结论</button>':'')+'<p class="hint">仅批准结果可进入定时分发档案。</p></section></div>':'<section class="panel empty">'+(j.status==='failed'?'计算失败：'+esc(j.error)+'。请返回工作台调整参数或数据。':'后台正在执行计算。点击刷新结果查看最新进展。')+'<a href="#/workbench">返回工作台 ↗</a></section>');
}
export function bindResults(refresh){document.querySelector('#refresh-results')?.addEventListener('click',refresh);document.querySelector('#select-job')?.addEventListener('change',e=>{workspace.selectedJob=Number(e.target.value);location.hash='#/results/'+e.target.value;});document.querySelector('#approve-job')?.addEventListener('click',async()=>{try{await jobs.approve(workspace.selectedJob);notify('已批准，可创建分发计划');await refresh();}catch(e){notify(e.message,true);}});}
