"""Asynchronous Gemini second opinion. Keys never enter browsers or containers."""
import hashlib,json,os,re,threading,time
import gemini_client,ai_models,ai_prompts
from urllib.parse import quote,urlencode
import store as s,reports
from engine import Mart
PROMPT_VERSION='decision-diff-evidence-3-prompts'
SYSTEM=ai_prompts.DEFAULT_SYSTEM
STR={'type':'string'}
REFS={'type':'array','items':STR}
SCHEMA={'type':'object','required':['summary','assessment','findings','review_plan','limitations'],'properties':{
 'summary':STR,'assessment':STR,
 'findings':{'type':'array','items':{'type':'object','required':['title','severity','observation','quantitative_details','interpretation','hypothesis','alternatives','recommendation','evidence_ids'],'properties':{
  **{k:STR for k in ('title','observation','quantitative_details','interpretation','hypothesis','recommendation')},
  'severity':{'type':'string','enum':['low','medium','high','critical']},'alternatives':{'type':'array','items':STR},'evidence_ids':REFS}}},
 'review_plan':{'type':'array','items':{'type':'object','required':['priority','action','expected_result','evidence_ids'],'properties':{
  'priority':{'type':'string','enum':['P1','P2','P3']},'action':STR,'expected_result':STR,'evidence_ids':REFS}}},
 'limitations':{'type':'array','items':STR}}}
lock=threading.Lock()
def configured():
    if not os.environ.get('GEMINI_API_KEY'):return False,'На сервере не задан GEMINI_API_KEY'
    if not ai_models.choices():return False,'В настройке GEMINI_MODELS нет допустимых моделей'
    return True,'Gemini настроен; запрос выполняется только по кнопке'
def authorized(c,user,eid,minimum='reader'):
    row=c.execute('SELECT * FROM executions WHERE id=? AND deleted_at IS NULL AND purged_at IS NULL',(eid,)).fetchone()
    if not row:raise FileNotFoundError('Выполнение не найдено')
    e=dict(row);r=s.resource(c,e['run']);s.require(c,user,r,minimum)
    if e['state']!='done':raise ValueError('Анализ доступен после успешного расчёта')
    return e,r

def evidence(eid):
    base='/executions/'+eid;summary=reports.artifact(eid,'summary.json');checks=reports.artifact(eid,'checks.json')
    rows=[{'id':'E1','kind':'summary','data':summary,'url':base}]
    def add(kind,data,url):rows.append({'id':'E'+str(len(rows)+1),'kind':kind,'data':data,'url':url})
    check_rows=checks['rows'];selected_checks=check_rows[:80]
    add('coverage',{'total_check_rows':len(check_rows),'included_check_rows':len(selected_checks),'failed_rows':sum(x.get('RESULT')=='FAIL' for x in check_rows),'low_confidence_rows':sum(x.get('CONFIDENCE')=='LOW' for x in check_rows),'path_group_limit':30,'product_group_limit':20,'notes':'Сводная и бизнес-проверки учитывают только сопоставимые пары, если input_quality_available=true. Проверки ошибок используют input_apps; при неизвестном качестве требуется новый полный расчёт. Группы путей и продуктов пересекаются. Их уникальные заявки нельзя суммировать. Совпадение изменения атрибута и решения не доказывает причинность.'},base+'/checks')
    for x in selected_checks:
        data={k:x.get(k) for k in ('LABEL','SOURCE','ARGUMENT','CNT','TOTAL','METRIC','RESULT','CONFIDENCE')}
        data={k:(v[:240] if isinstance(v,str) else v) for k,v in data.items()}
        add('check',data,base+'/checks')
    with reports.connect(eid) as c:
        segments={x['APP_ID']:x for x in Mart(c).segments()};products={}
        changed={x[0] for x in c.execute('SELECT DISTINCT APP_ID FROM comparison WHERE DIFF_FLAG=1')}
        for app,seg in segments.items():
            for product in set(seg.get('PRODUCTS','').split(';'))-{''}:products.setdefault(product,set()).add(app)
        rows[1]['data'].update(total_product_groups=len(products),included_product_groups=min(20,len(products)))
        for product,ids in sorted(products.items(),key=lambda x:(-len(x[1]),x[0]))[:20]:
            transitions={}
            for app in ids:
                seg=segments[app];key=seg['DECISION_BEFORE']+' → '+seg['DECISION_AFTER'];transitions[key]=transitions.get(key,0)+1
            add('product',{'product':product,'unique_apps':len(ids),'apps_with_differences':len(ids&changed),'decision_changed':sum(bool(segments[x]['DECISION_CHANGED']) for x in ids),'decision_transitions':transitions,'membership':'Продукт присутствует хотя бы в одном срезе; группы могут пересекаться.'},base+'/applications?'+urlencode({'product':product,'diff':'all'}))
        top=list(c.execute('SELECT PATH,CHANGE_TYPE,count(*) attributes,count(distinct APP_ID) apps FROM comparison WHERE DIFF_FLAG=1 GROUP BY PATH,CHANGE_TYPE ORDER BY apps DESC,PATH LIMIT 30'))
        rows[1]['data'].update(total_changed_path_groups=c.execute('SELECT count(*) FROM (SELECT PATH,CHANGE_TYPE FROM comparison WHERE DIFF_FLAG=1 GROUP BY PATH,CHANGE_TYPE)').fetchone()[0],included_path_groups=len(top))
        for path,typ,attributes,apps in top:
            ids={x[0] for x in c.execute('SELECT DISTINCT APP_ID FROM comparison WHERE PATH=? AND CHANGE_TYPE=? AND DIFF_FLAG=1',(path,typ))}
            add('changed_path',{'PATH':path[:300],'CHANGE_TYPE':typ,'attributes':attributes,'unique_apps':apps,'decision_changed_among_affected':sum(bool(segments.get(x,{}).get('DECISION_CHANGED')) for x in ids),'note':'Пересечение по заявкам, не доказательство причинной связи.'},base+'/applications?'+urlencode({'filter':json.dumps({'PATH':path,'CHANGE_TYPE':typ}),'focus_path':path}))
    payload=[{k:v for k,v in x.items() if k!='url'} for x in rows]
    if len(json.dumps(payload,ensure_ascii=False).encode())>75000:raise ValueError('Сводка для Gemini превышает 75 КБ; уменьшите размер результатов проверок')
    return rows,payload

def transport(model,payload,system=None):
    return gemini_client.generate(model,system if system is not None else SYSTEM+"\n\n"+ai_prompts.CONTRACT,payload,SCHEMA,max_tokens=14000)

def validate(result,ids):
    if not isinstance(result,dict):raise ValueError('Ответ Gemini должен быть объектом')
    def text(obj,k,limit=4000):
        v=obj.get(k)
        if not isinstance(v,str) or not v.strip() or len(v)>limit:raise ValueError('Неверное текстовое поле ответа Gemini: '+k)
        return v
    def refs(obj):
        values=obj.get('evidence_ids')
        if not isinstance(values,list) or not 1<=len(values)<=15 or any(not isinstance(x,str) or x not in ids for x in values):raise ValueError('Gemini сослался на отсутствующее доказательство; ответ не опубликован')
        return list(dict.fromkeys(values))
    summary=text(result,'summary',10000);assessment=text(result,'assessment',5000);findings=result.get('findings');limitations=result.get('limitations');plan=result.get('review_plan')
    if not isinstance(findings,list) or len(findings)>15 or not isinstance(limitations,list) or len(limitations)>20 or any(not isinstance(x,str) or not x.strip() or len(x)>2000 for x in limitations):raise ValueError('Неверные списки ответа Gemini')
    if not isinstance(plan,list) or not 1<=len(plan)<=12:raise ValueError('Gemini не вернул план проверки')
    valid=[]
    for item in findings:
        if not isinstance(item,dict) or item.get('severity') not in ('low','medium','high','critical'):raise ValueError('Неверная важность вывода Gemini')
        alternatives=item.get('alternatives')
        if not isinstance(alternatives,list) or len(alternatives)>4 or any(not isinstance(x,str) or len(x)>2000 for x in alternatives):raise ValueError('Неверные альтернативные объяснения Gemini')
        valid.append({**{k:text(item,k,5000 if k=='recommendation' else 4000) for k in ('title','observation','quantitative_details','interpretation','hypothesis','recommendation')},'severity':item['severity'],'alternatives':alternatives,'evidence_ids':refs(item)})
    steps=[]
    for item in plan:
        if not isinstance(item,dict) or item.get('priority') not in ('P1','P2','P3'):raise ValueError('Неверный приоритет плана проверки')
        steps.append({'priority':item['priority'],'action':text(item,'action'),'expected_result':text(item,'expected_result'),'evidence_ids':refs(item)})
    return {'summary':summary,'assessment':assessment,'findings':valid,'review_plan':steps,'limitations':limitations}

def start(user,eid,model=None,prompt_mode='personal',system_prompt=None,prompt_revision=None):
    with s.db() as c:
        authorized(c,user,eid,'editor')
        prompt=ai_prompts.resolve(c,user,prompt_mode,system_prompt,prompt_revision)
    ok,message=configured()
    if not ok:raise ValueError(message)
    model=ai_models.validate(model or ai_models.default());refs,payload=evidence(eid)
    fingerprint=hashlib.sha256(json.dumps([PROMPT_VERSION,model,payload,prompt['sha256']],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    with lock:
        with s.db() as c:
            c.execute('BEGIN IMMEDIATE');e,r=authorized(c,user,eid,'editor')
            existing=c.execute("SELECT id FROM analyses WHERE execution=? AND fingerprint=? AND state IN ('queued','running','done') ORDER BY created DESC LIMIT 1",(eid,fingerprint)).fetchone()
            if existing:return existing[0]
            if c.execute("SELECT count(*) FROM analyses WHERE state IN ('queued','running')").fetchone()[0]>=2:raise ValueError('Уже выполняются два анализа Gemini')
            recent=c.execute('SELECT max(created) FROM analyses WHERE execution=? AND model=? AND fingerprint=?',(eid,model,fingerprint)).fetchone()[0]
            if recent and time.time()-recent<30:raise ValueError('Повторить запрос к этой модели можно через 30 секунд')
            aid=s.uid();c.execute('INSERT INTO analyses(id,execution,actor,state,model,fingerprint,evidence,result,error,created,finished,prompt_config) VALUES (?,?,?,?,?,?,?,?,?,?,NULL,?)',(aid,eid,user['id'],'queued',model,fingerprint,json.dumps(refs,ensure_ascii=False),'','',time.time(),json.dumps(prompt,ensure_ascii=False)))
            s.audit(c,user,r['id'],'Запуск анализа Gemini',{'execution':eid,'analysis':aid,'model':model,'fingerprint':fingerprint,'prompt_version':PROMPT_VERSION,'prompt_sha256':prompt['sha256'],'prompt_source':prompt['source']})
        threading.Thread(target=work,args=(aid,user,r['id'],model,payload,refs,prompt),daemon=True).start()
    return aid

def work(aid,user,rid,model,payload,refs,prompt):
    state='failed';error='';result=''
    try:
        with s.db() as c:c.execute("UPDATE analyses SET state='running' WHERE id=?",(aid,))
        result=json.dumps(validate(transport(model,payload,system=prompt['system']),{x['id'] for x in refs}),ensure_ascii=False);state='done'
    except ValueError as ex:error=str(ex)[:1000]
    except Exception:error='Не удалось обработать ответ Gemini. Проверьте настройки и повторите запрос.'
    with s.db() as c:
        c.execute('UPDATE analyses SET state=?,result=?,error=?,finished=? WHERE id=?',(state,result,error,time.time(),aid))
        s.audit(c,user,rid,'Анализ Gemini завершён',{'analysis':aid,'state':state,'error':error})

def recover():
    with s.db() as c:c.execute("UPDATE analyses SET state='failed',error='Сервер перезапущен. Повторите запрос.',finished=? WHERE state IN ('queued','running')",(time.time(),))
