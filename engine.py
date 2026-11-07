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

def strict_document(raw):
    def constant(value):raise ValueError('Нестандартное число JSON: '+value)
    def object_pairs(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise ValueError('Повтор ключа JSON: '+key[:80])
            result[key]=value
        return result
    doc=json.loads(raw.decode('utf-8-sig'),parse_constant=constant,object_pairs_hook=object_pairs)
    if not isinstance(doc,dict):raise TypeError('Верхний уровень ответа должен быть JSON-объектом')
    return doc

def read_document(file):
    if not file.exists():return None,'MISSING','Файл отсутствует'
    try:return strict_document(file.read_bytes()),'OK',''
    except OSError as ex:return None,'READ_ERROR',type(ex).__name__+': не удалось прочитать файл'
    except UnicodeError:return None,'INVALID_ENCODING','Ответ не является UTF-8'
    except TypeError as ex:return None,'INVALID_DOCUMENT',str(ex)[:300]
    except (ValueError,RecursionError) as ex:return None,'INVALID_JSON',str(ex)[:300]

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
    def has_input_quality(self):return self.db.execute("SELECT count(*) FROM sqlite_schema WHERE type='table' AND name IN ('input_status','pair_errors')").fetchone()[0]==2
    def input_total_apps(self):return self.db.execute('SELECT count(DISTINCT APP_ID) FROM input_status').fetchone()[0] if self.has_input_quality() else None
    def input_status(self,app_ids=None):
        if not self.has_input_quality():return []
        args=list(app_ids) if app_ids is not None else []
        suffix=(' WHERE APP_ID IN ('+','.join('?' for _ in args)+')') if args else (' WHERE 0' if app_ids is not None else '')
        return [dict(r) for r in self.db.execute('SELECT * FROM input_status'+suffix+' ORDER BY APP_ID,SOURCE',args)]
    def input_errors(self):return [x for x in self.input_status() if x['STATUS']!='OK']
    def pair_errors(self,app_ids=None):
        if not self.has_input_quality():return []
        args=list(app_ids) if app_ids is not None else []
        suffix=(' WHERE APP_ID IN ('+','.join('?' for _ in args)+')') if args else (' WHERE 0' if app_ids is not None else '')
        return [dict(r) for r in self.db.execute('SELECT * FROM pair_errors'+suffix+' ORDER BY APP_ID',args)]
    def excluded_app_ids(self):
        if not self.has_input_quality():return []
        return [r[0] for r in self.db.execute('SELECT DISTINCT i.APP_ID FROM input_status i LEFT JOIN segments s ON s.APP_ID=i.APP_ID WHERE s.APP_ID IS NULL ORDER BY i.APP_ID')]
    def input_quality_checks(self,limit=20):
        total=self.input_total_apps()
        if total is None:return []
        errors=self.input_errors();rows=[]
        def row(label,source,argument,apps):return dict(LABEL=label,SOURCE=source,ARGUMENT=argument,CNT=len(apps),TOTAL=total,METRIC=round(len(apps)/total,6) if total else 0,APP_IDS=sorted(apps)[:limit],RESULT='FAIL' if apps else 'PASS',CONFIDENCE='HIGH')
        for source in ('BEFORE','AFTER'):
            apps={x['APP_ID'] for x in errors if x['SOURCE']==source}
            rows.append(row('Ошибки исполнения '+source,source,'Нет ответа, повреждён JSON или не пройдена валидация; знаменатель — все входные заявки',apps))
        pair={x['APP_ID'] for x in self.pair_errors()}
        if pair:rows.append(row('Ошибки подготовки пар','AFTER','Общая ошибка разрезов/сравнения пары, без отнесения к конкретному срезу',pair))
        if not self.total_apps():
            item=row('Нет сопоставимых пар','AFTER','Бизнес-проверки не подтверждают отсутствие регрессий при пустой выборке',set(self.excluded_app_ids()));item['RESULT']='FAIL';item['CONFIDENCE']='LOW';rows.append(item)
        return rows


def checks(db, folder, config, log):
    mart = Mart(db); results=[{**row,'CHECK_FILE':'__input_quality__'} for row in mart.input_quality_checks()]; errors=[]
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
            CREATE TABLE input_status(APP_ID TEXT,SOURCE TEXT,STATUS TEXT,ERROR TEXT,PRIMARY KEY(APP_ID,SOURCE));
            CREATE TABLE pair_errors(APP_ID TEXT PRIMARY KEY,STAGE TEXT,ERROR TEXT);
            ''')
            norm=load(folder/'scripts'/config['normalizer']); comp=load(folder/'scripts'/config['comparator']); agg=load(folder/'scripts'/config['aggregator'])
            data=Path(config['dataset'])/'RESULTS'; b=data/'BEFORE_MASTER'; a=data/'AFTER_BETA'
            names=sorted({p.name for p in b.glob('*.json')}|{p.name for p in a.glob('*.json')})
            if not names: raise ValueError('Датасет пуст: ожидается RESULTS/BEFORE_MASTER и RESULTS/AFTER_BETA')
            started=time.monotonic(); normalize_seconds=0; compare_seconds=0; log('Нормализация и сравнение',progress=1,script=config['normalizer'])
            data_errors=(ValueError,TypeError,KeyError,IndexError,RecursionError)
            for i,name in enumerate(names):
                app=Path(name).stem
                if i%100==0:
                    db.commit();status['progress']=int(75*(i+1)/len(names));status['stage']=f'Проверено {i+1} / {len(names)}';dump(folder/'status.json',status)
                docs={};valid=True
                # Проверяем оба среза независимо: повреждение первого не скрывает ошибку второго.
                for source,file in [('BEFORE',b/name),('AFTER',a/name)]:
                    doc,state,error=read_document(file);docs[source]=doc
                    db.execute('INSERT INTO input_status VALUES (?,?,?,?)',(app,source,state,error))
                    if state!='OK':valid=False
                if not valid:continue
                normalized={};tick=time.monotonic()
                for source in ('BEFORE','AFTER'):
                    try:
                        if hasattr(norm,'validate') and norm.validate(docs[source],app,source,config.get('normalize_params',{})) is False:raise ValueError('Ответ отклонён валидатором процедуры')
                        normalized[source]=list(norm.normalize(docs[source],app,source,config.get('normalize_params',{})))
                    except data_errors as ex:
                        db.execute("UPDATE input_status SET STATUS='VALIDATION_ERROR',ERROR=? WHERE APP_ID=? AND SOURCE=?",((type(ex).__name__+': '+str(ex))[:300],app,source));valid=False
                normalize_seconds+=time.monotonic()-tick
                if not valid:continue
                union=normalized['BEFORE']+normalized['AFTER'];stage='segments';tick=time.monotonic()
                try:
                    segment=norm.segments(docs['BEFORE'],docs['AFTER'],app,config.get('normalize_params',{}));stage='compare'
                    rows=list(comp.compare(union,config.get('compare_params',{})))
                except data_errors as ex:
                    db.execute('INSERT INTO pair_errors VALUES (?,?,?)',(app,stage,(type(ex).__name__+': '+str(ex))[:300]));continue
                # Пары без полноценного ответа в каждом срезе не попадают в comparison/facts.
                # ATTRIBUTE_NEW/LOST внутри корректной пары сохраняются без изменений.
                db.executemany('INSERT INTO normalized VALUES (?,?,?,?,?)',[(x['APP_ID'],x['SOURCE'],x['PATH'],x['ARRAY_KEY'],json.dumps(x,ensure_ascii=False)) for x in union])
                db.executemany('INSERT INTO comparison VALUES ('+','.join('?' for _ in COLUMNS)+')',[[x[c] for c in COLUMNS] for x in rows])
                db.execute('INSERT INTO segments VALUES (?,?)',(app,json.dumps(segment,ensure_ascii=False)))
                compare_seconds+=time.monotonic()-tick
            db.commit();log('Приведение и сравнение завершено',round(time.monotonic()-started,3),75,config['comparator'])
            log('Нормализация: обработка и запись строк',round(normalize_seconds,3),script=config['normalizer'])
            log('Сравнение: обработка и запись строк',round(compare_seconds,3),script=config['comparator'])
            db.executescript('CREATE INDEX cmp_path_diff ON comparison(PATH,DIFF_FLAG); CREATE INDEX cmp_type ON comparison(CHANGE_TYPE); CREATE INDEX cmp_key ON comparison(KEY); CREATE INDEX cmp_diff ON comparison(DIFF_FLAG,APP_ID);')
            log('Агрегация фактов counts',progress=80,script=config['aggregator']); started=time.monotonic()
            mart=Mart(db)
            db.executemany('INSERT INTO facts VALUES (?)',((json.dumps(f,ensure_ascii=False),) for f in agg.aggregate(mart.rows(),list(mart.segments()),config.get('aggregate_params',{}))))
            db.commit();log('Агрегаты готовы',round(time.monotonic()-started,3),90,config['aggregator'])
        quality_mart=Mart(db);known=quality_mart.has_input_quality();input_errors=quality_mart.input_errors();pair_errors=quality_mart.pair_errors()
        quality={'available':known,'input_apps':quality_mart.input_total_apps(),'comparable_apps':quality_mart.total_apps(),'excluded_app_ids':quality_mart.excluded_app_ids(),'errors':input_errors,'pair_errors':pair_errors}
        dump(folder/'input_errors.json',quality)
        (folder/'parse_errors.log').write_text(''.join(x['SOURCE']+':'+x['APP_ID']+'\n' for x in input_errors),encoding='utf-8')
        log(('Качество входных данных: исключено '+str(len(quality['excluded_app_ids']))+' заявок') if known else 'В старой витрине нет сведений о качестве входных данных',progress=94)
        log('Автоматические проверки',progress=95)
        checks(db,folder,config,log)
        mart=Mart(db); segs=list(mart.segments()); changed=mart.app_ids(diff_flag=1)
        summary=dict(total_apps=mart.total_apps(), changed_apps=len(changed),decision_changed=sum(s['DECISION_CHANGED'] for s in segs),
            rows=mart.count(), normalized_rows=db.execute('SELECT count(*) FROM normalized').fetchone()[0], facts=db.execute('SELECT count(*) FROM facts').fetchone()[0],
            transitions={}, change_types={})
        summary.update(input_quality_available=known,input_apps=quality['input_apps'],comparable_apps=summary['total_apps'],excluded_apps=len(quality['excluded_app_ids']),input_errors={source:sum(x['SOURCE']==source for x in input_errors) for source in ('BEFORE','AFTER')},pair_errors=len(pair_errors))
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
