"""Exact set-union pivot, with server-side per-user layouts."""
import json,time
from urllib.parse import urlencode
import pages as p,store as s,reports
FIELDS=('PATH','CHANGE_TYPE','ARRAY_KEY','VALUE_ADJUSTED','PREFIX','CLASS','SUFFIX','KEY','SOURCE','DIFF_FLAG')
TYPES={'VALUE_CHANGED':'Значение изменилось','ATTRIBUTE_NEW':'Атрибут появился','ATTRIBUTE_LOST':'Атрибут исчез','SAME':'Без изменений'}
def label(field,value):return TYPES.get(value,value) if field=='CHANGE_TYPE' else value

def query(value):
    allowed={'group','columns','path','product','diff','decision','filter','offset','column_offset'}
    q={k:str(v) for k,v in value.items() if k in allowed}
    if len(json.dumps(q))>12000:raise ValueError('Слишком большой макет')
    axes=[]
    for key,default,limit in [('group','PATH,CHANGE_TYPE',4),('columns','SOURCE',2)]:
        dims=q.get(key,default).split(',') if q.get(key,default) else []
        if (key=='group' and not dims) or len(dims)>limit or any(x not in FIELDS for x in dims):raise ValueError('Строки: до 4 полей, столбцы: до 2')
        axes+=dims;q[key]=','.join(dims)
    if len(set(axes))!=len(axes):raise ValueError('Поле должно находиться только в одной области')
    filters=json.loads(q.get('filter','{}'))
    if not isinstance(filters,dict) or any(k not in FIELDS or not isinstance(v,(str,int)) for k,v in filters.items()):raise ValueError('Неверный фильтр сводной')
    if q.get('diff','1') not in ('0','1','all'):raise ValueError('Неверный фильтр различий')
    return q

def load(user,eid,kind='exact'):
    with s.db() as c:r=c.execute('SELECT body FROM views WHERE user_id=? AND execution=? AND kind=?',(user['id'],eid,kind)).fetchone()
    return json.loads(r[0]) if r else {}
def save(user,eid,value,kind='exact'):
    if kind=='exact':value=query(value)
    elif kind=='wdr':
        # No external data source, callback, URL or executable code is persisted.
        value={k:v for k,v in value.items() if k in ('slice','options','formats','conditions')}
        if len(json.dumps(value))>100000:raise ValueError('Слишком большой макет')
    else:raise ValueError('Неизвестный макет')
    with s.db() as c:
        c.execute('BEGIN IMMEDIATE')
        e=c.execute('SELECT * FROM executions WHERE id=? AND deleted_at IS NULL AND purged_at IS NULL',(eid,)).fetchone()
        if not e:raise FileNotFoundError('Выполнение не найдено')
        s.require(c,user,s.resource(c,e['run']))
        c.execute('INSERT OR REPLACE INTO views VALUES (?,?,?,?,?)',(user['id'],eid,kind,json.dumps(value,ensure_ascii=False),time.time()))

def render(user,e,head,q):
    eid=e['id'];base='/executions/'+eid
    q={k:v for k,v in q.items() if k in ('group','columns','path','product','diff','decision','filter','offset','column_offset')};q=query(q or load(user,eid));rows_dims=q['group'].split(',') if q['group'] else [];cols_dims=q['columns'].split(',') if q['columns'] else []
    cells={};rowsets={};colsets={};allapps=set()
    for fact,apps in reports.selected(eid,q):
        rk=tuple(str(fact[x]) for x in rows_dims);ck=tuple(str(fact[x]) for x in cols_dims)
        cells.setdefault((rk,ck),set()).update(apps);rowsets.setdefault(rk,set()).update(apps);colsets.setdefault(ck,set()).update(apps);allapps.update(apps)
    rkeys=sorted(rowsets,key=lambda k:(-len(rowsets[k]),k));ckeys=sorted(colsets,key=lambda k:tuple(('0' if v=='BEFORE' else '1' if v=='AFTER' else v) if f=='SOURCE' else v for f,v in zip(cols_dims,k)))
    off=max(0,int(q.get('offset',0)));co=max(0,int(q.get('column_offset',0)));shown=ckeys[co:co+12]
    def href(filters,apps):
        selected={**q,'filter':json.dumps({**json.loads(q.get('filter','{}')),**filters},ensure_ascii=False),'return':json.dumps(q,ensure_ascii=False)}
        selected['offset']='0';selected.pop('column_offset',None)
        if 'PATH' in filters:selected['focus_path']=filters['PATH']
        if 'ARRAY_KEY' in filters:selected['focus_array']=filters['ARRAY_KEY']
        return p.link(base+'/applications?'+urlencode(selected),len(apps)) if apps else '0'
    rendered=[]
    for rk in rkeys[off:off+100]:
        filt=dict(zip(rows_dims,rk));rendered.append([*[p.E(label(k,v)) for k,v in zip(rows_dims,rk)],*[href({**filt,**dict(zip(cols_dims,ck))},cells.get((rk,ck),set())) for ck in shown],href(filt,rowsets[rk])])
    total=['<strong>Уникальные заявки</strong>']+['']*max(0,len(rows_dims)-1)+[href(dict(zip(cols_dims,ck)),colsets[ck]) for ck in shown]+[href({},allapps)]
    def area(name,title,values):
        return '<div class="pivot-zone" data-zone="'+name+'"><h3>'+title+'</h3>'+''.join('<div class="pivot-field" draggable="true" data-field="'+v+'">'+p.E(v)+' <button type="button" data-move="'+v+'" aria-label="Переместить '+v+'">↔</button></div>' for v in values)+'</div>'
    controls=area('unused','Доступные поля',[x for x in FIELDS if x not in rows_dims+cols_dims])+area('group','Строки',rows_dims)+area('columns','Столбцы',cols_dims)
    fields=p.input_('group',q['group'],'hidden')+p.input_('columns',q['columns'],'hidden')
    fields+='<div class="pivot-areas">'+controls+'</div><p class="muted">Перетащите поля между областями. Кнопка ↔ перемещает поле без мыши. Порядок полей: слева направо.</p>'
    for key,title,widget in [('path','Путь содержит',p.input_('path',q.get('path',''))),('product','Продукт (ID)',p.input_('product',q.get('product',''))),('diff','Различия',p.select('diff',[('1','С изменениями'),('0','Без изменений'),('all','Все')],q.get('diff','1'))),('decision','Решение',p.select('decision',[('','Любое'),('1','Изменилось'),('0','Не изменилось')],q.get('decision','')))]:fields+='<label>'+title+widget+'</label>'
    fields+='<label>Фильтр по полю'+p.select('filter_field',[('','Не задан')]+[(x,x) for x in FIELDS])+'</label><label>Точное значение'+p.input_('filter_value')+'</label>'+p.input_('filter',q.get('filter','{}'),'hidden')+'<p>Точный фильтр: <code>'+p.E(q.get('filter','{}'))+'</code> <button type="button" data-clear-filter>Очистить</button></p>'+p.button('Построить')
    body=head+'<div class="card"><h2>Уникальные заявки</h2><form method="get" data-pivot-layout>'+fields+'</form>'+p.form(user,base+'/layout',p.input_('layout',json.dumps(q,ensure_ascii=False),'hidden')+p.button('Сохранить этот вид для меня'))+'<p class="muted">Одна заявка учитывается один раз внутри ячейки и итогов. Итог «Все срезы» объединяет BEFORE и AFTER. Нажмите число для списка заявок.</p></div>'
    body+='<div class="card">'+p.table([*rows_dims,*[' / '.join(label(k,v) for k,v in zip(cols_dims,ck)) or 'Все' for ck in shown],'Все срезы'],rendered+[total])+'</div>'
    def nav(param,offset,step,total):
        return '<div class="row">'+(p.link(base+'/pivot?'+urlencode({**q,param:max(0,offset-step)}),'← Назад','button') if offset else '')+p.E(str(offset+1 if total else 0)+'–'+str(min(offset+step,total))+' из '+str(total))+(p.link(base+'/pivot?'+urlencode({**q,param:offset+step}),'Далее →','button') if offset+step<total else '')+'</div>'
    body+=nav('offset',off,100,len(rkeys))
    if len(ckeys)>12:body+='<p>Столбцы</p>'+nav('column_offset',co,12,len(ckeys))
    # The standard pivot sums per-attribute observations; exact unique-app matrix above is authoritative for app counts.
    init={'base':base,'query':q,'csrf':user['csrf'],'layout':load(user,eid,'wdr')}
    body+='<details class="card" data-wdr-panel><summary>Конструктор WebDataRocks · наблюдения атрибутов</summary><p>Здесь сумма CNT — число наблюдений атрибутов в срезах. Число уникальных заявок смотрите в таблице выше. Редактор полей позволяет менять строки, столбцы и фильтры.</p><button type="button" data-wdr-save>Сохранить макет конструктора</button><p data-wdr-message role="status"></p><div id="wdr-pivot"></div></details><script type="application/json" id="pivot-settings">'+json.dumps(init,ensure_ascii=False).replace('<','\\u003c')+'</script>'
    extra='<link rel="stylesheet" href="/assets/vendor/webdatarocks/webdatarocks.min.css"><script src="/assets/vendor/webdatarocks/webdatarocks.js"></script><script src="/assets/vendor/webdatarocks/webdatarocks.toolbar.min.js"></script><script src="/assets/pivot.js"></script>'
    return p.shell(user,'Сводный отчёт',body,'run',extra=extra)
