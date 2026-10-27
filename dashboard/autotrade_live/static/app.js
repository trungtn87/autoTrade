// V3: strictly read-only theoretical strategy monitor.
const $=id=>document.getElementById(id);
const n=v=>Number(v||0);
const fmt=(v,d=2)=>Number(v).toLocaleString('vi-VN',{minimumFractionDigits:d,maximumFractionDigits:d});
const sign=v=>(n(v)>0?'+':'')+fmt(v)+'%';
const cls=v=>n(v)>0?'positive':n(v)<0?'negative':'';
const timeVN=ms=>new Date(ms).toLocaleString('vi-VN',{timeZone:'Asia/Ho_Chi_Minh',day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'});
function set(id,value){let e=$(id);if(e)e.textContent=value}
function element(tag,klass,value){let e=document.createElement(tag);if(klass)e.className=klass;if(value!==undefined)e.textContent=value;return e}
function selectCombos(items){let e=$('combo'),old=e.value;e.replaceChildren(new Option('Tất cả Combo',''));(items||[]).forEach(s=>e.add(new Option(s,s)));e.value=(items||[]).includes(old)?old:''}
function combos(rows){
 const root=$('combo-list');root.replaceChildren();set('combo-count',rows.length+' Combo');
 if(!rows.length){root.append(element('div','empty','Chưa có tín hiệu trong kỳ lọc.'));return}
 for(let r of rows){
   const card=element('article','strategy-card');
   const head=element('div','strategy-head');
   head.append(element('strong','',r.symbol.replace('-USDT','')+' · '+r.combo));
   head.append(element('strong','strategy-roi '+cls(r.roi_total),(r.closed?sign(r.roi_total):'—')));
   card.append(head);
   const stat=element('div','strategy-stats');
   for(let [label,value] of [['Lệnh',r.closed+'/'+r.signals],['Win',r.win_rate==null?'—':fmt(r.win_rate,1)+'%'],['RR',r.rr==null?'—':fmt(r.rr)],['ROI/lệnh',r.roi_avg==null?'—':sign(r.roi_avg)]]) {
     const tile=element('div');tile.append(element('small','',label),element('strong','',value));stat.append(tile)
   }
   card.append(stat);
   card.append(element('div','strategy-foot','TP '+r.tp_hits+' · Trailing '+r.trailing+' · SL/âm '+r.loss+' · Chưa đủ dữ liệu '+r.unresolved));
   root.append(card);
 }
}
const reasons={ENTRY_FAILED:'Entry thất bại',ENTRY_UNKNOWN:'Entry chưa xác định',PROTECTION_UNVERIFIED:'Thiếu ID TP/SL/Trailing'};
function reliability(d){
 if(!d.available){set('entryrate','—');set('botstatus','Không có dữ liệu');set('coverage',d.reason||'Chưa có dữ liệu V2');return}
 const s=d.summary||{};set('entryrate',s.entry_rate==null?'—':fmt(s.entry_rate,1)+'%');
 $('entrybar').style.width=(s.entry_rate||0)+'%';
 for(let key of ['signals','confirmed','failed','pending','unprotected'])set(key,s[key]??'—');
 set('coverage',d.coverage);
 const bad=n(s.failed)+n(s.pending)+n(s.unprotected);
 set('botstatus',bad?bad+' vấn đề đã ghi':'Không phát hiện lỗi đã ghi');
}
function advanced(d){
 const s=d.summary||{},x=d.exit_summary||{};
 set('pnl',fmt(s.pnl_usdt)+' USDT');set('pnlpct',s.pnl_pct==null?'—':fmt(s.pnl_pct)+'%');
 set('closed',s.closed??'—');set('unmapped',s.unmapped??'—');
 set('open',s.open_positions??'—');set('margin',fmt(s.margin_used_usdt));
 set('matchdetail','Thoát khớp ID: '+n(x.matched_by_id)+' · Chưa xác định: '+n(x.unmapped_or_ambiguous));
 set('exitstatus',n(x.matched_by_id)+' khớp ID');
 const root=$('exit-list');root.replaceChildren();
 (d.exit_events||[]).slice(0,15).forEach(e=>root.append(element('div','exit-row',timeVN(e.time_ms)+' · '+(e.combo||'Không rõ')+' · '+e.type+' · '+fmt(n(e.bingx_realized_pnl)+n(e.exit_fees))+' USDT')))
}
let busy=false;
async function refresh(){
 if(busy)return;busy=true;
 try{
   const p=new URLSearchParams();
   if($('symbol').value)p.set('symbol',$('symbol').value);
   if($('combo').value)p.set('combo',$('combo').value);
   p.set('days',$('period').value);
   const res=await fetch('/api/dashboard?'+p.toString(),{credentials:'same-origin',cache:'no-store'});
   if(res.status===401){location.href='/login';return}
   if(!res.ok)throw Error('API '+res.status);
   const d=await res.json(),t=d.theory||{};
   const s=t.summary||{};
   selectCombos(d.filters?.combos||[]);
   set('net',s.closed?sign(s.roi_total):'—');
   $('net').className='hero-value '+cls(s.roi_total);
   set('roi-note',(s.closed||0)+' lệnh đã xác định · ROI/lệnh '+(s.roi_avg==null?'—':sign(s.roi_avg)));
   set('winrate',s.win_rate==null?'—':fmt(s.win_rate,1)+'%');
   set('wins',(s.win||0)+' thắng · '+(s.loss||0)+' thua · '+(s.flat||0)+' hòa');
   set('pf',s.rr==null?'—':fmt(s.rr));
   set('count',s.closed??'—');set('roiavg',s.roi_avg==null?'—':sign(s.roi_avg));
   set('unknown',(s.unresolved||0)+' tín hiệu chưa đủ dữ liệu thoát');
   combos(t.combos||[]);reliability(t);advanced(d);
   const sync=d.last_sync?.time?Date.parse(d.last_sync.time):0;
   const stale=!sync||Date.now()-sync>2.5*3600000||d.last_sync?.status!=='success';
   set('sync',sync?'Đồng bộ BingX '+timeVN(sync):'Chưa xác định lần đồng bộ');
   $('sync-alert').hidden=!stale;
   if(stale)$('sync-alert').textContent='Dữ liệu BingX chưa được đồng bộ gần đây. ROI và kết quả thoát có thể chưa cập nhật.';
   set('updated',new Date().toLocaleTimeString('vi-VN',{hour:'2-digit',minute:'2-digit'}));
   set('baseline','ROI biến động giá, không tính phí/funding/đòn bẩy · Từ '+(d.theory_since||'đầu kỳ dữ liệu'));
 }catch(e){$('sync-alert').hidden=false;$('sync-alert').textContent='Không đọc được báo cáo: dữ liệu đang tạm thời không khả dụng.';set('updated','Lỗi');}
 finally{busy=false}
}
for(const id of ['symbol','period','combo'])$(id).addEventListener('change',refresh);
refresh();
if('serviceWorker'in navigator)navigator.serviceWorker.register('/static/sw.js').catch(()=>{});
