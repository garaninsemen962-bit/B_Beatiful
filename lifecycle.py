"""Trash, restoration and guarded physical cleanup; audit/revision rows are retained."""
import json,re,shutil,time
from pathlib import Path
import store as s

def owned(c,user,r):s.require(c,user,r,'owner')
def cfg_refs(body):
    cfg=body.get('config',body)
    return {cfg.get(k) for k in ('dataset','normalizer','comparator','aggregator')}|set(cfg.get('checks',[]))
def dependencies(c,rid):
    found=[]
    for row in c.execute("SELECT id,kind,name,owner,visibility,public_role,deleted_at,purged_at FROM resources WHERE purged_at IS NULL AND kind IN ('config','run') AND id!=?",(rid,)):
        for v in c.execute('SELECT body FROM versions WHERE resource=?',(row['id'],)):
            if rid in cfg_refs(json.loads(v[0])):found.append(dict(row));break
    for e in c.execute('SELECT id,run,manifest FROM executions WHERE purged_at IS NULL'):
        m=json.loads(e['manifest'])
        if rid==m.get('dataset',{}).get('resource') or any(x.get('resource')==rid for x in m.get('scripts',[])):
            found.append({'id':e['id'],'kind':'execution','name':'Выполнение '+e['id'][:12],'run':e['run']})
    return found

def resource_action(user,rid,action,expected,confirm=''):
    cleanup=[]
    with s.db() as c:
        c.execute('BEGIN IMMEDIATE');r=s.resource(c,rid,include_deleted=True);owned(c,user,r)
        if r['purged_at'] is not None:raise ValueError('Ресурс уже удалён навсегда')
        if str(r['revision'])!=str(expected):raise ValueError('Ресурс изменён. Обновите страницу подтверждения.')
        active=c.execute("SELECT count(*) FROM executions WHERE run=? AND state IN ('queued','running')",(rid,)).fetchone()[0]+c.execute("SELECT count(*) FROM analyses a JOIN executions e ON e.id=a.execution WHERE e.run=? AND a.state IN ('queued','running')",(rid,)).fetchone()[0]
        if action=='delete':
            if r['deleted_at'] is not None:raise ValueError('Ресурс уже в корзине')
            if active:raise ValueError('Дождитесь завершения выполняющихся расчётов прогона')
            c.execute('UPDATE resources SET deleted_at=?,deleted_by=? WHERE id=?',(time.time(),user['id'],rid))
            label='Удаление в корзину'
        elif action=='restore':
            if r['deleted_at'] is None:raise ValueError('Ресурс не находится в корзине')
            c.execute('UPDATE resources SET deleted_at=NULL,deleted_by=NULL WHERE id=?',(rid,));label='Восстановление ресурса'
        elif action=='purge':
            if r['deleted_at'] is None:raise ValueError('Сначала переместите ресурс в корзину')
            if confirm!=r['name']:raise ValueError('Для окончательного удаления введите точное название ресурса')
            if active:raise ValueError('Прогон ещё выполняется')
            refs=dependencies(c,rid)
            if refs:raise ValueError('Окончательное удаление запрещено: ресурс используется в конфигурациях, прогонах или истории выполнений. Сначала удалите навсегда связанные ресурсы.')
            if r['kind']=='run':
                for e in c.execute('SELECT id FROM executions WHERE run=? AND purged_at IS NULL',(rid,)):
                    cleanup.append(('execution',e['id']))
                    c.execute("UPDATE analyses SET evidence='',result='',prompt_config=NULL WHERE execution=?",(e['id'],))
                    c.execute('DELETE FROM views WHERE execution=?',(e['id'],))
                    s.audit(c,user,rid,'Окончательное удаление выполнения',{'execution':e['id'],'with_run':True})
                c.execute('UPDATE executions SET deleted_at=coalesce(deleted_at,?),deleted_by=?,purged_at=? WHERE run=? AND purged_at IS NULL',(time.time(),user['id'],time.time(),rid))
            for v in c.execute('SELECT body FROM versions WHERE resource=?',(rid,)):
                body=json.loads(v[0])
                if r['kind']=='dataset':cleanup.append(('object',body['folder']))
                if r['kind']=='script':cleanup.append(('object',body['file']))
            c.execute('UPDATE resources SET purged_at=? WHERE id=?',(time.time(),rid));label='Окончательное удаление ресурса'
        else:raise ValueError('Неизвестное действие')
        s.audit(c,user,rid,label,{'name':r['name'],'kind':r['kind'],'revision':r['revision']})
    return cleanup_files(cleanup)

def execution_action(user,eid,action,confirm=''):
    cleanup=[]
    with s.db() as c:
        c.execute('BEGIN IMMEDIATE');e=c.execute('SELECT * FROM executions WHERE id=?',(eid,)).fetchone()
        if not e:raise FileNotFoundError('Выполнение не найдено')
        r=s.resource(c,e['run'],include_deleted=True);owned(c,user,r)
        if e['purged_at'] is not None or r['purged_at'] is not None:raise ValueError('Выполнение уже удалено навсегда')
        if e['state'] in ('running','queued') or c.execute("SELECT count(*) FROM analyses WHERE execution=? AND state IN ('queued','running')",(eid,)).fetchone()[0]:raise ValueError('Дождитесь завершения выполнения и анализа Gemini')
        if action=='delete':
            if e['deleted_at'] is not None:raise ValueError('Выполнение уже в корзине')
            c.execute('UPDATE executions SET deleted_at=?,deleted_by=? WHERE id=?',(time.time(),user['id'],eid));label='Удаление выполнения в корзину'
        elif action=='restore':
            if e['deleted_at'] is None:raise ValueError('Выполнение не находится в корзине')
            if r['deleted_at'] is not None:raise ValueError('Сначала восстановите родительский прогон')
            c.execute('UPDATE executions SET deleted_at=NULL,deleted_by=NULL WHERE id=?',(eid,));label='Восстановление выполнения'
        elif action=='purge':
            if e['deleted_at'] is None:raise ValueError('Сначала переместите выполнение в корзину')
            if confirm!=eid:raise ValueError('Введите полный ID выполнения для окончательного удаления')
            c.execute("UPDATE analyses SET evidence='',result='',prompt_config=NULL WHERE execution=?",(eid,))
            c.execute('DELETE FROM views WHERE execution=?',(eid,))
            c.execute('UPDATE executions SET purged_at=? WHERE id=?',(time.time(),eid));cleanup=[('execution',eid)];label='Окончательное удаление выполнения'
        else:raise ValueError('Неизвестное действие')
        s.audit(c,user,e['run'],label,{'execution':eid})
    return cleanup_files(cleanup)

def cleanup_files(paths):
    errors=[]
    for kind,name in set(paths):
        if not re.fullmatch(r'[a-f0-9]{24}(?:\.py)?',name):errors.append('Некорректный служебный путь');continue
        root=s.STATE/('objects' if kind=='object' else 'executions');path=root/name
        try:
            if path.is_symlink() or path.is_file():path.unlink(missing_ok=True)
            elif path.is_dir():shutil.rmtree(path)
        except OSError as ex:errors.append(str(ex))
    return errors

def trash(user):
    with s.db() as c:
        condition='' if user['admin'] else ' AND owner=?';args=() if user['admin'] else (user['id'],)
        resources=[dict(x) for x in c.execute('SELECT * FROM resources WHERE deleted_at IS NOT NULL AND purged_at IS NULL'+condition+' ORDER BY deleted_at DESC',args)]
        condition='' if user['admin'] else ' AND r.owner=?'
        executions=[dict(x) for x in c.execute('SELECT e.*,r.name as run_name,r.deleted_at as parent_deleted FROM executions e JOIN resources r ON e.run=r.id WHERE e.deleted_at IS NOT NULL AND e.purged_at IS NULL AND r.purged_at IS NULL'+condition+' ORDER BY e.deleted_at DESC',args)]
    return resources,executions
