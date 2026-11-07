"""SQLite-backed MartAPI and background worker. Python 3.10+, standard library only."""
import argparse, importlib.util, json, os, sqlite3, time, traceback, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
COLUMNS = ['APP_ID','PREFIX','CLASS','SUFFIX','ARRAY_KEY','KEY','PATH','VALUE_BEFORE','VALUE_AFTER','ADJUSTED_BEFORE','ADJUSTED_AFTER','DIFF_FLAG','CHANGE_TYPE']
FILTERS = dict(path='PATH', key='KEY', array_key='ARRAY_KEY', diff_flag='DIFF_FLAG', change_type='CHANGE_TYPE', value_before='VALUE_BEFORE', value_after='VALUE_AFTER', app_ids='APP_ID')

def load(path):
    spec = importlib.util.spec_from_file_location('user_'+path.stem, path)
    mod = importlib.util.module_from_spec(spec); sys.modules[spec.name] = mod; spec.loader.exec_module(mod); return mod

def dump(path, value):
    temp = path.with_suffix('.tmp'); temp.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8'); temp.replace(path)

class Mart:
    def __init__(self, db):
        self.db = db; self.db.row_factory = sqlite3.Row
    def where(self, filters):
        conditions, args = [], []
        for k,v in filters.items():
            if k == 'path_prefix':
                conditions.append('substr(PATH,1,?)=?'); args.extend([len(v), v]); continue
            if k not in FILTERS: raise ValueError('Unknown filter: '+k)
            col = FILTERS[k]
            if isinstance(v, (list,tuple,set)):
                if not v: conditions.append('0'); continue
                conditions.append(col+' IN ('+','.join('?' for _ in v)+')'); args.extend(v)
            else: conditions.append(col+'=?'); args.append(v)
        return (' WHERE '+' AND '.join(conditions) if conditions else ''),args
    def rows(self, **filters):
        w,a = self.where(filters)
        return (dict(r) for r in self.db.execute('SELECT * FROM comparison'+w,a))
    def count(self, **filters):
        w,a = self.where(filters); return self.db.execute('SELECT count(*) FROM comparison'+w,a).fetchone()[0]
    def app_ids(self, **filters):
        w,a = self.where(filters); return [r[0] for r in self.db.execute('SELECT DISTINCT APP_ID FROM comparison'+w+' ORDER BY APP_ID',a)]
    def segments(self): return (json.loads(r[0]) for r in self.db.execute('SELECT data FROM segments ORDER BY APP_ID'))
    def total_apps(self): return self.db.execute('SELECT count(*) FROM segments').fetchone()[0]

def checks(db, folder, config, log):
    mart = Mart(db); results=[]; errors=[]
    for name in config['checks']:
        started=time.monotonic()
        try:
            mod=load(folder/'scripts'/name); check=mod.CHECK
            rows=check['entrypoint'](mart, {**check.get('params',{}), **config.get('check_params',{}).get(name,{})})
            for row in rows:
                required={'LABEL','SOURCE','ARGUMENT','CNT','TOTAL','METRIC','APP_IDS','RESULT','CONFIDENCE'}
                if not required <= row.keys(): raise ValueError('Incomplete check row')
                if row['RESULT'] not in ('PASS','FAIL') or row['SOURCE'] not in ('BEFORE','AFTER'): raise ValueError('Invalid check result')
                row['CHECK_FILE']=name
                results.append(row)
            log('Проверка: '+name, round(time.monotonic()-started,3),script=name)
        except Exception:
            errors.append({'file':name,'error':traceback.format_exc()}); log('Ошибка проверки: '+name,0,script=name)
    dump(folder/'checks.json', {'rows':results, 'errors':errors})
    if errors: raise RuntimeError('Некоторые проверки завершились с ошибкой. См. checks.json и журнал.')

def process(folder, checks_only=False):
    config=json.loads((folder/'config.json').read_text(encoding='utf-8'))
    status={'state':'running','stage':'Инициализация','progress':0,'logs':[], 'started':time.time()}
    def log(stage, seconds=None, progress=None, script=None):
        status['stage']=stage
        if progress is not None: status['progress']=progress
        status['logs'].append(dict(stage=stage,seconds=seconds,time=time.time(),script=script))
        dump(folder/'status.json',status)
    try:
        checks_only = checks_only or config.get('mode') == 'checks'
        db=sqlite3.connect(Path(config.get('source_mart',folder/'mart.sqlite')).as_uri()+'?mode=ro&immutable=1',uri=True) if checks_only else sqlite3.connect(folder/'mart.sqlite')
        if checks_only:
            db.execute('PRAGMA query_only=ON');log('Готовая витрина подключена: повторная нормализация не требуется',progress=90)
        if not checks_only:
            db.executescript('''PRAGMA journal_mode=WAL;
            CREATE TABLE normalized(APP_ID TEXT,SOURCE TEXT,PATH TEXT,ARRAY_KEY TEXT,data TEXT, PRIMARY KEY(APP_ID,SOURCE,PATH,ARRAY_KEY));
            CREATE TABLE segments(APP_ID TEXT PRIMARY KEY,data TEXT);
            CREATE TABLE comparison('''+','.join(c+(' INTEGER' if c=='DIFF_FLAG' else ' TEXT') for c in COLUMNS)+''',PRIMARY KEY(APP_ID,PATH,ARRAY_KEY));
            CREATE TABLE facts(data TEXT);
            ''')
            norm=load(folder/'scripts'/config['normalizer']); comp=load(folder/'scripts'/config['comparator']); agg=load(folder/'scripts'/config['aggregator'])
            data=Path(config['dataset'])/'RESULTS'; b=data/'BEFORE_MASTER'; a=data/'AFTER_BETA'
            names=sorted({p.name for p in b.glob('*.json')}|{p.name for p in a.glob('*.json')})
            if not names: raise ValueError('Датасет пуст: ожидается RESULTS/BEFORE_MASTER и RESULTS/AFTER_BETA')
            started=time.monotonic(); normalize_seconds=0; compare_seconds=0; log('Нормализация и сравнение',progress=1,script=config['normalizer'])
            for i,name in enumerate(names):
                app=Path(name).stem
                before=json.loads((b/name).read_text(encoding='utf-8')) if (b/name).exists() else {}
                after=json.loads((a/name).read_text(encoding='utf-8')) if (a/name).exists() else {}
                tick=time.monotonic()
                union=list(norm.normalize(before,app,'BEFORE',config.get('normalize_params',{})))+list(norm.normalize(after,app,'AFTER',config.get('normalize_params',{})))
                db.executemany('INSERT INTO normalized VALUES (?,?,?,?,?)',[(r['APP_ID'],r['SOURCE'],r['PATH'],r['ARRAY_KEY'],json.dumps(r,ensure_ascii=False)) for r in union])
                normalize_seconds+=time.monotonic()-tick;tick=time.monotonic()
                rows=list(comp.compare(union,config.get('compare_params',{})))
                db.executemany('INSERT INTO comparison VALUES ('+','.join('?' for _ in COLUMNS)+')',[[r[c] for c in COLUMNS] for r in rows])
                compare_seconds+=time.monotonic()-tick
                segment=norm.segments(before,after,app,config.get('normalize_params',{}))
                db.execute('INSERT INTO segments VALUES (?,?)',(app,json.dumps(segment,ensure_ascii=False)))
                if i%100==0:
                    db.commit(); status['progress']=int(75*(i+1)/len(names));status['stage']=f'Обработано {i+1} / {len(names)}';dump(folder/'status.json',status)
            db.commit();log('Приведение и сравнение завершено',round(time.monotonic()-started,3),75,config['comparator'])
            log('Нормализация: обработка и запись строк',round(normalize_seconds,3),script=config['normalizer'])
            log('Сравнение: обработка и запись строк',round(compare_seconds,3),script=config['comparator'])
            db.executescript('CREATE INDEX cmp_path_diff ON comparison(PATH,DIFF_FLAG); CREATE INDEX cmp_type ON comparison(CHANGE_TYPE); CREATE INDEX cmp_key ON comparison(KEY); CREATE INDEX cmp_diff ON comparison(DIFF_FLAG,APP_ID);')
            log('Агрегация фактов counts',progress=80,script=config['aggregator']); started=time.monotonic()
            mart=Mart(db)
            db.executemany('INSERT INTO facts VALUES (?)',((json.dumps(f,ensure_ascii=False),) for f in agg.aggregate(mart.rows(),list(mart.segments()),config.get('aggregate_params',{}))))
            db.commit();log('Агрегаты готовы',round(time.monotonic()-started,3),90,config['aggregator'])
        log('Автоматические проверки',progress=95)
        checks(db,folder,config,log)
        mart=Mart(db); segs=list(mart.segments()); changed=mart.app_ids(diff_flag=1)
        summary=dict(total_apps=mart.total_apps(), changed_apps=len(changed),decision_changed=sum(s['DECISION_CHANGED'] for s in segs),
            rows=mart.count(), normalized_rows=db.execute('SELECT count(*) FROM normalized').fetchone()[0], facts=db.execute('SELECT count(*) FROM facts').fetchone()[0],
            transitions={}, change_types={})
        for s in segs:
            k=s['DECISION_BEFORE']+' → '+s['DECISION_AFTER'];summary['transitions'][k]=summary['transitions'].get(k,0)+1
        for r in db.execute('SELECT CHANGE_TYPE,count(*),count(distinct APP_ID) FROM comparison GROUP BY CHANGE_TYPE'):
            summary['change_types'][r[0]]={'attributes':r[1],'apps':r[2]}
        dump(folder/'summary.json',summary)
        status.update(state='done',progress=100,elapsed=round(time.time()-status['started'],3));log('Готово')
        db.close()
    except Exception:
        status.update(state='failed',error=traceback.format_exc());log('Ошибка выполнения')
        raise

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('folder',type=Path);parser.add_argument('--checks-only',action='store_true');args=parser.parse_args()
    process(args.folder,args.checks_only)
