// Personal prompt settings are private; editing them never launches a model request.
(() => {
 for(const area of document.querySelectorAll('[data-prompt-area]')){
  const config=JSON.parse(area.querySelector('[data-prompt-settings]').textContent),form=area.closest('form'),text=area.querySelector('[name=system_prompt]'),revision=area.querySelector('[name=prompt_revision]'),mode=area.querySelector('[name=prompt_mode]'),message=area.querySelector('[data-prompt-message]');
  let customDraft=null,previous=mode?.value,busy=false,dirty=false;
  function show(){
   if(!mode)return;
   if(previous==='request')customDraft=text.value;
   const current=text.value;text.readOnly=mode.value!=='request';
   text.value=mode.value==='builtin'?config.builtin:mode.value==='personal'?config.personal:customDraft??current;previous=mode.value;
  }
  mode?.addEventListener('change',show);show();
  text.addEventListener('input',()=>dirty=true);
  window.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue=''}});
  form.addEventListener('dd-ai-prompt-accepted',event=>{if(event.detail.mode==='request'&&mode?.value==='request'&&text.value===event.detail.text)dirty=false});
  async function save(action){
   if(busy)return;const submittedText=text.value,submittedMode=mode?.value;busy=true;const buttons=[...area.querySelectorAll('button')];buttons.forEach(x=>x.disabled=true);message.textContent='Сохранение…';
   try{
    const response=await fetch('/api/me/ai-prompt',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf:form.elements.csrf.value,action,revision:revision.value,system_prompt:submittedText})});
    if(!response.headers.get('Content-Type')?.includes('application/json'))throw Error('Сессия истекла или сервер вернул неожиданный ответ. Введённый текст оставлен в форме.');
    const data=await response.json();if(!response.ok)throw Error(data.error||'Не удалось сохранить промпт');
    const changed=text.value!==submittedText||mode?.value!==submittedMode;
    if(mode?.value==='request')customDraft=text.value;
    config.personal=data.text;config.custom=data.custom;config.revision=data.revision;revision.value=data.revision;
    if(!changed){if(mode){mode.value='personal';previous='personal';text.readOnly=true}text.value=data.text;dirty=false}
    message.textContent=(action==='reset'?'Личный промпт сброшен к стандартному.':'Личный промпт сохранён для следующих анализов.')+(changed?' Новые правки оставлены в поле и ещё не сохранены.':'');
   }catch(error){message.textContent=error.message}finally{busy=false;buttons.forEach(x=>x.disabled=false)}
  }
  area.querySelector('[data-prompt-save]')?.addEventListener('click',()=>save('save'));
  area.querySelector('[data-prompt-reset]')?.addEventListener('click',()=>save('reset'));
  if(area.dataset.promptKind==='profile')form.addEventListener('submit',event=>{event.preventDefault();save(event.submitter?.value==='reset'?'reset':'save')});
 }
})();
