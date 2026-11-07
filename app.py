"""Decision Diff 2. Standard library, server-rendered multipage application."""
import argparse,ast,email.policy,getpass,hashlib,html,io,json,os,secrets,sqlite3,time,traceback,zipfile
from email.parser import BytesParser
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse,parse_qs,unquote,urlencode
import store as s
import labels,service,sandbox,pages as p,reports,accounts,lifecycle,pivot,ai_review,ai_models,ai_views,ai_prompts,root_admin
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
        try:self.end_headers();self.wfile.write(raw)
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):self.close_connection=True
    def json(self,value,status=200):self.send(json.dumps(value,ensure_ascii=False),status,'application/json; charset=utf-8')
    def redirect(self,path,message=''):
        if message:path+=('&' if '?' in path else '?')+urlencode({'message':message})
        self.send('',303,headers={'Location':path})
    def current_user(self):
        cookie=SimpleCookie(self.headers.get('Cookie',''));token=cookie.get('session')
        if not token:return None
        with s.db() as c:
            row=c.execute('SELECT users.*,sessions.csrf,sessions.root_until FROM sessions JOIN users ON users.id=sessions.user_id WHERE token=? AND expires>?',(token.value,time.time())).fetchone()
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
        if ct.startswith('application/json'):
            value=json.loads(raw)
            if not isinstance(value,dict):raise ValueError('JSON-запрос должен быть объектом')
            return value
        data=parse_qs(raw.decode('utf-8'),keep_blank_values=True)
        return {k:(v if k=='checks' else v[-1]) for k,v in data.items()}
    def get_resource(self,rid,minimum='reader',include_deleted=False):
        with s.db() as c:r=s.resource(c,rid,include_deleted=include_deleted);role=s.require(c,self.user,r,minimum);download=s.can_download(c,self.user,r)
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
            if self.path_only.startswith('/api/') or self.path_only.endswith('/facts') or (self.path_only.endswith('/layout') and self.headers.get('Content-Type','').startswith('application/json')):self.json({'error':str(ex)},code)
            else:self.send(p.shell(self.user,'Не удалось выполнить действие','<div class="error">'+p.E(str(ex))+'</div><p>Вернитесь назад кнопкой браузера или откройте список ресурсов.</p>'),code)
        except Exception:
            traceback.print_exc();self.send(p.shell(self.user,'Ошибка сервера','<div class="error">Подробности записаны в журнал сервера.</div>'),500)
    def route(self,post):
        path=self.path_only;q=self.q;f=self.form;user=self.user
        if path=='/favicon.ico' and not post:return self.send((s.ROOT/'web/favicon.svg').read_bytes(),kind='image/svg+xml')
        if path.startswith('/assets/') and not post:
            relative=Path(path[len('/assets/'):]);file=s.ROOT/'web'/relative
            if '..' in relative.parts or relative.is_absolute() or not file.is_file() or file.resolve().is_relative_to((s.ROOT/'web').resolve()) is False:raise FileNotFoundError('Файл не найден')
            kinds={'.css':'text/css','.js':'text/javascript','.woff':'font/woff','.woff2':'font/woff2','.ttf':'font/ttf','.svg':'image/svg+xml','.txt':'text/plain; charset=utf-8','.json':'application/json; charset=utf-8'}
            if file.suffix not in kinds:raise FileNotFoundError('Файл не найден')
            return self.send(file.read_bytes(),kind=kinds[file.suffix])
        if path in ('/login','/register'):
            if not post:return self.send(p.auth(path[1:],q.get('message','')))
            if path=='/register':user=s.register(f['login'],f['display'],f['password'])
            else:user=root_admin.authenticate(f['login'],f['password'],self.client_address[0])
            token=s.session(user);return self.send('',303,headers={'Set-Cookie':f'session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=86400','Location':'/runs' if user['status']=='active' else '/account'})
        if not user:return self.redirect('/login')
        if path=='/logout' and post:
            cookie=SimpleCookie(self.headers.get('Cookie',''))
            with s.db() as c:c.execute('DELETE FROM sessions WHERE token=?',(cookie['session'].value,))
            return self.send('',303,headers={'Location':'/login','Set-Cookie':'session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0'})
        if path=='/account':return self.send(p.pending(user))
        if user['status']!='active':return self.redirect('/account')
        if path=='/':return self.redirect('/runs')
        if path=='/root' or path.startswith('/root/') or path.startswith('/api/root/'):return root_admin.route(self,post)
        if path=='/api/me/ai-prompt':
            if post:
                action=f.get('action','save')
                if action not in ('save','reset'):raise ValueError('Неизвестное действие с промптом')
                pref=ai_prompts.save(user,f.get('system_prompt',''),f.get('revision'),reset=action=='reset')
            else:pref=ai_prompts.read(user)
            return self.json(pref)
        if path=='/profile/ai-prompt':
            if not post:return self.redirect('/profile')
            action=f.get('action','save')
            if action not in ('save','reset'):raise ValueError('Неизвестное действие с промптом')
            try:ai_prompts.save(user,f.get('system_prompt',''),f.get('prompt_revision'),reset=action=='reset')
            except ValueError as ex:return self.send(p.shell(user,'Личный промпт Gemini',p.ai_prompt_fields(user,True,submitted=f,error=str(ex)),'profile',extra='<script defer src="/assets/prompts.js"></script>'),422)
            return self.redirect('/profile','Личный промпт Gemini сохранён')
        if path=='/profile':
            with s.db() as c:target=dict(c.execute('SELECT * FROM users WHERE id=?',(user['id'],)).fetchone())
            if post:
                try:revoke=accounts.update(user,user['id'],f)
                except (ValueError,PermissionError) as ex:return self.send(self.profile_page(target,submitted=f,error=str(ex)),422)
                return self.redirect('/login' if revoke else '/profile','Изменения сохранены. Войдите заново.' if revoke else 'Профиль обновлён')
            return self.send(self.profile_page(target,flash=q.get('message','')))
        if path=='/trash':
            resources,executions=lifecycle.trash(user);return self.send(p.trash_page(user,resources,executions,q.get('message','')))

        if path=='/api/script-info' and post:
            name,desc=s.script_info(f['code'],f['kind'],validate=False);return self.json({'name':name,'description':desc})
        if path.startswith('/admin'):
            if not user['admin']:raise PermissionError('Требуются права администратора')
            if path.startswith('/admin/users/') and path.rsplit('/',1)[-1] in ('delete','restore'):
                uid=path.split('/')[3];action=path.rsplit('/',1)[-1]
                with s.db() as c:
                    row=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
                    if not row:raise FileNotFoundError('Пользователь не найден')
                    target=dict(row)
                if post:
                    accounts.delete_user(user,uid,f['revision'],restore=action=='restore')
                    return self.redirect('/admin','Пользователь восстановлен в заблокированном состоянии' if action=='restore' else 'Пользователь удалён; история и ресурсы сохранены')
                owned=len([x for x in s.visible(user) if x['owner']==uid])
                body='<div class="card"><p>'+p.E(target['display'])+' · '+p.E(target['login'])+'</p><p>При удалении доступ закрывается и сессии отзываются. Ресурсы ('+str(owned)+') и авторство сохраняются. Восстановленный пользователь будет заблокирован до отдельного разрешения администратора.</p>'+p.form(user,path,p.input_('revision',target['revision'],'hidden')+p.button('Восстановить пользователя' if action=='restore' else 'Удалить пользователя'))+'</div>'
                return self.send(p.shell(user,'Восстановление пользователя' if action=='restore' else 'Удаление пользователя',body,'admin'))
            if path.startswith('/admin/users/') and path.endswith('/edit'):
                uid=path.split('/')[3]
                with s.db() as c:
                    target=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
                    if not target:raise FileNotFoundError('Пользователь не найден')
                    target=dict(target)
                if post:
                    try:revoke=accounts.update(user,uid,f,admin_mode=True)
                    except (ValueError,PermissionError) as ex:return self.send(p.user_form(user,target,True,submitted=f,error=str(ex)),422)
                    return self.redirect('/login' if revoke and uid==user['id'] else '/admin','Изменения сохранены')
                return self.send(p.user_form(user,target,True))
            if path=='/admin/demo' and post:service.import_demo(user);return self.redirect('/runs','Демонстрационные ресурсы добавлены')
            if path.startswith('/admin/users/') and post:
                uid=path.rsplit('/',1)[1];status=f['status']
                if status not in ('active','rejected','blocked'):raise ValueError('Неверный статус')
                with s.db() as c:
                    target=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
                    if not target or target['admin'] or target['status']=='deleted' or target['id']==user['id']:raise PermissionError('Нельзя изменить этого пользователя')
                    c.execute('UPDATE users SET status=?,revision=revision+1 WHERE id=?',(status,uid))
                    # Approval and blocking both revoke old sessions; pending users log in again.
                    c.execute('DELETE FROM sessions WHERE user_id=?',(uid,))
                    s.audit(c,user,None,'Изменение статуса пользователя',{'user':uid,'login':target['login'],'before':target['status'],'after':status})
                return self.redirect('/admin','Статус изменён; пользователю нужно войти заново')
            with s.db() as c:users=[dict(x) for x in c.execute("SELECT * FROM users WHERE status="+("'deleted'" if q.get('deleted')=='1' else "'active' OR status IN ('pending','blocked','rejected')")+" ORDER BY created DESC")];events=[dict(x) for x in c.execute('SELECT * FROM audit WHERE resource IS NULL ORDER BY id DESC LIMIT 100')]
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
            with s.db() as c:executions=[dict(x) for x in c.execute('SELECT executions.*,users.display FROM executions JOIN users ON actor=users.id WHERE run=? AND executions.deleted_at IS NULL AND executions.purged_at IS NULL ORDER BY created DESC',(rid,))]
            return self.send(p.run_page(user,r,role,executions,q.get('message','')))
        if collection=='resources' and len(parts)==3:
            rid,action=parts[1:];r,role,download=self.get_resource(rid,include_deleted=action in ('delete','restore','purge','history','version'))
            if action in ('delete','restore','purge'):
                if role!='owner':raise PermissionError('Удалять и восстанавливать ресурс может только владелец или администратор')
                if r['purged_at'] is not None:raise FileNotFoundError('Ресурс удалён навсегда')
                if post:
                    errors=lifecycle.resource_action(user,rid,action,f['revision'],f.get('confirm',''));reports.facts.cache_clear()
                    message='Ресурс восстановлен' if action=='restore' else 'Ресурс перемещён в корзину' if action=='delete' else 'Файлы ресурса удалены; история сохранена'
                    if errors:message+='; некоторые файлы не удалось очистить — проверьте права на каталог хранения'
                    return self.redirect('/'+p.URLS[r['kind']]+'/'+rid if action=='restore' else '/trash',message)
                with s.db() as c:
                    refs=lifecycle.dependencies(c,rid) if action=='purge' else []
                    for ref in refs:
                        rr=s.resource(c,ref.get('run',ref['id']),include_deleted=True)
                        if s.role(c,user,rr):
                            ref['label']=('Выполнение '+ref['id'][:12]+' · '+rr['name']) if ref['kind']=='execution' else rr['name']
                            ref['url']='/trash' if rr['deleted_at'] is not None else '/runs/'+rr['id'] if ref['kind']=='execution' else '/resources/'+rr['id']+'/history'
                        else:ref['label']='Недоступный связанный ресурс — обратитесь к его владельцу или администратору';ref['url']=''
                return self.send(p.deletion_page(user,r,action,refs=refs))
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
            if len(parts)>=4 and parts[3]=='ai':
                if len(parts)==5 and parts[4]=='models' and not post:
                    with s.db() as c:s.require(c,user,s.resource(c,e['run']),'editor')
                    return self.json({'models':ai_models.catalog(),'message':'Список получен из Google (кэш до 15 минут). Наличие в каталоге не гарантирует успешный запуск или свободную квоту.'})
                if len(parts)!=4:raise FileNotFoundError('Страница не найдена')
                if post:
                    aid=ai_review.start(user,eid,f.get('model'),f.get('prompt_mode','personal'),f.get('system_prompt'),f.get('prompt_revision'));data=ai_views.panel(user,eid,aid)
                    return self.json(data,202 if data['state'] in ('queued','running') else 200)
                return self.json(ai_views.panel(user,eid,q.get('analysis')))
            folder=s.STATE/'executions'/eid
            status=sandbox.status_file(folder/'result') if e['state'] in ('running','queued') else {}
            return self.json({'state':e['state'],'progress':status.get('progress',100 if e['state']=='done' else 0),'stage':status.get('stage',e['state']),'error':e['error']})
        raise FileNotFoundError('Страница не найдена')
    def get_execution(self,eid,include_deleted=False):
        with s.db() as c:
            e=c.execute('SELECT * FROM executions WHERE id=?',(eid,)).fetchone()
            if not e or (not include_deleted and (e['deleted_at'] is not None or e['purged_at'] is not None)):raise FileNotFoundError('Выполнение не найдено или удалено')
            r=s.resource(c,e['run'],include_deleted=include_deleted);s.require(c,self.user,r)
        return dict(e),r
    def execution(self,parts,post):
        eid=parts[1];section='/'+parts[2] if len(parts)>2 else '';e,r=self.get_execution(eid,include_deleted=section in ('/delete','/restore','/purge'));user=self.user;q=self.q;base='/executions/'+eid
        if section in ('/delete','/restore','/purge'):
            with s.db() as c:s.require(c,user,r,'owner')
            if e['purged_at'] is not None or r['purged_at'] is not None:raise FileNotFoundError('Выполнение удалено навсегда')
            if post:
                errors=lifecycle.execution_action(user,eid,section[1:],self.form.get('confirm',''));reports.facts.cache_clear()
                message='Выполнение восстановлено' if section=='/restore' else 'Выполнение перемещено в корзину' if section=='/delete' else 'Файлы выполнения удалены; история сохранена'
                if errors:message+='; некоторые файлы не удалось очистить — проверьте права на каталог хранения'
                return self.redirect(base if section=='/restore' else '/trash',message)
            return self.send(p.deletion_page(user,e,section[1:],execution=True))
        if section=='/layout':
            if not post:raise ValueError('Для сохранения макета используйте кнопку сводной')
            layout=self.form.get('layout',{})
            if isinstance(layout,str):layout=json.loads(layout)
            kind=self.form.get('kind','exact');pivot.save(user,eid,layout,kind)
            return self.json({'saved':True}) if kind=='wdr' else self.redirect(base+'/pivot','Вид сохранён')
        if section=='/facts':
            if e['state']!='done':raise ValueError('Дождитесь завершения расчёта')
            rows=[{**{k:(labels.value(k,v) if k in ('PATH','CHANGE_TYPE','SOURCE','DIFF_FLAG') else v) for k,v in fact.items() if k!='APP_IDS'},'CNT':len(apps)} for fact,apps in reports.selected(eid,pivot.query(q))]
            if len(json.dumps(rows,ensure_ascii=False).encode())>950000:return self.json({'error':'Объём данных превышает лимит WebDataRocks 1 МБ. Сузьте фильтры; точная сводная выше доступна без этого ограничения.'},400)
            return self.json({'rows':rows})
        if section=='/recheck':
            with s.db() as c:s.require(c,user,r,'editor')
            if e['state']!='done':raise ValueError('Нужно успешно завершённое выполнение')
            error=''
            if post:
                try:return self.redirect('/executions/'+service.recheck(user,eid,self.form))
                except (ValueError,PermissionError) as ex:error=str(ex)
            manifest=json.loads(e['manifest']);cfg=manifest.get('config',{});chosen=self.form.get('checks',cfg.get('checks',[]));fields=''
            for item in s.visible(user,'script'):
                if json.loads(item['body'])['script_kind']=='check':fields+='<label class="checklabel"><input type="checkbox" name="checks" value="'+item['id']+'" '+('checked' if item['id'] in chosen else '')+'>'+p.E(item['name'])+'<small>'+p.E(item['description'])+'</small></label>'
            fields+='<label>Параметры: {ID проверки: {параметры}}</label>'+p.textarea('params',self.form.get('params',json.dumps(cfg.get('check_params',{}),ensure_ascii=False,indent=2)))+p.button('Запустить проверки по этой витрине')
            body=p.execution_head(user,r,e,section)+'<div class="card"><p>Новые проверки выполняются в контейнере по готовой витрине. Будет создано отдельное выполнение с прежними данными и зафиксированными версиями выбранных проверок. Старые результаты сохраняются.</p>'+('<p class="error">'+p.E(error)+'</p>' if error else '')+p.form(user,base+'/recheck',fields)+'</div>'
            return self.send(p.shell(user,'Проверки по готовой витрине',body,'run'),422 if error else 200)
        if section=='/ai':
            if post:
                aid=ai_review.start(user,eid,self.form.get('model'),self.form.get('prompt_mode','personal'),self.form.get('system_prompt'),self.form.get('prompt_revision'));return self.redirect(base+'/ai?analysis='+aid)
            return self.send(self.ai_page(e,r))
        if post:raise ValueError('Результат выполнения неизменяем. Создайте новое выполнение.')
        head=p.execution_head(user,r,e,section)
        with s.db() as c:
            if s.role(c,user,r)=='owner' and e['state'] not in ('running','queued'):head+='<p>'+p.link(base+'/delete','Удалить выполнение','button danger')+'</p>'
        manifest=json.loads(e['manifest']);script_names={x['file']:x['name'] for x in manifest['scripts']}
        if section=='/code':
            srid=q.get('script');item=next((x for x in manifest['scripts'] if x['resource']==srid),None)
            if not item:raise FileNotFoundError('Скрипт не найден')
            with s.db() as c:sr=s.resource(c,srid,include_deleted=True);allowed=s.can_download(c,user,sr)
            if not allowed:raise PermissionError('Нет доступа к исходному коду выбранного скрипта')
            code=(s.STATE/'executions'/eid/'input/scripts'/item['file']).read_text(encoding='utf-8')
            return self.send(p.shell(user,item['name'],head+'<div class="card"><p>Зафиксированная версия '+str(item['revision'])+' · SHA-256 '+p.E(item['sha256'])+'</p><pre>'+p.E(code)+'</pre></div>','run'))
        if section=='':
            status={}
            if e['state']=='done':status=reports.artifact(eid,'status.json')
            elif e['state'] in ('running','queued'):status=sandbox.status_file(s.STATE/'executions'/eid/'result')
            body=head+'<div class="card" data-execution="'+eid+'" data-initial-state="'+p.E(e['state'])+'"><h2>Состояние выполнения</h2><progress max="100" value="'+str(status.get('progress',0))+'"></progress><p data-stage>'+p.E(status.get('stage',labels.state(e['state'])))+'</p><p class="muted">Начало: '+p.date(e['created'])+' · завершение: '+p.date(e['finished'])+'</p>'+('<pre class="error">'+p.E(e['error'])+'</pre>' if e['error'] else '')+'</div>'
            if e['state']=='done':
                summary=reports.artifact(eid,'summary.json');total=summary['total_apps'] or 1
                same=summary['changed_apps']-summary['decision_changed']
                def num(n):return f'{n:,}'.replace(',','\u202f')
                body+='<section class="verdict"><p class="verdict-main"><b>'+num(summary['decision_changed'])+'</b> из '+num(summary['total_apps'])+' '+labels.plural(summary['total_apps'],'заявки','заявок','заявок')+' сменили решение</p><p class="verdict-sub">Различия в ответе есть у '+num(summary['changed_apps'])+', из них у '+num(same)+' решение осталось прежним. Сравнено '+num(summary['rows'])+' атрибутов.</p>'
                order=[('APPROVE → REJECT','flip-reject'),('REJECT → APPROVE','flip-approve'),('APPROVE → APPROVE','keep-approve'),('REJECT → REJECT','keep-reject')]
                known=dict(summary['transitions']);segments=[(k,c,known.pop(k,0)) for k,c in order]+[(k,'keep-other',v) for k,v in known.items()]
                body+='<div class="flowbar" role="img" aria-label="Распределение заявок по переходам решений">'+''.join('<span class="'+c+'" style="flex-grow:'+str(v)+'" title="'+p.E(labels.transition(k))+': '+str(v)+'"></span>' for k,c,v in segments if v)+'</div>'
                body+='<ul class="flowlegend">'+''.join('<li class="'+c+'"><i></i>'+p.E(labels.transition(k))+' <b>'+num(v)+'</b> <span>'+labels.percent(v/total)+'</span></li>' for k,c,v in segments if v)+'</ul></section>'
                if summary.get('input_quality_available'):
                    body+='<div class="card"><h2>Качество входных данных</h2><p>Входных заявок: '+str(summary['input_apps'])+' · сопоставлено: '+str(summary['total_apps'])+' · исключено: '+str(summary['excluded_apps'])+'.</p><p>Сводная и бизнес-проверки используют только корректные пары BEFORE/AFTER. Проверки ошибок используют все входные заявки.</p>'+p.link(base+'/applications?excluded=1','Посмотреть исключённые заявки','button')+' '+p.link(base+'/input-errors','Скачать причины в JSON','button')+'</div>'
                else:body+='<div class="warning">Для этой витрины качество исходных ответов неизвестно. Выполните новый полный расчёт с обновлённым контейнером.</div>'
                try:causes=reports.decision_causes(eid)
                except Exception:causes=[]
                if causes:
                    crow=[[p.E(k),p.link(base+'/applications?'+urlencode({'ids':json.dumps(ids)}),str(len(ids))),p.E(', '.join(labels.transition(t)+': '+str(n) for t,n in sorted(tr.items())))] for k,ids,tr in causes]
                    body+='<div class="card"><h2>Почему сменилось решение</h2><p class="muted">Каждая заявка со сменой решения отнесена к рычагам стратегии, которые изменились в её ответе. Сумма строк равна '+str(summary['decision_changed'])+'.</p>'+p.table(['Причина','Заявок','Смена решения'],crow)+'<p>Различия есть, а решение то же: <b>'+str(same)+'</b> заявок. '+p.link(base+'/checks','Подробнее в отчёте проверок')+'</p></div>'
            logs=[]
            for x in status.get('logs',[]):
                script=x.get('script');item=next((z for z in manifest['scripts'] if z['file']==script),None)
                code=(p.link(base+'/code?script='+item['resource'],'Снимок v'+str(item['revision'])+' · '+item['name'])+'<br>'+p.link('/scripts/'+item['resource'],'Текущий скрипт')) if item else ''
                stage=x.get('stage') or ''
                if item and script and script in stage:stage=stage.replace(script,item['name'])
                logs.append([p.E(stage),p.E(x.get('seconds')),code])
            body+='<div class="card"><h2>Журнал этапов</h2>'+p.table(['Этап','Секунды','Код'],logs)+'</div>'
            output=s.STATE/'executions'/eid/'console.log';text=output.read_text(encoding='utf-8',errors='replace')[:1024*1024] if output.exists() else ''
            for item in manifest['scripts']:text=text.replace(item['file'],item['name'])
            body+='<details class="card"><summary>Вывод Python</summary><pre>'+(p.E(text) if text.strip() else '<span class="muted">Скрипты ничего не выводили.</span>')+'</pre></details><details class="card"><summary>Входы, версии и контрольные суммы</summary><pre>'+p.E(json.dumps(manifest,ensure_ascii=False,indent=2))+'</pre></details>'
            return self.send(p.shell(user,'Выполнение '+eid[:12],body,'run'))
        if e['state']!='done':return self.send(p.shell(user,'Результат ещё не готов',head+'<div class="card">Дождитесь успешного завершения выполнения.</div>','run'))
        if section=='/input-errors':
            file=s.STATE/'executions'/eid/'artifacts/input_errors.json'
            if not file.is_file():raise FileNotFoundError('У этого выполнения нет отчёта качества. Создайте новый полный расчёт с обновлённым контейнером.')
            return self.send(file.read_bytes(),kind='application/json; charset=utf-8',headers={'Content-Disposition':'attachment; filename="input-errors.json"'})
        if section in ('/checks','/export'):
            checks=reports.artifact(eid,'checks.json');rows=[]
            try:names=reports.product_names(eid)
            except Exception:names={}
            def argument(arg):
                if labels.looks_like_path(arg) and labels.path(arg)!=arg:return p.E(labels.path(arg))+'<small class="muted">'+p.E(arg)+'</small>'
                if isinstance(arg,str) and arg in names:return p.E(labels.product(arg,names))
                return p.E(arg)
            for x in checks['rows']:
                low=x['CONFIDENCE']=='LOW';n=len(x['APP_IDS'])
                qs=urlencode({'ids':json.dumps(x['APP_IDS'])})
                verdict='<span class="badge '+('low ' if low else '')+('fail' if x['RESULT']=='FAIL' else 'pass')+'">'+p.E(x['RESULT'])+'</span>'+('<small class="muted">мало данных</small>' if low else '')
                rows.append([('<span class="low">' if low else '')+p.E(labels.check(x['LABEL']))+('</span>' if low else ''),'<b>'+p.E(labels.source(x['SOURCE']))+'</b><br>'+argument(x['ARGUMENT']),'<span class="nowrap">'+str(x['CNT'])+' / '+str(x['TOTAL'])+'</span>','<span class="nowrap">'+p.E(labels.percent(x['METRIC']))+'</span>',verdict,p.link(base+'/applications?'+qs,str(n)+' '+labels.plural(n,'заявка','заявки','заявок')) if n else '—'])
            body='<div class="card"><p class="muted">FAIL — условие проверки сработало, PASS — нарушений не найдено. «Мало данных» и бледная строка — срабатываний немного, уверенности в проблеме нет. В «Примерах» — до 20 заявок, ссылка ведёт к ним.</p>'+p.table(['Проверка','Срез и аргумент','Заявок','Доля','Результат','Примеры'],rows)+'</div>'
            with s.db() as c:analysis=c.execute("SELECT a.*,u.display FROM analyses a JOIN users u ON u.id=a.actor WHERE execution=? AND a.state='done' ORDER BY a.created DESC LIMIT 1",(eid,)).fetchone()
            body+='<div class="card"><h2>Второе мнение Gemini</h2>'+(p.ai_result(dict(analysis)) if analysis else '<p>Анализ ещё не запрашивался или не завершён.</p>')+p.link(base+'/ai','Открыть анализ и историю запросов')+'</div>'
            if section=='/export':
                origin=os.environ.get('DD_PUBLIC_URL','http://'+self.headers['Host']).rstrip('/')
                parsed=urlparse(origin)
                if parsed.scheme not in ('http','https') or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:raise ValueError('DD_PUBLIC_URL: ожидается http(s)://имя-сервера[:порт] без пути')
                body=body.replace('href="/','href="'+p.E(origin)+'/')
                doc='<!doctype html><html lang="ru"><meta charset="utf-8"><title>Отчёт проверок</title><style>'+ (s.ROOT/'web/style.css').read_text()+'</style><main><h1>'+p.E(r['name'])+'</h1><p>Выполнение '+eid+' · '+p.date(e['created'])+'</p>'+p.link(origin+base+'/pivot','Открыть сводный отчёт приложения')+body+'</main></html>'
                return self.send(doc,headers={'Content-Disposition':'attachment; filename="report-'+eid+'.html"'})
            return self.send(p.shell(user,'Отчёт проверок',head+p.link(base+'/export','Скачать HTML','button')+body,'run'))
        if section=='/pivot':return self.send(pivot.render(user,e,head,q))
        if section=='/applications':
            if len(parts)>3:
                app=parts[3]
                with reports.connect(eid) as c:
                    mart=Mart(c);rows=list(mart.rows(app_ids=[app]));seg=next((x for x in mart.segments() if x['APP_ID']==app),{});quality=mart.input_status([app]);pair_errors=mart.pair_errors([app])
                if not rows and not seg and not quality:raise FileNotFoundError('Заявка не найдена')
                if quality and not seg:
                    body=head+'<p>'+p.link(base+'/applications?'+urlencode({k:v for k,v in q.items() if k!='all'}),'← Назад к выбранному списку')+'</p><div class="card input-quality"><h2>Заявка исключена из сравнения</h2><p>Нет корректной пары BEFORE/AFTER. Строки этой заявки не включены в сводную и бизнес-проверки.</p>'+p.table(['Срез','Статус','Причина'],[[p.E(x['SOURCE']),p.E(x['STATUS']),p.E(x['ERROR'] or 'Ответ прочитан и разобран')] for x in quality])+(p.table(['Общий этап пары','Причина'],[[p.E(x['STAGE']),p.E(x['ERROR'])] for x in pair_errors]) if pair_errors else '')+'</div>'
                    return self.send(p.shell(user,app,body,'run'))
                allrows=q.get('all')=='1'
                try:names=reports.product_names(eid)
                except Exception:names={}
                body=head+'<p>'+p.link(base+'/applications?'+urlencode({k:v for k,v in q.items() if k not in ('all',)}),'← Назад к выбранному списку')+'</p>'+'<div class="card"><h2>Заявка '+p.E(app)+'</h2><p>Решение: <b>'+p.E(labels.decision(seg.get('DECISION_BEFORE','')))+'</b> → <b>'+p.E(labels.decision(seg.get('DECISION_AFTER','')))+'</b>'+(' <span class="badge fail">решение сменилось</span>' if seg.get('DECISION_CHANGED') else ' <span class="badge">решение то же</span>')+'</p><p class="muted">Продукты: '+p.E(labels.products(seg.get('PRODUCTS',''),names) or '—')+' · изменённых атрибутов: '+str(sum(1 for x in rows if x['DIFF_FLAG']))+'</p></div>'+p.link(self.path_only+'?'+urlencode({**q,'all':'0' if allrows else '1'}),'Показать только изменения' if allrows else 'Показать все атрибуты','button')
                focus=q.get('focus_path') or json.loads(q.get('filter','{}')).get('PATH')
                focused=[x for x in rows if x['PATH']==focus and ('focus_array' not in q or x['ARRAY_KEY']==q['focus_array'])]
                if focused:
                    with reports.connect(eid) as c:
                        originals=[json.loads(x[0]) for x in c.execute('SELECT data FROM normalized WHERE APP_ID=? AND PATH=?',(app,focus))]
                    body+='<section class="focused-attribute" id="focus"><h2>Выбранный атрибут: '+p.E(labels.path(focus))+'</h2>'+('<p class="muted">'+p.E(focus)+'</p>' if labels.path(focus)!=focus else '')
                    for x in focused:
                        body+='<h3>Элемент массива: '+p.E(labels.array_key(x['ARRAY_KEY'],names))+'</h3><p>'+p.E(pivot.TYPES.get(x['CHANGE_TYPE'],x['CHANGE_TYPE']))+'</p>'
                        for source in ('BEFORE','AFTER'):
                            original=next((n for n in originals if n['SOURCE']==source and n['ARRAY_KEY']==x['ARRAY_KEY']),None)
                            raw=original.get('RAW') if original else None
                            body+='<h4>'+p.E(labels.source(source))+'</h4><pre class="attribute-value">'+p.E(raw if original else 'Атрибут отсутствует')+'</pre>'
                    body+='</section>'
                body+='<div class="card">'+p.table(['Атрибут','Элемент массива','До','После','Тип'],[[p.link(self.path_only+'?'+urlencode({**q,'focus_path':x['PATH'],'focus_array':x['ARRAY_KEY']})+'#focus',labels.path(x['PATH']))+('<br><small class="muted">'+p.E(x['PATH'])+'</small>' if labels.path(x['PATH'])!=x['PATH'] else ''),p.E(labels.array_key(x['ARRAY_KEY'],names)),'<i>отсутствует</i>' if x['CHANGE_TYPE']=='ATTRIBUTE_NEW' else p.E(json.dumps(x['VALUE_BEFORE'],ensure_ascii=False)),'<i>отсутствует</i>' if x['CHANGE_TYPE']=='ATTRIBUTE_LOST' else p.E(json.dumps(x['VALUE_AFTER'],ensure_ascii=False)),p.E(pivot.TYPES.get(x['CHANGE_TYPE'],x['CHANGE_TYPE']))] for x in rows if allrows or x['DIFF_FLAG']])+'</div><details class="card"><summary>Разрезы и сведения по продуктам</summary><pre>'+p.E(json.dumps(seg,ensure_ascii=False,indent=2))+'</pre></details>'

                return self.send(p.shell(user,app,body,'run'))
            ids=set(json.loads(q['ids'])) if 'ids' in q else (set() if q.get('excluded')=='1' else set().union(*(apps for _,apps in reports.selected(eid,q))))
            with reports.connect(eid) as c:
                mart=Mart(c)
                if q.get('excluded')=='1':ids=set(mart.excluded_app_ids())
                apps=[x for x in mart.segments() if x['APP_ID'] in ids];present={x['APP_ID'] for x in apps}
                excluded=set(mart.excluded_app_ids()) & ids
                apps.extend(dict(APP_ID=appid,DECISION_BEFORE='Не сравнивалась',DECISION_AFTER='Исключена',PRODUCTS='') for appid in sorted(excluded-present))
                apps.sort(key=lambda x:x['APP_ID'])
            offset=max(0,int(q.get('offset',0)));rows=[]
            try:names=reports.product_names(eid)
            except Exception:names={}
            for x in apps[offset:offset+100]:
                changed=bool(x.get('DECISION_CHANGED'))
                after=p.E(labels.decision(x['DECISION_AFTER']))
                rows.append([p.link(base+'/applications/'+x['APP_ID']+'?'+urlencode(q)+('#focus' if q.get('focus_path') else ''),x['APP_ID']),p.E(labels.decision(x['DECISION_BEFORE'])),('<b class="changed">'+after+' ●</b>' if changed else after),p.E(labels.products(x['PRODUCTS'],names))])
            body=head+'<p>'+str(len(apps))+' '+labels.plural(len(apps),'заявка','заявки','заявок')+'. <span class="muted">● — решение сменилось.</span> '+p.link(base+'/pivot?'+urlencode(pivot.query(json.loads(q['return'])) if 'return' in q else {k:v for k,v in q.items() if k not in ('filter','ids','offset','focus_path','focus_array')}),'Вернуться к сводной')+'</p><div class="card">'+p.table(['Заявка','Решение BEFORE (прод)','Решение AFTER (бета)','Продукты'],rows)+'</div>'+self.pagination(base+'/applications',q,offset,len(apps))
            return self.send(p.shell(user,'Заявки',body,'run'))
        raise FileNotFoundError('Страница не найдена')
    def pagination(self,url,q,offset,total):
        return '<div class="row">'+(p.link(url+'?'+urlencode({**q,'offset':max(0,offset-100)}),'← Назад','button') if offset else '')+'<span>'+str(min(offset+1,total))+'–'+str(min(offset+100,total))+' из '+str(total)+'</span>'+(p.link(url+'?'+urlencode({**q,'offset':offset+100}),'Далее →','button') if offset+100<total else '')+'</div>'
    def profile_page(self,target,submitted=None,error='',flash=''):
        html=p.user_form(self.user,target,submitted=submitted,error=error,flash=flash)
        return html.replace('<footer>',p.ai_prompt_fields(self.user,True)+'<footer>').replace('</html>','<script defer src="/assets/prompts.js"></script></html>')
    def ai_page(self,e,r):
        eid=e['id'];base='/executions/'+eid;data=ai_views.panel(self.user,eid,self.q.get('analysis'))
        with s.db() as c:role=s.role(c,self.user,s.resource(c,e['run']))
        ok,message=ai_review.configured();refs,payload=ai_review.evidence(eid)
        selected=data['model'] or ai_models.default();options=ai_models.choices(selected)
        if selected not in {x[0] for x in options}:selected=options[0][0] if options else ''
        body=p.execution_head(self.user,r,e,'/ai')+'<div class="card" data-ai-controller data-ai-api="/api/executions/'+eid+'/ai"><h2>Подробный разбор результатов</h2><p>Анализ включает масштабы изменений, интерпретацию, альтернативные объяснения и порядок проверки гипотез. Выводы опираются на сводку и результаты проверок.</p><p>'+p.E(message)+'</p>'
        if ok and role in ('owner','editor'):
            fields='<label for="gemini-model">Модель Gemini</label>'+p.select('model',options,selected).replace('<select ','<select id="gemini-model" ')
            fields+='<p class="muted">Модель выбирается для этого запроса. Типовой список можно дополнить каталогом Google. Разные модели могут иметь разные цены, ограничения и доступность.</p><button type="button" data-ai-models>Загрузить список из Google</button><p data-ai-model-message role="status"></p><p>Будет отправлена показанная ниже сводка; запрос использует квоту API вашего проекта.</p>'+p.ai_prompt_fields(self.user)+p.button('Отправить сводку в Gemini')
            body+=p.form(self.user,base+'/ai',fields).replace('<form ','<form data-ai-start ')
        body+='<p>Полные ответы СПР, идентификаторы заявок, исходный код и пароли не передаются. Сводка содержит до 80 строк проверок, 30 групп изменённых путей, продуктовые группы и пересечения со сменой решения. Названия и аргументы проверок входят в отправку.</p><details data-ai-preview><summary>Данные для отправки ('+str(len(json.dumps(payload,ensure_ascii=False).encode()))+' байт)</summary><pre>'+p.E(json.dumps(payload,ensure_ascii=False,indent=2))+'</pre></details><p class="error" data-ai-network role="status" hidden></p></div>'+ai_views.container(data)
        return p.shell(self.user,'Анализ Gemini',body,'run',extra='<script defer src="/assets/prompts.js"></script><script defer src="/assets/ai.js"></script>')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8765);parser.add_argument('--host',default='127.0.0.1');recovery=parser.add_mutually_exclusive_group();recovery.add_argument('--create-admin',metavar='LOGIN');recovery.add_argument('--create-root',action='store_true');parser.add_argument('--enable-root-console',action='store_true');recovery.add_argument('--reset-password',metavar='LOGIN');parser.add_argument('--import-demo',action='store_true');parser.add_argument('--local-runner',action='store_true',help='Демо-режим без Docker: процедуры выполняются на этом компьютере без изоляции. Только для одного доверенного пользователя.');args=parser.parse_args()
    if args.reset_password:
        if args.import_demo:raise SystemExit('--import-demo не используется при сбросе пароля')
        if not (s.STATE/'app.sqlite').exists():raise SystemExit('База пользователей не найдена. Проверьте папку установки и DD_STATE.')
        s.init()
        with s.db() as c:
            if not c.execute('SELECT id FROM users WHERE login=?',(args.reset_password,)).fetchone():raise SystemExit('Пользователь с таким логином не найден')
        password=getpass.getpass('Новый пароль (минимум 8 символов): ')
        if password!=getpass.getpass('Повторите новый пароль: '):raise SystemExit('Пароли не совпали. Пароль не изменён.')
        try:s.reset_password(args.reset_password,password)
        except ValueError as ex:raise SystemExit(str(ex))
        print('Пароль изменён. Прежние сессии закрыты. Войдите с новым паролем.');return
    s.init()
    if args.create_root:
        display=input('Имя root: ').strip() or 'Root';password=getpass.getpass('Новый пароль root (минимум 12 символов): ')
        if password!=getpass.getpass('Повторите пароль: '):raise SystemExit('Пароли не совпали')
        try:root_user=s.create_root(display,password)
        except ValueError as ex:raise SystemExit(str(ex))
        if args.import_demo:service.import_demo(root_user)
        print('Root настроен. Прежние сессии root закрыты. Для серверной консоли запускайте app.py с --enable-root-console.');return
    if args.create_admin:
        display=input('Имя администратора: ').strip() or args.create_admin
        password=getpass.getpass('Пароль (минимум 8 символов): ')
        if password!=getpass.getpass('Повторите пароль: '):raise SystemExit('Пароли не совпали')
        user=s.register(args.create_admin,display,password,admin=True)
        if args.import_demo:service.import_demo(user)
        print('Администратор создан. Теперь запустите python app.py');return
    if args.import_demo:raise SystemExit('--import-demo используется вместе с --create-admin')
    if args.local_runner or sandbox.LOCAL:
        sandbox.enable_local()
        if args.host not in ('127.0.0.1','localhost','::1'):raise SystemExit('--local-runner разрешён только с --host 127.0.0.1: код процедур выполняется без изоляции')
        print('ВНИМАНИЕ: локальный режим без Docker. Python-процедуры выполняются на этом компьютере без изоляции.',flush=True)
    sandbox.recover();ai_review.recover();server=ThreadingHTTPServer((args.host,args.port),Handler);server.root_console_enabled=args.enable_root_console;print(f'Откройте http://{args.host}:{args.port}',flush=True);server.serve_forever()
if __name__=='__main__':main()
