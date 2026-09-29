const DEFAULT_PLACE={name:'Thành phố Cao Bằng',lat:22.66432,lon:106.25826};
const MODEL_CONFIG=[
  {key:'ecmwf',name:'ECMWF',endpoint:'https://api.open-meteo.com/v1/ecmwf'},
  {key:'gfs',name:'GFS',endpoint:'https://api.open-meteo.com/v1/gfs'},
  {key:'cma',name:'CMA',endpoint:'https://api.open-meteo.com/v1/cma'}
];
const HOURLY='temperature_2m,relative_humidity_2m,precipitation,weather_code,wind_speed_10m,wind_direction_10m';
const DAILY='weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,wind_speed_10m_max';
let state={place:loadPlace(),models:{},tab:'hourly'};

const $=s=>document.querySelector(s);
const $$=s=>[...document.querySelectorAll(s)];
const fmt=n=>Number.isFinite(n)?Math.round(n):'--';
const fmt1=n=>Number.isFinite(n)?n.toFixed(1):'--';

function loadPlace(){try{return JSON.parse(localStorage.getItem('cbw_place'))||DEFAULT_PLACE}catch{return DEFAULT_PLACE}}
function savePlace(){localStorage.setItem('cbw_place',JSON.stringify(state.place))}
function codeInfo(code){
  if(code===0)return['☀️','Trời quang'];
  if([1,2].includes(code))return['🌤️','Ít mây'];
  if(code===3)return['☁️','Nhiều mây'];
  if([45,48].includes(code))return['🌫️','Sương mù'];
  if([51,53,55,56,57].includes(code))return['🌦️','Mưa phùn'];
  if([61,63,65,66,67].includes(code))return['🌧️','Mưa'];
  if([71,73,75,77,85,86].includes(code))return['🌨️','Tuyết'];
  if([80,81,82].includes(code))return['🌦️','Mưa rào'];
  if([95,96,99].includes(code))return['⛈️','Dông'];
  return['🌥️','Biến đổi'];
}
function isRain(code,precip){return (Number(precip)||0)>=0.1 || [51,53,55,56,57,61,63,65,66,67,80,81,82,95,96,99].includes(Number(code))}
function compass(deg){const a=['B','ĐB','Đ','ĐN','N','TN','T','TB'];return a[Math.round((Number(deg)||0)/45)%8]}
function localHourLabel(iso){const d=new Date(iso+':00');return d.toLocaleTimeString('vi-VN',{hour:'2-digit',minute:'2-digit'})}
function dayLabel(iso){const d=new Date(iso+'T12:00:00');return d.toLocaleDateString('vi-VN',{weekday:'short',day:'2-digit',month:'2-digit'})}

async function fetchJson(url,timeout=15000){
  const ctrl=new AbortController();const t=setTimeout(()=>ctrl.abort(),timeout);
  try{const r=await fetch(url,{signal:ctrl.signal,cache:'no-store'});if(!r.ok)throw new Error('HTTP '+r.status);return await r.json()}finally{clearTimeout(t)}
}
function apiUrl(endpoint,lat,lon){
  const q=new URLSearchParams({latitude:lat,longitude:lon,hourly:HOURLY,daily:DAILY,timezone:'Asia/Ho_Chi_Minh',forecast_days:'10',wind_speed_unit:'kmh'});
  return endpoint+'?'+q.toString();
}
async function loadWeather(){
  showStatus('Đang cập nhật dữ liệu từ 3 mô hình…',true);
  $('#placeLabel').textContent=state.place.name;
  const settled=await Promise.allSettled(MODEL_CONFIG.map(async m=>({m,data:await fetchJson(apiUrl(m.endpoint,state.place.lat,state.place.lon))})));
  state.models={};let errors=[];
  settled.forEach((x,i)=>{if(x.status==='fulfilled')state.models[x.value.m.key]=x.value.data;else errors.push(MODEL_CONFIG[i].name)});
  if(Object.keys(state.models).length===0){showStatus('Không tải được dữ liệu. Kiểm tra kết nối mạng rồi thử lại.');return}
  if(errors.length)showStatus('Tạm thời chưa lấy được: '+errors.join(', ')+'. Các nguồn còn lại vẫn hiển thị.');else hideStatus();
  localStorage.setItem('cbw_cache',JSON.stringify({time:Date.now(),place:state.place,models:state.models}));
  renderAll();
  $('#updatedAt').textContent='Cập nhật '+new Date().toLocaleTimeString('vi-VN',{hour:'2-digit',minute:'2-digit'});
}
function currentIndex(data){
  const now=new Date();let best=0,bestDiff=Infinity;
  (data.hourly?.time||[]).forEach((t,i)=>{const diff=Math.abs(new Date(t+':00')-now);if(diff<bestDiff){bestDiff=diff;best=i}});return best;
}
function renderAll(){renderModelCards();renderConsensus();renderHourly();renderDaily()}
function renderModelCards(){
  $('#modelCards').innerHTML=MODEL_CONFIG.map(m=>{
    const d=state.models[m.key];if(!d)return `<article class="model-card card"><div class="top"><span class="model-name">${m.name}</span><span>⚠️</span></div><div class="metrics">Không có dữ liệu</div></article>`;
    const i=currentIndex(d),h=d.hourly;const [ico,desc]=codeInfo(h.weather_code[i]);
    return `<article class="model-card card"><div><div class="top"><span class="model-name">${m.name}</span><span class="weather-icon">${ico}</span></div><div class="temp">${fmt(h.temperature_2m[i])}°C</div><div class="muted">${desc}</div></div><div class="metrics">💧 Ẩm ${fmt(h.relative_humidity_2m[i])}%<br>🌧️ Mưa ${fmt1(h.precipitation[i])} mm<br>💨 ${fmt(h.wind_speed_10m[i])} km/h · ${compass(h.wind_direction_10m[i])}</div></article>`
  }).join('');
}
function renderConsensus(){
  const rain=[];const temps=[];
  MODEL_CONFIG.forEach(m=>{const d=state.models[m.key];if(!d)return;const i=currentIndex(d),h=d.hourly;rain.push(isRain(h.weather_code[i],h.precipitation[i]));temps.push(h.temperature_2m[i])});
  const n=rain.filter(Boolean).length,total=rain.length;$('#consensusBadge').textContent=`${n}/${total||3}`;
  let title='Chưa đủ dữ liệu',text='Không thể kết luận từ các nguồn hiện có.';
  if(total){if(n===total){title='Các mô hình cùng nghiêng về mưa';text='Mức đồng thuận cao. Nên chú ý mưa trong khung giờ hiện tại.'}else if(n>=2){title='Khả năng mưa đáng chú ý';text='Phần lớn mô hình đang cho tín hiệu mưa.'}else if(n===1){title='Dự báo chưa đồng thuận';text='Chỉ một mô hình cho tín hiệu mưa, nên theo dõi cập nhật tiếp theo.'}else{title='Các mô hình cùng nghiêng về khô ráo';text='Hiện chưa thấy tín hiệu mưa rõ ở cả ba mô hình.'}}
  if(temps.length){text+=` Nhiệt độ trung bình mô hình khoảng ${fmt(temps.reduce((a,b)=>a+b,0)/temps.length)}°C.`}
  $('#consensusTitle').textContent=title;$('#consensusText').textContent=text;
}
function alignedRows(){
  const ds=MODEL_CONFIG.map(m=>state.models[m.key]).filter(Boolean);if(!ds.length)return[];
  const base=ds[0].hourly.time;const now=Date.now();let start=base.findIndex(t=>new Date(t+':00').getTime()>=now-30*60*1000);if(start<0)start=0;
  const rows=[];for(let i=start;i<Math.min(base.length,start+26);i+=2)rows.push(base[i]);return rows;
}
function getHour(d,time){if(!d)return null;const i=d.hourly.time.indexOf(time);if(i<0)return null;const h=d.hourly;return{temp:h.temperature_2m[i],rain:h.precipitation[i],code:h.weather_code[i]}}
function modelCell(x){if(!x)return'<span class="muted">--</span>';const [ico]=codeInfo(x.code);return `<div class="cell-main">${ico} ${fmt(x.temp)}°</div><div class="cell-sub">${fmt1(x.rain)} mm</div>`}
function renderHourly(){
  $('#hourlyBody').innerHTML=alignedRows().map(t=>{const vals=MODEL_CONFIG.map(m=>getHour(state.models[m.key],t));const valid=vals.filter(Boolean),n=valid.filter(v=>isRain(v.code,v.rain)).length;let c=n>=2?'🌧️ Nghiêng mưa':n===1?'🌦️ Chưa rõ':'☀️ Nghiêng khô';return `<tr><td><strong>${localHourLabel(t)}</strong></td>${vals.map(v=>`<td>${modelCell(v)}</td>`).join('')}<td><div class="cell-main">${c}</div><div class="cell-sub">${n}/${valid.length} nguồn báo mưa</div></td></tr>`}).join('');
}
function dailyAt(d,idx){if(!d?.daily)return null;const x=d.daily;return{date:x.time[idx],max:x.temperature_2m_max[idx],min:x.temperature_2m_min[idx],rain:x.precipitation_sum[idx],code:x.weather_code[idx],wind:x.wind_speed_10m_max[idx]}}
function renderDaily(){
  const ref=MODEL_CONFIG.map(m=>state.models[m.key]).find(Boolean);if(!ref)return;const count=Math.min(10,ref.daily.time.length);let html='';
  for(let i=0;i<count;i++){const vals=MODEL_CONFIG.map(m=>dailyAt(state.models[m.key],i));const valid=vals.filter(Boolean),n=valid.filter(v=>isRain(v.code,v.rain)).length;html+=`<article class="daily-card card"><div class="daily-head"><span class="daily-date">${dayLabel(ref.daily.time[i])}</span><span class="daily-consensus">${n}/${valid.length} nguồn nghiêng mưa</span></div><div class="daily-models">${MODEL_CONFIG.map((m,j)=>{const v=vals[j];if(!v)return `<div class="daily-model"><b>${m.name}</b><span>Không có dữ liệu</span></div>`;const[ico,desc]=codeInfo(v.code);return `<div class="daily-model"><b>${m.name}</b><span>${ico} ${desc}</span><span>${fmt(v.min)}–${fmt(v.max)}°C · mưa ${fmt1(v.rain)} mm</span><span>gió tối đa ${fmt(v.wind)} km/h</span></div>`}).join('')}</div></article>`}
  $('#dailyList').innerHTML=html;
}
function showStatus(msg,loading=false){const el=$('#statusBox');el.textContent=(loading?'⏳ ':'⚠️ ')+msg;el.classList.remove('hidden')}
function hideStatus(){$('#statusBox').classList.add('hidden')}
async function searchPlace(){
  const q=$('#searchInput').value.trim();if(q.length<2)return;
  const box=$('#searchResults');box.classList.remove('hidden');box.innerHTML='<div class="muted">Đang tìm…</div>';
  try{
    const url='https://geocoding-api.open-meteo.com/v1/search?'+new URLSearchParams({name:q+', Cao Bằng',count:'10',language:'vi',countryCode:'VN'});
    const data=await fetchJson(url);let rows=(data.results||[]).filter(r=>((r.admin1||'')+' '+(r.admin2||'')).toLowerCase().includes('cao b'));
    if(!rows.length)rows=(data.results||[]);
    if(!rows.length){box.innerHTML='<div class="muted">Không tìm thấy địa điểm.</div>';return}
    box.innerHTML=rows.map((r,i)=>`<button class="search-result" data-i="${i}"><b>${r.name}</b><br><span class="muted">${[r.admin2,r.admin1,r.country].filter(Boolean).join(', ')}</span></button>`).join('');
    box.querySelectorAll('.search-result').forEach(btn=>btn.onclick=()=>{const r=rows[Number(btn.dataset.i)];state.place={name:[r.name,r.admin2].filter(Boolean).join(', '),lat:r.latitude,lon:r.longitude};savePlace();box.classList.add('hidden');$('#searchInput').value='';loadWeather()});
  }catch(e){box.innerHTML='<div class="muted">Không tìm được địa điểm. Thử lại sau.</div>'}
}
function useGps(){
  if(!navigator.geolocation){showStatus('Thiết bị không hỗ trợ định vị.');return}
  showStatus('Đang lấy vị trí GPS…',true);
  navigator.geolocation.getCurrentPosition(pos=>{state.place={name:'Vị trí của tôi',lat:pos.coords.latitude,lon:pos.coords.longitude};savePlace();loadWeather()},err=>showStatus('Không lấy được vị trí. Hãy cấp quyền vị trí hoặc nhập địa điểm.'),{enableHighAccuracy:true,timeout:12000,maximumAge:600000});
}
function setupTabs(){$$('.tab').forEach(b=>b.onclick=()=>{$$('.tab').forEach(x=>x.classList.remove('active'));b.classList.add('active');$$('.panel').forEach(p=>p.classList.remove('active'));$('#'+b.dataset.tab+'Panel').classList.add('active')})}
function restoreCache(){try{const c=JSON.parse(localStorage.getItem('cbw_cache'));if(c&&Date.now()-c.time<6*3600e3&&c.place?.lat===state.place.lat){state.models=c.models;renderAll()}}catch{}}

$('#searchBtn').onclick=searchPlace;$('#searchInput').addEventListener('keydown',e=>{if(e.key==='Enter')searchPlace()});$('#gpsBtn').onclick=useGps;$('#refreshBtn').onclick=loadWeather;setupTabs();restoreCache();loadWeather();
if('serviceWorker' in navigator && location.protocol.startsWith('http'))navigator.serviceWorker.register('sw.js').catch(()=>{});
