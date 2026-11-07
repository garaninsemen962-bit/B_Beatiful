"""Read promoted execution artifacts, never container-written mutable files."""
import json,sqlite3,time
from contextlib import contextmanager
from functools import lru_cache
from engine import Mart
from store import STATE
@contextmanager
def connect(eid):
    p=STATE/'executions'/eid/'artifacts/mart.sqlite'
    c=sqlite3.connect(p.as_uri()+'?mode=ro&immutable=1',uri=True);deadline=time.monotonic()+8;c.set_progress_handler(lambda:int(time.monotonic()>deadline),10000);c.execute('PRAGMA query_only=ON');c.execute('PRAGMA trusted_schema=OFF')
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
    if not isinstance(filters,dict) or len(filters)>12 or any(not isinstance(v,(str,int)) for v in filters.values()):raise ValueError('Неверные фильтры')
    if q.get('product') or q.get('decision'):
        with connect(eid) as c:allowed={x['APP_ID'] for x in Mart(c).segments() if (not q.get('product') or q['product'] in x['PRODUCTS'].split(';')) and (not q.get('decision') or str(x['DECISION_CHANGED'])==q['decision'])}
    for f in facts(eid):
        if q.get('diff','1')!='all' and str(f['DIFF_FLAG'])!=q.get('diff','1'):continue
        if q.get('path') and q['path'].lower() not in f['PATH'].lower():continue
        if not all(str(f.get(k,''))==str(v) for k,v in filters.items()):continue
        apps=set(f['APP_IDS'])
        if allowed is not None:apps &= allowed
        if apps:yield f,apps

CAUSE_ORDER=('Порог','Балл / модель','Продукты / офферы','Прочее')
@lru_cache(maxsize=4)
def decision_causes(eid):
    """Why did the decision change? Attributes each decision-changed application to the
    strategy levers that moved in its response. One application may have several causes;
    rows are unique combinations, so their sum equals the number of changed decisions."""
    with connect(eid) as c:
        m=Mart(c);changed={}
        for s in m.segments():
            if s.get('DECISION_CHANGED'):changed[s['APP_ID']]=(s.get('DECISION_BEFORE','')+' → '+s.get('DECISION_AFTER',''),s.get('PD_MODEL_BEFORE')!=s.get('PD_MODEL_AFTER'))
        causes={a:set(['Балл / модель'] if model else []) for a,(_,model) in changed.items()}
        for app,path,before,after,akey in c.execute("SELECT APP_ID,PATH,VALUE_BEFORE,VALUE_AFTER,ARRAY_KEY FROM comparison WHERE DIFF_FLAG=1"):
            if app not in causes:continue
            if path.endswith('.rules.hit'):
                rule=(akey or '').split('|')[-1]
                if before=='true' and after!='true':causes[app].add('Стоп-фактор снят: '+rule)
                elif after=='true' and before!='true':causes[app].add('Новый стоп-фактор: '+rule)
            elif '.cutOff.value' in path or path.endswith('.cutOff.name'):causes[app].add('Порог')
            elif '.scoring.' in path or '.scoreModels' in path or path.endswith('.pd.value'):causes[app].add('Балл / модель')
            elif 'available_offers' in path or path.endswith('product_trials.product_id'):causes[app].add('Продукты / офферы')
    groups={}
    for app,found in causes.items():
        stops=sorted(x for x in found if x.startswith(('Стоп-фактор снят','Новый стоп-фактор')))
        main=stops+[x for x in CAUSE_ORDER if x in found and x!='Продукты / офферы'] or ([x for x in CAUSE_ORDER if x in found] or ['Прочее'])
        key=' + '.join(main);g=groups.setdefault(key,{'apps':[],'transitions':{}})
        g['apps'].append(app);t=changed[app][0];g['transitions'][t]=g['transitions'].get(t,0)+1
    return sorted(([k,sorted(v['apps']),v['transitions']] for k,v in groups.items()),key=lambda x:-len(x[1]))

@lru_cache(maxsize=8)
def product_names(eid):
    """product_id -> product_name from the segments of this execution."""
    names={}
    with connect(eid) as c:
        for s in Mart(c).segments():
            for side in ('BEFORE','AFTER'):
                for pid,d in ((s.get('PRODUCT_DETAILS') or {}).get(side) or {}).items():
                    if isinstance(d,dict) and d.get('product_name'):names[str(pid)]=d['product_name']
    return names
