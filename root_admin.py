"""Opt-in server console for a locally provisioned root account, never ordinary admins."""
import hashlib,itertools,json,os,shutil,signal,sqlite3,stat,subprocess,tempfile,threading,time
from pathlib import Path
from http.cookies import SimpleCookie
import store as s,accounts,pages as p,reports
MAX_TEXT=2*1024*1024
MAX_OUTPUT=1024*1024
_jobs={};_lock=threading.Lock();_files_lock=threading.Lock();_auth_failures={}

def auth_check(address,stage):
    key=(address,stage);now=time.monotonic()
    with _lock:
        _auth_failures[key]=[x for x in _auth_failures.get(key,[]) if now-x<300]
        if len(_auth_failures[key])>=5:raise PermissionError('Слишком много неверных паролей root с этого адреса. Повторите через 5 минут.')

def auth_result(address,stage,success):
    key=(address,stage)
    with _lock:
        if success:_auth_failures.pop(key,None)
        else:_auth_failures.setdefault(key,[]).append(time.monotonic())
        if len(_auth_failures)>2048:
            for candidate in list(_auth_failures)[:1024]:
                if not _auth_failures[candidate] or time.monotonic()-_auth_failures[candidate][-1]>300:_auth_failures.pop(candidate,None)
            while len(_auth_failures)>2048:_auth_failures.pop(next(iter(_auth_failures)))

def authenticate(login,password,address):
    if login.lower()!='root':return s.authenticate(login,password)
    auth_check(address,'login')
    try:user=s.authenticate(login,password)
    except PermissionError:auth_result(address,'login',False);raise
    auth_result(address,'login',True);return user

def token(handler):
    item=SimpleCookie(handler.headers.get('Cookie','')).get('session')
    return item.value if item else ''

def require(handler,unlocked=True):
    with s.db() as c:
        row=c.execute('SELECT * FROM users WHERE id=?',(handler.user['id'],)).fetchone();user=dict(row) if row else None
        if not s.is_root(user):raise PermissionError('Консоль сервера доступна только локально созданному root')
        if not getattr(handler.server,'root_console_enabled',False):raise PermissionError('Консоль сервера отключена. Локальный оператор должен запустить app.py с --enable-root-console')
        session=c.execute('SELECT root_until FROM sessions WHERE token=? AND user_id=? AND expires>?',(token(handler),user['id'],time.time())).fetchone()
        if not session:raise PermissionError('Сессия root истекла')
        if unlocked and session[0]<time.time():raise PermissionError('Подтвердите пароль root на странице /root')
    return user

def event(user,action,details):
    with s.db() as c:s.audit(c,user,None,action,details)

def path(value):
    if not isinstance(value,str) or not value or '\x00' in value:raise ValueError('Укажите путь на сервере')
    return Path(os.path.abspath(os.path.expanduser(value)))
def stamp(file):
    info=file.lstat();return hashlib.sha256(str((info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)).encode()).hexdigest()
def private_dir(name):
    folder=s.STATE/name;folder.mkdir(exist_ok=True,mode=0o700);return folder

def listing(value,offset=0):
    folder=path(value)
    if not folder.is_dir():raise ValueError('Путь не является каталогом')
    offset=int(offset)
    if not 0<=offset<=1000000:raise ValueError('Неверная страница каталога')
    items=list(itertools.islice(folder.iterdir(),offset,offset+1001));rows=[]
    for child in sorted(items[:1000],key=lambda x:(not x.is_dir(),x.name.lower())):
        try:
            info=child.lstat();rows.append({'name':child.name,'path':str(child),'directory':child.is_dir(),'symlink':child.is_symlink(),'size':info.st_size,'modified':info.st_mtime,'stamp':stamp(child)})
        except OSError:continue
        if len(rows)>=1000:break
    return {'path':str(folder),'parent':str(folder.parent),'entries':rows,'limit':1000,'offset':offset,'has_more':len(items)>1000}

def read_text(value):
    file=path(value)
    if file.is_symlink():raise ValueError('Это ссылка. Для редактирования откройте целевой путь: '+str(file.resolve()))
    if not stat.S_ISREG(file.stat().st_mode):raise ValueError('Редактируются только обычные файлы')
    if file.stat().st_size>MAX_TEXT:raise ValueError('Текстовый редактор: лимит 2 МБ. Используйте скачивание или терминал.')
    before=stamp(file);raw=file.read_bytes()
    if b'\0' in raw:raise ValueError('Бинарный файл: используйте скачивание или редактор базы данных')
    try:text=raw.decode('utf-8')
    except UnicodeError:raise ValueError('Файл не является текстом UTF-8') from None
    if before!=stamp(file):raise ValueError('Файл изменился при чтении. Откройте его заново.')
    return {'path':str(file),'text':text,'stamp':before}

def backup_file(file):
    if not file.exists():return None
    if not file.is_file() or file.is_symlink():raise ValueError('Нельзя заменить каталог или ссылку')
    if file.stat().st_size>64*1024*1024:raise ValueError('Автоматическая копия файла: лимит 64 МБ. Для больших файлов используйте терминал.')
    dest=private_dir('root-backups')/(s.uid()+'-'+file.name);shutil.copy2(file,dest);return str(dest)

def atomic_write(file,raw):
    if not file.parent.is_dir():raise ValueError('Родительский каталог не найден')
    mode=stat.S_IMODE(file.stat().st_mode) if file.exists() else 0o600
    descriptor,name=tempfile.mkstemp(prefix='.dd-root-',dir=file.parent)
    try:
        with os.fdopen(descriptor,'wb') as out:out.write(raw);out.flush();os.fsync(out.fileno())
        os.chmod(name,mode);os.replace(name,file)
    finally:
        if os.path.exists(name):os.unlink(name)

def mutate_file(user,f,files):
    operation=f.get('operation');file=path(f.get('path',''))
    with _files_lock:
        if operation=='write':
            raw=f.get('text','').encode('utf-8')
            if len(raw)>MAX_TEXT:raise ValueError('Текст больше 2 МБ')
            if file.is_symlink() or not file.is_file():raise ValueError('Откройте обычный файл в редакторе')
            if f.get('stamp')!=stamp(file):raise ValueError('Файл уже изменён. Буфер оставлен в редакторе; откройте актуальную версию отдельно.')
            backup=backup_file(file);atomic_write(file,raw);details={'path':str(file),'backup':backup,'bytes':len(raw)}
        elif operation=='upload':
            item=files.get('file')
            if not item:raise ValueError('Выберите файл')
            name=item[0]
            if not name or name in ('.','..') or Path(name).name!=name or '/' in name or '\\' in name:raise ValueError('Неверное имя файла')
            file=file/name
            if file.exists() and f.get('overwrite')!='1':raise ValueError('Файл существует. Отметьте замену или выберите другое имя.')
            backup=backup_file(file);atomic_write(file,item[1]);details={'path':str(file),'backup':backup,'bytes':len(item[1])}
        elif operation=='mkdir':
            name=f.get('name','')
            if name in ('','.','..') or Path(name).name!=name or '/' in name or '\\' in name:raise ValueError('Укажите одно имя нового каталога')
            file=file/name;file.mkdir(mode=0o700);details={'path':str(file)}
        elif operation in ('move','trash'):
            if file==file.parent or s.STATE.resolve().is_relative_to(file.resolve()):raise ValueError('Нельзя переместить корень файловой системы или каталог, содержащий хранилище приложения')
            if f.get('stamp')!=stamp(file):raise ValueError('Объект изменён. Обновите список файлов.')
            if operation=='move':
                dest=path(f.get('destination',''))
                if dest.exists() or dest.is_symlink():raise ValueError('Путь назначения уже занят')
                if file.is_dir() and dest.resolve().is_relative_to(file.resolve()):raise ValueError('Нельзя переместить каталог внутрь себя')
            else:
                key=s.uid();dest=private_dir('root-trash')/key
                (private_dir('root-trash')/(key+'.json')).write_text(json.dumps({'original':str(file),'created':time.time()},ensure_ascii=False),encoding='utf-8')
            shutil.move(str(file),str(dest));details={'path':str(file),'destination':str(dest)}
        else:raise ValueError('Неизвестная операция с файлом')
    reports.facts.cache_clear();event(user,'Root: '+operation,details);return {'ok':True,**details,**({'stamp':stamp(file)} if operation in ('write','upload') else {})}

def db_path(value):
    file=path(value)
    if not file.is_file():raise ValueError('База не найдена')
    with open(file,'rb') as source:
        if source.read(16)!=b'SQLite format 3\0':raise ValueError('Поддерживаются базы SQLite. Для других СУБД используйте терминал.')
    return file

def db_connect(file,write=False):
    c=sqlite3.connect(file.as_uri()+('?mode=rw' if write else '?mode=ro'),uri=True,timeout=5)
    if hasattr(c,'setlimit'):c.setlimit(sqlite3.SQLITE_LIMIT_LENGTH,4*1024*1024)
    c.execute('PRAGMA foreign_keys=ON');c.execute('PRAGMA trusted_schema=OFF')
    if not write:
        c.execute('PRAGMA query_only=ON')
        def readonly(action,arg1,arg2,database,trigger):
            if action in (sqlite3.SQLITE_ATTACH,sqlite3.SQLITE_DETACH):return sqlite3.SQLITE_DENY
            if action==sqlite3.SQLITE_PRAGMA:
                with_argument={'table_info','table_xinfo','index_info','index_xinfo','index_list','foreign_key_list','integrity_check','quick_check'}
                without_argument=with_argument|{'database_list','compile_options','page_count','freelist_count','schema_version','user_version','journal_mode','encoding'}
                if (arg1 or '').lower() not in without_argument or (arg2 is not None and (arg1 or '').lower() not in with_argument):return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        c.set_authorizer(readonly)
    deadline=time.monotonic()+15;c.set_progress_handler(lambda:int(time.monotonic()>deadline),10000)
    return c

def databases():
    rows=[]
    for folder,dirs,files in os.walk(s.STATE,followlinks=False):
        for name in files:
            if name.lower().endswith(('.sqlite','.sqlite3','.db')):
                file=Path(folder)/name
                try:rows.append({'path':str(file),'bytes':file.stat().st_size})
                except OSError:continue
                if len(rows)>=300:return rows
    return rows

def schema(value):
    file=db_path(value);c=db_connect(file)
    try:tables=[{'name':x[0],'type':x[1],'sql':x[2]} for x in c.execute("SELECT name,type,sql FROM sqlite_schema WHERE type IN ('table','view') ORDER BY name")]
    finally:c.close()
    return {'path':str(file),'tables':tables}

def json_value(value):return {'blob_bytes':len(value)} if isinstance(value,bytes) else value

def query(user,f):
    file=db_path(f.get('path',''));sql=f.get('sql','')
    if not isinstance(sql,str) or not sql.strip() or len(sql)>100000:raise ValueError('Введите SQL-запрос до 100000 символов')
    write=f.get('write') in ('1',True);c=db_connect(file,write);backup=None
    try:
        if write:
            backup=private_dir('root-backups')/('sql-'+s.uid()+'.sqlite');dest=sqlite3.connect(backup);deadline=time.monotonic()+30
            def progress(*args):
                if time.monotonic()>deadline:raise ValueError('Превышено время резервного копирования SQLite')
            try:c.backup(dest,pages=128,progress=progress)
            finally:dest.close()
            os.chmod(backup,0o600)
        cursor=c.execute(sql)
        columns=[x[0] for x in cursor.description] if cursor.description else []
        truncated=False;rows=[];size=0
        for index in range(201) if columns else []:
            row=cursor.fetchone()
            if row is None:break
            if index==200:truncated=True;break
            values=[json_value(v) for v in row];size+=len(json.dumps(values,ensure_ascii=False))
            if size>2*1024*1024:truncated=True;break
            rows.append(values)
        changes=c.total_changes
        if write:c.commit();reports.facts.cache_clear()
    except Exception:c.rollback();raise
    finally:c.close()
    event(user,'Root: SQL с изменением' if write else 'Root: SQL чтение',{'database':str(file),'sql_sha256':hashlib.sha256(sql.encode()).hexdigest(),'changes':changes,'backup':str(backup) if backup else None})
    return {'columns':columns,'rows':rows,'truncated':truncated,'changes':changes,'backup':str(backup) if backup else None}

def terminate(process):
    if process.poll() is not None:return
    if os.name=='posix':
        try:os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError:pass
    else:
        subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True,timeout=10)

def start_command(user,f):
    command=f.get('command','');cwd=path(f.get('cwd',str(s.ROOT)))
    if not isinstance(command,str) or not command.strip() or len(command)>16000:raise ValueError('Команда: от 1 до 16000 символов')
    if not cwd.is_dir():raise ValueError('Рабочий каталог не найден')
    timeout=int(f.get('timeout',60))
    if not 1<=timeout<=300:raise ValueError('Лимит команды: 1–300 секунд')
    with _lock:
        for key in list(_jobs):
            if _jobs[key]['state']!='running' and time.time()-_jobs[key]['created']>1800:del _jobs[key]
        if sum(x['state']=='running' for x in _jobs.values())>=2:raise ValueError('Уже выполняются две команды root')
        if len(_jobs)>=20:
            oldest=next((k for k,v in _jobs.items() if v['state']!='running'),None)
            if oldest:del _jobs[oldest]
        key=s.uid();job={'id':key,'state':'running','created':time.time(),'output':'','exit_code':None,'truncated':False,'cancel':False,'reason':''};_jobs[key]=job
    event(user,'Root: запуск команды',{'job':key,'cwd':str(cwd),'command_sha256':hashlib.sha256(command.encode()).hexdigest(),'timeout':timeout})
    def work():
        process=None;buffer=bytearray();retained=0
        try:
            args=['cmd.exe','/d','/s','/c',command] if os.name=='nt' else [shutil.which('bash') or '/bin/sh','-c',command]
            options={'creationflags':subprocess.CREATE_NEW_PROCESS_GROUP} if os.name=='nt' else {'start_new_session':True}
            process=subprocess.Popen(args,cwd=cwd,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,**options)
            def drain():
                nonlocal retained
                updated=0
                while chunk:=process.stdout.read1(8192):
                    with _lock:
                        keep=chunk[:max(0,MAX_OUTPUT-retained)];retained+=len(keep);buffer.extend(keep)
                        if keep and time.monotonic()-updated>.15:job['output']=buffer.decode('utf-8',errors='replace');updated=time.monotonic()
                        job['truncated']=job['truncated'] or len(keep)!=len(chunk)
                with _lock:job['output']=buffer.decode('utf-8',errors='replace')
            reader=threading.Thread(target=drain,daemon=True);reader.start();started=time.monotonic()
            while process.poll() is None:
                if job['cancel'] or time.monotonic()-started>timeout:
                    job['reason']='Отменено' if job['cancel'] else 'Превышено время';terminate(process);break
                time.sleep(.1)
            process.wait();reader.join(timeout=3);job['exit_code']=process.returncode
        except Exception as ex:job['reason']=type(ex).__name__
        finally:
            if process and process.poll() is None:terminate(process)
            job['state']='done';job['finished']=time.time()
            try:event(user,'Root: команда завершена',{'job':key,'exit_code':job['exit_code'],'reason':job['reason'],'output_bytes':retained})
            except sqlite3.Error:pass
    threading.Thread(target=work,daemon=True).start();return {'id':key}

def get_job(key,cancel=False):
    with _lock:
        job=_jobs.get(key)
        if not job:raise FileNotFoundError('Команда не найдена или её история истекла')
        if cancel:job['cancel']=True
        return dict(job)

def console_page(user,unlocked,enabled):
    if not enabled:return p.shell(user,'Консоль сервера','<div class="card"><p>Консоль выключена при запуске сервера.</p><pre>python3 app.py --host 0.0.0.0 --enable-root-console</pre></div>','root')
    if not unlocked:return p.shell(user,'Подтвердите доступ root','<div class="card"><p>Доступ к серверным файлам, SQLite и командам открывается на 15 минут после подтверждения пароля.</p>'+p.form(user,'/root/unlock','<label>Пароль root</label>'+p.input_('password',type='password',required=True)+p.button('Открыть консоль'))+'</div>','root')
    base='<div data-root-console data-csrf="'+p.E(user['csrf'])+'"><div class="notice">Команды и файловые операции выполняются с правами процесса веб-сервера. Учётная запись root приложения не повышает права ОС. Это не интерактивная SSH-сессия.</div>'+p.form(user,'/root/lock',p.button('Закрыть доступ к консоли'))+'<p data-root-message role="status"></p>'
    base+='<section class="card"><h2>Файлы сервера</h2><form data-root-path><label>Каталог</label>'+p.input_('path',str(s.ROOT))+p.button('Открыть')+'</form><div data-root-files></div><details><summary>Загрузка файла и новый каталог</summary><form data-root-upload><input type="file" name="file" required><label><input type="checkbox" name="overwrite" value="1">Разрешить замену существующего файла с резервной копией</label>'+p.button('Загрузить')+'</form><form data-root-mkdir><label>Имя нового каталога</label>'+p.input_('name')+p.button('Создать каталог')+'</form></details><details data-root-editor hidden><summary>Текстовый редактор</summary><form data-root-write><p data-root-editor-path></p>'+p.textarea('text','',16,'system-prompt')+p.button('Сохранить с резервной копией')+'</form></details><p class="muted">Удаление перемещает объект в state/root-trash. Исходный путь записывается рядом в JSON. Файлы и базы для копий — state/root-backups.</p></section>'
    base+='<section class="card"><h2>Базы SQLite</h2><button type="button" data-root-databases>Найти базы в хранилище</button><div data-root-databases></div><form data-root-schema><label>Путь к базе</label>'+p.input_('path',str(s.STATE/'app.sqlite'))+p.button('Показать таблицы')+'</form><div data-root-schema></div><form data-root-sql><label>SQL: один запрос</label>'+p.textarea('sql','SELECT name, type FROM sqlite_schema ORDER BY name',5,'system-prompt')+'<label><input type="checkbox" name="write" value="1">Разрешить изменение базы; перед запросом создаётся копия</label>'+p.button('Выполнить SQL')+'</form><div data-root-result></div><p class="muted">Результат ограничен 200 строками и 2 МБ. Для больших таблиц используйте LIMIT/OFFSET. Изменение служебных таблиц может нарушить работу приложения.</p></section>'
    base+='<section class="card"><h2>Командная строка сервера</h2><form data-root-command><label>Рабочий каталог</label>'+p.input_('cwd',str(s.ROOT))+'<label>Команда</label>'+p.textarea('command','',3,'system-prompt')+'<label>Лимит времени, секунды (1–300)</label>'+p.input_('timeout','60',type='number')+p.button('Выполнить команду')+'</form><button type="button" data-root-cancel disabled>Остановить команду</button><p data-root-command-status role="status"></p><pre class="root-output" data-root-output></pre><p class="muted">Вывод ограничен 1 МБ. Временная история команд хранится в памяти 30 минут; в аудите сохраняются хеш команды и результат без её текста и вывода.</p></section></div>'
    return p.shell(user,'Консоль сервера root',base,'root',extra='<script defer src="/assets/root.js"></script>')

def route(handler,post):
    user=handler.user;f=handler.form;q=handler.q;url=handler.path_only
    with s.db() as c:
        fresh=c.execute('SELECT * FROM users WHERE id=?',(user['id'],)).fetchone()
        if not fresh or not s.is_root(dict(fresh)):raise PermissionError('Требуется локально созданная учётная запись root')
    enabled=getattr(handler.server,'root_console_enabled',False)
    if url=='/root' and not post:
        unlocked=False
        if enabled:
            try:require(handler);unlocked=True
            except PermissionError:pass
        return handler.send(console_page(user,unlocked,enabled))
    if url in ('/root/unlock','/root/lock') and post:
        user=require(handler,False)
        if url=='/root/unlock':
            auth_check(handler.client_address[0],'unlock');success=accounts.verify(user,f.get('password',''));auth_result(handler.client_address[0],'unlock',success)
            if not success:raise PermissionError('Пароль root указан неверно')
        with s.db() as c:c.execute('UPDATE sessions SET root_until=? WHERE token=? AND user_id=?',(time.time()+900 if url.endswith('unlock') else 0,token(handler),user['id']))
        event(user,'Root: доступ открыт' if url.endswith('unlock') else 'Root: доступ закрыт',{})
        return handler.redirect('/root')
    user=require(handler)
    try:
        if url=='/api/root/files':return handler.json(mutate_file(user,f,f.get('_files',{})) if post else listing(q.get('path',str(s.ROOT)),q.get('offset',0)))
        if url=='/api/root/text' and not post:return handler.json(read_text(q['path']))
        if url=='/api/root/download' and not post:
            file=path(q['path'])
            if not stat.S_ISREG(file.stat().st_mode):raise ValueError('Скачиваются только обычные файлы')
            if file.stat().st_size>1024*1024*1024:raise ValueError('Скачивание: лимит 1 ГБ')
            handler.send_response(200);handler.send_header('Content-Type','application/octet-stream');handler.send_header('Content-Disposition','attachment; filename="server-file.bin"');handler.send_header('Content-Length',str(file.stat().st_size));handler.send_header('Cache-Control','no-store');handler.send_header('X-Content-Type-Options','nosniff');handler.end_headers()
            with open(file,'rb') as source:shutil.copyfileobj(source,handler.wfile,65536)
            return
        if url=='/api/root/databases' and not post:return handler.json({'databases':databases()})
        if url=='/api/root/schema' and not post:return handler.json(schema(q['path']))
        if url=='/api/root/sql' and post:return handler.json(query(user,f))
        if url=='/api/root/commands' and post:return handler.json(start_command(user,f),202)
        if url.startswith('/api/root/commands/'):
            key=url.rsplit('/',1)[1];return handler.json(get_job(key,cancel=post))
        raise FileNotFoundError('Страница не найдена')
    except sqlite3.Error as ex:raise ValueError('SQLite: '+str(ex)) from None
    except OSError as ex:raise ValueError('Операция ОС не выполнена: '+str(ex)) from None
