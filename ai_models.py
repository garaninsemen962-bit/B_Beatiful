"""Server-side Gemini model choices and a bounded metadata-only catalog lookup."""
import hashlib,json,os,re,threading,time,urllib.request,urllib.error
from urllib.parse import urlencode
import gemini_client
DEFAULTS=[('gemini-3.5-flash-lite','Gemini 3.5 Flash-Lite · экономичный'),('gemini-3.8-flash','Gemini 3.8 Flash'),('gemini-3.1-pro-preview','Gemini 3.1 Pro · Preview'),('gemini-3.1-flash-lite','Gemini 3.1 Flash-Lite')]
_cache={};_lock=threading.Lock()
def default():return os.environ.get('GEMINI_MODEL','').strip() or 'gemini-3.5-flash-lite'
def validate(model):
    if not isinstance(model,str) or not re.fullmatch(r'[A-Za-z0-9._-]{1,100}',model):raise ValueError('Некорректное имя модели Gemini')
    configured=os.environ.get('GEMINI_MODELS','').strip()
    if configured and model not in {x.strip() for x in configured.split(',') if x.strip()}:raise ValueError('Эта модель не разрешена настройкой GEMINI_MODELS сервера')
    return model

def choices(selected=None):
    configured=os.environ.get('GEMINI_MODELS','').strip()
    rows=[(x.strip(),x.strip()) for x in configured.split(',') if x.strip()] if configured else list(DEFAULTS)
    for model in (default(),selected):
        if model and not any(x[0]==model for x in rows):
            try:validate(model)
            except ValueError:continue
            rows.insert(0,(model,model))
    valid=[]
    for item in rows:
        try:validate(item[0])
        except ValueError:continue
        valid.append(item)
    return valid

def catalog():
    key=os.environ.get('GEMINI_API_KEY','').strip()
    if not key:raise ValueError('На сервере не задан GEMINI_API_KEY')
    fingerprint=hashlib.sha256(key.encode()).hexdigest()
    with _lock:
        cached=_cache.get(fingerprint)
        if cached and time.monotonic()-cached[0]<900:return cached[1]
        models={};token='';seen=set()
        try:
            for _ in range(3):
                params={'pageSize':1000}
                if token:params['pageToken']=token
                request=urllib.request.Request('https://generativelanguage.googleapis.com/v1beta/models?'+urlencode(params),headers={'x-goog-api-key':key,'Accept':'application/json'})
                with urllib.request.urlopen(request,timeout=15) as response:raw=response.read(1024*1024+1)
                if len(raw)>1024*1024:raise ValueError('Каталог моделей превышает лимит')
                data=json.loads(raw)
                for item in data.get('models',[]):
                    name=item.get('name','').removeprefix('models/')
                    if not name.startswith('gemini-') or 'generateContent' not in item.get('supportedGenerationMethods',[]):continue
                    if any(x in name.lower() for x in ('image','tts','live','audio','embedding','transcribe','video','omni')):continue
                    try:validate(name)
                    except ValueError:continue
                    models[name]={'id':name,'label':str(item.get('displayName',name))[:160]+' · '+name}
                token=data.get('nextPageToken','')
                if not token:break
                if not isinstance(token,str) or len(token)>2000 or token in seen:raise ValueError('Некорректная пагинация каталога моделей')
                seen.add(token)
            if not models:raise ValueError('Google не вернул подходящих текстовых моделей. Можно использовать список из настроек сервера.')
            rows=sorted(models.values(),key=lambda x:x['id']);_cache.clear();_cache[fingerprint]=(time.monotonic(),rows)
            return rows
        except urllib.error.HTTPError as ex:
            detail=gemini_client.api_message(ex,key)
            raise ValueError('Каталог Gemini: HTTP '+str(ex.code)+('. '+detail if detail else '')) from None
        except (OSError,urllib.error.URLError):raise ValueError('Не удалось загрузить каталог Google. Текущий выбор модели сохранён.') from None
        except (TypeError,KeyError,AttributeError,json.JSONDecodeError):raise ValueError('Google вернул неожиданный формат каталога моделей') from None
