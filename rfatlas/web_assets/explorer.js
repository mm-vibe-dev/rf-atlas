/* RF Atlas Spectrum Explorer. All measurements stay on the local dashboard. */
'use strict';
(() => {
  const el = id => document.getElementById(id), form = el('explorerform');
  let current = null, power = null, history = null, peak = [], peakKey = '', loading = false;
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const fmt = (value, digits = 3) => finite(value) ? value.toFixed(digits) : '—';
  const time = value => {
    if(typeof value === 'number')return value;
    if(typeof value !== 'string')return NaN;
    const stamp=value.replace(' ','T');
    const naive=/^\d{4}-\d{2}-\d{2}T/.test(stamp)&&!/(Z|[+-]\d{2}:?\d{2})$/i.test(stamp);
    return Date.parse(naive?stamp+'Z':stamp)/1000;
  };
  const colors = {NOT_SCANNED:'#405363', INVALID:'#e78686', VALID_NO_DETECTION:'#5a9cbd', DETECTED:'#74d6b3'};
  function canvas(id) {
    const node = el(id), width = Math.max(1, node.clientWidth), height = node.clientHeight, ratio = window.devicePixelRatio || 1;
    node.width = width * ratio; node.height = height * ratio;
    const ctx = node.getContext('2d'); ctx.scale(ratio, ratio); ctx.fillStyle = '#0e1c27'; ctx.fillRect(0,0,width,height);
    ctx.font = '12px system-ui'; return {ctx,width,height};
  }
  function empty(id, text) { const {ctx} = canvas(id); ctx.fillStyle='#a3b5c4'; ctx.fillText(text,12,25); }
  function range(data) {
    const frequency = data.spectrum?.frequency_hz || data.waterfall?.frequency_hz || [];
    return [data.bounds?.min_hz ?? frequency[0] ?? 500000, data.bounds?.max_hz ?? frequency[frequency.length-1] ?? 1766000000];
  }
  function plotOptions(target, temporal, series) {
    return {width:Math.max(250,target.clientWidth),height:230,scales:{x:{time:temporal}},
      axes:[{stroke:'#a3b5c4',grid:{stroke:'#2c414e'}, ...(temporal?{}:{values:(u,values)=>values.map(v=>fmt(v/1e6))})},
            {stroke:'#a3b5c4',grid:{stroke:'#2c414e'},size:55}],
      cursor:{drag:{x:true,y:false}},series:[{label:temporal?'Time':'MHz'},...series]};
  }
  function renderPower(data) {
    if(power){power.destroy();power=null;}
    const s = data.spectrum || {}, x = s.frequency_hz || [], values = s.power_db || [];
    if(!x.length || !values.some(finite)){el('powerplot').textContent='No retained spectrum in this range. Missing measurements are not silence.'; return;}
    el('powerplot').replaceChildren();
    const key=JSON.stringify([x,s.settings || {}]);
    if(key!==peakKey){peak=values.slice();peakKey=key;}
    else peak=values.map((value,i)=>finite(value)?Math.max(value,finite(peak[i])?peak[i]:value):peak[i]??null);
    const noise=Array.isArray(s.noise_db)?s.noise_db:x.map(()=>finite(s.noise_db)?s.noise_db:null);
    const options=plotOptions(el('powerplot'),false,[{label:'Relative power dB',stroke:'#80d8e4',width:1},{label:'Peak hold dB',stroke:'#f0c36b',width:1},{label:'Noise dB',stroke:'#b89adf',width:1,dash:[4,4]}]);
    options.hooks={setSelect:[u=>{if(u.select.width>2){const low=u.posToVal(u.select.left,'x'),high=u.posToVal(u.select.left+u.select.width,'x');form.elements.min.value=(low/1e6).toFixed(6);form.elements.max.value=(high/1e6).toFixed(6);el('selection').textContent=`Selected ${fmt(low/1e6,6)}–${fmt(high/1e6,6)} MHz. Explore range to reload matching history and coverage.`;}}]};
    power=new uPlot(options,[x,values,peak,noise],el('powerplot'));
  }
  function renderMarkers(data) {
    const {ctx,width,height}=canvas('markers'), [low,high]=range(data), scale=value=>(value-low)/(high-low||1)*width;
    const signals=data.signals || [];el('detectionlinks').replaceChildren();
    for(const signal of signals){if(!finite(signal.center_frequency_hz))continue;
      const x=scale(signal.center_frequency_hz),bw=signal.occupied_bandwidth_hz || 0;
      ctx.fillStyle=signal.protocol&&signal.protocol!=='UNKNOWN'?'#74d6b3':'#f0c36b';
      ctx.globalAlpha=.35;ctx.fillRect(scale(signal.center_frequency_hz-bw/2),12,Math.max(2,bw/(high-low||1)*width),28);ctx.globalAlpha=1;
      ctx.fillRect(x-1,8,2,36);
      const button=document.createElement('button');button.type='button';button.textContent=`${fmt(signal.center_frequency_hz/1e6,6)} MHz · ${signal.protocol || 'UNKNOWN'} · ${signal.modulation || 'UNKNOWN'} · ${signal.investigation_status || 'not investigated'}`;
      button.title=`Occupied bandwidth ${fmt(bw/1000,2)} kHz; frequency uncertainty ±${fmt(signal.frequency_uncertainty_hz,0)} Hz`;
      button.onclick=()=>investigate(signal.signal_id).catch(error=>message(error.message));el('detectionlinks').append(button);
    }
    ctx.fillStyle='#a3b5c4';ctx.fillText(`${fmt(low/1e6)} MHz`,5,height-6);ctx.fillText(`${fmt(high/1e6)} MHz`,Math.max(5,width-100),height-6);
    if(!signals.length)el('detectionlinks').textContent='No catalog detections matching this view.';
  }
  function renderWaterfall(data) {
    const rows=data.waterfall?.rows || [], frequencies=data.waterfall?.frequency_hz || [];
    if(!rows.length||!frequencies.length){empty('waterfall','No retained spectral rows');el('waterfalllegend').textContent='Missing files and unobserved frequencies remain blank.';return;}
    const {ctx,width,height}=canvas('waterfall'),values=rows.flatMap(row=>row.power_db || []).filter(finite);
    if(!values.length){empty('waterfall','No valid spectral samples');return;}
    const low=Math.min(...values),high=Math.max(...values),[start,stop]=range(data);
    for(let r=0;r<rows.length;r++)for(let c=0;c<frequencies.length;c++){
      const value=rows[r].power_db?.[c];if(!finite(value))continue;
      const level=Math.max(0,Math.min(1,(value-low)/(high-low||1))),left=(frequencies[c]-start)/(stop-start||1)*width;
      const next=c+1<frequencies.length?(frequencies[c+1]-start)/(stop-start||1)*width:left+width/frequencies.length;
      ctx.fillStyle=`rgb(${Math.round(30+225*level)},${Math.round(50+170*level)},${Math.round(90-40*level)})`;
      ctx.fillRect(left,r*height/rows.length,Math.max(1,next-left),Math.ceil(height/rows.length));
    }
    el('waterfalllegend').textContent=`Top ${rows[0].observed || 'unknown time'} → bottom ${rows[rows.length-1].observed || 'unknown time'} · ${fmt(low,1)} to ${fmt(high,1)} relative dB · ${rows.length} retained rows. Color scale is local to this view; gain and acquisition settings affect comparisons.`;
  }
  function renderCoverage(data) {
    const {ctx,width,height}=canvas('coverageheat'),[low,high]=range(data);ctx.fillStyle=colors.NOT_SCANNED;ctx.fillRect(0,0,width,height);
    for(const region of [...(data.coverage || [])].reverse()){ctx.fillStyle=colors[region.status] || colors.NOT_SCANNED;ctx.fillRect((region.start_hz-low)/(high-low||1)*width,0,(region.stop_hz-region.start_hz)/(high-low||1)*width,height);}
  }
  function renderHistory(data) {
    if(history){history.destroy();history=null;}el('historyplot').replaceChildren();
    const rows=(data.history || []).filter(row=>finite(time(row.observed))).sort((a,b)=>time(a.observed)-time(b.observed));
    if(!rows.length){el('historyplot').textContent='No signal strength observations in this range.';return;}
    history=new uPlot(plotOptions(el('historyplot'),true,[{label:'Peak SNR dB',stroke:'#80d8e4',points:{show:true},spanGaps:false}]),[rows.map(row=>time(row.observed)),rows.map(row=>finite(row.peak_snr_db)?row.peak_snr_db:null)],el('historyplot'));
  }
  function renderOccupancy(data) {
    const rows=data.occupancy || [];if(!rows.length){empty('occupancyheat','No measurement visits in this time range');el('occupancylegend').textContent='Occupancy requires valid observations; unscanned intervals are unknown.';return;}
    const {ctx,width,height}=canvas('occupancyheat');
    rows.forEach((row,i)=>{const observed=row.observed_regions || 0,detected=row.detected_regions || 0,invalid=row.invalid_regions || 0,x=i*width/rows.length,w=Math.max(1,width/rows.length-1);ctx.fillStyle=observed?colors.VALID_NO_DETECTION:colors.NOT_SCANNED;ctx.fillRect(x,20,w,height-40);if(observed){ctx.fillStyle=colors.DETECTED;const filled=Math.min(1,detected/observed)*(height-40);ctx.fillRect(x,height-20-filled,w,filled);}if(invalid){ctx.fillStyle=colors.INVALID;ctx.fillRect(x,0,w,12);}});
    el('occupancylegend').textContent=`${rows.length} time buckets · green = detected / valid observed regions; red top = invalid attempts. This is sampled detection occupancy, not continuous transmitter airtime.`;
  }
  function render(data) {
    current=data;renderPower(data);renderMarkers(data);renderWaterfall(data);renderCoverage(data);renderHistory(data);renderOccupancy(data);
    el('explorerstatus').textContent=`${data.signals?.length || 0} detections · latest retained measurement ${data.spectrum?.observed || 'unavailable'}${data.truncated?' · bounded view truncated':''}`;
    el('explorermetadata').textContent=JSON.stringify({settings:data.spectrum?.settings,units:data.spectrum?.units,bounds:data.bounds,limits:data.limits,truncated:data.truncated,limitations:data.limitations},null,2);
  }
  async function load() {
    if(loading)return;loading=true;
    try{const params=new URLSearchParams();for(const name of ['min','max'])if(form.elements[name].value)params.set(name+'_hz',Number(form.elements[name].value)*1e6);
      for(const name of ['since','until'])if(form.elements[name].value)params.set(name,form.elements[name].value+'Z');
      if(form.elements.unknown.value)params.set('unknown','true');
      render(await api('/api/explorer?'+params));
    }catch(error){el('explorerstatus').textContent=error.message;}finally{loading=false;}
  }
  form.onsubmit=event=>{event.preventDefault();load();};el('explorerreset').onclick=()=>{form.reset();peakKey='';el('selection').textContent='';load();};el('peakreset').onclick=()=>{peakKey='';if(current)renderPower(current);};
  window.addEventListener('resize',()=>{if(current)render(current);});
  setInterval(()=>{if(form.elements.live.value==='true'&&!document.hidden)load();},10000);load();
  window.rfAtlasExplorer={load};
  const originalInvestigate=investigate;
  investigate=async id=>{
    if(await originalInvestigate(id)===false)return false;
    const data=await signalDetail(id),signal=data.signal;
    const metrics=document.createElement('div');metrics.id='investigationmetrics';
    for(const text of [`Frequency uncertainty ±${fmt(signal.frequency_uncertainty_hz,0)} Hz`,`Occupied bandwidth ${fmt(signal.occupied_bandwidth_hz/1000,2)} kHz`,`Peak SNR ${fmt(signal.peak_snr_db,1)} dB`,`Protocol ${signal.protocol || 'UNKNOWN'}`,`Protection ${signal.protection || 'UNKNOWN'}`]){const span=document.createElement('span');span.textContent=text;metrics.append(span);}
    el('detail').prepend(metrics);
    const actions=document.createElement('div');actions.id='recordingactions';
    for(const recording of data.recordings || [])for(const action of ['replay','reanalyze']){
      const button=document.createElement('button');button.type='button';button.textContent=`${action==='replay'?'Replay diagnostics':'Reanalyze offline'} · ${recording.capture_id}`;
      button.disabled=recording.supports_actions===false;
      button.title='Analyze the retained recording locally. Original measurements remain preserved; no audio playback or RF transmission.';
      button.onclick=async()=>{button.disabled=true;try{const result=await api('/api/recordings/'+encodeURIComponent(recording.capture_id)+'/'+action,{method:'POST',headers:{'Content-Type':'application/json','X-RFAtlas-Token':token},body:'{}'});jsonSection(el('detail'),action==='replay'?'Retained recording replay diagnostics':'New offline reanalysis result',result,true);message('Offline recording analysis completed');}catch(error){message(error.message);}finally{button.disabled=false;}};actions.append(button);
    }
    const compare=document.createElement('button');compare.type='button';compare.textContent='Explore this frequency and compare observation dates';compare.onclick=()=>{const half=Math.max(25000,(signal.occupied_bandwidth_hz || 0)*3);form.elements.min.value=((signal.center_frequency_hz-half)/1e6).toFixed(6);form.elements.max.value=((signal.center_frequency_hz+half)/1e6).toFixed(6);load();el('explorer').scrollIntoView({behavior:'smooth'});};actions.append(compare);el('detail').append(actions);
  };
})();
