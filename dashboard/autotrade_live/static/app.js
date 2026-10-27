// Read-only execution report. Never infer an independent strategy PnL from
// a shared BingX position. All headline results use confirmed exit order IDs.
const $ = id => document.getElementById(id);
const num = x => Number(x || 0);
const fmt = (x,d=2) => num(x).toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d});
const signed = n => (num(n)>0?'+':'')+fmt(n);
const cls = n => num(n)>0?'positive':num(n)<0?'negative':'';
const dateVN = ms => new Date(ms).toLocaleString('vi-VN',{timeZone:'Asia/Ho_Chi_Minh',day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'});
const set = (id,value,color='') => {
  const el=$(id);
  if(!el)return;
  el.textContent=value;
  el.classList.remove('positive','negative');
  if(color)el.classList.add(color);
};
function fillSelect(id,items){
  const el=$(id),old=el.value;
  while(el.options.length>1)el.remove(1);
  (items||[]).forEach(v=>{
    const o=document.createElement('option');o.value=v;o.textContent=v;
    el.append(o);
  });
  el.value=items.includes(old)?old:'';
}
function node(tag,css,text){
  const el=document.createElement(tag);
  if(css)el.className=css;
  if(text!==undefined)el.textContent=text;
  return el;
}
function chart(points){
  const target=$('chart');target.replaceChildren();
  if(!points||!points.length){
    target.append(node('div','empty','Chưa có lệnh thoát xác nhận để vẽ đường PnL.'));
    return;
  }
  const svgNS='http://www.w3.org/2000/svg';
  const svg=document.createElementNS(svgNS,'svg');
  svg.setAttribute('viewBox','0 0 720 210');
  svg.setAttribute('role','img');
  svg.setAttribute('aria-label','PnL cộng dồn theo từng lệnh thoát đã khớp ID');
  svg.setAttribute('preserveAspectRatio','none');
  // Include a real initial zero point; avoid depicting a single result as a
  // full trading trend and handle negative-only PnL.
  const values=[0,...points.map(p=>num(p.v))];
  const lo=Math.min(...values), hi=Math.max(...values);
  const spread=Math.max(0.01,hi-lo);
  const low=lo-spread*.12,high=hi+spread*.12;
  const height=high-low;
  const xx=i=>42+i/Math.max(1,values.length-1)*660;
  const yy=v=>178-(v-low)/height*153;
  for(let i=0;i<4;i++){
    const v=low+i/3*(high-low);
    const y=yy(v);
    const line=document.createElementNS(svgNS,'line');
    [['x1',42],['x2',704],['y1',y],['y2',y],['stroke','#2a4559'],['stroke-width',1]].forEach(p=>line.setAttribute(p[0],p[1]));
    svg.append(line);
  }
  const zero=document.createElementNS(svgNS,'line');
  [['x1',42],['x2',704],['y1',yy(0)],['y2',yy(0)],['stroke','#7497a1'],['stroke-dasharray','5 5'],['stroke-width','1.1']].forEach(p=>zero.setAttribute(p[0],p[1]));
  svg.append(zero);
  const path=document.createElementNS(svgNS,'path');
  path.setAttribute('d',values.map((v,i)=>(i?'L':'M')+xx(i).toFixed(2)+','+yy(v).toFixed(2)).join(' '));
  path.setAttribute('fill','none');
  const color=values[values.length-1]>=0?'#5de5b9':'#ff8d98';
  path.setAttribute('stroke',color);
  path.setAttribute('stroke-width','3');
  path.setAttribute('stroke-linecap','round');
  path.setAttribute('stroke-linejoin','round');
  path.setAttribute('vector-effect','non-scaling-stroke');
  svg.append(path);
  for(const [label,value,y] of [['max',high,27],['min',low,183]]){
    const tx=document.createElementNS(svgNS,'text');
    tx.setAttribute('x','2');tx.setAttribute('y',y);tx.textContent=fmt(value,2);
    svg.append(tx);
  }
  const tip=document.createElementNS(svgNS,'circle');
  tip.setAttribute('cx',xx(values.length-1));tip.setAttribute('cy',yy(values[values.length-1]));
  tip.setAttribute('r','4');tip.setAttribute('fill',color);svg.append(tip);
  target.append(svg);
}
function showCombos(items){
  const root=$('combo-list');root.replaceChildren();
  const rows=Array.isArray(items)?items:[];
  set('combo-count',rows.length+' Combo');
  if(!rows.length){
    root.append(node('div','empty','Chưa có Combo có lệnh thoát được xác nhận.'));
    return;
  }
  rows.forEach(item=>{
    const row=node('div','combo-row');
    row.append(node('div','combo-title',item.combo||'—'));
    const mid=node('div','combo-mid');
    mid.append(node('div','combo-meta',item.count+' lệnh thoát · thắng '+fmt(item.win_rate,0)+'%'));
    const kinds=[];
    if(num(item.TP))kinds.push('TP '+item.TP);
    if(num(item.SL))kinds.push('SL '+item.SL);
    if(num(item.TRAILING))kinds.push('Trailing '+item.TRAILING);
    mid.append(node('div','combo-sub',kinds.join(' · ')||'Không có loại thoát'));
    row.append(mid);
    row.append(node('div','combo-pnl '+cls(item.net_after_exit_fee),signed(item.net_after_exit_fee)));
    root.append(row);
  });
}
function showHistory(items,summary){
  const root=$('exit-list');root.replaceChildren();
  const rows=Array.isArray(items)?items:[];
  const count=num(summary?.matched_by_id),missing=num(summary?.unmapped_or_ambiguous);
  set('exitstatus',missing?count+' xác nhận · '+missing+' cần kiểm tra':count+' đã xác nhận');
  if(!rows.length){root.append(node('div','empty','Chưa có lệnh thoát trong kỳ báo cáo.'));return;}
  const names={SL:'SL',TP:'TP',TRAILING:'Trailing',LIQUIDATION:'Thanh lý'};
  rows.slice(0,30).forEach(item=>{
    const row=node('div','exit-row');
    const left=node('div','exit-main');
    const heading=node('div','exit-heading');
    const kind=item.type||'UNKNOWN';
    heading.append(node('span','',item.combo||'Chưa xác định'));
    heading.append(node('span','',item.symbol?.replace('-USDT','')||''));
    heading.append(node('span','outcome '+(kind==='SL'?'sl':kind==='TP'?'tp':kind==='TRAILING'?'trailing':'unknown'),names[kind]||kind));
    left.append(heading);
    left.append(node('div','exit-time',dateVN(item.time_ms)+(item.combo?'':' · chưa khớp Combo')));
    row.append(left);
    const right=node('div','exit-result');
    const value=num(item.bingx_realized_pnl)+num(item.exit_fees);
    right.append(node('div','exit-pnl '+cls(value),(value>0?'+':'')+fmt(value)+' USDT'));
    right.append(node('div','exit-fee',item.confidence==='PRICE_TIME_CANDIDATE'?'Ứng viên, chưa xác nhận':item.combo?'Sau phí thoát':'Chưa xác nhận Combo'));
    row.append(right);
    root.append(row);
  });
}
function showRisk(items){
  const root=$('riskPanel'),container=$('riskItems');
  const rows=Array.isArray(items)?items:[];
  root.hidden=!rows.length;container.replaceChildren();
  if(!rows.length)return;
  set('riskCount',rows.length+' cảnh báo cần kiểm tra');
  const labels={
    LEGACY_SL_REPLACEMENT_UNVERIFIED:'ID thay SL cũ chưa được hệ thống cleanup xác minh hoàn toàn',
    EXIT_HISTORY_INCOMPLETE_OPEN_POSITION:'Lịch sử lệnh bảo vệ chưa đầy đủ',
    NO_TRACKED_ACTIVE_SL:'Chưa xác nhận được SL đang hoạt động',
    SL_OVER_REMAINING:'Khối lượng SL vượt phần vị thế còn lại',
    CANCEL_RETRY_EXHAUSTED:'Đã hết giới hạn thử hủy lệnh'
  };
  rows.forEach(row=>container.append(node('div','risk-item',(row.combo||'—')+' · '+(row.symbol||'')+': '+(labels[row.reason]||row.reason))));
}
function fillAdvanced(d){
  const s=d.summary||{},e=d.exit_summary||{};
  set('pnl',signed(s.pnl_usdt),cls(s.pnl_usdt));
  set('pnlpct',s.pnl_pct==null?'—':fmt(s.pnl_pct)+'%',cls(s.pnl_pct));
  set('closed',String(s.closed||0));
  set('unmapped',String(s.unmapped||0));
  set('open',String(s.open_positions||0));
  set('margin',s.margin_used_usdt==null?'—':fmt(s.margin_used_usdt));
  set('matchdetail','Khớp '+num(e.matched_by_id)+' lệnh thoát theo ID · '+num(e.matched_by_trigger)+
    ' qua triggerOrderId · '+num(e.matched_by_legacy_client)+' qua ID thay SL cũ · '+
    num(e.unmapped_or_ambiguous)+' chưa xác định. Không phân bổ lại PnL của vị thế chung.');
}
let fetching=false;
async function refresh(){
  if(fetching)return;
  fetching=true;
  const button=$('reload');button.disabled=true;
  try{
    const params=new URLSearchParams();
    if($('symbol').value)params.set('symbol',$('symbol').value);
    if($('combo').value)params.set('combo',$('combo').value);
    const response=await fetch('/api/dashboard?'+params,{credentials:'same-origin',cache:'no-store'});
    if(response.status===401){location.href='/login';return;}
    if(!response.ok)throw Error('API HTTP '+response.status);
    const d=await response.json();
    fillSelect('symbol',d.filters.symbols);
    fillSelect('combo',d.filters.combos);
    const p=d.exit_performance||{};
    set('net',p.count?signed(p.pnl_usdt):'—',cls(p.pnl_usdt));
    set('count',String(p.count||0));
    set('winrate',p.win_rate==null?'—':fmt(p.win_rate,1)+'%');
    set('wins',num(p.wins)+' thắng · '+num(p.losses)+' thua'+(num(p.breakeven)?' · '+p.breakeven+' hòa vốn':''));
    set('pf',p.profit_factor==null?'—':fmt(p.profit_factor,2));
    chart(p.curve);
    showCombos(d.combo_exits);
    showHistory(d.exit_events,d.exit_summary);
    showRisk(d.cleanup_risks);
    fillAdvanced(d);
    set('baseline',d.start_at?'Lệnh thoát từ '+dateVN(new Date(d.start_at).getTime())+' · Dữ liệu BingX đã lưu':'Dữ liệu thoát lệnh BingX đã lưu');
    const syncTime=d.last_sync?.time?new Date(d.last_sync.time).getTime():0;
    const stale=!syncTime||Date.now()-syncTime>2.5*3600000||d.last_sync?.status!=='success';
    set('sync',syncTime?'Đồng bộ '+dateVN(syncTime):'Chưa đồng bộ');
    const alert=$('sync-alert');alert.hidden=!stale;
    if(stale)alert.textContent='Dữ liệu BingX có thể đã cũ. Kết quả chỉ cập nhật sau lần đồng bộ thành công.';
    set('updated',new Date().toLocaleTimeString('vi-VN',{hour:'2-digit',minute:'2-digit'}));
  }catch(error){
    const alert=$('sync-alert');alert.hidden=false;
    alert.textContent='Không tải được dữ liệu Dashboard. Vui lòng thử cập nhật.';
    set('updated','Lỗi dữ liệu');
  }finally{
    fetching=false;button.disabled=false;
  }
}
$('reload').addEventListener('click',refresh);
$('symbol').addEventListener('change',refresh);
$('combo').addEventListener('change',refresh);
refresh();
setInterval(refresh,60000);
if('serviceWorker' in navigator)navigator.serviceWorker.register('/static/sw.js').catch(()=>{});
