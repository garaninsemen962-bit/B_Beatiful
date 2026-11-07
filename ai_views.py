"""Server-rendered AI response fragments; also usable without JavaScript."""
import hashlib,json
import store as s,pages as p,ai_review

def panel(user,eid,analysis_id=None):
    with s.db() as c:
        c.execute('BEGIN')
        ai_review.authorized(c,user,eid)
        # Polling reads only history metadata, not the bodies of 50 older LLM replies.
        analyses=[dict(x) for x in c.execute('SELECT a.id,a.state,a.model,a.created,a.finished,u.display FROM analyses a JOIN users u ON u.id=a.actor WHERE execution=? ORDER BY a.created DESC LIMIT 50',(eid,))]
        selected_id=analysis_id or (analyses[0]['id'] if analyses else None)
        selected=None
        if selected_id:
            row=c.execute('SELECT a.*,u.display FROM analyses a JOIN users u ON u.id=a.actor WHERE a.execution=? AND a.id=?',(eid,selected_id)).fetchone()
            if not row:raise FileNotFoundError('Анализ не найден в этом выполнении')
            selected=dict(row)
    base='/executions/'+eid+'/ai';state=selected['state'] if selected else 'idle'
    body='<div class="card ai-response"><h2>Ответ Gemini</h2>'
    if not selected:body+='<p class="muted">Выберите модель и отправьте сводку. Результат появится в этом блоке.</p>'
    elif state in ('queued','running'):
        body+='<div class="ai-wait" role="status"><span class="ai-spinner" aria-hidden="true"></span><div><strong>Анализ выполняется в фоне</strong><p>Модель '+p.E(selected['model'])+' · прошло <span data-ai-elapsed>0</span> с</p><p class="muted">Ответ появится здесь после завершения и проверки. Можно продолжать работу на странице.</p></div></div><noscript><p>'+p.link(base+'?analysis='+selected['id'],'Проверить состояние','button')+'</p></noscript>'
    elif state=='failed':body+='<p class="muted">Модель '+p.E(selected['model'])+' · '+p.date(selected['created'])+'</p><p class="error" role="alert">'+p.E(selected['error'])+'</p>'
    else:body+=p.ai_result(selected)
    if selected and state!='done':body+=p.prompt_snapshot(selected)
    body+='</div><div class="card"><h2>История анализа</h2><p class="muted">Последние 50 запросов. Старые результаты сохраняются в прежнем виде.</p>'
    body+=p.table(['Дата','Автор','Модель','Состояние'],[['<a data-ai-history="'+a['id']+'" href="'+p.E(base+'?analysis='+a['id'])+'" '+('aria-current="true"' if selected and selected['id']==a['id'] else '')+'>'+p.E(p.date(a['created']))+'</a>',p.E(a['display']),p.E(a['model']),p.E({'queued':'В очереди','running':'Выполняется','done':'Готово','failed':'Ошибка'}.get(a['state'],a['state']))] for a in analyses])+'</div>'
    return {'analysis':selected['id'] if selected else '', 'model':selected['model'] if selected else '', 'state':state,'created':selected['created'] if selected else None,'finished':selected['finished'] if selected else None,'html':body,'view':hashlib.sha256(body.encode()).hexdigest()}

def container(data):
    return '<section id="ai-panel" data-ai-analysis="'+p.E(data['analysis'])+'" data-ai-state="'+p.E(data['state'])+'" data-ai-created="'+p.E(data['created'])+'" data-ai-view="'+data['view']+'" aria-busy="'+('true' if data['state'] in ('queued','running') else 'false')+'">'+data['html']+'</section>'
