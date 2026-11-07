// This widget updates only the answer/history fragment; the page and form stay mounted.
(() => {
 const root=document.querySelector('[data-ai-controller]'),panel=document.getElementById('ai-panel');if(!root||!panel)return;
 const api=root.dataset.aiApi,form=root.querySelector('[data-ai-start]'),notice=root.querySelector('[data-ai-network]'),button=form?.querySelector('button[type="submit"],button.primary');
 const initialLabel=button?.textContent||'',pending=()=>['queued','running'].includes(panel.dataset.aiState);
 let epoch=0,timer,controller,submitting=false,stopped=false;
 function message(text){notice.textContent=text||'';notice.hidden=!text}
 function sync(){
  if(button){button.disabled=submitting||pending();button.textContent=submitting?'Запускаем…':pending()?'Анализ выполняется…':initialLabel}
  const elapsed=panel.querySelector('[data-ai-elapsed]');if(elapsed)elapsed.textContent=String(Math.max(0,Math.floor(Date.now()/1000-Number(panel.dataset.aiCreated||Date.now()/1000))));
 }
 let clock=setInterval(sync,1000);sync();
 async function json(response){
  if(response.redirected&&new URL(response.url).pathname==='/login')throw Object.assign(Error('Сессия истекла. Войдите в приложение заново.'),{stop:true});
  if(!response.headers.get('Content-Type')?.includes('application/json'))throw Error('Сервер вернул неожиданный ответ. Запрос не повторялся автоматически.');
  const data=await response.json();if(!response.ok)throw Object.assign(Error(data.error||'Не удалось выполнить запрос'),{stop:[400,403,404].includes(response.status)});return data;
 }
 function apply(data){
  if(data.view!==panel.dataset.aiView){panel.innerHTML=data.html;panel.dataset.aiView=data.view}
  panel.dataset.aiAnalysis=data.analysis;panel.dataset.aiState=data.state;panel.dataset.aiCreated=data.created||'';
  panel.setAttribute('aria-busy',String(['queued','running'].includes(data.state)));sync();
 }
 function urlFor(id){const url=new URL(location.href);if(id)url.searchParams.set('analysis',id);else url.searchParams.delete('analysis');return url.pathname+url.search+url.hash}
 function reset(){epoch++;clearTimeout(timer);controller?.abort();controller=null;return epoch}
 function schedule(token,delay=1800){if(!stopped&&pending()&&token===epoch)timer=setTimeout(()=>poll(token),delay)}
 async function poll(token){
  if(stopped||token!==epoch)return;const activeController=new AbortController();controller=activeController;const timeout=setTimeout(()=>activeController.abort(),15000);
  try{
   const suffix=panel.dataset.aiAnalysis?'?'+new URLSearchParams({analysis:panel.dataset.aiAnalysis}):'';
   const data=await json(await fetch(api+suffix,{credentials:'same-origin',headers:{Accept:'application/json'},signal:activeController.signal}));
   if(token!==epoch||stopped)return;apply(data);message('');schedule(token);
  }catch(error){if(token!==epoch||stopped)return;message(error.name==='AbortError'?'Не удалось обновить состояние. Повторная проверка через несколько секунд.':error.message);if(!error.stop)schedule(token,5000)}
  finally{clearTimeout(timeout)}
 }
 form?.addEventListener('submit',async event=>{
  event.preventDefault();if(submitting||pending())return;const token=reset();submitting=true;sync();message('');
  const promptRequest={csrf:form.elements.csrf.value,model:form.elements.model.value,prompt_mode:form.elements.prompt_mode.value,prompt_revision:form.elements.prompt_revision.value,system_prompt:form.elements.prompt_mode.value==='request'?form.elements.system_prompt.value:undefined};
  try{
   const data=await json(await fetch(api,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json',Accept:'application/json'},body:JSON.stringify(promptRequest)}));
   if(token!==epoch||stopped)return;form.dispatchEvent(new CustomEvent('dd-ai-prompt-accepted',{detail:{mode:promptRequest.prompt_mode,text:promptRequest.system_prompt}}));apply(data);history.pushState({},'',urlFor(data.analysis));schedule(token);
  }catch(error){
   if(token!==epoch||stopped)return;
   message(error.message+' Если соединение прервалось при запуске, проверьте историю перед повторной отправкой.');
   // The POST may already have been accepted. Read status, never resubmit it automatically.
   if(!error.stop){panel.dataset.aiAnalysis='';poll(token)}
  }finally{submitting=false;sync()}
 });
 panel.addEventListener('click',event=>{
  const link=event.target.closest('[data-ai-history]');if(!link||event.ctrlKey||event.metaKey||event.shiftKey||event.altKey||event.button>0)return;
  event.preventDefault();const token=reset();panel.dataset.aiAnalysis=link.dataset.aiHistory;history.pushState({},'',link.href);message('');poll(token);
 });
 window.addEventListener('popstate',()=>{const token=reset();panel.dataset.aiAnalysis=new URL(location.href).searchParams.get('analysis')||'';poll(token)});
 const modelsButton=root.querySelector('[data-ai-models]'),modelMessage=root.querySelector('[data-ai-model-message]');
 modelsButton?.addEventListener('click',async()=>{
  modelsButton.disabled=true;modelMessage.textContent='Загрузка списка моделей…';
  try{
   const data=await json(await fetch(api+'/models',{credentials:'same-origin',headers:{Accept:'application/json'}})),select=form.elements.model,selected=select.value;
   const rows=[...data.models];if(!rows.some(x=>x.id===selected))rows.unshift({id:selected,label:selected+' · выбранная модель'});
   select.replaceChildren(...rows.map(item=>{const option=document.createElement('option');option.value=item.id;option.textContent=item.label;return option}));select.value=selected;modelMessage.textContent=data.message;
  }catch(error){modelMessage.textContent=error.message}finally{modelsButton.disabled=false}
 });
 window.addEventListener('pagehide',()=>{stopped=true;reset();clearInterval(clock)});
 window.addEventListener('pageshow',event=>{if(event.persisted){stopped=false;clock=setInterval(sync,1000);sync();schedule(epoch)}});
 schedule(epoch);
})();
