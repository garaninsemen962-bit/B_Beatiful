"""Persistent resources, immutable revisions, ACL and append-only audit."""
import ast, hashlib, hmac, json, os, re, secrets, sqlite3, time
from contextlib import contextmanager
from pathlib import Path
ROOT=Path(__file__).resolve().parent
STATE=Path(os.environ.get('DD_STATE',ROOT/'state')).resolve()
KINDS={'dataset','script','config','run'}
@contextmanager
def db():
    c=sqlite3.connect(STATE/'app.sqlite',timeout=20);c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON')
    try:yield c;c.commit()
    except Exception:c.rollback();raise
    finally:c.close()
def init():
    for p in ('objects','executions'): (STATE/p).mkdir(parents=True,exist_ok=True)
    with db() as c:
        c.executescript('''PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,login TEXT UNIQUE,display TEXT,salt TEXT,password TEXT,status TEXT,admin INTEGER DEFAULT 0,created REAL);
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,user_id TEXT REFERENCES users(id),csrf TEXT,expires REAL);
        CREATE TABLE IF NOT EXISTS resources(id TEXT PRIMARY KEY,kind TEXT,owner TEXT REFERENCES users(id),name TEXT,description TEXT,visibility TEXT DEFAULT 'private',public_role TEXT DEFAULT 'reader',public_download INTEGER DEFAULT 0,revision INTEGER,body TEXT,created REAL);
        CREATE TABLE IF NOT EXISTS versions(resource TEXT REFERENCES resources(id),revision INTEGER,name TEXT,description TEXT,body TEXT,author TEXT,created REAL,PRIMARY KEY(resource,revision));
        CREATE TABLE IF NOT EXISTS grants(resource TEXT REFERENCES resources(id),user_id TEXT REFERENCES users(id),role TEXT,download INTEGER DEFAULT 0,PRIMARY KEY(resource,user_id));
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT,resource TEXT,actor TEXT,display TEXT,action TEXT,details TEXT,created REAL);
        CREATE TABLE IF NOT EXISTS executions(id TEXT PRIMARY KEY,run TEXT REFERENCES resources(id),actor TEXT,state TEXT,manifest TEXT,created REAL,finished REAL,error TEXT);
        CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT,'Audit is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT,'Audit is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS version_no_update BEFORE UPDATE ON versions BEGIN SELECT RAISE(ABORT,'Versions are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS version_no_delete BEFORE DELETE ON versions BEGIN SELECT RAISE(ABORT,'Versions are immutable'); END;
        ''')
def uid():return secrets.token_hex(12)
def audit(c,user,resource,action,details=None):
    c.execute('INSERT INTO audit(resource,actor,display,action,details,created) VALUES (?,?,?,?,?,?)',(resource,user['id'],user['display'],action,json.dumps(details or {},ensure_ascii=False),time.time()))
def register(login,display,password,admin=False):
    if not re.fullmatch('[A-Za-z0-9_-]{2,32}',login):raise ValueError('Логин: 2–32 латинских символа, цифры, _ или -')
    if len(password)<8:raise ValueError('Пароль: минимум 8 символов')
    if not display.strip() or len(display)>100:raise ValueError('Укажите имя (до 100 символов)')
    salt=secrets.token_hex(16);password_hash=hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),250000).hex()
    user=dict(id=uid(),login=login,display=display.strip(),salt=salt,password=password_hash,status='active' if admin else 'pending',admin=int(admin),created=time.time())
    with db() as c:
        c.execute('INSERT INTO users VALUES (:id,:login,:display,:salt,:password,:status,:admin,:created)',user)
        audit(c,user,None,'Регистрация администратора' if admin else 'Заявка на регистрацию',{})
    return user
def reset_password(login,password):
    """Local server operator recovery; does not alter roles, approval or resources."""
    if len(password)<8:raise ValueError('Пароль: минимум 8 символов')
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT * FROM users WHERE login=?',(login,)).fetchone()
        if not row:raise ValueError('Пользователь с таким логином не найден')
        user=dict(row);salt=secrets.token_hex(16)
        hashed=hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),250000).hex()
        c.execute('UPDATE users SET salt=?,password=? WHERE id=?',(salt,hashed,user['id']))
        c.execute('DELETE FROM sessions WHERE user_id=?',(user['id'],))
        audit(c,user,None,'Сброс пароля через консоль сервера',{'login':login,'source':'local_console','sessions_revoked':True})

def authenticate(login,password):
    with db() as c:r=c.execute('SELECT * FROM users WHERE login=?',(login,)).fetchone()
    if not r or not hmac.compare_digest(r['password'],hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(r['salt']),250000).hex()):raise PermissionError('Неверный логин или пароль')
    return dict(r)
def session(user):
    token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(24)
    with db() as c:c.execute('INSERT INTO sessions VALUES (?,?,?,?)',(token,user['id'],csrf,time.time()+86400))
    return token

def resource(c,rid):
    r=c.execute('SELECT * FROM resources WHERE id=?',(rid,)).fetchone()
    if not r:raise FileNotFoundError('Ресурс не найден')
    return dict(r)
def role(c,user,r):
    if not user or user['status']!='active':return None
    if user['admin'] or r['owner']==user['id']:return 'owner'
    g=c.execute('SELECT role FROM grants WHERE resource=? AND user_id=?',(r['id'],user['id'])).fetchone()
    roles=[]
    if r['visibility']=='public':roles.append(r['public_role'])
    if r['visibility'] in ('named','public') and g:roles.append(g[0])
    return 'editor' if 'editor' in roles else 'reader' if roles else None
def require(c,user,r,minimum='reader'):
    v=role(c,user,r)
    if {'reader':1,'editor':2,'owner':3}.get(v,0)<{'reader':1,'editor':2,'owner':3}[minimum]:raise PermissionError('Недостаточно прав на ресурс')
    return v
def can_download(c,user,r):
    v=role(c,user,r)
    if v in ('owner','editor'):return True
    if not v:return False
    g=c.execute('SELECT download FROM grants WHERE resource=? AND user_id=?',(r['id'],user['id'])).fetchone()
    return bool((r['visibility']=='public' and r['public_download']) or (r['visibility'] in ('named','public') and g and g[0]))
def visible(user,kind=None):
    with db() as c:
        rows=c.execute('SELECT * FROM resources'+(' WHERE kind=?' if kind else '')+' ORDER BY created DESC',(kind,) if kind else ()).fetchall()
        result=[]
        for row in rows:
            r=dict(row);v=role(c,user,r)
            if v:r['role']=v;result.append(r)
        return result

def create(c,user,kind,name,description,body,visibility='private'):
    if kind not in KINDS:raise ValueError('Неизвестный тип ресурса')
    if not name.strip() or len(name)>200:raise ValueError('Название: 1–200 символов')
    rid=uid();now=time.time();payload=json.dumps(body,ensure_ascii=False)
    c.execute('INSERT INTO resources(id,kind,owner,name,description,visibility,revision,body,created) VALUES (?,?,?,?,?,?,1,?,?)',(rid,kind,user['id'],name.strip(),description,visibility,payload,now))
    c.execute('INSERT INTO versions VALUES (?,?,?,?,?,?,?)',(rid,1,name.strip(),description,payload,user['id'],now));audit(c,user,rid,'Создание',{'kind':kind,'name':name,'revision':1})
    return rid

def revise(c,user,r,expected,name,description,body):
    require(c,user,r,'editor')
    if r['revision']!=int(expected):raise ValueError('Ресурс уже изменён другим пользователем. Обновите страницу и сравните версии.')
    if not name.strip() or len(name)>200:raise ValueError('Название: 1–200 символов')
    rev=r['revision']+1;payload=json.dumps(body,ensure_ascii=False)
    c.execute('UPDATE resources SET name=?,description=?,body=?,revision=? WHERE id=?',(name.strip(),description,payload,rev,r['id']))
    c.execute('INSERT INTO versions VALUES (?,?,?,?,?,?,?)',(r['id'],rev,name.strip(),description,payload,user['id'],time.time()))
    audit(c,user,r['id'],'Изменение',{'from_revision':r['revision'],'to_revision':rev,'before':{'name':r['name'],'description':r['description'],'body':json.loads(r['body'])},'after':{'name':name,'description':description,'body':body}})

def script_info(code,kind,validate=True):
    if kind not in ('normalizer','comparator','aggregator','check'):raise ValueError('Неизвестное назначение скрипта')
    tree=ast.parse(code);doc=ast.get_docstring(tree) or '';lines=doc.strip().splitlines()
    funcs={n.name for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
    required={'normalizer':{'normalize','segments'},'comparator':{'compare'},'aggregator':{'aggregate'},'check':{'run'}}[kind]
    if validate and not required<=funcs:
        missing=', '.join(sorted(required-funcs))
        raise ValueError('Для выбранного назначения не хватает функций: '+missing+'. Выберите подходящее назначение или используйте шаблон под редактором.')
    return (lines[0][:200] if lines else ''),'\n'.join(lines[1:]).strip()
