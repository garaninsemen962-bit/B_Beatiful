// Progressive enhancement: navigation and form responses remain real server pages.
document.addEventListener('DOMContentLoaded',()=>{
 for(const select of document.querySelectorAll('[data-script-select]')){
  const show=()=>{select.nextElementSibling.textContent=select.selectedOptions[0]?.dataset.description||'Описание не задано'};select.addEventListener('change',show);show();
 }
 for(const other of document.querySelectorAll('form')){
  if(other.elements.code||other.matches('[data-ai-start],[data-personal-prompt]')||!other.querySelector('textarea'))continue;
  let dirty=false;other.addEventListener('input',()=>dirty=true);other.addEventListener('submit',()=>dirty=false);
  window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue=''}});
 }
 const form=document.querySelector('form[data-script-editor]');
 if(form?.elements.code){
  const field=name=>form.querySelector(`[name="${name}"]`),editor=field('code'),message=form.querySelector('[data-editor-message]');
  const widget=window.DDPythonEditor?.mount(editor),owner=field('draft_owner').value;
  const keyFor=path=>'dd-script-draft:'+owner+':'+path;
  const key=keyFor(new URL(form.action).pathname),query=new URLSearchParams(location.search);
  const banner=document.createElement('div');banner.className='draft-banner';banner.hidden=true;editor.closest('.python-editor')?.before(banner);
  const stamp=v=>new Date(v).toLocaleString('ru-RU');let dirty=false,loading=false,storageFailed=false;
  const note=text=>{message.textContent=text};
  function read(key){try{return JSON.parse(sessionStorage.getItem(key)||'null')}catch{return null}}
  function erase(key){try{sessionStorage.removeItem(key)}catch{}}
  function collect(){return {code:editor.value,name:field('name').value,description:field('description').value,script_kind:field('script_kind').value,revision:field('revision').value,token:field('draft_token').value,time:Date.now()}}
  function saveDraft(){
   try{sessionStorage.setItem(key,JSON.stringify(collect()));if(!storageFailed)note('Черновик сохранён в этой вкладке · '+new Date().toLocaleTimeString('ru-RU'))}
   catch{storageFailed=true;note('Браузер не разрешил сохранить черновик. Можно скачать код кнопкой «Скачать код .py».')}
  }
  function changed(){dirty=true;saveDraft()}
  function sync(){widget?.render()}
  function restore(draft,rebase=false){
   for(const name of ['code','name','description','script_kind'])if(typeof draft[name]==='string')field(name).value=draft[name];
   if(!rebase)field('revision').value=draft.revision;
   field('draft_token').value=draft.token||field('draft_token').value;
   sync();contract();dirty=true;saveDraft();banner.hidden=false;
   banner.replaceChildren(document.createTextNode('Восстановлен несохранённый черновик от '+stamp(draft.time)+'. '));
   const discard=document.createElement('button');discard.type='button';discard.textContent='Удалить черновик и загрузить сохранённую версию';
   discard.addEventListener('click',()=>{if(confirm('Удалить черновик? Несохранённые изменения будут потеряны.')){erase(key);dirty=false;location.reload()}});banner.append(discard);
  }
  // A draft is cleared ONLY after the server confirmed a successful save.
  if(query.has('script_saved')){
   const source=query.get('draft_source'),saved=source?read(keyFor(source)):null;
   if(saved&&saved.token===query.get('script_saved'))erase(keyFor(source));
  }
  form.addEventListener('input',changed);form.addEventListener('change',changed);
  window.addEventListener('beforeunload',e=>{if(dirty){saveDraft();e.preventDefault();e.returnValue=''}});
  field('file').addEventListener('change',async()=>{
   const file=field('file').files[0];if(!file)return;loading=true;
   const buttons=[...form.querySelectorAll('button:not([type="button"])')];buttons.forEach(x=>x.disabled=true);
   try{
    if(file.size>512*1024)throw Error('Python-файл больше 512 КБ');
    editor.value=(await file.text()).replace(/^\uFEFF/,'');sync();changed();note('Файл загружен в редактор; теперь можно изменить и сохранить его.');
   }catch(e){note(e.message);field('file').value=''}finally{loading=false;buttons.forEach(x=>x.disabled=false)}
  });
  form.querySelector('[data-docstring]').addEventListener('click',async()=>{
   try{
    const r=await fetch('/api/script-info',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf:field('csrf').value,code:editor.value,kind:field('script_kind').value})});
    const d=await r.json();if(!r.ok)throw Error(d.error);
    if(d.name)field('name').value=d.name;if(d.description)field('description').value=d.description;
    changed();note(d.name?'Название и описание заполнены из docstring.':'Начальный docstring не найден. Код не изменён.');
   }catch(e){saveDraft();note(e.message)}
  });
  const contracts={
   check:'Проверка / метрика: нужна функция def run(mart, params=None), возвращающая строки отчёта, и объект CHECK с entrypoint: run. Обычный print(...) без run не является подключаемой проверкой.',
   normalizer:'Нормализация: нужны normalize(document, app_id, source, params) и segments(before, after, app_id, params).',
   comparator:'Сравнение: нужна compare(union_rows, params).',
   aggregator:'Агрегация: нужна aggregate(rows, segments, params).'
  };
  function contract(){form.querySelector('[data-contract]').textContent=contracts[field('script_kind').value]||''}
  field('script_kind').addEventListener('change',contract);contract();
  const existing=read(key);
  if(form.querySelector('[data-script-error]')){dirty=true;saveDraft()}
  else if(existing&&typeof existing.code==='string'){
   if(String(existing.revision)===field('revision').value)restore(existing);
   else{
    banner.hidden=false;banner.textContent='Есть черновик от '+stamp(existing.time)+', но сохранённая версия уже изменилась. ';
    const recover=document.createElement('button');recover.type='button';recover.textContent='Применить черновик к текущей версии';recover.addEventListener('click',()=>restore(existing,true));
    const discard=document.createElement('button');discard.type='button';discard.textContent='Удалить старый черновик';discard.addEventListener('click',()=>{erase(key);banner.hidden=true});banner.append(recover,discard);
   }
  }
  const templates={
   check:'"""Моя проверка\n\nПоказывает число заявок с различиями.\n"""\n\ndef run(mart, params=None):\n    params = params or {}\n    apps = mart.app_ids(diff_flag=1)\n    total = mart.total_apps()\n    return [{\n        "LABEL": "Заявки с различиями",\n        "SOURCE": "AFTER",\n        "ARGUMENT": "",\n        "CNT": len(apps),\n        "TOTAL": total,\n        "METRIC": len(apps) / total if total else 0,\n        "APP_IDS": apps[:20],\n        "RESULT": "FAIL" if apps else "PASS",\n        "CONFIDENCE": "HIGH",\n    }]\n\nCHECK = {\n    "id": "my_check",\n    "label": "Моя проверка",\n    "params": {},\n    "entrypoint": run,\n}\n',
   normalizer:'"""Моя нормализация\n\nОписание процедуры.\n"""\n\ndef normalize(document, app_id, source, params):\n    # Верните нормализованные строки по контракту.\n    raise NotImplementedError("Реализуйте нормализацию")\n\ndef segments(before, after, app_id, params):\n    # Верните сведения о заявке по контракту.\n    raise NotImplementedError("Реализуйте слой разрезов")\n',
   comparator:'"""Моё сравнение\n\nОписание процедуры.\n"""\n\ndef compare(union_rows, params):\n    # Верните строки сравнения по контракту.\n    raise NotImplementedError("Реализуйте сравнение")\n',
   aggregator:'"""Моя агрегация\n\nОписание процедуры.\n"""\n\ndef aggregate(rows, segments, params):\n    # Верните факты counts и связанные APP_IDS.\n    raise NotImplementedError("Реализуйте агрегацию")\n'
  };
  form.querySelector('[data-template]').addEventListener('click',()=>{
   if(editor.value.trim()&&!confirm('Заменить текущий код шаблоном? При необходимости сначала скачайте текущий код.'))return;
   editor.value=templates[field('script_kind').value];sync();changed();editor.focus();
  });
  form.querySelector('[data-download-draft]').addEventListener('click',()=>{
   const a=document.createElement('a'),url=URL.createObjectURL(new Blob([editor.value],{type:'text/x-python;charset=utf-8'}));a.href=url;a.download='script_draft.py';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  });
  form.addEventListener('submit',e=>{
   saveDraft();if(loading){e.preventDefault();note('Дождитесь загрузки файла.');return}
   // Preserve edits to the uploaded buffer. With JS disabled the server accepts the file.
   field('file').value='';dirty=false;
  });
 }
 const card=document.querySelector('[data-execution]');
 if(card&&['queued','running'].includes(card.dataset.initialState)){
  const poll=async()=>{try{const r=await fetch('/api/executions/'+card.dataset.execution);if(!r.ok)return;const d=await r.json();card.querySelector('progress').value=d.progress;card.querySelector('[data-stage]').textContent=d.stage;if(['done','failed'].includes(d.state)){location.reload();return}setTimeout(poll,1200)}catch(e){setTimeout(poll,3000)}};setTimeout(poll,1000);
 }
});
