(() => {
 const form=document.querySelector('[data-pivot-layout]');if(!form)return;
 const zones=[...form.querySelectorAll('[data-zone]')];let moving;
 function sync(){for(const name of ['group','columns'])form.elements[name].value=[...form.querySelector(`[data-zone="${name}"]`).querySelectorAll('[data-field]')].map(x=>x.dataset.field).join(',')}
 for(const field of form.querySelectorAll('[data-field]')){
  field.addEventListener('dragstart',e=>{moving=field;e.dataTransfer.setData('text/plain',field.dataset.field)});
  field.querySelector('button').addEventListener('click',()=>{const i=zones.indexOf(field.parentElement);zones[(i+1)%zones.length].append(field);sync()});
 }
 for(const zone of zones){zone.addEventListener('dragover',e=>e.preventDefault());zone.addEventListener('drop',e=>{e.preventDefault();if(!moving)return;const target=e.target.closest('[data-field]');if(target&&target!==moving)zone.insertBefore(moving,target);else zone.append(moving);sync();moving=null})}
 form.addEventListener('submit',()=>{sync();const field=form.elements.filter_field.value;if(field){const f=JSON.parse(form.elements.filter.value||'{}');f[field]=form.elements.filter_value.value;form.elements.filter.value=JSON.stringify(f)}form.elements.filter_field.disabled=true;form.elements.filter_value.disabled=true});
 form.querySelector('[data-clear-filter]').addEventListener('click',()=>{form.elements.filter.value='{}';form.querySelector('code').textContent='{}'});
 const settings=JSON.parse(document.getElementById('pivot-settings').textContent),panel=document.querySelector('[data-wdr-panel]'),message=panel.querySelector('[data-wdr-message]');let pivot,loading=false;
 panel.addEventListener('toggle',async()=>{
  if(!panel.open||pivot||loading)return;loading=true;message.textContent='Загрузка агрегатов…';
  try{
   const response=await fetch(settings.base+'/facts?'+new URLSearchParams(settings.query));const data=await response.json();if(!response.ok)throw Error(data.error||'Не удалось загрузить факты');
   const caps=settings.captions||{};const meta=Object.fromEntries(Object.keys(caps).map(k=>[k,{type:k==='CNT'?'number':'string',caption:caps[k]}]));const report={...settings.layout,dataSource:{data:[meta,...data.rows]},slice:settings.layout.slice||{rows:[{uniqueName:'PATH'}],columns:[{uniqueName:'SOURCE'}],measures:[{uniqueName:'CNT',aggregation:'sum',caption:'Наблюдения атрибутов'}]},options:{...(settings.layout.options||{}),configuratorActive:false,drillThrough:false}};
   for(const area of ['rows','columns','reportFilters']){for(const item of (report.slice[area]||[])){if(!item.caption&&caps[item.uniqueName])item.caption=caps[item.uniqueName]}}
   pivot=new WebDataRocks({container:'#wdr-pivot',height:650,toolbar:true,global:{localization:'/assets/vendor/webdatarocks/ru.json'},report,beforetoolbarcreated:toolbar=>{const get=toolbar.getTabs;toolbar.getTabs=function(){return get.call(this).filter(x=>!['wdr-tab-connect','wdr-tab-open','wdr-tab-save'].includes(x.id))}},reportcomplete:()=>{message.textContent='Конструктор готов. Изменения можно сохранить отдельной кнопкой.'}});
  }catch(e){message.textContent=e.message}finally{loading=false}
 });
 panel.querySelector('[data-wdr-save]').addEventListener('click',async()=>{
  if(!pivot){message.textContent='Сначала дождитесь загрузки конструктора';return}
  try{const report=pivot.getReport();delete report.dataSource;const r=await fetch(settings.base+'/layout',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf:settings.csrf,kind:'wdr',layout:report})});const d=await r.json();if(!r.ok)throw Error(d.error||'Не удалось сохранить');message.textContent='Макет сохранён для вашей учётной записи.'}catch(e){message.textContent=e.message}
 });
})();
