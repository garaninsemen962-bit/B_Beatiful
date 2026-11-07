"""Server-rendered, independent HTML pages. Ordinary links preserve browser history."""
import html,json,secrets
from datetime import datetime,timezone,timedelta
from urllib.parse import urlencode
import store as s
E=lambda x:html.escape(str(x if x is not None else ''),quote=True)
LABELS={'dataset':'Датасеты','script':'Процедуры и метрики','config':'Конфигурации','run':'Прогоны'}
URLS={'dataset':'datasets','script':'scripts','config':'configurations','run':'runs'}
KINDS={'normalizer':'Нормализация','comparator':'Сравнение','aggregator':'Агрегация','check':'Проверка / метрика'}
def date(t):return datetime.fromtimestamp(t,timezone(timedelta(hours=3))).strftime('%d.%m.%Y %H:%M:%S') if t else '—'
def link(url,text,cls=''):return f'<a class="{E(cls)}" href="{E(url)}">{E(text)}</a>'
def button(text):return f'<button class="primary">{E(text)}</button>'
def input_(name,value='',type='text',required=False):return f'<input name="{E(name)}" type="{E(type)}" value="{E(value)}" {"required" if required else ""}>'
def textarea(name,text,rows=5,cls=''):return f'<textarea class="{E(cls)}" name="{E(name)}" rows="{rows}">{E(text)}</textarea>'
def table(head,rows):return '<div class="tablewrap"><table><thead><tr>'+''.join('<th>'+E(h)+'</th>' for h in head)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+str(x)+'</td>' for x in row)+'</tr>' for row in rows)+'</tbody></table></div>'
def select(name,options,current=None,multiple=False):
    currents=current if isinstance(current,list) else [current]
    options=list(options)
    options += [(value,'Недоступный ресурс: '+str(value)) for value in currents if value not in (None,'') and not any(key==value for key,_ in options)]
    return f'<select name="{E(name)}" {"multiple size=6" if multiple else ""}>'+''.join(f'<option value="{E(k)}" {"selected" if k in currents else ""}>{E(v)}</option>' for k,v in options)+'</select>'
def form(user,action,body,multipart=False):
    csrf=user.get('csrf','') if user else ''
    return f'<form method="post" action="{E(action)}" {"enctype=multipart/form-data" if multipart else ""}>'+input_('csrf',csrf,'hidden')+body+'</form>'
def shell(user,title,body,section='',flash='',extra=''):
    nav=''
    if user and user['status']=='active':
        nav=''.join(link('/'+URLS[k],v,'active' if section==k else '') for k,v in LABELS.items())
        nav+=link('/profile','Мой профиль','active' if section=='profile' else '')+link('/trash','Корзина','active' if section=='trash' else '')
        if user['admin']:nav+=link('/admin','Администрирование','active' if section=='admin' else '')
        if s.is_root(user):nav+=link('/root','Сервер root','active' if section=='root' else '')
    identity=link('/profile',user['display'])+' · '+E(user['login'])+' '+form(user,'/logout',button('Выйти')) if user else link('/login','Войти')
    return '<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+E(title)+' · Decision Diff</title><link rel="stylesheet" href="/assets/style.css"><script defer src="/assets/editor.js"></script><script defer src="/assets/app.js"></script><header><div><strong>Decision Diff</strong><small>Совместный анализ изменений</small></div><div class="row">'+identity+'</div></header><div class="layout"><aside>'+nav+'</aside><main>'+('<div class="notice">'+E(flash)+'</div>' if flash else '')+'<h1>'+E(title)+'</h1>'+body+'<footer>Decision Diff 2 · серверное время отображается по Москве</footer></main></div>'+extra+'</html>'
def auth(mode,flash=''):
    register=mode=='register'
    body='<div class="card auth"><p>После регистрации администратор должен подтвердить вашу учётную запись.</p>' if register else '<div class="card auth"><p>Войдите в рабочую область команды.</p>'
    fields='<label>Логин латиницей</label>'+input_('login',required=True)
    if register:fields+='<label>Отображаемое имя</label>'+input_('display',required=True)
    fields+='<label>Пароль</label>'+input_('password',type='password',required=True)+'<p class="muted">Минимум 8 символов.</p>'+button('Зарегистрироваться' if register else 'Войти')
    return shell(None,'Регистрация' if register else 'Вход',body+form(None,'/'+mode,fields)+ '<p>'+link('/login' if register else '/register','Уже есть аккаунт' if register else 'Создать аккаунт')+'</p></div>',flash=flash)
def pending(user):return shell(user,'Состояние учётной записи','<div class="card"><p>Статус: <strong>'+E(user['status'])+'</strong></p><p>Если заявка ещё ожидает подтверждения, доступ появится после одобрения администратором.</p>'+link('/account','Обновить состояние','button')+'</div>')
def listing(user,kind,rows,flash=''):
    body='<div class="row between"><p class="muted">Доступные вам ресурсы, включая общие и предоставленные поимённо.</p>'+link('/'+URLS[kind]+'/new','Создать' if kind in ('run','config') else 'Загрузить','button primary')+'</div>'
    result=[]
    with s.db() as c:
        for r in rows:
            owner=c.execute('SELECT display FROM users WHERE id=?',(r['owner'],)).fetchone()[0]
            result.append([link('/'+URLS[kind]+'/'+r['id'],r['name'])+'<small class="muted">'+E(r['description'][:180])+'</small>',E(owner),E(r['visibility']),E(r['role']),str(r['revision']),date(r['created'])])
    body+='<div class="card">'+table(['Название / описание','Владелец','Доступ','Ваши права','Версия','Создан'],result)+'</div>'
    return shell(user,LABELS[kind],body,kind,flash)
def resource_head(r,role,download=False):
    path='/'+URLS[r['kind']]+'/'+r['id'];deleted=r.get('deleted_at') is not None;purged=r.get('purged_at') is not None
    actions=link('/resources/'+r['id']+'/history','История')
    if role=='owner':
        if not deleted:actions+=link('/resources/'+r['id']+'/access','Доступ')+link('/resources/'+r['id']+'/delete','Удалить','button danger')
        elif not purged:actions+=link('/resources/'+r['id']+'/restore','Восстановить','button')+link('/resources/'+r['id']+'/purge','Удалить навсегда','button danger')
    if download and not deleted and r['kind'] in ('script','dataset'):actions+=link('/resources/'+r['id']+'/download','Скачать исходник')
    return '<div class="crumb">'+link('/'+URLS[r['kind']],LABELS[r['kind']])+' / '+E(r['name'])+'</div><div class="row"><span class="badge">Версия '+str(r['revision'])+'</span><span class="badge">'+E(role)+'</span>'+actions+'</div><p class="description">'+E(r['description'])+'</p>'+('<p class="warning">Ресурс удалён навсегда. Сохранены только сведения об изменениях.</p>' if purged else '<p class="warning">Ресурс находится в корзине.</p>' if deleted else '')
def script_form(user,r=None,code='',version=None,editable=True,submitted=None,error=''):
    body=json.loads(r['body']) if r else {};rid=r['id'] if r else '';role='owner' if r and (r['owner']==user['id'] or user['admin']) else 'editor' if editable else 'reader'
    values={'revision':r['revision'] if r else 0,'script_kind':body.get('script_kind','check'),'name':r['name'] if r else '', 'description':r['description'] if r else '', 'draft_token':secrets.token_hex(16)}
    if submitted:
        values.update({key:submitted[key] for key in values if key in submitted})
    top=resource_head(r,role) if r else '<p>Загрузите .py или напишите код. Для использования в прогоне скрипт должен соответствовать выбранному назначению.</p>'
    fields=input_('revision',values['revision'],'hidden')+input_('draft_owner',user['id'],'hidden')+input_('draft_token',values['draft_token'],'hidden')
    if error:
        fields+='<div class="error" role="alert" data-script-error>'+E(error)+'<p>Код и заполненные поля сохранены в форме. Исправьте ошибку и отправьте снова.</p></div>'
    fields+='<label>Назначение</label>'+select('script_kind',KINDS.items(),values['script_kind'])+'<label>Python-файл с компьютера</label><input name="file" type="file" accept=".py"><label>Русское название</label>'+input_('name',values['name'])+'<label>Краткое описание (показывается при выборе)</label>'+textarea('description',values['description'],3)
    fields+='<div class="row space"><button type="button" data-docstring>Заполнить из docstring</button><button type="button" data-template>Вставить шаблон</button><button type="button" data-download-draft>Скачать код .py</button></div><p role="status" class="muted" data-editor-message></p>'
    fields+='<div class="notice" data-contract></div><label for="script-code">Исходный код</label>'+textarea('code',code,24,'editor').replace('<textarea ','<textarea id="script-code" spellcheck="false" autocapitalize="off" autocomplete="off" ')
    fields+='<p class="muted">Tab / Shift+Tab — отступы, Enter — продолжение отступа, Ctrl/Cmd+S — сохранить. Черновик хранится в текущей вкладке. При ошибке он не удаляется. Введённый код здесь не исполняется.</p>'+button('Сохранить новую версию' if r else 'Создать скрипт')
    if version:top+='<div class="warning">Просматривается версия '+E(version)+'. Сохранение создаст новую версию текущего ресурса.</div>'
    bodyhtml=top+'<div class="card">'+(form(user,'/scripts/'+rid if r else '/scripts/new',fields,True) if editable else '<pre>'+E(code)+'</pre>')+'</div>'
    result=shell(user,r['name'] if r else 'Новый скрипт',bodyhtml,'script')
    return result.replace('<form method="post" action="/scripts/', '<form data-script-editor method="post" action="/scripts/')
def dataset_page(user,r=None,role='owner',download=False,flash=''):
    body=resource_head(r,role,download) if r else '<p>ZIP должен содержать RESULTS/BEFORE_MASTER/*.json и RESULTS/AFTER_BETA/*.json. Также принимаются RESULTS/BEFORE и RESULTS/AFTER. Допускается общая внешняя папка.</p>'
    if r:
        data=json.loads(r['body']);body+='<div class="card">'+table(['Показатель','Значение'],[[E(k),E(v)] for k,v in data.items() if k not in ('folder','validation_examples')])+'</div>'
    if r:
        data=json.loads(r['body'])
        if data.get('invalid_responses') or any(not n for n in data.get('slices',{}).values()):
            body+='<div class="warning"><p>Есть повреждённые ответы или отсутствует целый срез. Датасет сохранён; такие пары будут исключены из сравнения, причины появятся в отчёте выполнения.</p>'+table(['Срез','Заявка','Ошибка при загрузке'],[[E(x['source']),E(x['app_id']),E(x['error'])] for x in data.get('validation_examples',[])])+'</div>'
    body+='<p class="muted">Повреждённые JSON и заявки без одного ответа сохраняются для диагностики. В сводную попадут только корректные пары. Сведения о валидации процедурой появляются после расчёта.</p>'
    if not r or role in ('owner','editor'):
        fields=input_('revision',r['revision'] if r else 0,'hidden')+'<label>Название</label>'+input_('name',r['name'] if r else '',required=True)+'<label>Описание</label>'+textarea('description',r['description'] if r else '',3)+'<label>ZIP-архив (до 64 МБ)</label><input name="file" type="file" accept=".zip" required><p class="muted">До 256 МБ распакованных данных, не более 50 000 записей архива. Изменение создаёт новую версию; старые данные сохраняются.</p>'+button('Загрузить новую версию' if r else 'Загрузить датасет')
        body+='<div class="card">'+form(user,'/datasets/'+r['id'] if r else '/datasets/new',fields,True)+'</div>'
    return shell(user,r['name'] if r else 'Загрузка датасета',body,'dataset',flash)
def config_form(user,resources,r=None,role='owner',flash='',action=None):
    cfg=json.loads(r['body']) if r else {};body=resource_head(r,role) if r else ''
    fields=input_('revision',r['revision'] if r else 0,'hidden')+'<label>Название</label>'+input_('name',r['name'] if r else '',required=True)+'<label>Описание</label>'+textarea('description',r['description'] if r else '',3)
    fields+='<label>Датасет</label>'+select('dataset',[(x['id'],x['name']) for x in resources if x['kind']=='dataset'],cfg.get('dataset'))
    for key,kind in [('normalizer','normalizer'),('comparator','comparator'),('aggregator','aggregator')]:
        choices=[x for x in resources if x['kind']=='script' and json.loads(x['body'])['script_kind']==kind]
        if cfg.get(key) and not any(item['id']==cfg[key] for item in choices):choices=[{'id':cfg[key],'name':'Недоступный ресурс: '+cfg[key],'description':'Ресурс удалён или доступ отозван. Выберите замену.'},*choices]
        fields+='<label>'+KINDS[kind]+'</label><select name="'+key+'" data-script-select>'+''.join('<option value="'+x['id']+'" data-description="'+E(x['description'])+'" '+('selected' if x['id']==cfg.get(key) else '')+'>'+E(x['name'])+'</option>' for x in choices)+'</select><p class="muted" data-description></p>'
    fields+='<label>Проверки и метрики</label>'
    for x in resources:
        if x['kind']=='script' and json.loads(x['body'])['script_kind']=='check':fields+='<label class="checklabel"><input type="checkbox" name="checks" value="'+x['id']+'" '+('checked' if x['id'] in cfg.get('checks',[]) else '')+'>'+E(x['name'])+'<small class="muted">'+E(x['description'])+'</small></label>'
    for missing in set(cfg.get('checks',[]))-{item['id'] for item in resources}:
        fields+='<label class="checklabel warning"><input type="checkbox" name="checks" value="'+E(missing)+'" checked>Недоступная проверка: '+E(missing)+'<small>Восстановите ресурс, запросите доступ или снимите отметку.</small></label>'
    params={k:cfg.get(k,{}) for k in ('normalize_params','compare_params','aggregate_params','check_params')}
    fields+='<label>Параметры (JSON)</label>'+textarea('params',json.dumps(params,ensure_ascii=False,indent=2),10)+'<p class="muted">check_params задаются по ID ресурса проверки. При выполнении используются последние доступные версии; точные версии и SHA-256 фиксируются в манифесте выполнения.</p>'+button('Сохранить конфигурацию')
    if r and role=='reader':body+='<div class="card"><pre>'+E(json.dumps(cfg,ensure_ascii=False,indent=2))+'</pre></div>'
    else:body+='<div class="card">'+form(user,action or ('/configurations/'+r['id'] if r else '/configurations/new'),fields)+'</div>'
    if r and r['kind']=='config':body+=link('/runs/new?config='+r['id'],'Создать прогон на основе конфигурации','button primary')
    return shell(user,r['name'] if r else 'Новая конфигурация',body,'config',flash)
def run_new(user,configs,current=''):
    fields='<label>Название</label>'+input_('name',required=True)+'<label>Описание</label>'+textarea('description','',3)+'<label>Конфигурация</label>'+select('config',[(x['id'],x['name']) for x in configs],current)+'<p class="muted">Настройки копируются в прогон. Дальнейшие изменения исходной конфигурации не меняют прогон автоматически.</p>'+button('Создать прогон')
    return shell(user,'Новый прогон','<div class="card">'+form(user,'/runs/new',fields)+'</div>','run')
def run_page(user,r,role,executions,flash=''):
    body=resource_head(r,role)
    if role in ('editor','owner'):body+='<div class="row">'+form(user,'/runs/'+r['id']+'/execute',button('Новое выполнение'))+link('/runs/'+r['id']+'/edit','Изменить настройки','button')+'</div>'
    body+='<div class="card"><h2>Выполнения</h2>'+table(['Выполнение','Состояние','Кто запустил','Начало','Завершение'],[[link('/executions/'+x['id'],x['id'][:12]),'<span class="badge '+E(x['state'])+'">'+E(x['state'])+'</span>',E(x['display']),date(x['created']),date(x['finished'])] for x in executions])+'</div>'
    body+='<details class="card"><summary>Текущие настройки прогона</summary><pre>'+E(json.dumps(json.loads(r['body']),ensure_ascii=False,indent=2))+'</pre></details>'
    return shell(user,r['name'],body,'run',flash)
def run_edit(user,r,resources):
    view={**r,'body':json.dumps(json.loads(r['body'])['config'])}
    return config_form(user,resources,view,'editor',action='/runs/'+r['id']+'/edit')
def access(user,r,users,grants,stamp):
    fields=input_('stamp',stamp,'hidden')+'<label>Режим доступа</label>'+select('visibility',[('private','Только владелец'),('named','Поимённый доступ'),('public','Все подтверждённые пользователи')],r['visibility'])+'<label>Права для всех при общем доступе</label>'+select('public_role',[('reader','Читатель'),('editor','Редактор')],r['public_role'])+'<label><input name="public_download" type="checkbox" '+('checked' if r['public_download'] else '')+'> Всем разрешено прямое скачивание исходника</label><h3>Поимённые права</h3><p class="muted">Действуют в режимах «Поимённый» и «Общий». В общем режиме индивидуальные права могут повысить общий уровень.</p>'
    rows=[]
    for u in users:
        if u['id']==r['owner']:continue
        g=grants.get(u['id'],{})
        rows.append([E(u['display'])+'<small>'+E(u['login'])+'</small>',select('g_'+u['id'],[('','Нет индивидуальных прав'),('reader','Читатель'),('editor','Редактор')],g.get('role','')),'<input type="checkbox" name="d_'+u['id']+'" '+('checked' if g.get('download') else '')+'>'])
    fields+=table(['Пользователь','Права','Скачивание'],rows)+'<p class="warning">Доступ к результатам прогона предоставляется отдельно от исходного датасета. Редактор прогона может выполнять код, если имеет доступ к выбранным ресурсам. Такой код получает входные данные целиком; разрешение скачивания ограничивает прямую кнопку скачивания, а не возможности авторского кода.</p>'+button('Сохранить права')
    return shell(user,'Управление доступом',resource_head(r,'owner')+'<div class="card">'+form(user,'/resources/'+r['id']+'/access',fields)+'</div>',r['kind'])
def history(user,r,events,versions):
    rows=[[date(x['created']),E(x['display']),E(x['action']),'<details><summary>Подробности</summary><pre>'+E(json.dumps(json.loads(x['details']),ensure_ascii=False,indent=2))+'</pre></details>'] for x in events]
    vrows=[]
    for v in versions:
        url='/scripts/'+r['id']+'?version='+str(v['revision']) if r['kind']=='script' and r.get('deleted_at') is None else '/resources/'+r['id']+'/version?version='+str(v['revision'])
        vrows.append([link(url,'Версия '+str(v['revision'])),E(v['name']),E(v['display']),date(v['created'])])
    return shell(user,'История изменений',resource_head(r,'reader')+'<div class="card"><h2>Версии</h2>'+table(['Версия','Название','Автор','Дата'],vrows)+'</div><div class="card"><h2>Журнал действий</h2>'+table(['Дата','Пользователь','Действие','Изменения'],rows)+'</div>',r['kind'])
def admin(user,users,health,events,flash=''):
    rows=[]
    for u in users:
        controls=''
        if u['id']!=user['id'] and not u['admin'] and u['status']!='deleted':
            controls=form(user,'/admin/users/'+u['id'],select('status',[('active','Подтвердить / разблокировать'),('rejected','Отклонить'),('blocked','Заблокировать')],u['status'])+button('Применить'))
        actions=link('/admin/users/'+u['id']+'/restore','Восстановить','button') if u['status']=='deleted' else link('/admin/users/'+u['id']+'/edit','Редактировать','button')+controls
        if u.get('server_root') and not s.is_root(user):actions='<span class="muted">Защищённая учётная запись root</span>'
        if u['status']!='deleted' and u['id']!=user['id'] and not u.get('server_root'):actions+=link('/admin/users/'+u['id']+'/delete','Удалить','button danger')
        rows.append([E(u['display'])+'<small>'+E(u['login'])+'</small>',E(u['status']),E('Root сервера' if u.get('server_root') else 'Администратор' if u['admin'] else 'Участник'),actions])
    body='<p>'+link('/admin','Действующие пользователи')+' · '+link('/admin?deleted=1','Удалённые пользователи')+'</p><div class="card"><h2>Пользователи</h2>'+table(['Имя','Статус','Роль','Действие'],rows)+'</div><div class="card"><h2>Контейнерный исполнитель</h2><p>'+E(health)+'</p><p class="muted">Без готового Docker runner запуск Python запрещён. До двух одновременных расчётов.</p></div><div class="card"><h2>Последние административные события</h2>'+table(['Дата','Автор','Действие'],[[date(x['created']),E(x['display']),E(x['action'])] for x in events])+'</div>'
    return shell(user,'Администрирование',body,'admin',flash)
def execution_head(user,r,e,section):
    base='/executions/'+e['id'];body='<div class="crumb">'+link('/runs','Прогоны')+' / '+link('/runs/'+r['id'],r['name'])+' / '+E(e['id'][:12])+'</div><div class="tabs">'
    with s.db() as c:can_execute=s.role(c,user,r) in ('owner','editor')
    for suffix,label in [('', 'Обзор и журнал'),('/pivot','Сводная'),('/checks','Проверки'),('/applications','Заявки'),('/recheck','Новые проверки'),('/ai','Gemini')]:
        if suffix!='/recheck' or can_execute:body+=link(base+suffix,label,'active' if section==suffix else '')
    return body+'</div>'

def user_form(user,target,admin_mode=False,submitted=None,error='',flash=''):
    values={k:target[k] for k in ('login','display','revision','status','admin')}
    if submitted:values.update({k:submitted[k] for k in values if k in submitted})
    fields=input_('revision',values['revision'],'hidden')
    if error:fields+='<div class="error" role="alert">'+E(error)+'</div>'
    fields+='<label>Отображаемое имя</label>'+input_('display',values['display'],required=True)+'<label>Логин</label>'+input_('login',values['login'],required=True)
    if admin_mode:
        fields+='<label>Статус</label>'+select('status',[('pending','Ожидает подтверждения'),('active','Активен'),('rejected','Отклонён'),('blocked','Заблокирован')],values['status'])+'<label>Роль на сервере</label>'+select('admin',[('0','Участник'),('1','Администратор')],str(values['admin']))
    if not admin_mode or target['id']==user['id']:fields+='<label>Текущий пароль (для изменения логина или пароля)</label>'+input_('current_password',type='password')
    fields+='<label>Новый пароль (оставьте пустым, чтобы не менять)</label>'+input_('new_password',type='password').replace('<input ','<input autocomplete="new-password" ')+'<label>Повторите новый пароль</label>'+input_('confirm_password',type='password').replace('<input ','<input autocomplete="new-password" ')+'<p class="muted">Новый пароль — минимум 8 символов. Смена логина, пароля, статуса или роли закрывает прежние сессии. Пароли в истории изменений не сохраняются.</p>'+button('Сохранить изменения')
    action='/admin/users/'+target['id']+'/edit' if admin_mode else '/profile'
    title='Пользователь: '+target['display'] if admin_mode else 'Мой профиль'
    return shell(user,title,'<div class="card">'+form(user,action,fields)+'</div>','admin' if admin_mode else 'profile',flash)

def trash_page(user,resources,executions,flash=''):
    rows=[]
    for r in resources:
        actions=link('/resources/'+r['id']+'/restore','Восстановить','button')+' '+link('/resources/'+r['id']+'/purge','Удалить навсегда','button danger')
        rows.append([E(LABELS[r['kind']]),E(r['name']),date(r['deleted_at']),actions+' '+link('/resources/'+r['id']+'/history','История')])
    erows=[]
    for e in executions:
        actions=link('/executions/'+e['id']+'/restore','Восстановить','button')+' '+link('/executions/'+e['id']+'/purge','Удалить навсегда','button danger')
        erows.append([E(e['id']),E(e['run_name'])+('<small>Прогон тоже в корзине: сначала восстановите его.</small>' if e['parent_deleted'] is not None else ''),date(e['deleted_at']),actions])
    body='<p>Здесь видны ваши удалённые ресурсы; администратору доступны все. Корзина сохраняет файлы до окончательного удаления.</p><div class="card"><h2>Ресурсы</h2>'+table(['Тип','Название','Удалён','Действия'],rows)+'</div><div class="card"><h2>Отдельные выполнения</h2>'+table(['ID','Прогон','Удалён','Действия'],erows)+'</div>'
    return shell(user,'Корзина',body,'trash',flash)

def deletion_page(user,item,action,execution=False,refs=None):
    labels={'delete':'Удалить в корзину','restore':'Восстановить','purge':'Удалить навсегда'};label=labels[action]
    name=item['id'] if execution else item['name'];url=('/executions/' if execution else '/resources/')+item['id']+'/'+action
    fields=''
    if not execution:fields+=input_('revision',item['revision'],'hidden')
    if action=='purge':
        fields+='<p class="error">Будут удалены файлы и результаты. Восстановить их из приложения будет нельзя. Записи истории останутся.</p>'
        if not execution and item['kind']=='run':fields+='<p>Будут также окончательно удалены все выполнения этого прогона.</p>'
        fields+='<label>Для подтверждения введите '+('полный ID выполнения' if execution else 'точное название ресурса')+'</label>'+input_('confirm',required=True)
    elif action=='delete':fields+='<p>Ресурс исчезнет из рабочих списков и станет недоступен по прямым ссылкам. Его можно будет восстановить из корзины.</p>'
    else:fields+='<p>Ресурс вернётся в рабочие списки с прежними настройками доступа. Проверьте их после восстановления.</p>'
    if refs:fields+='<div class="warning">Найдены связи с другими ресурсами или историей выполнений: '+str(len(refs))+'. Окончательное удаление будет заблокировано, пока эти связи существуют. Учитываются и старые версии настроек, и корзина.</div>'
    if refs:fields+='<ul>'+''.join('<li>'+(link(ref['url'],ref['label']) if ref.get('url') else E(ref.get('label','Связанный ресурс')))+'</li>' for ref in refs)+'</ul>'
    fields+='<div class="row space">'+button(label)+' '+link('/trash' if action!='delete' else ('/runs/'+item['run'] if execution else '/'+URLS[item['kind']]+'/'+item['id']),'Отмена','button')+'</div>'
    return shell(user,label,'<div class="card"><h2>'+E(name)+'</h2>'+form(user,url,fields)+'</div>','trash' if action!='delete' else 'run' if execution else item['kind'])

def ai_result(row):
    result=json.loads(row['result']);evidence={x['id']:x for x in json.loads(row['evidence'])}
    def paragraph(text):return '<p class="ai-prose">'+E(text)+'</p>'
    def refs(ids):
        links=[]
        for x in ids:
            item=evidence.get(x)
            if not item:continue
            data=item.get('data',{});caption=data.get('LABEL') or data.get('PATH') or data.get('product') or {'summary':'Итоги выполнения','coverage':'Охват проверок'}.get(item.get('kind'),'Данные')
            links.append('<a href="'+E(item['url'])+'" title="'+E(caption)+'">'+E(x)+' · '+E(str(caption)[:100])+'</a>')
        return '<p class="ai-references">Основания: '+', '.join(links)+'</p>'
    body='<p class="muted">Модель '+E(row['model'])+' · '+date(row['created'])+' · автор запроса '+E(row.get('display',''))+'</p><h3>Резюме</h3>'+paragraph(result['summary'])
    if result.get('assessment'):body+='<h3>Общая оценка и приоритет разбора</h3>'+paragraph(result['assessment'])
    body+='<h3>Разбор изменений</h3>'
    for index,finding in enumerate(result['findings'],1):
        importance={'low':'Низкая','medium':'Средняя','high':'Высокая','critical':'Критическая'}[finding['severity']]
        body+='<section class="finding '+E(finding['severity'])+'"><h3>'+E(str(index)+'. '+finding['title'])+'</h3><p class="badge">'+E(importance)+' важность</p>' if finding.get('title') else '<section class="finding '+E(finding['severity'])+'"><h3>'+E(importance)+' важность</h3>'
        for key,label in [('observation','Наблюдение'),('quantitative_details','Масштаб и цифры'),('interpretation','Почему это важно'),('hypothesis','Гипотеза')]:
            if finding.get(key):body+='<h4>'+label+'</h4>'+paragraph(finding[key])
        if finding.get('alternatives'):body+='<h4>Альтернативные объяснения</h4><ul>'+''.join('<li>'+E(x)+'</li>' for x in finding['alternatives'])+'</ul>'
        body+='<h4>Что проверить</h4>'+paragraph(finding['recommendation'])+refs(finding['evidence_ids'])+'</section>'
    if result.get('review_plan'):
        body+='<h3>Порядок проверки</h3><ol class="ai-plan">'
        for step in result['review_plan']:body+='<li><span class="badge">'+E(step['priority'])+'</span>'+paragraph(step['action'])+'<p><strong>Ожидаемый результат:</strong> '+E(step['expected_result'])+'</p>'+refs(step['evidence_ids'])+'</li>'
        body+='</ol>'
    return body+'<h3>Ограничения анализа</h3><ul>'+''.join('<li>'+E(x)+'</li>' for x in result['limitations'])+'</ul>'+prompt_snapshot(row)

def ai_prompt_fields(user,profile=False,submitted=None,error=''):
    import ai_prompts
    pref=ai_prompts.read(user);values=submitted or {};revision=values.get('prompt_revision',pref['revision']);text=values.get('system_prompt',pref['text'])
    data={'builtin':ai_prompts.DEFAULT_SYSTEM,'personal':pref['text'],'revision':revision,'custom':pref['custom']}
    kind='profile' if profile else 'analysis'
    body='<details class="prompt-settings" data-prompt-area data-prompt-kind="'+kind+'" '+('open' if error else '')+'><summary>'+('Личный системный промпт Gemini' if profile else 'Дополнительные настройки Gemini')+'</summary><div class="prompt-settings-body">'
    if error:body+='<p class="error">'+E(error)+'</p>'
    body+='<p>Личный промпт используется по умолчанию для ваших новых анализов. Для отдельного запуска можно выбрать свой текст. Формат JSON и проверка ссылок задаются приложением отдельно.</p>'
    if not profile:body+='<label>Источник промпта</label>'+select('prompt_mode',[('personal','Мой промпт по умолчанию'),('request','Свой для этого запуска'),('builtin','Стандартный промпт приложения')],'personal')
    body+=input_('prompt_revision',revision,'hidden')+'<label>Системный промпт</label>'+textarea('system_prompt',text,13,'system-prompt').replace('<textarea ','<textarea maxlength="16000" '+('readonly ' if not profile else ''))
    body+='<p class="muted">До 16000 символов. Сохранение личного промпта не запускает Gemini. Текст, использованный в анализе, сохраняется вместе с результатом и доступен читателям этого прогона.</p><div class="row space">'
    if profile:body+='<button class="primary" name="action" value="save">Сохранить мой промпт</button><button name="action" value="reset">Сбросить личный промпт</button>'
    else:body+='<button type="button" data-prompt-save>Сохранить как мой промпт по умолчанию</button><button type="button" data-prompt-reset>Сбросить личный промпт</button>'
    body+='</div><p data-prompt-message role="status"></p><script type="application/json" data-prompt-settings>'+json.dumps(data,ensure_ascii=False).replace('<','\\u003c')+'</script></div></details>'
    if profile:body='<div class="card">'+form(user,'/profile/ai-prompt',body).replace('<form ','<form data-personal-prompt ' )+'</div>'
    return body

def prompt_snapshot(row):
    raw=row.get('prompt_config')
    if not raw:return '<details class="prompt-snapshot"><summary>Использованный системный промпт</summary><p class="muted">Для этого старого анализа текст промпта не сохранялся.</p></details>'
    config=json.loads(raw);source={'personal':'Личный промпт автора запроса','builtin':'Стандартный промпт приложения','request':'Свой текст для этого запуска'}.get(config['source'],config['source'])
    return '<details class="prompt-snapshot"><summary>Использованный системный промпт</summary><p>'+E(source)+(' · личная версия '+E(config['preference_revision']) if config['source']=='personal' else '')+'</p><p class="muted">SHA-256: '+E(config['sha256'])+'</p><pre>'+E(config['text'])+'</pre><details><summary>Полный текст, отправленный модели</summary><pre>'+E(config['system'])+'</pre></details></details>'
