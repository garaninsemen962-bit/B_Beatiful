"""Fail-closed Docker runner. User Python is NEVER executed on the web host,
unless the operator explicitly starts the server with --local-runner (DD_LOCAL_RUNNER=1):
a demo mode for a single trusted machine without Docker."""
import json,os,shutil,subprocess,sys,threading,time
from pathlib import Path
from store import ROOT,STATE,db,audit
IMAGE=os.environ.get('DD_RUNNER_IMAGE','decision-diff-runner:2')
RESOLVED_IMAGE=IMAGE
MAX_WORKERS=2
TIMEOUT=int(os.environ.get('DD_TIMEOUT','600'))
LIMIT_BYTES=900*1024*1024
lock=threading.Lock()
LOCAL=os.environ.get('DD_LOCAL_RUNNER')=='1'
LOCAL_IMAGE='local-python-unsandboxed'

def enable_local():
    """Demo mode: run engine.py as a host subprocess. No container isolation."""
    global LOCAL,RESOLVED_IMAGE
    LOCAL=True;RESOLVED_IMAGE=LOCAL_IMAGE
def available():
    global RESOLVED_IMAGE
    if LOCAL:
        RESOLVED_IMAGE=LOCAL_IMAGE;return True,'Локальный режим без Docker: код процедур выполняется на этом компьютере'
    if not shutil.which('docker'):return False,'Docker не установлен. Установите Docker и соберите образ runner.'
    try:
        r=subprocess.run(['docker','image','inspect','--format','{{.Id}}',IMAGE],capture_output=True,timeout=8)
        if r.returncode:return False,'Образ runner недоступен. Выполните docker build -t decision-diff-runner:2 -f Dockerfile.runner .'
        RESOLVED_IMAGE=r.stdout.decode().strip()
        if not RESOLVED_IMAGE.startswith('sha256:'):return False,'Не удалось определить точный образ runner'
        r=subprocess.run(['docker','info'],capture_output=True,timeout=8)
        return (True,'Docker runner готов') if r.returncode==0 else (False,'Docker daemon недоступен')
    except (OSError,subprocess.TimeoutExpired):return False,'Docker не отвечает'

def command(eid,folder,dataset,image=None,source_mart=None):
    # No application state, user database, Docker socket, or host env are mounted.
    result=(folder/'result').resolve();inp=(folder/'input').resolve()
    def mount(source,dest,ro=False):
        if ',' in str(source):raise ValueError('Путь сервера не должен содержать запятую')
        return ['--mount',f'type=bind,source={source},target={dest}'+(',readonly' if ro else '')]
    return ['docker','run','--name','dd-'+eid,'--pull=never','--network=none','--read-only','--user=65534:65534',
        '--cap-drop=ALL','--security-opt=no-new-privileges','--pids-limit=64','--memory=1g','--memory-swap=1g','--cpus=2',
        '--ulimit','fsize=1073741824:1073741824','--ulimit','nofile=256:256','--tmpfs','/tmp:rw,noexec,nosuid,size=64m',
        '--log-driver=none',*(mount(dataset.resolve(),'/dataset',True) if dataset else mount(source_mart.resolve(),'/source/mart.sqlite',True)),*mount(result,'/output'),
        *mount(inp/'scripts','/output/scripts',True),*mount(inp/'config.json','/output/config.json',True),
        image or IMAGE,'python','-I','-u','/app/engine.py','/output']

def safe_file(folder,name,max_bytes=1024*1024):
    p=folder/name
    if p.is_symlink() or not p.is_file() or p.resolve().parent!=folder.resolve():raise ValueError('Недопустимый файл результата')
    flags=os.O_RDONLY|getattr(os,'O_NOFOLLOW',0);fd=os.open(p,flags)
    with os.fdopen(fd,'rb') as f:
        content=f.read(max_bytes+1)
    if len(content)>max_bytes:raise ValueError('Файл результата слишком велик')
    return content

def status_file(folder):
    try:return json.loads(safe_file(folder,'status.json',2*1024*1024))
    except (OSError,ValueError):return {}

def finish(eid,state,error=''):
    with db() as c:
        e=c.execute('SELECT * FROM executions WHERE id=?',(eid,)).fetchone()
        if not e or e['state'] not in ('queued','running'):return
        c.execute('UPDATE executions SET state=?,finished=?,error=? WHERE id=?',(state,time.time(),error,eid))
        user=dict(c.execute('SELECT * FROM users WHERE id=?',(e['actor'],)).fetchone())
        audit(c,user,e['run'],'Выполнение завершено',{'execution':eid,'state':state,'error':error})

def launch(eid,dataset=None):
    folder=STATE/'executions'/eid;result=folder/'result';result.mkdir(exist_ok=True);result.chmod(0o777)
    # Reserve output paths before bind mounting them read-only inside the output directory.
    (result/'scripts').mkdir(exist_ok=True);(result/'config.json').touch()
    with db() as c:manifest=json.loads(c.execute('SELECT manifest FROM executions WHERE id=?',(eid,)).fetchone()[0])
    checks_only=manifest.get('mode')=='checks'
    if manifest.get('image')==LOCAL_IMAGE:
        if not LOCAL:raise ValueError('Выполнение подготовлено для локального режима, а сервер запущен без --local-runner')
        # Same layout as inside the container, but with real host paths in config.json.
        shutil.rmtree(result/'scripts');shutil.copytree(folder/'input/scripts',result/'scripts')
        cfg=json.loads((folder/'input/config.json').read_text(encoding='utf-8'))
        if checks_only:cfg['source_mart']=str((folder/'input/mart.sqlite').resolve())
        else:cfg['dataset']=str(Path(dataset).resolve())
        (result/'config.json').write_text(json.dumps(cfg,ensure_ascii=False),encoding='utf-8')
        cmd=[sys.executable,'-I','-X','utf8','-u',str(ROOT/'engine.py'),str(result.resolve())]
    else:
        if LOCAL:raise ValueError('Сервер запущен в локальном режиме; Docker-выполнение недоступно')
        cmd=command(eid,folder,dataset,manifest['image'],folder/'input/mart.sqlite' if checks_only else None)
    def work():
        error='';state='failed';p=None
        try:
            with db() as c:c.execute("UPDATE executions SET state='running' WHERE id=?",(eid,))
            p=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
            def drain():
                # Drain continuously; cap retained console output independently of worker.
                with open(folder/'console.log','wb') as f:
                    n=0
                    while chunk:=p.stdout.read(8192):
                        keep=chunk[:max(0,1024*1024-n)]
                        if keep:f.write(keep);f.flush();n+=len(keep)
            reader=threading.Thread(target=drain,daemon=True);reader.start();started=time.monotonic()
            while p.poll() is None:
                if time.monotonic()-started>TIMEOUT:raise TimeoutError('Превышено время выполнения')
                size=0;count=0
                for parent,dirs,files in os.walk(result,followlinks=False):
                    for name in files:
                        file=Path(parent)/name
                        if file.is_symlink():continue
                        size+=file.lstat().st_size;count+=1
                        if count>50000 or size>LIMIT_BYTES:raise ValueError('Превышен лимит файлов результатов')
                if size>LIMIT_BYTES:raise ValueError('Превышен лимит результатов')
                time.sleep(.25)
            reader.join(timeout=2)
            status=status_file(result)
            if p.returncode or status.get('state')!='done':
                error=str(status.get('error') or f'Контейнер завершился с кодом {p.returncode}. См. вывод Python.')[:10000]
            else:
                # Promote only known regular files after the container has stopped.
                # API never opens SQLite or JSON directly in the container-writable directory.
                final=folder/'artifacts';final.mkdir()
                for name in ('mart.sqlite','summary.json','checks.json','status.json'):
                    source=(folder/'input/mart.sqlite') if checks_only and name=='mart.sqlite' else result/name
                    expected=(folder/'input').resolve() if checks_only and name=='mart.sqlite' else result.resolve()
                    if source.is_symlink() or not source.is_file() or source.resolve().parent!=expected:raise ValueError('Недопустимый артефакт: '+name)
                    if source.stat().st_size>LIMIT_BYTES:raise ValueError('Слишком большой артефакт')
                    with open(source,'rb') as src,open(final/name,'wb') as dest:shutil.copyfileobj(src,dest)
                for name in ('input_errors.json','parse_errors.log'):
                    source=result/name
                    if source.exists():
                        content=safe_file(result,name,128*1024*1024);(final/name).write_bytes(content)
                state='done'
        except Exception as ex:error=str(ex)
        finally:
            if not LOCAL:
                try:subprocess.run(['docker','rm','-f','dd-'+eid],capture_output=True,timeout=15)
                except (OSError,subprocess.TimeoutExpired):pass
            if p:
                try:p.wait(timeout=5)
                except subprocess.TimeoutExpired:p.kill();p.wait()
            if state=='done':
                shutil.rmtree(result,ignore_errors=True)
                if checks_only:(folder/'input/mart.sqlite').unlink(missing_ok=True)
            finish(eid,state,error)
    threading.Thread(target=work,daemon=True).start()

def recover():
    # A stopped coordinator does not silently accept orphaned execution output.
    with db() as c:ids=[r[0] for r in c.execute("SELECT id FROM executions WHERE state IN ('queued','running')")]
    for eid in ids:
        if not LOCAL:
            try:subprocess.run(['docker','rm','-f','dd-'+eid],capture_output=True,timeout=10)
            except (OSError,subprocess.TimeoutExpired):pass
        finish(eid,'failed','Сервер был перезапущен. Создайте новое выполнение.')
