"""Bounded Gemini REST/SSE client. No automatic retries, secrets or model text in logs."""
import argparse,http.client,json,logging,os,re,socket,ssl,time,urllib.error,urllib.request
from urllib.parse import quote

LOG=logging.getLogger('decision_diff.gemini')
if not LOG.handlers:
    handler=logging.StreamHandler();handler.setFormatter(logging.Formatter('Gemini %(levelname)s: %(message)s'));LOG.addHandler(handler)
LOG.setLevel(logging.INFO);LOG.propagate=False
MAX_BYTES=2*1024*1024
MAX_EVENT=256*1024

class GeminiError(ValueError):
    """Safe to display: no credentials, raw prompts, responses or proxy URLs."""

def settings(model):
    if not re.fullmatch(r'[A-Za-z0-9._-]{1,100}',model):raise GeminiError('Некорректное имя модели Gemini')
    mode=os.environ.get('GEMINI_RESPONSE_MODE','stream').strip().lower()
    if mode not in ('stream','single'):raise GeminiError('GEMINI_RESPONSE_MODE: используйте stream или single')
    try:timeout=float(os.environ.get('GEMINI_TIMEOUT','90'))
    except ValueError:raise GeminiError('GEMINI_TIMEOUT должен быть числом секунд') from None
    if not 5<=timeout<=600:raise GeminiError('GEMINI_TIMEOUT: допустимо от 5 до 600 секунд')
    level=os.environ.get('GEMINI_THINKING_LEVEL','low' if model.startswith('gemini-3') else '').strip().lower()
    if level not in ('','low','medium','high'):raise GeminiError('GEMINI_THINKING_LEVEL: low, medium или high')
    return mode,timeout,level

def clean(value,key,limit=500):
    value=str(value)
    if key:value=value.replace(key,'[КЛЮЧ СКРЫТ]')
    # Do not show URLs, proxy credentials or echoed authorization headers.
    value=re.sub(r'https?://\S+','[URL]',value)
    value=re.sub(r'(?i)(authorization|x-goog-api-key)\s*[:=]\s*\S+',r'\1: [СКРЫТО]',value)
    return value[:limit]

def events(response,stats):
    """SSE over HTTPResponse.readline: CRLF, comments, split packets and multiline data."""
    data=[];event_size=0
    while True:
        raw=response.readline(MAX_EVENT+1)
        if not raw:
            if data:yield '\n'.join(data)
            return
        stats['bytes']+=len(raw)
        if stats['bytes']>MAX_BYTES:raise GeminiError('Поток Gemini превышает лимит 2 МБ')
        if len(raw)>MAX_EVENT:raise GeminiError('Слишком длинная строка потока Gemini')
        try:line=raw.decode('utf-8').rstrip('\r\n')
        except UnicodeDecodeError:raise GeminiError('Gemini вернул повреждённый UTF-8 в потоке') from None
        if not line:
            if data:yield '\n'.join(data)
            data=[];event_size=0;continue
        if line.startswith(':'):continue
        field,separator,value=line.partition(':')
        if field!='data' or not separator:continue
        if value.startswith(' '):value=value[1:]
        event_size+=len(raw)
        if event_size>MAX_EVENT:raise GeminiError('Слишком большое событие потока Gemini')
        data.append(value)

def api_message(error,key):
    try:
        raw=error.read(16000)
        body=json.loads(raw)
        info=body.get('error',{})
        message=info.get('message','') if isinstance(info,dict) else ''
        status=info.get('status','') if isinstance(info,dict) else ''
        return clean((status+': '+message).strip(': '),key,420)
    except Exception:return ''

def generate(model,system,payload,schema,max_tokens=6000):
    key=os.environ.get('GEMINI_API_KEY','').strip()
    if not key:raise GeminiError('На сервере не задан GEMINI_API_KEY')
    mode,timeout,level=settings(model)
    config={'responseMimeType':'application/json','responseJsonSchema':schema,'maxOutputTokens':max_tokens}
    if level:config['thinkingConfig']={'thinkingLevel':level}
    body={'systemInstruction':{'parts':[{'text':system}]},'contents':[{'role':'user','parts':[{'text':json.dumps(payload,ensure_ascii=False)}]}],'generationConfig':config}
    endpoint='streamGenerateContent?alt=sse' if mode=='stream' else 'generateContent'
    request=urllib.request.Request('https://generativelanguage.googleapis.com/v1beta/models/'+quote(model,safe='')+':'+endpoint,
        data=json.dumps(body,ensure_ascii=False,allow_nan=False).encode(),headers={'Content-Type':'application/json','Accept':'text/event-stream' if mode=='stream' else 'application/json','x-goog-api-key':key})
    started=time.monotonic();stats={'model':clean(model,key),'mode':mode,'phase':'ожидание HTTP-заголовков','http':None,'events':0,'bytes':0,'text_chars':0};parts=[];finish=None;blocked=None
    def log(event,warning=False):
        meta={**stats,'elapsed':round(time.monotonic()-started,2)}
        (LOG.warning if warning else LOG.info)(event+' '+json.dumps(meta,ensure_ascii=False))
    def consume(chunk):
        nonlocal finish,blocked
        stats['events']+=1
        if not isinstance(chunk,dict):raise GeminiError('Неверный формат события Gemini')
        if 'error' in chunk:raise GeminiError('Gemini сообщил об ошибке внутри потока; неполный ответ не сохранён')
        if not blocked:blocked=chunk.get('promptFeedback',{}).get('blockReason')
        for candidate in chunk.get('candidates',[]):
            if candidate.get('index',0)!=0:continue
            if candidate.get('finishReason'):finish=candidate['finishReason']
            for part in candidate.get('content',{}).get('parts',[]):
                if part.get('thought'):continue
                text=part.get('text','')
                if not isinstance(text,str):raise GeminiError('Неверный текстовый фрагмент Gemini')
                if text:parts.append(text);stats['text_chars']+=len(text)
        if stats['events']==1:log('Первый фрагмент получен')
    log('Запрос отправляется')
    try:
        with urllib.request.urlopen(request,timeout=timeout) as response:
            stats['http']=getattr(response,'status',200);stats['phase']='чтение ответа';log('HTTP-ответ получен')
            headers=getattr(response,'headers',{})
            content_type=headers.get('Content-Type','application/json').lower()
            if 'text/event-stream' in content_type:
                for event in events(response,stats):
                    if event.strip()=='[DONE]':break
                    try:chunk=json.loads(event)
                    except json.JSONDecodeError:raise GeminiError('Повреждённое JSON-событие Gemini; неполный ответ не сохранён') from None
                    consume(chunk)
            else:
                raw=response.read(MAX_BYTES+1);stats['bytes']=len(raw)
                if len(raw)>MAX_BYTES:raise GeminiError('Ответ Gemini превышает лимит 2 МБ')
                try:chunk=json.loads(raw)
                except (UnicodeError,json.JSONDecodeError):raise GeminiError('Gemini вернул неожиданный формат вместо JSON/SSE') from None
                # alt=sse normally uses SSE; accepting a complete JSON response also helps testing and compatible gateways.
                if isinstance(chunk,list):
                    for item in chunk:consume(item)
                else:consume(chunk)
        stats['phase']='проверка полноты'
        if finish!='STOP':
            if blocked:raise GeminiError('Gemini заблокировал ответ: '+clean(blocked,key,80))
            if finish=='MAX_TOKENS':raise GeminiError('Ответ Gemini усечён по лимиту токенов; неполный JSON не сохранён')
            if finish:raise GeminiError('Gemini не завершил ответ: '+clean(finish,key,80))
            raise GeminiError('Поток Gemini закончился без подтверждения завершения (STOP); неполный ответ не сохранён')
        stats['phase']='разбор JSON'
        try:result=json.loads(''.join(parts))
        except json.JSONDecodeError:raise GeminiError('Gemini завершил генерацию, но вернул некорректный JSON') from None
        stats['phase']='готово';log('Ответ получен полностью');return result
    except urllib.error.HTTPError as ex:
        stats['http']=ex.code;stats['phase']='HTTP-ошибка API';detail=api_message(ex,key)
        labels={400:'запрос отклонён',401:'неверный API-ключ',403:'доступ запрещён',404:'модель недоступна',429:'превышен лимит запросов или квота'}
        message='Gemini HTTP '+str(ex.code)+': '+labels.get(ex.code,'ошибка сервиса')+('. '+detail if detail else '')
    except http.client.RemoteDisconnected:
        message='Gemini: соединение закрыто без HTTP-ответа' if stats['http'] is None else 'Gemini: соединение оборвалось во время получения ответа'
    except http.client.IncompleteRead:
        message='Gemini: получена только часть HTTP-ответа; неполный результат не сохранён'
    except (TimeoutError,socket.timeout):
        message='Gemini: истекло время ожидания '+('HTTP-ответа' if stats['http'] is None else 'следующей части ответа')
    except ssl.SSLError:
        message='Gemini: ошибка TLS/сертификата при установлении или чтении соединения'
    except urllib.error.URLError as ex:
        reason=ex.reason
        if isinstance(reason,ssl.SSLError):message='Gemini: ошибка TLS/сертификата'
        elif isinstance(reason,socket.gaierror):message='Gemini: не удалось разрешить имя сервера (DNS)'
        elif isinstance(reason,(TimeoutError,socket.timeout)):message='Gemini: истекло время ожидания соединения'
        else:message='Gemini: ошибка подключения ('+type(reason).__name__+')'
    except (OSError,http.client.HTTPException) as ex:
        message='Gemini: транспортная ошибка ('+type(ex).__name__+')'
    except GeminiError as ex:
        message=str(ex)
    except (TypeError,AttributeError,KeyError,ValueError):
        message='Gemini: ответ не соответствует ожидаемой структуре JSON/SSE'
    log('Ошибка запроса',True)
    elapsed=round(time.monotonic()-started,1)
    # Keep the UI explanation specific; a disconnect does not imply that Google did not process the request.
    message=clean(message,key,700)+f". Этап: {stats['phase']}; {elapsed} с; событий: {stats['events']}; байт: {stats['bytes']}. Автоматического повтора не было."
    raise GeminiError(message) from None

def main():
    parser=argparse.ArgumentParser(description='Один короткий запрос Gemini с диагностикой; ключ берётся из окружения.')
    parser.add_argument('--check',action='store_true',required=True);parser.parse_args()
    model=os.environ.get('GEMINI_MODEL','')
    try:
        result=generate(model,'Ответь JSON-объектом {"ok": true}.',{'task':'connection check'},
            {'type':'object','properties':{'ok':{'type':'boolean'}},'required':['ok']},max_tokens=1024)
        if not isinstance(result,dict) or result.get('ok') is not True:raise GeminiError('Ответ получен, но не соответствует проверочному JSON')
        print('OK: ответ Gemini получен полностью и разобран.')
    except ValueError as ex:raise SystemExit(str(ex))
if __name__=='__main__':main()
