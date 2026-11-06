"""Read promoted execution artifacts, never container-written mutable files."""
import json,sqlite3,time
from contextlib import contextmanager
from functools import lru_cache
from engine import Mart
from store import STATE
@contextmanager
def connect(eid):
    p=STATE/'executions'/eid/'artifacts/mart.sqlite'
    c=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True);deadline=time.monotonic()+8;c.set_progress_handler(lambda:int(time.monotonic()>deadline),10000);c.execute('PRAGMA query_only=ON');c.execute('PRAGMA trusted_schema=OFF')
    try:yield c
    finally:c.close()
def artifact(eid,name):
    p=STATE/'executions'/eid/'artifacts'/name
    if p.stat().st_size>30*1024*1024:raise ValueError('Отчёт слишком велик')
    return json.loads(p.read_text(encoding='utf-8'))
@lru_cache(maxsize=2)
def facts(eid):
    with connect(eid) as c:
        rows=[];size=0;links=0
        for x in c.execute('SELECT data FROM facts'):
            size+=len(x[0]);f=json.loads(x[0]);links+=len(f.get('APP_IDS',[]))
            if len(rows)>=100000 or size>100*1024*1024 or links>2000000:raise ValueError('Превышен лимит сводного отчёта')
            if f.get('SOURCE') not in ('BEFORE','AFTER') or not isinstance(f.get('APP_IDS'),list):raise ValueError('Неверный формат facts')
            rows.append(f)
        return rows
def selected(eid,q):
    filters=json.loads(q.get('filter','{}'));allowed=None
    if q.get('product') or q.get('decision'):
        with connect(eid) as c:allowed={x['APP_ID'] for x in Mart(c).segments() if (not q.get('product') or q['product'] in x['PRODUCTS'].split(';')) and (not q.get('decision') or str(x['DECISION_CHANGED'])==q['decision'])}
    for f in facts(eid):
        if q.get('diff','1')!='all' and str(f['DIFF_FLAG'])!=q.get('diff','1'):continue
        if q.get('path') and q['path'].lower() not in f['PATH'].lower():continue
        if not all(str(f.get(k,''))==str(v) for k,v in filters.items()):continue
        apps=set(f['APP_IDS'])
        if allowed is not None:apps &= allowed
        if apps:yield f,apps
