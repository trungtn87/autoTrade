const $ = id => document.getElementById(id);
const fmt = (x,d=2) => Number(x||0).toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d});
const signed = n => (n>0?'+':'')+fmt(n);
const signedPct = n => n == null || !Number.isFinite(Number(n)) ? '—' : (Number(n)>0?'+':'')+fmt(n)+'%';
const shade = n => n>0?'positive':n<0?'negative':'';
function set(id,value,cls=''){const e=$(id);e.textContent=value;e.className=cls;}
function fillSelect(id,items){const s=$(id),old=s.value;while(s.options.length>1)s.remove(1);items.forEach(v=>{const o=document.createElement('option');o.value=v;o.textContent=v;s.append(o)});s.value=items.includes(old)?old:'';}
function cell(row,val,cls=''){let el=document.createElement('td');el.textContent=val;el.className=cls;row.append(el);}
function buildChart(points){const root=$('chart');root.replaceChildren();if(!points.length){root.innerHTML='<div class="empty">Chưa có lịch sử đóng lệnh gắn Combo</div>';return;}
 const values=[0,...points.map(x=>Number(x.v))],min=Math.min(...values),max=Math.max(...values),span=Math.max(1,max-min),n=points.length;
 const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 720 230');svg.setAttribute('preserveAspectRatio','none');
 for(let k=0;k<4;k++){let y=30+k*53;let line=document.createElementNS(svg.namespaceURI,'line');[['x1',45],['x2',706],['y1',y],['y2',y],['stroke','#2a4559'],['stroke-width',1]].forEach(([a,v])=>line.setAttribute(a,v));svg.append(line);}
 const path=document.createElementNS(svg.namespaceURI,'path');const xy=points.map((p,i)=>[45+(i/(Math.max(n-1,1)))*661,190-(p.v-min)/span*160]);path.setAttribute('d',xy.map(([x,y],i)=>(i?'L':'M')+x.toFixed(2)+','+y.toFixed(2)).join(' '));path.setAttribute('fill','none');path.setAttribute('stroke','#55ddba');path.setAttribute('stroke-width','3');path.setAttribute('vector-effect','non-scaling-stroke');svg.append(path);
 for(let i=0;i<2;i++){let tx=document.createElementNS(svg.namespaceURI,'text');tx.setAttribute('x','4');tx.setAttribute('y',i?189:30);tx.textContent=fmt(i?min:max);svg.append(tx)}root.append(svg);
}
function drawCombos(items){const tbody=$('combos').querySelector('tbody');tbody.replaceChildren();if(!items.length){let r=document.createElement('tr');let c=document.createElement('td');c.colSpan=5;c.textContent='Chưa có dữ liệu Combo';r.append(c);tbody.append(r)}items.sort((a,b)=>b['PnL ròng USDT']-a['PnL ròng USDT']).forEach(x=>{let r=document.createElement('tr');cell(r,x.Combo);cell(r,String(x['Số lệnh']));cell(r,fmt(x['Win rate %'])+'%');cell(r,signed(x['PnL ròng USDT']),shade(x['PnL ròng USDT']));cell(r,signedPct(x['PnL %']),shade(x['PnL %']));tbody.append(r)})}
function drawComboExits(items){
  const table=$('comboexits');if(!table)return;
  const tbody=table.querySelector('tbody');tbody.replaceChildren();
  for(const item of (items||[])){
    const tr=document.createElement('tr');
    cell(tr,item.combo);cell(tr,String(item.TP));cell(tr,String(item.SL));
    cell(tr,String(item.TRAILING));cell(tr,signed(item.realized_pnl),shade(item.realized_pnl));
    cell(tr,signed(item.exit_fees),shade(item.exit_fees));tbody.append(tr);
  }
  if(!items||!items.length){const tr=document.createElement('tr');const td=document.createElement('td');td.colSpan=6;td.textContent='Chưa có lệnh thoát xác nhận bằng ID';tr.append(td);tbody.append(tr)}
}
function drawExits(items,summary){
  const body=$('exitfills').querySelector('tbody');body.replaceChildren();
  const safe=Array.isArray(items)?items:[];
  if(!safe.length){let tr=document.createElement('tr');let td=document.createElement('td');
    td.colSpan=6;td.textContent='Chưa có lệnh TP/SL/trailing đã khớp trong lần đồng bộ BingX.';tr.append(td);body.append(tr);
  }else{
    const confidence={ORDER_ID:'ID gốc khớp',TRIGGER_ORDER_ID:'Trigger ID khớp',PRICE_TIME_CANDIDATE:'Ứng viên giờ/giá',
      AMBIGUOUS:'Trùng điều kiện',CONFLICT:'Sai thông tin',UNMAPPED:'Chưa xác định'};
    safe.slice(0,60).forEach(x=>{
      const tr=document.createElement('tr');
      cell(tr,x.time_ms?new Date(x.time_ms).toLocaleString('vi-VN',{timeZone:'Asia/Ho_Chi_Minh'}):'—');
      cell(tr,(x.combo||'Chưa xác định')+' / '+(x.symbol||''));
      cell(tr,x.type||'—');cell(tr,fmt(x.exit_price,2));
      cell(tr,signed(x.bingx_realized_pnl),shade(x.bingx_realized_pnl));
      cell(tr,confidence[x.confidence]||'Chưa xác định');body.append(tr);
    });
  }
  const confirmed=Number(summary.matched_by_id||0),candidates=Number(summary.price_time_candidates||0);
  const missing=Number(summary.unmapped_or_ambiguous||0);
  set('exitstatus','Đối chiếu: '+confirmed+' lệnh khớp ID (gồm '+Number(summary.matched_by_trigger||0)+' trigger ID) · '+candidates+
    ' ứng viên theo giờ/giá · '+missing+' chưa xác định. PnL chỉ là realizedPNL của các lệnh thoát BingX, không phải ROI riêng của Combo.');
}
function drawCleanupRisks(items){
  const el=$('cleanuprisks');if(!el)return;
  const labels={
    LEGACY_SL_REPLACEMENT_UNVERIFIED:'SL thay thế cũ chưa xác minh Order ID; kiểm tra trên BingX',
    EXIT_HISTORY_INCOMPLETE_OPEN_POSITION:'Thiếu lịch sử lệnh điều kiện khi vị thế vẫn mở',
    NO_TRACKED_ACTIVE_SL:'Chưa xác nhận được SL đang hoạt động',
    SL_OVER_REMAINING:'SL lớn hơn khối lượng còn lại',
    CANCEL_RETRY_EXHAUSTED:'Đã hết số lần thử hủy giới hạn',
  };
  if(!items||!items.length){el.textContent='Không có cảnh báo cleanup chưa giải quyết trong dữ liệu đã lưu.';return;}
  el.replaceChildren();
  items.forEach(item=>{
    const p=document.createElement('p');
    p.textContent=item.combo+' / '+item.symbol+': '+(labels[item.reason]||item.reason)+
      ' (trạng thái: '+item.cleanup_status+')';
    el.append(p);
  });
}
function drawTrades(items){const root=$('trades');root.replaceChildren();if(!items.length){root.innerHTML='<div class="empty">Chưa có vị thế đã đóng</div>';return;}
items.slice(0,30).forEach(x=>{let card=document.createElement('div');card.className='trade';let coin=document.createElement('div');coin.className='coin';coin.textContent=(x.symbol||'').split('-')[0];card.append(coin);let body=document.createElement('div');body.className='trade-body';let title=document.createElement('div');title.className='trade-title';title.textContent=(x.combo||'UNMAPPED')+' · '+x.side+(x.liquidated?' · ⚠ Thanh lý':'');let sub=document.createElement('div');sub.className='trade-sub';sub.textContent=(x.exit_ms?new Date(x.exit_ms).toLocaleString('vi-VN'):'')+' · '+(x.exit_types||'KHÔNG RÕ');body.append(title,sub);card.append(body);let pnl=document.createElement('div');pnl.className='trade-pnl '+shade(x.pnl);pnl.textContent=signed(x.pnl);let pct=document.createElement('div');pct.className='trade-pct';pct.textContent=signedPct(x.pnl_pct);pnl.append(pct);card.append(pnl);root.append(card)})}
async function refresh(){set('updated','Đang tải...');try{let params=new URLSearchParams();if($('symbol').value)params.set('symbol',$('symbol').value);if($('combo').value)params.set('combo',$('combo').value);
const r=await fetch('/api/dashboard?'+params.toString(),{credentials:'same-origin',cache:'no-store'});if(r.status===401){location.href='/login';return}if(!r.ok)throw Error('API HTTP '+r.status);const d=await r.json();
fillSelect('symbol',d.filters.symbols);fillSelect('combo',d.filters.combos);
set('pnl',signed(d.summary.pnl_usdt),shade(d.summary.pnl_usdt));set('pnlpct',signedPct(d.summary.pnl_pct),shade(d.summary.pnl_pct));set('margin',d.summary.margin_used_usdt==null?'—':fmt(d.summary.margin_used_usdt));set('win',fmt(d.summary.win_rate)+'%');set('closed',String(d.summary.closed));set('unmapped',String(d.summary.unmapped));set('liqs',String(d.summary.liquidations));set('open',String(d.summary.open_positions));buildChart(d.equity);drawCombos(d.combos);drawTrades(d.trades);drawExits(d.exit_events,d.exit_summary);drawComboExits(d.combo_exits);drawCleanupRisks(d.cleanup_risks);
if($('pnlpct'))set('pnlpct',d.summary.pnl_pct==null?'—':fmt(d.summary.pnl_pct)+'%');
if($('margin'))set('margin',d.summary.margin_used_usdt==null?'—':fmt(d.summary.margin_used_usdt));
if($('baseline'))set('baseline',d.start_at?'Mốc thống kê: '+new Date(d.start_at).toLocaleString('vi-VN',{timeZone:'Asia/Ho_Chi_Minh'}):'Đang hiển thị toàn bộ lịch sử đã thu thập');
set('baseline',d.start_at?'Thống kê chỉ tính vị thế MỞ MỚI từ '+new Date(d.start_at).toLocaleString('vi-VN',{timeZone:'Asia/Ho_Chi_Minh'})+' · Dữ liệu cũ vẫn lưu để tra cứu.':'Tính các lệnh có thể đối soát từ lịch sử.');const synced=d.last_sync&&d.last_sync.time?new Date(d.last_sync.time).getTime():0;
const stale=!synced||(Date.now()-synced)>2.5*3600000;
set('sync',(d.last_sync?'Đồng bộ: '+d.last_sync.status+' · '+(d.last_sync.time?new Date(d.last_sync.time).toLocaleString('vi-VN',{timeZone:'Asia/Ho_Chi_Minh'}):'đang chạy'):'Chưa có lần đồng bộ')+(stale?' · ⚠ Dữ liệu quá hạn 2h30':''));
set('updated',new Date().toLocaleTimeString('vi-VN'));
}catch(e){set('updated','Lỗi tải dữ liệu');set('sync','Không kết nối được cơ sở dữ liệu. Kiểm tra Render/Supabase.')}}
$('reload').addEventListener('click',refresh);$('symbol').addEventListener('change',refresh);$('combo').addEventListener('change',refresh);refresh();setInterval(refresh,60000);
if('serviceWorker' in navigator)navigator.serviceWorker.register('/static/sw.js').catch(()=>{});
