const $ = id => document.getElementById(id);
const fmt = (x,d=2) => Number(x||0).toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d});
const signed = n => (n>0?'+':'')+fmt(n);
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
function drawCombos(items){const tbody=$('combos').querySelector('tbody');tbody.replaceChildren();if(!items.length){let r=document.createElement('tr');let c=document.createElement('td');c.colSpan=4;c.textContent='Chưa có dữ liệu Combo';r.append(c);tbody.append(r)}items.sort((a,b)=>b['PnL ròng USDT']-a['PnL ròng USDT']).forEach(x=>{let r=document.createElement('tr');cell(r,x.Combo);cell(r,String(x['Số lệnh']));cell(r,fmt(x['Win rate %'])+'%');cell(r,signed(x['PnL ròng USDT']),shade(x['PnL ròng USDT']));tbody.append(r)})}
function drawTrades(items){const root=$('trades');root.replaceChildren();if(!items.length){root.innerHTML='<div class="empty">Chưa có vị thế đã đóng</div>';return;}
items.slice(0,30).forEach(x=>{let card=document.createElement('div');card.className='trade';let coin=document.createElement('div');coin.className='coin';coin.textContent=(x.symbol||'').split('-')[0];card.append(coin);let body=document.createElement('div');body.className='trade-body';let title=document.createElement('div');title.className='trade-title';title.textContent=(x.combo||'UNMAPPED')+' · '+x.side+(x.liquidated?' · ⚠ Thanh lý':'');let sub=document.createElement('div');sub.className='trade-sub';sub.textContent=(x.exit_ms?new Date(x.exit_ms).toLocaleString('vi-VN'):'')+' · '+(x.exit_types||'KHÔNG RÕ');body.append(title,sub);card.append(body);let pnl=document.createElement('div');pnl.className='trade-pnl '+shade(x.pnl);pnl.textContent=signed(x.pnl);card.append(pnl);root.append(card)})}
async function refresh(){set('updated','Đang tải...');try{let params=new URLSearchParams();if($('symbol').value)params.set('symbol',$('symbol').value);if($('combo').value)params.set('combo',$('combo').value);
const r=await fetch('/api/dashboard?'+params.toString(),{credentials:'same-origin',cache:'no-store'});if(r.status===401){location.href='/login';return}if(!r.ok)throw Error('API HTTP '+r.status);const d=await r.json();
fillSelect('symbol',d.filters.symbols);fillSelect('combo',d.filters.combos);
set('pnl',signed(d.summary.pnl_usdt),shade(d.summary.pnl_usdt));set('win',fmt(d.summary.win_rate)+'%');set('closed',String(d.summary.closed));set('unmapped',String(d.summary.unmapped));set('liqs',String(d.summary.liquidations));set('open',String(d.summary.open_positions));buildChart(d.equity);drawCombos(d.combos);drawTrades(d.trades);
set('sync',d.last_sync?'Đồng bộ: '+d.last_sync.status+' · '+(d.last_sync.time||'đang chạy'):'Chưa có lần đồng bộ');set('updated',new Date().toLocaleTimeString('vi-VN'));
}catch(e){set('updated','Lỗi tải dữ liệu');set('sync','Không kết nối được cơ sở dữ liệu. Kiểm tra Render/Supabase.')}}
$('reload').addEventListener('click',refresh);$('symbol').addEventListener('change',refresh);$('combo').addEventListener('change',refresh);refresh();setInterval(refresh,60000);
if('serviceWorker' in navigator)navigator.serviceWorker.register('/static/sw.js').catch(()=>{});
