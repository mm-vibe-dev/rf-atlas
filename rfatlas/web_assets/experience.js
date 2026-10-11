/* Progressive disclosure over the existing receiver and evidence controllers. */
'use strict';
(() => {
 const el=id=>document.getElementById(id);
 function disclosure(title,nodes){const box=document.createElement('details'),summary=document.createElement('summary');summary.textContent=title;box.append(summary,...nodes);return box;}
 // Keep scope and health evidence available without dominating the task controls.
 const header=document.querySelector('header');header.append(disclosure('Session, time window and receiver diagnostics',[el('sessionform'),el('healthstrip')]));
 const counts=header.querySelector('.counts');if(counts)header.lastChild.append(counts);
 const form=el('filters'),advanced=[...form.children].filter(n=>n.tagName==='LABEL'&&n.querySelector('input')?.name!=='q'&&n.querySelector('input,select')?.name!=='q');
 form.append(disclosure('Advanced filters',advanced));
 const sort=document.createElement('label');sort.textContent='Sort';const select=document.createElement('select');select.name='sort';
 for(const [value,text] of [['recent','Last seen'],['frequency','Frequency'],['protocol','Protocol'],['favorite','Favorites first']]){const option=document.createElement('option');option.value=value;option.textContent=text;select.append(option);}sort.append(select);form.prepend(sort);
 const originalQuery=query;query=()=>{const params=originalQuery();params.set('sort',select.value);return params;};select.onchange=()=>{catalogOffset=0;loadCatalog().catch(e=>message(e.message));};
 const catalog=el('signals').closest('section');catalog.querySelector('.scroll').style.maxHeight='480px';catalog.before(el('investigation'));
 const batch=el('reanalyze-unknown').closest('p');const batchBox=disclosure('Advanced batch reanalysis',[]);batch.before(batchBox);batchBox.append(batch);
 const modes=document.querySelector('[aria-label="Survey mode"]');if(modes){const box=disclosure('Discovery profile',[]);modes.before(box);box.append(modes);}
 function foldSection(section,title){const children=[...section.children];section.append(disclosure(title,children));}
 foldSection(el('discovery-categories').closest('section'),'Category summary');
 foldSection(el('spectrum').closest('section'),'Spectrum overview and observed coverage');
 const links=document.createElement('div');links.className='filters';
 for(const [label,page] of [['Timeline and spectrum','timeline'],['Manual tuner','tuner']]){const button=document.createElement('button');button.type='button';button.textContent=label;button.onclick=()=>window.rfAtlasRadio.navigate(page);links.append(button);}
 el('discovery-coverage').after(links);
 const alertList=document.createElement('div'),alerts=disclosure('Local discovery alerts',[alertList]);alerts.hidden=true;el('discovery-coverage').after(alerts);let cursor=null,polling=false;
 async function alertPoll(){if(polling||document.hidden)return;polling=true;try{const result=await api('/api/local-alerts'+(cursor===null?'':'?after='+cursor));cursor=result.cursor;alerts.hidden=!result.enabled;if(!result.enabled){alertList.replaceChildren();return;}for(const event of result.events||[]){const button=document.createElement('button');button.type='button';button.textContent='New verified '+event.protocol+' channel';button.onclick=()=>investigate(event.signal_id).catch(e=>message(e.message));alertList.prepend(button);}while(alertList.children.length>20)alertList.lastChild.remove();if(!alertList.children.length)alertList.textContent='Watching for new local verifications. Historical results do not trigger alerts.';}catch(_){/* Optional local notifications never block discovery. */}finally{polling=false;}}
 alertPoll();setInterval(alertPoll,8000);
 const original=investigate,persistentReplay=el('reanalyze-selected');
 investigate=async id=>{
  const replay=persistentReplay;
  if(await original(id)===false)return false;const data=await signalDetail(id),parent=el('detail');
  for(const node of parent.querySelectorAll('details'))node.open=false;
  const notes=parent.querySelector('form');if(notes){const box=disclosure('Private name, notes, tags and favorite',[]);notes.before(box);box.append(notes);}
  const summary=document.createElement('p');summary.id='signal-summary';summary.textContent=`${data.signal.signal_class||'UNKNOWN'} RF · ${data.signal.modulation||'UNKNOWN'} · ${data.signal.protocol||'UNKNOWN'} protocol${data.signal.protocol==='RDS'?' (digital metadata in analog FM)':''}`;
  const actions=document.createElement('div');actions.id='signal-actions';actions.className='filters';
  for(const node of [el('tune-monitor'),el('deep-investigate'),replay])if(node){if(node.id==='deep-investigate')node.textContent='INVESTIGATE';if(node.id==='reanalyze-selected')node.textContent='REANALYZE';actions.append(node);}
  const advanced=disclosure('History, evidence and advanced controls',[...parent.children]);
  parent.replaceChildren(summary,actions,advanced);
  const messages=disclosure('Validated message metadata',[]),list=document.createElement('div');
  const entries=data.messages||[];
  if(!entries.length)list.textContent='No retained validated message metadata. Bit candidates and failed CRC frames are not messages.';
  for(const entry of entries.slice(0,64)){const row=document.createElement('p');row.textContent=`${entry.protocol} · ${entry.status} · ${entry.frame_length??'?'} bytes · ${entry.fec}`;list.append(row);if(Object.keys(entry.fields||{}).length){const fields=document.createElement('pre');fields.textContent=JSON.stringify(entry.fields,null,2);list.append(fields);}}
  messages.append(list);advanced.before(messages);
  if(data.ml_analysis)jsonSection(advanced,'Optional CPU AI hypothesis (uncalibrated)',data.ml_analysis,false);
  const plots=el('plots');if(plots.children.length)plots.replaceChildren(disclosure('Retained diagnostic plots',[...plots.children]));
 };
})();
