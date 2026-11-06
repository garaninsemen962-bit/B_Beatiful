"""Resource mutations and execution snapshots. No user module imports here."""
import hashlib,json,shutil,time
from pathlib import Path
import store as s
import sandbox
from uploads import dataset_zip

def save_script(user,fields,code,rid=None):
    kind=fields['script_kind'];title,desc=s.script_info(code,kind)
    name=fields.get('name','').strip() or title
    if not name:raise ValueError('Укажите название скрипта')
    description=fields.get('description','').strip() or desc
    code_bytes=code.encode('utf-8');sha=hashlib.sha256(code_bytes).hexdigest();file=s.uid()+'.py';path=s.STATE/'objects'/file;path.write_bytes(code_bytes)
    body={'script_kind':kind,'file':file,'sha256':sha,'filename':fields.get('filename','script.py')}
    with s.db() as c:
        if rid:
            r=s.resource(c,rid)
            if r['kind']!='script':raise ValueError('Не скрипт')
            s.revise(c,user,r,fields['revision'],name,description,body)
        else:rid=s.create(c,user,'script',name,description,body)
    return rid

def save_dataset(user,fields,raw,rid=None):
    folder=s.STATE/'objects'/s.uid()
    try:
        info=dataset_zip(raw,folder)
        body={'folder':folder.name,'sha256':hashlib.sha256(raw).hexdigest(),**info}
        with s.db() as c:
            if rid:
                r=s.resource(c,rid)
                if r['kind']!='dataset':raise ValueError('Не датасет')
                s.revise(c,user,r,fields['revision'],fields['name'],fields.get('description',''),body)
            else:rid=s.create(c,user,'dataset',fields['name'],fields.get('description',''),body)
        return rid
    except Exception:shutil.rmtree(folder,ignore_errors=True);raise

def validate_config(c,user,cfg):
    dataset=s.resource(c,cfg['dataset']);s.require(c,user,dataset)
    if dataset['kind']!='dataset':raise ValueError('Неверный датасет')
    refs=[]
    for key,kind in [('normalizer','normalizer'),('comparator','comparator'),('aggregator','aggregator')]:refs.append((cfg[key],kind))
    if not isinstance(cfg.get('checks',[]),list):raise ValueError('checks должен быть списком')
    if len(cfg.get('checks',[]))>50:raise ValueError('Не более 50 проверок')
    refs.extend((rid,'check') for rid in cfg.get('checks',[]))
    for rid,kind in refs:
        r=s.resource(c,rid);s.require(c,user,r)
        if r['kind']!='script' or json.loads(r['body'])['script_kind']!=kind:raise ValueError('Неверное назначение скрипта')
    for key in ('normalize_params','compare_params','aggregate_params','check_params'):
        if not isinstance(cfg.get(key,{}),dict):raise ValueError(key+' должен быть объектом')
    return dataset,refs

def save_config(user,fields,rid=None):
    cfg={k:fields[k] for k in ('dataset','normalizer','comparator','aggregator')}
    cfg['checks']=fields.get('checks',[])
    params=json.loads(fields.get('params','{}'));cfg.update({k:params.get(k,{}) for k in ('normalize_params','compare_params','aggregate_params','check_params')})
    with s.db() as c:
        validate_config(c,user,cfg)
        if rid:s.revise(c,user,s.resource(c,rid),fields['revision'],fields['name'],fields.get('description',''),cfg)
        else:rid=s.create(c,user,'config',fields['name'],fields.get('description',''),cfg)
    return rid

def create_run(user,fields):
    with s.db() as c:
        config=s.resource(c,fields['config']);s.require(c,user,config)
        if config['kind']!='config':raise ValueError('Неверная конфигурация')
        cfg=json.loads(config['body']);validate_config(c,user,cfg)
        return s.create(c,user,'run',fields['name'],fields.get('description',''),{'config':cfg,'source_config':config['id'],'source_revision':config['revision']})

def edit_run(user,rid,fields):
    cfg={k:fields[k] for k in ('dataset','normalizer','comparator','aggregator')}
    cfg['checks']=fields.get('checks',[])
    params=json.loads(fields.get('params','{}'))
    cfg.update({k:params.get(k,{}) for k in ('normalize_params','compare_params','aggregate_params','check_params')})
    with s.db() as c:
        r=s.resource(c,rid);validate_config(c,user,cfg)
        body={**json.loads(r['body']),'config':cfg}
        s.revise(c,user,r,fields['revision'],fields['name'],fields.get('description',''),body)

def execute(user,rid):
    with s.db() as c:
        r=s.resource(c,rid);s.require(c,user,r,'editor')
    ok,message=sandbox.available()
    if not ok:raise ValueError(message+' Код на сервере без контейнера запускаться не будет.')
    with sandbox.lock:
        with s.db() as c:
            c.execute('BEGIN IMMEDIATE')
            r=s.resource(c,rid);s.require(c,user,r,'editor')
            if r['kind']!='run':raise ValueError('Не прогон')
            active=c.execute("SELECT count(*) FROM executions WHERE state IN ('queued','running')").fetchone()[0]
            if active>=sandbox.MAX_WORKERS:raise ValueError('Уже выполняются два расчёта. Повторите позже.')
            cfg=json.loads(r['body'])['config'];dataset,refs=validate_config(c,user,cfg)
            eid=s.uid();folder=s.STATE/'executions'/eid;inp=folder/'input';(inp/'scripts').mkdir(parents=True)
            versions=[];names={}
            for srid,kind in refs:
                resource=s.resource(c,srid);body=json.loads(resource['body']);name='s_'+srid+'.py';names[srid]=name
                shutil.copyfile(s.STATE/'objects'/body['file'],inp/'scripts'/name)
                versions.append({'resource':srid,'revision':resource['revision'],'name':resource['name'],'sha256':body['sha256'],'file':name,'kind':kind})
            compiled={key:names[cfg[key]] for key in ('normalizer','comparator','aggregator')}
            compiled.update(dataset='/dataset',checks=[names[x] for x in cfg['checks']],normalize_params=cfg.get('normalize_params',{}),compare_params=cfg.get('compare_params',{}),aggregate_params=cfg.get('aggregate_params',{}),
                check_params={names[x]:cfg.get('check_params',{}).get(x,{}) for x in cfg['checks']})
            (inp/'config.json').write_text(json.dumps(compiled,ensure_ascii=False),encoding='utf-8')
            manifest={'run_revision':r['revision'],'dataset':{'resource':dataset['id'],'name':dataset['name'],'revision':dataset['revision'],'sha256':json.loads(dataset['body'])['sha256']},'scripts':versions,'config':cfg,'image':sandbox.RESOLVED_IMAGE}
            c.execute('INSERT INTO executions VALUES (?,?,?,?,?,?,NULL,?)',(eid,rid,user['id'],'queued',json.dumps(manifest,ensure_ascii=False),time.time(),''))
            s.audit(c,user,rid,'Запуск выполнения',{'execution':eid,**manifest})
        try:sandbox.launch(eid,s.STATE/'objects'/json.loads(dataset['body'])['folder'])
        except Exception as ex:sandbox.finish(eid,'failed',str(ex));raise
    return eid

def import_demo(user):
    existing=s.visible(user)
    if any(x['name']=='Демонстрационный прогон' for x in existing):return
    demo=s.ROOT/'data/RUN-20260827-0001';folder=s.STATE/'objects'/s.uid();shutil.copytree(demo,folder)
    with s.db() as c:
        data=s.create(c,user,'dataset','Открытый датасет · 2002 заявки','Данные участников хакатона',{'folder':folder.name,'apps':2002,'files':4004,'sha256':'bundled-open-dataset'},'public')
        ids={}
        labels={'normalize':'Нормализация ответов СПР','compare':'Сравнение атрибутов','aggregate':'Агрегация фактов counts','check_ar_drop_by_product':'Падение доли одобрений','check_attribute_new_loss':'Изменение состава атрибутов','check_external_score_drift':'Изменение внешнего скора','check_stop_factor_disappeared':'Исчезновение стоп-фактора','check_target_model_changed':'Смена целевой модели'}
        for p in [*(s.ROOT/'procedures').glob('*.py'),*(s.ROOT/'checks').glob('*.py')]:
            code=p.read_text(encoding='utf-8');kind={'normalize':'normalizer','compare':'comparator','aggregate':'aggregator'}.get(p.stem,'check');_,desc=s.script_info(code,kind)
            file=s.uid()+'.py';(s.STATE/'objects'/file).write_text(code,encoding='utf-8')
            ids[p.stem]=s.create(c,user,'script',labels.get(p.stem,p.stem),desc,{'script_kind':kind,'file':file,'filename':p.name,'sha256':hashlib.sha256(code.encode()).hexdigest()},'public')
        cfg={'dataset':data,'normalizer':ids['normalize'],'comparator':ids['compare'],'aggregator':ids['aggregate'],'checks':[v for k,v in ids.items() if k.startswith('check_')], 'normalize_params':{},'compare_params':{},'aggregate_params':{},'check_params':{}}
        cid=s.create(c,user,'config','Демонстрационная конфигурация','Выданный датасет и пять исходных проверок',cfg,'public')
        s.create(c,user,'run','Демонстрационный прогон','Первый полный расчёт',{'config':cfg,'source_config':cid,'source_revision':1},'public')
