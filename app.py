"""Decision Diff 2. Standard library, server-rendered multipage application."""
import argparse,ast,email.policy,getpass,hashlib,html,io,json,os,secrets,sqlite3,time,traceback,zipfile
from email.parser import BytesParser
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse,parse_qs,unquote,urlencode
import store as s
import service,sandbox,pages as p,reports
from engine import Mart
from uploads import MAX_UPLOAD

def policy_stamp(c,r):
    grants=[dict(x) for x in c.execute('SELECT * FROM grants WHERE resource=? ORDER BY user_id',(r['id'],))]
    return hashlib.sha256(json.dumps([r['visibility'],r['public_role'],r['public_download'],grants],sort_keys=True).encode()).hexdigest()

class Handler(BaseHTTPRequestHandler):
    server_version='DecisionDiff/2'
    def send(self,body,status=200,kind='text/html; charset=utf-8',headers=None):
        raw=body.encode('utf-8') if isinstance(body,str) else body
        self.send_response(status)
        for k,v in {'Content-Type':kind,'Content-Length':str(len(raw)),'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY','Referrer-Policy':'same-origin',**(headers or {})}.items():self.send_header(k,v)
        self.end_headers();self.wfile.write(raw)
    def json(self,value,status=200):self.send(json.dumps(value,ensure_ascii=False),status,'application/json; charset=utf-8')
    def redirect(self,path,message=''):
        if message:path+=('&' if '?' in path else '?')+urlencode({'message':message})
        self.send('',303,headers={'Location':path})
    def current_user(self):
        cookie=SimpleCookie(self.headers.get('Cookie',''));token=cookie.get('session')
        if not token:return None
        with s.db() as c:
            row=c.execute('SELECT users.*,sessions.csrf FROM sessions JOIN users ON users.id=sessions.user_id WHERE token=? AND expires>?',(token.value,time.time())).fetchone()
        return dict(row) if row else None
    def fields(self):
        length=int(self.headers.get('Content-Length','0'))
        if length>MAX_UPLOAD+2*1024*1024:raise ValueError('Запрос слишком велик')
        if length<0:raise ValueError('Неверный Content-Length')
        raw=self.rfile.read(length);ct=self.headers.get('Content-Type','')
        if ct.startswith('multipart/form-data'):
            if len(ct)>300:raise ValueError('Неверный Content-Type')
            msg=BytesParser(policy=email.policy.default).parsebytes(('Content-Type: '+ct+'\r\nMIME-Version: 1.0\r\n\r\n').encode()+raw)
            if not msg.is_multipart():raise ValueError('Неверная форма загрузки')
            fields={};files={}
            for part in msg.iter_parts():
                name=part.get_param('name',header='content-disposition');data=part.get_payload(decode=True) or b''
                if part.get_filename():files[name]=(part.get_filename(),data)
                elif name:
                    text=data.decode('utf-8');fields.setdefault(name,[]).append(text)
            result={k:(v if k=='checks' else v[-1]) for k,v in fields.items()};result['_files']=files;return result
        if ct.startswith('application/json'):return json.loads(raw)
        data=parse_qs(raw.decode('utf-8'),keep_blank_values=True)
        return {k:(v if k=='checks' else v[-1]) for k,v in data.items()}
    def get_resource(self,rid,minimum='reader'):
        with s.db() as c:r=s.resource(c,rid);role=s.require(c,self.user,r,minimum);download=s.can_download(c,self.user,r)
        return r,role,download
    def do_GET(self):self.handle_request(False)
    def do_POST(self):self.handle_request(True)
    def handle_request(self,post):
        self.user=None
        try:
            parsed=urlparse(self.path);self.path_only=unquote(parsed.path);self.q={k:v[-1] for k,v in parse_qs(parsed.query).items()};self.user=self.current_user();self.form=self.fields() if post else {}
            if post:
                origin=self.headers.get('Origin')
                if origin and urlparse(origin).netloc!=self.headers.get('Host'):raise PermissionError('Недопустимый Origin')
                if self.path_only not in ('/login','/register'):
                    token=self.form.get('csrf','') or self.headers.get('X-CSRF-Token','')
                    if not self.user or not secrets.compare_digest(token,self.user['csrf']):raise PermissionError('Сессия устарела. Обновите страницу.')
            self.route(post)
        except (PermissionError,FileNotFoundError,ValueError,KeyError,sqlite3.IntegrityError,SyntaxError,UnicodeError,zipfile.BadZipFile) as ex:
            code=403 if isinstance(ex,PermissionError) else 404 if isinstance(ex,FileNotFoundError) else 400
            if self.path_only.startswith('/api/'):self.json({'error':str(ex)},code)
            else:self.send(p.shell(self.user,'Не удалось выполнить действие','<div class="error">'+p.E(str(ex))+'</div><p>Вернитесь назад кнопкой браузера или откройте список ресурсов.</p>'),code)
        except Exception:
            traceback.print_exc();self.send(p.shell(self.user,'Ошибка сервера','<div class="error">Подробности записаны в журнал сервера.</div>'),500)
    def route(self,post):
        path=self.path_only;q=self.q;f=self.form;user=self.user
        if path in ('/assets/style.css','/assets/app.js','/assets/editor.js') and not post:
            file=s.ROOT/'web'/path.rsplit('/',1)[1];return self.send(file.read_bytes(),kind='text/css' if file.suffix=='.css' else 'text/javascript')
        if path in ('/login','/register'):
            if not post:return self.send(p.auth(path[1:]))
            if path=='/register':user=s.register(f['login'],f['display'],f['password'])
            else:user=s.authenticate(f['login'],f['password'])
            token=s.session(user);return self.send('',303,headers={'Set-Cookie':f'session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=86400','Location':'/runs' if user['status']=='active' else '/account'})
        if not user:return self.redirect('/login')
        if path=='/logout' and post:
            cookie=SimpleCookie(self.headers.get('Cookie',''))
            with s.db() as c:c.execute('DELETE FROM sessions WHERE token=?',(cookie['session'].value,))
            return self.send('',303,headers={'Location':'/login','Set-Cookie':'session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0'})
        if path=='/account':return self.send(p.pending(user))
        if user['status']!='active':return self.redirect('/account')
        if path=='/':return self.redirect('/runs')
        if path=='/api/script-info' and post:
            name,desc=s.script_info(f['code'],f['kind'],validate=False);return self.json({'name':name,'description':desc})
        if path.startswith('/admin'):
            if not user['admin']:raise PermissionError('Требуются права администратора')
            if path=='/admin/demo' and post:service.import_demo(user);return self.redirect('/runs','Демонстрационные ресурсы добавлены')
            if path.startswith('/admin/users/') and post:
                uid=path.rsplit('/',1)[1];status=f['status']
                if status not in ('active','rejected','blocked'):raise ValueError('Неверный статус')
                with s.db() as c:
                    target=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
                    if not target or target['admin'] or target['id']==user['id']:raise PermissionError('Нельзя изменить этого пользователя')
                    c.execute('UPDATE users SET status=? WHERE id=?',(status,uid))
                    # Approval and blocking both revoke old sessions; pending users log in again.
                    c.execute('DELETE FROM sessions WHERE user_id=?',(uid,))
                    s.audit(c,user,None,'Изменение статуса пользователя',{'user':uid,'login':target['login'],'before':target['status'],'after':status})
                return self.redirect('/admin','Статус изменён; пользователю нужно войти заново')
            with s.db() as c:users=[dict(x) for x in c.execute('SELECT * FROM users ORDER BY created DESC')];events=[dict(x) for x in c.execute('SELECT * FROM audit WHERE resource IS NULL ORDER BY id DESC LIMIT 100')]
            _,health=sandbox.available();html=p.admin(user,users,health,events,q.get('message',''))
            html=html.replace('<footer>',p.form(user,'/admin/demo',p.button('Добавить открытый датасет и исходные проверки'))+'<footer>')
            return self.send(html)
        parts=path.strip('/').split('/');collection=parts[0]
        kind={v:k for k,v in p.URLS.items()}.get(collection)
        if kind:
            rid=parts[1] if len(parts)>1 else None
            if not rid:return self.send(p.listing(user,kind,s.visible(user,kind),q.get('message','')))
            new=rid=='new';r,role,download=(None,'owner',True) if new else self.get_resource(rid)
            if r and r['kind']!=kind:raise FileNotFoundError('Ресурс не найден')
            if len(parts)>2 and kind=='run':
                if parts[2]=='execute' and post:
                    eid=service.execute(user,rid);return self.redirect('/executions/'+eid)
                if parts[2]=='edit':
                    self.get_resource(rid,'editor')
                    if post:service.edit_run(user,rid,f);return self.redirect('/runs/'+rid,'Сохранена новая версия настроек')
                    return self.send(p.run_edit(user,r,s.visible(user)))
                raise FileNotFoundError('Страница не найдена')
            if post:
                if r:self.get_resource(rid,'editor')
                if kind=='script':
                    file=f.get('_files',{}).get('file');code=(file[1].decode('utf-8-sig') if file else f.get('code',''))
                    if len(code)>512*1024:return self.send(p.script_form(user,r,code,submitted=f,error='Скрипт больше 512 КБ'),422)
                    f['filename']=file[0] if file else (json.loads(r['body']).get('filename','script.py') if r else 'script.py')
                    try:
                        rid=service.save_script(user,f,code,None if new else rid)
                    except (ValueError,SyntaxError,sqlite3.IntegrityError) as ex:
                        message=(f'Ошибка синтаксиса: строка {ex.lineno}, столбец {ex.offset}. {ex.msg}' if isinstance(ex,SyntaxError) else str(ex))
                        return self.send(p.script_form(user,r,code,submitted=f,error=message),422)
                    token=f.get('draft_token','')
                    saved={'message':'Сохранено'}
                    if len(token)<=64 and token.isalnum():saved.update(script_saved=token,draft_source='/scripts/new' if new else '/scripts/'+rid)
                    return self.redirect('/scripts/'+rid+'?'+urlencode(saved))
                elif kind=='dataset':
                    file=f.get('_files',{}).get('file')
                    if not file:raise ValueError('Выберите ZIP-файл')
                    rid=service.save_dataset(user,f,file[1],None if new else rid)
                elif kind=='config':rid=service.save_config(user,f,None if new else rid)
                elif kind=='run':rid=service.create_run(user,f)
                return self.redirect('/'+collection+'/'+rid,'Сохранено')
            if kind=='script':
                if new:return self.send(p.script_form(user))
                version=int(q.get('version',r['revision']))
                with s.db() as c:v=c.execute('SELECT * FROM versions WHERE resource=? AND revision=?',(rid,version)).fetchone()
                if not v:raise FileNotFoundError('Версия не найдена')
                b=json.loads(v['body']);code=(s.STATE/'objects'/b['file']).read_text(encoding='utf-8') if download else 'Исходный код недоступен. Владелец может разрешить скачивание или редактирование.'
                html=p.script_form(user,r,code,version if version!=r['revision'] else None,role in ('owner','editor'))
                html=html.replace('<footer>',('<p>'+p.link('/resources/'+rid+'/access','Управление доступом')+'</p>' if role=='owner' else '')+'<footer>')
                return self.send(html)
            if kind=='dataset':return self.send(p.dataset_page(user,r,role,download,q.get('message','')))
            if kind=='config':return self.send(p.config_form(user,s.visible(user),r,role,q.get('message','')))
            if new:return self.send(p.run_new(user,s.visible(user,'config'),q.get('config','')))
            with s.db() as c:executions=[dict(x) for x in c.execute('SELECT executions.*,users.display FROM executions JOIN users ON actor=users.id WHERE run=? ORDER BY created DESC',(rid,))]
            return self.send(p.run_page(user,r,role,executions,q.get('message','')))
        if collection=='resources' and len(parts)==3:
            rid,action=parts[1:];r,role,download=self.get_resource(rid)
            if action=='access':
                self.get_resource(rid,'owner')
                with s.db() as c:
                    if post:
                        c.execute('BEGIN IMMEDIATE');r=s.resource(c,rid);s.require(c,user,r,'owner')
                        if not secrets.compare_digest(f['stamp'],policy_stamp(c,r)):raise ValueError('Права уже изменены. Обновите страницу.')
                        visibility=f['visibility'];public_role=f['public_role']
                        if visibility not in ('private','named','public') or public_role not in ('reader','editor'):raise ValueError('Неверные права')
                        before={'visibility':r['visibility'],'public_role':r['public_role'],'public_download':r['public_download'],'grants':[dict(x) for x in c.execute('SELECT * FROM grants WHERE resource=?',(rid,))]}
                        c.execute('UPDATE resources SET visibility=?,public_role=?,public_download=? WHERE id=?',(visibility,public_role,int('public_download' in f),rid));c.execute('DELETE FROM grants WHERE resource=?',(rid,))
                        grants=[]
                        for key,val in f.items():
                            if key.startswith('g_') and val:
                                if val not in ('reader','editor'):raise ValueError('Неверная роль')
                                target=c.execute("SELECT id FROM users WHERE id=? AND status='active'",(key[2:],)).fetchone()
                                if not target or target[0]==r['owner']:raise ValueError('Неверный получатель прав')
                                d=int('d_'+target[0] in f);c.execute('INSERT INTO grants VALUES (?,?,?,?)',(rid,target[0],val,d));grants.append({'user':target[0],'role':val,'download':d})
                        s.audit(c,user,rid,'Изменение прав доступа',{'before':before,'after':{'visibility':visibility,'public_role':public_role,'public_download':'public_download' in f,'grants':grants}})
                    else:
                        users=[dict(x) for x in c.execute("SELECT * FROM users WHERE status='active' ORDER BY display")];grants={x['user_id']:dict(x) for x in c.execute('SELECT * FROM grants WHERE resource=?',(rid,))};stamp=policy_stamp(c,r)
                if post:return self.redirect('/'+p.URLS[r['kind']]+'/'+rid,'Права доступа сохранены')
                return self.send(p.access(user,r,users,grants,stamp))
            if action=='history':
                with s.db() as c:
                    events=[dict(x) for x in c.execute('SELECT * FROM audit WHERE resource=? ORDER BY id DESC',(rid,))]
                    versions=[dict(x) for x in c.execute('SELECT versions.*,users.display FROM versions JOIN users ON author=users.id WHERE resource=? ORDER BY revision DESC',(rid,))]
                return self.send(p.history(user,r,events,versions))
            if action in ('download','version'):
                version=int(q.get('version',r['revision']))
                with s.db() as c:v=c.execute('SELECT * FROM versions WHERE resource=? AND revision=?',(rid,version)).fetchone()
                if not v:raise FileNotFoundError('Версия не найдена')
                b=json.loads(v['body'])
                if action=='version':return self.send(p.shell(user,'Версия '+str(version),p.resource_head(r,role)+'<div class="card"><pre>'+p.E(json.dumps(dict(v),ensure_ascii=False,indent=2))+'</pre></div>',r['kind']))
                if not download:raise PermissionError('Владелец не разрешил скачивание исходников')
                if r['kind']=='script':return self.send((s.STATE/'objects'/b['file']).read_bytes(),kind='text/x-python; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="script-v{version}.py"'})
                if r['kind']=='dataset':
                    out=io.BytesIO();root=s.STATE/'objects'/b['folder']
                    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
                        for file in root.rglob('*.json'):z.write(file,file.relative_to(root).as_posix())
                    return self.send(out.getvalue(),kind='application/zip',headers={'Content-Disposition':f'attachment; filename="dataset-v{version}.zip"'})
                raise ValueError('У ресурса нет скачиваемого исходника')
        if collection=='executions' and len(parts)>=2:return self.execution(parts,post)
        if path.startswith('/api/executions/'):
            eid=parts[2];e,r=self.get_execution(eid)
            folder=s.STATE/'executions'/eid
            status=sandbox.status_file(folder/'result') if e['state'] in ('running','queued') else {}
            return self.json({'state':e['state'],'progress':status.get('progress',100 if e['state']=='done' else 0),'stage':status.get('stage',e['state']),'error':e['error']})
        raise FileNotFoundError('Страница не найдена')
    def get_execution(self,eid):
        with s.db() as c:
            e=c.execute('SELECT * FROM executions WHERE id=?',(eid,)).fetchone()
            if not e:raise FileNotFoundError('Выполнение не найдено')
            r=s.resource(c,e['run']);s.require(c,self.user,r)
        return dict(e),r
    def execution(self,parts,post):
        eid=parts[1];section='/'+parts[2] if len(parts)>2 else '';e,r=self.get_execution(eid);user=self.user;q=self.q;base='/executions/'+eid
        if post:raise ValueError('Результат выполнения неизменяем. Создайте новое выполнение.')
        head=p.execution_head(user,r,e,section)
        manifest=json.loads(e['manifest']);script_names={x['file']:x['name'] for x in manifest['scripts']}
        if section=='/code':
            srid=q.get('script');item=next((x for x in manifest['scripts'] if x['resource']==srid),None)
            if not item:raise FileNotFoundError('Скрипт не найден')
            with s.db() as c:sr=s.resource(c,srid);allowed=s.can_download(c,user,sr)
            if not allowed:raise PermissionError('Нет доступа к исходному коду выбранного скрипта')
            code=(s.STATE/'executions'/eid/'input/scripts'/item['file']).read_text(encoding='utf-8')
            return self.send(p.shell(user,item['name'],head+'<div class="card"><p>Зафиксированная версия '+str(item['revision'])+' · SHA-256 '+p.E(item['sha256'])+'</p><pre>'+p.E(code)+'</pre></div>','run'))
        if section=='':
            status={}
            if e['state']=='done':status=reports.artifact(eid,'status.json')
            elif e['state'] in ('running','queued'):status=sandbox.status_file(s.STATE/'executions'/eid/'result')
            body=head+'<div class="card" data-execution="'+eid+'" data-initial-state="'+p.E(e['state'])+'"><h2>Состояние выполнения</h2><progress max="100" value="'+str(status.get('progress',0))+'"></progress><p data-stage>'+p.E(status.get('stage',e['state']))+'</p><p class="muted">Начало: '+p.date(e['created'])+' · завершение: '+p.date(e['finished'])+'</p>'+('<pre class="error">'+p.E(e['error'])+'</pre>' if e['error'] else '')+'</div>'
            if e['state']=='done':
                summary=reports.artifact(eid,'summary.json');body+='<div class="metrics">'+''.join('<div class="metric"><span class="muted">'+label+'</span><b>'+str(summary[key])+'</b></div>' for key,label in [('total_apps','Всего заявок'),('changed_apps','С различиями'),('decision_changed','Сменили решение'),('rows','Атрибутов сравнения')])+'</div>'
                body+='<div class="card"><h2>Изменения решений</h2>'+p.table(['Переход','Заявок'],[[p.E(k),str(v)] for k,v in summary['transitions'].items()])+'</div>'
            logs=[]
            for x in status.get('logs',[]):
                script=x.get('script');item=next((z for z in manifest['scripts'] if z['file']==script),None)
                code=p.link(base+'/code?script='+item['resource'],item['name']) if item else ''
                logs.append([p.E(x.get('stage')),p.E(x.get('seconds')),code])
            body+='<div class="card"><h2>Журнал этапов</h2>'+p.table(['Этап','Секунды','Код'],logs)+'</div>'
            output=s.STATE/'executions'/eid/'console.log';text=output.read_text(encoding='utf-8',errors='replace')[:1024*1024] if output.exists() else ''
            body+='<details class="card"><summary>Вывод Python</summary><pre>'+p.E(text)+'</pre></details><details class="card"><summary>Входы, версии и контрольные суммы</summary><pre>'+p.E(json.dumps(manifest,ensure_ascii=False,indent=2))+'</pre></details>'
            return self.send(p.shell(user,'Выполнение '+eid[:12],body,'run'))
        if e['state']!='done':return self.send(p.shell(user,'Результат ещё не готов',head+'<div class="card">Дождитесь успешного завершения выполнения.</div>','run'))
        if section in ('/checks','/export'):
            checks=reports.artifact(eid,'checks.json');rows=[]
            for x in checks['rows']:
                qs=urlencode({'ids':json.dumps(x['APP_IDS'])});rows.append([p.E(x['LABEL']),p.E(x['SOURCE'])+'<br>'+p.E(x['ARGUMENT']),str(x['CNT'])+' / '+str(x['TOTAL']),p.E(x['METRIC']),'<span class="badge '+('low ' if x['CONFIDENCE']=='LOW' else '')+('fail' if x['RESULT']=='FAIL' else 'pass')+'">'+p.E(x['RESULT'])+' · '+p.E(x['CONFIDENCE'])+'</span>',p.link(base+'/applications?'+qs,str(len(x['APP_IDS']))+' примеров')])
            body='<div class="card"><p class="muted">FAIL — условие проверки сработало. Бледные строки — LOW confidence. Количество примеров может быть ограничено параметрами.</p>'+p.table(['Проверка','Срез / аргумент','Количество','Метрика','Результат','Примеры'],rows)+'</div>'
            if section=='/export':
                origin='http://'+self.headers['Host'];body=body.replace('href="/','href="'+p.E(origin)+'/')
                doc='<!doctype html><html lang="ru"><meta charset="utf-8"><title>Отчёт проверок</title><style>'+ (s.ROOT/'web/style.css').read_text()+'</style><main><h1>'+p.E(r['name'])+'</h1><p>Выполнение '+eid+' · '+p.date(e['created'])+'</p>'+p.link(origin+base+'/pivot','Открыть сводный отчёт приложения')+body+'</main></html>'
                return self.send(doc,headers={'Content-Disposition':'attachment; filename="report-'+eid+'.html"'})
            return self.send(p.shell(user,'Отчёт проверок',head+p.link(base+'/export','Скачать HTML','button')+body,'run'))
        if section=='/pivot':return self.pivot(e,r,head)
        if section=='/applications':
            if len(parts)>3:
                app=parts[3]
                with reports.connect(eid) as c:
                    rows=list(Mart(c).rows(app_ids=[app]));seg=next((x for x in Mart(c).segments() if x['APP_ID']==app),{})
                if not rows:raise FileNotFoundError('Заявка не найдена')
                allrows=q.get('all')=='1'
                body=head+'<p>'+p.link(base+'/applications?'+urlencode({k:v for k,v in q.items() if k not in ('all',)}),'← Назад к выбранному списку')+'</p>'+p.link(self.path_only+'?'+urlencode({**q,'all':'0' if allrows else '1'}),'Показать только изменения' if allrows else 'Показать все атрибуты','button')
                body+='<div class="card">'+p.table(['Путь','Ключ массива','До','После','Тип'],[[p.E(x['PATH']),p.E(x['ARRAY_KEY']),'<i>отсутствует</i>' if x['CHANGE_TYPE']=='ATTRIBUTE_NEW' else p.E(json.dumps(x['VALUE_BEFORE'],ensure_ascii=False)),'<i>отсутствует</i>' if x['CHANGE_TYPE']=='ATTRIBUTE_LOST' else p.E(json.dumps(x['VALUE_AFTER'],ensure_ascii=False)),p.E(x['CHANGE_TYPE'])] for x in rows if allrows or x['DIFF_FLAG']])+'</div><details class="card"><summary>Разрезы и сведения по продуктам</summary><pre>'+p.E(json.dumps(seg,ensure_ascii=False,indent=2))+'</pre></details>'
                return self.send(p.shell(user,app,body,'run'))
            ids=set(json.loads(q['ids'])) if 'ids' in q else set().union(*(apps for _,apps in reports.selected(eid,q)))
            with reports.connect(eid) as c:apps=[x for x in Mart(c).segments() if x['APP_ID'] in ids]
            offset=max(0,int(q.get('offset',0)));rows=[]
            for x in apps[offset:offset+100]:rows.append([p.link(base+'/applications/'+x['APP_ID']+'?'+urlencode(q),x['APP_ID']),p.E(x['DECISION_BEFORE']),p.E(x['DECISION_AFTER']),p.E(x['PRODUCTS'])])
            body=head+'<p>'+str(len(apps))+' заявок. '+p.link(base+'/pivot?'+urlencode({k:v for k,v in q.items() if k not in ('filter','ids','offset')}),'Вернуться к сводной')+'</p><div class="card">'+p.table(['Заявка','До','После','Продукты'],rows)+'</div>'+self.pagination(base+'/applications',q,offset,len(apps))
            return self.send(p.shell(user,'Заявки',body,'run'))
        raise FileNotFoundError('Страница не найдена')
    def pagination(self,url,q,offset,total):
        return '<div class="row">'+(p.link(url+'?'+urlencode({**q,'offset':max(0,offset-100)}),'← Назад','button') if offset else '')+'<span>'+str(min(offset+1,total))+'–'+str(min(offset+100,total))+' из '+str(total)+'</span>'+(p.link(url+'?'+urlencode({**q,'offset':offset+100}),'Далее →','button') if offset+100<total else '')+'</div>'
    def pivot(self,e,r,head):
        q=self.q;eid=e['id'];base='/executions/'+eid;dims=q.get('group','PATH,CHANGE_TYPE').split(',')
        allowed=('PATH','CHANGE_TYPE','ARRAY_KEY','VALUE_ADJUSTED','PREFIX','CLASS','SUFFIX','KEY')
        if not dims or len(dims)>4 or any(x not in allowed for x in dims):raise ValueError('Выберите 1–4 измерения')
        fields='<div class="row"><div><label>Группировки через запятую</label>'+p.input_('group',','.join(dims))+'</div><div><label>Путь содержит</label>'+p.input_('path',q.get('path',''))+'</div><div><label>Продукт (ID)</label>'+p.input_('product',q.get('product',''))+'</div><div><label>Различия</label>'+p.select('diff',[('1','С изменениями'),('0','Без изменений'),('all','Все')],q.get('diff','1'))+'</div><div><label>Решение</label>'+p.select('decision',[('','Любое'),('1','Изменилось'),('0','Не изменилось')],q.get('decision',''))+'</div>'+p.button('Построить')+'</div>'
        groups={}
        for f,apps in reports.selected(eid,q):
            key=tuple(str(f[x]) for x in dims);g=groups.setdefault(key,{'BEFORE':set(),'AFTER':set()});g[f['SOURCE']].update(apps)
        rows=sorted(groups.items(),key=lambda kv:(-len(kv[1]['BEFORE']|kv[1]['AFTER']),kv[0]));offset=max(0,int(q.get('offset',0)));render=[]
        for key,g in rows[offset:offset+100]:
            query={**q,'filter':json.dumps(dict(zip(dims,key)),ensure_ascii=False)};query.pop('offset',None)
            render.append([*[p.E(x) for x in key],str(len(g['BEFORE'])),str(len(g['AFTER'])),p.link(base+'/applications?'+urlencode(query),str(len(g['BEFORE']|g['AFTER'])))])
        body=head+'<div class="card"><form method="get">'+fields+'</form><p class="muted">Доступные измерения: '+', '.join(allowed)+'. Считаются уникальные заявки внутри группы и среза. Количества разных атрибутов нельзя суммировать как общее число заявок.</p></div><div class="card">'+p.table([*dims,'BEFORE','AFTER','Заявки'],render)+'</div>'+self.pagination(base+'/pivot',q,offset,len(rows))
        return self.send(p.shell(self.user,'Сводный отчёт',body,'run'))

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8765);parser.add_argument('--host',default='127.0.0.1');recovery=parser.add_mutually_exclusive_group();recovery.add_argument('--create-admin',metavar='LOGIN');recovery.add_argument('--reset-password',metavar='LOGIN');parser.add_argument('--import-demo',action='store_true');args=parser.parse_args()
    if args.reset_password:
        if args.import_demo:raise SystemExit('--import-demo не используется при сбросе пароля')
        if not (s.STATE/'app.sqlite').exists():raise SystemExit('База пользователей не найдена. Проверьте папку установки и DD_STATE.')
        with s.db() as c:
            if not c.execute('SELECT id FROM users WHERE login=?',(args.reset_password,)).fetchone():raise SystemExit('Пользователь с таким логином не найден')
        password=getpass.getpass('Новый пароль (минимум 8 символов): ')
        if password!=getpass.getpass('Повторите новый пароль: '):raise SystemExit('Пароли не совпали. Пароль не изменён.')
        try:s.reset_password(args.reset_password,password)
        except ValueError as ex:raise SystemExit(str(ex))
        print('Пароль изменён. Прежние сессии закрыты. Войдите с новым паролем.');return
    s.init()
    if args.create_admin:
        display=input('Имя администратора: ').strip() or args.create_admin
        password=getpass.getpass('Пароль (минимум 8 символов): ')
        if password!=getpass.getpass('Повторите пароль: '):raise SystemExit('Пароли не совпали')
        user=s.register(args.create_admin,display,password,admin=True)
        if args.import_demo:service.import_demo(user)
        print('Администратор создан. Теперь запустите python app.py');return
    if args.import_demo:raise SystemExit('--import-demo используется вместе с --create-admin')
    sandbox.recover();print(f'Откройте http://{args.host}:{args.port}',flush=True);ThreadingHTTPServer((args.host,args.port),Handler).serve_forever()
if __name__=='__main__':main()
